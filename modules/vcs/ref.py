"""
RepoRef — provider-neutral repository handle passed to VcsClient methods.

GitLab addresses a repo by numeric `project_id`; GitHub/BitBucket by
`owner/repo` (owner == BitBucket workspace, repo == repo_slug). RepoRef carries
whichever identity the provider needs and is built from a project's
`VcsBinding` (config) or directly from CLI/MCP arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class RepoRefError(Exception):
    """Raised when a RepoRef lacks the identity its provider needs."""


@dataclass
class RepoRef:
    provider: str
    project_id: Optional[int] = None     # gitlab
    owner: Optional[str] = None          # github owner / bitbucket workspace
    repo: Optional[str] = None           # github/bitbucket repo name / slug
    host: Optional[str] = None

    @property
    def slug(self) -> str:
        if self.owner and self.repo:
            return f"{self.owner}/{self.repo}"
        if self.project_id is not None:
            return str(self.project_id)
        return "?"

    def require_project_id(self) -> int:
        if self.project_id is None:
            raise RepoRefError(
                f"{self.provider}: numeric project_id required (got {self!r})"
            )
        return int(self.project_id)

    def require_owner_repo(self) -> tuple[str, str]:
        if not self.owner or not self.repo:
            raise RepoRefError(
                f"{self.provider}: owner/repo required (got {self!r})"
            )
        return self.owner, self.repo


def ref_from_binding(binding) -> RepoRef:
    """Build a RepoRef from a shared.config_loader.VcsBinding."""
    return RepoRef(
        provider=binding.provider,
        project_id=binding.project_id,
        owner=binding.owner,
        repo=binding.repo,
        host=binding.host,
    )


def parse_repo_arg(provider: str, value: str, host: Optional[str] = None) -> RepoRef:
    """
    Build a RepoRef from a raw CLI/MCP positional argument.

    - gitlab:           numeric project_id
    - github/bitbucket: "owner/repo"
    """
    provider = (provider or "gitlab").lower()
    if provider == "gitlab":
        return RepoRef(provider=provider, project_id=int(value), host=host)
    if "/" not in value:
        raise RepoRefError(f"{provider}: expected 'owner/repo', got {value!r}")
    owner, repo = value.split("/", 1)
    return RepoRef(provider=provider, owner=owner, repo=repo, host=host)
