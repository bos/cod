from __future__ import annotations

from jj_review.commands.cleanup import _should_inspect_stack_comment_cleanup
from jj_review.models.bookmarks import BookmarkState, GitRemote, RemoteBookmarkState
from jj_review.models.cache import CachedChange


def test_should_skip_stack_comment_inspection_for_stale_open_change_without_comment_hint(
) -> None:
    bookmark_state = BookmarkState(
        name="review/feature-aaaaaaaa",
        remote_targets=(
            RemoteBookmarkState(remote="origin", targets=("commit-1",)),
        ),
    )

    should_inspect = _should_inspect_stack_comment_cleanup(
        bookmark_state=bookmark_state,
        cached_change=CachedChange(
            bookmark="review/feature-aaaaaaaa",
            pr_number=7,
            pr_state="open",
            stack_comment_id=None,
        ),
        remote=GitRemote(name="origin", url="git@github.com:octo-org/stacked-review.git"),
        stale_reason="local change is no longer reviewable",
    )

    assert should_inspect is False


def test_should_inspect_stack_comment_for_stale_change_with_cached_comment_id() -> None:
    should_inspect = _should_inspect_stack_comment_cleanup(
        bookmark_state=BookmarkState(name="review/feature-aaaaaaaa"),
        cached_change=CachedChange(
            bookmark="review/feature-aaaaaaaa",
            pr_number=7,
            pr_state="open",
            stack_comment_id=12,
        ),
        remote=GitRemote(name="origin", url="git@github.com:octo-org/stacked-review.git"),
        stale_reason="local change is no longer reviewable",
    )

    assert should_inspect is True


def test_should_inspect_stack_comment_for_stale_change_with_missing_remote_branch() -> None:
    should_inspect = _should_inspect_stack_comment_cleanup(
        bookmark_state=BookmarkState(name="review/feature-aaaaaaaa"),
        cached_change=CachedChange(
            bookmark="review/feature-aaaaaaaa",
            pr_number=7,
            pr_state="open",
            stack_comment_id=None,
        ),
        remote=GitRemote(name="origin", url="git@github.com:octo-org/stacked-review.git"),
        stale_reason="local change is no longer reviewable",
    )

    assert should_inspect is True
