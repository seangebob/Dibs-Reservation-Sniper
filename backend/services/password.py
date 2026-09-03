"""Password hashing for Milestone 5 accounts.

Wraps argon2id with the configured cost parameters and centralizes two
security-sensitive rules: only ever persist the argon2 encoded hash (never the
plaintext), and equalize login timing for unknown emails so an attacker cannot
enumerate accounts by measuring how long a login takes.
"""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from backend.config import AccountSettings


#: Verified against — and discarded — for logins where the email is unknown. Its
#: only job is to spend argon2 time so an unknown-email login costs about the
#: same as a real one (see AuthService.login in Task 4).
_TIMING_PASSWORD = "dibs-timing-equalizer"


class PasswordHasherService:
    """argon2id hashing with a precomputed dummy hash for timing equalization."""

    def __init__(self, hasher: PasswordHasher) -> None:
        self._hasher = hasher
        # Precomputed once at construction so dummy_verify() spends roughly the
        # same time as a real verify without hashing on every unknown login.
        self._dummy_hash = hasher.hash(_TIMING_PASSWORD)

    def hash(self, password: str) -> str:
        """Return the argon2 encoded hash (salt + params embedded)."""

        return self._hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        """True iff `password` matches `encoded`; False on mismatch or a
        malformed stored hash — never raising into the caller."""

        try:
            return self._hasher.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def dummy_verify(self, password: str) -> None:
        """Spend argon2 time against a throwaway hash and discard the result, so
        an unknown-email login takes as long as a known-email one."""

        try:
            self._hasher.verify(self._dummy_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            pass


def build_password_hasher(settings: AccountSettings) -> PasswordHasherService:
    """Construct the hashing service from the configured argon2 cost params."""

    return PasswordHasherService(
        PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            type=Type.ID,
        )
    )
