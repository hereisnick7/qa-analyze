"""
Normalized VCS result shapes.

Every provider maps its native JSON onto these dataclasses so CLI/MCP/agents
get one stable shape regardless of GitLab vs GitHub vs BitBucket. Vocabulary is
GitLab-leaning by precedent (the original implementation): an MR is a "merge
request" even when GitHub calls it a pull request; `state` is normalized to
{opened, closed, merged, locked}.

Each model keeps a `raw` dict with the untouched provider payload for callers
that need a field the normalized shape does not surface yet. `as_dict()`
returns a JSON-serializable view WITHOUT `raw` (so MCP/CLI output stays lean);
pass `include_raw=True` when the raw payload is actually needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def _normalize_state(state: Optional[str], merged: bool = False) -> str:
    """Map provider-specific MR/PR states onto a common vocabulary."""
    if merged:
        return "merged"
    s = (state or "").lower()
    if s in ("opened", "open"):
        return "opened"
    if s in ("closed", "declined", "superseded"):
        return "closed"
    if s == "merged":
        return "merged"
    if s == "locked":
        return "locked"
    return s or "unknown"


@dataclass
class _Model:
    def as_dict(self, include_raw: bool = False) -> dict:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data


@dataclass
class User(_Model):
    username: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    web_url: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class MergeRequest(_Model):
    id: Optional[int] = None            # gitlab iid / github+bitbucket number
    title: str = ""
    state: str = ""                     # normalized via _normalize_state
    source_branch: Optional[str] = None
    target_branch: Optional[str] = None
    author: Optional[str] = None
    web_url: Optional[str] = None
    description: str = ""
    assignees: list = field(default_factory=list)
    reviewers: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class Change(_Model):
    status: str = "modified"            # added | modified | deleted | renamed
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    diff: Optional[str] = None          # unified diff body (no file header)


@dataclass
class Branch(_Model):
    name: str = ""
    protected: bool = False
    default: bool = False
    merged: bool = False
    commit_sha: Optional[str] = None
    commit_title: Optional[str] = None
    commit_author: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Pipeline(_Model):
    """A CI run — GitLab pipeline / GitHub Actions run / BitBucket pipeline."""
    id: Optional[int] = None
    status: Optional[str] = None        # provider status/conclusion as-is
    ref: Optional[str] = None
    sha: Optional[str] = None
    web_url: Optional[str] = None
    jobs: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class Note(_Model):
    id: Optional[Any] = None
    body: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    system: Optional[bool] = None
    resolvable: Optional[bool] = None
    resolved: Optional[bool] = None
    position: Optional[dict] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Discussion(_Model):
    id: Optional[Any] = None
    individual_note: Optional[bool] = None
    notes: list = field(default_factory=list)  # list[Note]

    def as_dict(self, include_raw: bool = False) -> dict:
        return {
            "id": self.id,
            "individual_note": self.individual_note,
            "notes": [
                n.as_dict(include_raw=include_raw) if isinstance(n, Note) else n
                for n in self.notes
            ],
        }
