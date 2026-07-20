from __future__ import annotations

import os

import pytest

from netmon.config import ConfigError, load_config


CONFIG_YAML = """
networks:
  - name: "Wired Adult"
    vlan_id: 10
    role: management
unifi:
  host: "10.0.0.1"
  username: "ro"
  password: "${UNIFI_PASSWORD}"
alerts:
  discord_webhook_url: "${DISCORD_WEBHOOK_URL}"
dashboard:
  session_secret: "${SESSION_SECRET}"
"""


def _write(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text)
    return path


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does-not-exist.yml")


def test_invalid_yaml_raises(tmp_path):
    path = _write(tmp_path, "networks: [this is not: valid: yaml")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_missing_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("UNIFI_PASSWORD", raising=False)
    path = _write(tmp_path, CONFIG_YAML)
    with pytest.raises(ConfigError, match="UNIFI_PASSWORD"):
        load_config(path)


def test_env_var_substitution_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_PASSWORD", "hunter2")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    path = _write(tmp_path, CONFIG_YAML)

    settings = load_config(path)

    assert settings.unifi.password == "hunter2"
    assert settings.alerts.discord_webhook_url == "https://discord.example/webhook"
    assert settings.dashboard.session_secret == "x" * 32
    assert settings.networks[0].vlan_id == 10


def test_duplicate_vlan_id_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_PASSWORD", "hunter2")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    duplicated = CONFIG_YAML.replace(
        'networks:\n  - name: "Wired Adult"\n    vlan_id: 10\n    role: management\n',
        'networks:\n  - name: "Wired Adult"\n    vlan_id: 10\n    role: management\n'
        '  - name: "Other"\n    vlan_id: 10\n    role: trusted\n',
    )
    path = _write(tmp_path, duplicated)
    with pytest.raises(ConfigError, match="unique"):
        load_config(path)


def test_website_monitor_requires_http_scheme(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIFI_PASSWORD", "hunter2")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    bad = CONFIG_YAML + "\nwebsite_monitors:\n  - name: bad\n    url: \"ftp://example.com\"\n"
    path = _write(tmp_path, bad)
    with pytest.raises(ConfigError):
        load_config(path)
