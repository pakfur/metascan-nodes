from __future__ import annotations

import json
from pathlib import Path

import pytest

from mscan_client.config import ClientConfig, resolve_config


def test_defaults_when_nothing_set(monkeypatch, tmp_path):
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", tmp_path / "missing.json")

    cfg = resolve_config(settings_override=None)

    assert cfg == ClientConfig(url="http://localhost:8700", api_key=None)


def test_env_vars_override_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("METASCAN_URL", "http://envhost:9000")
    monkeypatch.setenv("METASCAN_API_KEY", "env-key")
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", tmp_path / "missing.json")

    cfg = resolve_config(settings_override=None)

    assert cfg == ClientConfig(url="http://envhost:9000", api_key="env-key")


def test_file_overrides_defaults_but_env_wins(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "url": "http://filehost:7000", "api_key": "file-key",
    }))
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", cfg_file)
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)

    cfg = resolve_config(settings_override=None)
    assert cfg.url == "http://filehost:7000"
    assert cfg.api_key == "file-key"

    monkeypatch.setenv("METASCAN_URL", "http://envhost:9000")
    cfg2 = resolve_config(settings_override=None)
    assert cfg2.url == "http://envhost:9000"
    assert cfg2.api_key == "file-key"  # env didn't set the key, file value stands


def test_settings_override_wins_over_everything(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"url": "http://filehost", "api_key": "filek"}))
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", cfg_file)
    monkeypatch.setenv("METASCAN_URL", "http://envhost")
    monkeypatch.setenv("METASCAN_API_KEY", "envk")

    cfg = resolve_config(
        settings_override=ClientConfig(url="http://overhost", api_key="overk")
    )
    assert cfg == ClientConfig(url="http://overhost", api_key="overk")


def test_empty_string_settings_override_treated_as_unset(monkeypatch, tmp_path):
    """The MetascanSettings node passes empty strings for unset fields;
    those must not blank out env / file / default values."""
    monkeypatch.setenv("METASCAN_URL", "http://envhost")
    monkeypatch.setenv("METASCAN_API_KEY", "envk")
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", tmp_path / "missing.json")

    cfg = resolve_config(settings_override=ClientConfig(url="", api_key=""))
    assert cfg == ClientConfig(url="http://envhost", api_key="envk")


def test_corrupt_json_falls_back(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{ this is not valid json")
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", cfg_file)
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)

    cfg = resolve_config(settings_override=None)
    assert cfg == ClientConfig(url="http://localhost:8700", api_key=None)


def test_empty_object_in_file_falls_back(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}")
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", cfg_file)
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)

    cfg = resolve_config(settings_override=None)
    assert cfg == ClientConfig(url="http://localhost:8700", api_key=None)


def test_non_string_value_in_file_treated_as_unset(monkeypatch, tmp_path):
    """A config file with `{"url": 8700}` must NOT propagate the int —
    httpx would fail confusingly downstream. Fall back to env/default."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"url": 8700, "api_key": None}))
    monkeypatch.setattr("mscan_client.config._CONFIG_FILE", cfg_file)
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)

    cfg = resolve_config(settings_override=None)
    assert cfg == ClientConfig(url="http://localhost:8700", api_key=None)
