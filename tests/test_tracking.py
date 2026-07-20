from __future__ import annotations

from netmon.tracking import reconcile_devices
from netmon.unifi_client import ClientRecord


def _client(mac, network, hostname="host", vendor="Apple, Inc.", wired=False):
    return ClientRecord(
        mac=mac,
        hostname=hostname,
        vendor=vendor,
        network=network,
        is_wired=wired,
        is_active=True,
        first_seen=None,
        last_seen=None,
    )


async def test_new_device_is_created_and_reported(db_session):
    result = await reconcile_devices(db_session, [_client("aa:bb:cc:00:00:01", "Adults Wi-Fi")], [])

    assert len(result.new_devices) == 1
    device = result.new_devices[0]
    assert device.mac == "aa:bb:cc:00:00:01"
    assert device.network == "Adults Wi-Fi"
    assert device.is_online is True


async def test_device_goes_offline_when_absent_from_poll(db_session):
    mac = "aa:bb:cc:00:00:02"
    await reconcile_devices(db_session, [_client(mac, "Adults Wi-Fi")], [])

    result = await reconcile_devices(db_session, [], [])

    assert len(result.newly_offline) == 1
    assert result.newly_offline[0].mac == mac
    assert result.newly_offline[0].is_online is False


async def test_roam_to_new_network_while_still_online_is_detected(db_session):
    mac = "aa:bb:cc:00:00:03"
    await reconcile_devices(db_session, [_client(mac, "Adults Wi-Fi")], [])

    result = await reconcile_devices(db_session, [_client(mac, "Guests")], [])

    assert len(result.network_changes) == 1
    device, old_net, new_net = result.network_changes[0]
    assert (old_net, new_net) == ("Adults Wi-Fi", "Guests")
    assert device.network == "Guests"


async def test_reconnect_on_new_network_after_offline_is_detected(db_session):
    """Regression test: a device that goes offline and reconnects on a
    *different* network must still show up in network_changes, not just
    newly_online -- these used to be mutually exclusive branches.
    """
    mac = "aa:bb:cc:00:00:04"
    await reconcile_devices(db_session, [_client(mac, "Adults Wi-Fi")], [])
    await reconcile_devices(db_session, [], [])  # goes offline

    result = await reconcile_devices(db_session, [_client(mac, "IoT")], [])

    assert len(result.newly_online) == 1
    assert len(result.network_changes) == 1
    _, old_net, new_net = result.network_changes[0]
    assert (old_net, new_net) == ("Adults Wi-Fi", "IoT")


async def test_known_client_backfilled_without_new_device_alert(db_session):
    known = _client("aa:bb:cc:00:00:05", "Guests")
    known.is_active = False

    result = await reconcile_devices(db_session, [], [known])

    assert result.new_devices == []  # backfill isn't treated as a fresh discovery
