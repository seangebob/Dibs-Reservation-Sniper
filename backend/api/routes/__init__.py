"""FastAPI routers grouped by resource."""

from backend.api.routes.watches import router as watches_router

__all__ = ["watches_router"]
