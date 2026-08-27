"""Watch endpoints: create, inspect, and cancel background monitoring."""

from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from backend.api.dependencies import get_watch_service
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch
from backend.services.watch_policy import AvailabilityPolicy
from backend.services.watch_service import WatchService


router = APIRouter(prefix="/api/watches", tags=["watches"])

WatchServiceDep = Annotated[WatchService, Depends(get_watch_service)]


@router.post(
    "",
    response_model=Watch,
    status_code=status.HTTP_201_CREATED,
    summary="Open a watch and dispatch its first background check",
)
async def create_watch(
    request: Request,
    query: AvailabilityQuery,
    service: WatchServiceDep,
    response: Response,
    auto_book: Annotated[
        bool,
        Query(description="Book the first matching slot instead of only notifying"),
    ] = False,
) -> Watch:
    # get_watch_service has already established that settings exist; a missing
    # value is an invariant failure there rather than a UTC fallback here.
    timezone_name = request.app.state.watch_settings.timezone_name
    if date.fromisoformat(query.date) < datetime.now(
        ZoneInfo(timezone_name)
    ).date():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot watch a reservation date in the past: {query.date}",
        )
    watch = await service.create(query, auto_book=auto_book)
    _apply_policy_headers(response, service.describe_policy(watch))
    return watch


def _apply_policy_headers(
    response: Response,
    policy: AvailabilityPolicy,
) -> None:
    """Disclose the effective monitoring policy without changing the body.

    Deadline-capable watches carry only the informational policy/limit headers.
    An attempt-limited watch additionally carries a `Warning`, so a client that
    surfaces standard headers tells the user monitoring may stop early.
    """

    response.headers["X-Watch-Monitoring-Policy"] = policy.monitoring_policy_header
    response.headers["X-Watch-Max-Availability-Checks"] = str(
        policy.effective_attempts
    )
    if policy.is_attempt_limited:
        response.headers["Warning"] = (
            f'199 - "Monitoring may stop after {policy.effective_attempts} '
            'availability checks, before the reservation date."'
        )


@router.get("", response_model=list[Watch], summary="List watches")
async def list_watches(
    service: WatchServiceDep,
    active_only: Annotated[
        bool,
        Query(description="Return only watches the queue is still polling"),
    ] = False,
) -> list[Watch]:
    return await service.list(active_only=active_only)


@router.get("/{watch_id}", response_model=Watch, summary="Read one watch")
async def read_watch(watch_id: str, service: WatchServiceDep) -> Watch:
    watch = await service.get(watch_id)
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown watch: {watch_id}",
        )
    return watch


@router.delete("/{watch_id}", response_model=Watch, summary="Cancel a watch")
async def cancel_watch(watch_id: str, service: WatchServiceDep) -> Watch:
    watch = await service.cancel(watch_id)
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown watch: {watch_id}",
        )
    return watch
