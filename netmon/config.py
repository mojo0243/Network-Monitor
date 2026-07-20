"""Loads and validates config.yml.

Every other module reads settings through the `Settings` object returned by
`load_config()` -- nothing in the app should read os.environ or YAML directly
outside this module, so there is exactly one place that knows how config is
sourced.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised for anything wrong with config.yml -- bad YAML, missing env
    vars, invalid values. Always caught at startup and reported with a
    plain-English message; never meant to propagate into request handling.
    """


class NetworkConfig(BaseModel):
    name: str
    vlan_id: int
    role: Literal["management", "trusted", "restricted", "untrusted", "infrastructure", "lab", "dmz"]


class UnifiConfig(BaseModel):
    host: str
    username: str
    password: str
    site: str = "default"
    verify_ssl: bool = False
    poll_interval_seconds: int = Field(default=30, ge=5)


class HeuristicsConfig(BaseModel):
    enabled: bool = True
    flapping_window_minutes: int = Field(default=10, ge=1)
    flapping_threshold: int = Field(default=6, ge=2)
    new_device_spike_window_minutes: int = Field(default=15, ge=1)
    new_device_spike_threshold: int = Field(default=5, ge=2)
    off_hours_min_history_sightings: int = Field(default=50, ge=1)


class AlertsConfig(BaseModel):
    new_device_days: int = Field(default=7, ge=1)
    infra_offline_minutes: int = Field(default=20, ge=1)
    discord_webhook_url: str | None = None
    heuristics: HeuristicsConfig = HeuristicsConfig()


class WebsiteMonitorConfig(BaseModel):
    name: str
    url: str
    interval_seconds: int = Field(default=60, ge=10)
    timeout_seconds: int = Field(default=10, ge=1)
    failure_threshold: int = Field(default=2, ge=1)

    @field_validator("url")
    @classmethod
    def _must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"website_monitors url must start with http:// or https:// (got {v!r})")
        return v


class DatabaseConfig(BaseModel):
    path: str = "data/netmon.db"


class DashboardConfig(BaseModel):
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8080, ge=1, le=65535)
    session_secret: str
    session_max_age_hours: int = Field(default=12, ge=1)

    @field_validator("session_secret")
    @classmethod
    def _secret_not_empty(cls, v: str) -> str:
        if not v or len(v) < 16:
            raise ValueError(
                "dashboard.session_secret must be set and at least 16 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "data/netmon.log"


class Settings(BaseModel):
    networks: list[NetworkConfig]
    unifi: UnifiConfig
    alerts: AlertsConfig
    website_monitors: list[WebsiteMonitorConfig] = []
    database: DatabaseConfig = DatabaseConfig()
    dashboard: DashboardConfig
    logging: LoggingConfig = LoggingConfig()

    @field_validator("networks")
    @classmethod
    def _networks_not_empty(cls, v: list[NetworkConfig]) -> list[NetworkConfig]:
        if not v:
            raise ValueError("config.yml must define at least one network")
        vlan_ids = [n.vlan_id for n in v]
        if len(vlan_ids) != len(set(vlan_ids)):
            raise ValueError("networks: vlan_id values must be unique")
        return v

    def network_by_vlan(self, vlan_id: int | None) -> NetworkConfig | None:
        for net in self.networks:
            if net.vlan_id == vlan_id:
                return net
        return None


def _substitute_env_vars(value):
    """Recursively substitutes ${VAR} placeholders in string *values* only --
    run after YAML parsing, not on the raw file text, so a comment that
    happens to mention "${SOMETHING}" (like this file's own header) can
    never be mistaken for a real placeholder.
    """

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ConfigError(
                f"config.yml references ${{{var_name}}} but that environment "
                f"variable is not set. Export it before starting the app, e.g.\n"
                f"  export {var_name}=... "
            )
        return env_value

    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env_vars(v) for v in value]
    return value


def load_config(path: str | Path = "config.yml") -> Settings:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"{config_path} not found. Copy config.example.yml to {config_path} "
            f"and fill in your own values."
        )

    try:
        raw_data = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping at the top level")

    data = _substitute_env_vars(raw_data)

    try:
        return Settings(**data)
    except Exception as exc:  # pydantic.ValidationError, mainly
        raise ConfigError(f"{config_path} failed validation:\n{exc}") from exc
