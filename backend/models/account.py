"""Account and bearer-session models (Milestone 5)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    """Public account fields.

    Deliberately carries no password hash, so a `User` can be returned in any
    API response or logged without leaking a secret (Requirement 6.3).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    email: str
    created_at: datetime


class StoredUser(User):
    """Internal record: the public fields plus the argon2 hash for verification.

    Never serialized to a client. Login reads it, verifies the password, and
    returns `to_public()`; the hash never leaves the service boundary.
    """

    password_hash: str

    def to_public(self) -> User:
        return User(id=self.id, email=self.email, created_at=self.created_at)


class Session(BaseModel):
    """One opaque bearer session. Only `token_hash` (sha256 of the raw token,
    which is returned to the client once) is ever stored."""

    model_config = ConfigDict(extra="forbid")

    token_hash: str
    user_id: UUID
    created_at: datetime
    expires_at: datetime
