"""Application logging configuration helpers."""

import logging


def configure_application_logging() -> None:
    """Install a fallback only for the pristine backend logger hierarchy."""

    backend_logger = logging.getLogger("backend")
    if (
        backend_logger.level != logging.NOTSET
        or not backend_logger.propagate
        or backend_logger.disabled
    ):
        return

    current_logger: logging.Logger | None = backend_logger
    while current_logger is not None:
        if current_logger.handlers:
            return
        if not current_logger.propagate:
            return
        current_logger = current_logger.parent

    logging.basicConfig(level=logging.INFO)
