"""Watch endpoints: create, inspect, and cancel background monitoring."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.dependencies import get_watch_service
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch
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
    query: AvailabilityQuery,
    service: WatchServiceDep,
    auto_book: Annotated[
        bool,
        Query(description="Book the first matching slot instead of only notifying"),
    ] = False,
) -> Watch:
    return await service.create(query, auto_book=auto_book)


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
