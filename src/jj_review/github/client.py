"""Minimal async GitHub API client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from jj_review.models.github import (
    GithubIssueComment,
    GithubPullRequest,
    GithubPullRequestReview,
    GithubRepository,
)

logger = logging.getLogger(__name__)
_GRAPHQL_PULL_REQUEST_BATCH_SIZE = 25

_DEFAULT_RATE_LIMIT_RETRIES = 3
_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 1.0
_DEFAULT_MAX_RATE_LIMIT_BACKOFF_SECONDS = 8.0


class GithubClientError(RuntimeError):
    """Raised when GitHub returns a non-success response."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code


class GithubClient:
    """Thin async wrapper around the GitHub REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        base_rate_limit_backoff_seconds: float = _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
        max_rate_limit_backoff_seconds: float = _DEFAULT_MAX_RATE_LIMIT_BACKOFF_SECONDS,
        max_rate_limit_retries: int = _DEFAULT_RATE_LIMIT_RETRIES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "jj-review/dev",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=30.0,
            transport=transport,
        )
        self._base_rate_limit_backoff_seconds = base_rate_limit_backoff_seconds
        self._max_rate_limit_backoff_seconds = max_rate_limit_backoff_seconds
        self._max_rate_limit_retries = max_rate_limit_retries
        self._sleep = sleep

    async def __aenter__(self) -> GithubClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_repository(self, owner: str, repo: str) -> GithubRepository:
        response = await self._request("GET", f"/repos/{owner}/{repo}")
        return GithubRepository.model_validate(self._expect_success(response))

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        state: str = "all",
    ) -> tuple[GithubPullRequest, ...]:
        payload = await self._get_paginated_json_array(
            f"/repos/{owner}/{repo}/pulls",
            params={"head": head, "state": state},
            response_name="pull request list",
        )
        return tuple(GithubPullRequest.model_validate(item) for item in payload)

    async def get_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        pull_number: int,
    ) -> GithubPullRequest:
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
        )
        return GithubPullRequest.model_validate(self._expect_success(response))

    async def get_pull_requests_by_numbers(
        self,
        owner: str,
        repo: str,
        *,
        pull_numbers: Sequence[int],
    ) -> dict[int, GithubPullRequest | None]:
        numbers = sorted(set(pull_numbers))
        if not numbers:
            return {}

        results: dict[int, GithubPullRequest | None] = {}
        for chunk in _chunked(numbers, size=_GRAPHQL_PULL_REQUEST_BATCH_SIZE):
            query = _pull_requests_by_number_query(chunk)
            payload = await self._graphql_query(
                query,
                variables={"owner": owner, "repo": repo},
                response_name="pull request batch lookup",
            )
            repository = payload.get("repository")
            if repository is None:
                raise GithubClientError(
                    "GitHub pull request batch lookup response was missing repository data."
                )
            if not isinstance(repository, dict):
                raise GithubClientError(
                    "GitHub pull request batch lookup response had invalid repository data."
                )
            for number in chunk:
                alias = _pull_request_alias(number)
                raw_pull_request = repository.get(alias)
                if raw_pull_request is None:
                    results[number] = None
                    continue
                if not isinstance(raw_pull_request, dict):
                    raise GithubClientError(
                        "GitHub pull request batch lookup response had invalid pull request "
                        f"payload for #{number}."
                    )
                results[number] = GithubPullRequest.model_validate(
                    _pull_request_payload_from_graphql(raw_pull_request)
                )
        return results

    async def get_pull_requests_by_head_refs(
        self,
        owner: str,
        repo: str,
        *,
        head_refs: Sequence[str],
    ) -> dict[str, tuple[GithubPullRequest, ...]]:
        refs = sorted(set(head_refs))
        if not refs:
            return {}

        results: dict[str, tuple[GithubPullRequest, ...]] = {}
        for chunk in _chunked(refs, size=_GRAPHQL_PULL_REQUEST_BATCH_SIZE):
            aliases = {
                _pull_request_head_ref_alias(index): head_ref
                for index, head_ref in enumerate(chunk)
            }
            query = _pull_requests_by_head_ref_query(aliases)
            payload = await self._graphql_query(
                query,
                variables={"owner": owner, "repo": repo},
                response_name="pull request head lookup",
            )
            repository = payload.get("repository")
            if repository is None:
                raise GithubClientError(
                    "GitHub pull request head lookup response was missing repository data."
                )
            if not isinstance(repository, dict):
                raise GithubClientError(
                    "GitHub pull request head lookup response had invalid repository data."
                )
            for alias, head_ref in aliases.items():
                connection = repository.get(alias)
                results[head_ref] = _pull_request_connection_from_graphql(
                    alias=alias,
                    connection=connection,
                    response_name="pull request head lookup",
                )
        return results

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        base: str,
        body: str,
        head: str,
        title: str,
    ) -> GithubPullRequest:
        response = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"base": base, "body": body, "head": head, "title": title},
        )
        return GithubPullRequest.model_validate(self._expect_success(response))

    async def list_pull_request_reviews(
        self,
        owner: str,
        repo: str,
        *,
        pull_number: int,
    ) -> tuple[GithubPullRequestReview, ...]:
        payload = await self._get_paginated_json_array(
            f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            response_name="pull request reviews",
        )
        return tuple(GithubPullRequestReview.model_validate(item) for item in payload)

    async def list_issue_comments(
        self,
        owner: str,
        repo: str,
        *,
        issue_number: int,
    ) -> tuple[GithubIssueComment, ...]:
        payload = await self._get_paginated_json_array(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            response_name="issue comment list",
        )
        return tuple(GithubIssueComment.model_validate(item) for item in payload)

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        *,
        issue_number: int,
        body: str,
    ) -> GithubIssueComment:
        response = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        return GithubIssueComment.model_validate(self._expect_success(response))

    async def update_issue_comment(
        self,
        owner: str,
        repo: str,
        *,
        comment_id: int,
        body: str,
    ) -> GithubIssueComment:
        response = await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            json={"body": body},
        )
        return GithubIssueComment.model_validate(self._expect_success(response))

    async def delete_issue_comment(
        self,
        owner: str,
        repo: str,
        *,
        comment_id: int,
    ) -> None:
        response = await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
        )
        self._expect_no_content(response)

    async def update_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        pull_number: int,
        base: str,
        body: str,
        title: str,
    ) -> GithubPullRequest:
        response = await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
            json={"base": base, "body": body, "title": title},
        )
        return GithubPullRequest.model_validate(self._expect_success(response))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        for attempt in range(self._max_rate_limit_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                )
            except httpx.RequestError as error:
                raise GithubClientError(f"GitHub request failed: {error}") from error

            retry_after_seconds = self._retry_after_seconds(
                attempt=attempt,
                response=response,
            )
            if retry_after_seconds is None:
                return response

            logger.debug(
                "github rate limit encountered: method=%s path=%s status=%s attempt=%d "
                "retry_after_seconds=%.3f",
                method,
                path,
                response.status_code,
                attempt + 1,
                retry_after_seconds,
            )
            await self._sleep(retry_after_seconds)

        raise AssertionError("Rate-limit retry loop did not return a response.")

    async def _get_paginated_json_array(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        response_name: str,
    ) -> tuple[object, ...]:
        items: list[object] = []
        next_path: str | None = path
        next_params = params

        while next_path is not None:
            response = await self._request(
                "GET",
                next_path,
                params=next_params,
            )
            payload = self._expect_success(response)
            if not isinstance(payload, list):
                raise GithubClientError(
                    f"GitHub {response_name} response was not a JSON array."
                )
            items.extend(payload)
            next_path = response.links.get("next", {}).get("url")
            next_params = None

        return tuple(items)

    async def _graphql_query(
        self,
        query: str,
        *,
        response_name: str,
        variables: dict[str, object] | None = None,
    ) -> dict[str, object]:
        response = await self._request(
            "POST",
            "/graphql",
            json={
                "query": query,
                "variables": variables or {},
            },
        )
        payload = self._expect_success(response)
        if not isinstance(payload, dict):
            raise GithubClientError(
                f"GitHub {response_name} response was not a JSON object."
            )
        errors = payload.get("errors")
        if errors:
            raise GithubClientError(f"GitHub {response_name} failed: {errors}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GithubClientError(
                f"GitHub {response_name} response was missing `data`."
            )
        return data

    def _expect_success(self, response: httpx.Response) -> Any:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise GithubClientError(
                f"GitHub request failed: {error.response.status_code} {error.response.text}",
                retry_after_seconds=_parse_retry_after_header(
                    error.response.headers.get("Retry-After")
                ),
                status_code=error.response.status_code,
            ) from error
        return response.json()

    def _expect_no_content(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise GithubClientError(
                f"GitHub request failed: {error.response.status_code} {error.response.text}",
                retry_after_seconds=_parse_retry_after_header(
                    error.response.headers.get("Retry-After")
                ),
                status_code=error.response.status_code,
            ) from error

    def _retry_after_seconds(
        self,
        *,
        attempt: int,
        response: httpx.Response,
    ) -> float | None:
        if not _is_retryable_rate_limit(response):
            return None
        if attempt >= self._max_rate_limit_retries:
            return None

        retry_after_seconds = _parse_retry_after_header(response.headers.get("Retry-After"))
        if retry_after_seconds is not None:
            return retry_after_seconds

        reset_after_seconds = _seconds_until_rate_limit_reset(
            response.headers.get("X-RateLimit-Reset")
        )
        if reset_after_seconds is not None:
            return reset_after_seconds

        backoff_seconds = self._base_rate_limit_backoff_seconds * (2**attempt)
        return min(backoff_seconds, self._max_rate_limit_backoff_seconds)


def _is_retryable_rate_limit(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if "Retry-After" in response.headers or "X-RateLimit-Reset" in response.headers:
        return True
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    return "rate limit" in response.text.lower()


def _parse_retry_after_header(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        retry_after_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return max(retry_after_at.timestamp() - time.time(), 0.0)


def _seconds_until_rate_limit_reset(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value) - time.time(), 0.0)
    except ValueError:
        return None


def _chunked[ChunkValue](
    values: Sequence[ChunkValue], *, size: int
) -> list[tuple[ChunkValue, ...]]:
    return [tuple(values[index : index + size]) for index in range(0, len(values), size)]


def _pull_request_alias(number: int) -> str:
    return f"pr_{number}"


def _pull_request_head_ref_alias(index: int) -> str:
    return f"head_{index}"


def _pull_requests_by_number_query(numbers: Sequence[int]) -> str:
    selections = "\n".join(
        (
            f"      {_pull_request_alias(number)}: pullRequest(number: {number}) {{\n"
            "        number\n"
            "        state\n"
            "        mergedAt\n"
            "        url\n"
            "        title\n"
            "        body\n"
            "        baseRefName\n"
            "        headRefName\n"
            "      }"
        )
        for number in numbers
    )
    return (
        "query PullRequestsByNumber($owner: String!, $repo: String!) {\n"
        "  repository(owner: $owner, name: $repo) {\n"
        f"{selections}\n"
        "  }\n"
        "}\n"
    )


def _pull_requests_by_head_ref_query(aliases: dict[str, str]) -> str:
    selections = "\n".join(
        (
            f"    {alias}: pullRequests("
            f'first: 2, states: [OPEN, CLOSED], headRefName: {json.dumps(head_ref)}) {{\n'
            "      nodes {\n"
            "        number\n"
            "        state\n"
            "        mergedAt\n"
            "        url\n"
            "        title\n"
            "        body\n"
            "        baseRefName\n"
            "        headRefName\n"
            "      }\n"
            "    }"
        )
        for alias, head_ref in aliases.items()
    )
    return (
        "query PullRequestsByHeadRef($owner: String!, $repo: String!) {\n"
        "  repository(owner: $owner, name: $repo) {\n"
        f"{selections}\n"
        "  }\n"
        "}\n"
    )


def _pull_request_payload_from_graphql(raw_pull_request: dict[str, object]) -> dict[str, object]:
    return {
        "base": {"ref": raw_pull_request.get("baseRefName")},
        "body": raw_pull_request.get("body"),
        "head": {"ref": raw_pull_request.get("headRefName")},
        "html_url": raw_pull_request.get("url"),
        "merged_at": raw_pull_request.get("mergedAt"),
        "number": raw_pull_request.get("number"),
        "state": str(raw_pull_request.get("state", "")).lower(),
        "title": raw_pull_request.get("title"),
    }


def _pull_request_connection_from_graphql(
    *,
    alias: str,
    connection: object,
    response_name: str,
) -> tuple[GithubPullRequest, ...]:
    if connection is None:
        return ()
    if not isinstance(connection, dict):
        raise GithubClientError(
            f"GitHub {response_name} response had invalid connection payload for {alias!r}."
        )
    raw_nodes = connection.get("nodes")
    if raw_nodes is None:
        return ()
    if not isinstance(raw_nodes, list):
        raise GithubClientError(
            f"GitHub {response_name} response had invalid node payload for {alias!r}."
        )
    pull_requests: list[GithubPullRequest] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise GithubClientError(
                f"GitHub {response_name} response had invalid pull request payload for "
                f"{alias!r}."
            )
        pull_requests.append(
            GithubPullRequest.model_validate(_pull_request_payload_from_graphql(raw_node))
        )
    return tuple(pull_requests)
