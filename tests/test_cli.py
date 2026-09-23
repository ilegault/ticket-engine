"""Tests for the dispatch CLI.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions and Ticket 01 Acceptance Criterion 6
require a CLI dry-run capability:
`dispatch --dry-run <path-to-clone>`
This command allows the developer or CI to verify the frontier, planned start
actions, skipped windows tickets, and parse findings without performing any
modifications or making live API calls.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

from ticket_engine.cli import main, run_dispatch_dry_run


def test_cli_dry_run_prints_frontier_actions_skipped_and_findings(tmp_path: pathlib.Path, capsys):
    issues_dir = tmp_path / ".scratch" / "test-effort" / "issues"
    issues_dir.mkdir(parents=True)

    # Ticket 01: Done
    (issues_dir / "01-first.md").write_text(
        """# 01: First Ticket
**Status:** done
**Blocked by:** None
""",
        encoding="utf-8",
    )

    # Ticket 02: Ready-for-agent, runner windows
    (issues_dir / "02-windows-task.md").write_text(
        """# 02: Windows Task
**Status:** ready-for-agent
**Blocked by:** 01
**Runner:** windows
""",
        encoding="utf-8",
    )

    # Ticket 03: Ready-for-agent, runner any
    (issues_dir / "03-agent-task.md").write_text(
        """# 03: Agent Task
**Status:** ready-for-agent
**Blocked by:** 01
**Runner:** any
""",
        encoding="utf-8",
    )

    # Ticket 04: Legacy status
    (issues_dir / "04-legacy-task.md").write_text(
        """# 04: Legacy Task
**Status:** human-task
**Blocked by:** None
""",
        encoding="utf-8",
    )

    exit_code = run_dispatch_dry_run(str(tmp_path))
    assert exit_code == 0

    captured = capsys.readouterr().out

    assert "Frontier:" in captured
    assert "02: Windows Task" in captured
    assert "03: Agent Task" in captured
    assert "Start actions:" in captured
    assert "03: Agent Task" in captured
    assert "Skipped windows tickets:" in captured
    assert "02: Windows Task" in captured
    assert "Parse findings:" in captured
    assert "04-legacy-task.md" in captured


def test_cli_live_dispatch_with_env_tokens(tmp_path: pathlib.Path, monkeypatch, capsys):
    issues_dir = tmp_path / ".scratch" / "test-effort" / "issues"
    issues_dir.mkdir(parents=True)
    (issues_dir / "01-test.md").write_text(
        """# 01: Test Ticket
**Status:** ready-for-agent
**Blocked by:** None
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("PIPELINE_TOKEN", "mock_pipeline_token")
    monkeypatch.setenv("JULES_API_KEY", "mock_jules_key")

    with patch("ticket_engine.cli.LiveDispatcher") as mock_disp_cls:
        mock_disp_instance = MagicMock()
        mock_disp_instance.dispatch.return_value = []
        mock_disp_cls.return_value = mock_disp_instance

        exit_code = main([str(tmp_path)])
        assert exit_code == 0
        mock_disp_cls.assert_called_once()
        mock_disp_instance.dispatch.assert_called_once()
