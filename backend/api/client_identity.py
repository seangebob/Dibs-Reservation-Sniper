"""The anonymous `X-Dibs-Client-Id` header (Milestone 4, Requirement 2).

There is no authentication in this milestone: the header is an opaque token a
visitor's browser generates and persists itself (see design.md), used only to
scope "my watches" on the dashboard. It is never used to gate read/cancel
access to a specific watch by id (Requirement 2.5) -- knowing a watch's id
remains sufficient there, exactly as before this milestone.
"""

from __future__ import annotations

import re


__all__ = ["extract_client_id"]


#: An opaque, client-generated token -- not a real identity, so the shape
#: check exists only to keep obviously-garbage input out of storage/logs, not
#: to enforce any particular ID scheme. Alphanumeric plus dash/underscore
#: comfortably covers a UUID, a nanoid, or a hex token.
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def extract_client_id(raw: str | None) -> str | None:
    """Validate an `X-Dibs-Client-Id` header value.

    A missing or malformed value degrades to `None` (anonymous/unscoped)
    rather than rejecting the request -- there is no access boundary being
    enforced, so failing loudly here would be pretending to a security
    guarantee this milestone does not provide (Requirement 2.2, design.md's
    Error Handling section).
    """

    if raw is None:
        return None
    candidate = raw.strip()
    if not _CLIENT_ID_PATTERN.fullmatch(candidate):
        return None
    return candidate
