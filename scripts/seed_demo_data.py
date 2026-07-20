"""Populate the database with fake devices/infrastructure/alerts/uptime data.

Not part of the plan's phases -- added so someone forking this project can
see the dashboard actually working before they've pointed it at a real
UDM-Pro. Safe to run repeatedly; it only adds rows, and 'python scripts/
reset_db.py' (delete data/netmon.db and re-run migrations) clears them.

Usage:
    python scripts/seed_demo_data.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netmon.config import ConfigError, load_config  # noqa: E402
from netmon.db import create_all_tables, init_engine, session_scope  # noqa: E402
from netmon.models import Alert, Device, DeviceSighting, InfraDevice, UptimeCheck, UptimeIncident, utcnow  # noqa: E402

DEMO_DEVICES = [
    ("aa:bb:cc:00:00:01", "dads-macbook-pro", "Apple, Inc.", True, True),
    ("aa:bb:cc:00:00:02", "kids-ipad", "Apple, Inc.", True, False),
    ("aa:bb:cc:00:00:03", "nest-thermostat", "Google Inc.", True, True),
    ("aa:bb:cc:00:00:04", "guest-phone-42", "Samsung Electronics", True, True),
    ("aa:bb:cc:00:00:05", "proxmox-node1", "Dell Inc.", False, True),
    ("aa:bb:cc:00:00:06", "old-roomba", "iRobot Corporation", False, False),
]

DEMO_INFRA = [
    ("uap-lite-1", "UAP-Lite Living Room", "ap", "Adults Wi-Fi", True),
    ("uap-lite-2", "UAP-Lite Upstairs", "ap", "Adults Wi-Fi", False),
    ("usw-8-1", "Switch-Rack-1", "switch", "Wired Adult", True),
    ("usw-8-2", "Switch-Rack-2", "switch", "Wired Adult", True),
]


async def main() -> None:
    config_path = os.environ.get("NETMON_CONFIG", "config.yml")
    try:
        settings = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    init_engine(settings)
    await create_all_tables()

    networks = [n.name for n in settings.networks] or ["Adults Wi-Fi", "Wired Adult"]
    now = utcnow()

    async with session_scope() as session:
        for i, (mac, hostname, vendor, is_wired, online) in enumerate(DEMO_DEVICES):
            network = networks[i % len(networks)]
            first_seen = now - dt.timedelta(days=random.choice([1, 3, 10, 60]))
            session.add(
                Device(
                    mac=mac,
                    hostname=hostname,
                    vendor=vendor,
                    network=network,
                    is_wired=is_wired,
                    is_online=online,
                    first_seen=first_seen,
                    last_seen=now if online else now - dt.timedelta(hours=random.randint(1, 48)),
                )
            )
            session.add(DeviceSighting(mac=mac, network=network, event_type="connect", timestamp=first_seen))

        for unifi_id, name, kind, network, online in DEMO_INFRA:
            session.add(
                InfraDevice(
                    unifi_id=unifi_id,
                    name=name,
                    kind=kind,
                    network=network,
                    is_online=online,
                    last_seen=now if online else now - dt.timedelta(minutes=45),
                    offline_since=None if online else now - dt.timedelta(minutes=45),
                    offline_alerted=not online,
                )
            )

        session.add(
            Alert(
                type="new_device",
                severity="info",
                message="guest-phone-42 (aa:bb:cc:00:00:04) joined 'Guests'.",
                target="aa:bb:cc:00:00:04",
                created_at=now - dt.timedelta(minutes=20),
            )
        )
        session.add(
            Alert(
                type="infra_offline",
                severity="warning",
                message="'UAP-Lite Upstairs' (ap) on 'Adults Wi-Fi' has been offline for 45+ minutes.",
                target="uap-lite-2",
                created_at=now - dt.timedelta(minutes=25),
            )
        )
        session.add(
            Alert(
                type="suspicious",
                severity="warning",
                message="[new_network] old-roomba appeared on 'Wired Adult' -- a network it has never used before.",
                target="aa:bb:cc:00:00:06",
                created_at=now - dt.timedelta(minutes=5),
            )
        )

        for monitor in settings.website_monitors or []:
            for i in range(60):
                ts = now - dt.timedelta(minutes=(60 - i))
                status = "down" if 40 <= i <= 42 else "up"
                session.add(
                    UptimeCheck(
                        target_name=monitor.name,
                        timestamp=ts,
                        status=status,
                        status_code=200 if status == "up" else None,
                        response_ms=random.uniform(80, 220) if status == "up" else None,
                        error=None if status == "up" else "Connection timed out",
                    )
                )
            session.add(
                UptimeIncident(
                    target_name=monitor.name,
                    started_at=now - dt.timedelta(minutes=20),
                    resolved_at=now - dt.timedelta(minutes=18),
                )
            )

    print("Seeded demo data. Start the app and log in to look around.")


if __name__ == "__main__":
    asyncio.run(main())
