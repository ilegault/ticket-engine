import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ticket_engine.config import RepoConfig
from ticket_engine.repo_env import (
    EnvPaths,
    default_command_runner,
    ensure_repo_env,
    env_fingerprint,
    env_paths,
    env_vars,
    venv_create_command,
)


class FakeRunner:
    def __init__(self, responses: list[tuple[int, str]] | None = None) -> None:
        self.calls: list[tuple[list[str] | str, str, dict[str, str] | None, bool]] = []
        self._responses: list[tuple[int, str]] = list(responses or [(0, "ok")])

    def __call__(
        self,
        args: list[str] | str,
        cwd: str,
        env: dict[str, str] | None,
        shell: bool,
    ) -> tuple[int, str]:
        self.calls.append((args, cwd, env, shell))
        if self._responses:
            return self._responses.pop(0)
        return (0, "")


# ---------------------------------------------------------------------------
# Criterion 1: Fingerprint
# ---------------------------------------------------------------------------


def test_env_fingerprint_same_inputs_same_digest(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")

    fp1 = env_fingerprint(repo, "3.12", "pip install -e .")
    fp2 = env_fingerprint(repo, "3.12", "pip install -e .")

    assert fp1 == fp2
    assert len(fp1) == 64


def test_env_fingerprint_editing_requirements_changes_digest(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    req = repo / "requirements.txt"
    req.write_text("pytest==8.0.0\n", encoding="utf-8")

    fp1 = env_fingerprint(repo, "3.12", "pip install -e .")

    req.write_text("pytest==8.1.0\n", encoding="utf-8")
    fp2 = env_fingerprint(repo, "3.12", "pip install -e .")

    assert fp1 != fp2


def test_env_fingerprint_changing_install_changes_digest(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")

    fp1 = env_fingerprint(repo, "3.12", "pip install -e .")
    fp2 = env_fingerprint(repo, "3.12", "pip install -r requirements.txt")

    assert fp1 != fp2


def test_env_fingerprint_changing_python_version_changes_digest(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")

    fp1 = env_fingerprint(repo, "3.12", "pip install -e .")
    fp2 = env_fingerprint(repo, "3.11", "pip install -e .")

    assert fp1 != fp2


def test_env_fingerprint_crlf_and_lf_same_digest(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    req = repo / "requirements.txt"

    req.write_bytes(b"pytest==8.0.0\r\nruff==0.1.0\r\n")
    fp_crlf = env_fingerprint(repo, "3.12", "pip install -e .")

    req.write_bytes(b"pytest==8.0.0\nruff==0.1.0\n")
    fp_lf = env_fingerprint(repo, "3.12", "pip install -e .")

    assert fp_crlf == fp_lf


def test_env_fingerprint_editing_non_dependency_file_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")

    fp1 = env_fingerprint(repo, "3.12", "pip install -e .")

    (repo / "README.md").write_text("# Documentation\n", encoding="utf-8")
    fp2 = env_fingerprint(repo, "3.12", "pip install -e .")

    assert fp1 == fp2


def test_env_fingerprint_includes_all_dependency_files_sorted(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
    (repo / "setup.cfg").write_text("[metadata]\nname = test\n", encoding="utf-8")
    (repo / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")

    fp1 = env_fingerprint(repo, "3.12", "pip install -e .")

    (repo / "requirements-dev.txt").write_text("pytest\nruff\n", encoding="utf-8")
    fp2 = env_fingerprint(repo, "3.12", "pip install -e .")

    assert fp1 != fp2


# ---------------------------------------------------------------------------
# Criterion 2: Paths and commands
# ---------------------------------------------------------------------------


def test_env_paths_windows() -> None:
    paths = env_paths("C:/envs", "owner/slackbot", is_windows=True)
    assert paths.root == Path("C:/envs/slackbot")
    assert paths.bin_dir == Path("C:/envs/slackbot/Scripts")
    assert paths.python == Path("C:/envs/slackbot/Scripts/python.exe")


def test_env_paths_posix() -> None:
    paths = env_paths("/envs", "owner/slackbot", is_windows=False)
    assert paths.root == Path("/envs/slackbot")
    assert paths.bin_dir == Path("/envs/slackbot/bin")
    assert paths.python == Path("/envs/slackbot/bin/python")


def test_venv_create_command_windows() -> None:
    root = Path("C:/envs/slackbot")
    cmd = venv_create_command("3.12", root, is_windows=True)
    assert cmd == ["py", "-3.12", "-m", "venv", "--clear", str(root)]


def test_venv_create_command_posix() -> None:
    root = Path("/envs/slackbot")
    cmd = venv_create_command("3.12", root, is_windows=False)
    assert cmd == ["python3.12", "-m", "venv", "--clear", str(root)]


def test_env_vars_exact_value() -> None:
    paths = EnvPaths(
        root=Path("/envs/slackbot"),
        bin_dir=Path("/envs/slackbot/bin"),
        python=Path("/envs/slackbot/bin/python"),
    )
    result = env_vars(paths, "/usr/bin:/bin")
    assert result == {
        "PATH": f"{paths.bin_dir}{os.pathsep}/usr/bin:/bin",
        "VIRTUAL_ENV": str(paths.root),
    }


def test_env_vars_empty_base_path() -> None:
    paths = EnvPaths(
        root=Path("/envs/slackbot"),
        bin_dir=Path("/envs/slackbot/bin"),
        python=Path("/envs/slackbot/bin/python"),
    )
    result = env_vars(paths, "")
    assert result == {
        "PATH": str(paths.bin_dir),
        "VIRTUAL_ENV": str(paths.root),
    }


# ---------------------------------------------------------------------------
# Criterion 3: Build only when needed
# ---------------------------------------------------------------------------


def test_unchanged_fingerprint_runs_nothing(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    envs_dir = tmp_path / "envs"
    env_dir = envs_dir / "my-repo"
    bin_dir = env_dir / "Scripts"
    bin_dir.mkdir(parents=True)
    python_exe = bin_dir / "python.exe"
    python_exe.write_text("", encoding="utf-8")

    config = RepoConfig(python_version="3.12", install="pip install -e .")
    fp = env_fingerprint(repo_path, config.python_version, config.install)
    (env_dir / ".engine-fingerprint").write_text(fp, encoding="utf-8")

    runner = FakeRunner()
    result = ensure_repo_env(
        repo="owner/my-repo",
        repo_path=repo_path,
        config=config,
        envs_dir=str(envs_dir),
        run=runner,
        is_windows=True,
    )

    assert result.ok is True
    assert result.rebuilt is False
    assert result.fingerprint == fp
    assert len(runner.calls) == 0


def test_changed_fingerprint_creates_then_installs_in_order(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    envs_dir = tmp_path / "envs"
    config = RepoConfig(python_version="3.12", install="pip install -e .")
    fp = env_fingerprint(repo_path, config.python_version, config.install)

    runner = FakeRunner([(0, "venv created"), (0, "installed ok")])
    result = ensure_repo_env(
        repo="owner/my-repo",
        repo_path=repo_path,
        config=config,
        envs_dir=str(envs_dir),
        run=runner,
        is_windows=True,
    )

    assert result.ok is True
    assert result.rebuilt is True
    assert result.fingerprint == fp

    # Verify both recorded calls exactly
    expected_paths = env_paths(str(envs_dir), "owner/my-repo", is_windows=True)
    assert len(runner.calls) == 2

    # Call 1: venv creation
    expected_venv_cmd = ["py", "-3.12", "-m", "venv", "--clear", str(expected_paths.root)]
    assert runner.calls[0] == (expected_venv_cmd, str(repo_path), None, False)

    # Call 2: install string in environment
    expected_env = env_vars(expected_paths, os.environ.get("PATH", ""))
    assert runner.calls[1] == ("pip install -e .", str(repo_path), expected_env, True)

    # Verify fingerprint file was written
    fp_file = expected_paths.root / ".engine-fingerprint"
    assert fp_file.is_file()
    assert fp_file.read_text(encoding="utf-8").strip() == fp


def test_install_runs_inside_the_environment(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    envs_dir = tmp_path / "envs"
    config = RepoConfig(python_version="3.12", install="pip install -r requirements.txt")

    runner = FakeRunner([(0, ""), (0, "")])
    result = ensure_repo_env(
        repo="owner/my-repo",
        repo_path=repo_path,
        config=config,
        envs_dir=str(envs_dir),
        run=runner,
        is_windows=True,
    )

    assert result.ok is True
    install_call = runner.calls[1]
    install_env = install_call[2]
    assert install_env is not None
    assert install_env["PATH"].startswith(str(result.paths.bin_dir))
    assert install_env["VIRTUAL_ENV"] == str(result.paths.root)


def test_missing_python_triggers_rebuild_even_with_matching_fingerprint(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    envs_dir = tmp_path / "envs"
    env_dir = envs_dir / "my-repo"
    env_dir.mkdir(parents=True)

    config = RepoConfig(python_version="3.12", install="pip install -e .")
    fp = env_fingerprint(repo_path, config.python_version, config.install)
    (env_dir / ".engine-fingerprint").write_text(fp, encoding="utf-8")
    # Note: python.exe is missing!

    runner = FakeRunner([(0, "venv created"), (0, "installed ok")])
    result = ensure_repo_env(
        repo="owner/my-repo",
        repo_path=repo_path,
        config=config,
        envs_dir=str(envs_dir),
        run=runner,
        is_windows=True,
    )

    assert result.ok is True
    assert result.rebuilt is True
    assert len(runner.calls) == 2


# ---------------------------------------------------------------------------
# Criterion 4: A failure leaves no fingerprint and logs, never raises
# ---------------------------------------------------------------------------


def test_venv_failure_leaves_no_fingerprint_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    envs_dir = tmp_path / "envs"
    config = RepoConfig(python_version="3.12", install="pip install -e .")

    runner = FakeRunner([(1, "py: error: Python 3.12 not found")])

    with caplog.at_level(logging.WARNING):
        result = ensure_repo_env(
            repo="owner/my-repo",
            repo_path=repo_path,
            config=config,
            envs_dir=str(envs_dir),
            run=runner,
            is_windows=True,
        )

    assert result.ok is False
    assert not (result.paths.root / ".engine-fingerprint").exists()
    assert len(runner.calls) == 1

    # Check logged message
    assert "repo environment for owner/my-repo failed at venv (exit 1):" in caplog.text

    # Next call runs commands again
    runner._responses = [(0, "ok"), (0, "ok")]
    result2 = ensure_repo_env(
        repo="owner/my-repo",
        repo_path=repo_path,
        config=config,
        envs_dir=str(envs_dir),
        run=runner,
        is_windows=True,
    )
    assert result2.ok is True
    assert len(runner.calls) == 3


def test_install_failure_leaves_no_fingerprint_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    envs_dir = tmp_path / "envs"
    config = RepoConfig(python_version="3.12", install="pip install -e .")

    runner = FakeRunner([(0, "venv created"), (2, "pip: error: could not find a version")])

    with caplog.at_level(logging.WARNING):
        result = ensure_repo_env(
            repo="owner/my-repo",
            repo_path=repo_path,
            config=config,
            envs_dir=str(envs_dir),
            run=runner,
            is_windows=True,
        )

    assert result.ok is False
    assert not (result.paths.root / ".engine-fingerprint").exists()
    assert len(runner.calls) == 2

    # Check logged message
    assert "repo environment for owner/my-repo failed at install (exit 2):" in caplog.text

    # Next call runs commands again
    runner._responses = [(0, "ok"), (0, "ok")]
    result2 = ensure_repo_env(
        repo="owner/my-repo",
        repo_path=repo_path,
        config=config,
        envs_dir=str(envs_dir),
        run=runner,
        is_windows=True,
    )
    assert result2.ok is True
    assert len(runner.calls) == 4


# ---------------------------------------------------------------------------
# Criterion 5: The real runner
# ---------------------------------------------------------------------------


def test_default_command_runner_runs_command_with_env(tmp_path: Path) -> None:
    code, output = default_command_runner(
        [sys.executable, "-c", "import os; print(os.environ['VIRTUAL_ENV'])"],
        cwd=str(tmp_path),
        env={"VIRTUAL_ENV": "x"},
        shell=False,
    )
    assert code == 0
    assert output == "x\n"


def test_default_command_runner_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="test", timeout=1800)):
        code, output = default_command_runner(
            ["dummy"],
            cwd=str(tmp_path),
            env=None,
            shell=False,
        )
        assert code == 124
        assert output == "timed out after 1800s"
