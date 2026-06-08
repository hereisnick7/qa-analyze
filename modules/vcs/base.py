"""
VcsClient — the common, provider-agnostic VCS interface.

Read-only methods are abstract: every provider must implement them. Write
methods have default implementations that raise `VcsWriteNotSupported`, so a
read-only provider (the GitHub provider in this first cut) is complete without
them. Write enablement, attribution checks and protected-ref guards are layered
on per provider as those capabilities are turned on (separate, guarded step).

Repo identity is passed as a `RepoRef` (see ref.py). Results are normalized
models (see models.py). `mr_id` is the project-local MR/PR number (GitLab iid,
GitHub/BitBucket number).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from modules.vcs.models import (
    Branch,
    Change,
    Discussion,
    MergeRequest,
    Note,
    Pipeline,
    User,
)
from modules.vcs.ref import RepoRef


class VcsError(Exception):
    """Base error for the VCS layer."""


class VcsWriteNotSupported(VcsError, NotImplementedError):
    """Raised when a provider has no (enabled) write capability for an op."""


class VcsClient(ABC):
    #: provider id — "gitlab" | "github" | "bitbucket"
    provider: str = ""

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    @abstractmethod
    def current_user(self) -> User: ...

    # ------------------------------------------------------------------
    # Merge / pull requests (read)
    # ------------------------------------------------------------------
    @abstractmethod
    def list_my_mrs(self, state: str = "opened") -> list[MergeRequest]: ...

    @abstractmethod
    def list_mrs(
        self,
        repo: RepoRef,
        state: str = "opened",
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> list[MergeRequest]: ...

    @abstractmethod
    def get_mr(self, repo: RepoRef, mr_id: int) -> MergeRequest: ...

    @abstractmethod
    def get_mr_changes(self, repo: RepoRef, mr_id: int) -> list[Change]:
        """Changed files. Each Change carries its unified `diff` body too."""

    @abstractmethod
    def list_mr_notes(self, repo: RepoRef, mr_id: int) -> list[Note]: ...

    @abstractmethod
    def list_mr_discussions(self, repo: RepoRef, mr_id: int) -> list[Discussion]: ...

    # ------------------------------------------------------------------
    # Branches (read)
    # ------------------------------------------------------------------
    @abstractmethod
    def get_branch(self, repo: RepoRef, branch: str) -> Branch: ...

    @abstractmethod
    def list_branches(self, repo: RepoRef) -> list[Branch]: ...

    # ------------------------------------------------------------------
    # CI (read)
    # ------------------------------------------------------------------
    @abstractmethod
    def list_pipelines(
        self,
        repo: RepoRef,
        ref: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Pipeline]: ...

    @abstractmethod
    def get_pipeline(self, repo: RepoRef, pipeline_id: int) -> Pipeline: ...

    # ------------------------------------------------------------------
    # Write — default: unsupported. Providers override as capabilities land.
    # ------------------------------------------------------------------
    def create_mr(
        self,
        repo: RepoRef,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        raise VcsWriteNotSupported(f"{self.provider}: create_mr not supported")

    def comment_mr(self, repo: RepoRef, mr_id: int, body: str) -> Note:
        raise VcsWriteNotSupported(f"{self.provider}: comment_mr not supported")

    def create_mr_discussion(
        self,
        repo: RepoRef,
        mr_id: int,
        body: str,
        file_path: str,
        line: int,
        line_type: str = "new",
    ) -> Discussion:
        raise VcsWriteNotSupported(
            f"{self.provider}: create_mr_discussion not supported"
        )
