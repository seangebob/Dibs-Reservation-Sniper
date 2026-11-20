"""Build the reservation adapter this deployment is configured to use.

The API and every worker build their adapter here, from the same
`RESERVATION_PROVIDER` setting, so they can never disagree about where
availability comes from. Adding a real provider means one more branch below
and its name in `backend.config.RESERVATION_PROVIDERS`.
"""

from backend.config import MOCK_RESERVATION_PROVIDER, ConfigurationError
from backend.db.repositories.mock_booking import MockBookingStateRepository
from backend.integrations.base import ReservationAdapter
from backend.integrations.mock_booking import MockBookingAdapter


def build_reservation_adapter(
    provider: str,
    *,
    mock_state: MockBookingStateRepository,
) -> ReservationAdapter:
    """The adapter for `provider`.

    `mock_state` is the simulation's shared store; only the mock uses it.
    """

    if provider == MOCK_RESERVATION_PROVIDER:
        return MockBookingAdapter(state=mock_state)
    # `WatchSettings` rejects unknown names at startup, so reaching this means
    # a name was registered there without an adapter here.
    raise ConfigurationError(f"No adapter is registered for provider {provider!r}")
