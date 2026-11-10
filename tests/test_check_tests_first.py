"""Tests for scripts/check_tests_first.py (ticket 87).

The script is copied unedited into every target repo, so it is loaded from its
path, exactly as a target repo would run it. Git tests build a real repository
in ``tmp_path``; nothing is patched.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check_tests_first.py"
_spec = importlib.util.spec_from_file_location("check_tests_first", _SCRIPT)
assert _spec and _spec.loader
ctf = importlib.util.module_from_spec(_spec)
sys.modules["check_tests_first"] = ctf
_spec.loader.exec_module(ctf)

TAG = "[no-test-needed: bench only]"


@pytest.fixture(autouse=True)
def _no_event(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)


def git(repo: pathlib.Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return res.stdout.strip()


def make_repo(tmp_path: pathlib.Path, *, origin: bool = True) -> pathlib.Path:
    """A real repo with one base commit; optionally marks it as origin/master."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.name", "Test Author")
    git(repo, "config", "user.email", "author@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    if origin:
        git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    return repo


def commit(repo: pathlib.Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def run(repo: pathlib.Path, capsys, *extra: str) -> tuple[int, str]:
    capsys.readouterr()
    code = ctf.main([*extra, "--repo-root", str(repo)])
    return code, capsys.readouterr().out


def test_unresolvable_base_fails_closed(tmp_path, capsys):
    repo = make_repo(tmp_path, origin=False)
    commit(repo, {"src/a.py": "x = 1\n"}, "add a")
    code, out = run(repo, capsys)
    assert code == 1
    assert "FAIL: cannot determine the changes to check" in out
    assert "fetch-depth: 0" in out

    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    code, out = run(not_a_repo, capsys)
    assert code == 1
    assert "FAIL: cannot determine the changes to check" in out
    assert "fetch-depth: 0" in out


def test_commit_tag_is_read_on_a_pr_run(tmp_path, capsys):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"src/a.py": "x = 1\n"}, f"add a {TAG}")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "title": "Add a",
                    "body": "Plain body, no tag.",
                    "base": {"sha": base},
                    "head": {"sha": head},
                }
            }
        ),
        encoding="utf-8",
    )
    code, out = run(repo, capsys, "--event-path", str(event))
    assert code == 0
    assert "bench only" in out


def test_later_untagged_commit_is_not_covered(tmp_path, capsys):
    repo = make_repo(tmp_path)
    c1 = commit(repo, {"src/a.py": "x = 1\n"}, f"add a {TAG}")
    c2 = commit(repo, {"src/a.py": "x = 2\n"}, "change a again")
    code, out = run(repo, capsys)
    assert code == 1
    assert "src/a.py" in out
    assert f"untagged commit {c2[:7]}" in out
    assert c1[:7] not in out

    repo2 = make_repo(tmp_path / "second")
    commit(repo2, {"src/a.py": "x = 1\n"}, f"add a {TAG}")
    commit(repo2, {"src/b.py": "y = 1\n"}, "add b")
    code, out = run(repo2, capsys)
    assert code == 1
    fail_list = out.split("Tests-first rule violation")[0]
    assert "src/b.py" in fail_list
    assert "src/a.py" not in fail_list


def test_docstring_and_comment_only_change_passes(tmp_path, capsys):
    repo = make_repo(tmp_path)
    old = 'def f():\n    """Old doc."""\n    return 1\n'
    new = 'def f():\n    """Reworded doc."""\n    # a new comment\n    return 1\n'
    commit(repo, {"src/a.py": old}, "add a")
    git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    commit(repo, {"src/a.py": new}, "reword docs")
    code, out = run(repo, capsys)
    assert code == 0
    assert "Docstring/comment-only: src/a.py" in out


def test_code_fingerprint():
    a = (
        '"""Mod doc."""\n# comment\n\nclass C:\n    """Class doc."""\n\n'
        '    def m(self):\n        """Method doc."""\n        return 1\n'
    )
    b = (
        '"""Other mod doc."""\n\nclass C:\n    """Other."""\n\n'
        '    def m(self):\n        # note\n        """Other method doc."""\n'
        "        return 1\n"
    )
    assert ctf.code_fingerprint(a) == ctf.code_fingerprint(b)
    assert ctf.code_fingerprint("X = 1\n") != ctf.code_fingerprint("X = 2\n")
    assert ctf.code_fingerprint('def f():\n    "d"\n') != ctf.code_fingerprint(
        "def f():\n    pass\n"
    )
    assert ctf.code_fingerprint("def f(:\n") is None


def test_source_paths_come_from_engine_config(tmp_path, capsys):
    repo = make_repo(tmp_path / "configured")
    commit(
        repo,
        {
            ".ticket-engine.toml": 'source_paths = ["t8_daq_system"]\n',
            "t8_daq_system/x.py": "x = 1\n",
        },
        "add x",
    )
    code, out = run(repo, capsys)
    assert code == 1
    assert "t8_daq_system/x.py" in out

    plain = make_repo(tmp_path / "plain")
    commit(plain, {"lib/x.py": "x = 1\n"}, "add x")
    code, out = run(plain, capsys)
    assert code == 0

    bad = make_repo(tmp_path / "bad")
    commit(bad, {".ticket-engine.toml": "source_paths = [\n"}, "bad toml")
    code, out = run(bad, capsys)
    assert code == 1
    assert "FAIL: cannot read .ticket-engine.toml" in out


def test_push_to_base_branch_checks_the_last_commit(tmp_path, capsys):
    repo = make_repo(tmp_path)
    commit(repo, {"src/a.py": "x = 1\n"}, "add a")
    git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    code, out = run(repo, capsys)
    assert code == 1
    assert "src/a.py" in out


def test_files_mode_is_unchanged(tmp_path, capsys):
    def files(*args: str) -> int:
        return ctf.main(["--files", *args, "--repo-root", str(tmp_path)])

    assert files("docs/README.md", "tests/test_foo.py") == 0
    assert files("src/x.py") == 1
    assert files("src/x.py", "tests/test_x.py") == 0
    assert files("src/x.py", "--exempt-reason", "r") == 0
    assert files("src/x.py", "--message", "m [no-test-needed: n]") == 0
    assert files("src/x.py", "--label", "tests-exempt") == 0
    passed, message = ctf.evaluate_tests_first(["src/x.py"])
    assert not passed
    assert (
        "FAIL: Application source files modified under 'src/' without "
        "corresponding test changes under 'tests/'"
    ) in message


def test_script_fits_every_target_repo():
    text = _SCRIPT.read_text(encoding="utf-8")
    too_long = [
        n for n, line in enumerate(text.splitlines(), 1) if len(line) > 88
    ]
    assert too_long == []
    tree = ast.parse(text)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names)
