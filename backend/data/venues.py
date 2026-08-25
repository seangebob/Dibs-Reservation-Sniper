"""Mock Kitchener-Waterloo venue catalog and deterministic name resolution.

This module is fixture data for the Milestone 2 mock adapter, not a live venue
directory. It exists so slot generation can respect realistic opening hours,
weekday/weekend differences, holiday closures, and per-venue party limits, and
so ambiguous venue names can be detected before anything is booked.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
import unicodedata

from backend.orchestrator.schemas import VenueType


SLOT_INTERVAL_MINUTES = 15
MAX_GENERATED_SLOTS = 16

#: Years the generated holiday calendar covers. The validator refuses dates
#: more than a year out, so this range stays comfortably ahead of any request.
SUPPORTED_YEARS: range = range(2024, 2032)

_MONDAY = 0
_SUNDAY = 6


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """Return the nth (1-based) given weekday of a month, e.g. 2nd Monday."""

    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def _easter_sunday(year: int) -> date:
    """Western Easter for one year (anonymous Gregorian computus)."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _victoria_day(year: int) -> date:
    """The Monday on or before May 24."""

    may_24 = date(year, 5, 24)
    return may_24 - timedelta(days=may_24.weekday())


def _holidays_for(year: int) -> dict[str, str]:
    """Ontario holidays for one year, keyed by ISO date."""

    easter = _easter_sunday(year)
    days: tuple[tuple[date, str], ...] = (
        (date(year, 1, 1), "New Year's Day"),
        (_nth_weekday(year, 2, _MONDAY, 3), "Family Day"),
        (date(year, 2, 14), "Valentine's Day"),
        (easter - timedelta(days=2), "Good Friday"),
        (easter, "Easter Sunday"),
        (easter + timedelta(days=1), "Easter Monday"),
        (_nth_weekday(year, 5, _SUNDAY, 2), "Mother's Day"),
        (_victoria_day(year), "Victoria Day"),
        (date(year, 7, 1), "Canada Day"),
        (_nth_weekday(year, 8, _MONDAY, 1), "Civic Holiday"),
        (_nth_weekday(year, 9, _MONDAY, 1), "Labour Day"),
        (_nth_weekday(year, 10, _MONDAY, 2), "Thanksgiving Monday"),
        (date(year, 12, 24), "Christmas Eve"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Boxing Day"),
        (date(year, 12, 31), "New Year's Eve"),
    )
    return {day.isoformat(): name for day, name in days}


#: Every recognized holiday in :data:`SUPPORTED_YEARS`, keyed by ISO date.
#: Membership here says nothing about opening; the sets below decide that.
HOLIDAY_CALENDAR: dict[str, str] = {
    iso: name for year in SUPPORTED_YEARS for iso, name in _holidays_for(year).items()
}


def _dates_named(*names: str) -> frozenset[str]:
    wanted = frozenset(names)
    return frozenset(iso for iso, name in HOLIDAY_CALENDAR.items() if name in wanted)


#: Holidays on which hospitality venues in KW are most consistently shut.
STATUTORY_CLOSURES: frozenset[str] = _dates_named(
    "New Year's Day",
    "Good Friday",
    "Easter Sunday",
    "Thanksgiving Monday",
    "Christmas Day",
    "Boxing Day",
)

#: Recreation venues work the long weekends they earn their money on, so they
#: close on fewer days than restaurants do.
RECREATION_CLOSURES: frozenset[str] = _dates_named(
    "New Year's Day",
    "Easter Sunday",
    "Christmas Day",
    "Boxing Day",
)

#: Open, but every table is already spoken for: the holidays people book out
#: months ahead plus the long-weekend Mondays that fill a dining room.
COMMON_SELLOUT_DATES: frozenset[str] = _dates_named(
    "Valentine's Day",
    "Mother's Day",
    "Easter Monday",
    "Family Day",
    "Victoria Day",
    "Canada Day",
    "Civic Holiday",
    "Labour Day",
    "Christmas Eve",
    "New Year's Eve",
)

#: Long weekends fill a tube park and a climbing gym the same way.
RECREATION_SELLOUT_DATES: frozenset[str] = _dates_named(
    "Family Day",
    "Victoria Day",
    "Canada Day",
    "Civic Holiday",
    "Labour Day",
)


def holiday_name(day: date) -> str | None:
    """Return the holiday falling on this date, if the calendar knows one."""

    return HOLIDAY_CALENDAR.get(day.isoformat())


_WORDS_IGNORED_WHEN_MATCHING = frozenset({"the", "and", "of", "at", "a"})


@dataclass(frozen=True, slots=True)
class OpeningHours:
    """Local opening and closing time for a single day."""

    open_time: str
    close_time: str


@dataclass(frozen=True, slots=True)
class VenueProfile:
    """Scheduling rules the mock adapter needs to build slots."""

    weekly_hours: tuple[OpeningHours | None, ...]
    max_party_size: int
    minimum_stay_minutes: int
    closed_dates: frozenset[str] = frozenset()
    sold_out_dates: frozenset[str] = frozenset()

    def hours_for(self, day: date) -> OpeningHours | None:
        """Return opening hours for one date, or None when shut."""

        if day.isoformat() in self.closed_dates:
            return None
        return self.weekly_hours[day.weekday()]

    def is_sold_out(self, day: date) -> bool:
        """Whether every slot on this date is already taken."""

        return day.isoformat() in self.sold_out_dates


@dataclass(frozen=True, slots=True)
class Venue:
    """One catalog venue and the aliases users type for it."""

    name: str
    venue_type: VenueType
    profile: VenueProfile
    aliases: tuple[str, ...] = field(default_factory=tuple)


class VenueResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class VenueResolution:
    """Outcome of matching untrusted venue text against the catalog."""

    status: VenueResolutionStatus
    venue: Venue | None = None
    candidates: tuple[Venue, ...] = field(default_factory=tuple)


def _hours(open_time: str, close_time: str) -> OpeningHours:
    return OpeningHours(open_time=open_time, close_time=close_time)


def _week(
    *,
    weekday: OpeningHours | None,
    friday: OpeningHours | None = None,
    saturday: OpeningHours | None = None,
    sunday: OpeningHours | None = None,
    monday: OpeningHours | None = None,
    monday_closed: bool = False,
) -> tuple[OpeningHours | None, ...]:
    """Build Monday-to-Sunday hours from weekday and weekend variations."""

    friday = friday or weekday
    saturday = saturday or friday
    sunday = sunday if sunday is not None else weekday
    if monday_closed:
        monday = None
    else:
        monday = monday or weekday
    return (monday, weekday, weekday, weekday, friday, saturday, sunday)


def _restaurant_profile(
    *,
    weekly_hours: tuple[OpeningHours | None, ...],
    max_party_size: int,
    extra_closures: frozenset[str] = frozenset(),
) -> VenueProfile:
    return VenueProfile(
        weekly_hours=weekly_hours,
        max_party_size=max_party_size,
        minimum_stay_minutes=90,
        closed_dates=STATUTORY_CLOSURES | extra_closures,
        sold_out_dates=COMMON_SELLOUT_DATES,
    )


def _recreation_profile(
    *,
    weekly_hours: tuple[OpeningHours | None, ...],
    max_party_size: int,
) -> VenueProfile:
    return VenueProfile(
        weekly_hours=weekly_hours,
        max_party_size=max_party_size,
        minimum_stay_minutes=60,
        closed_dates=RECREATION_CLOSURES,
        sold_out_dates=RECREATION_SELLOUT_DATES,
    )


#: Profile applied to venues the catalog does not know, so unrecognized names
#: still behave like a plausible KW venue instead of booking around the clock.
DEFAULT_VENUE_PROFILE = VenueProfile(
    weekly_hours=_week(weekday=_hours("11:00", "22:00"), friday=_hours("11:00", "23:00")),
    max_party_size=12,
    minimum_stay_minutes=90,
    closed_dates=STATUTORY_CLOSURES,
    sold_out_dates=COMMON_SELLOUT_DATES,
)


VENUE_CATALOG: tuple[Venue, ...] = (
    Venue(
        name="The Bauer Kitchen",
        venue_type=VenueType.RESTAURANT,
        aliases=("bauer",),
        profile=_restaurant_profile(
            weekly_hours=_week(
                weekday=_hours("11:30", "22:00"),
                friday=_hours("11:30", "23:00"),
                sunday=_hours("10:00", "21:00"),
            ),
            max_party_size=10,
        ),
    ),
    Venue(
        name="Proof Kitchen + Lounge",
        venue_type=VenueType.RESTAURANT,
        aliases=("proof",),
        profile=_restaurant_profile(
            weekly_hours=_week(
                weekday=_hours("11:00", "22:00"),
                friday=_hours("11:00", "23:30"),
                sunday=_hours("11:00", "21:00"),
            ),
            max_party_size=12,
        ),
    ),
    Venue(
        name="The Charcoal Steak House",
        venue_type=VenueType.RESTAURANT,
        aliases=("charcoal",),
        profile=_restaurant_profile(
            weekly_hours=_week(
                weekday=_hours("16:30", "22:00"),
                friday=_hours("16:30", "23:00"),
                sunday=_hours("16:00", "21:00"),
                monday_closed=True,
            ),
            max_party_size=14,
        ),
    ),
    Venue(
        name="Golf's Steak House",
        venue_type=VenueType.RESTAURANT,
        aliases=("golfs",),
        profile=_restaurant_profile(
            weekly_hours=_week(
                weekday=_hours("17:00", "22:00"),
                friday=_hours("17:00", "23:00"),
                sunday=None,
                monday_closed=True,
            ),
            max_party_size=8,
        ),
    ),
    Venue(
        name="Ethel's Lounge",
        venue_type=VenueType.RESTAURANT,
        aliases=("ethels",),
        profile=_restaurant_profile(
            weekly_hours=_week(
                weekday=_hours("12:00", "23:00"),
                friday=_hours("12:00", "23:45"),
            ),
            max_party_size=8,
        ),
    ),
    Venue(
        name="Grand Trunk Saloon",
        venue_type=VenueType.RESTAURANT,
        aliases=("grand trunk",),
        profile=_restaurant_profile(
            weekly_hours=_week(
                weekday=_hours("16:00", "23:00"),
                friday=_hours("16:00", "23:45"),
                sunday=_hours("16:00", "22:00"),
            ),
            max_party_size=12,
        ),
    ),
    Venue(
        name="Grand River Rocks",
        venue_type=VenueType.RECREATION,
        aliases=("grr", "grand river rocks climbing gym"),
        profile=_recreation_profile(
            weekly_hours=_week(
                weekday=_hours("06:00", "23:00"),
                saturday=_hours("09:00", "22:00"),
                sunday=_hours("09:00", "21:00"),
            ),
            max_party_size=20,
        ),
    ),
    Venue(
        name="Waterloo Bowling Lanes",
        venue_type=VenueType.RECREATION,
        aliases=("waterloo bowling",),
        profile=_recreation_profile(
            weekly_hours=_week(
                weekday=_hours("12:00", "22:00"),
                friday=_hours("12:00", "00:00"),
                saturday=_hours("10:00", "00:00"),
                sunday=_hours("10:00", "21:00"),
            ),
            max_party_size=24,
        ),
    ),
    Venue(
        name="Chicopee Tube Park",
        venue_type=VenueType.RECREATION,
        aliases=("chicopee",),
        profile=_recreation_profile(
            weekly_hours=_week(
                weekday=None,
                friday=_hours("16:00", "21:00"),
                saturday=_hours("10:00", "21:00"),
                sunday=_hours("10:00", "17:00"),
            ),
            max_party_size=30,
        ),
    ),
)


def _normalize(value: str) -> str:
    """Casefold, strip accents, and reduce punctuation to single spaces."""

    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    # Apostrophes are dropped rather than split on, so "Ethel's Lounge" and
    # "ethels lounge" normalize to the same key.
    without_apostrophes = without_accents.replace("'", "").replace("\u2019", "")
    cleaned = "".join(
        char if char.isalnum() else " " for char in without_apostrophes.casefold()
    )
    return " ".join(cleaned.split())


def _match_tokens(value: str) -> frozenset[str]:
    return frozenset(_normalize(value).split()) - _WORDS_IGNORED_WHEN_MATCHING


def _venue_keys(venue: Venue) -> tuple[str, ...]:
    return (_normalize(venue.name), *(_normalize(alias) for alias in venue.aliases))


def resolve_venue(venue_name: str) -> VenueResolution:
    """Match untrusted venue text to at most one catalog venue.

    An exact name or alias always wins. Otherwise a partial name is accepted
    only when it identifies exactly one venue; anything matching several is
    reported as ambiguous so the caller can ask instead of guessing.
    """

    query = _normalize(venue_name)
    if not query:
        return VenueResolution(status=VenueResolutionStatus.UNKNOWN)

    exact = [venue for venue in VENUE_CATALOG if query in _venue_keys(venue)]
    if len(exact) == 1:
        return VenueResolution(
            status=VenueResolutionStatus.RESOLVED,
            venue=exact[0],
            candidates=(exact[0],),
        )
    if exact:
        return VenueResolution(
            status=VenueResolutionStatus.AMBIGUOUS,
            candidates=tuple(exact),
        )

    query_tokens = _match_tokens(venue_name)
    if not query_tokens:
        return VenueResolution(status=VenueResolutionStatus.UNKNOWN)

    partial = [
        venue
        for venue in VENUE_CATALOG
        if any(
            query_tokens <= _match_tokens(key)
            for key in (venue.name, *venue.aliases)
        )
    ]
    if len(partial) == 1:
        return VenueResolution(
            status=VenueResolutionStatus.RESOLVED,
            venue=partial[0],
            candidates=(partial[0],),
        )
    if partial:
        return VenueResolution(
            status=VenueResolutionStatus.AMBIGUOUS,
            candidates=tuple(partial),
        )
    return VenueResolution(status=VenueResolutionStatus.UNKNOWN)


def profile_for(venue_name: str) -> VenueProfile:
    """Return catalog scheduling rules, or the generic KW profile."""

    resolution = resolve_venue(venue_name)
    if resolution.venue is not None:
        return resolution.venue.profile
    return DEFAULT_VENUE_PROFILE
