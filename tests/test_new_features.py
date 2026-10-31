"""Tests for new features: tool policy, SSE streaming, SSH tokens, secrets, orgs."""

from __future__ import annotations

import json
import typing as t

import httpx
import pytest

from instabox import Client, ToolPolicy, exceptions
from instabox._base import parse_sse
from instabox.mcp import DEFAULT_DENIED, SAFE_TOOLS, _tool_filter, build_server

API_KEY = "ibk_" + "x" * 52


def client_with(
    handler: t.Callable[[httpx.Request], httpx.Response], policy: ToolPolicy | None = None
) -> Client:
    return Client(api_key=API_KEY, transport=httpx.MockTransport(handler), policy=policy)


class TestSse:
    def test_parse_sse_multiline_and_comments(self) -> None:
        events = list(
            parse_sse(
                [
                    ": keep-alive",
                    "event: stdout",
                    "data: hello",
                    "data: world",
                    "",
                    "event: exit",
                    'data: {"exitCode": 0}',
                    "",
                ]
            )
        )
        assert events == [
            ("stdout", "hello\nworld"),
            ("exit", '{"exitCode": 0}'),
        ]

    def test_exec_stream(self) -> None:
        sse_body = (
            "event: stdout\ndata: hel\n\n"
            "event: stdout\ndata: lo\n\n"
            'event: exit\ndata: {"exitCode": 0, "timedOut": false}\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Accept"] == "text/event-stream"
            body = json.loads(request.content.decode())
            assert body["cmd"] == "echo hello"
            return httpx.Response(200, content=sse_body.encode())

        client = client_with(handler)
        events = list(client.machines.exec_stream("m_1", cmd="echo hello"))
        assert events == [
            ("stdout", "hel"),
            ("stdout", "lo"),
            ("exit", {"exitCode": 0, "timedOut": False}),
        ]

    def test_exec_stream_rejects_cmd_and_argv(self) -> None:
        client = client_with(lambda r: httpx.Response(200))
        with pytest.raises(ValueError):
            list(client.machines.exec_stream("m_1"))


class TestSshTokens:
    def test_create_list_revoke_certificate(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            path, method = request.url.path, request.method
            if method == "POST" and path.endswith("/ssh-tokens"):
                seen["create"] = request.headers.get("Idempotency-Key", "")
            if method == "POST" and path.endswith("/connections"):
                seen["connections"] = request.headers.get("Idempotency-Key", "")
            if method == "POST" and path.endswith("/ssh-tokens"):
                return httpx.Response(201, json={"id": "st_1", "token": "secret", "port": 22})
            if method == "GET" and path.endswith("/ssh-tokens"):
                return httpx.Response(200, json={"tokens": [{"id": "st_1"}]})
            if method == "DELETE" and path.endswith("/ssh-tokens/st_1"):
                return httpx.Response(204)
            if method == "DELETE" and path.endswith("/ssh-tokens"):
                return httpx.Response(200, json={"revoked": 3})
            if method == "POST" and path.endswith("/connections"):
                assert request.headers.get("Idempotency-Key")
                return httpx.Response(201, json={"certificate": "ssh-cert ...", "port": 22})
            return httpx.Response(404)

        client = client_with(handler)
        token = client.machines.ssh_tokens.create("m_1", name="ci")
        assert token.token == "secret"  # secret only in create response
        assert len(client.machines.ssh_tokens.list("m_1")) == 1
        client.machines.ssh_tokens.revoke("m_1", "st_1")
        assert client.machines.ssh_tokens.revoke_all("m_1") == 3
        cert = client.machines.ssh_tokens.certificate("m_1", "ssh-ed25519 AAA")
        assert cert.certificate == "ssh-cert ..."
        # ssh-tokens create has no idempotency key; the certificate route does
        assert seen["create"] == ""
        assert seen["connections"]


class TestMachineSecrets:
    def test_inspect_update_refresh(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"secrets": ["all"], "version": 2})
            body = json.loads(request.content.decode() or "{}")
            return httpx.Response(200, json={"secrets": body.get("secrets", [])})

        client = client_with(handler)
        assert client.machines.secrets.inspect("m_1")["version"] == 2
        updated = client.machines.secrets.update("m_1", secrets=["DB_PASS"])
        assert updated["secrets"] == ["DB_PASS"]
        client.machines.secrets.refresh("m_1")


class TestOrgsAndOrgSecrets:
    def test_account_orgs(self) -> None:
        client = client_with(lambda r: httpx.Response(200, json={"orgs": [{"id": "o1"}]}))
        assert client.account.orgs()[0]["id"] == "o1"

    def test_org_secrets_crud(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path, method = request.url.path, request.method
            if method == "GET" and path == "/v1/orgs/o1/secrets":
                return httpx.Response(
                    200, json={"items": [{"name": "DB_PASS", "delivery": "proxy"}]}
                )
            if method == "PUT" and path == "/v1/orgs/o1/secrets/DB_PASS":
                body = json.loads(request.content.decode())
                assert body["delivery"] == "proxy" and "value" in body
                return httpx.Response(201, json={"name": "DB_PASS", "version": 1})
            if method == "POST" and path == "/v1/orgs/o1/secrets/import":
                return httpx.Response(200, json={"items": [{"name": "A"}, {"name": "B"}]})
            if method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(404)

        client = client_with(handler)
        assert client.org_secrets.list("o1")[0].name == "DB_PASS"
        client.org_secrets.put("o1", "DB_PASS", "hunter2", delivery="proxy", hosts=["*"])
        imported = client.org_secrets.import_many("o1", {"A": "1", "B": "2"}, delivery="env")
        assert [s.name for s in imported] == ["A", "B"]
        client.org_secrets.delete("o1", "DB_PASS")


class TestToolPolicy:
    def test_allow_only(self) -> None:
        client = client_with(
            lambda r: httpx.Response(200, json={"stdout": "ok", "stderr": "", "exitCode": 0}),
            policy=ToolPolicy(allow=["machines.exec"]),
        )
        result = client.machines.exec("m_1", cmd="true")
        assert result.stdout == "ok"
        with pytest.raises(exceptions.PermissionDeniedError) as exc_info:
            client.machines.delete("m_1")
        assert exc_info.value.code == "sdk_not_allowed"
        # no request was made for the denied call

    def test_prefix_wildcard_and_deny_wins(self) -> None:
        client = client_with(
            lambda r: httpx.Response(200, json={"id": "m_1"}),
            policy=ToolPolicy(allow=["machines.*"], deny=["machines.delete"]),
        )
        assert client.machines.get("m_1").id == "m_1"
        with pytest.raises(exceptions.PermissionDeniedError):
            client.machines.delete("m_1")

    def test_nested_resources_are_checked(self) -> None:
        client = client_with(
            lambda r: httpx.Response(200, json={}),
            policy=ToolPolicy(allow=["machines.*"]),
        )
        with pytest.raises(exceptions.PermissionDeniedError):
            client.machines.secrets.refresh("m_1")

    def test_async_policy(self) -> None:
        from instabox import AsyncClient

        async def run() -> None:
            async with AsyncClient(
                api_key=API_KEY,
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                policy=ToolPolicy(allow=["machines.get"]),
            ) as client:
                await client.machines.get("m_1")
                with pytest.raises(exceptions.PermissionDeniedError):
                    await client.machines.delete("m_1")

        import asyncio

        asyncio.run(run())

    def test_no_policy_allows_everything(self) -> None:
        client = client_with(lambda r: httpx.Response(200, json={}))
        client.account.me()


class TestMcpServer:
    def test_tool_filter_defaults(self) -> None:
        is_enabled = _tool_filter(None, None)
        assert not is_enabled("create_machine")
        assert not is_enabled("delete_machine")
        assert is_enabled("exec")

    def test_tool_filter_env(self) -> None:
        is_enabled = _tool_filter("create_machine", None)
        assert is_enabled("create_machine")

    def test_tool_filter_wildcard(self) -> None:
        is_enabled = _tool_filter("*", None)
        assert all(is_enabled(name) for name in SAFE_TOOLS)

    def test_build_server_registers_safe_tools_only(self) -> None:
        import asyncio

        server = build_server(api_key=API_KEY, allowed=set(SAFE_TOOLS) - DEFAULT_DENIED)
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        assert "exec" in names and "list_machines" in names
        assert not (names & DEFAULT_DENIED)

    def test_build_server_without_key_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INSTABOX_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            build_server()
