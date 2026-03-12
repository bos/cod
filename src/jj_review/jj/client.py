"""Typed access to local `jj` stack state."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from jj_review.errors import CliError
from jj_review.models.stack import LocalRevision, LocalStack

_COMMIT_TEMPLATE = (
    r'json(self) ++ "\t" ++ json(empty) ++ "\t" ++ json(divergent) ++ "\t" ++ '
    r'json(current_working_copy) ++ "\t" ++ json(immutable) ++ "\n"'
)


class JjCommandError(CliError):
    """Raised when a `jj` invocation fails."""


class RevsetResolutionError(CliError):
    """Raised when a revset does not resolve to exactly one visible revision."""


class UnsupportedStackError(CliError):
    """Raised when local history cannot be treated as a linear review stack."""


type JjRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


class JjClient:
    """Thin wrapper around `jj` commands used by the review tool."""

    def __init__(self, repo_root: Path, *, runner: JjRunner | None = None) -> None:
        self._repo_root = repo_root
        self._runner = runner or _default_runner

    def discover_review_stack(self, revset: str | None = None) -> LocalStack:
        """Resolve a review stack from a selected head back to `trunk()`."""

        trunk = self._resolve_trunk()
        if revset is None:
            head, selected_revset = self.resolve_default_head()
        else:
            head = self.resolve_revision(revset)
            selected_revset = revset
            if head.current_working_copy and head.empty:
                raise UnsupportedStackError(
                    "Selected revision resolves to the empty working-copy commit. "
                    "Select a concrete change instead."
                )

        if head.commit_id == trunk.commit_id:
            return LocalStack(
                head=head,
                revisions=(),
                selected_revset=selected_revset,
                trunk=trunk,
            )

        stack_head_first: list[LocalRevision] = []
        child_in_path: LocalRevision | None = None
        current = head
        while current.commit_id != trunk.commit_id:
            self._validate_reviewable_revision(current)
            if child_in_path is not None:
                reviewable_children = self.list_reviewable_children(current.commit_id)
                if len(reviewable_children) > 1:
                    raise UnsupportedStackError(
                        f"Unsupported stack shape at {current.change_id}: multiple "
                        "reviewable children require separate PR chains."
                    )
                child_matches_path = any(
                    child.commit_id == child_in_path.commit_id
                    for child in reviewable_children
                )
                if not child_matches_path:
                    raise UnsupportedStackError(
                        f"Unsupported stack shape at {current.change_id}: selected head does "
                        "not follow the only reviewable child of this ancestor."
                    )

            stack_head_first.append(current)
            parent_commit_id = current.only_parent_commit_id()
            child_in_path = current
            current = self.resolve_revision(parent_commit_id)

        return LocalStack(
            head=head,
            revisions=tuple(reversed(stack_head_first)),
            selected_revset=selected_revset,
            trunk=trunk,
        )

    def resolve_default_head(self) -> tuple[LocalRevision, str]:
        """Resolve the default head revision used when the CLI omits `<revset>`."""

        working_copy = self.resolve_revision("@")
        if working_copy.current_working_copy and working_copy.empty:
            return self.resolve_revision("@-"), "@-"
        return working_copy, "@"

    def resolve_revision(self, revset: str) -> LocalRevision:
        """Resolve a revset to exactly one visible revision."""

        revisions = self._query_revisions(revset, limit=2)
        if not revisions:
            raise RevsetResolutionError(
                f"Revset {revset!r} did not resolve to a visible revision."
            )
        if len(revisions) > 1:
            raise RevsetResolutionError(f"Revset {revset!r} resolved to more than one revision.")
        return revisions[0]

    def _resolve_trunk(self) -> LocalRevision:
        """Resolve `trunk()` and reject the implicit root fallback."""

        trunk = self.resolve_revision("trunk()")
        if len(trunk.parents) == 0:
            raise UnsupportedStackError(
                "`trunk()` resolved to the root commit. Configure a concrete trunk bookmark "
                "before discovering a review stack."
            )
        return trunk

    def list_reviewable_children(self, commit_id: str) -> list[LocalRevision]:
        """List visible mutable children that count as reviewable units."""

        revisions = self._query_revisions(f"children('{commit_id}')")
        return [revision for revision in revisions if revision.is_reviewable()]

    def _query_revisions(self, revset: str, *, limit: int | None = None) -> list[LocalRevision]:
        command = ["log", "--no-graph", "-r", revset, "-T", _COMMIT_TEMPLATE]
        if limit is not None:
            command.extend(["--limit", str(limit)])

        stdout = self._run(command)
        revisions: list[LocalRevision] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            revisions.append(_parse_revision_line(stripped))
        return revisions

    def _run(self, args: Sequence[str]) -> str:
        command = ["jj", *args]
        try:
            completed = self._runner(command, self._repo_root)
        except FileNotFoundError as error:
            raise JjCommandError("`jj` is not installed or is not on PATH.") from error

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise JjCommandError(f"{shlex.join(command)} failed: {message}")
        return completed.stdout

    def _validate_reviewable_revision(self, revision: LocalRevision) -> None:
        if revision.immutable:
            raise UnsupportedStackError(
                f"Unsupported stack shape at {revision.change_id}: immutable commits are not "
                "reviewable."
            )
        if revision.divergent:
            raise UnsupportedStackError(
                f"Unsupported stack shape at {revision.change_id}: divergent changes are not "
                "supported."
            )
        if len(revision.parents) > 1:
            raise UnsupportedStackError(
                f"Unsupported stack shape at {revision.change_id}: merge commits are not "
                "supported."
            )
        if len(revision.parents) == 0:
            raise UnsupportedStackError(
                f"Unsupported stack shape at {revision.change_id}: stack reached the root "
                "commit before `trunk()`."
            )


def _default_runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        cwd=cwd,
        text=True,
    )


def _parse_revision_line(line: str) -> LocalRevision:
    commit_json, empty_json, divergent_json, working_copy_json, immutable_json = line.split("\t")
    commit = json.loads(commit_json)
    return LocalRevision(
        change_id=commit["change_id"],
        commit_id=commit["commit_id"],
        current_working_copy=json.loads(working_copy_json),
        description=commit["description"],
        divergent=json.loads(divergent_json),
        empty=json.loads(empty_json),
        immutable=json.loads(immutable_json),
        parents=tuple(commit["parents"]),
    )
