"""Ticket 32: the box setup runbook matches the code as it stands after ticket 31.

WHY THIS EXISTS
---------------
`docs/box-setup.md` is a runbook the developer follows to rebuild the box from
a clean Windows install. A runbook that drifts from `LocalWorkerConfig`'s real
fields, or from the actual console-script name and flags, would silently send
the developer down the wrong path. This test reads the doc as plain text,
extracts its one `toml` config example, and checks it against the real
`LocalWorkerConfig`/`load_local_config` and `pyproject.toml` — so the doc
cannot drift without a test going red.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import tomllib

from ticket_engine.local_config import LocalWorkerConfig, load_local_config

_ROOT = pathlib.Path(__file__).parent.parent
_DOC_PATH = _ROOT / "docs" / "box-setup.md"
_DOC = _DOC_PATH.read_text(encoding="utf-8")

_EXPECTED_HEADINGS = [
    "The agent account",
    "Python and the engine checkout",
    "agy login",
    "GitHub token",
    "Local config",
    "Start at boot",
    "Checking it works",
]


def test_doc_exists_and_is_not_a_held_path():
    assert _DOC_PATH.is_file()
    # A held path (AGENTS.md §7) would always block auto-merge; this doc must not be one.
    held_prefixes = (".github/", "docs/adr/")
    held_names = {"AGENTS.md", "CONTEXT.md"}
    rel = str(_DOC_PATH.relative_to(_ROOT)).replace("\\", "/")
    assert not rel.startswith(held_prefixes)
    assert rel not in held_names


def test_headings_appear_in_order():
    positions = []
    for heading in _EXPECTED_HEADINGS:
        match = re.search(rf"^## {re.escape(heading)}$", _DOC, re.MULTILINE)
        assert match, f"Missing heading: {heading!r}"
        positions.append(match.start())
    assert positions == sorted(positions), "Headings are out of order"


def _extract_toml_block(text: str) -> str:
    match = re.search(r"```toml\n(.*?)```", text, re.DOTALL)
    assert match, "No fenced toml block found in the Local config section"
    return match.group(1)


def test_config_example_covers_every_local_worker_config_field(tmp_path):
    local_config_section = _DOC.split("## Local config", 1)[1].split("## Start at boot", 1)[0]
    toml_text = _extract_toml_block(local_config_section)

    config_path = tmp_path / "local.toml"
    config_path.write_text(toml_text, encoding="utf-8")

    data = tomllib.load(config_path.open("rb"))

    excluded = {"repos", "github_token"}
    nested_under_agy = {"print_timeout", "quota_error_patterns", "auth_error_patterns"}
    for f in dataclasses.fields(LocalWorkerConfig):
        if f.name in excluded:
            continue
        if f.name in nested_under_agy:
            assert f.name in data.get("agy", {}), f"Missing [agy].{f.name} in example config"
        else:
            assert f.name in data, f"Missing top-level key {f.name!r} in example config"

    loaded = load_local_config(config_path)
    assert len(loaded.repos) >= 1


def test_doc_contains_no_secrets_and_says_the_developer_not_a_name():
    assert not re.search(r"gh[pousr]_[A-Za-z0-9]{10,}", _DOC)
    assert "github_pat_" not in _DOC
    assert "the developer" in _DOC


def test_doc_names_the_real_console_script_and_flag():
    assert "box-worker" in _DOC
    assert "--once" in _DOC

    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "box-worker" in pyproject["project"]["scripts"]
