from __future__ import annotations

import datetime as dt

import httpx
import pytest

from netmon.unifi_client import UnifiClient, UnifiRequestError
from tests.conftest import make_settings


class _ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses):
        self._responses = list(responses)

    async def handle_async_request(self, request):
        status, body = self._responses.pop(0)
        return httpx.Response(status, json=body, request=request)


def _client_with_transport(settings, responses) -> UnifiClient:
    client = UnifiClient(settings.unifi, settings)
    client._client = httpx.AsyncClient(
        base_url=f"https://{settings.unifi.host}", transport=_ScriptedTransport(responses)
    )
    client._logged_in = True  # skip the login round-trip, not under test here
    return client


async def test_get_recent_alarms_returns_empty_on_404_instead_of_raising(tmp_path):
    settings = make_settings(tmp_path)
    # One response: the exact 404 body a real UDM-Pro returned in the field
    # when stat/alarm isn't available on that controller.
    client = _client_with_transport(
        settings, [(404, {"meta": {"rc": "error", "msg": "api.err.NotFound"}, "data": []})]
    )

    alarms = await client.get_recent_alarms()

    assert alarms == []
    assert client._alarm_endpoint_warned is True
    await client.close()


async def test_get_recent_alarms_only_warns_once(tmp_path):
    settings = make_settings(tmp_path)
    client = _client_with_transport(
        settings,
        [
            (404, {"meta": {"rc": "error", "msg": "api.err.NotFound"}, "data": []}),
            (404, {"meta": {"rc": "error", "msg": "api.err.NotFound"}, "data": []}),
        ],
    )

    await client.get_recent_alarms()
    assert client._alarm_endpoint_warned is True
    await client.get_recent_alarms()  # must not raise the second time either
    await client.close()


async def test_get_recent_alarms_parses_port_scan_style_fields(tmp_path):
    settings = make_settings(tmp_path)
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    client = _client_with_transport(
        settings,
        [
            (
                200,
                {
                    "meta": {"rc": "ok"},
                    "data": [
                        {
                            "key": "EVT_IPS_IpsAlert",
                            "msg": "Attempted Information Leak",
                            "time": now_ms,
                            "catname": "Attempted-Recon",
                            "srcip": "10.72.8.55",
                            "dstip": "10.72.8.10",
                            "dstport": 22,
                        }
                    ],
                },
            )
        ],
    )

    alarms = await client.get_recent_alarms()

    assert len(alarms) == 1
    alarm = alarms[0]
    assert alarm.category == "Attempted-Recon"
    assert alarm.src_ip == "10.72.8.55"
    assert alarm.dst_ip == "10.72.8.10"
    assert alarm.dst_port == 22
    assert "src=10.72.8.55" in alarm.detail_suffix()
    assert "dst=10.72.8.10:22" in alarm.detail_suffix()
    await client.close()


async def test_core_endpoint_404_still_raises(tmp_path):
    """Only the optional alarms endpoint degrades gracefully -- a 404 on a
    core endpoint (clients/devices) is a real problem and must still surface
    loudly rather than being silently swallowed.
    """
    settings = make_settings(tmp_path)
    client = _client_with_transport(settings, [(404, {"meta": {"rc": "error"}, "data": []})])

    with pytest.raises(UnifiRequestError):
        await client.get_active_clients()
    await client.close()
