"""
BitBucket provider — BitBucket Cloud REST API 2.0 (Epic A, phase 4).

Maps BitBucket Cloud onto the VcsClient interface. Vocabulary bridges BitBucket
to the GitLab-leaning models: a pull request is a `MergeRequest`, a Pipelines
run is a `Pipeline`, conversation comments are notes, inline comments are grouped
into discussions (threads). Repo identity is `owner/repo` where owner == the
workspace and repo == the repo_slug.

Auth/host (config/bitbucket/.env, bare or BITBUCKET__<ACCOUNT>__*):
  - Bearer:  BITBUCKET_TOKEN            (workspace / repo / project access token)
  - Basic:   BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD   (app password)
             (or BITBUCKET_EMAIL + BITBUCKET_API_TOKEN — same Basic scheme)
  - host:    default https://api.bitbucket.org/2.0; override with
             BITBUCKET_API_URL (full 2.0 base). Targets BitBucket **Cloud**;
             self-hosted Data Center (a different REST API) is out of scope.

Write methods (create_mr / comment_mr / create_mr_discussion) are gated by
BITBUCKET_WRITE_ENABLED (default off) plus the provider-agnostic guards in
`modules.vcs.guards` — opt-in, only on explicit request.
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

# Common vocab → BitBucket PR states (BitBucket uses upper-case).
_STATE_TO_BB = {
    "opened": "OPEN",
    "open": "OPEN",
    "closed": "DECLINED",
    "declined": "DECLINED",
    "merged": "MERGED",
    "all": None,
}


def _default_api_base(env: dict) -> str:
    explicit = env.get("BITBUCKET_API_URL")
    if explicit:
        return explicit.rstrip("/")
    return "https://api.bitbucket.org/2.0"


class BitBucketVcs(VcsClient):
    provider = "bitbucket"

    def __init__(self, account: str = "default"):
        env = load_module_env("bitbucket", account)
        self.account = account
        self.api_base = _default_api_base(env)
        self.session = requests.Session()
        token = env.get("BITBUCKET_TOKEN", "")
        user = env.get("BITBUCKET_USERNAME") or env.get("BITBUCKET_EMAIL")
        secret = env.get("BITBUCKET_APP_PASSWORD") or env.get("BITBUCKET_API_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif user and secret:
            self.session.auth = (user, secret)
        else:
            raise RuntimeError(
                f"Set BITBUCKET_TOKEN (or BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD) "
                f"for account '{account}' in config/bitbucket/.env (bare key, or "
                f"BITBUCKET__{account.upper()}__TOKEN for multi-account)"
            )
        self.session.headers.setdefault("Accept", "application/json")
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

    def _get_text(self, path: str, params: Optional[dict] = None) -> str:
        resp = self.session.get(f"{self.api_base}{path}", params=params, timeout=(5, 30))
        resp.raise_for_status()
        return resp.text

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        resp = self.session.post(f"{self.api_base}{path}", json=json, timeout=(5, 15))
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, path: str, params: Optional[dict] = None, max_pages: int = 5) -> list:
        """Follow BitBucket's `next` cursor (page envelopes: {values, next})."""
        params = dict(params or {})
        params.setdefault("pagelen", 50)
        results: list = []
        data = self._get(path, params)
        for _ in range(max_pages):
            results.extend(data.get("values", []))
            nxt = data.get("next")
            if not nxt:
                break
            # `next` is an absolute URL; strip the api_base to reuse the session.
            data = self._get(nxt[len(self.api_base):] if nxt.startswith(self.api_base) else nxt)
        return results

    def _default_branch(self, owner: str, repo: str) -> Optional[str]:
        key = f"{owner}/{repo}"
        if key not in self._default_branch_cache:
            try:
                info = self._get(f"/repositories/{owner}/{repo}")
                self._default_branch_cache[key] = (info.get("mainbranch") or {}).get("name")
            except Exception:
                self._default_branch_cache[key] = None
        return self._default_branch_cache[key]

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def current_user(self) -> User:
        u = self._get("/user")
        return User(
            username=u.get("username") or u.get("nickname"),
            name=u.get("display_name"),
            email=u.get("email"),
            web_url=((u.get("links") or {}).get("html") or {}).get("href"),
            raw=u,
        )

    # ------------------------------------------------------------------
    # Merge / pull requests
    # ------------------------------------------------------------------
    def list_my_mrs(self, state: str = "opened") -> list[MergeRequest]:
        uuid = self._get("/user").get("uuid")
        params: dict = {}
        bb_state = _STATE_TO_BB.get((state or "opened").lower(), "OPEN")
        if bb_state:
            params["state"] = bb_state
        # /2.0/pullrequests/{selected_user}: PRs authored by the user.
        return [_mr(p) for p in self._paginate(f"/pullrequests/{quote(uuid, safe='')}", params)]

    def list_mrs(
        self,
        repo: RepoRef,
        state: str = "opened",
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> list[MergeRequest]:
        owner, name = repo.require_owner_repo()
        params: dict = {}
        bb_state = _STATE_TO_BB.get((state or "opened").lower(), "OPEN")
        if bb_state:
            params["state"] = bb_state
        clauses = []
        if source_branch:
            clauses.append(f'source.branch.name="{source_branch}"')
        if target_branch:
            clauses.append(f'destination.branch.name="{target_branch}"')
        if clauses:
            params["q"] = " AND ".join(clauses)
        return [_mr(p) for p in self._paginate(f"/repositories/{owner}/{name}/pullrequests", params)]

    def get_mr(self, repo: RepoRef, mr_id: int) -> MergeRequest:
        owner, name = repo.require_owner_repo()
        return _mr(self._get(f"/repositories/{owner}/{name}/pullrequests/{mr_id}"))

    def get_mr_changes(self, repo: RepoRef, mr_id: int) -> list[Change]:
        owner, name = repo.require_owner_repo()
        diffstat = self._paginate(f"/repositories/{owner}/{name}/pullrequests/{mr_id}/diffstat")
        try:
            diff_text = self._get_text(f"/repositories/{owner}/{name}/pullrequests/{mr_id}/diff")
        except Exception:
            diff_text = ""
        per_file = _split_unified_diff(diff_text)
        return [_change(d, per_file) for d in diffstat]

    def list_mr_notes(self, repo: RepoRef, mr_id: int) -> list[Note]:
        owner, name = repo.require_owner_repo()
        comments = self._paginate(f"/repositories/{owner}/{name}/pullrequests/{mr_id}/comments")
        # Conversation notes = comments without an inline anchor and not deleted.
        return [_comment(c) for c in comments if not c.get("inline") and not c.get("deleted")]

    def list_mr_discussions(self, repo: RepoRef, mr_id: int) -> list[Discussion]:
        owner, name = repo.require_owner_repo()
        comments = self._paginate(f"/repositories/{owner}/{name}/pullrequests/{mr_id}/comments")
        inline = [c for c in comments if c.get("inline") and not c.get("deleted")]
        return _group_inline_threads(inline)

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------
    def get_branch(self, repo: RepoRef, branch: str) -> Branch:
        owner, name = repo.require_owner_repo()
        b = self._get(f"/repositories/{owner}/{name}/refs/branches/{quote(branch, safe='')}")
        return _branch(b, default_branch=self._default_branch(owner, name))

    def list_branches(self, repo: RepoRef) -> list[Branch]:
        owner, name = repo.require_owner_repo()
        default = self._default_branch(owner, name)
        return [
            _branch(b, default_branch=default)
            for b in self._paginate(f"/repositories/{owner}/{name}/refs/branches")
        ]

    # ------------------------------------------------------------------
    # CI (BitBucket Pipelines)
    # ------------------------------------------------------------------
    def list_pipelines(
        self,
        repo: RepoRef,
        ref: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Pipeline]:
        owner, name = repo.require_owner_repo()
        params: dict = {"sort": "-created_on", "pagelen": min(limit or 50, 100)}
        if ref:
            params["target.ref_name"] = ref
        runs = self._paginate(f"/repositories/{owner}/{name}/pipelines/", params, max_pages=1)
        if limit:
            runs = runs[:limit]
        return [_pipeline(r) for r in runs]

    def get_pipeline(self, repo: RepoRef, pipeline_id: int) -> Pipeline:
        owner, name = repo.require_owner_repo()
        run = self._get(f"/repositories/{owner}/{name}/pipelines/{pipeline_id}")
        try:
            steps = self._paginate(
                f"/repositories/{owner}/{name}/pipelines/{pipeline_id}/steps/", max_pages=1
            )
        except Exception:
            steps = []
        return _pipeline(run, steps)

    # ------------------------------------------------------------------
    # Write — gated by BITBUCKET_WRITE_ENABLED (default off) + shared guards
    # ------------------------------------------------------------------
    def create_mr(
        self,
        repo: RepoRef,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        assert_write_enabled("bitbucket")
        assert_mr_title_is_source_branch(title, source_branch)
        assert_no_agent_attribution(title)
        assert_no_agent_attribution(description)
        owner, name = repo.require_owner_repo()
        pr = self._post(
            f"/repositories/{owner}/{name}/pullrequests",
            {
                "title": title,
                "source": {"branch": {"name": source_branch}},
                "destination": {"branch": {"name": target_branch}},
                "description": description,
            },
        )
        return _mr(pr)

    def comment_mr(self, repo: RepoRef, mr_id: int, body: str) -> Note:
        assert_write_enabled("bitbucket")
        assert_no_agent_attribution(body)
        owner, name = repo.require_owner_repo()
        c = self._post(
            f"/repositories/{owner}/{name}/pullrequests/{mr_id}/comments",
            {"content": {"raw": body}},
        )
        return _comment(c)

    def create_mr_discussion(
        self,
        repo: RepoRef,
        mr_id: int,
        body: str,
        file_path: str,
        line: int,
        line_type: str = "new",
    ) -> Discussion:
        assert_write_enabled("bitbucket")
        assert_no_agent_attribution(body)
        if line_type not in ("new", "old"):
            raise ValueError("line_type must be 'new' or 'old'")
        owner, name = repo.require_owner_repo()
        # BitBucket inline anchor: `to` = line in the new file, `from` = old file.
        inline = {"path": file_path, ("to" if line_type == "new" else "from"): line}
        c = self._post(
            f"/repositories/{owner}/{name}/pullrequests/{mr_id}/comments",
            {"content": {"raw": body}, "inline": inline},
        )
        return Discussion(id=c.get("id"), individual_note=True, notes=[_comment(c)])


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------

def _author_name(actor: Optional[dict]) -> Optional[str]:
    actor = actor or {}
    return actor.get("nickname") or actor.get("display_name") or actor.get("username")


def _mr(pr: dict) -> MergeRequest:
    return MergeRequest(
        id=pr.get("id"),
        title=pr.get("title", ""),
        state=_normalize_state(pr.get("state")),
        source_branch=((pr.get("source") or {}).get("branch") or {}).get("name"),
        target_branch=((pr.get("destination") or {}).get("branch") or {}).get("name"),
        author=_author_name(pr.get("author")),
        web_url=((pr.get("links") or {}).get("html") or {}).get("href"),
        description=pr.get("description") or "",
        reviewers=[_author_name(r) for r in pr.get("reviewers", [])],
        raw=pr,
    )


def _change(d: dict, per_file: Optional[dict] = None) -> Change:
    status_map = {"removed": "deleted", "added": "added", "renamed": "renamed", "modified": "modified"}
    status = status_map.get(d.get("status"), d.get("status") or "modified")
    new_path = (d.get("new") or {}).get("path")
    old_path = (d.get("old") or {}).get("path")
    diff = None
    if per_file:
        diff = per_file.get(new_path) or per_file.get(old_path)
    return Change(
        status=status,
        old_path=old_path or new_path,
        new_path=new_path or old_path,
        diff=diff,
    )


def _branch(b: dict, default_branch: Optional[str] = None) -> Branch:
    target = b.get("target") or {}
    message = target.get("message") or ""
    return Branch(
        name=b.get("name", ""),
        protected=False,  # branch-restrictions API is separate/permission-heavy
        default=bool(default_branch and b.get("name") == default_branch),
        merged=False,
        commit_sha=(target.get("hash") or "")[:8] or None,
        commit_title=message.splitlines()[0] if message else None,
        commit_author=_author_name((target.get("author") or {}).get("user")),
        raw=b,
    )


def _pipeline(run: dict, steps: Optional[list] = None) -> Pipeline:
    state = run.get("state") or {}
    status = (state.get("result") or {}).get("name") or state.get("name")
    target = run.get("target") or {}
    sha = ((target.get("commit") or {}).get("hash") or "")[:8] or None
    return Pipeline(
        id=run.get("build_number"),
        status=status,
        ref=target.get("ref_name"),
        sha=sha,
        web_url=((run.get("links") or {}).get("self") or {}).get("href"),
        jobs=[
            {
                "stage": None,
                "name": s.get("name"),
                "status": ((s.get("state") or {}).get("result") or {}).get("name")
                or (s.get("state") or {}).get("name"),
            }
            for s in (steps or [])
        ],
        raw=run,
    )


def _comment(c: dict) -> Note:
    inline = c.get("inline")
    position = None
    if inline:
        position = {
            "new_path": inline.get("path"),
            "new_line": inline.get("to"),
            "old_line": inline.get("from"),
        }
    return Note(
        id=c.get("id"),
        body=(c.get("content") or {}).get("raw"),
        author=_author_name(c.get("user")),
        created_at=c.get("created_on"),
        updated_at=c.get("updated_on"),
        system=False,
        resolvable=bool(inline),
        position=position,
        raw=c,
    )


def _group_inline_threads(comments: list) -> list[Discussion]:
    """Group inline comments into threads via `parent.id` (root has no parent)."""
    threads: dict = {}
    order: list = []
    for c in comments:
        parent = (c.get("parent") or {}).get("id")
        root_id = parent if parent in threads else c.get("id")
        if root_id not in threads:
            threads[root_id] = []
            order.append(root_id)
        threads[root_id].append(_comment(c))
    return [
        Discussion(id=rid, individual_note=len(threads[rid]) == 1, notes=threads[rid])
        for rid in order
    ]


def _split_unified_diff(text: str) -> dict:
    """Split a multi-file unified diff into {path: hunk-text}.

    Keyed by both the old (a/) and new (b/) paths so a Change can look up its
    body by whichever path it carries. Best-effort: returns {} on empty input.
    """
    if not text:
        return {}
    out: dict = {}
    current_paths: list = []
    current_lines: list = []

    def _flush():
        if current_paths and current_lines:
            body = "\n".join(current_lines)
            for p in current_paths:
                if p:
                    out[p] = body

    for line in text.splitlines():
        if line.startswith("diff --git "):
            _flush()
            current_lines = [line]
            current_paths = []
            # "diff --git a/old b/new"
            parts = line.split(" ")
            if len(parts) >= 4:
                a = parts[2][2:] if parts[2].startswith("a/") else parts[2]
                b = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                current_paths = [a, b]
        else:
            current_lines.append(line)
    _flush()
    return out
