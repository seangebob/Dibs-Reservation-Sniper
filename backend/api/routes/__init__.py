"""FastAPI routers grouped by resource."""

from backend.api.routes.auth import router as auth_router
from backend.api.routes.watches import router as watches_router

__all__ = ["auth_router", "watches_router"]
