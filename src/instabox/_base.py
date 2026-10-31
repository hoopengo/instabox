"""Shared internals: headers, options and small helpers used by both clients."""

from __future__ import annotations

import typing as t
import uuid
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://api.instabox.sh"
LONG_POLL_SECONDS = "25s"  # max allowed by GET /v1/operations?wait=
UPLOAD_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB


@dataclass(frozen=True)
class ClientOptions:
    """Common configuration shared by the sync and async clients."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 60.0
    default_headers: dict[str, str] | None = None


def new_idempotency_key() -> str:
    """Generate a fresh idempotency key (any unique string works)."""
    return str(uuid.uuid4())


def build_headers(api_key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build authentication and content headers for a request."""
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def json_body(data: dict[str, Any] | None) -> dict[str, Any]:
    """Drop ``None`` values from a request body so omitted fields stay unchanged."""
    return {k: v for k, v in (data or {}).items() if v is not None}


def parse_sse(lines: t.Iterable[str]) -> t.Iterator[tuple[str, str]]:
    """Parse a ``text/event-stream`` into ``(event, data)`` pairs.

    Handles multi-line ``data:`` payloads (joined with newlines), comments and
    blank-line event separators, per the SSE specification.
    """
    event = ""
    data: list[str] = []
    for line in lines:
        line = line.rstrip("\r")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):
            continue  # comment
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield event or "message", "\n".join(data)
