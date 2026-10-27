"""Shared fixtures and hooks for e2e tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from support import (
    default_module,
    find_free_port,
    start_mcp_server,
    stop_mcp_server,
)


@pytest.fixture
async def server_url():
    module_type, module_config = default_module()
    port = find_free_port()
    proc, config_path = await start_mcp_server(module_type, module_config, port)
    yield f"http://127.0.0.1:{port}/mcp"
    await stop_mcp_server(proc, config_path)


def _quota_exhausted(exc: BaseException) -> BaseException | None:
    """The provider error in `exc`'s chain that reports an exhausted quota, if
    any: Google's `429 RESOURCE_EXHAUSTED` (the Gemini SDK's `ClientError`,
    which the module wraps in `TTSError`). Duck-typed, so the SDK need not be
    installed for the hook to load."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            getattr(current, "code", None) == 429
            and getattr(current, "status", None) == "RESOURCE_EXHAUSTED"
        ):
            return current
        current = current.__cause__ or current.__context__
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item):
    # A provider out of quota says nothing about this code: skip the test, as
    # for a missing key, instead of failing the tier on the account's state
    # (specs/testing.md, "Live tier").
    try:
        return (yield)
    except Exception as exc:
        quota = _quota_exhausted(exc)
        if quota is None:
            raise
        pytest.skip(f"provider quota exhausted: {str(quota)[:120]}")
