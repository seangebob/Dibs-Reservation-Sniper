"""The API and the worker build their reservation adapter in one place."""

import pytest

from backend.config import ConfigurationError
from backend.db.repositories.mock_booking import in_memory_mock_state
from backend.integrations.mock_booking import MockBookingAdapter
from backend.integrations.registry import build_reservation_adapter


def test_mock_builds_the_simulation_over_the_shared_state() -> None:
    state = in_memory_mock_state(capacity=10, idle_ttl_seconds=60, retention_seconds=60)

    adapter = build_reservation_adapter("mock", mock_state=state)

    assert isinstance(adapter, MockBookingAdapter)


def test_a_name_without_an_adapter_is_a_configuration_error() -> None:
    state = in_memory_mock_state(capacity=10, idle_ttl_seconds=60, retention_seconds=60)

    with pytest.raises(ConfigurationError, match="kw-tables"):
        build_reservation_adapter("kw-tables", mock_state=state)
