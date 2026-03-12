from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jj_review.jj import JjClient, UnsupportedStackError


def test_discover_review_stack_walks_linear_history_from_default_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feature 1", "feature-1.txt")
    _commit(repo, "feature 2", "feature-2.txt")

    stack = JjClient(repo).discover_review_stack()

    assert stack.selected_revset == "@-"
    assert [revision.subject for revision in stack.revisions] == ["feature 1", "feature 2"]


def test_discover_review_stack_rejects_root_fallback_trunk(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, configure_trunk=False)
    _commit(repo, "feature 1", "feature-1.txt")

    with pytest.raises(
        UnsupportedStackError,
        match=r"`trunk\(\)` resolved to the root commit",
    ):
        JjClient(repo).discover_review_stack()


def test_discover_review_stack_rejects_branching_review_children(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feature 1", "feature-1.txt")
    feature_1 = _current_parent_commit_id(repo)
    _commit(repo, "feature 2", "feature-2.txt")
    feature_2 = _current_parent_commit_id(repo)
    _new_child(repo, feature_1)
    _commit(repo, "feature side", "feature-side.txt")

    with pytest.raises(
        UnsupportedStackError,
        match="multiple reviewable children require separate PR chains",
    ):
        JjClient(repo).discover_review_stack(feature_2)


def test_discover_review_stack_rejects_immutable_revisions(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "feature 1", "feature-1.txt")
    feature_1 = _current_parent_commit_id(repo)
    _commit(repo, "feature 2", "feature-2.txt")
    _run(
        [
            "jj",
            "config",
            "set",
            "--repo",
            'revset-aliases."immutable_heads()"',
            f"builtin_immutable_heads() | {feature_1}",
        ],
        repo,
    )

    with pytest.raises(
        UnsupportedStackError,
        match="immutable commits are not reviewable",
    ):
        JjClient(repo).discover_review_stack()


def _init_repo(tmp_path: Path, *, configure_trunk: bool = True) -> Path:
    repo = tmp_path / "repo"
    _run(["jj", "git", "init", str(repo)], tmp_path)
    _run(["jj", "config", "set", "--repo", "user.name", "Test User"], repo)
    _run(["jj", "config", "set", "--repo", "user.email", "test@example.com"], repo)
    _write_file(repo / "README.md", "base\n")
    _run(["jj", "commit", "-m", "base"], repo)
    if configure_trunk:
        _run(["jj", "bookmark", "create", "main", "-r", "@-"], repo)
        _run(["jj", "config", "set", "--repo", 'revset-aliases."trunk()"', "main"], repo)
    return repo


def _commit(repo: Path, message: str, filename: str) -> None:
    _write_file(repo / filename, f"{message}\n")
    _run(["jj", "commit", "-m", message], repo)


def _current_parent_commit_id(repo: Path) -> str:
    completed = _run(
        [
            "jj",
            "log",
            "--no-graph",
            "-r",
            "@-",
            "-T",
            "commit_id",
        ],
        repo,
    )
    return completed.stdout.strip()


def _new_child(repo: Path, parent_commit_id: str) -> None:
    _run(["jj", "new", parent_commit_id], repo)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        cwd=cwd,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{command!r} failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def _write_file(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
