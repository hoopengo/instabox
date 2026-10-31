"""Synchronous InstaBox API client.

Example::

    from instabox import Client

    client = Client(api_key="ibk_...")
    op = client.machines.create(name="dev")
    client.operations.wait(op.id)          # block until the machine exists
    result = client.machines.exec(op.machine_id, cmd="echo hello")

Restrict what a client may do (e.g. for an AI agent)::

    from instabox import Client, ToolPolicy

    client = Client(api_key="ibk_...", policy=ToolPolicy(
        allow=["machines.exec", "machines.read_file", "operations.*"],
    ))
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
    parse_sse,
)
from ._policy import ToolPolicy, wrap_resource_methods
from .exceptions import raise_for_status
from .machine import MachineHandle
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

__all__ = ["Client"]

ExecEvent = (
    tuple[t.Literal["stdout"], str]
    | tuple[t.Literal["stderr"], str]
    | tuple[t.Literal["exit"], dict[str, t.Any]]
    | tuple[t.Literal["error"], dict[str, t.Any]]
)


class _SyncClientMixin:
    """Request plumbing shared by resource objects of the sync client."""

    _http: httpx.Client
    _api_key: str
    _policy: ToolPolicy | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, t.Any] | None = None,
        json: t.Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._http.request(
            method,
            path,
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=json if content is None else None,
            content=content,
            headers=headers,
        )

    def _json(
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
            key = idempotency_key if isinstance(idempotency_key, str) else new_idempotency_key()
            headers["Idempotency-Key"] = key
        if confirm_delete is not None:
            headers["X-Confirm-Delete"] = confirm_delete
        response = self._request(method, path, params=params, json=json_body(body), headers=headers)
        _raise(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def _exec_body(self, kwargs: dict[str, t.Any]) -> dict[str, t.Any]:
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


def _raise(response: httpx.Response) -> None:
    if not response.is_error:
        return
    try:
        body = response.json()
    except Exception:
        body = {"error": {"code": "unknown", "message": response.text}}
    raise_for_status(response.status_code, body)


class _Machines(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy
        self.ssh_tokens = _SshTokens(http, api_key, policy)
        self.secrets = _MachineSecrets(http, api_key, policy)

    # -- CRUD ----------------------------------------------------------------

    def create(
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
        env_secrets: builtins.list[str] | None = None,
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
        data = self._json(
            "POST",
            "/v1/machines",
            body=body,
            idempotency_key=idempotency_key or True,
        )
        return Operation.from_dict(data)

    def get(self, id_or_name: str) -> Machine:
        """Get a machine by ID or name."""
        data = self._json("GET", f"/v1/machines/{id_or_name}")
        return Machine.from_dict(data)

    def list(
        self,
        *,
        metadata: str | builtins.list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[builtins.list[Machine], str | None]:
        """List machines. Returns ``(machines, next_cursor)``."""
        params: dict[str, t.Any] = {"limit": limit, "cursor": cursor}
        if metadata:
            params["metadata"] = metadata
        data = self._json("GET", "/v1/machines", params=params) or {}
        machines = [Machine.from_dict(m) for m in data.get("machines", [])]
        return machines, data.get("nextCursor")

    def iter_machines(
        self,
        *,
        metadata: str | builtins.list[str] | None = None,
        limit: int | None = None,
    ) -> t.Iterator[Machine]:
        """Iterate over all machines, following pagination cursors."""
        cursor: str | None = None
        while True:
            machines, cursor = self.list(metadata=metadata, limit=limit, cursor=cursor)
            yield from machines
            if not cursor:
                return

    def update(
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
        """Update a machine (resize, disk, auto-suspend, metadata)."""
        body = {
            "vcpus": vcpus,
            "memMiB": mem_mib,
            "persistentDiskGiB": persistent_disk_gib,
            "rootDiskGiB": root_disk_gib,
            "autoSuspendAfterSeconds": auto_suspend_after_seconds,
            "metadata": metadata,
        }
        data = self._json(
            "PATCH",
            f"/v1/machines/{machine_id}",
            body=body,
            idempotency_key=idempotency_key or True,
        )
        return Machine.from_dict(data)

    def delete(self, machine_id: str, *, idempotency_key: str | None = None) -> Operation:
        """Delete a machine (snapshots are kept). Confirmation header is sent automatically."""
        data = self._json(
            "DELETE",
            f"/v1/machines/{machine_id}",
            idempotency_key=idempotency_key or True,
            confirm_delete=machine_id,
        )
        return Operation.from_dict(data)

    # -- Lifecycle -----------------------------------------------------------

    def start(self, machine_id: str) -> Operation:
        """Start a stopped machine (resumes a paused one)."""
        data = self._json("POST", f"/v1/machines/{machine_id}/start", idempotency_key=True)
        return Operation.from_dict(data)

    def stop(self, machine_id: str, *, timeout_seconds: int | None = None) -> Operation:
        """Stop a machine; files are kept."""
        data = self._json(
            "POST",
            f"/v1/machines/{machine_id}/stop",
            body={"timeoutSeconds": timeout_seconds},
            idempotency_key=True,
        )
        return Operation.from_dict(data)

    def suspend(self, machine_id: str) -> Operation:
        """Pause a machine, preserving memory and running programs."""
        data = self._json("POST", f"/v1/machines/{machine_id}/suspend", idempotency_key=True)
        return Operation.from_dict(data)

    def resume(self, machine_id: str) -> Operation:
        """Resume a paused machine."""
        data = self._json("POST", f"/v1/machines/{machine_id}/resume", idempotency_key=True)
        return Operation.from_dict(data)

    def fork(
        self,
        machine_id: str,
        *,
        name: str | None = None,
        vcpus: int | None = None,
        mem_mib: int | None = None,
        metadata: dict[str, str] | None = None,
        delete_after_seconds: int | None = None,
    ) -> Operation:
        """Fork a machine (a ``create`` operation for the new machine)."""
        body = {
            "name": name,
            "vcpus": vcpus,
            "memMiB": mem_mib,
            "metadata": metadata,
            "deleteAfterSeconds": delete_after_seconds,
        }
        data = self._json(
            "POST", f"/v1/machines/{machine_id}/fork", body=body, idempotency_key=True
        )
        return Operation.from_dict(data)

    # -- Commands ------------------------------------------------------------

    def exec(
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
        """Run a command on a running machine (exactly one of ``cmd`` / ``argv``)."""
        body = self._exec_body(
            {
                "cmd": cmd,
                "argv": argv,
                "cwd": cwd,
                "envs": envs,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self._request(
            "POST",
            f"/v1/machines/{machine_id}/exec",
            json=body,
            headers=build_headers(self._api_key),
        )
        _raise(response)
        return ExecResult.from_dict(response.json())

    def exec_stream(
        self,
        machine_id: str,
        *,
        cmd: str | None = None,
        argv: builtins.list[str] | None = None,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> t.Iterator[ExecEvent]:
        """Run a command and stream its output as SSE events.

        Yields ``("stdout", text)`` / ``("stderr", text)`` chunks, then a final
        ``("exit", {"exitCode": N, "timedOut": bool, ...})`` or
        ``("error", {...})`` event.
        """
        body = self._exec_body(
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
        with self._http.stream(
            "POST",
            f"/v1/machines/{machine_id}/exec",
            json=body,
            headers=headers,
        ) as response:
            _raise(response)
            for event, data in parse_sse(response.iter_lines()):
                if event in ("stdout", "stderr"):
                    yield (event, data)  # type: ignore[misc]
                elif event in ("exit", "error"):
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        payload = {"raw": data}
                    yield (event, payload)  # type: ignore[misc]

    # -- Files ---------------------------------------------------------------

    def read_file(self, machine_id: str, path: str) -> FileListing:
        """Read a text file (≤256 KiB) or list a directory (≤5000 entries)."""
        data = self._json("GET", f"/v1/machines/{machine_id}/files", params={"path": path})
        return FileListing.from_dict(data)

    def upload_file(
        self,
        machine_id: str,
        path: str,
        content: bytes,
        *,
        mode: str | None = None,
        parents: bool | None = None,
    ) -> dict[str, t.Any]:
        """Upload raw bytes to a file on the machine (up to 5 GiB)."""
        headers = build_headers(self._api_key)
        params: dict[str, t.Any] = {"path": path, "mode": mode, "parents": parents}
        response = self._request(
            "PUT",
            f"/v1/machines/{machine_id}/files",
            params=params,
            content=content,
            headers=headers,
        )
        _raise(response)
        return t.cast(dict[str, t.Any], response.json())

    def download_file(self, machine_id: str, path: str) -> bytes:
        """Download a file's raw bytes (any size)."""
        headers = build_headers(self._api_key)
        response = self._request(
            "GET",
            f"/v1/machines/{machine_id}/files/content",
            params={"path": path},
            headers=headers,
        )
        _raise(response)
        return response.content

    def get_upload_url(
        self,
        machine_id: str,
        path: str,
        *,
        mode: str | None = None,
        parents: bool | None = None,
        expires_in_seconds: int | None = None,
    ) -> UploadUrl:
        """Get a signed, credential-less upload URL."""
        body = {
            "path": path,
            "mode": mode,
            "parents": parents,
            "expiresInSeconds": expires_in_seconds,
        }
        data = self._json("POST", f"/v1/machines/{machine_id}/files/upload-url", body=body)
        return UploadUrl.from_dict(data)

    # -- Snapshots -----------------------------------------------------------

    def snapshot(
        self,
        machine_id: str,
        name: str,
        *,
        description: str | None = None,
        root_only: bool | None = None,
    ) -> Snapshot:
        """Save a snapshot of the machine (201 stopped / 202 live)."""
        body = {"name": name, "description": description, "rootOnly": root_only}
        data = self._json(
            "POST", f"/v1/machines/{machine_id}/snapshots", body=body, idempotency_key=True
        )
        return Snapshot.from_dict(data)

    # -- Ports ---------------------------------------------------------------

    def publish_port(self, machine_id: str, port: int, *, name: str | None = None) -> Port:
        """Publish a machine port over HTTPS."""
        data = self._json(
            "POST", f"/v1/machines/{machine_id}/ports", body={"port": port, "name": name}
        )
        return Port.from_dict(data)

    def list_ports(self, machine_id: str) -> builtins.list[Port]:
        """List the machine's published ports."""
        data = self._json("GET", f"/v1/machines/{machine_id}/ports") or {}
        return [Port.from_dict(p) for p in data.get("ports", [])]


class _SshTokens(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def create(
        self,
        machine_id: str,
        *,
        name: str | None = None,
        ttl_seconds: int | None = None,
        no_expiry: bool | None = None,
    ) -> SshToken:
        """Create an SSH token (the secret is only shown in this response)."""
        body = {"name": name, "ttlSeconds": ttl_seconds, "noExpiry": no_expiry}
        data = self._json("POST", f"/v1/machines/{machine_id}/ssh-tokens", body=body)
        return SshToken.from_dict(data)

    def list(self, machine_id: str) -> builtins.list[SshToken]:
        """List the machine's SSH tokens (newest first, without secrets)."""
        data = self._json("GET", f"/v1/machines/{machine_id}/ssh-tokens") or {}
        return [SshToken.from_dict(tok) for tok in data.get("tokens", [])]

    def revoke(self, machine_id: str, token_id: str) -> None:
        """Revoke one token and close its connections."""
        self._json("DELETE", f"/v1/machines/{machine_id}/ssh-tokens/{token_id}")

    def revoke_all(self, machine_id: str) -> int:
        """Revoke every token on the machine; returns the count."""
        data = self._json("DELETE", f"/v1/machines/{machine_id}/ssh-tokens") or {}
        return int(data.get("revoked", 0))

    def certificate(
        self, machine_id: str, public_key: str, *, ttl_seconds: int | None = None
    ) -> SshCertificate:
        """Sign a public key into a short-lived SSH certificate (machine must be running)."""
        body = {"publicKey": public_key, "ttlSeconds": ttl_seconds}
        data = self._json(
            "POST", f"/v1/machines/{machine_id}/connections", body=body, idempotency_key=True
        )
        return SshCertificate.from_dict(data)


class _MachineSecrets(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def inspect(self, machine_id: str) -> dict[str, t.Any]:
        """Inspect the machine's secret selections and delivery status."""
        return t.cast(dict[str, t.Any], self._json("GET", f"/v1/machines/{machine_id}/secrets"))

    def update(
        self,
        machine_id: str,
        *,
        secrets: str | builtins.list[str] | None = None,
        env_secrets: builtins.list[str] | None = None,
    ) -> dict[str, t.Any]:
        """Change the machine's secret selections (``secrets="all"`` or names)."""
        body = {"secrets": secrets, "envSecrets": env_secrets}
        return t.cast(
            dict[str, t.Any],
            self._json("PATCH", f"/v1/machines/{machine_id}/secrets", body=body),
        )

    def refresh(self, machine_id: str) -> dict[str, t.Any]:
        """Re-deliver env secrets and reconcile the proxy (machine must be running)."""
        return t.cast(
            dict[str, t.Any],
            self._json("POST", f"/v1/machines/{machine_id}/secrets"),
        )


class _Operations(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def get(self, operation_id: str, *, wait: str | None = None) -> Operation:
        """Get an operation, optionally long-polling (e.g. ``wait="25s"``)."""
        data = self._json("GET", f"/v1/operations/{operation_id}", params={"wait": wait})
        return Operation.from_dict(data)

    def wait(self, operation_id: str, *, poll_seconds: str = LONG_POLL_SECONDS) -> Operation:
        """Block until the operation finishes, long-polling without sleeping."""
        operation = self.get(operation_id, wait=poll_seconds)
        while not operation.finished:
            operation = self.get(operation_id, wait=poll_seconds)
        return operation


class _Snapshots(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def list(self) -> builtins.list[Snapshot]:
        data = self._json("GET", "/v1/snapshots") or {}
        return [Snapshot.from_dict(s) for s in data.get("snapshots", [])]

    def get(self, snapshot_id: str) -> Snapshot:
        data = self._json("GET", f"/v1/snapshots/{snapshot_id}")
        return Snapshot.from_dict(data)

    def read_files(self, snapshot_id: str, path: str = "/") -> FileListing:
        """Browse a snapshot's saved files without starting a machine."""
        data = self._json("GET", f"/v1/snapshots/{snapshot_id}/files", params={"path": path})
        return FileListing.from_dict(data)

    def delete(self, snapshot_id: str, *, idempotency_key: str | None = None) -> None:
        """Delete a snapshot; the confirmation header is sent automatically."""
        self._json(
            "DELETE",
            f"/v1/snapshots/{snapshot_id}",
            idempotency_key=idempotency_key or True,
            confirm_delete=snapshot_id,
        )


class _Ports(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def list_all(self) -> builtins.list[Port]:
        """List published ports across every machine."""
        data = self._json("GET", "/v1/ports") or {}
        return [Port.from_dict(p) for p in data.get("ports", [])]

    def unpublish(self, machine_id: str, port: int) -> None:
        self._json("DELETE", f"/v1/machines/{machine_id}/ports/{port}")


class _OrgSecrets(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def list(self, org_id: str) -> builtins.list[Secret]:
        """List org secret metadata (names and modes, never values)."""
        data = self._json("GET", f"/v1/orgs/{org_id}/secrets") or {}
        return [Secret.from_dict(item) for item in data.get("items", [])]

    def put(
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
        data = self._json("PUT", f"/v1/orgs/{org_id}/secrets/{name}", body=body)
        return Secret.from_dict(data)

    def import_many(
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
        data = self._json("POST", f"/v1/orgs/{org_id}/secrets/import", body=body) or {}
        return [Secret.from_dict(item) for item in data.get("items", [])]

    def delete(self, org_id: str, name: str) -> None:
        """Delete an org secret (proxy revocation may take up to 60s)."""
        self._json("DELETE", f"/v1/orgs/{org_id}/secrets/{name}")


class _Account(_SyncClientMixin):
    def __init__(self, http: httpx.Client, api_key: str, policy: ToolPolicy | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._policy = policy

    def me(self) -> CurrentUser:
        data = self._json("GET", "/v1/me")
        return CurrentUser.from_dict(data)

    def templates(self) -> builtins.list[Template]:
        data = self._json("GET", "/v1/templates") or {}
        return [Template.from_dict(tpl) for tpl in data.get("templates", [])]

    def usage(self) -> Usage:
        data = self._json("GET", "/v1/usage")
        return Usage.from_dict(data)

    def orgs(self) -> builtins.list[dict[str, t.Any]]:
        """List the user's organizations (includes per-org ``allowedSpecs``)."""
        data = self._json("GET", "/v1/orgs")
        if isinstance(data, builtins.list):
            return data
        return data.get("orgs", []) if isinstance(data, dict) else []


def _apply_policy(client: Client, policy: ToolPolicy) -> None:
    """Attach per-operation policy checks to every exposed resource."""
    for resource_name in ("machines", "operations", "snapshots", "ports", "account", "org_secrets"):
        resource = getattr(client, resource_name)
        wrap_resource_methods(resource)
        for attr_name in ("ssh_tokens", "secrets"):
            nested = getattr(resource, attr_name, None)
            if nested is not None:
                wrap_resource_methods(nested)


class Client:
    """Synchronous client for the InstaBox API.

    Args:
        api_key: API key (``ibk_...``). Usually ``os.environ["INSTABOX_API_KEY"]``.
        base_url: API root; override for tests or self-hosted servers.
        timeout: Per-request timeout in seconds.
        default_headers: Extra headers merged into every request.
        transport: Custom ``httpx`` transport (e.g. ``MockTransport`` in tests).
        policy: Optional :class:`instabox.ToolPolicy` restricting which
            operations this client may call (checked before any request).

    Resources: ``machines`` (with ``machines.ssh_tokens`` / ``machines.secrets``),
    ``operations``, ``snapshots``, ``ports``, ``account``, ``org_secrets``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        policy: ToolPolicy | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=default_headers or {},
            transport=transport,
        )
        self.machines = _Machines(self._http, api_key, policy)
        self.operations = _Operations(self._http, api_key, policy)
        self.snapshots = _Snapshots(self._http, api_key, policy)
        self.ports = _Ports(self._http, api_key, policy)
        self.account = _Account(self._http, api_key, policy)
        self.org_secrets = _OrgSecrets(self._http, api_key, policy)
        if policy is not None:
            _apply_policy(self, policy)

    def machine(self, id_or_name: str) -> MachineHandle:
        """Get a stateful handle for one machine: ``client.machine("dev").start()``.

        The handle delegates to the wrapped resources, so a client-level
        :class:`~instabox.ToolPolicy` applies to its methods too.
        """
        return MachineHandle(self, id_or_name)

    @property
    def api_key(self) -> str:
        return self._api_key

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
