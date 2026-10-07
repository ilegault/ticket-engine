"""Per-repo virtual environments for the box worker.

WHY THIS EXISTS
---------------
ADR 0010 (rules 1-2) and the add-repo spec require that the box worker keep
one isolated Python virtual environment per target repo outside the clone at
<envs_dir>/<name>. Environments are built from each repo's declared
`python_version` and `install` command, and rebuilt only when a fingerprint of
the repo's dependency files changes.

Decisions live in pure functions (`env_fingerprint`, `env_paths`,
`venv_create_command`, `env_vars`) while command execution is abstracted via
`CommandRunner` so environments can be verified without creating real venvs.
"""

import hashlib
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ticket_engine.config import RepoConfig

logger = logging.getLogger(__name__)

CommandRunner = Callable[[list[str] | str, str, dict[str, str] | None, bool], tuple[int, str]]


@dataclass(frozen=True)
class EnvPaths:
    root: Path
    bin_dir: Path
    python: Path


@dataclass(frozen=True)
class EnvResult:
    ok: bool
    rebuilt: bool
    paths: EnvPaths
    fingerprint: str


def env_fingerprint(repo_path: Path, python_version: str, install: str) -> str:
    """Compute a SHA-256 fingerprint over python_version, install, and dependency files.

    Candidate files at repo root: pyproject.toml, setup.py, setup.cfg, and any
    requirements*.txt files, sorted by name, with \\r\\n normalised to \\n.
    """
    candidate_names: set[str] = {"pyproject.toml", "setup.py", "setup.cfg"}
    for p in repo_path.glob("requirements*.txt"):
        if p.is_file():
            candidate_names.add(p.name)

    files_to_hash: list[tuple[str, Path]] = []
    for name in sorted(candidate_names):
        p = repo_path / name
        if p.is_file():
            files_to_hash.append((name, p))

    h = hashlib.sha256()
    h.update(python_version.encode("utf-8"))
    h.update(b"\n")
    h.update(install.encode("utf-8"))
    h.update(b"\n")
    for name, p in files_to_hash:
        h.update(name.encode("utf-8"))
        h.update(b"\n")
        content = p.read_bytes().replace(b"\r\n", b"\n")
        h.update(content)
        h.update(b"\n")
    return h.hexdigest()


def env_paths(envs_dir: str, repo: str, is_windows: bool) -> EnvPaths:
    """Return the filesystem paths for a repo's virtual environment."""
    repo_name = repo.split("/")[-1]
    root = Path(envs_dir) / repo_name
    if is_windows:
        bin_dir = root / "Scripts"
        python = bin_dir / "python.exe"
    else:
        bin_dir = root / "bin"
        python = bin_dir / "python"
    return EnvPaths(root=root, bin_dir=bin_dir, python=python)


def venv_create_command(python_version: str, root: Path, is_windows: bool) -> list[str]:
    """Return the command arguments to create the virtual environment."""
    if is_windows:
        return ["py", f"-{python_version}", "-m", "venv", "--clear", str(root)]
    return [f"python{python_version}", "-m", "venv", "--clear", str(root)]


def env_vars(paths: EnvPaths, base_path: str) -> dict[str, str]:
    """Return environment variables directing execution to the repo environment."""
    path_val = f"{paths.bin_dir}{os.pathsep}{base_path}" if base_path else str(paths.bin_dir)
    return {
        "PATH": path_val,
        "VIRTUAL_ENV": str(paths.root),
    }


def default_command_runner(
    args: list[str] | str,
    cwd: str,
    env: dict[str, str] | None,
    shell: bool,
) -> tuple[int, str]:
    """Execute a command via subprocess.run, capturing output up to 1800s."""
    merged_env = {**os.environ, **env} if env is not None else None
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=merged_env,
            shell=shell,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=1800,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after 1800s"


def ensure_repo_env(
    repo: str,
    repo_path: Path,
    config: RepoConfig,
    envs_dir: str,
    run: CommandRunner = default_command_runner,
    is_windows: bool = (os.name == "nt"),
) -> EnvResult:
    """Ensure the repo virtual environment is built and up to date."""
    paths = env_paths(envs_dir, repo, is_windows)
    fp = env_fingerprint(repo_path, config.python_version, config.install)
    fp_file = paths.root / ".engine-fingerprint"

    if fp_file.is_file() and paths.python.exists():
        try:
            existing_fp = fp_file.read_text(encoding="utf-8").strip()
            if existing_fp == fp:
                return EnvResult(ok=True, rebuilt=False, paths=paths, fingerprint=fp)
        except OSError:
            pass

    # Rebuild required
    create_cmd = venv_create_command(config.python_version, paths.root, is_windows)
    code, output = run(create_cmd, str(repo_path), None, False)
    if code != 0:
        logger.warning(
            "repo environment for %s failed at %s (exit %d): %s",
            repo,
            "venv",
            code,
            output[-4000:],
        )
        fp_file.unlink(missing_ok=True)
        return EnvResult(ok=False, rebuilt=False, paths=paths, fingerprint=fp)

    install_env = env_vars(paths, os.environ.get("PATH", ""))
    code, output = run(config.install, str(repo_path), install_env, True)
    if code != 0:
        logger.warning(
            "repo environment for %s failed at %s (exit %d): %s",
            repo,
            "install",
            code,
            output[-4000:],
        )
        fp_file.unlink(missing_ok=True)
        return EnvResult(ok=False, rebuilt=False, paths=paths, fingerprint=fp)

    paths.root.mkdir(parents=True, exist_ok=True)
    fp_file.write_text(fp, encoding="utf-8")
    return EnvResult(ok=True, rebuilt=True, paths=paths, fingerprint=fp)
