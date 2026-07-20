"""Tier 2 suspicious-activity heuristics (Phase 6, task 6.2).

These are deliberately simple, explainable rules -- not ML -- because false
positives are cheap to shrug off in a Discord channel but a black-box model
you can't reason about is not, on a home network. Tier 1 (netmon.ids_ingest,
the UDM-Pro's own IDS/IPS) is the higher-confidence signal; treat these as a
supplement, and expect to tune the thresholds in config.yml's
alerts.heuristics section for your own network's normal behaviour.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from netmon.config import HeuristicsConfig
from netmon.models import Device, DeviceHourProfile, DeviceSighting, utcnow
from netmon.tracking import ReconcileResult

# hostname keyword -> vendor keywords we'd expect the OUI lookup to contain.
# Deliberately small and conservative: an unmatched hostname is just skipped,
# not flagged, because there are far more legitimate hostname patterns than
# this list can ever cover.
_VENDOR_HINTS: dict[str, tuple[str, ...]] = {
    "iphone": ("apple",),
    "ipad": ("apple",),
    "macbook": ("apple",),
    "appletv": ("apple",),
    "galaxy": ("samsung",),
    "pixel": ("google",),
    "nest": ("google", "nest"),
    "echo": ("amazon",),
    "kindle": ("amazon",),
    "firetv": ("amazon",),
    "raspberrypi": ("raspberry pi",),
}


@dataclasses.dataclass
class SuspiciousFinding:
    heuristic: str
    target: str | None  # usually a mac, or None for network-wide findings
    message: str


async def find_new_network_for_known_device(session: AsyncSession, mac: str, new_network: str) -> bool:
    other_networks_count = await session.scalar(
        select(func.count(DeviceSighting.id)).where(
            DeviceSighting.mac == mac, DeviceSighting.network != new_network
        )
    )
    this_network_count = await session.scalar(
        select(func.count(DeviceSighting.id)).where(
            DeviceSighting.mac == mac, DeviceSighting.network == new_network
        )
    )
    return bool(other_networks_count) and (this_network_count or 0) <= 1


def check_vendor_mismatch(device: Device) -> str | None:
    if not device.hostname or not device.vendor:
        return None
    hostname = device.hostname.lower()
    vendor = device.vendor.lower()
    for keyword, expected_vendors in _VENDOR_HINTS.items():
        if keyword in hostname and not any(ev in vendor for ev in expected_vendors):
            return (
                f"Hostname '{device.hostname}' suggests {expected_vendors[0].title()} hardware, "
                f"but the MAC vendor lookup says '{device.vendor}'."
            )
    return None


async def check_off_hours(
    session: AsyncSession, mac: str, hour: int, min_history: int
) -> bool:
    total = await session.scalar(
        select(func.sum(DeviceHourProfile.count)).where(DeviceHourProfile.mac == mac)
    )
    if not total or total < min_history:
        return False

    this_hour = await session.scalar(
        select(DeviceHourProfile.count).where(DeviceHourProfile.mac == mac, DeviceHourProfile.hour == hour)
    )
    # this_hour includes the sighting that was just recorded for the current
    # poll, so "never seen at this hour before" is count == 1.
    return (this_hour or 0) <= 1


async def check_flapping(session: AsyncSession, mac: str, window_minutes: int, threshold: int) -> bool:
    since = utcnow() - dt.timedelta(minutes=window_minutes)
    count = await session.scalar(
        select(func.count(DeviceSighting.id)).where(
            DeviceSighting.mac == mac,
            DeviceSighting.event_type.in_(["connect", "disconnect"]),
            DeviceSighting.timestamp >= since,
        )
    )
    return (count or 0) >= threshold


async def check_new_device_spike(session: AsyncSession, window_minutes: int, threshold: int) -> int:
    since = utcnow() - dt.timedelta(minutes=window_minutes)
    count = await session.scalar(select(func.count(Device.id)).where(Device.first_seen >= since))
    return count or 0


async def run_heuristics(
    session: AsyncSession,
    config: HeuristicsConfig,
    reconcile_result: ReconcileResult,
) -> list[SuspiciousFinding]:
    if not config.enabled:
        return []

    findings: list[SuspiciousFinding] = []
    now_hour = utcnow().hour

    # 6.2.1 -- new network for a known device.
    for device, old_network, new_network in reconcile_result.network_changes:
        if await find_new_network_for_known_device(session, device.mac, new_network):
            findings.append(
                SuspiciousFinding(
                    heuristic="new_network",
                    target=device.mac,
                    message=(
                        f"{device.display_name()} appeared on '{new_network}' -- a network it has "
                        f"never used before (previously seen on '{old_network}')."
                    ),
                )
            )

    # Devices worth checking for vendor-mismatch / off-hours: anything that
    # connected or roamed this cycle.
    seen_macs: dict[str, Device] = {}
    for d in reconcile_result.new_devices + reconcile_result.newly_online:
        seen_macs[d.mac] = d
    for d, _, _ in reconcile_result.network_changes:
        seen_macs[d.mac] = d

    for device in seen_macs.values():
        # 6.2.2 -- vendor/hostname mismatch.
        mismatch_msg = check_vendor_mismatch(device)
        if mismatch_msg:
            findings.append(SuspiciousFinding(heuristic="vendor_mismatch", target=device.mac, message=mismatch_msg))

        # 6.2.3 -- off-hours activity.
        if await check_off_hours(session, device.mac, now_hour, config.off_hours_min_history_sightings):
            findings.append(
                SuspiciousFinding(
                    heuristic="off_hours",
                    target=device.mac,
                    message=f"{device.display_name()} was active at an hour it has never been active at before.",
                )
            )

        # 6.2.4 -- flapping.
        if await check_flapping(session, device.mac, config.flapping_window_minutes, config.flapping_threshold):
            findings.append(
                SuspiciousFinding(
                    heuristic="flapping",
                    target=device.mac,
                    message=(
                        f"{device.display_name()} has connected/disconnected "
                        f"{config.flapping_threshold}+ times in the last "
                        f"{config.flapping_window_minutes} minutes."
                    ),
                )
            )

    # 6.2.5 -- spike in new-device count.
    spike_count = await check_new_device_spike(
        session, config.new_device_spike_window_minutes, config.new_device_spike_threshold
    )
    if spike_count >= config.new_device_spike_threshold:
        findings.append(
            SuspiciousFinding(
                heuristic="new_device_spike",
                target=None,
                message=(
                    f"{spike_count} new devices joined the network in the last "
                    f"{config.new_device_spike_window_minutes} minutes."
                ),
            )
        )

    return findings
