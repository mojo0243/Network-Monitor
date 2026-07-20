"""Device online/offline state machine (Phase 4).

`reconcile_devices()` is called once per UniFi poll cycle. It is the single
place that writes to the `devices` and `device_sightings` tables, and it
hands back exactly what changed so the alerting and heuristics layers don't
have to re-derive it by diffing the database themselves.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netmon.models import Device, DeviceHourProfile, DeviceSighting, utcnow
from netmon.unifi_client import ClientRecord


@dataclasses.dataclass
class ReconcileResult:
    new_devices: list[Device] = dataclasses.field(default_factory=list)
    newly_online: list[Device] = dataclasses.field(default_factory=list)
    newly_offline: list[Device] = dataclasses.field(default_factory=list)
    network_changes: list[tuple[Device, str, str]] = dataclasses.field(default_factory=list)  # (device, old_net, new_net)


async def _record_sighting(session: AsyncSession, mac: str, network: str, event_type: str) -> None:
    session.add(DeviceSighting(mac=mac, network=network, event_type=event_type, timestamp=utcnow()))

    hour = utcnow().hour
    profile = await session.scalar(
        select(DeviceHourProfile).where(DeviceHourProfile.mac == mac, DeviceHourProfile.hour == hour)
    )
    if profile is None:
        session.add(DeviceHourProfile(mac=mac, hour=hour, count=1))
    else:
        profile.count += 1


async def reconcile_devices(
    session: AsyncSession,
    active_clients: list[ClientRecord],
    known_clients: list[ClientRecord],
) -> ReconcileResult:
    result = ReconcileResult()
    now = utcnow()

    active_by_mac = {c.mac: c for c in active_clients if c.mac}
    existing_macs = set(active_by_mac) | {c.mac for c in known_clients if c.mac}

    existing_devices: dict[str, Device] = {}
    if existing_macs:
        rows = await session.scalars(select(Device).where(Device.mac.in_(existing_macs)))
        existing_devices = {d.mac: d for d in rows}

    # 1. Active clients: create or transition to online.
    for mac, client in active_by_mac.items():
        device = existing_devices.get(mac)

        if device is None:
            device = Device(
                mac=mac,
                hostname=client.hostname,
                vendor=client.vendor,
                network=client.network,
                is_wired=client.is_wired,
                is_online=True,
                first_seen=now,
                last_seen=now,
            )
            session.add(device)
            existing_devices[mac] = device
            result.new_devices.append(device)
            await _record_sighting(session, mac, client.network, "connect")
            continue

        was_offline = not device.is_online
        network_changed = device.network != client.network

        if was_offline:
            device.is_online = True
            result.newly_online.append(device)
            await _record_sighting(session, mac, client.network, "connect")

        if network_changed:
            # Fires whether the device was already online and roamed, or was
            # offline and comes back on a different network than before --
            # both are "first time on this network" from the heuristics'
            # point of view (see heuristics.find_new_network_for_known_device).
            result.network_changes.append((device, device.network, client.network))
            if not was_offline:
                await _record_sighting(session, mac, client.network, "roam")

        device.network = client.network
        device.last_seen = now
        if client.hostname:
            device.hostname = client.hostname
        if client.vendor:
            device.vendor = client.vendor
        device.is_wired = client.is_wired

    # 2. Devices that were online but are absent from this poll's active list.
    stale = await session.scalars(select(Device).where(Device.is_online.is_(True)))
    for device in stale:
        if device.mac not in active_by_mac:
            device.is_online = False
            result.newly_offline.append(device)
            await _record_sighting(session, device.mac, device.network, "disconnect")

    # 3. Backfill known-but-never-active-this-runtime clients so the
    #    dashboard's "disconnected devices" list is complete from the start,
    #    without treating them as newly-discovered (no alert fires for these).
    for client in known_clients:
        if not client.mac or client.mac in existing_devices:
            continue
        device = Device(
            mac=client.mac,
            hostname=client.hostname,
            vendor=client.vendor,
            network=client.network,
            is_wired=client.is_wired,
            is_online=False,
            first_seen=client.first_seen or now,
            last_seen=client.last_seen or now,
        )
        session.add(device)
        existing_devices[client.mac] = device

    return result


async def devices_grouped_by_network(session: AsyncSession) -> dict[str, list[Device]]:
    devices = await session.scalars(select(Device).order_by(Device.network, Device.last_seen.desc()))
    grouped: dict[str, list[Device]] = {}
    for device in devices:
        grouped.setdefault(device.network, []).append(device)
    return grouped
