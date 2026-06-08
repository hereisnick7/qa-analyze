"""
Provider-agnostic VCS layer (Epic A).

`VcsClient` (base.py) is the common interface; concrete providers live in
`modules/vcs/providers/`. `registry.get_vcs_client(provider, account)` is the
factory. Normalized result shapes live in `models.py`; repo identity in
`ref.py`.

GitLab remains the reference implementation and wraps the existing
`modules.gitlab.client.GitLabClient` unchanged — the abstraction is additive,
not a rewrite. GitOps QA deploy discovery (`modules.gitlab.gitops_qa`) stays
GitLab-only on purpose and is intentionally NOT part of this interface.
"""
