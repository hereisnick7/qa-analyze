"""
GitHub provider — read-only first cut (Epic A).

Maps GitHub's REST v3 onto the VcsClient interface. Vocabulary bridges GitHub
to the GitLab-leaning models: a pull request is a `MergeRequest`, an Actions
workflow run is a `Pipeline`, PR conversation comments are notes, inline review
comments are grouped into discussions (threads).

Auth/host:
  - token:  GITHUB_TOKEN (or GITHUB__<ACCOUNT>__TOKEN) in config/github/.env
  - host:   default api.github.com; GitHub Enterprise via GITHUB_API_URL
            (full base, e.g. https://ghe.example.com/api/v3) or GITHUB_HOST.

Write methods (create_mr / comment_mr / create_mr_discussion) are implemented on
REST v3 and gated by GITHUB_WRITE_ENABLED (default off) plus the provider-agnostic
guards in `modules.vcs.guards` — opt-in, only on explicit request.
"""

from __future__ import annotations

from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib.parse import quote
from urllib3.util.retry import Retry

from shared.config_loader import load_module_env
from modules.vcs.base import VcsClient
from modules.vcs.guards import (
    assert_mr_title_is_source_branch,
    assert_no_agent_attribution,
    assert_write_enabled,
)
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

_API_VERSION = "2022-11-28"

# GitHub PR/issue states map onto the common vocabulary.
_STATE_TO_GITHUB = {"opened": "open", "open": "open", "closed": "closed", "all": "all"}


def _default_api_base(env: dict) -> str:
    explicit = env.get("GITHUB_API_URL")
    if explicit:
        return explicit.rstrip("/")
    host = env.get("GITHUB_HOST")
    if host and host not in ("github.com", "api.github.com"):
        return f"https://{host}/api/v3"
    return "https://api.github.com"


class GitHubVcs(VcsClient):
    provider = "github"

    def __init__(self, account: str = "default"):
        env = load_module_env("github", account)
        token = env.get("GITHUB_TOKEN", "")
        if not token:
            raise RuntimeError(
                f"Set GITHUB_TOKEN for account '{account}' in config/github/.env "
                f"(bare key, or GITHUB__{account.upper()}__TOKEN for multi-account)"
            )
        self.account = account
        self.api_base = _default_api_base(env)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
            }
        )
        adapter = HTTPAdapter(
            max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504]),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._default_branch_cache: dict = {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self.session.get(f"{self.api_base}{path}", params=params, timeout=(5, 15))
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        resp = self.session.post(f"{self.api_base}{path}", json=json, timeout=(5, 15))
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, path: str, params: Optional[dict] = None, max_pages: int = 5) -> list:
        params = dict(params or {})
        params.setdefault("per_page", 50)
        results: list = []
        page = 1
        while page <= max_pages:
            params["page"] = page
            batch = self._get(path, params)
            if not batch:
                break
            results.extend(batch)
            if len(batch) < params["per_page"]:
                break
            page += 1
        return results

    def _default_branch(self, owner: str, repo: str) -> Optional[str]:
        key = f"{owner}/{repo}"
        if key not in self._default_branch_cache:
            try:
                self._default_branch_cache[key] = self._get(f"/repos/{owner}/{repo}").get(
                    "default_branch"
                )
            except Exception:
                self._default_branch_cache[key] = None
        return self._default_branch_cache[key]

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def current_user(self) -> User:
        u = self._get("/user")
        return User(
            username=u.get("login"),
            name=u.get("name"),
            email=u.get("email"),
            web_url=u.get("html_url"),
            raw=u,
        )

    # ------------------------------------------------------------------
    # Merge / pull requests
    # ------------------------------------------------------------------
    def list_my_mrs(self, state: str = "opened") -> list[MergeRequest]:
        login = self._get("/user").get("login")
        q = f"is:pr author:{login}"
        gh_state = _STATE_TO_GITHUB.get((state or "opened").lower())
        if gh_state in ("open", "closed"):
            q += f" state:{gh_state}"
        data = self._get("/search/issues", {"q": q, "per_page": 50})
        return [_mr_from_search(item) for item in data.get("items", [])]

    def list_mrs(
        self,
        repo: RepoRef,
        state: str = "opened",
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> list[MergeRequest]:
        owner, name = repo.require_owner_repo()
        params: dict = {"state": _STATE_TO_GITHUB.get((state or "opened").lower(), "open")}
        if source_branch:
            params["head"] = f"{owner}:{source_branch}"
        if target_branch:
            params["base"] = target_branch
        return [_mr(p) for p in self._paginate(f"/repos/{owner}/{name}/pulls", params)]

    def get_mr(self, repo: RepoRef, mr_id: int) -> MergeRequest:
        owner, name = repo.require_owner_repo()
        return _mr(self._get(f"/repos/{owner}/{name}/pulls/{mr_id}"))

    def get_mr_changes(self, repo: RepoRef, mr_id: int) -> list[Change]:
        owner, name = repo.require_owner_repo()
        files = self._paginate(f"/repos/{owner}/{name}/pulls/{mr_id}/files")
        return [_change(f) for f in files]

    def list_mr_notes(self, repo: RepoRef, mr_id: int) -> list[Note]:
        owner, name = repo.require_owner_repo()
        comments = self._paginate(f"/repos/{owner}/{name}/issues/{mr_id}/comments")
        return [_issue_comment(c) for c in comments]

    def list_mr_discussions(self, repo: RepoRef, mr_id: int) -> list[Discussion]:
        owner, name = repo.require_owner_repo()
        comments = self._paginate(f"/repos/{owner}/{name}/pulls/{mr_id}/comments")
        return _group_review_threads(comments)

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------
    def get_branch(self, repo: RepoRef, branch: str) -> Branch:
        owner, name = repo.require_owner_repo()
        b = self._get(f"/repos/{owner}/{name}/branches/{quote(branch, safe='')}")
        return _branch(b, default_branch=self._default_branch(owner, name))

    def list_branches(self, repo: RepoRef) -> list[Branch]:
        owner, name = repo.require_owner_repo()
        default = self._default_branch(owner, name)
        return [_branch(b, default_branch=default) for b in self._paginate(f"/repos/{owner}/{name}/branches")]

    # ------------------------------------------------------------------
    # CI (GitHub Actions)
    # ------------------------------------------------------------------
    def list_pipelines(
        self,
        repo: RepoRef,
        ref: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Pipeline]:
        owner, name = repo.require_owner_repo()
        params: dict = {"per_page": min(limit or 50, 100)}
        if ref:
            params["branch"] = ref
        if status:
            params["status"] = status
        data = self._get(f"/repos/{owner}/{name}/actions/runs", params)
        runs = data.get("workflow_runs", [])
        if limit:
            runs = runs[:limit]
        return [_run(r) for r in runs]

    def get_pipeline(self, repo: RepoRef, pipeline_id: int) -> Pipeline:
        owner, name = repo.require_owner_repo()
        run = self._get(f"/repos/{owner}/{name}/actions/runs/{pipeline_id}")
        jobs_data = self._get(f"/repos/{owner}/{name}/actions/runs/{pipeline_id}/jobs")
        jobs = jobs_data.get("jobs", [])
        return _run(run, jobs)

    # ------------------------------------------------------------------
    # Write — gated by GITHUB_WRITE_ENABLED (default off) + shared guards
    # ------------------------------------------------------------------
    def create_mr(
        self,
        repo: RepoRef,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        assert_write_enabled("github")
        assert_mr_title_is_source_branch(title, source_branch)
        assert_no_agent_attribution(title)
        assert_no_agent_attribution(description)
        owner, name = repo.require_owner_repo()
        pr = self._post(
            f"/repos/{owner}/{name}/pulls",
            {
                "title": title,
                "head": source_branch,
                "base": target_branch,
                "body": description,
            },
        )
        return _mr(pr)

    def comment_mr(self, repo: RepoRef, mr_id: int, body: str) -> Note:
        assert_write_enabled("github")
        assert_no_agent_attribution(body)
        owner, name = repo.require_owner_repo()
        # PR conversation comments are issue comments on GitHub.
        c = self._post(f"/repos/{owner}/{name}/issues/{mr_id}/comments", {"body": body})
        return _issue_comment(c)

    def create_mr_discussion(
        self,
        repo: RepoRef,
        mr_id: int,
        body: str,
        file_path: str,
        line: int,
        line_type: str = "new",
    ) -> Discussion:
        assert_write_enabled("github")
        assert_no_agent_attribution(body)
        if line_type not in ("new", "old"):
            raise ValueError("line_type must be 'new' or 'old'")
        owner, name = repo.require_owner_repo()
        head_sha = (self._get(f"/repos/{owner}/{name}/pulls/{mr_id}").get("head") or {}).get("sha")
        if not head_sha:
            raise RuntimeError(f"Cannot create inline comment: PR #{mr_id} has no head SHA")
        c = self._post(
            f"/repos/{owner}/{name}/pulls/{mr_id}/comments",
            {
                "body": body,
                "commit_id": head_sha,
                "path": file_path,
                "line": line,
                "side": "RIGHT" if line_type == "new" else "LEFT",
            },
        )
        note = _review_comment(c)
        return Discussion(id=c.get("id"), individual_note=True, notes=[note])


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------

def _mr(pr: dict) -> MergeRequest:
    return MergeRequest(
        id=pr.get("number"),
        title=pr.get("title", ""),
        state=_normalize_state(pr.get("state"), merged=bool(pr.get("merged") or pr.get("merged_at"))),
        source_branch=(pr.get("head") or {}).get("ref"),
        target_branch=(pr.get("base") or {}).get("ref"),
        author=(pr.get("user") or {}).get("login"),
        web_url=pr.get("html_url"),
        description=pr.get("body") or "",
        assignees=[a.get("login") for a in pr.get("assignees", [])],
        reviewers=[r.get("login") for r in pr.get("requested_reviewers", [])],
        labels=[l.get("name") for l in pr.get("labels", [])],
        raw=pr,
    )


def _mr_from_search(item: dict) -> MergeRequest:
    """Search results lack head/base refs; map what is available."""
    return MergeRequest(
        id=item.get("number"),
        title=item.get("title", ""),
        state=_normalize_state(item.get("state"), merged=bool((item.get("pull_request") or {}).get("merged_at"))),
        author=(item.get("user") or {}).get("login"),
        web_url=item.get("html_url"),
        description=item.get("body") or "",
        labels=[l.get("name") for l in item.get("labels", [])],
        raw=item,
    )


def _change(f: dict) -> Change:
    status_map = {"removed": "deleted", "changed": "modified"}
    status = status_map.get(f.get("status"), f.get("status") or "modified")
    return Change(
        status=status,
        old_path=f.get("previous_filename") or f.get("filename"),
        new_path=f.get("filename"),
        diff=f.get("patch"),
    )


def _branch(b: dict, default_branch: Optional[str] = None) -> Branch:
    commit = b.get("commit") or {}
    inner = commit.get("commit") or {}
    message = inner.get("message") or ""
    return Branch(
        name=b.get("name", ""),
        protected=bool(b.get("protected", False)),
        default=bool(default_branch and b.get("name") == default_branch),
        merged=False,  # GitHub branch listing does not expose a merged flag
        commit_sha=(commit.get("sha") or "")[:8] or None,
        commit_title=message.splitlines()[0] if message else None,
        commit_author=(inner.get("author") or {}).get("name"),
        raw=b,
    )


def _run(run: dict, jobs: Optional[list] = None) -> Pipeline:
    # Prefer the terminal conclusion (success/failure/...) when the run finished.
    status = run.get("conclusion") or run.get("status")
    return Pipeline(
        id=run.get("id"),
        status=status,
        ref=run.get("head_branch"),
        sha=(run.get("head_sha") or "")[:8] or None,
        web_url=run.get("html_url"),
        jobs=[
            {"stage": None, "name": j.get("name"), "status": j.get("conclusion") or j.get("status")}
            for j in (jobs or [])
        ],
        raw=run,
    )


def _issue_comment(c: dict) -> Note:
    return Note(
        id=c.get("id"),
        body=c.get("body"),
        author=(c.get("user") or {}).get("login"),
        created_at=c.get("created_at"),
        updated_at=c.get("updated_at"),
        system=False,
        raw=c,
    )


def _review_comment(c: dict) -> Note:
    return Note(
        id=c.get("id"),
        body=c.get("body"),
        author=(c.get("user") or {}).get("login"),
        created_at=c.get("created_at"),
        updated_at=c.get("updated_at"),
        system=False,
        resolvable=True,
        position={
            "new_path": c.get("path"),
            "new_line": c.get("line") if c.get("line") is not None else c.get("original_line"),
        },
        raw=c,
    )


def _group_review_threads(comments: list) -> list[Discussion]:
    """
    Group flat review comments into threads (discussions).

    A comment with no `in_reply_to_id` starts a thread; replies attach to the
    thread rooted at their `in_reply_to_id`. Order is preserved.
    """
    threads: dict = {}
    order: list = []
    for c in comments:
        reply_to = c.get("in_reply_to_id")
        root_id = reply_to if reply_to in threads else c.get("id")
        if root_id not in threads:
            threads[root_id] = []
            order.append(root_id)
        threads[root_id].append(_review_comment(c))
    discussions = []
    for root_id in order:
        notes = threads[root_id]
        discussions.append(
            Discussion(id=root_id, individual_note=len(notes) == 1, notes=notes)
        )
    return discussions
