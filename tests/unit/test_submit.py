from __future__ import annotations

import pytest

from jj_review.commands.submit import (
    SubmitRemoteResolutionError,
    select_submit_remote,
)
from jj_review.config import RepoConfig
from jj_review.models.bookmarks import GitRemote


def test_select_submit_remote_prefers_configured_remote() -> None:
    remote = select_submit_remote(
        RepoConfig(remote="upstream"),
        (
            GitRemote(name="origin", url="git@example.com:org/repo.git"),
            GitRemote(name="upstream", url="git@example.com:org/repo.git"),
        ),
    )

    assert remote.name == "upstream"


def test_select_submit_remote_uses_origin_when_multiple_remotes_exist() -> None:
    remote = select_submit_remote(
        RepoConfig(),
        (
            GitRemote(name="origin", url="git@example.com:org/repo.git"),
            GitRemote(name="backup", url="git@example.com:org/repo.git"),
        ),
    )

    assert remote.name == "origin"


def test_select_submit_remote_uses_only_remote_when_unambiguous() -> None:
    remote = select_submit_remote(
        RepoConfig(),
        (GitRemote(name="upstream", url="git@example.com:org/repo.git"),),
    )

    assert remote.name == "upstream"


def test_select_submit_remote_rejects_missing_configured_remote() -> None:
    with pytest.raises(SubmitRemoteResolutionError, match="Configured remote 'origin'"):
        select_submit_remote(
            RepoConfig(remote="origin"),
            (GitRemote(name="upstream", url="git@example.com:org/repo.git"),),
        )


def test_select_submit_remote_rejects_ambiguous_remote_set_without_origin() -> None:
    with pytest.raises(
        SubmitRemoteResolutionError,
        match="Could not determine which Git remote to use for submit",
    ):
        select_submit_remote(
            RepoConfig(),
            (
                GitRemote(name="backup", url="git@example.com:org/repo.git"),
                GitRemote(name="upstream", url="git@example.com:org/repo.git"),
            ),
        )
