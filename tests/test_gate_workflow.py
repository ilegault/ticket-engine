"""Tests for the shared gate workflow (ticket 83, ADR 0011 rule 4).

The workflow file is read as text, the way caller workflows are read in
test_bootstrap_adopt.py; nothing is faked.
"""
from __future__ import annotations

import pathlib
import re

WORKFLOW = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "gate.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_gate_workflow_is_reusable_with_one_gate_job() -> None:
    text = _text()
    assert "workflow_call:" in text
    assert re.search(r"^\s+name: gate\s*$", text, re.MULTILINE)
    jobs_block = text.split("\njobs:\n", 1)[1]
    job_keys = re.findall(r"^  (\w[\w-]*):\s*$", jobs_block, re.MULTILINE)
    assert len(job_keys) == 1


def test_gate_workflow_reads_python_version_and_install_from_the_repo_config() -> None:
    text = _text()
    for needle in (
        ".ticket-engine.toml",
        "tomllib",
        "python_version",
        "install",
        "actions/setup-python@v5",
    ):
        assert needle in text


def test_gate_workflow_installs_the_engine_and_runs_engine_gate() -> None:
    text = _text()
    assert (
        'pip install "ticket-engine @ git+https://github.com/ilegault/ticket-engine.git@v1"'
        in text
    )
    assert re.search(r"^\s+run: engine-gate\s*$", text, re.MULTILINE)
