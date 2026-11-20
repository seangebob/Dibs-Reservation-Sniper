"""Email alerts for guests: give an address, confirm it by link (roadmap 3b).

Every route but `verify` acts on the calling browser, named by the anonymous
`X-Dibs-Client-Id` header that already owns its watches. `verify` takes only the
link's token, so the link works from any device. Signed-in accounts don't use
these: their alerts go to the account's address.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.client_identity import extract_client_id
from backend.api.dependencies import get_guest_alerts
from backend.services.guest_alerts import GuestAlertService


router = APIRouter(prefix="/api/alerts", tags=["alerts"])

ClientIdHeader = Annotated[
    str | None,
    Header(description="Opaque anonymous client id that owns this browser's watches"),
]
GuestAlertsDep = Annotated[GuestAlertService, Depends(get_guest_alerts)]


class AlertEmail(BaseModel):
    """The address this browser's alerts go to, or null when there is none."""

    email: str | None


class AlertEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)


def _client(raw: str | None) -> str:
    client_id = extract_client_id(raw)
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This browser has no Dibs id yet. Reload the page and try again.",
        )
    return client_id


@router.get("/email", response_model=AlertEmail, summary="This browser's alert address")
async def get_alert_email(
    service: GuestAlertsDep, x_dibs_client_id: ClientIdHeader = None
) -> AlertEmail:
    return AlertEmail(email=await service.email_for_client(_client(x_dibs_client_id)))


@router.post(
    "/email",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a confirmation link for this browser's alerts",
)
async def request_alert_email(
    body: AlertEmailRequest,
    service: GuestAlertsDep,
    x_dibs_client_id: ClientIdHeader = None,
) -> Response:
    """Alerts start only once the link is opened, so a typo or someone else's
    address never receives anything but the one confirmation email."""

    await service.request_verification(_client(x_dibs_client_id), body.email)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/email/verify", response_model=AlertEmail, summary="Confirm an alert address")
async def verify_alert_email(body: VerifyRequest, service: GuestAlertsDep) -> AlertEmail:
    return AlertEmail(email=await service.verify(body.token))


@router.delete(
    "/email",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop alerts to this browser's address",
)
async def remove_alert_email(
    service: GuestAlertsDep, x_dibs_client_id: ClientIdHeader = None
) -> Response:
    await service.remove(_client(x_dibs_client_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
