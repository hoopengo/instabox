"""Optional MCP (Model Context Protocol) server built on this SDK.

InstaBox ships a hosted MCP server at ``https://api.instabox.sh/mcp``, but it
has **no per-tool toggles** — what a client may do is decided solely by the API
key's scopes. This module fills that gap: a local stdio MCP server that wraps
the SDK and enforces a tool allowlist on top of (or instead of) key scopes.

Install and run::

    uv add "instabox[mcp]"
    export INSTABOX_API_KEY=ibk_...
    export INSTABOX_ALLOWED_TOOLS="machines.exec,machines.read_file,machines.write_file"
    export INSTABOX_ALLOWED_TOOLS="${INSTABOX_ALLOWED_TOOLS},list_machines,get_machine"
    instabox-mcp

Then register it with a client, e.g. for Claude Code::

    claude mcp add instabox -- uvx --from "instabox[mcp]" instabox-mcp

Tool names mirror the hosted server where they exist (``list_machines``,
``get_machine``, ``create_machine``, ``delete_machine``, ``exec``,
``read_file``, ``write_file``, ``suspend_machine``, ``resume_machine``,
``save_snapshot``, ``list_snapshots``, ``get_usage``). Dangerous tools
(``create_machine``, ``delete_machine``, ``save_snapshot``) are **denied by
default**; enable them explicitly via ``INSTABOX_ALLOWED_TOOLS``.
"""

from __future__ import annotations

import os
import typing as t

__all__ = ["build_server", "main"]

# Tools that create, destroy or persist state — off unless explicitly allowed.
DEFAULT_DENIED = frozenset({"create_machine", "delete_machine", "save_snapshot"})

SAFE_TOOLS = (
    "list_machines",
    "get_machine",
    "get_usage",
    "exec",
    "read_file",
    "write_file",
    "suspend_machine",
    "resume_machine",
    "create_machine",
    "delete_machine",
    "save_snapshot",
    "list_snapshots",
)


def _tool_filter(allowed_env: str | None, denied_env: str | None) -> t.Callable[[str], bool]:
    """Build an allow-check.

    Without ``INSTABOX_ALLOWED_TOOLS`` the safe tools are enabled (minus
    ``INSTABOX_DENIED_TOOLS``). An explicit allowlist is authoritative: it may
    re-enable default-denied tools (``*`` = everything), while
    ``INSTABOX_DENIED_TOOLS`` always wins.
    """
    denied = {n.strip() for n in (denied_env or "").split(",") if n.strip()}
    if allowed_env:
        allowed = {n.strip() for n in allowed_env.split(",") if n.strip()}
        if "*" in allowed:
            allowed = set(SAFE_TOOLS) - denied
        return lambda name: name in allowed and name not in denied
    allowed = set(SAFE_TOOLS) - DEFAULT_DENIED - denied
    return lambda name: name in allowed


def build_server(
    *,
    api_key: str | None = None,
    allowed: t.Iterable[str] | None = None,
) -> t.Any:
    """Build a FastMCP server exposing InstaBox tools.

    Args:
        api_key: API key; defaults to ``INSTABOX_API_KEY``.
        allowed: Explicit tool allowlist. Defaults to the
            ``INSTABOX_ALLOWED_TOOLS`` env var (comma-separated, ``*`` = all);
            tools in ``DEFAULT_DENIED`` stay off unless listed.

    Returns a ``mcp.server.fastmcp.FastMCP`` instance ready for
    ``server.run()`` (stdio).
    """
    try:
        import mcp.server.fastmcp as _fastmcp
    except ImportError as exc:  # pragma: no cover - only without the [mcp] extra
        raise ImportError(
            'the MCP server requires the "mcp" package: uv add "instabox[mcp]"'
        ) from exc
    fastmcp_cls: type[t.Any] = _fastmcp.FastMCP

    from instabox import Client

    api_key = api_key or os.environ.get("INSTABOX_API_KEY")
    if not api_key:
        raise SystemExit("INSTABOX_API_KEY is not set")

    is_enabled = (
        _tool_filter(
            os.environ.get("INSTABOX_ALLOWED_TOOLS"), os.environ.get("INSTABOX_DENIED_TOOLS")
        )
        if allowed is None
        else (lambda name: name in set(allowed))
    )
    client = Client(api_key)
    mcp = fastmcp_cls("instabox")

    def tool(
        name: str, description: str
    ) -> t.Callable[[t.Callable[..., t.Any]], t.Callable[..., t.Any]]:
        def register(fn: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
            if is_enabled(name):
                registered = mcp.tool(name=name, description=description)(fn)
                return fn if registered is None else registered
            return fn

        return register

    @tool("list_machines", "List InstaBox machines in the workspace")
    def list_machines(metadata: str | None = None) -> list[dict[str, t.Any]]:
        machines, _ = client.machines.list(metadata=metadata)
        return [m.raw for m in machines]

    @tool("get_machine", "Get one machine by ID or name")
    def get_machine(id_or_name: str) -> dict[str, t.Any]:
        return client.machines.get(id_or_name).raw

    @tool("get_usage", "Get account usage and limits")
    def get_usage() -> dict[str, t.Any]:
        return client.account.usage().raw

    @tool("exec", "Run a shell command on a machine and return stdout/stderr/exitCode")
    def exec(
        machine_id: str,
        cmd: str,
        timeout_seconds: int | None = None,
    ) -> dict[str, t.Any]:
        result = client.machines.exec(machine_id, cmd=cmd, timeout_seconds=timeout_seconds)
        return {"stdout": result.stdout, "stderr": result.stderr, "exitCode": result.exit_code}

    @tool("read_file", "Read a text file (<=256 KiB) or list a directory on a machine")
    def read_file(machine_id: str, path: str) -> dict[str, t.Any]:
        return client.machines.read_file(machine_id, path).raw

    @tool("write_file", "Write a UTF-8 text file on a machine (parents are created)")
    def write_file(machine_id: str, path: str, content: str) -> dict[str, t.Any]:
        return client.machines.upload_file(machine_id, path, content.encode(), parents=True)

    @tool("suspend_machine", "Pause a machine, preserving memory")
    def suspend_machine(machine_id: str) -> dict[str, t.Any]:
        return client.operations.wait(client.machines.suspend(machine_id).id).raw

    @tool("resume_machine", "Resume a paused machine")
    def resume_machine(machine_id: str) -> dict[str, t.Any]:
        return client.operations.wait(client.machines.resume(machine_id).id).raw

    @tool("create_machine", "Create a new machine (dangerous; denied by default)")
    def create_machine(
        name: str | None = None,
        template_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, t.Any]:
        op = client.operations.wait(
            client.machines.create(name=name, template_id=template_id, snapshot_id=snapshot_id).id
        )
        return op.raw

    @tool("delete_machine", "Delete a machine (dangerous; requires confirm=true)")
    def delete_machine(machine_id: str, confirm: bool) -> dict[str, t.Any]:
        if not confirm:
            return {"error": "pass confirm=true to delete a machine"}
        op = client.operations.wait(client.machines.delete(machine_id).id)
        return op.raw

    @tool("save_snapshot", "Save a snapshot of a machine (dangerous; denied by default)")
    def save_snapshot(machine_id: str, name: str) -> dict[str, t.Any]:
        return client.machines.snapshot(machine_id, name).raw

    @tool("list_snapshots", "List saved snapshots")
    def list_snapshots() -> list[dict[str, t.Any]]:
        return [s.raw for s in client.snapshots.list()]

    return mcp


def main() -> None:  # pragma: no cover - entry point
    """Run the MCP server over stdio."""
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
