"""Unit tests for config.py."""
import os
from pathlib import Path
import pytest
import yaml

from config import ConfigError, Settings, _validate, load_settings


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    with open(p, "w") as f:
        yaml.dump(data, f)
    return p


def test_defaults_load(tmp_path):
    cfg = _write_config(tmp_path, {})
    s = load_settings(cfg)
    assert s.ollama_model == "llama3"
    assert s.web_port == 8080
    assert s.poll_interval_sec == 5
    assert s.output_path.exists()


def test_custom_values_load(tmp_path):
    cfg = _write_config(tmp_path, {"ollama_model": "mistral", "web_port": 9090})
    s = load_settings(cfg)
    assert s.ollama_model == "mistral"
    assert s.web_port == 9090


def test_env_override(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, {})
    monkeypatch.setenv("PODCAST_OLLAMA_MODEL", "phi3")
    s = load_settings(cfg)
    assert s.ollama_model == "phi3"


def test_invalid_port(tmp_path):
    cfg = _write_config(tmp_path, {"web_port": 80})
    with pytest.raises(ConfigError, match="web_port"):
        load_settings(cfg)


def test_invalid_ollama_url(tmp_path):
    cfg = _write_config(tmp_path, {"ollama_url": "not-a-url"})
    with pytest.raises(ConfigError, match="ollama_url"):
        load_settings(cfg)


def test_invalid_poll_interval(tmp_path):
    cfg = _write_config(tmp_path, {"poll_interval_sec": 0})
    with pytest.raises(ConfigError, match="poll_interval_sec"):
        load_settings(cfg)


def test_output_dir_created(tmp_path):
    out = tmp_path / "new_output"
    cfg = _write_config(tmp_path, {"output_dir": str(out)})
    s = load_settings(cfg)
    assert out.exists()
    assert s.output_path == out


def test_missing_config_uses_defaults(tmp_path):
    missing = tmp_path / "nonexistent.yaml"
    # Should not raise; uses defaults
    s = load_settings(missing)
    assert s.ollama_model == "llama3"


def test_max_input_chars():
    s = Settings()
    s.max_input_tokens = 100
    assert s.max_input_chars == 400
