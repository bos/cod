from __future__ import annotations

import asyncio

import httpx
import pytest

from jj_review.github.client import GithubClient, GithubClientError


def test_github_client_retries_429_responses_with_retry_after() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"message": "slow down"},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "clone_url": "https://github.test/octo-org/stacked-review.git",
                "default_branch": "main",
                "full_name": "octo-org/stacked-review",
                "html_url": "https://github.test/octo-org/stacked-review",
                "name": "stacked-review",
                "private": True,
                "url": "https://api.github.test/repos/octo-org/stacked-review",
            },
            request=request,
        )

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def run_test() -> str:
        transport = httpx.MockTransport(handler)
        async with GithubClient(
            base_url="https://api.github.test",
            max_rate_limit_retries=1,
            sleep=record_sleep,
            transport=transport,
        ) as client:
            repository = await client.get_repository("octo-org", "stacked-review")
        return repository.full_name

    assert asyncio.run(run_test()) == "octo-org/stacked-review"
    assert attempts == 2
    assert sleeps == [0.0]


def test_github_client_retries_secondary_rate_limits_without_retry_after() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                403,
                json={"message": "You have exceeded a secondary rate limit."},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "clone_url": "https://github.test/octo-org/stacked-review.git",
                "default_branch": "main",
                "full_name": "octo-org/stacked-review",
                "html_url": "https://github.test/octo-org/stacked-review",
                "name": "stacked-review",
                "private": True,
                "url": "https://api.github.test/repos/octo-org/stacked-review",
            },
            request=request,
        )

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def run_test() -> str:
        transport = httpx.MockTransport(handler)
        async with GithubClient(
            base_rate_limit_backoff_seconds=0.25,
            base_url="https://api.github.test",
            max_rate_limit_retries=1,
            sleep=record_sleep,
            transport=transport,
        ) as client:
            repository = await client.get_repository("octo-org", "stacked-review")
        return repository.default_branch

    assert asyncio.run(run_test()) == "main"
    assert attempts == 2
    assert sleeps == [0.25]


def test_github_client_does_not_retry_non_rate_limited_errors() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with GithubClient(
            base_url="https://api.github.test",
            max_rate_limit_retries=1,
            sleep=record_sleep,
            transport=transport,
        ) as client:
            await client.get_repository("octo-org", "stacked-review")

    with pytest.raises(GithubClientError, match="GitHub request failed: 404"):
        asyncio.run(run_test())

    assert attempts == 1
    assert sleeps == []


def test_github_client_lists_pull_request_reviews() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octo-org/stacked-review/pulls/7/reviews"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "state": "APPROVED",
                    "user": {"login": "reviewer-1"},
                },
                {
                    "id": 2,
                    "state": "COMMENTED",
                    "user": {"login": "reviewer-2"},
                },
            ],
            request=request,
        )

    async def run_test() -> tuple[str, str]:
        transport = httpx.MockTransport(handler)
        async with GithubClient(
            base_url="https://api.github.test",
            transport=transport,
        ) as client:
            reviews = await client.list_pull_request_reviews(
                "octo-org",
                "stacked-review",
                pull_number=7,
            )
        if reviews[0].user is None:
            raise AssertionError("Review payload should include a user.")
        return reviews[0].user.login, reviews[1].state

    assert asyncio.run(run_test()) == ("reviewer-1", "COMMENTED")
