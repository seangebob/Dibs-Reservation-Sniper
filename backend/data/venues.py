"""Mock Kitchener-Waterloo venue catalog and deterministic name resolution.

This module is fixture data for the Milestone 2 mock adapter, not a live venue
directory. It exists so slot generation can respect realistic opening hours,
weekday/weekend differences, holiday closures, and per-venue party limits, and
so ambiguous venue names can be detected before anything is booked.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import unicodedata

from backend.orchestrator.schemas import VenueType


SLOT_INTERVAL_MINUTES = 15
MAX_GENERATED_SLOTS = 16

#: Dates on which catalog venues close. Ontario statutory days on which
#: hospitality venues in KW are most consistently shut.
STATUTORY_CLOSURES: frozenset[str] = frozenset(
    {
        "2026-01-01",  # New Year's Day
        "2026-12-25",  # Christmas Day
        "2026-12-26",  # Boxing Day
        "2027-01-01",
        "2027-12-25",
        "2027-12-26",
    }
)

#: Dates the catalog treats as sold out even though the venue is open.
COMMON_SELLOUT_DATES: frozenset[str] = frozenset({"2026-02-14", "2027-02-14"})

_MONDAY = 0
_SUNDAY = 6
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
        closed_dates=STATUTORY_CLOSURES,
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
