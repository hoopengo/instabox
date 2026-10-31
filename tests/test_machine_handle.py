"""Tests for object-oriented machine handles (MachineHandle / AsyncMachineHandle)."""

from __future__ import annotations

import json
import typing as t

import httpx
import pytest

from instabox import AsyncClient, Client, MachineHandle, ToolPolicy, exceptions

API_KEY = "ibk_" + "x" * 52


def client_from(
    handler: t.Callable[[httpx.Request], httpx.Response], policy: ToolPolicy | None = None
) -> Client:
    return Client(api_key=API_KEY, transport=httpx.MockTransport(handler), policy=policy)


def make_client(routes: dict[tuple[str, str], object], policy: ToolPolicy | None = None) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get((request.method, request.url.path))
        if body is None:
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        if callable(body):
            return body(request)
        return httpx.Response(200, json=body)

    return Client(api_key=API_KEY, transport=httpx.MockTransport(handler), policy=policy)


def op(state: str = "succeeded") -> dict[str, object]:
    return {"id": "op_1", "machineId": "m_1", "type": "start", "state": state}


def machine(state: str = "running", ready: bool = True) -> dict[str, object]:
    return {
        "id": "m_1",
        "name": "dev",
        "state": state,
        "runtime": {"ready": ready},
        "metadata": {},
    }


class TestMachineHandle:
    def test_start_stop_suspend_resume(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path)
            calls.append(f"{key[0]} {key[1]}")
            if key[1].startswith("/v1/machines/m_1/"):
                return httpx.Response(202, json=op())
            return httpx.Response(200, json=machine())

        client = client_from(handler)
        m = client.machine("dev")
        m.start()
        m.stop(timeout_seconds=30)
        m.suspend()
        m.resume()
        # each lifecycle call resolves the handle's id first (GET by name)
        assert calls == [
            "GET /v1/machines/dev",
            "POST /v1/machines/m_1/start",
            "GET /v1/machines/dev",
            "POST /v1/machines/m_1/stop",
            "GET /v1/machines/dev",
            "POST /v1/machines/m_1/suspend",
            "GET /v1/machines/dev",
            "POST /v1/machines/m_1/resume",
        ]

    def test_exec_and_run(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"stdout": "hi\n", "stderr": "", "exitCode": 0})

        client = client_from(handler)
        m = client.machine("dev")
        result = m.exec("echo hi")
        assert result.stdout == "hi\n"
        m.run("echo hi")

    def test_run_raises_on_nonzero(self) -> None:
        client = make_client(
            {
                ("GET", "/v1/machines/dev"): machine(),
                ("POST", "/v1/machines/m_1/exec"): {"stdout": "", "stderr": "boom", "exitCode": 2},
            }
        )
        with pytest.raises(RuntimeError, match="exit code 2"):
            client.machine("dev").run("false")

    def test_is_running_and_metadata(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH":
                body = json.loads(request.content.decode())
                return httpx.Response(200, json={**machine(), "metadata": body["metadata"]})
            return httpx.Response(200, json=machine())

        client = client_from(handler)
        m = client.machine("dev")
        assert m.is_running() is True
        updated = m.set_metadata({"task": "review"})
        assert updated.metadata == {"task": "review"}

    def test_files(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                assert request.url.params["parents"] == "true"
                return httpx.Response(201, json={"path": "/data/home/a.txt", "size": 2})
            if request.url.path.endswith("/files/content"):
                return httpx.Response(200, content=b"hi")
            return httpx.Response(200, json={"path": "/data/home/a.txt", "type": "file"})

        client = client_from(handler)
        m = client.machine("dev")
        m.write_file("/data/home/a.txt", b"hi")
        assert m.download_file("/data/home/a.txt") == b"hi"
        assert m.read_file("/data/home/a.txt").path == "/data/home/a.txt"

    def test_connect_resumes_paused(self) -> None:
        calls: list[str] = []
        states = iter(["suspended", "running", "running"])

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.method == "POST":
                return httpx.Response(202, json=op())
            return httpx.Response(200, json=machine(state=next(states)))

        client = client_from(handler)
        result = client.machine("dev").connect()
        assert result.state == "running"
        assert "POST /v1/machines/m_1/resume" in calls

    def test_connect_running_is_noop(self) -> None:
        client = make_client({("GET", "/v1/machines/dev"): machine()})
        result = client.machine("dev").connect()
        assert result.state == "running"

    def test_policy_enforced_through_handle(self) -> None:
        client = make_client(
            {("GET", "/v1/machines/dev"): machine()}, policy=ToolPolicy(allow=["machines.get"])
        )
        m: MachineHandle = client.machine("dev")
        assert m.refresh().id == "m_1"
        with pytest.raises(exceptions.PermissionDeniedError):
            m.start()

    def test_wait_operation_long_polls(self) -> None:
        seq = iter([op("running"), op("succeeded")])
        client = Client(
            api_key=API_KEY,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=next(seq))),
        )
        from instabox.models import Operation

        finished = client.machine("dev").wait_operation(Operation.from_dict(op("running")))
        assert finished.succeeded


class TestAsyncMachineHandle:
    async def test_lifecycle(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.method == "POST":
                return httpx.Response(202, json=op())
            return httpx.Response(200, json=machine())

        async with AsyncClient(api_key=API_KEY, transport=httpx.MockTransport(handler)) as client:
            m = client.machine("dev")
            await m.start()
            await m.suspend()
            assert await m.is_running() is True

    async def test_exec(self) -> None:
        async with AsyncClient(
            api_key=API_KEY,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"stdout": "ok\n", "stderr": "", "exitCode": 0})
            ),
        ) as client:
            result = await client.machine("dev").exec("echo ok")
            assert result.exit_code == 0

    async def test_policy(self) -> None:
        async with AsyncClient(
            api_key=API_KEY,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=machine())),
            policy=ToolPolicy(allow=["machines.get"]),
        ) as client:
            m = client.machine("dev")
            assert (await m.refresh()).id == "m_1"
            with pytest.raises(exceptions.PermissionDeniedError):
                await m.start()
