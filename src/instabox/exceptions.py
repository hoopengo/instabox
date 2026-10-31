"""Exception hierarchy for the InstaBox API.

Every error the API can return is described at https://instabox.sh/docs/errors.
The SDK maps HTTP status codes to exception subclasses; the machine-readable
``code`` and the ``retryable`` flag from the response body are always available
on :class:`APIError`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "APIError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "InstaboxError",
    "NotFoundError",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "TimeoutExceededError",
]


class InstaboxError(Exception):
    """Base class for every error raised by this SDK."""


class APIError(InstaboxError):
    """The InstaBox API returned a structured error response.

    Attributes:
        status: HTTP status code.
        code: Machine-readable error identifier (e.g. ``no_capacity``).
        message: Human-readable description.
        request_id: Server-side request identifier (``req_...``), useful in
            support requests.
        retryable: Whether the same request may succeed later.
        retry_after_seconds: Server-suggested wait time, when present.
        docs_url: Link to the error's documentation entry, when present.
        operation_id: Conflicting operation identifier, when applicable.
        response: The full decoded error body, when available.
    """

    def __init__(
        self,
        *,
        status: int,
        code: str = "unknown",
        message: str = "",
        request_id: str | None = None,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        docs_url: str | None = None,
        operation_id: str | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message or code
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.docs_url = docs_url
        self.operation_id = operation_id
        self.response = response
        super().__init__(f"[{status} {code}] {self.message}")

    @classmethod
    def from_body(cls, status: int, body: Any) -> APIError:
        """Build an :class:`APIError` from an HTTP status and response body."""
        payload: dict[str, Any] = {}
        if isinstance(body, dict):
            error = body.get("error")
            payload = error if isinstance(error, dict) else body
        return cls(
            status=status,
            code=str(payload.get("code", "unknown")),
            message=str(payload.get("message", "")),
            request_id=payload.get("requestId"),
            retryable=bool(payload.get("retryable", False)),
            retry_after_seconds=payload.get("retryAfterSeconds"),
            docs_url=payload.get("docs_url"),
            operation_id=payload.get("operationId"),
            response=payload or None,
        )


class BadRequestError(APIError):
    """HTTP 400 — malformed request."""


class AuthenticationError(APIError):
    """HTTP 401 — missing, invalid, expired or revoked API key."""


class PaymentRequiredError(APIError):
    """HTTP 402 — billing limits reached (credits, spend cap, subscription)."""


class PermissionDeniedError(APIError):
    """HTTP 403 — key lacks scope or the account lacks permission."""


class NotFoundError(APIError):
    """HTTP 404 — resource does not exist or belongs to another workspace."""


class ConflictError(APIError):
    """HTTP 409 — conflicting operation, state or idempotency key."""


class RateLimitError(APIError):
    """HTTP 429 — quota, rate or concurrency limit reached."""


class ServerError(APIError):
    """HTTP 5xx — server-side failure; may be retryable."""


class TimeoutExceededError(InstaboxError):
    """A command executed via ``exec`` hit its ``timeoutSeconds`` limit."""


_STATUS_TO_EXCEPTION: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


def raise_for_status(status: int, body: Any) -> None:
    """Raise the appropriate :class:`APIError` subclass for a status."""
    if 200 <= status < 300:
        return
    exc_cls = _STATUS_TO_EXCEPTION.get(status, ServerError)
    raise exc_cls.from_body(status, body)
