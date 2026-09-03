"""Password hashing service (Milestone 5, Task 2)."""

from backend.config import AccountSettings
from backend.services.password import build_password_hasher


def _fast_service():
    # Low cost params keep the suite fast; the properties under test (salted,
    # verifiable, non-raising) are independent of the argon2 cost.
    return build_password_hasher(
        AccountSettings(
            argon2_time_cost=1,
            argon2_memory_cost_kib=8_192,
            argon2_parallelism=1,
        )
    )


def test_hash_is_verifiable() -> None:
    svc = _fast_service()
    encoded = svc.hash("correct horse battery")
    assert svc.verify(encoded, "correct horse battery") is True


def test_wrong_password_fails() -> None:
    svc = _fast_service()
    encoded = svc.hash("s3cret-password")
    assert svc.verify(encoded, "not-the-password") is False


def test_hash_never_contains_the_plaintext() -> None:
    svc = _fast_service()
    encoded = svc.hash("plaintext-secret")
    assert "plaintext-secret" not in encoded
    assert encoded.startswith("$argon2id$")


def test_same_password_hashes_differently_each_time() -> None:
    svc = _fast_service()
    first = svc.hash("same-password")
    second = svc.hash("same-password")
    assert first != second  # random per-hash salt
    assert svc.verify(first, "same-password")
    assert svc.verify(second, "same-password")


def test_verify_on_a_malformed_hash_returns_false_not_raises() -> None:
    svc = _fast_service()
    assert svc.verify("not-a-real-hash", "whatever") is False


def test_dummy_verify_never_raises() -> None:
    svc = _fast_service()
    # The unknown-email login path calls this; it must never raise.
    svc.dummy_verify("anything")
    svc.dummy_verify("")
