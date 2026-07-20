"""Switch/AP offline monitoring (Phase 5).

Separate from tracking.py because infrastructure gets duration-based alerting
(offline for more than N minutes) with de-duplication, rather than the
new-device / per-poll treatment client devices get.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netmon.models import InfraDevice, utcnow
from netmon.unifi_client import InfraRecord


@dataclasses.dataclass
class InfraReconcileResult:
    newly_offline_alerts: list[InfraDevice] = dataclasses.field(default_factory=list)
    recovered: list[InfraDevice] = dataclasses.field(default_factory=list)


async def reconcile_infra(
    session: AsyncSession,
    records: list[InfraRecord],
    offline_minutes_threshold: int,
) -> InfraReconcileResult:
    result = InfraReconcileResult()
    now = utcnow()

    existing = {
        row.unifi_id: row
        for row in await session.scalars(select(InfraDevice))
    }

    seen_ids = set()
    for record in records:
        seen_ids.add(record.unifi_id)
        device = existing.get(record.unifi_id)

        if device is None:
            device = InfraDevice(
                unifi_id=record.unifi_id,
                name=record.name,
                kind=record.kind,
                network=record.network,
                mac=record.mac,
                is_online=record.is_online,
                last_seen=now,
                offline_since=None if record.is_online else now,
                offline_alerted=False,
            )
            session.add(device)
            existing[record.unifi_id] = device
            continue

        device.name = record.name
        device.network = record.network

        if record.is_online:
            if not device.is_online:
                # Recovery.
                if device.offline_alerted:
                    result.recovered.append(device)
                device.offline_since = None
                device.offline_alerted = False
            device.is_online = True
            device.last_seen = now
        else:
            if device.is_online:
                device.is_online = False
                device.offline_since = now
            # else: still offline, offline_since already set from before.

    # A device that has disappeared from the controller's device list
    # entirely (e.g. unplugged and the controller stopped reporting it) is
    # still "offline" for our purposes -- don't lose the alert just because
    # it's absent from this poll rather than reporting state=0.
    for unifi_id, device in existing.items():
        if unifi_id in seen_ids:
            continue
        if device.is_online:
            device.is_online = False
            device.offline_since = now

    for device in existing.values():
        if (
            not device.is_online
            and device.offline_since is not None
            and not device.offline_alerted
            and now - device.offline_since >= dt.timedelta(minutes=offline_minutes_threshold)
        ):
            device.offline_alerted = True
            result.newly_offline_alerts.append(device)

    return result
