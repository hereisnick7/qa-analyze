from shared.config import (
    PROTECTED_ENVIRONMENTS,
    ALLOWED_QA_SLOTS,
)

# Provider-agnostic guards live in `modules.vcs.guards` (the canonical home so a
# second provider can reuse them). Re-exported here so `modules.gitlab.client`
# and the existing tests keep importing them from this module unchanged.
from modules.vcs.guards import (  # noqa: F401
    GuardError,
    ProtectedRefError,
    AgentAttributionForbiddenError,
    assert_not_protected_ref,
    assert_no_agent_attribution,
)


class ProtectedEnvironmentError(GuardError):
    pass


class InvalidSlotError(GuardError):
    pass


class MrStateChangeForbiddenError(GuardError):
    pass


class JiraWriteTextError(GuardError):
    pass


def assert_not_protected_environment(env: str) -> None:
    if env in PROTECTED_ENVIRONMENTS:
        raise ProtectedEnvironmentError(f"Environment '{env}' is protected")


def assert_valid_qa_slot(slot: str) -> None:
    if slot not in ALLOWED_QA_SLOTS:
        raise InvalidSlotError(
            f"Invalid QA slot '{slot}'. Allowed: {sorted(ALLOWED_QA_SLOTS)}"
        )


def assert_no_mr_state_change(state_event) -> None:
    """Блокирует слияние и закрытие MR через update_mr.

    Открыть MR в любую ветку (включая master/main/release) — можно.
    Менять состояние MR (merge, close, reopen) через агента — нельзя:
    эти действия выполняются вручную.
    """
    if state_event is not None:
        raise MrStateChangeForbiddenError(
            f"Changing MR state via agent is forbidden (got state_event={state_event!r}); "
            "merging and closing MRs must be done manually"
        )


def assert_valid_jira_write_text(text: str, max_chars: int = 10_000) -> None:
    """Validate user-visible Jira write text before any HTTP write request."""
    if text is None or not str(text).strip():
        raise JiraWriteTextError("Jira write text must not be empty")
    if len(text) > max_chars:
        raise JiraWriteTextError(
            f"Jira write text is too long ({len(text)} chars); max is {max_chars}"
        )
    try:
        assert_no_agent_attribution(text)
    except AgentAttributionForbiddenError as exc:
        raise JiraWriteTextError(
            "Jira comments/descriptions must not mention Claude/AI; "
            "actions are performed under the user's account"
        ) from exc
