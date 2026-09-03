"""Watch endpoints: create, inspect, and cancel background monitoring."""

from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from backend.api.client_identity import extract_client_id
from backend.api.dependencies import current_user, get_watch_service
from backend.models.account import User
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch
from backend.services.watch_policy import AvailabilityPolicy
from backend.services.watch_service import WatchService


router = APIRouter(prefix="/api/watches", tags=["watches"])

WatchServiceDep = Annotated[WatchService, Depends(get_watch_service)]
CurrentUser = Annotated[User | None, Depends(current_user)]


async def _forbid_if_owned_by_another_account(
    request: Request, watch_id: str, user: User | None
) -> None:
    """Deny access to an account-owned watch requested by a different (or no)
    account, indistinguishably from a missing watch (Requirement 3.3).

    Reads the projection only. When the projection is disabled or the watch is
    anonymous-owned, this is a no-op and the Milestone 1-4 by-id behavior
    stands (Requirement 3.4)."""

    history = getattr(request.app.state, "watch_history", None)
    if history is None:
        return
    account_owner = await history.get_account_owner(watch_id)
    if account_owner is not None and (user is None or account_owner != user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown watch: {watch_id}",
        )


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
    user: CurrentUser,
    auto_book: Annotated[
        bool,
        Query(description="Book the first matching slot instead of only notifying"),
    ] = False,
    x_dibs_client_id: Annotated[
        str | None,
        Header(
            description="Opaque anonymous client id; scopes 'my watches' "
            "listing only, not a real access boundary"
        ),
    ] = None,
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
    owner_client_id = extract_client_id(x_dibs_client_id)
    watch = await service.create(
        query,
        auto_book=auto_book,
        owner_client_id=owner_client_id,
        user_id=user.id if user else None,
    )
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


@router.get(
    "/mine",
    response_model=list[Watch],
    summary="List the calling client's own watches",
)
async def list_my_watches(
    request: Request,
    user: CurrentUser,
    x_dibs_client_id: Annotated[
        str | None,
        Header(description="Opaque anonymous client id from watch creation"),
    ] = None,
) -> list[Watch]:
    """Durable, owner-scoped listing backed by the history projection.

    An authenticated caller sees the account's watches, scoped by `user_id`
    (Requirement 3.2, a real access boundary). An anonymous caller keeps the
    Milestone 4 behavior: scoped by `X-Dibs-Client-Id`, a convenience listing
    that makes no access-control claim (Requirement 2.5).

    Returns an empty list -- never an error -- when there is nothing to scope by
    or when the history projection is disabled (PostgreSQL not configured).
    Declared before `/{watch_id}` so "mine" is never matched as a watch id.
    """

    history = getattr(request.app.state, "watch_history", None)
    if history is None:
        return []
    if user is not None:
        return await history.list_for_user(user.id)
    owner_client_id = extract_client_id(x_dibs_client_id)
    if owner_client_id is None:
        return []
    return await history.list_for_owner(owner_client_id)


@router.get("/{watch_id}", response_model=Watch, summary="Read one watch")
async def read_watch(
    request: Request, watch_id: str, service: WatchServiceDep, user: CurrentUser
) -> Watch:
    await _forbid_if_owned_by_another_account(request, watch_id, user)
    watch = await service.get(watch_id)
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown watch: {watch_id}",
        )
    return watch


@router.delete("/{watch_id}", response_model=Watch, summary="Cancel a watch")
async def cancel_watch(
    request: Request, watch_id: str, service: WatchServiceDep, user: CurrentUser
) -> Watch:
    await _forbid_if_owned_by_another_account(request, watch_id, user)
    watch = await service.cancel(watch_id)
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown watch: {watch_id}",
        )
    return watch
