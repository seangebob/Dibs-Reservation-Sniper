# Unattended container image for the Dibs backend.
#
# One image runs both roles; select the role by overriding the command.
#
#   API (default):
#     python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
#
#   Worker (override CMD):
#     celery -A backend.workers.celery_app worker --loglevel=info
#
# Pinned base for reproducible builds -- a specific patch tag, never 3.12/latest.
FROM python:3.12.7-slim

# Predictable, log-friendly Python behaviour inside the container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy only what the build needs before install so the dependency layer is
# cached independently of later source-only changes.
COPY pyproject.toml ./
COPY backend/ ./backend/

# Install the project with the worker extra so the SAME image can run the API
# and the Celery worker. The test extra is intentionally NOT installed -- this
# is a runtime image, not a test image.
RUN pip install --no-cache-dir ".[worker]"

# Run as a dedicated non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Finite, stdlib-only health check against the API /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# Default role: API server, no reload/watcher.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
