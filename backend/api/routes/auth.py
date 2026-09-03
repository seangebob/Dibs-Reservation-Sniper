"""Account endpoints: signup, login, logout, and the current user (Milestone 5)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.client_identity import extract_client_id
from backend.api.dependencies import bearer_token, get_auth_service
from backend.models.account import User
from backend.services.auth_service import (
    AuthenticationRequiredError,
    AuthService,
    InvalidCredentialsError,
)
from backend.services.login_throttle import LoginThrottle, throttle_key


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
    x_dibs_client_id: ClientIdHeader = None,
) -> AuthResponse:
    user, token = await service.signup(body.email, body.password)
    await _claim_anonymous_watches(request, user, x_dibs_client_id)
    return AuthResponse(token=token, user=user)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    body: Credentials,
    service: AuthServiceDep,
    x_dibs_client_id: ClientIdHeader = None,
) -> AuthResponse:
    # Best-effort brute-force throttle (Req 6.4): only failures count, and a
    # success clears the window, so a fumbled password is never penalized.
    throttle: LoginThrottle | None = getattr(
        request.app.state, "login_throttle", None
    )
    key = throttle_key(body.email, request.headers.get("origin"))
    if throttle is not None:
        throttle.check(key)
    try:
        user, token = await service.login(body.email, body.password)
    except InvalidCredentialsError:
        if throttle is not None:
            throttle.record_failure(key)
        raise
    if throttle is not None:
        throttle.reset(key)
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
