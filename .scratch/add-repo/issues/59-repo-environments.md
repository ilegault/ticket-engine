# 59: Repo environments: one Python environment per repo, rebuilt when its dependencies change

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 54

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0010 rules 1–2; AGENTS.md §3 (decisions in pure code, thin adapters)

## What to build

A new module the box uses to build and reuse a *repo environment*: a Python
virtual environment for one target repo, kept outside the clone at
`<envs_dir>/<name>`, built with the repo's `python_version` and `install`
(ticket 54). It is rebuilt only when a fingerprint of the repo's dependency files
changes. This ticket builds the module only; nothing calls it yet (tickets 60–61).

The fingerprint and the command lists are **pure functions**, separate from the
function that runs commands, so they can be tested without creating real
environments.

## Acceptance criteria

Write the tests first in a new `tests/test_repo_env.py` and watch each fail before
writing `src/ticket_engine/repo_env.py`. Tests use real files in `tmp_path` and a
fake command runner that records `(args, cwd, env, shell)` and returns scripted
`(code, output)`. No test creates a real virtual environment.

- [x] **Fingerprint.** `env_fingerprint(repo_path: Path, python_version: str, install: str) -> str`
  returns a SHA-256 hex digest over `python_version`, `install`, and the name and
  contents of each of `pyproject.toml`, `setup.py`, `setup.cfg` and every
  `requirements*.txt` present at the repo root (sorted by name), with `\r\n`
  normalised to `\n` first. Tests: the same inputs give the same digest; editing
  `requirements.txt` changes it; changing `install` changes it; the same file with
  CRLF and LF line endings gives the same digest; editing a file not in that set
  (`README.md`) does not change it.
- [x] **Paths and commands.** `@dataclass(frozen=True) class EnvPaths` (`root`,
  `bin_dir`, `python`, all `Path`). `env_paths(envs_dir: str, repo: str, is_windows: bool) -> EnvPaths`:
  `root = Path(envs_dir) / <name after "/">`; `bin_dir = root / "Scripts"` and
  `python = bin_dir / "python.exe"` on Windows, else `root / "bin"` and
  `bin_dir / "python"`. `venv_create_command(python_version: str, root: Path, is_windows: bool) -> list[str]`
  returns `["py", f"-{python_version}", "-m", "venv", "--clear", str(root)]` on
  Windows and `[f"python{python_version}", "-m", "venv", "--clear", str(root)]`
  otherwise. `env_vars(paths: EnvPaths, base_path: str) -> dict[str, str]` returns
  `{"PATH": f"{paths.bin_dir}{os.pathsep}{base_path}", "VIRTUAL_ENV": str(paths.root)}`.
  One exact-value test each, for both platforms.
- [x] **Build only when needed.** `ensure_repo_env(repo: str, repo_path: Path, config: RepoConfig, envs_dir: str, run: CommandRunner, is_windows: bool) -> EnvResult`
  where `CommandRunner = Callable[[list[str] | str, str, dict[str, str] | None, bool], tuple[int, str]]`
  and `@dataclass(frozen=True) class EnvResult` (`ok: bool`, `rebuilt: bool`,
  `paths: EnvPaths`, `fingerprint: str`). If `root / ".engine-fingerprint"` holds
  the current fingerprint and `paths.python` exists, it runs nothing and returns
  `ok=True, rebuilt=False`. Otherwise it runs `venv_create_command(...)` (cwd
  `repo_path`, env None, shell False), then `config.install` as one shell string
  (cwd `repo_path`, env `env_vars(paths, os.environ.get("PATH", ""))`, shell True),
  and on success writes the fingerprint file and returns `ok=True, rebuilt=True`.
  Tests: `test_unchanged_fingerprint_runs_nothing`,
  `test_changed_fingerprint_creates_then_installs_in_order` (assert both recorded
  calls exactly), `test_install_runs_inside_the_environment` (the install call's
  env `PATH` starts with `bin_dir`).
- [x] **A failure leaves no fingerprint and logs, never raises.** If either command
  returns non-zero, `ensure_repo_env` logs
  `logger.warning("repo environment for %s failed at %s (exit %d): %s", repo, step, code, output[-4000:])`
  (`step` is `"venv"` or `"install"`), does not write the fingerprint file, and
  returns `ok=False`. Tests for each step, asserting the file is absent and the
  next call runs the commands again.
- [x] **The real runner.** `default_command_runner(args, cwd, env, shell)` runs
  `subprocess.run(args, cwd=cwd, env={**os.environ, **env} if env else None, shell=shell, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=1800)`
  and returns `(returncode, stdout + stderr)`; a `TimeoutExpired` returns
  `(124, "timed out after 1800s")`. Test it with `[sys.executable, "-c", "import os; print(os.environ['VIRTUAL_ENV'])"]`
  and env `{"VIRTUAL_ENV": "x"}`, asserting `(0, "x\n")`.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Calling this from the box, and the not-ready state (ticket 61).
- Choosing a Python version the box does not have installed: that surfaces as an
  `env_failed` repo in ticket 61.

## Comments

Implemented on 2026-10-06:
- Added `src/ticket_engine/repo_env.py` implementing `env_fingerprint`, `env_paths`, `venv_create_command`, `env_vars`, `ensure_repo_env`, and `default_command_runner`.
- Added `tests/test_repo_env.py` covering all acceptance criteria with 21 tests:
  - Fingerprint determinism, dependency file sorting, CRLF/LF normalization, and non-dependency file invariance.
  - Paths and venv creation command generation for Windows and POSIX.
  - `ensure_repo_env` caching via `.engine-fingerprint` and rebuild execution order.
  - Warning logging and absent fingerprint file on venv or install failure.
  - `default_command_runner` execution with env passing and timeout handling.
- All CI gates pass (ruff, check_tests_first, pytest).
