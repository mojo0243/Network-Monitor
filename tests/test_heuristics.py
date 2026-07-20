from __future__ import annotations

import datetime as dt

from netmon.config import HeuristicsConfig
from netmon.heuristics import (
    check_flapping,
    check_new_device_spike,
    check_vendor_mismatch,
    find_new_network_for_known_device,
    run_heuristics,
)
from netmon.models import Device, DeviceSighting, utcnow
from netmon.tracking import ReconcileResult, reconcile_devices
from netmon.unifi_client import ClientRecord


def _client(mac, network):
    return ClientRecord(
        mac=mac, hostname="host", vendor="Apple, Inc.", network=network,
        is_wired=False, is_active=True, first_seen=None, last_seen=None,
    )


async def test_new_network_for_known_device_true_when_device_has_other_history(db_session):
    mac = "aa:bb:cc:00:01:01"
    db_session.add(DeviceSighting(mac=mac, network="Adults Wi-Fi", event_type="connect", timestamp=utcnow()))
    db_session.add(DeviceSighting(mac=mac, network="Guests", event_type="connect", timestamp=utcnow()))
    await db_session.flush()

    assert await find_new_network_for_known_device(db_session, mac, "Guests") is True


async def test_new_network_for_known_device_false_for_brand_new_device(db_session):
    mac = "aa:bb:cc:00:01:02"
    db_session.add(DeviceSighting(mac=mac, network="Guests", event_type="connect", timestamp=utcnow()))
    await db_session.flush()

    # Only ever seen on Guests -- not a network *change*.
    assert await find_new_network_for_known_device(db_session, mac, "Guests") is False


def test_vendor_mismatch_flags_apple_hostname_on_non_apple_vendor():
    device = Device(mac="x", hostname="Johns-iPhone", vendor="TP-Link Corporation", network="Adults Wi-Fi")
    msg = check_vendor_mismatch(device)
    assert msg is not None
    assert "Apple" in msg


def test_vendor_mismatch_silent_when_consistent():
    device = Device(mac="x", hostname="Johns-iPhone", vendor="Apple, Inc.", network="Adults Wi-Fi")
    assert check_vendor_mismatch(device) is None


def test_vendor_mismatch_silent_for_unrecognized_hostname():
    device = Device(mac="x", hostname="some-random-box", vendor="Totally Unknown Vendor", network="Adults Wi-Fi")
    assert check_vendor_mismatch(device) is None


async def test_flapping_detected_over_threshold(db_session):
    mac = "aa:bb:cc:00:01:03"
    now = utcnow()
    for i in range(6):
        event = "connect" if i % 2 == 0 else "disconnect"
        db_session.add(DeviceSighting(mac=mac, network="IoT", event_type=event, timestamp=now - dt.timedelta(minutes=i)))
    await db_session.flush()

    assert await check_flapping(db_session, mac, window_minutes=10, threshold=6) is True
    assert await check_flapping(db_session, mac, window_minutes=10, threshold=10) is False


async def test_new_device_spike_counts_recent_first_seen(db_session):
    now = utcnow()
    for i in range(5):
        db_session.add(Device(mac=f"aa:bb:cc:00:02:0{i}", network="Guests", first_seen=now, last_seen=now))
    await db_session.flush()

    count = await check_new_device_spike(db_session, window_minutes=15, threshold=5)
    assert count == 5


async def test_run_heuristics_end_to_end_flags_new_network(db_session):
    mac = "aa:bb:cc:00:03:01"
    await reconcile_devices(db_session, [_client(mac, "Adults Wi-Fi")], [])
    result = await reconcile_devices(db_session, [_client(mac, "Wired Adult")], [])

    findings = await run_heuristics(db_session, HeuristicsConfig(), result)

    assert any(f.heuristic == "new_network" and f.target == mac for f in findings)


async def test_run_heuristics_disabled_returns_nothing(db_session):
    findings = await run_heuristics(db_session, HeuristicsConfig(enabled=False), ReconcileResult())
    assert findings == []
