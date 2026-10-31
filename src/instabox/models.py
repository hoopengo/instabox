"""Typed data models returned by the InstaBox API.

Models are plain dataclasses built defensively from the JSON responses, so
unknown/extra fields never break the SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    return data.get(key, default)


@dataclass(frozen=True)
class Operation:
    """An asynchronous change tracked by the API (create/start/stop/...)."""

    id: str
    machine_id: str
    type: str
    state: str
    error: dict[str, Any] | None = None
    phase: str | None = None
    machine: Machine | None = None
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Operation:
        machine = data.get("machine")
        return cls(
            id=_get(data, "id", ""),
            machine_id=_get(data, "machineId", ""),
            type=_get(data, "type", ""),
            state=_get(data, "state", ""),
            error=data.get("error"),
            phase=data.get("phase"),
            machine=Machine.from_dict(machine) if isinstance(machine, dict) else None,
            created_at=data.get("createdAt"),
            updated_at=data.get("updatedAt"),
            raw=data,
        )

    @property
    def succeeded(self) -> bool:
        return self.state == "succeeded"

    @property
    def failed(self) -> bool:
        return self.state == "failed"

    @property
    def finished(self) -> bool:
        return self.state in ("succeeded", "failed")


@dataclass(frozen=True)
class Spec:
    """Machine compute specification."""

    vcpus: int | None = None
    mem_mib: int | None = None
    persistent_disk_gib: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Spec:
        return cls(
            vcpus=data.get("vcpus"),
            mem_mib=data.get("memMiB"),
            persistent_disk_gib=data.get("persistentDiskGiB"),
        )


@dataclass(frozen=True)
class Machine:
    """A cloud machine."""

    id: str
    name: str
    state: str
    desired_state: str | None = None
    revision: int | None = None
    operation_id: str | None = None
    template: dict[str, Any] | None = None
    spec: Spec | None = None
    storage: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    auto_suspend_after_seconds: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    guest_user: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Machine:
        spec = data.get("spec")
        return cls(
            id=_get(data, "id", ""),
            name=_get(data, "name", ""),
            state=_get(data, "state", ""),
            desired_state=data.get("desiredState"),
            revision=data.get("revision"),
            operation_id=data.get("operationId"),
            template=data.get("template"),
            spec=Spec.from_dict(spec) if isinstance(spec, dict) else None,
            storage=data.get("storage"),
            runtime=data.get("runtime"),
            auto_suspend_after_seconds=data.get("autoSuspendAfterSeconds"),
            metadata=data.get("metadata") or {},
            guest_user=data.get("guestUser"),
            created_at=data.get("createdAt"),
            updated_at=data.get("updatedAt"),
            raw=data,
        )

    @property
    def runtime_ready(self) -> bool:
        """True when ``runtime.ready`` is reported by the API."""
        runtime = self.runtime or {}
        return bool(runtime.get("ready", False))


@dataclass(frozen=True)
class ExecResult:
    """Result of running a command on a machine."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecResult:
        return cls(
            stdout=data.get("stdout") or "",
            stderr=data.get("stderr") or "",
            exit_code=int(data.get("exitCode", 0)),
            timed_out=bool(data.get("timedOut", False)),
            truncated=bool(data.get("truncated", False)),
            raw=data,
        )


@dataclass(frozen=True)
class FileEntry:
    """One entry of a directory listing."""

    name: str
    type: str
    size: int | None = None
    mode: str | None = None
    mod_time: str | None = None
    target: str | None = None  # symlink target

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileEntry:
        return cls(
            name=_get(data, "name", ""),
            type=_get(data, "type", ""),
            size=data.get("size"),
            mode=data.get("mode"),
            mod_time=data.get("modTime"),
            target=data.get("target"),
        )


@dataclass(frozen=True)
class FileListing:
    """A file or directory read from a machine."""

    path: str
    type: str
    size: int | None = None
    mode: str | None = None
    mod_time: str | None = None
    content: str | None = None  # text file content, when a file was read
    entries: tuple[FileEntry, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileListing:
        entries = data.get("entries") or []
        return cls(
            path=_get(data, "path", ""),
            type=_get(data, "type", ""),
            size=data.get("size"),
            mode=data.get("mode"),
            mod_time=data.get("modTime"),
            content=data.get("content"),
            entries=tuple(FileEntry.from_dict(e) for e in entries),
            raw=data,
        )


@dataclass(frozen=True)
class UploadUrl:
    """A pre-signed, credential-less upload URL."""

    url: str
    method: str
    path: str
    expires_at: str | None = None
    max_bytes: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UploadUrl:
        return cls(
            url=_get(data, "url", ""),
            method=_get(data, "method", "PUT"),
            path=_get(data, "path", ""),
            expires_at=data.get("expiresAt"),
            max_bytes=data.get("maxBytes"),
        )


@dataclass(frozen=True)
class Snapshot:
    """A saved machine image."""

    id: str
    name: str
    status: str
    source_machine_id: str | None = None
    source_snapshot_seq: int | None = None
    template: dict[str, Any] | None = None
    root_size_bytes: int | None = None
    data_size_bytes: int | None = None
    description: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        return cls(
            id=_get(data, "id", ""),
            name=_get(data, "name", ""),
            status=_get(data, "status", ""),
            source_machine_id=data.get("sourceMachineId"),
            source_snapshot_seq=data.get("sourceSnapshotSeq"),
            template=data.get("template"),
            root_size_bytes=data.get("rootSizeBytes"),
            data_size_bytes=data.get("dataSizeBytes"),
            description=data.get("description"),
            created_at=data.get("createdAt"),
            raw=data,
        )


@dataclass(frozen=True)
class Port:
    """A publicly published HTTPS port on a machine."""

    id: str | None = None
    machine_id: str | None = None
    port: int | None = None
    name: str | None = None
    url: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Port:
        return cls(
            id=data.get("id"),
            machine_id=data.get("machineId"),
            port=data.get("port"),
            name=data.get("name"),
            url=data.get("url"),
            created_at=data.get("createdAt"),
            raw=data,
        )


@dataclass(frozen=True)
class Template:
    """A machine template (e.g. ``agent-browser``)."""

    id: str
    version: str
    description: str | None = None
    arch: str | None = None
    default_spec: Spec | None = None
    creatable: bool = True
    latest: bool = False
    default: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Template:
        default_spec = data.get("defaultSpec")
        return cls(
            id=_get(data, "id", ""),
            version=_get(data, "version", ""),
            description=data.get("description"),
            arch=data.get("arch"),
            default_spec=Spec.from_dict(default_spec) if isinstance(default_spec, dict) else None,
            creatable=bool(data.get("creatable", True)),
            latest=bool(data.get("latest", False)),
            default=bool(data.get("default", False)),
            raw=data,
        )


@dataclass(frozen=True)
class Usage:
    """Account usage counters and limits."""

    machines: int | None = None
    snapshots: int | None = None
    stored_bytes: int | None = None
    computed_at: str | None = None
    limits: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            machines=data.get("machines"),
            snapshots=data.get("snapshots"),
            stored_bytes=data.get("storedBytes"),
            computed_at=data.get("computedAt"),
            limits=data.get("limits") or {},
            raw=data,
        )


@dataclass(frozen=True)
class CurrentUser:
    """The user an API key belongs to."""

    user_id: str | None = None
    login: str | None = None
    admin: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurrentUser:
        return cls(
            user_id=data.get("userId"),
            login=data.get("login"),
            admin=bool(data.get("admin", False)),
            raw=data,
        )


@dataclass(frozen=True)
class SshToken:
    """A one-shot SSH token for a machine (secret only in the create response)."""

    id: str | None = None
    name: str | None = None
    machine_id: str | None = None
    host: str | None = None
    port: int | None = None
    token: str | None = None
    command: str | None = None
    expires_at: str | None = None
    host_key_fingerprint: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SshToken:
        return cls(
            id=data.get("id"),
            name=data.get("name"),
            machine_id=data.get("machineId"),
            host=data.get("host"),
            port=data.get("port"),
            token=data.get("token"),
            command=data.get("command"),
            expires_at=data.get("expiresAt"),
            host_key_fingerprint=data.get("hostKeyFingerprint"),
            created_at=data.get("createdAt"),
            raw=data,
        )


@dataclass(frozen=True)
class SshCertificate:
    """A short-lived SSH certificate for direct login."""

    id: str | None = None
    machine_id: str | None = None
    transport: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    certificate: str | None = None
    expires_at: str | None = None
    host_trust: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SshCertificate:
        return cls(
            id=data.get("id"),
            machine_id=data.get("machineId"),
            transport=data.get("transport"),
            host=data.get("host"),
            port=data.get("port"),
            username=data.get("username"),
            certificate=data.get("certificate"),
            expires_at=data.get("expiresAt"),
            host_trust=data.get("hostTrust"),
            raw=data,
        )


@dataclass(frozen=True)
class Secret:
    """Metadata for an org secret (values are never returned by the API)."""

    id: str | None = None
    org_id: str | None = None
    name: str | None = None
    delivery: str | None = None  # "proxy" | "env"
    hosts: tuple[str, ...] = ()
    size: int | None = None
    version: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Secret:
        return cls(
            id=data.get("id"),
            org_id=data.get("orgId"),
            name=data.get("name"),
            delivery=data.get("delivery"),
            hosts=tuple(data.get("hosts") or ()),
            size=data.get("size"),
            version=data.get("version"),
            created_at=data.get("createdAt"),
            updated_at=data.get("updatedAt"),
            raw=data,
        )
