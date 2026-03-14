"""Minimal async GitHub API client."""

from __future__ import annotations

from typing import Any

import httpx

from jj_review.models.github import GithubPullRequest, GithubRepository


class GithubClientError(RuntimeError):
    """Raised when GitHub returns a non-success response."""


class GithubClient:
    """Thin async wrapper around the GitHub REST API."""

    def __init__(
        self,
        *,
        base_url: str,
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

    async def __aenter__(self) -> GithubClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_repository(self, owner: str, repo: str) -> GithubRepository:
        response = await self._client.get(f"/repos/{owner}/{repo}")
        return GithubRepository.model_validate(self._expect_success(response))

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        state: str = "all",
    ) -> tuple[GithubPullRequest, ...]:
        response = await self._client.get(
            f"/repos/{owner}/{repo}/pulls",
            params={"head": head, "state": state},
        )
        payload = self._expect_success(response)
        if not isinstance(payload, list):
            raise GithubClientError("GitHub pull request list response was not a JSON array.")
        return tuple(GithubPullRequest.model_validate(item) for item in payload)

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
        response = await self._client.post(
            f"/repos/{owner}/{repo}/pulls",
            json={"base": base, "body": body, "head": head, "title": title},
        )
        return GithubPullRequest.model_validate(self._expect_success(response))

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
        response = await self._client.patch(
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
            json={"base": base, "body": body, "title": title},
        )
        return GithubPullRequest.model_validate(self._expect_success(response))

    def _expect_success(self, response: httpx.Response) -> Any:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise GithubClientError(
                f"GitHub request failed: {error.response.status_code} {error.response.text}"
            ) from error
        return response.json()
