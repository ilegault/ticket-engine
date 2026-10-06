"""Tests for docs/box-setup.md (ticket 32): the runbook cannot drift from the code.

WHY THIS EXISTS
---------------
Ticket 32 requires the box setup runbook to name the same config keys
`LocalWorkerConfig`/`load_local_config` actually read, and the same console
script `pyproject.toml` actually exposes, so the doc cannot silently rot as
those change.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import tomllib

from ticket_engine.local_config import (
    LocalWorkerConfig,
    load_box_config,
)

DOC_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs" / "box-setup.md"
PYPROJECT_PATH = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"

REQUIRED_HEADINGS = [
    "The agent account",
    "Python and the engine checkout",
    "agy and Claude logins",
    "GitHub token",
    "Local config",
    "Start at boot",
    "Checking it works",
]


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, flags=re.MULTILINE)


def _toml_block(text: str) -> str:
    match = re.search(r"```toml\n(.*?)```", text, flags=re.DOTALL)
    assert match is not None, "expected one fenced toml block in docs/box-setup.md"
    return match.group(1)


def test_sections_in_order():
    headings = _headings(_doc_text())
    assert headings == REQUIRED_HEADINGS


def test_config_example_is_real(tmp_path):
    toml_text = _toml_block(_doc_text())
    config_path = tmp_path / "local-config.toml"
    config_path.write_text(toml_text, encoding="utf-8")

    data = tomllib.loads(toml_text)
    flat_keys = set(data.keys()) | set(data.get("agy", {}).keys())
    expected_fields = {
        f.name for f in dataclasses.fields(LocalWorkerConfig)
    } - {"repos", "github_token"}
    assert expected_fields <= flat_keys

    config = load_box_config(config_path)
    assert isinstance(config, LocalWorkerConfig)


def test_no_secrets_no_names():
    text = _doc_text()
    assert re.search(r"gh[pousr]_[A-Za-z0-9]{10,}", text) is None
    assert "github_pat_" not in text


def test_commands_are_the_real_ones():
    text = _doc_text()
    assert "box-worker" in text
    assert "--once" in text

    with PYPROJECT_PATH.open("rb") as fh:
        pyproject = tomllib.load(fh)
    assert "box-worker" in pyproject["project"]["scripts"]
