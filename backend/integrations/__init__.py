"""External reservation platform adapters."""

from backend.integrations.base import ReservationAdapter
from backend.integrations.mock_booking import MockBookingAdapter

__all__ = ["MockBookingAdapter", "ReservationAdapter"]
