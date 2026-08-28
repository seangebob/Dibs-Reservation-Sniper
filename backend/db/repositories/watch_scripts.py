"""Exact Lua sources for the atomic watch repository.

Every multi-key lifecycle change on Redis runs as one of these scripts, so the
change is atomic and compare-and-set fenced rather than a best-effort pipeline.
`RedisWatchRepository` registers each source and invokes it by `EVALSHA` with
automatic reload on `NOSCRIPT` (redis-py's `Script` handles that).

Design rules that keep these portable and safe:

* The caller computes every new document with Pydantic and passes it as an
  opaque JSON string that the script stores verbatim. Scripts use `cjson` only
  to *read* a scalar field (status, window_id, revision) for a decision; they
  never re-encode a document, so JSON shape and types can never drift.
* Deterministic identities (event ids) and all timestamps are computed by the
  caller and passed as validated scalar `ARGV`; no script hashes, formats time,
  or embeds arbitrary user text in a key.
* "now" is an injected epoch-millisecond argument, not `redis.call('TIME')`, so
  lease and due-time decisions are deterministic under an injected clock.
* Each script returns a flat array whose first element is a decision code.
"""

# KEYS: watch, runtime, fence, all_index, active_index, schedule
# ARGV: watch_id, watch_json, runtime_json, scheduled_ms ('' when none)
CREATE_WITH_SCHEDULE = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return {'ALREADY_EXISTS', redis.call('GET', KEYS[1]), redis.call('GET', KEYS[2])}
end
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SET', KEYS[2], ARGV[3])
redis.call('SET', KEYS[3], '0')
redis.call('SADD', KEYS[4], ARGV[1])
redis.call('SADD', KEYS[5], ARGV[1])
if ARGV[4] ~= '' then
  redis.call('ZADD', KEYS[6], tonumber(ARGV[4]), ARGV[1])
end
return {'CREATED'}
"""

# A claim key holds "owner|token|expires_ms". Native PX is only a crash
# backstop; the authoritative lease decision compares expires_ms against the
# injected now, so lease expiry is deterministic under an injected clock and
# identical to the in-memory store.
_PARSE_CLAIM = """
local function parse_claim(v)
  local p1 = string.find(v, '|')
  local p2 = string.find(v, '|', p1 + 1)
  return string.sub(v, 1, p1 - 1),
         string.sub(v, p1 + 1, p2 - 1),
         tonumber(string.sub(v, p2 + 1))
end
"""

# KEYS: watch, runtime, fence, claim, schedule
# ARGV: watch_id, window_id, owner_id, lease_ms, now_ms, ignore_schedule ('1'/'0')
CLAIM_WINDOW = _PARSE_CLAIM + """
local w = redis.call('GET', KEYS[1])
if not w then return {'UNKNOWN'} end
if cjson.decode(w).status ~= 'ACTIVE' then return {'TERMINAL'} end
local rraw = redis.call('GET', KEYS[2])
if not rraw then return {'UNKNOWN'} end
if cjson.decode(rraw).window_id ~= ARGV[2] then return {'STALE'} end
local now = tonumber(ARGV[5])
if ARGV[6] ~= '1' then
  local due = redis.call('ZSCORE', KEYS[5], ARGV[1])
  if due and tonumber(due) > now then return {'EARLY'} end
end
local held = redis.call('GET', KEYS[4])
if held then
  local _, _, exp = parse_claim(held)
  if exp > now then return {'BUSY'} end
end
local token = redis.call('INCR', KEYS[3])
local expires = now + tonumber(ARGV[4])
redis.call('SET', KEYS[4], ARGV[3] .. '|' .. token .. '|' .. expires,
           'PX', tonumber(ARGV[4]))
return {'OWNED', w, rraw, tostring(token), tostring(expires)}
"""

# KEYS: watch, runtime, claim
# ARGV: watch_id, expected_revision, expected_token, owner_id,
#       booking_runtime_json, now_ms
BEGIN_BOOKING = _PARSE_CLAIM + """
local w = redis.call('GET', KEYS[1])
if not w then return {'FENCED'} end
if cjson.decode(w).status ~= 'ACTIVE' then return {'FENCED'} end
local rraw = redis.call('GET', KEYS[2])
if not rraw then return {'FENCED'} end
local rt = cjson.decode(rraw)
if tostring(rt.revision) ~= ARGV[2] then return {'FENCED'} end
local claim = redis.call('GET', KEYS[3])
if not claim then return {'FENCED'} end
local owner, token, exp = parse_claim(claim)
if owner ~= ARGV[4] or token ~= ARGV[3] or exp <= tonumber(ARGV[6]) then
  return {'FENCED'}
end
if rt.cancel_requested == true then return {'CANCELLED'} end
redis.call('SET', KEYS[2], ARGV[5])
return {'GRANTED'}
"""

# KEYS: watch, runtime, claim, all_index, active_index, schedule, terminal, events
# ARGV: watch_id, expected_revision, expected_token, owner_id, new_watch_json,
#       new_runtime_json, is_terminal ('1'/'0'), next_scheduled_ms ('' none),
#       terminal_delete_ms ('' none), event_id ('' none),
#       retention_expire_ms ('' none), now_ms
COMMIT_WINDOW = _PARSE_CLAIM + """
local w = redis.call('GET', KEYS[1])
if not w then return {'UNKNOWN'} end
if cjson.decode(w).status ~= 'ACTIVE' then return {'TERMINAL', w} end
local rraw = redis.call('GET', KEYS[2])
if not rraw then return {'UNKNOWN'} end
if tostring(cjson.decode(rraw).revision) ~= ARGV[2] then return {'FENCED'} end
local claim = redis.call('GET', KEYS[3])
if not claim then return {'FENCED'} end
local owner, token, exp = parse_claim(claim)
if owner ~= ARGV[4] or token ~= ARGV[3] or exp <= tonumber(ARGV[12]) then
  return {'FENCED'}
end
redis.call('SET', KEYS[1], ARGV[5])
redis.call('SET', KEYS[2], ARGV[6])
redis.call('DEL', KEYS[3])
if ARGV[7] == '1' then
  redis.call('SREM', KEYS[5], ARGV[1])
  redis.call('ZREM', KEYS[6], ARGV[1])
  if ARGV[9] ~= '' then redis.call('ZADD', KEYS[7], tonumber(ARGV[9]), ARGV[1]) end
  if ARGV[10] ~= '' then redis.call('SADD', KEYS[8], ARGV[10]) end
  if ARGV[11] ~= '' then
    redis.call('PEXPIREAT', KEYS[1], tonumber(ARGV[11]))
    redis.call('PEXPIREAT', KEYS[2], tonumber(ARGV[11]))
  end
  return {'COMMITTED', ARGV[5], ARGV[10]}
end
if ARGV[8] ~= '' then
  redis.call('ZADD', KEYS[6], tonumber(ARGV[8]), ARGV[1])
else
  redis.call('ZREM', KEYS[6], ARGV[1])
end
return {'COMMITTED', ARGV[5], ''}
"""

# KEYS: watch, runtime, claim, active_index, schedule, terminal_index
# ARGV: watch_id, cas_revision ('' to skip), mode ('cancel'/'pending'),
#       new_watch_json ('' for pending), new_runtime_json ('' when no runtime),
#       terminal_delete_ms ('' for pending), retention_expire_ms ('' none)
CANCEL_IF_ACTIVE = """
local w = redis.call('GET', KEYS[1])
if not w then return {'UNKNOWN'} end
if cjson.decode(w).status ~= 'ACTIVE' then return {'NOOP', w} end
local rraw = redis.call('GET', KEYS[2])
if rraw and ARGV[2] ~= '' and tostring(cjson.decode(rraw).revision) ~= ARGV[2] then
  return {'FENCED'}
end
if ARGV[3] == 'pending' then
  if rraw then redis.call('SET', KEYS[2], ARGV[5]) end
  return {'NOT_ELIGIBLE', w}
end
redis.call('SET', KEYS[1], ARGV[4])
if rraw then redis.call('SET', KEYS[2], ARGV[5]) end
redis.call('DEL', KEYS[3])
redis.call('SREM', KEYS[4], ARGV[1])
redis.call('ZREM', KEYS[5], ARGV[1])
if ARGV[6] ~= '' then redis.call('ZADD', KEYS[6], tonumber(ARGV[6]), ARGV[1]) end
if ARGV[7] ~= '' then
  redis.call('PEXPIREAT', KEYS[1], tonumber(ARGV[7]))
  redis.call('PEXPIREAT', KEYS[2], tonumber(ARGV[7]))
end
return {'APPLIED', ARGV[4]}
"""

# Remove a bounded batch of terminal watches whose retention has elapsed. Each
# is revalidated as still-present and still-terminal before deletion; a member
# that is gone or was resurrected is just dropped from the cleanup index. Native
# key TTLs are only a backstop, so this is what actually removes set members.
# KEYS: terminal_index, all_index, active_index, schedule_index
# ARGV: now_ms, batch, key_prefix
CLEANUP_DUE = """
local due = redis.call(
  'ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2])
)
local removed = 0
for _, wid in ipairs(due) do
  local wkey = ARGV[3]..':'..wid
  local doc = redis.call('GET', wkey)
  if doc and cjson.decode(doc).status ~= 'ACTIVE' then
    redis.call('DEL', wkey)
    redis.call('DEL', wkey..':runtime')
    redis.call('DEL', wkey..':fence')
    redis.call('DEL', wkey..':claim')
    redis.call('SREM', KEYS[2], wid)
    redis.call('SREM', KEYS[3], wid)
    redis.call('ZREM', KEYS[4], wid)
    removed = removed + 1
  end
  redis.call('ZREM', KEYS[1], wid)
end
local remaining = redis.call('ZCOUNT', KEYS[1], '-inf', ARGV[1])
return {removed, remaining}
"""

# KEYS: watch, runtime, claim, active_index, schedule, terminal, events
# ARGV: watch_id, cas_revision ('' to skip), new_watch_json, new_runtime_json
#       ('' when no runtime), event_id ('' none), terminal_delete_ms ('' none),
#       retention_expire_ms ('' none)
EXPIRE_IF_ELIGIBLE = """
local w = redis.call('GET', KEYS[1])
if not w then return {'UNKNOWN'} end
if cjson.decode(w).status ~= 'ACTIVE' then return {'NOOP', w} end
local rraw = redis.call('GET', KEYS[2])
if rraw and ARGV[2] ~= '' and tostring(cjson.decode(rraw).revision) ~= ARGV[2] then
  return {'FENCED'}
end
redis.call('SET', KEYS[1], ARGV[3])
if rraw then redis.call('SET', KEYS[2], ARGV[4]) end
redis.call('DEL', KEYS[3])
redis.call('SREM', KEYS[4], ARGV[1])
redis.call('ZREM', KEYS[5], ARGV[1])
if ARGV[6] ~= '' then redis.call('ZADD', KEYS[6], tonumber(ARGV[6]), ARGV[1]) end
if ARGV[5] ~= '' then redis.call('SADD', KEYS[7], ARGV[5]) end
if ARGV[7] ~= '' then
  redis.call('PEXPIREAT', KEYS[1], tonumber(ARGV[7]))
  redis.call('PEXPIREAT', KEYS[2], tonumber(ARGV[7]))
end
return {'APPLIED', ARGV[3]}
"""

# KEYS: claim
# ARGV: owner_id, token
RELEASE_CLAIM = _PARSE_CLAIM + """
local claim = redis.call('GET', KEYS[1])
if not claim then return 0 end
local owner, token, _ = parse_claim(claim)
if owner == ARGV[1] and token == ARGV[2] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# The dispatch lease is single-flight for *publishing* a marker to the queue,
# separate from the poll claim that fences provider work. Its key is per-window
# (`:dispatch:{hash}`), so a lease left over from a consumed window is simply a
# different key that expires on its own. The schedule ZSET score stays the
# logical due time; deferral after a successful publish is expressed by
# extending this lease's expiry to the recovery grace, not by moving the score
# (which would make the poll claim read the window as not-yet-due).
#
# KEYS: watch, runtime, dispatch_fence, dispatch_lease, schedule
# ARGV: watch_id, window_id, owner_id, lease_ms, now_ms
CLAIM_DISPATCH = _PARSE_CLAIM + """
local w = redis.call('GET', KEYS[1])
if not w then return {'STALE'} end
if cjson.decode(w).status ~= 'ACTIVE' then return {'STALE'} end
local rraw = redis.call('GET', KEYS[2])
if not rraw then return {'STALE'} end
if cjson.decode(rraw).window_id ~= ARGV[2] then return {'STALE'} end
if not redis.call('ZSCORE', KEYS[5], ARGV[1]) then return {'STALE'} end
local now = tonumber(ARGV[5])
local held = redis.call('GET', KEYS[4])
if held then
  local _, _, exp = parse_claim(held)
  if exp > now then return {'BUSY'} end
end
local gen = redis.call('INCR', KEYS[3])
local expires = now + tonumber(ARGV[4])
redis.call('SET', KEYS[4], ARGV[3] .. '|' .. gen .. '|' .. expires,
           'PX', tonumber(ARGV[4]))
return {'CLAIMED', tostring(gen), tostring(expires)}
"""

# KEYS: dispatch_lease
# ARGV: owner_id, generation, redispatch_after_ms, now_ms
MARK_DISPATCHED = _PARSE_CLAIM + """
local held = redis.call('GET', KEYS[1])
if not held then return 0 end
local owner, gen, _ = parse_claim(held)
if owner ~= ARGV[1] or gen ~= ARGV[2] then return 0 end
local redispatch = tonumber(ARGV[3])
local px = redispatch - tonumber(ARGV[4])
if px < 1 then px = 1 end
redis.call('SET', KEYS[1], owner .. '|' .. gen .. '|' .. redispatch, 'PX', px)
return 1
"""

# KEYS: dispatch_lease
# ARGV: owner_id, generation
RELEASE_DISPATCH = _PARSE_CLAIM + """
local held = redis.call('GET', KEYS[1])
if not held then return 0 end
local owner, gen, _ = parse_claim(held)
if owner == ARGV[1] and gen == ARGV[2] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# Compare-owner renewal of the recovery leader lease. Acquisition is a plain
# `SET NX PX`; only renewal and release need to be conditional on ownership so a
# replica that lost the lease (its PX elapsed and another replica took it) can
# neither extend nor delete the new owner's lease.
# KEYS: leader
# ARGV: owner_id, lease_ms
RENEW_LEADER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', tonumber(ARGV[2]))
  return 1
end
return 0
"""

# KEYS: leader
# ARGV: owner_id
RELEASE_LEADER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# Repair an active record that has no durable schedule marker. Conditional on
# the watch still being active, no unexpired poll claim holding it, and no
# marker already present, so a live owner is left to finish and a healthy marker
# is never rewritten. The caller computes the full runtime (carrying the window
# id and the capped due time) so this only stores it verbatim and re-indexes.
# KEYS: watch, runtime, fence, claim, schedule
# ARGV: watch_id, runtime_json, scheduled_ms, now_ms
SYNTHESIZE_MARKER = _PARSE_CLAIM + """
local w = redis.call('GET', KEYS[1])
if not w then return 0 end
if cjson.decode(w).status ~= 'ACTIVE' then return 0 end
local claim = redis.call('GET', KEYS[4])
if claim then
  local _, _, exp = parse_claim(claim)
  if exp > tonumber(ARGV[4]) then return 0 end
end
if redis.call('ZSCORE', KEYS[5], ARGV[1]) then return 0 end
redis.call('SET', KEYS[2], ARGV[2])
if redis.call('EXISTS', KEYS[3]) == 0 then redis.call('SET', KEYS[3], '0') end
redis.call('ZADD', KEYS[5], tonumber(ARGV[3]), ARGV[1])
return 1
"""
