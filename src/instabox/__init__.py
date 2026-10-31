"""Python SDK for the InstaBox API (https://instabox.sh).

InstaBox gives every agent a Linux machine of its own in the cloud.
This SDK wraps the REST API: create, start, stop, pause and resume machines,
run commands, read and write files, take snapshots, publish ports and track
async operations.

Quick start::

    from instabox import Client

    client = Client(api_key="ibk_...")
    op = client.machines.create(name="dev")
    op = client.operations.wait(op.id)
    result = client.machines.exec("dev", cmd="echo hello")
"""

from __future__ import annotations

from ._policy import ToolPolicy
from .async_client import AsyncClient
from .async_machine import AsyncMachineHandle
from .client import Client
from .exceptions import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InstaboxError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    TimeoutExceededError,
)
from .machine import MachineHandle
from .models import (
    CurrentUser,
    ExecResult,
    FileEntry,
    FileListing,
    Machine,
    Operation,
    Port,
    Snapshot,
    Spec,
    Template,
    UploadUrl,
    Usage,
)

__version__ = "0.1.0"
__all__ = [
    "APIError",
    "AsyncClient",
    "AsyncMachineHandle",
    "AuthenticationError",
    "BadRequestError",
    "Client",
    "ConflictError",
    "CurrentUser",
    "ExecResult",
    "FileEntry",
    "FileListing",
    "InstaboxError",
    "Machine",
    "MachineHandle",
    "NotFoundError",
    "Operation",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "Port",
    "RateLimitError",
    "ServerError",
    "Snapshot",
    "Spec",
    "Template",
    "TimeoutExceededError",
    "ToolPolicy",
    "UploadUrl",
    "Usage",
    "__version__",
]
