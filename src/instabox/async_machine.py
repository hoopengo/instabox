"""Async object-oriented machine handle (mirror of :class:`instabox.machine.MachineHandle`)."""

from __future__ import annotations

import time
import typing as t

from .exceptions import APIError
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
    from .async_client import AsyncClient

__all__ = ["AsyncMachineHandle"]


class AsyncMachineHandle:
    """Async view of one machine; created with :meth:`instabox.AsyncClient.machine`.

    Method names mirror :class:`instabox.machine.MachineHandle`; every call is
    awaited and delegates to the client's wrapped resources, so a client-level
    :class:`instabox.ToolPolicy` is enforced the same way.
    """

    def __init__(self, client: AsyncClient, id_or_name: str) -> None:
        self._client = client
        self._id_or_name = id_or_name

    # -- Info -----------------------------------------------------------------

    async def refresh(self) -> Machine:
        return await self._client.machines.get(self._id_or_name)

    async def id(self) -> str:
        return (await self.refresh()).id

    async def is_running(self) -> bool:
        return (await self.refresh()).state == "running"

    async def wait_until_ready(
        self, *, poll_seconds: str = "25s", timeout: float = 600.0
    ) -> Machine:
        """Await until the machine reaches ``running``; raises on operation failure."""
        deadline = time.monotonic() + timeout
        machine = await self.refresh()
        while machine.operation_id and not machine.runtime_ready:
            if time.monotonic() > deadline:
                raise TimeoutError(f"machine {self._id_or_name} not ready after {timeout}s")
            operation = await self._client.operations.get(machine.operation_id, wait=poll_seconds)
            if operation.failed:
                error = operation.error or {}
                raise APIError(
                    status=409,
                    code=str(error.get("code", "unknown")),
                    message=str(error.get("message", "operation failed")),
                    operation_id=operation.id,
                    response=error,
                )
            machine = await self.refresh()
            if machine.state == "running":
                return machine
        return machine

    async def connect(self) -> Machine:
        """Ensure the machine is running (resume/start as needed) and wait."""
        machine = await self.refresh()
        if machine.state == "running":
            return machine
        if machine.state == "suspended":
            await self._client.machines.resume(machine.id)
        elif machine.state not in ("starting", "resuming", "creating"):
            await self._client.machines.start(machine.id)
        return await self.wait_until_ready()

    # -- Lifecycle ------------------------------------------------------------

    async def start(self) -> Operation:
        return await self._client.machines.start(await self._id())

    async def stop(self, *, timeout_seconds: int | None = None) -> Operation:
        return await self._client.machines.stop(await self._id(), timeout_seconds=timeout_seconds)

    async def suspend(self) -> Operation:
        return await self._client.machines.suspend(await self._id())

    async def resume(self) -> Operation:
        return await self._client.machines.resume(await self._id())

    async def restart(self) -> Operation:
        """Power-cycle: stop, wait, then start again."""
        await self.wait_operation(await self._client.machines.stop(await self._id()))
        return await self._client.machines.start(await self._id())

    async def delete(self) -> Operation:
        return await self._client.machines.delete(await self._id())

    async def wait_operation(self, operation: Operation, *, timeout: float = 600.0) -> Operation:
        deadline = time.monotonic() + timeout
        op = await self._client.operations.get(operation.id, wait="25s")
        while not op.finished:
            if time.monotonic() > deadline:
                raise TimeoutError(f"operation {op.id} not finished after {timeout}s")
            op = await self._client.operations.get(operation.id, wait="25s")
        return op

    # -- Commands -------------------------------------------------------------

    async def exec(self, cmd: str, **kwargs: t.Any) -> ExecResult:
        return await self._client.machines.exec(await self._id(), cmd=cmd, **kwargs)

    async def run(self, cmd: str, **kwargs: t.Any) -> ExecResult:
        """Like :meth:`exec` but raises :class:`RuntimeError` on non-zero exit."""
        result = await self.exec(cmd, **kwargs)
        if result.exit_code != 0:
            raise RuntimeError(
                f"command failed with exit code {result.exit_code}: {result.stderr!r}"
            )
        return result

    async def exec_stream(self, cmd: str, **kwargs: t.Any) -> t.AsyncIterator[t.Any]:
        async for event in self._client.machines.exec_stream(await self._id(), cmd=cmd, **kwargs):
            yield event

    # -- Files ----------------------------------------------------------------

    async def read_file(self, path: str) -> FileListing:
        return await self._client.machines.read_file(await self._id(), path)

    async def write_file(
        self, path: str, content: bytes, *, mode: str | None = None
    ) -> dict[str, t.Any]:
        return await self._client.machines.upload_file(
            await self._id(), path, content, mode=mode, parents=True
        )

    async def download_file(self, path: str) -> bytes:
        return await self._client.machines.download_file(await self._id(), path)

    # -- Networking / snapshots / ssh ------------------------------------------

    async def publish_port(self, port: int, *, name: str | None = None) -> Port:
        return await self._client.machines.publish_port(await self._id(), port, name=name)

    async def snapshot(self, name: str, **kwargs: t.Any) -> Snapshot:
        return await self._client.machines.snapshot(await self._id(), name, **kwargs)

    async def ssh_token(self, **kwargs: t.Any) -> SshToken:
        return await self._client.machines.ssh_tokens.create(await self._id(), **kwargs)

    async def ssh_certificate(self, public_key: str, **kwargs: t.Any) -> SshCertificate:
        return await self._client.machines.ssh_tokens.certificate(
            await self._id(), public_key, **kwargs
        )

    # -- Config -----------------------------------------------------------------

    async def set_metadata(self, metadata: dict[str, str]) -> Machine:
        return await self._client.machines.update(await self._id(), metadata=metadata)

    async def update(self, **kwargs: t.Any) -> Machine:
        return await self._client.machines.update(await self._id(), **kwargs)

    # -- Internals ---------------------------------------------------------------

    async def _id(self) -> str:
        return (await self.refresh()).id
