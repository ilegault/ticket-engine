"""Tests for RepoConfig install keys and defaults (ticket 54).

WHY THIS EXISTS
---------------
Ticket 54 adds python_version, install, and jules_enabled to RepoConfig so target
repos declare how they install and whether Jules may work them (ADR 0010 rule 1).
"""
from __future__ import annotations

import pathlib
import tomllib

from ticket_engine.bootstrap import ENGINE_CONFIG_TEMPLATE
from ticket_engine.config import RepoConfig, load_repo_config


def test_repo_config_install_keys_default(tmp_path: pathlib.Path):
    cfg = load_repo_config(tmp_path / "nonexistent")
    assert cfg.python_version == "3.12"
    assert cfg.install == "pip install -e .[dev]"
    assert cfg.jules_enabled is True

    direct_cfg = RepoConfig()
    assert direct_cfg.python_version == "3.12"
    assert direct_cfg.install == "pip install -e .[dev]"
    assert direct_cfg.jules_enabled is True


def test_repo_config_reads_install_keys_from_toml(tmp_path: pathlib.Path):
    config_file = tmp_path / ".ticket-engine.toml"
    config_file.write_text(
        'python_version = "3.11"\n'
        'install = "pip install -r requirements.txt"\n'
        "jules_enabled = false\n",
        encoding="utf-8",
    )
    cfg = load_repo_config(tmp_path)
    assert cfg.python_version == "3.11"
    assert cfg.install == "pip install -r requirements.txt"
    assert cfg.jules_enabled is False


def test_engine_config_template_declares_install_keys():
    data = tomllib.loads(ENGINE_CONFIG_TEMPLATE)
    assert data["python_version"] == "3.12"
    assert data["install"] == "pip install -e .[dev]"
    assert data["jules_enabled"] is True


def test_repo_config_ignores_retired_box_enabled(tmp_path: pathlib.Path):
    config_file = tmp_path / ".ticket-engine.toml"
    config_file.write_text("box_enabled = true\n", encoding="utf-8")
    cfg = load_repo_config(tmp_path)
    assert not hasattr(cfg, "box_enabled")

