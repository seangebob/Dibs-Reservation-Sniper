"""PostgreSQL storage for guests' verified alert addresses (roadmap 3b).

Thin data access over the asyncpg pool, like `accounts.py`: methods raise on a
Postgres failure and the service decides what that means. Verification links
are stored only as sha256 hashes.
"""

from __future__ import annotations

from datetime import datetime

from backend.db.postgres import PoolLike


__all__ = ["GuestAlertRepository"]


_INSERT_VERIFICATION_SQL = """
INSERT INTO guest_email_verifications (token_hash, client_id, email, expires_at)
VALUES ($1, $2, $3, $4)
""".strip()

#: Marks the link used and returns what it was for, in one statement, so two
#: clicks racing on the same link cannot both succeed.
_CONSUME_VERIFICATION_SQL = """
UPDATE guest_email_verifications
SET used_at = $2
WHERE token_hash = $1 AND used_at IS NULL AND expires_at > $2
RETURNING client_id, email
""".strip()

_UPSERT_EMAIL_SQL = """
INSERT INTO guest_alert_emails (client_id, email, verified_at)
VALUES ($1, $2, $3)
ON CONFLICT (client_id) DO UPDATE
SET email = EXCLUDED.email, verified_at = EXCLUDED.verified_at
""".strip()

_SELECT_EMAIL_SQL = "SELECT email FROM guest_alert_emails WHERE client_id = $1"

_DELETE_EMAIL_SQL = "DELETE FROM guest_alert_emails WHERE client_id = $1"


class GuestAlertRepository:
    """Pending verification links and each guest's verified address."""

    def __init__(self, pool: PoolLike) -> None:
        self._pool = pool

    async def add_verification(
        self, token_hash: str, client_id: str, email: str, expires_at: datetime
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_VERIFICATION_SQL, token_hash, client_id, email, expires_at
            )

    async def consume_verification(
        self, token_hash: str, now: datetime
    ) -> tuple[str, str] | None:
        """Use a link once: its (client_id, email), or None if unknown, used, or expired."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_CONSUME_VERIFICATION_SQL, token_hash, now)
        if not rows:
            return None
        return rows[0]["client_id"], rows[0]["email"]

    async def set_email(self, client_id: str, email: str, verified_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_EMAIL_SQL, client_id, email, verified_at)

    async def email_for_client(self, client_id: str) -> str | None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_EMAIL_SQL, client_id)
        return rows[0]["email"] if rows else None

    async def remove_email(self, client_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_EMAIL_SQL, client_id)
