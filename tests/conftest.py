from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest_asyncio

from netmon.config import (
    AlertsConfig,
    DashboardConfig,
    DatabaseConfig,
    HeuristicsConfig,
    NetworkConfig,
    Settings,
    UnifiConfig,
)
from netmon.db import create_all_tables, dispose_engine, init_engine, session_scope


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        networks=[
            NetworkConfig(name="Wired Adult", vlan_id=10, role="management"),
            NetworkConfig(name="Adults Wi-Fi", vlan_id=20, role="trusted"),
            NetworkConfig(name="IoT", vlan_id=40, role="restricted"),
            NetworkConfig(name="Guests", vlan_id=50, role="untrusted"),
        ],
        unifi=UnifiConfig(host="10.0.0.1", username="ro", password="secret"),
        alerts=AlertsConfig(new_device_days=7, infra_offline_minutes=20, heuristics=HeuristicsConfig()),
        website_monitors=[],
        database=DatabaseConfig(path=str(tmp_path / "test.db")),
        dashboard=DashboardConfig(session_secret="x" * 32),
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture
async def db_session(tmp_path):
    settings = make_settings(tmp_path)
    init_engine(settings)
    await create_all_tables()
    async with session_scope() as session:
        yield session
    await dispose_engine()
