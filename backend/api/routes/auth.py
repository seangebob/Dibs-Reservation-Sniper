"""Account endpoints: signup, login, logout, the current user (Milestone 5),
and password reset by emailed link (roadmap 3b)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.client_identity import extract_client_id
from backend.api.dependencies import bearer_token, get_auth_service
from backend.models.account import User
from backend.services.auth_service import (
    AuthenticationRequiredError,
    AuthService,
    InvalidCredentialsError,
)
from backend.services.throttle import (
    RedisSlidingWindowThrottle,
    SlidingWindowThrottle,
    is_internal_address,
    login_email_key,
    login_ip_key,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

ClientIdHeader = Annotated[
    str | None,
    Header(description="Opaque anonymous client id; its watches are claimed on auth"),
]


async def _claim_anonymous_watches(
    request: Request, user: User, x_dibs_client_id: str | None
) -> None:
    """Reassign the caller's anonymous watches to their account (Requirement 4).

    Best-effort, mirroring the history projection's own write path: a claim
    failure is logged but never fails signup/login, which have already
    succeeded. A no-op when there is no client id or no projection configured;
    idempotent when there is (the repository's ``user_id IS NULL`` guard)."""

    client_id = extract_client_id(x_dibs_client_id)
    history = getattr(request.app.state, "watch_history", None)
    if client_id is None or history is None:
        return
    try:
        await history.claim_anonymous(client_id, user.id)
    except Exception:
        logger.warning(
            "failed to claim anonymous watches on auth", exc_info=True
        )


_warned_about_proxy = False


def _warn_untrusted_proxy_once(peer: str | None) -> None:
    global _warned_about_proxy
    if _warned_about_proxy:
        return
    _warned_about_proxy = True
    logger.warning(
        "Logins arrive from internal address %s, so the per-address login "
        "limit is off. Behind a proxy, set FORWARDED_ALLOW_IPS so the real "
        "client address is used.",
        peer,
    )


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Loose bounds only; the real email shape + password policy live in
    # AuthService. max_length caps input before argon2 to blunt a DoS.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class AuthResponse(BaseModel):
    token: str
    user: User


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    request: Request,
    body: Credentials,
    service: AuthServiceDep,
    background: BackgroundTasks,
    x_dibs_client_id: ClientIdHeader = None,
) -> AuthResponse:
    user, token = await service.signup(body.email, body.password)
    await _claim_anonymous_watches(request, user, x_dibs_client_id)
    # The confirmation email goes out after the response, so a slow relay
    # never slows sign-up down.
    background.add_task(_send_email_confirmation, service, user.id)
    return AuthResponse(token=token, user=user)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    body: Credentials,
    service: AuthServiceDep,
    x_dibs_client_id: ClientIdHeader = None,
) -> AuthResponse:
    # Best-effort brute-force throttle (Req 6.4): only failures count. Two
    # limits: per email (guessing one account's password) and a looser one per
    # client address (trying many emails). A success clears only the email
    # window; clearing the address window would let an attacker reset it by
    # logging into their own account between guesses.
    email_throttle: SlidingWindowThrottle | RedisSlidingWindowThrottle | None = getattr(
        request.app.state, "login_throttle", None
    )
    ip_throttle: SlidingWindowThrottle | RedisSlidingWindowThrottle | None = getattr(
        request.app.state, "login_ip_throttle", None
    )
    email_key = login_email_key(body.email)
    peer = request.client.host if request.client else None
    ip_key = login_ip_key(peer)
    if ip_throttle is not None and is_internal_address(peer):
        # Every user would share this address (see is_internal_address).
        _warn_untrusted_proxy_once(peer)
        ip_throttle = None
    email_token = await email_throttle.aconsume(email_key) if email_throttle else None
    ip_token = None
    try:
        if ip_throttle is not None:
            ip_token = await ip_throttle.aconsume(ip_key)
    except BaseException:
        if email_throttle is not None:
            await email_throttle.arelease(email_key, email_token)
        raise
    try:
        user, token = await service.login(body.email, body.password)
    except InvalidCredentialsError:
        # Keep the reservations as failed attempts. Pending attempts already
        # count, so simultaneous guesses cannot all pass an empty window.
        raise
    except BaseException:
        if email_throttle is not None:
            await email_throttle.arelease(email_key, email_token)
        if ip_throttle is not None:
            await ip_throttle.arelease(ip_key, ip_token)
        raise
    if email_throttle is not None:
        await email_throttle.areset(email_key)
    if ip_throttle is not None:
        await ip_throttle.arelease(ip_key, ip_token)
    await _claim_anonymous_watches(request, user, x_dibs_client_id)
    return AuthResponse(token=token, user=user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    service: AuthServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    token = bearer_token(authorization)
    if token:
        await service.logout(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=User)
async def me(
    service: AuthServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    user = await service.authenticate(bearer_token(authorization))
    if user is None:
        raise AuthenticationRequiredError()
    return user


# -- password reset ------------------------------------------------------------


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


async def _send_password_reset(service: AuthService, email: str) -> None:
    # Runs after the response; a failure is logged without the address.
    try:
        await service.send_password_reset(email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Password reset email failed: %s", type(exc).__name__)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    body: ForgotPasswordRequest,
    service: AuthServiceDep,
    background: BackgroundTasks,
) -> Response:
    """Email a reset link if the account exists. Always the same 202, sent
    before any account lookup, so neither the answer nor its timing says
    whether an account exists."""

    normalized = service.prepare_password_reset(body.email)
    background.add_task(_send_password_reset, service, normalized)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password/reset", response_model=AuthResponse)
async def reset_password(body: ResetPasswordRequest, service: AuthServiceDep) -> AuthResponse:
    """Use a reset link: new password, every other session signed out, and a
    fresh session returned for this browser."""

    user, token = await service.reset_password(body.token, body.password)
    return AuthResponse(token=token, user=user)


# -- password change -----------------------------------------------------------


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.post("/password/change", response_model=AuthResponse)
async def change_password(
    body: ChangePasswordRequest,
    service: AuthServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthResponse:
    """Change the signed-in account's password. Signs out every session and
    returns a fresh one for this browser."""

    user, token = await service.change_password(
        bearer_token(authorization), body.current_password, body.new_password
    )
    return AuthResponse(token=token, user=user)


# -- email confirmation ----------------------------------------------------------


class ConfirmEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)


async def _send_email_confirmation(service: AuthService, user_id: UUID) -> None:
    # Runs after the response; a failure is logged without the address.
    try:
        await service.send_email_confirmation(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Confirmation email failed: %s", type(exc).__name__)


@router.post("/email/confirm", response_model=User)
async def confirm_email(body: ConfirmEmailRequest, service: AuthServiceDep) -> User:
    """Use a confirmation link; the account's address can now get alerts."""

    return await service.confirm_email(body.token)


@router.post("/email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_confirmation(
    service: AuthServiceDep,
    background: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Send the signed-in account another confirmation link (3 an hour). A
    no-op once the address is confirmed."""

    user = await service.prepare_confirmation_resend(bearer_token(authorization))
    background.add_task(_send_email_confirmation, service, user.id)
    return Response(status_code=status.HTTP_202_ACCEPTED)
