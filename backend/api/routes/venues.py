"""The venue catalog, read-only, for the manual watch form's suggestions."""

from fastapi import APIRouter
from pydantic import BaseModel

from backend.data.venues import VENUE_CATALOG
from backend.orchestrator.schemas import VenueType


router = APIRouter(prefix="/api/venues", tags=["venues"])


class VenueSummary(BaseModel):
    name: str
    venue_type: VenueType
    aliases: list[str]


#: Built once: the catalog is fixed for the life of the process.
_CATALOG = [
    VenueSummary(name=venue.name, venue_type=venue.venue_type, aliases=list(venue.aliases))
    for venue in VENUE_CATALOG
]


@router.get(
    "",
    response_model=list[VenueSummary],
    summary="List the venues Dibs has scheduling rules for",
)
async def list_venues() -> list[VenueSummary]:
    """Every catalog venue, in catalog order.

    Suggestions, not a whitelist: a watch for a name outside this list is still
    accepted and scheduled with the generic Kitchener-Waterloo profile.
    """

    return _CATALOG
