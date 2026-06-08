"""
VCS provider registry — the single factory for VcsClient instances.

`get_vcs_client(provider, account)` returns the right provider implementation.
Resolution of *which* provider/account to use for a given call lives in
`shared.config_loader.resolve_provider` / `resolve_account` (driven by the
active project's `vcs:` binding); this module only maps a resolved
(provider, account) pair to a client.
"""

from __future__ import annotations

from modules.vcs.base import VcsClient

SUPPORTED_PROVIDERS = ("gitlab", "github", "bitbucket")


def get_vcs_client(provider: str = "gitlab", account: str = "default") -> VcsClient:
    provider = (provider or "gitlab").lower()
    if provider == "gitlab":
        from modules.vcs.providers.gitlab import GitLabVcs
        return GitLabVcs(account=account)
    if provider == "github":
        from modules.vcs.providers.github import GitHubVcs
        return GitHubVcs(account=account)
    if provider == "bitbucket":
        from modules.vcs.providers.bitbucket import BitBucketVcs
        return BitBucketVcs(account=account)
    raise ValueError(
        f"Unknown VCS provider: {provider!r}. Supported: {SUPPORTED_PROVIDERS}"
    )
