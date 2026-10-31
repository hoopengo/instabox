"""Tests for the sync client using httpx.MockTransport."""

from __future__ import annotations

import json
import typing as t

import httpx
import pytest

from instabox import Client, exceptions
from instabox.exceptions import APIError


def _handler(
    routes: t.Sequence[tuple[str, str, t.Any, int]],
) -> httpx.MockTransport:
    """routes: list of (method, path, response_body_or_callable, status)."""
    index: dict[tuple[str, str], tuple[t.Any, int]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key not in index:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "no route"}})
        body, status = index[key]
        return httpx.Response(status, json=body) if not callable(body) else body(request)

    return httpx.MockTransport(handle)


def _client(handler: t.Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(api_key="ibk_" + "x" * 52, transport=httpx.MockTransport(handler))


def make_client(routes: dict[tuple[str, str], t.Any]) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        body = routes.get(key)
        if body is None:
            return httpx.Response(
                404, json={"error": {"code": "not_found", "message": "unknown route"}}
            )
        if callable(body):
            return body(request)
        return httpx.Response(200, json=body)

    return _client(handler)


OP = {
    "id": "op_abcdefghijklmnopqrstuvwxyz123456",
    "machineId": "m_test123",
    "type": "create",
    "state": "queued",
    "error": None,
    "createdAt": "2026-10-31T00:00:00Z",
    "updatedAt": "2026-10-31T00:00:00Z",
}

MACHINE = {
    "id": "m_test123",
    "name": "dev",
    "state": "running",
    "desiredState": "running",
    "spec": {"vcpus": 4, "memMiB": 8192, "persistentDiskGiB": 32},
    "metadata": {"task": "review"},
    "createdAt": "2026-10-31T00:00:00Z",
}


class TestMachines:
    def test_create_returns_operation_and_sends_headers(self) -> None:
        captured: dict[str, t.Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["idem"] = request.headers.get("Idempotency-Key")
            captured["auth"] = request.headers.get("X-API-Key")
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(202, json=OP)

        client = _client(handler)
        op = client.machines.create(name="dev", template_id="agent-browser", vcpus=2)

        assert op.id == OP["id"]
        assert op.machine_id == "m_test123"
        assert op.state == "queued"
        assert captured["idem"]
        assert captured["auth"].startswith("ibk_")
        assert captured["body"] == {"name": "dev", "templateId": "agent-browser", "vcpus": 2}

    def test_get_machine_by_name(self) -> None:
        client = make_client({("GET", "/v1/machines/dev"): MACHINE})
        machine = client.machines.get("dev")
        assert machine.id == "m_test123"
        assert machine.state == "running"
        assert machine.spec is not None and machine.spec.vcpus == 4
        assert machine.metadata == {"task": "review"}

    def test_list_and_pagination(self) -> None:
        page1 = {"machines": [MACHINE], "nextCursor": "c2"}
        page2 = {"machines": [], "nextCursor": None}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("cursor") == "c2":
                return httpx.Response(200, json=page2)
            return httpx.Response(200, json=page1)

        client = _client(handler)
        all_machines = list(client.machines.iter_machines())
        assert len(all_machines) == 1

    def test_delete_sends_confirm_header(self) -> None:
        captured: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["confirm"] = request.headers.get("X-Confirm-Delete")
            captured["idem"] = request.headers.get("Idempotency-Key")
            return httpx.Response(202, json={**OP, "type": "delete"})

        client = _client(handler)
        op = client.machines.delete("m_test123")
        assert op.type == "delete"
        assert captured["confirm"] == "m_test123"
        assert captured["idem"]


class TestLifecycle:
    def test_start(self) -> None:
        client = make_client({("POST", "/v1/machines/m_1/start"): OP})
        op = client.machines.start("m_1")
        assert op.id == OP["id"]

    def test_stop_with_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            assert body == {"timeoutSeconds": 120}
            return httpx.Response(202, json=OP)

        client = _client(handler)
        client.machines.stop("m_1", timeout_seconds=120)

    def test_exec_ok(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            assert body == {"cmd": "echo hello", "timeoutSeconds": 30}
            return httpx.Response(
                200,
                json={
                    "stdout": "hello\n",
                    "stderr": "",
                    "exitCode": 0,
                    "timedOut": False,
                    "truncated": False,
                },
            )

        client = _client(handler)
        result = client.machines.exec("m_1", cmd="echo hello", timeout_seconds=30)
        assert result.ok and result.stdout == "hello\n"

    def test_exec_rejects_cmd_and_argv(self) -> None:
        client = make_client({})
        with pytest.raises(ValueError):
            client.machines.exec("m_1", cmd="ls", argv=["ls"])
        with pytest.raises(ValueError):
            client.machines.exec("m_1")


class TestOperations:
    def test_wait_long_polls_until_finished(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            assert request.url.params.get("wait") == "25s"
            state = "running" if calls["n"] == 1 else "succeeded"
            return httpx.Response(200, json={**OP, "state": state})

        client = _client(handler)
        op = client.operations.wait(OP["id"])
        assert op.succeeded
        assert calls["n"] == 2


class TestFiles:
    def test_read_file(self) -> None:
        body = {
            "path": "/data/home",
            "type": "dir",
            "entries": [
                {"name": "a.txt", "type": "file", "size": 3, "mode": "0644"},
            ],
        }
        client = make_client({("GET", "/v1/machines/m_1/files"): body})
        listing = client.machines.read_file("m_1", "/data/home")
        assert listing.entries[0].name == "a.txt"

    def test_upload_and_download(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                assert request.content == b"hello"
                assert request.url.params["path"] == "/data/home/a.txt"
                return httpx.Response(201, json={"path": "/data/home/a.txt", "size": 5})
            assert request.method == "GET"
            return httpx.Response(200, content=b"hello")

        client = _client(handler)
        client.machines.upload_file("m_1", "/data/home/a.txt", b"hello")
        assert client.machines.download_file("m_1", "/data/home/a.txt") == b"hello"


class TestErrors:
    def test_structured_error_maps_to_exception(self) -> None:
        client = make_client({})
        with pytest.raises(exceptions.NotFoundError) as exc_info:
            client.machines.get("missing")
        assert exc_info.value.code == "not_found"

    def test_retryable_and_retry_after(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": "rate_limited",
                        "message": "too many starts",
                        "retryable": True,
                        "retryAfterSeconds": 30,
                        "requestId": "req_1",
                    }
                },
            )

        client = _client(handler)
        with pytest.raises(exceptions.RateLimitError) as exc_info:
            client.machines.start("m_1")
        err = exc_info.value
        assert err.retryable and err.retry_after_seconds == 30 and err.request_id == "req_1"

    def test_error_hierarchy_is_api_error(self) -> None:
        client = make_client({})
        with pytest.raises(APIError):
            client.account.me()

    def test_non_json_error_body(self) -> None:
        client = Client(
            api_key="ibk_x",
            transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")),
        )
        with pytest.raises(exceptions.ServerError) as exc_info:
            client.account.me()
        assert exc_info.value.code == "unknown"


class TestAccount:
    def test_me_templates_usage(self) -> None:
        client = make_client(
            {
                ("GET", "/v1/me"): {"userId": "u1", "login": "alex", "admin": False},
                ("GET", "/v1/templates"): {
                    "templates": [
                        {
                            "id": "agent-browser",
                            "version": "1",
                            "default": True,
                            "latest": True,
                            "defaultSpec": {"vcpus": 4, "memMiB": 8192},
                        },
                    ]
                },
                ("GET", "/v1/usage"): {"machines": 1, "snapshots": 0, "limits": {"maxMachines": 5}},
            }
        )
        user = client.account.me()
        assert user.login == "alex"
        templates = client.account.templates()
        assert templates[0].default_spec is not None
        usage = client.account.usage()
        assert usage.limits["maxMachines"] == 5
