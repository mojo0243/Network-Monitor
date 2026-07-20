from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from netmon.infra_monitor import reconcile_infra
from netmon.models import InfraDevice, utcnow
from netmon.unifi_client import InfraRecord


def _record(unifi_id="sw1", online=True, network="Wired Adult"):
    return InfraRecord(unifi_id=unifi_id, name="Switch-1", kind="switch", network=network, mac=None, is_online=online)


async def test_offline_below_threshold_does_not_alert(db_session):
    await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)
    device = await db_session.scalar(select(InfraDevice).where(InfraDevice.unifi_id == "sw1"))
    device.offline_since = utcnow() - dt.timedelta(minutes=5)

    result = await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)

    assert result.newly_offline_alerts == []


async def test_offline_past_threshold_alerts_once(db_session):
    await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)
    device = await db_session.scalar(select(InfraDevice).where(InfraDevice.unifi_id == "sw1"))
    device.offline_since = utcnow() - dt.timedelta(minutes=25)

    result = await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)
    assert len(result.newly_offline_alerts) == 1

    # Still offline on the next poll -- must not alert again (de-duplication).
    result2 = await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)
    assert result2.newly_offline_alerts == []


async def test_recovery_after_alert_is_reported(db_session):
    await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)
    device = await db_session.scalar(select(InfraDevice).where(InfraDevice.unifi_id == "sw1"))
    device.offline_since = utcnow() - dt.timedelta(minutes=25)
    await reconcile_infra(db_session, [_record(online=False)], offline_minutes_threshold=20)

    result = await reconcile_infra(db_session, [_record(online=True)], offline_minutes_threshold=20)

    assert len(result.recovered) == 1
    assert result.recovered[0].is_online is True
    assert result.recovered[0].offline_alerted is False


async def test_device_missing_from_poll_entirely_still_tracked_offline(db_session):
    await reconcile_infra(db_session, [_record(online=True)], offline_minutes_threshold=20)

    result = await reconcile_infra(db_session, [], offline_minutes_threshold=20)

    device = await db_session.scalar(select(InfraDevice).where(InfraDevice.unifi_id == "sw1"))
    assert device.is_online is False
    assert device.offline_since is not None
