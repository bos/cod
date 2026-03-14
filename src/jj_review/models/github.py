"""GitHub API response models."""

from pydantic import BaseModel, ConfigDict


class GithubRepository(BaseModel):
    """Subset of repository fields used by the client."""

    model_config = ConfigDict(extra="ignore")

    clone_url: str
    default_branch: str
    full_name: str
    html_url: str
    name: str
    private: bool
    url: str


class GithubBranchRef(BaseModel):
    """Subset of branch-ref fields embedded in pull request payloads."""

    model_config = ConfigDict(extra="ignore")

    label: str | None = None
    ref: str


class GithubPullRequest(BaseModel):
    """Subset of pull request fields used by the client."""

    model_config = ConfigDict(extra="ignore")

    base: GithubBranchRef
    body: str | None = None
    head: GithubBranchRef
    html_url: str
    number: int
    state: str
    title: str
