"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("vendor", sa.String(255), nullable=True),
        sa.Column("custom_name", sa.String(100), nullable=True),
        sa.Column("network", sa.String(64), nullable=False),
        sa.Column("is_wired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_devices_mac", "devices", ["mac"], unique=True)
    op.create_index("ix_devices_network", "devices", ["network"])
    op.create_index("ix_devices_is_online", "devices", ["is_online"])
    op.create_index("ix_devices_last_seen", "devices", ["last_seen"])

    op.create_table(
        "device_sightings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("network", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sightings_mac", "device_sightings", ["mac"])
    op.create_index("ix_sightings_network", "device_sightings", ["network"])
    op.create_index("ix_sightings_timestamp", "device_sightings", ["timestamp"])
    op.create_index("ix_sightings_mac_timestamp", "device_sightings", ["mac", "timestamp"])

    op.create_table(
        "device_hour_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("mac", "hour", name="uq_device_hour"),
    )
    op.create_index("ix_device_hour_profile_mac", "device_hour_profile", ["mac"])

    op.create_table(
        "infra_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unifi_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("network", sa.String(64), nullable=False),
        sa.Column("mac", sa.String(17), nullable=True),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("offline_since", sa.DateTime(), nullable=True),
        sa.Column("offline_alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_infra_devices_unifi_id", "infra_devices", ["unifi_id"], unique=True)

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("target", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_alerts_type", "alerts", ["type"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_index("ix_alerts_acknowledged", "alerts", ["acknowledged"])

    op.create_table(
        "uptime_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_name", sa.String(128), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.String(255), nullable=True),
    )
    op.create_index("ix_uptime_checks_target_name", "uptime_checks", ["target_name"])
    op.create_index("ix_uptime_checks_timestamp", "uptime_checks", ["timestamp"])

    op.create_table(
        "uptime_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_name", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_uptime_incidents_target_name", "uptime_incidents", ["target_name"])


def downgrade() -> None:
    op.drop_table("uptime_incidents")
    op.drop_table("uptime_checks")
    op.drop_table("alerts")
    op.drop_table("infra_devices")
    op.drop_table("device_hour_profile")
    op.drop_table("device_sightings")
    op.drop_table("devices")
    op.drop_table("users")
