"""
GitLab provider — adapts the existing `GitLabClient` to the VcsClient interface.

This is a thin normalizing wrapper: all HTTP/auth/retry/guard logic stays in
`modules.gitlab.client.GitLabClient` (unchanged), so the existing GitLab test
suite is unaffected. Only the result shapes are mapped onto the normalized
models. GitOps QA deploy discovery is deliberately NOT surfaced here — it stays
GitLab-only via `modules.gitlab.gitops_qa` / the GitLab CLI/MCP.
"""

from __future__ import annotations

from typing import Optional

from modules.gitlab.client import GitLabClient
from modules.vcs.base import VcsClient
from modules.vcs.guards import assert_mr_title_is_source_branch
from modules.vcs.models import (
    Branch,
    Change,
    Discussion,
    MergeRequest,
    Note,
    Pipeline,
    User,
    _normalize_state,
)
from modules.vcs.ref import RepoRef


def _mr(mr: dict) -> MergeRequest:
    return MergeRequest(
        id=mr.get("iid"),
        title=mr.get("title", ""),
        state=_normalize_state(mr.get("state")),
        source_branch=mr.get("source_branch"),
        target_branch=mr.get("target_branch"),
        author=(mr.get("author") or {}).get("username"),
        web_url=mr.get("web_url"),
        description=mr.get("description") or "",
        assignees=[a.get("username") for a in mr.get("assignees", [])],
        reviewers=[r.get("username") for r in mr.get("reviewers", [])],
        labels=list(mr.get("labels", [])),
        raw=mr,
    )


def _change(c: dict) -> Change:
    if c.get("new_file"):
        status = "added"
    elif c.get("deleted_file"):
        status = "deleted"
    elif c.get("renamed_file"):
        status = "renamed"
    else:
        status = "modified"
    return Change(
        status=status,
        old_path=c.get("old_path"),
        new_path=c.get("new_path"),
        diff=c.get("diff"),
    )


def _branch(b: dict) -> Branch:
    commit = b.get("commit") or {}
    return Branch(
        name=b.get("name", ""),
        protected=bool(b.get("protected", False)),
        default=bool(b.get("default", False)),
        merged=bool(b.get("merged", False)),
        commit_sha=(commit.get("id") or "")[:8] or None,
        commit_title=commit.get("title"),
        commit_author=commit.get("author_name"),
        raw=b,
    )


def _pipeline(p: dict, jobs: Optional[list] = None) -> Pipeline:
    return Pipeline(
        id=p.get("id"),
        status=p.get("status"),
        ref=p.get("ref"),
        sha=(p.get("sha") or "")[:8] or None,
        web_url=p.get("web_url"),
        jobs=[
            {"stage": j.get("stage"), "name": j.get("name"), "status": j.get("status")}
            for j in (jobs or [])
        ],
        raw=p,
    )


def _note(n: dict) -> Note:
    return Note(
        id=n.get("id"),
        body=n.get("body"),
        author=(n.get("author") or {}).get("username"),
        created_at=n.get("created_at"),
        updated_at=n.get("updated_at"),
        system=n.get("system"),
        resolvable=n.get("resolvable"),
        resolved=n.get("resolved"),
        position=n.get("position"),
        raw=n,
    )


class GitLabVcs(VcsClient):
    provider = "gitlab"

    def __init__(self, account: str = "default"):
        self.account = account
        self.client = GitLabClient(account=account)

    # -- identity -------------------------------------------------------
    def current_user(self) -> User:
        u = self.client.current_user()
        return User(
            username=u.get("username"),
            name=u.get("name"),
            email=u.get("email"),
            web_url=u.get("web_url"),
            raw=u,
        )

    # -- merge requests -------------------------------------------------
    def list_my_mrs(self, state: str = "opened") -> list[MergeRequest]:
        return [_mr(m) for m in self.client.list_my_mrs(state=state)]

    def list_mrs(
        self,
        repo: RepoRef,
        state: str = "opened",
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> list[MergeRequest]:
        pid = repo.require_project_id()
        return [
            _mr(m)
            for m in self.client.list_mrs(
                pid, state=state, source_branch=source_branch, target_branch=target_branch
            )
        ]

    def get_mr(self, repo: RepoRef, mr_id: int) -> MergeRequest:
        return _mr(self.client.get_mr(repo.require_project_id(), mr_id))

    def get_mr_changes(self, repo: RepoRef, mr_id: int) -> list[Change]:
        data = self.client.get_mr_changes(repo.require_project_id(), mr_id)
        return [_change(c) for c in data.get("changes", [])]

    def list_mr_notes(self, repo: RepoRef, mr_id: int) -> list[Note]:
        return [_note(n) for n in self.client.list_mr_notes(repo.require_project_id(), mr_id)]

    def list_mr_discussions(self, repo: RepoRef, mr_id: int) -> list[Discussion]:
        result = []
        for d in self.client.list_mr_discussions(repo.require_project_id(), mr_id):
            result.append(
                Discussion(
                    id=d.get("id"),
                    individual_note=d.get("individual_note"),
                    notes=[_note(n) for n in d.get("notes", [])],
                )
            )
        return result

    # -- branches -------------------------------------------------------
    def get_branch(self, repo: RepoRef, branch: str) -> Branch:
        return _branch(self.client.get_branch(repo.require_project_id(), branch))

    def list_branches(self, repo: RepoRef) -> list[Branch]:
        return [_branch(b) for b in self.client.list_branches(repo.require_project_id())]

    # -- CI -------------------------------------------------------------
    def list_pipelines(
        self,
        repo: RepoRef,
        ref: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Pipeline]:
        return [
            _pipeline(p)
            for p in self.client.list_pipelines(
                repo.require_project_id(), ref=ref, status=status, limit=limit
            )
        ]

    def get_pipeline(self, repo: RepoRef, pipeline_id: int) -> Pipeline:
        pid = repo.require_project_id()
        p = self.client.get_pipeline(pid, pipeline_id)
        jobs = self.client.list_pipeline_jobs(pid, pipeline_id)
        return _pipeline(p, jobs)

    # -- write (GitLab supports these; client enforces guards) ----------
    def create_mr(
        self,
        repo: RepoRef,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        assert_mr_title_is_source_branch(title, source_branch)
        return _mr(
            self.client.create_mr(
                repo.require_project_id(), source_branch, target_branch, title, description
            )
        )

    def comment_mr(self, repo: RepoRef, mr_id: int, body: str) -> Note:
        return _note(self.client.comment_mr(repo.require_project_id(), mr_id, body))

    def create_mr_discussion(
        self,
        repo: RepoRef,
        mr_id: int,
        body: str,
        file_path: str,
        line: int,
        line_type: str = "new",
    ) -> Discussion:
        if line_type not in ("new", "old"):
            raise ValueError("line_type must be 'new' or 'old'")
        pid = repo.require_project_id()
        diff_refs = self.client.get_mr_diff_refs(pid, mr_id)
        missing = [k for k in ("base_sha", "start_sha", "head_sha") if not diff_refs.get(k)]
        if missing:
            raise RuntimeError(f"Cannot create inline comment: missing diff refs {missing}")
        change = next(
            (
                c
                for c in self.client.get_mr_changes(pid, mr_id).get("changes", [])
                if c.get("new_path") == file_path or c.get("old_path") == file_path
            ),
            None,
        )
        if not change:
            raise RuntimeError(f"File is not present in MR diff: {file_path}")
        position = {
            "position_type": "text",
            "base_sha": diff_refs["base_sha"],
            "start_sha": diff_refs["start_sha"],
            "head_sha": diff_refs["head_sha"],
            "old_path": change.get("old_path") or file_path,
            "new_path": change.get("new_path") or file_path,
        }
        position["new_line" if line_type == "new" else "old_line"] = line
        d = self.client.create_mr_discussion(pid, mr_id, body, position)
        return Discussion(
            id=d.get("id"),
            individual_note=d.get("individual_note"),
            notes=[_note(n) for n in d.get("notes", [])],
        )
