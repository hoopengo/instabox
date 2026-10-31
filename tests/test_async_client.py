"""Tests for the async client."""

from __future__ import annotations

import httpx
import pytest

from instabox import AsyncClient, exceptions

MACHINE = {"id": "m_test123", "name": "dev", "state": "running", "metadata": {}}


def make_client(routes: dict[tuple[str, str], object]) -> AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get((request.method, request.url.path))
        if body is None:
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        if callable(body):
            return body(request)
        return httpx.Response(200, json=body)

    return AsyncClient(api_key="ibk_" + "x" * 52, transport=httpx.MockTransport(handler))


async def test_get_machine() -> None:
    client = make_client({("GET", "/v1/machines/dev"): MACHINE})
    machine = await client.machines.get("dev")
    assert machine.id == "m_test123"


async def test_exec() -> None:
    client = make_client(
        {
            ("POST", "/v1/machines/m_1/exec"): {
                "stdout": "hi\n",
                "stderr": "",
                "exitCode": 0,
            }
        }
    )
    result = await client.machines.exec("m_1", argv=["echo", "hi"])
    assert result.stdout == "hi\n"


async def test_operations_wait() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        state = "running" if calls["n"] == 1 else "succeeded"
        return httpx.Response(
            200,
            json={
                "id": "op_1",
                "machineId": "m_1",
                "type": "create",
                "state": state,
            },
        )

    client = AsyncClient(api_key="ibk_x", transport=httpx.MockTransport(handler))
    op = await client.operations.wait("op_1")
    assert op.succeeded and calls["n"] == 2
    await client.aclose()


async def test_context_manager() -> None:
    async with make_client({}) as client:
        with pytest.raises(exceptions.NotFoundError):
            await client.machines.get("nope")
