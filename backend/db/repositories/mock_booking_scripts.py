"""Exact Lua for the shared mock booking state on Redis.

Each multi-key decision -- publishing candidates under capacity, booking a slot,
releasing a search's pins, and bounded cleanup -- runs as one script so it is
atomic and reproduces the in-memory store's decisions exactly.

Design rules mirror the watch scripts:

* Keys are built from a single `prefix` argument. The deployment is a single
  Redis primary (cluster is refused at startup), so a script may touch keys it
  does not declare; the prefix keeps them under one namespace.
* "now", retention, and pin lifetimes are injected epoch-millisecond arguments,
  never `redis.call('TIME')`, so every decision is deterministic under an
  injected clock and matches the in-memory repository.
* The unpinned-eviction index (`lru`) holds only slots that may be evicted, so
  choosing the oldest victim is `ZRANGE lru 0 0` -- bounded, and the same victim
  the in-memory store picks. Pinned slots live in `all` (the capacity count)
  but not in `lru`.
"""

# Reconcile pins whose lease has lapsed: drop them and return the slot to the
# eviction index so a crashed search cannot pin state forever. Bounded by the
# number of expired pins.
_RECONCILE_PINS = """
local function reconcile_pins(prefix, now)
  local stale = redis.call('ZRANGEBYSCORE', prefix..':pin', '-inf', now)
  for _, sid in ipairs(stale) do
    redis.call('ZREM', prefix..':pin', sid)
    local owner = redis.call('HGET', prefix..':pinowner', sid)
    if owner then
      redis.call('SREM', prefix..':pinop:'..owner, sid)
      redis.call('HDEL', prefix..':pinowner', sid)
    end
    if redis.call('EXISTS', prefix..':slot:'..sid) == 1 then
      local score = redis.call('ZSCORE', prefix..':all', sid)
      if score then redis.call('ZADD', prefix..':lru', score, sid) end
    end
  end
end
"""

# ARGV: prefix, operation_id, now_ms, pin_ttl_ms, capacity,
#       then repeating (slot_id, slot_json)
# Returns the JSON of every admitted, available slot in candidate order.
PUBLISH_AND_FILTER = _RECONCILE_PINS + """
local prefix = ARGV[1]
local op = ARGV[2]
local now = tonumber(ARGV[3])
local pin_ttl = tonumber(ARGV[4])
local capacity = tonumber(ARGV[5])
reconcile_pins(prefix, now)

local function pin(sid)
  redis.call('ZREM', prefix..':lru', sid)
  local owner = redis.call('HGET', prefix..':pinowner', sid)
  if owner and owner ~= op then redis.call('SREM', prefix..':pinop:'..owner, sid) end
  redis.call('HSET', prefix..':pinowner', sid, op)
  redis.call('SADD', prefix..':pinop:'..op, sid)
  redis.call('ZADD', prefix..':pin', now + pin_ttl, sid)
  redis.call('PEXPIRE', prefix..':pinop:'..op, pin_ttl)
end

local result = {}
local i = 6
while i <= #ARGV do
  local sid = ARGV[i]
  local sjson = ARGV[i + 1]
  i = i + 2
  local skip = false
  local tomb = redis.call('GET', prefix..':booked:'..sid)
  if tomb then
    local bar = string.find(tomb, '|')
    if tonumber(string.sub(tomb, bar + 1)) > now then skip = true end
  end
  if not skip then
    if redis.call('EXISTS', prefix..':slot:'..sid) == 1 then
      redis.call('ZADD', prefix..':all', now, sid)
      result[#result + 1] = redis.call('GET', prefix..':slot:'..sid)
      pin(sid)
    else
      local admit = true
      if redis.call('ZCARD', prefix..':all') >= capacity then
        local oldest = redis.call('ZRANGE', prefix..':lru', 0, 0)
        if #oldest == 0 then
          admit = false
        else
          local victim = oldest[1]
          redis.call('ZREM', prefix..':lru', victim)
          redis.call('ZREM', prefix..':all', victim)
          redis.call('DEL', prefix..':slot:'..victim)
        end
      end
      if admit then
        redis.call('SET', prefix..':slot:'..sid, sjson)
        redis.call('ZADD', prefix..':all', now, sid)
        result[#result + 1] = sjson
        pin(sid)
      end
    end
  end
end
return result
"""

# ARGV: prefix, slot_id, key, confirmation_json ('' if the slot was gone at
#       read time), protected_ms, now_ms, backstop_grace_ms
BOOK_SLOT = """
local prefix = ARGV[1]
local sid = ARGV[2]
local key = ARGV[3]
local conf = ARGV[4]
local pms = tonumber(ARGV[5])
local now = tonumber(ARGV[6])
local grace = tonumber(ARGV[7])

local booking = redis.call('GET', prefix..':booking:'..key)
if booking then
  local pexp = redis.call('ZSCORE', prefix..':bookexp', key)
  if pexp and tonumber(pexp) > now then return {'EXISTING', booking} end
end
local tomb = redis.call('GET', prefix..':booked:'..sid)
if tomb then
  local bar = string.find(tomb, '|')
  local tkey = string.sub(tomb, 1, bar - 1)
  local tpexp = tonumber(string.sub(tomb, bar + 1))
  if tpexp > now and tkey ~= key then return {'UNAVAILABLE'} end
end
if redis.call('EXISTS', prefix..':slot:'..sid) == 0 then return {'NOTFOUND'} end

redis.call('SET', prefix..':booking:'..key, conf)
redis.call('ZADD', prefix..':bookexp', pms, key)
redis.call('SET', prefix..':booked:'..sid, key..'|'..pms)
redis.call('ZADD', prefix..':tombexp', pms, sid)
redis.call('DEL', prefix..':slot:'..sid)
redis.call('ZREM', prefix..':all', sid)
redis.call('ZREM', prefix..':lru', sid)
redis.call('ZREM', prefix..':pin', sid)
local owner = redis.call('HGET', prefix..':pinowner', sid)
if owner then
  redis.call('SREM', prefix..':pinop:'..owner, sid)
  redis.call('HDEL', prefix..':pinowner', sid)
end
redis.call('PEXPIREAT', prefix..':booking:'..key, pms + grace)
redis.call('PEXPIREAT', prefix..':booked:'..sid, pms + grace)
return {'BOOKED', conf}
"""

# ARGV: prefix, operation_id
RELEASE_OPERATION = """
local prefix = ARGV[1]
local op = ARGV[2]
local slots = redis.call('SMEMBERS', prefix..':pinop:'..op)
for _, sid in ipairs(slots) do
  if redis.call('HGET', prefix..':pinowner', sid) == op then
    redis.call('ZREM', prefix..':pin', sid)
    redis.call('HDEL', prefix..':pinowner', sid)
    if redis.call('EXISTS', prefix..':slot:'..sid) == 1 then
      local score = redis.call('ZSCORE', prefix..':all', sid)
      if score then redis.call('ZADD', prefix..':lru', score, sid) end
    end
  end
end
redis.call('DEL', prefix..':pinop:'..op)
return 1
"""

# ARGV: prefix, now_ms, idle_cutoff_ms, batch
# Returns {idle_slots_removed, expired_bookings_removed}
CLEANUP = _RECONCILE_PINS + """
local prefix = ARGV[1]
local now = tonumber(ARGV[2])
local idle_cutoff = tonumber(ARGV[3])
local batch = tonumber(ARGV[4])
reconcile_pins(prefix, now)

local idle = redis.call(
  'ZRANGEBYSCORE', prefix..':lru', '-inf', idle_cutoff, 'LIMIT', 0, batch
)
for _, sid in ipairs(idle) do
  redis.call('ZREM', prefix..':lru', sid)
  redis.call('ZREM', prefix..':all', sid)
  redis.call('DEL', prefix..':slot:'..sid)
end

local ekeys = redis.call(
  'ZRANGEBYSCORE', prefix..':bookexp', '-inf', now, 'LIMIT', 0, batch
)
for _, k in ipairs(ekeys) do
  redis.call('DEL', prefix..':booking:'..k)
  redis.call('ZREM', prefix..':bookexp', k)
end
local etombs = redis.call(
  'ZRANGEBYSCORE', prefix..':tombexp', '-inf', now, 'LIMIT', 0, batch
)
for _, sid in ipairs(etombs) do
  redis.call('DEL', prefix..':booked:'..sid)
  redis.call('ZREM', prefix..':tombexp', sid)
end
return {#idle, #ekeys}
"""
