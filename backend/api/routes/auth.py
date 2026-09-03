"""Account endpoints: signup, login, logout, and the current user (Milestone 5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import bearer_token, get_auth_service
from backend.models.account import User
from backend.services.auth_service import AuthenticationRequiredError, AuthService


router = APIRouter(prefix="/api/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


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
async def signup(body: Credentials, service: AuthServiceDep) -> AuthResponse:
    user, token = await service.signup(body.email, body.password)
    return AuthResponse(token=token, user=user)


@router.post("/login", response_model=AuthResponse)
async def login(body: Credentials, service: AuthServiceDep) -> AuthResponse:
    user, token = await service.login(body.email, body.password)
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
