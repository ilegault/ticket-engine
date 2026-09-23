"""Tests for integrity gate Check 7: new tests must fail on base code.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Integrity checks) and ADR 0001 §2.7
establish Check 7: a PR's new tests must fail when run against the base branch's
code. A test that already passes before the feature exists is not testing the
feature. A refactor ticket may add characterisation tests that already pass;
these trigger a hold (ADR 0001 §2.7, Spec User Story 34, Ticket 05 AC 3), never
a fail.
Check 7 runs the new tests against the base branch's source in the adapter layer.
"""
from __future__ import annotations

import io
import pathlib
import subprocess
from unittest.mock import patch

from ticket_engine.integrity_runner import run_integrity_gate


def _init_git_repo(repo_dir: pathlib.Path) -> None:
    _sub = dict(check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "init", "-b", "master"], cwd=repo_dir, **_sub)
    subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=repo_dir, **_sub)
    subprocess.run(["git", "config", "user.email", "agent@example.com"], cwd=repo_dir, **_sub)


def test_tiny_fixture_project_new_test_fails_on_base_passes(tmp_path: pathlib.Path):
    """Scenario 1: PR adds a new feature and a new test for it.
    The new test fails on base (as the feature does not exist yet).
    Verdict: pass, runtime reported.
    """
    _init_git_repo(tmp_path)

    # Base commit on master
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from calc import add\n\n"
        "def test_existing_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = ['src']\n", encoding="utf-8"
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base commit"], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    # PR branch: add multiply and test_multiply
    subprocess.run(["git", "checkout", "-b", "ticket/feat-mult"], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    (src_dir / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from calc import add, multiply\n\n"
        "def test_existing_add():\n"
        "    assert add(1, 2) == 3\n\n"
        "def test_multiply():\n"
        "    assert multiply(2, 3) == 6\n",
        encoding="utf-8",
    )

    ticket_dir = tmp_path / ".scratch" / "phase-1" / "issues"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "05-new-feature.md").write_text(
        "# 05: New feature\n"
        "**Status:** done\n"
        "## Acceptance criteria\n"
        "- [x] Multiply works\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "implement multiply"], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    # Capture stdout to assert gate output
    captured_out = io.StringIO()
    with patch("sys.stdout", captured_out):
        exit_code = run_integrity_gate(
            repo_path=tmp_path,
            base_ref="master",
        )

    output = captured_out.getvalue()
    assert exit_code == 0
    assert "## Integrity Gate Verdict: PASS" in output
    assert "`pass`" in output
    assert "Check 7 pass" in output
    assert "failed on base code" in output
    assert "runtime" in output.lower()


def test_tiny_fixture_project_new_test_passes_on_base_holds(tmp_path: pathlib.Path):
    """Scenario 2: PR adds a characterisation test for existing behavior.
    The new test passes on base.
    Verdict: hold (listing the test), runtime reported.
    """
    _init_git_repo(tmp_path)

    # Base commit on master
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from calc import add\n\n"
        "def test_existing_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = ['src']\n", encoding="utf-8"
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base commit"], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    # PR branch: add characterisation test for add without changing source
    subprocess.run(["git", "checkout", "-b", "ticket/refactor-char"], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    (tests_dir / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from calc import add\n\n"
        "def test_existing_add():\n"
        "    assert add(1, 2) == 3\n\n"
        "def test_add_characterisation():\n"
        "    assert add(0, 0) == 0\n",
        encoding="utf-8",
    )

    ticket_dir = tmp_path / ".scratch" / "phase-1" / "issues"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "05-char-test.md").write_text(
        "# 05: Characterisation test\n"
        "**Status:** done\n"
        "## Acceptance criteria\n"
        "- [x] Characterisation test added\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "add characterisation test"], cwd=tmp_path, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    captured_out = io.StringIO()
    with patch("sys.stdout", captured_out):
        exit_code = run_integrity_gate(
            repo_path=tmp_path,
            base_ref="master",
        )

    output = captured_out.getvalue()
    assert exit_code == 0
    assert "## Integrity Gate Verdict: HOLD" in output
    assert "`hold`" in output
    assert "Check 7 hold" in output
    assert "tests/test_calc.py::test_add_characterisation" in output
    assert "runtime" in output.lower()
