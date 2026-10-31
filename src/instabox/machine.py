"""Object-oriented machine handles for model/agent ergonomics.

Instead of calling resource methods, get a handle once and act on it::

    from instabox import Client

    client = Client(api_key="ibk_...")
    machine = client.machine("dev")          # by name or id
    machine.connect()                        # ensure it is running
    result = machine.exec("echo hello")      # machine.exec(cmd=...)
    machine.stop()
    machine.delete()

Every method delegates to the wrapped resources of :class:`instabox.Client`,
so a client-level :class:`instabox.ToolPolicy` is enforced exactly the same
way (operation names such as ``machines.exec``, ``machines.start``, ...).
"""

from __future__ import annotations

import time
import typing as t

from .models import (
    ExecResult,
    FileListing,
    Machine,
    Operation,
    Port,
    Snapshot,
    SshCertificate,
    SshToken,
)

if t.TYPE_CHECKING:
    from .client import Client

__all__ = ["MachineHandle"]


class MachineHandle:
    """A convenient, stateful view of one machine.

    Created with :meth:`instabox.Client.machine`. The handle caches nothing:
    every call hits the API, so state is always fresh.
    """

    def __init__(self, client: Client, id_or_name: str) -> None:
        self._client = client
        self._id_or_name = id_or_name

    # -- Info -----------------------------------------------------------------

    def refresh(self) -> Machine:
        """Fetch and return the current machine state."""
        return self._client.machines.get(self._id_or_name)

    def id(self) -> str:
        """The machine's ID (resolved from a name on first call)."""
        return self.refresh().id

    def is_running(self) -> bool:
        return self.refresh().state == "running"

    def wait_until_ready(self, *, poll_seconds: str = "25s", timeout: float = 600.0) -> Machine:
        """Block until the machine reaches ``running`` (or raise on failure).

        Uses long-polling on the machine's current operation, so no sleeping
        is needed. Raises :class:`instabox.exceptions.APIError` with the
        operation's error when it fails.
        """
        deadline = time.monotonic() + timeout
        machine = self.refresh()
        while machine.operation_id and not machine.runtime_ready:
            if time.monotonic() > deadline:
                raise TimeoutError(f"machine {self._id_or_name} not ready after {timeout}s")
            operation = self._client.operations.get(machine.operation_id, wait=poll_seconds)
            if operation.failed:
                error = operation.error or {}
                from .exceptions import APIError

                raise APIError(
                    status=409,
                    code=str(error.get("code", "unknown")),
                    message=str(error.get("message", "operation failed")),
                    operation_id=operation.id,
                    response=error,
                )
            machine = self.refresh()
            if machine.state == "running":
                return machine
        return machine

    def connect(self) -> Machine:
        """Ensure the machine is running: resumes a paused machine, starts a
        stopped one, waits until ready; returns the running machine."""
        machine = self.refresh()
        if machine.state == "running":
            return machine
        if machine.state == "suspended":
            self._client.machines.resume(machine.id)
        elif machine.state not in ("starting", "resuming", "creating"):
            self._client.machines.start(machine.id)
        return self.wait_until_ready()

    # -- Lifecycle ------------------------------------------------------------

    def start(self) -> Operation:
        return self._client.machines.start(self._id())

    def stop(self, *, timeout_seconds: int | None = None) -> Operation:
        return self._client.machines.stop(self._id(), timeout_seconds=timeout_seconds)

    def suspend(self) -> Operation:
        return self._client.machines.suspend(self._id())

    def resume(self) -> Operation:
        return self._client.machines.resume(self._id())

    def restart(self) -> Operation:
        """Power-cycle: stop, wait, then start again."""
        self.wait_operation(self._client.machines.stop(self._id()))
        return self._client.machines.start(self._id())

    def delete(self) -> Operation:
        return self._client.machines.delete(self._id())

    def wait_operation(self, operation: Operation, *, timeout: float = 600.0) -> Operation:
        """Wait for an operation of this machine to finish."""
        deadline = time.monotonic() + timeout
        op = self._client.operations.get(operation.id, wait="25s")
        while not op.finished:
            if time.monotonic() > deadline:
                raise TimeoutError(f"operation {op.id} not finished after {timeout}s")
            op = self._client.operations.get(operation.id, wait="25s")
        return op

    # -- Commands -------------------------------------------------------------

    def exec(self, cmd: str, **kwargs: t.Any) -> ExecResult:
        """Run a shell command; returns stdout/stderr/exitCode (does not raise on non-zero)."""
        return self._client.machines.exec(self._id(), cmd=cmd, **kwargs)

    def run(self, cmd: str, **kwargs: t.Any) -> ExecResult:
        """Like :meth:`exec` but raises :class:`RuntimeError` on non-zero exit."""
        result = self.exec(cmd, **kwargs)
        if result.exit_code != 0:
            raise RuntimeError(
                f"command failed with exit code {result.exit_code}: {result.stderr!r}"
            )
        return result

    def exec_stream(self, cmd: str, **kwargs: t.Any) -> t.Iterator[t.Any]:
        """Stream command output as SSE events (see ``Client.machines.exec_stream``)."""
        return self._client.machines.exec_stream(self._id(), cmd=cmd, **kwargs)

    # -- Files ----------------------------------------------------------------

    def read_file(self, path: str) -> FileListing:
        return self._client.machines.read_file(self._id(), path)

    def write_file(self, path: str, content: bytes, *, mode: str | None = None) -> dict[str, t.Any]:
        """Write a file, creating missing parent directories."""
        return self._client.machines.upload_file(self._id(), path, content, mode=mode, parents=True)

    def download_file(self, path: str) -> bytes:
        return self._client.machines.download_file(self._id(), path)

    # -- Networking / snapshots / ssh ------------------------------------------

    def publish_port(self, port: int, *, name: str | None = None) -> Port:
        return self._client.machines.publish_port(self._id(), port, name=name)

    def snapshot(self, name: str, **kwargs: t.Any) -> Snapshot:
        return self._client.machines.snapshot(self._id(), name, **kwargs)

    def ssh_token(self, **kwargs: t.Any) -> SshToken:
        return self._client.machines.ssh_tokens.create(self._id(), **kwargs)

    def ssh_certificate(self, public_key: str, **kwargs: t.Any) -> SshCertificate:
        return self._client.machines.ssh_tokens.certificate(self._id(), public_key, **kwargs)

    # -- Config -----------------------------------------------------------------

    def set_metadata(self, metadata: dict[str, str]) -> Machine:
        """Replace the machine's labels (``{}`` clears them)."""
        return self._client.machines.update(self._id(), metadata=metadata)

    def update(self, **kwargs: t.Any) -> Machine:
        return self._client.machines.update(self._id(), **kwargs)

    # -- Internals ---------------------------------------------------------------

    def _id(self) -> str:
        return self.id()
