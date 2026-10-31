"""Asynchronous InstaBox API client.

Example::

    from instabox import AsyncClient

    async with AsyncClient(api_key="ibk_...") as client:
        op = await client.machines.create(name="dev")
        await client.operations.wait(op.id)
        result = await client.machines.exec(op.machine_id, cmd="echo hello")
"""

from __future__ import annotations

import builtins
import json
import typing as t
from types import TracebackType

import httpx

from ._base import (
    DEFAULT_BASE_URL,
    LONG_POLL_SECONDS,
    build_headers,
    json_body,
    new_idempotency_key,
)
from ._policy import ToolPolicy, wrap_resource_methods
from .async_machine import AsyncMachineHandle
from .exceptions import raise_for_status
from .models import (
    CurrentUser,
    ExecResult,
    FileListing,
    Machine,
    Operation,
    Port,
    Secret,
    Snapshot,
    SshCertificate,
    SshToken,
    Template,
    UploadUrl,
    Usage,
)

__all__ = ["AsyncClient"]

ExecEvent = (
    tuple[t.Literal["stdout"], str]
    | tuple[t.Literal["stderr"], str]
    | tuple[t.Literal["exit"], dict[str, t.Any]]
    | tuple[t.Literal["error"], dict[str, t.Any]]
)


def _exec_body(kwargs: dict[str, t.Any]) -> dict[str, t.Any]:
    cmd, argv = kwargs.get("cmd"), kwargs.get("argv")
    if (cmd is None) == (argv is None):
        raise ValueError("pass exactly one of cmd= or argv=")
    return json_body(
        {
            "cmd": cmd,
            "argv": argv,
            "cwd": kwargs.get("cwd"),
            "envs": kwargs.get("envs"),
            "stdin": kwargs.get("stdin"),
            "timeoutSeconds": kwargs.get("timeout_seconds"),
        }
    )


async def _parse_sse_async(
    lines: t.AsyncIterator[str],
) -> t.AsyncIterator[tuple[str, str]]:
    """Async variant of :func:`instabox._base.parse_sse`."""
    event = ""
    data: builtins.list[str] = []
    async for line in lines:
        line = line.rstrip("\r")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield event or "message", "\n".join(data)


def _raise(response: httpx.Response) -> None:
    if not response.is_error:
        return
    try:
        body = response.json()
    except Exception:
        body = {"error": {"code": "unknown", "message": response.text}}
    raise_for_status(response.status_code, body)


class _AsyncResource:
    def __init__(
        self, http: httpx.AsyncClient, api_key: str, policy: ToolPolicy | None = None
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, t.Any] | None = None,
        json: t.Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self._http.request(
            method,
            path,
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=json if content is None else None,
            content=content,
            headers=headers,
        )

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, t.Any] | None = None,
        body: t.Any = None,
        idempotency_key: bool | str = False,
        confirm_delete: str | None = None,
    ) -> t.Any:
        headers = build_headers(self._api_key)
        if idempotency_key:
            headers["Idempotency-Key"] = (
                idempotency_key if isinstance(idempotency_key, str) else new_idempotency_key()
            )
        if confirm_delete is not None:
            headers["X-Confirm-Delete"] = confirm_delete
        response = await self._request(
            method, path, params=params, json=json_body(body), headers=headers
        )
        _raise(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


class AsyncSshTokens(_AsyncResource):
    async def create(
        self,
        machine_id: str,
        *,
        name: str | None = None,
        ttl_seconds: int | None = None,
        no_expiry: bool | None = None,
    ) -> SshToken:
        """Create an SSH token (the secret is only shown in this response)."""
        body = {"name": name, "ttlSeconds": ttl_seconds, "noExpiry": no_expiry}
        data = await self._json("POST", f"/v1/machines/{machine_id}/ssh-tokens", body=body)
        return SshToken.from_dict(data)

    async def list(self, machine_id: str) -> builtins.list[SshToken]:
        """List the machine's SSH tokens (newest first, without secrets)."""
        data = await self._json("GET", f"/v1/machines/{machine_id}/ssh-tokens") or {}
        return [SshToken.from_dict(tok) for tok in data.get("tokens", [])]

    async def revoke(self, machine_id: str, token_id: str) -> None:
        await self._json("DELETE", f"/v1/machines/{machine_id}/ssh-tokens/{token_id}")

    async def revoke_all(self, machine_id: str) -> int:
        data = await self._json("DELETE", f"/v1/machines/{machine_id}/ssh-tokens") or {}
        return int(data.get("revoked", 0))

    async def certificate(
        self, machine_id: str, public_key: str, *, ttl_seconds: int | None = None
    ) -> SshCertificate:
        body = {"publicKey": public_key, "ttlSeconds": ttl_seconds}
        data = await self._json(
            "POST", f"/v1/machines/{machine_id}/connections", body=body, idempotency_key=True
        )
        return SshCertificate.from_dict(data)


class AsyncMachineSecrets(_AsyncResource):
    async def inspect(self, machine_id: str) -> dict[str, t.Any]:
        return t.cast(
            dict[str, t.Any], await self._json("GET", f"/v1/machines/{machine_id}/secrets")
        )

    async def update(
        self,
        machine_id: str,
        *,
        secrets: str | builtins.list[str] | None = None,
        env_secrets: builtins.list[str] | None = None,
    ) -> dict[str, t.Any]:
        body = {"secrets": secrets, "envSecrets": env_secrets}
        return t.cast(
            dict[str, t.Any],
            await self._json("PATCH", f"/v1/machines/{machine_id}/secrets", body=body),
        )

    async def refresh(self, machine_id: str) -> dict[str, t.Any]:
        return t.cast(
            dict[str, t.Any], await self._json("POST", f"/v1/machines/{machine_id}/secrets")
        )


class AsyncMachines(_AsyncResource):
    def __init__(
        self, http: httpx.AsyncClient, api_key: str, policy: ToolPolicy | None = None
    ) -> None:
        super().__init__(http, api_key, policy)
        self.ssh_tokens = AsyncSshTokens(http, api_key, policy)
        self.secrets = AsyncMachineSecrets(http, api_key, policy)

    async def create(
        self,
        *,
        name: str | None = None,
        template_id: str | None = None,
        template_version: str | None = None,
        snapshot_id: str | None = None,
        vcpus: int | None = None,
        mem_mib: int | None = None,
        guest_user: str | None = None,
        auto_suspend_after_seconds: int | None = None,
        metadata: dict[str, str] | None = None,
        org_id: str | None = None,
        vault: bool | None = None,
        secrets: str | builtins.list[str] | None = None,
        env_secrets: list[str] | None = None,
        desired_state: str | None = None,
        persistent_disk_gib: int | None = None,
        root_disk_gib: int | None = None,
        fail_fast: bool | None = None,
        archive_seconds: int | None = None,
        delete_after_seconds: int | None = None,
        max_run_minutes: int | None = None,
        idempotency_key: str | None = None,
    ) -> Operation:
        """Create a machine. Returns a ``create`` operation (202 Accepted)."""
        body = {
            "name": name,
            "templateId": template_id,
            "templateVersion": template_version,
            "snapshotId": snapshot_id,
            "vcpus": vcpus,
            "memMiB": mem_mib,
            "guestUser": guest_user,
            "autoSuspendAfterSeconds": auto_suspend_after_seconds,
            "metadata": metadata,
            "orgId": org_id,
            "vault": vault,
            "secrets": secrets,
            "envSecrets": env_secrets,
            "desiredState": desired_state,
            "persistentDiskGiB": persistent_disk_gib,
            "rootDiskGiB": root_disk_gib,
            "failFast": fail_fast,
            "archiveSeconds": archive_seconds,
            "deleteAfterSeconds": delete_after_seconds,
            "maxRunMinutes": max_run_minutes,
        }
        data = await self._json(
            "POST",
            "/v1/machines",
            body=body,
            idempotency_key=idempotency_key or True,
        )
        return Operation.from_dict(data)

    async def get(self, id_or_name: str) -> Machine:
        data = await self._json("GET", f"/v1/machines/{id_or_name}")
        return Machine.from_dict(data)

    async def list(
        self,
        *,
        metadata: str | builtins.list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[builtins.list[Machine], str | None]:
        params: dict[str, t.Any] = {"limit": limit, "cursor": cursor}
        if metadata:
            params["metadata"] = metadata
        data = await self._json("GET", "/v1/machines", params=params) or {}
        machines = [Machine.from_dict(m) for m in data.get("machines", [])]
        return machines, data.get("nextCursor")

    async def iter_machines(
        self,
        *,
        metadata: str | builtins.list[str] | None = None,
        limit: int | None = None,
    ) -> t.AsyncIterator[Machine]:
        """Async-iterate over all machines, following pagination cursors."""
        cursor: str | None = None
        while True:
            machines, cursor = await self.list(metadata=metadata, limit=limit, cursor=cursor)
            for machine in machines:
                yield machine
            if not cursor:
                return

    async def update(
        self,
        machine_id: str,
        *,
        vcpus: int | None = None,
        mem_mib: int | None = None,
        persistent_disk_gib: int | None = None,
        root_disk_gib: int | None = None,
        auto_suspend_after_seconds: int | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Machine:
        body = {
            "vcpus": vcpus,
            "memMiB": mem_mib,
            "persistentDiskGiB": persistent_disk_gib,
            "rootDiskGiB": root_disk_gib,
            "autoSuspendAfterSeconds": auto_suspend_after_seconds,
            "metadata": metadata,
        }
        data = await self._json(
            "PATCH",
            f"/v1/machines/{machine_id}",
            body=body,
            idempotency_key=idempotency_key or True,
        )
        return Machine.from_dict(data)

    async def delete(self, machine_id: str, *, idempotency_key: str | None = None) -> Operation:
        data = await self._json(
            "DELETE",
            f"/v1/machines/{machine_id}",
            idempotency_key=idempotency_key or True,
            confirm_delete=machine_id,
        )
        return Operation.from_dict(data)

    # -- Lifecycle -----------------------------------------------------------

    async def start(self, machine_id: str) -> Operation:
        data = await self._json("POST", f"/v1/machines/{machine_id}/start", idempotency_key=True)
        return Operation.from_dict(data)

    async def stop(self, machine_id: str, *, timeout_seconds: int | None = None) -> Operation:
        data = await self._json(
            "POST",
            f"/v1/machines/{machine_id}/stop",
            body={"timeoutSeconds": timeout_seconds},
            idempotency_key=True,
        )
        return Operation.from_dict(data)

    async def suspend(self, machine_id: str) -> Operation:
        data = await self._json("POST", f"/v1/machines/{machine_id}/suspend", idempotency_key=True)
        return Operation.from_dict(data)

    async def resume(self, machine_id: str) -> Operation:
        data = await self._json("POST", f"/v1/machines/{machine_id}/resume", idempotency_key=True)
        return Operation.from_dict(data)

    async def fork(
        self,
        machine_id: str,
        *,
        name: str | None = None,
        vcpus: int | None = None,
        mem_mib: int | None = None,
        metadata: dict[str, str] | None = None,
        delete_after_seconds: int | None = None,
    ) -> Operation:
        body = {
            "name": name,
            "vcpus": vcpus,
            "memMiB": mem_mib,
            "metadata": metadata,
            "deleteAfterSeconds": delete_after_seconds,
        }
        data = await self._json(
            "POST", f"/v1/machines/{machine_id}/fork", body=body, idempotency_key=True
        )
        return Operation.from_dict(data)

    # -- Commands ------------------------------------------------------------

    async def exec(
        self,
        machine_id: str,
        *,
        cmd: str | None = None,
        argv: builtins.list[str] | None = None,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        body = _exec_body(
            {
                "cmd": cmd,
                "argv": argv,
                "cwd": cwd,
                "envs": envs,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = await self._request(
            "POST",
            f"/v1/machines/{machine_id}/exec",
            json=body,
            headers=build_headers(self._api_key),
        )
        _raise(response)
        return ExecResult.from_dict(response.json())

    async def exec_stream(
        self,
        machine_id: str,
        *,
        cmd: str | None = None,
        argv: builtins.list[str] | None = None,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> t.AsyncIterator[ExecEvent]:
        """Run a command and stream its output as SSE events (see sync ``exec_stream``)."""
        body = _exec_body(
            {
                "cmd": cmd,
                "argv": argv,
                "cwd": cwd,
                "envs": envs,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        headers = build_headers(self._api_key)
        headers["Accept"] = "text/event-stream"
        async with self._http.stream(
            "POST", f"/v1/machines/{machine_id}/exec", json=body, headers=headers
        ) as response:
            _raise(response)
            async for event, data in _parse_sse_async(response.aiter_lines()):
                if event in ("stdout", "stderr"):
                    yield (event, data)  # type: ignore[misc]
                elif event in ("exit", "error"):
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        payload = {"raw": data}
                    yield (event, payload)  # type: ignore[misc]

    # -- Files ---------------------------------------------------------------

    async def read_file(self, machine_id: str, path: str) -> FileListing:
        data = await self._json("GET", f"/v1/machines/{machine_id}/files", params={"path": path})
        return FileListing.from_dict(data)

    async def upload_file(
        self,
        machine_id: str,
        path: str,
        content: bytes,
        *,
        mode: str | None = None,
        parents: bool | None = None,
    ) -> dict[str, t.Any]:
        params: dict[str, t.Any] = {"path": path, "mode": mode, "parents": parents}
        response = await self._request(
            "PUT",
            f"/v1/machines/{machine_id}/files",
            params=params,
            content=content,
            headers=build_headers(self._api_key),
        )
        _raise(response)
        return t.cast(dict[str, t.Any], response.json())

    async def download_file(self, machine_id: str, path: str) -> bytes:
        response = await self._request(
            "GET",
            f"/v1/machines/{machine_id}/files/content",
            params={"path": path},
            headers=build_headers(self._api_key),
        )
        _raise(response)
        return response.content

    async def get_upload_url(
        self,
        machine_id: str,
        path: str,
        *,
        mode: str | None = None,
        parents: bool | None = None,
        expires_in_seconds: int | None = None,
    ) -> UploadUrl:
        body = {
            "path": path,
            "mode": mode,
            "parents": parents,
            "expiresInSeconds": expires_in_seconds,
        }
        data = await self._json("POST", f"/v1/machines/{machine_id}/files/upload-url", body=body)
        return UploadUrl.from_dict(data)

    # -- Snapshots & Ports ---------------------------------------------------

    async def snapshot(
        self,
        machine_id: str,
        name: str,
        *,
        description: str | None = None,
        root_only: bool | None = None,
    ) -> Snapshot:
        body = {"name": name, "description": description, "rootOnly": root_only}
        data = await self._json(
            "POST", f"/v1/machines/{machine_id}/snapshots", body=body, idempotency_key=True
        )
        return Snapshot.from_dict(data)

    async def publish_port(self, machine_id: str, port: int, *, name: str | None = None) -> Port:
        data = await self._json(
            "POST", f"/v1/machines/{machine_id}/ports", body={"port": port, "name": name}
        )
        return Port.from_dict(data)

    async def list_ports(self, machine_id: str) -> builtins.list[Port]:
        data = await self._json("GET", f"/v1/machines/{machine_id}/ports") or {}
        return [Port.from_dict(p) for p in data.get("ports", [])]


class AsyncOperations(_AsyncResource):
    async def get(self, operation_id: str, *, wait: str | None = None) -> Operation:
        data = await self._json("GET", f"/v1/operations/{operation_id}", params={"wait": wait})
        return Operation.from_dict(data)

    async def wait(self, operation_id: str, *, poll_seconds: str = LONG_POLL_SECONDS) -> Operation:
        """Await until the operation finishes, long-polling without sleeping."""
        operation = await self.get(operation_id, wait=poll_seconds)
        while not operation.finished:
            operation = await self.get(operation_id, wait=poll_seconds)
        return operation


class AsyncSnapshots(_AsyncResource):
    async def list(self) -> list[Snapshot]:
        data = await self._json("GET", "/v1/snapshots") or {}
        return [Snapshot.from_dict(s) for s in data.get("snapshots", [])]

    async def get(self, snapshot_id: str) -> Snapshot:
        data = await self._json("GET", f"/v1/snapshots/{snapshot_id}")
        return Snapshot.from_dict(data)

    async def read_files(self, snapshot_id: str, path: str = "/") -> FileListing:
        data = await self._json("GET", f"/v1/snapshots/{snapshot_id}/files", params={"path": path})
        return FileListing.from_dict(data)

    async def delete(self, snapshot_id: str, *, idempotency_key: str | None = None) -> None:
        await self._json(
            "DELETE",
            f"/v1/snapshots/{snapshot_id}",
            idempotency_key=idempotency_key or True,
            confirm_delete=snapshot_id,
        )


class AsyncPorts(_AsyncResource):
    async def list_all(self) -> builtins.list[Port]:
        data = await self._json("GET", "/v1/ports") or {}
        return [Port.from_dict(p) for p in data.get("ports", [])]

    async def unpublish(self, machine_id: str, port: int) -> None:
        await self._json("DELETE", f"/v1/machines/{machine_id}/ports/{port}")


class AsyncOrgSecrets(_AsyncResource):
    async def list(self, org_id: str) -> builtins.list[Secret]:
        """List org secret metadata (names and modes, never values)."""
        data = await self._json("GET", f"/v1/orgs/{org_id}/secrets") or {}
        return [Secret.from_dict(item) for item in data.get("items", [])]

    async def put(
        self,
        org_id: str,
        name: str,
        value: str,
        *,
        delivery: str,
        hosts: builtins.list[str] | None = None,
    ) -> Secret:
        """Create or replace an org secret (up to 64 KiB)."""
        body: dict[str, t.Any] = {"value": value, "delivery": delivery}
        if hosts is not None:
            body["hosts"] = hosts
        data = await self._json("PUT", f"/v1/orgs/{org_id}/secrets/{name}", body=body)
        return Secret.from_dict(data)

    async def import_many(
        self,
        org_id: str,
        values: dict[str, str],
        *,
        delivery: str,
        hosts: builtins.list[str] | None = None,
    ) -> builtins.list[Secret]:
        """Atomically import several secrets (failure changes nothing)."""
        body: dict[str, t.Any] = {"values": values, "delivery": delivery}
        if hosts is not None:
            body["hosts"] = hosts
        data = await self._json("POST", f"/v1/orgs/{org_id}/secrets/import", body=body) or {}
        return [Secret.from_dict(item) for item in data.get("items", [])]

    async def delete(self, org_id: str, name: str) -> None:
        """Delete an org secret (proxy revocation may take up to 60s)."""
        await self._json("DELETE", f"/v1/orgs/{org_id}/secrets/{name}")


class AsyncAccount(_AsyncResource):
    async def me(self) -> CurrentUser:
        data = await self._json("GET", "/v1/me")
        return CurrentUser.from_dict(data)

    async def templates(self) -> list[Template]:
        data = await self._json("GET", "/v1/templates") or {}
        return [Template.from_dict(tpl) for tpl in data.get("templates", [])]

    async def usage(self) -> Usage:
        data = await self._json("GET", "/v1/usage")
        return Usage.from_dict(data)

    async def orgs(self) -> builtins.list[dict[str, t.Any]]:
        """List the user's organizations (includes per-org ``allowedSpecs``)."""
        data = await self._json("GET", "/v1/orgs")
        if isinstance(data, builtins.list):
            return data
        return data.get("orgs", []) if isinstance(data, dict) else []


class AsyncClient:
    """Asynchronous client for the InstaBox API.

    Mirrors :class:`instabox.Client`. Use as an async context manager or
    call :meth:`aclose` explicitly. Accepts the same ``policy`` argument.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        policy: ToolPolicy | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=default_headers or {},
            transport=transport,
        )
        self.machines = AsyncMachines(self._http, api_key, policy)
        self.operations = AsyncOperations(self._http, api_key, policy)
        self.snapshots = AsyncSnapshots(self._http, api_key, policy)
        self.ports = AsyncPorts(self._http, api_key, policy)
        self.account = AsyncAccount(self._http, api_key, policy)
        self.org_secrets = AsyncOrgSecrets(self._http, api_key, policy)
        if policy is not None:
            _apply_policy(self, policy)

    def machine(self, id_or_name: str) -> AsyncMachineHandle:
        """Get a stateful async handle: ``await client.machine("dev").start()``."""
        return AsyncMachineHandle(self, id_or_name)

    @property
    def api_key(self) -> str:
        return self._api_key

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _apply_policy(client: AsyncClient, policy: ToolPolicy) -> None:
    """Attach per-operation policy checks to every exposed resource."""
    for resource_name in ("machines", "operations", "snapshots", "ports", "account", "org_secrets"):
        resource = getattr(client, resource_name)
        wrap_resource_methods(resource)
        for attr_name in ("ssh_tokens", "secrets"):
            nested = getattr(resource, attr_name, None)
            if nested is not None:
                wrap_resource_methods(nested)
