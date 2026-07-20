"""SQLAlchemy ORM models. Schema changes go through an Alembic migration
(see migrations/versions/) -- do not rely on create_all() in production,
it exists only to make tests fast.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    """Naive UTC on purpose: SQLite has no real timezone-aware storage, so a
    stored datetime comes back naive once read in a fresh session regardless
    of the column type. Mixing naive (from the DB) and aware (from
    datetime.now(timezone.utc)) blows up on comparison -- every timestamp in
    this app is naive UTC, always produced through this one function, so
    that mismatch can't happen.
    """
    return dt.datetime.utcnow()


class Base(DeclarativeBase):
    pass


class User(Base):
    """Dashboard login. Created via scripts/create_admin.py, not the API --
    there is deliberately no self-service signup for a single-household tool.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)


class Device(Base):
    """One row per MAC address ever seen. `custom_name` is the user-assigned
    nickname (Section 10.7 of the plan) -- purely cosmetic, every other table
    still keys off `mac`.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), unique=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custom_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    network: Mapped[str] = mapped_column(String(64), index=True)
    is_wired: Mapped[bool] = mapped_column(Boolean, default=False)
    is_online: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, index=True)

    def display_name(self) -> str:
        return self.custom_name or self.hostname or self.mac

    def is_new(self, new_device_days: int) -> bool:
        age = utcnow() - self.first_seen
        return age <= dt.timedelta(days=new_device_days)


class DeviceSighting(Base):
    """Append-only history of connect/disconnect/roam events per device.
    Source of truth for "has this device ever used this network before"
    (heuristic 6.2.1) and for the flapping heuristic (6.2.4).
    """

    __tablename__ = "device_sightings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), index=True)
    network: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(16))  # connect | disconnect | roam
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, index=True)

    __table_args__ = (Index("ix_sightings_mac_timestamp", "mac", "timestamp"),)


class DeviceHourProfile(Base):
    """Rolling per-device, per-hour-of-day sighting counts. Baseline for the
    off-hours-activity heuristic (6.2.3) -- updated once per sighting, so the
    heuristic pass itself is a cheap lookup rather than scanning history.
    """

    __tablename__ = "device_hour_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), index=True)
    hour: Mapped[int] = mapped_column(Integer)  # 0-23, local time
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("mac", "hour", name="uq_device_hour"),)


class InfraDevice(Base):
    """Switches and APs -- tracked separately from client Devices because
    they get their own offline-duration alert (Phase 5), not the
    new-device / suspicious-activity treatment client devices get.
    """

    __tablename__ = "infra_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unifi_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # switch | ap | gateway
    network: Mapped[str] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    offline_since: Mapped[dt.datetime | None] = mapped_column(DateTime(), nullable=True)
    offline_alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    """Every alert the app has ever raised, regardless of whether it also
    went to Discord. `target` is a free-form reference (mac, infra unifi_id,
    or monitor name) so the UI can deep-link back to the source.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)  # new_device | infra_offline | suspicious | website_down | website_recovered
    severity: Mapped[str] = mapped_column(String(16))  # info | warning | critical
    message: Mapped[str] = mapped_column(String(500))
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(), nullable=True)


class UptimeCheck(Base):
    """One row per health-check attempt. Kept for the response-time history
    chart; UptimeIncident (below) is what downtime alerting actually reads.
    """

    __tablename__ = "uptime_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_name: Mapped[str] = mapped_column(String(128), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(8))  # up | down
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)


class UptimeIncident(Base):
    """One row per downtime episode. Opened after failure_threshold
    consecutive failed checks, closed on the next successful check.
    """

    __tablename__ = "uptime_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_name: Mapped[str] = mapped_column(String(128), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(), nullable=True)

    def duration_seconds(self) -> float | None:
        if self.resolved_at is None:
            return None
        return (self.resolved_at - self.started_at).total_seconds()
