"""Regression coverage for a bug found against a real deployment: an
unhandled error fetching UniFi IDS/IPS alarms (an optional, bonus signal)
was aborting the entire poll cycle *before* device/infra reconciliation ran,
so clients never made it into the database even though they were fetched
successfully. See scheduler.py's comment in _unifi_cycle for the fix.
"""
from __future__ import annotations

from sqlalchemy import select

from netmon.models import Device
from netmon.scheduler import Scheduler
from netmon.unifi_client import ClientRecord
from tests.conftest import make_settings


def _client(mac, network="Adults Wi-Fi"):
    return ClientRecord(
        mac=mac, hostname="host", vendor="Apple, Inc.", network=network,
        is_wired=False, is_active=True, first_seen=None, last_seen=None,
    )


async def test_alarm_fetch_failure_does_not_block_device_reconciliation(db_session, tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    scheduler = Scheduler(settings)

    async def fake_active_clients():
        return [_client("aa:bb:cc:11:11:11")]

    async def fake_known_clients():
        return []

    async def fake_devices():
        return []

    async def failing_alarms(client):
        raise RuntimeError("simulated: stat/alarm returned 404 api.err.NotFound")

    monkeypatch.setattr(scheduler.unifi_client, "get_active_clients", fake_active_clients)
    monkeypatch.setattr(scheduler.unifi_client, "get_known_clients", fake_known_clients)
    monkeypatch.setattr(scheduler.unifi_client, "get_devices", fake_devices)
    monkeypatch.setattr(scheduler.ids_ingestor, "poll_new_alarms", failing_alarms)

    await scheduler._unifi_cycle()  # must not raise

    device = await db_session.scalar(select(Device).where(Device.mac == "aa:bb:cc:11:11:11"))
    assert device is not None
    assert device.is_online is True

    await scheduler.unifi_client.close()
    await scheduler.notifier.close()
    await scheduler.http_client.aclose()
