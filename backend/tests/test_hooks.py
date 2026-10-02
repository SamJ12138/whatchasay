"""The repository's pre-commit hook (scripts/hooks/pre-commit, installed by
scripts/install-hooks.py) runs the fast backend suite and the extension tests and
blocks the commit when either is red. Exercised in throwaway git repositories with a
stand-in backend Python, so this test never runs the real suites recursively."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "pre-commit"
INSTALL = REPO / "scripts" / "install-hooks.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("sh") is None,
                                reason="needs git and sh")


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def make_repo(tmp_path: Path, pytest_exit: int, node_ok: bool = True) -> Path:
    repo = tmp_path / "repo"
    (repo / "backend" / "venv" / "bin").mkdir(parents=True)
    (repo / "extension" / "tests").mkdir(parents=True)
    shutil.copytree(REPO / "scripts", repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    # stand-in for backend/venv/bin/python: "python -m pytest" exits with pytest_exit
    fake = repo / "backend" / "venv" / "bin" / "python"
    fake.write_text(f"#!/bin/sh\necho fake pytest $*\nexit {pytest_exit}\n", encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    (repo / "extension" / "tests" / "t.test.js").write_text(
        "require('node:test')('x', () => { if (%s) throw new Error('red'); });\n" % ("false" if node_ok else "true"),
        encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    subprocess.run([sys.executable, str(repo / "scripts" / "install-hooks.py")], cwd=repo, check=True,
                   capture_output=True, text=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "README.md")
    return repo


def commits(repo: Path) -> int:
    r = git(repo, "rev-list", "--count", "HEAD", check=False)
    return int(r.stdout.strip()) if r.returncode == 0 else 0


def test_hook_is_in_the_repo_and_runs_both_suites():
    text = HOOK.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    assert "-m pytest" in text and "node --test" in text
    assert "\r\n" not in text, "the hook must have LF line endings for sh"
    mode = git(REPO, "ls-files", "-s", "scripts/hooks/pre-commit").stdout.split()[0]
    assert mode == "100755", f"hook must be tracked as executable, got {mode}"


def test_installer_points_git_at_the_hooks(tmp_path):
    repo = make_repo(tmp_path, pytest_exit=0)
    assert git(repo, "config", "core.hooksPath").stdout.strip() == "scripts/hooks"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_green_suites_let_the_commit_through(tmp_path):
    repo = make_repo(tmp_path, pytest_exit=0)
    r = git(repo, "commit", "-m", "ok", check=False)
    assert r.returncode == 0, r.stderr + r.stdout
    assert commits(repo) == 1


def test_red_backend_suite_blocks_the_commit(tmp_path):
    repo = make_repo(tmp_path, pytest_exit=1)
    r = git(repo, "commit", "-m", "should be blocked", check=False)
    assert r.returncode != 0
    assert commits(repo) == 0
    assert "pre-commit" in (r.stderr + r.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_red_extension_suite_blocks_the_commit(tmp_path):
    repo = make_repo(tmp_path, pytest_exit=0, node_ok=False)
    r = git(repo, "commit", "-m", "should be blocked", check=False)
    assert r.returncode != 0
    assert commits(repo) == 0
