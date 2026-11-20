"""FastAPI routers grouped by resource."""

from backend.api.routes.alerts import router as alerts_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.venues import router as venues_router
from backend.api.routes.watches import router as watches_router

__all__ = ["alerts_router", "auth_router", "venues_router", "watches_router"]
