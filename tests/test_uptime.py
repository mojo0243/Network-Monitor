from __future__ import annotations

import httpx
import pytest

from netmon.config import WebsiteMonitorConfig
from netmon.uptime import run_check


def _monitor(**overrides):
    defaults = dict(name="example", url="https://example.test", interval_seconds=60, timeout_seconds=5, failure_threshold=2)
    defaults.update(overrides)
    return WebsiteMonitorConfig(**defaults)


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """Returns a scripted sequence of responses/exceptions, one per request."""

    def __init__(self, script):
        self._script = list(script)

    async def handle_async_request(self, request):
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=request)


@pytest.mark.parametrize("failure_threshold", [2])
async def test_incident_opens_after_consecutive_failures(db_session, failure_threshold):
    monitor = _monitor(failure_threshold=failure_threshold)
    transport = _ScriptedTransport([200, 500, 500])
    async with httpx.AsyncClient(transport=transport) as http_client:
        r1 = await run_check(db_session, http_client, monitor)
        assert r1.incident_opened is None

        r2 = await run_check(db_session, http_client, monitor)
        assert r2.incident_opened is None  # only 1 failure so far

        r3 = await run_check(db_session, http_client, monitor)
        assert r3.incident_opened is not None


async def test_incident_resolves_on_next_success(db_session):
    monitor = _monitor(failure_threshold=1)
    transport = _ScriptedTransport([500, 200])
    async with httpx.AsyncClient(transport=transport) as http_client:
        down = await run_check(db_session, http_client, monitor)
        assert down.incident_opened is not None

        up = await run_check(db_session, http_client, monitor)
        assert up.incident_resolved is not None
        assert up.incident_resolved.resolved_at is not None


async def test_network_error_counts_as_down(db_session):
    monitor = _monitor(failure_threshold=1)
    transport = _ScriptedTransport([httpx.ConnectTimeout("timed out")])
    async with httpx.AsyncClient(transport=transport) as http_client:
        result = await run_check(db_session, http_client, monitor)
        assert result.check.status == "down"
        assert result.incident_opened is not None
