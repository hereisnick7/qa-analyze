"""
Repo-token resolution — the single place that turns a CLI/MCP repo argument
into a concrete (provider, account, RepoRef) triple.

Used by the CLI facade (`modules/gitlab/cli.py`), the unified VCS MCP server
(`modules/vcs/mcp_server.py`) and `shared/mcp_base.py`, so all three resolve a
repo the same way. Pure Python — no OS-specific behaviour, identical on Windows
and macOS.

A repo token is one of:
  - a project slug from `projects.yaml`               → "web-app"
  - a numeric GitLab project_id (back-compat)         → "42"
  - an "owner/repo" pair (github owner / bitbucket ws) → "acme/web-app"
  - None / "" (identity & "my MRs" calls that need no repo)

Provider/account precedence mirrors `resolve_provider` / `resolve_account`:
explicit arg → WORKFLOW_PROVIDER/WORKFLOW_ACCOUNT env → matched project's
`vcs.provider` / `vcs.account` → default ("gitlab" / "default").
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from modules.vcs.ref import RepoRef, parse_repo_arg, ref_from_binding


class RepoResolveError(Exception):
    """Raised when a repo token cannot be resolved to a provider/repo."""


def _is_numeric(value: str) -> bool:
    return value.isdigit()


def resolve_repo(
    repo: Optional[object] = None,
    *,
    provider: Optional[str] = None,
    account: Optional[str] = None,
) -> Tuple[str, str, Optional[RepoRef]]:
    """
    Resolve a repo token to ``(provider, account, RepoRef | None)``.

    ``provider`` / ``account`` are explicit overrides (e.g. ``--provider=`` /
    ``--account=`` flags) and take top precedence. A ``None`` / empty ``repo``
    returns ``ref=None`` for provider-level calls (``current_user`` /
    ``list_my_mrs``).
    """
    from shared.config_loader import get_project, resolve_account, resolve_provider

    # No repo token — identity / my-MRs style calls.
    if repo is None or str(repo).strip() == "":
        prov = resolve_provider(explicit=provider)
        acc = resolve_account("vcs", explicit=account)
        return prov, acc, None

    token = str(repo).strip()

    # 1. Numeric → GitLab project_id (back-compat path).
    if _is_numeric(token):
        project = get_project(gitlab_project_id=int(token))
        if project is not None:
            prov = resolve_provider(explicit=provider, slug=project.slug)
            acc = resolve_account("vcs", explicit=account, slug=project.slug)
            ref = ref_from_binding(project.vcs)
            if ref.project_id is None:
                ref.project_id = int(token)
            return prov, acc, ref
        # A bare numeric id is inherently a GitLab project_id.
        prov = (provider or "gitlab").lower()
        acc = resolve_account("vcs", explicit=account)
        return prov, acc, RepoRef(provider=prov, project_id=int(token))

    # 2. "owner/repo" → github/bitbucket identity.
    if "/" in token:
        project = get_project(vcs_owner_repo=token)
        if project is not None:
            prov = resolve_provider(explicit=provider, slug=project.slug)
            acc = resolve_account("vcs", explicit=account, slug=project.slug)
            return prov, acc, ref_from_binding(project.vcs)
        # Unmatched "owner/repo" is never GitLab (which uses numeric ids) — default
        # to github unless an explicit flag / WORKFLOW_PROVIDER says otherwise.
        prov = (provider or os.getenv("WORKFLOW_PROVIDER") or "github").lower()
        acc = resolve_account("vcs", explicit=account)
        return prov, acc, parse_repo_arg(prov, token)

    # 3. Project slug.
    project = get_project(slug=token)
    if project is not None:
        prov = resolve_provider(explicit=provider, slug=project.slug)
        acc = resolve_account("vcs", explicit=account, slug=project.slug)
        return prov, acc, ref_from_binding(project.vcs)

    raise RepoResolveError(
        f"Cannot resolve repo {token!r}: not a known project slug, not a numeric "
        f"GitLab project_id, and not an 'owner/repo' pair. Run `workflow info` to "
        f"see configured projects."
    )
