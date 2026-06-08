"""
VCS CLI commands (historically GitLab; now provider-agnostic). Called from
cli/agent.py dispatcher. Each command handler is a standalone function;
run_command() returns True if the command was handled, False if it belongs to
another module.

Read commands (and the MR write trio) route through the provider-agnostic
`modules.vcs` layer: `resolve_repo()` turns the repo token (project slug /
numeric GitLab project_id / "owner/repo") into a (provider, account, RepoRef),
and `get_vcs_client()` returns the right client. Formatters consume the
normalized models, so GitLab output is unchanged and GitHub works through the
same handlers.

GitLab-only capabilities (project listing, environments, CI config, project
rules, deep pipeline/job introspection, GitOps QA deploy, MR metadata edit)
stay on the raw `GitLabClient` and refuse on non-GitLab providers with a clear
message. Pure Python — identical behaviour on Windows and macOS.
"""

import sys
import argparse
from pathlib import Path

_WORKFLOW_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKFLOW_ROOT))

from modules.gitlab.client import GitLabClient  # noqa: E402
from modules.gitlab.guards import assert_valid_qa_slot, InvalidSlotError, MrStateChangeForbiddenError  # noqa: E402
from modules.vcs.registry import get_vcs_client  # noqa: E402
from modules.vcs.resolve import resolve_repo, RepoResolveError  # noqa: E402
from modules.vcs.base import VcsWriteNotSupported  # noqa: E402
from modules.vcs.guards import VcsWriteDisabledError  # noqa: E402

_clients: dict = {}
_active_account = "default"


def _get_client(account: str | None = None) -> GitLabClient:
    """Return a GitLabClient for `account` (or the active one), cached per account."""
    acc = account or _active_account
    if acc not in _clients:
        _clients[acc] = GitLabClient(account=acc)
    return _clients[acc]


def _require_gitlab(provider: str, cmd: str) -> bool:
    """Gate a GitLab-only command. Prints a clear refusal on other providers."""
    if provider != "gitlab":
        print(
            f"\n  '{cmd}' is GitLab-only (GitOps QA / deep CI / project APIs) and is "
            f"not available for provider '{provider}'.\n"
        )
        return False
    return True


# ------------------------------------------------------------------
# Formatting — normalized models (modules.vcs.models)
# ------------------------------------------------------------------

def fmt_mr(mr) -> str:
    """Format a normalized MergeRequest."""
    labels = ", ".join(mr.labels) or "—"
    assignees = ", ".join(mr.assignees) or "—"
    reviewers = ", ".join(mr.reviewers) or "—"
    source = mr.source_branch or "?"
    target = mr.target_branch or "?"
    return (
        f"  !{mr.id}  [{(mr.state or '?').upper()}]  {mr.title}\n"
        f"       Ветка: {source} → {target}\n"
        f"       Автор: {mr.author or '?'}  |  Assignee: {assignees}  |  Reviewer: {reviewers}\n"
        f"       Метки: {labels}\n"
        f"       URL:   {mr.web_url}\n"
    )


def fmt_pipeline(p) -> str:
    """Format a normalized Pipeline (CI run)."""
    return (
        f"  #{p.id}  [{(p.status or '?').upper()}]  ref: {p.ref or '?'}  "
        f"sha: {p.sha or '?'}  {p.web_url or ''}\n"
    )


def fmt_env(env: dict) -> str:
    last = env.get("last_deployment")
    if last:
        dep_info = (
            f"Последний деплой: #{last['id']} | "
            f"ref: {last.get('ref','?')} | "
            f"статус: {last.get('status','?')} | "
            f"by: {last.get('deployable', {}).get('user', {}).get('username', '?')}"
        )
    else:
        dep_info = "Деплоев нет"
    return f"  [{env['id']}] {env['name']}  ({env.get('external_url') or 'нет URL'})\n        {dep_info}\n"


def fmt_branch(b) -> str:
    """Format a normalized Branch."""
    flags = []
    if b.protected:
        flags.append("PROTECTED")
    if b.default:
        flags.append("DEFAULT")
    if b.merged:
        flags.append("merged")
    return f"  {b.name}  {'  '.join(flags)}\n"


def _parse_unified_diff(diff: str) -> list[dict]:
    import re
    parsed = []
    old_line = None
    new_line = None
    hunk_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in (diff or "").splitlines():
        match = hunk_re.match(raw)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            parsed.append({"type": "hunk", "old_line": old_line, "new_line": new_line, "text": raw})
            continue
        if old_line is None or new_line is None:
            parsed.append({"type": "meta", "old_line": None, "new_line": None, "text": raw})
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            parsed.append({"type": "add", "old_line": None, "new_line": new_line, "text": raw})
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            parsed.append({"type": "del", "old_line": old_line, "new_line": None, "text": raw})
            old_line += 1
        else:
            parsed.append({"type": "ctx", "old_line": old_line, "new_line": new_line, "text": raw})
            old_line += 1
            new_line += 1
    return parsed


# ------------------------------------------------------------------
# Provider-agnostic command implementations (route through modules.vcs)
# ------------------------------------------------------------------

def cmd_info(client):
    u = client.current_user()
    print(f"\n{'='*50}")
    print(f"  Пользователь: {u.name} (@{u.username})")
    print(f"  Email:        {u.email}")
    print(f"  Provider:     {client.provider}")
    print(f"  Web:          {u.web_url}")
    print(f"{'='*50}\n")


def cmd_mrs(client, ref=None, state: str = "opened", source_branch: str = None, target_branch: str = None):
    if ref is not None:
        mrs = client.list_mrs(ref, state=state, source_branch=source_branch, target_branch=target_branch)
        title = f"MR проекта {ref.slug}"
    else:
        mrs = client.list_my_mrs(state=state)
        title = "Мои MR"
    print(f"\n  {title} ({len(mrs)}):\n")
    if not mrs:
        print("  (нет)\n")
        return
    for mr in mrs:
        print(fmt_mr(mr))


def cmd_mr_detail(client, ref, mr_iid: int, provider: str):
    mr = client.get_mr(ref, mr_iid)
    print(f"\n{'='*60}")
    print(fmt_mr(mr))
    # GitLab-specific enrichment: approvals + MR pipelines.
    if provider == "gitlab":
        gl = getattr(client, "client", None) or _get_client()
        pid = ref.require_project_id()
        approvals = gl.get_mr_approvals(pid, mr_iid)
        approved_by = [a["user"]["username"] for a in approvals.get("approved_by", [])]
        print(f"  Аппрувы ({approvals.get('approvals_left',0)} осталось): {', '.join(approved_by) or 'нет'}")
        pipelines = gl.get_mr_pipelines(pid, mr_iid)
        if pipelines:
            print("\n  Пайплайны MR:")
            for p in pipelines[:3]:
                print(f"  #{p['id']}  [{(p.get('status') or '?').upper()}]  "
                      f"ref: {p.get('ref','?')}  sha: {(p.get('sha') or '?')[:8]}  {p.get('web_url','')}")
    if mr.description:
        print("\n  Описание:\n")
        for line in mr.description.splitlines():
            print(f"    {line}")
    print(f"{'='*60}\n")


def cmd_mr_notes(client, ref, mr_iid: int):
    notes = client.list_mr_notes(ref, mr_iid)
    print(f"\n  Комментарии MR !{mr_iid} ({len(notes)}):\n")
    if not notes:
        print("  (нет)\n")
        return
    for note in notes:
        author = note.author or "?"
        created = (note.created_at or "")[:19]
        system = " [system]" if note.system else ""
        print(f"  #{note.id} {created} @{author}{system}")
        body = (note.body or "").strip()
        for line in body.splitlines()[:12]:
            print(f"    {line}")
        if len(body.splitlines()) > 12:
            print("    ...")
        print()


def cmd_mr_discussions(client, ref, mr_iid: int):
    discussions = client.list_mr_discussions(ref, mr_iid)
    print(f"\n  Discussions / review threads MR !{mr_iid} ({len(discussions)}):\n")
    if not discussions:
        print("  (нет)\n")
        return
    for discussion in discussions:
        print(f"  Discussion {discussion.id}:")
        for note in discussion.notes:
            author = note.author or "?"
            created = (note.created_at or "")[:19]
            resolved = note.resolved
            resolved_text = f" resolved={resolved}" if resolved is not None else ""
            print(f"    #{note.id} {created} @{author}{resolved_text}")
            body = (note.body or "").strip()
            for line in body.splitlines()[:8]:
                print(f"      {line}")
            if len(body.splitlines()) > 8:
                print("      ...")
        print()


def cmd_pipelines(client, ref, ref_branch: str = None, limit: int = None):
    pipelines = client.list_pipelines(ref, ref=ref_branch, limit=limit)
    print(f"\n  Пайплайны {ref.slug}{' / ' + ref_branch if ref_branch else ''} ({len(pipelines)}):\n")
    for p in pipelines:
        print(fmt_pipeline(p))
    print()


def cmd_branches(client, ref, provider: str):
    branches = client.list_branches(ref)
    print(f"\n  Ветки {ref.slug} ({len(branches)}):\n")
    for b in branches:
        print(fmt_branch(b))
    # GitLab-specific: protection rules detail.
    if provider == "gitlab":
        gl = getattr(client, "client", None) or _get_client()
        protected = gl.list_protected_branches(ref.require_project_id())
        if protected:
            print("\n  Правила защиты веток:\n")
            for p in protected:
                merge_access = [a.get("access_level_description", "?") for a in p.get("merge_access_levels", [])]
                push_access = [a.get("access_level_description", "?") for a in p.get("push_access_levels", [])]
                print(f"  {p['name']}")
                print(f"    Push:  {', '.join(push_access) or 'никто'}")
                print(f"    Merge: {', '.join(merge_access) or 'никто'}")
                print(f"    Запрет force-push: {not p.get('allow_force_push', False)}")
                print()


def cmd_mr_changes(client, ref, mr_iid: int):
    changes = client.get_mr_changes(ref, mr_iid)
    print(f"\n  Изменения MR !{mr_iid} ({len(changes)} файлов):\n")
    for c in changes:
        print(f"  [{c.status}]  {c.new_path}")
    print()


def cmd_mr_diff(client, ref, mr_iid: int, path_filter: str = None):
    changes = client.get_mr_changes(ref, mr_iid)
    if path_filter:
        changes = [c for c in changes if c.new_path == path_filter or c.old_path == path_filter]
    print(f"\n  Diff MR !{mr_iid} ({len(changes)} файлов):\n")
    if not changes:
        print("  (нет файлов)\n")
        return
    for change in changes:
        print(f"  FILE {change.old_path} -> {change.new_path}")
        for line in _parse_unified_diff(change.diff or ""):
            old_no = line["old_line"] if line["old_line"] is not None else ""
            new_no = line["new_line"] if line["new_line"] is not None else ""
            print(f"  {str(old_no):>5} {str(new_no):>5} | {line['text']}")
        print()


def cmd_open_mr(client, ref, source_branch: str, target_branch: str, desc: str = ""):
    try:
        mr = client.create_mr(ref, source_branch, target_branch, source_branch, description=desc)
    except VcsWriteNotSupported as e:
        print(f"\n  Write not supported for this provider yet: {e}\n")
        return
    except VcsWriteDisabledError as e:
        print(f"\n  {e}\n")
        return
    print("\n  MR создан:")
    print(fmt_mr(mr))


def cmd_comment(client, ref, mr_iid: int, text: str):
    try:
        note = client.comment_mr(ref, mr_iid, text)
    except VcsWriteNotSupported as e:
        print(f"\n  Write not supported for this provider yet: {e}\n")
        return
    except VcsWriteDisabledError as e:
        print(f"\n  {e}\n")
        return
    print(f"\n  Комментарий добавлен (id: {note.id})\n")


def cmd_mr_inline_comment(client, ref, mr_iid: int, file_path: str, line_type: str, line: int, body: str):
    if line_type not in ("new", "old"):
        raise ValueError("line_type must be 'new' or 'old'")
    try:
        discussion = client.create_mr_discussion(ref, mr_iid, body, file_path, line, line_type)
    except VcsWriteNotSupported as e:
        print(f"\n  Write not supported for this provider yet: {e}\n")
        return
    except VcsWriteDisabledError as e:
        print(f"\n  {e}\n")
        return
    print("\n  Inline review comment создан:")
    print(f"  discussion: {discussion.id}")
    print(f"  file: {file_path}")
    print(f"  {line_type}_line: {line}\n")


# ------------------------------------------------------------------
# GitLab-only command implementations (raw GitLabClient)
# ------------------------------------------------------------------

def cmd_projects():
    projects = _get_client().list_projects()
    print(f"\n  Доступные проекты ({len(projects)}):\n")
    for p in projects:
        print(f"  [{p['id']:>3}]  {p['path_with_namespace']}")
    print()


def cmd_pipeline_detail(project_id: int, pipeline_id: int):
    client = _get_client()
    jobs = client.list_pipeline_jobs(project_id, pipeline_id)
    pipeline = client.get_pipeline(project_id, pipeline_id)
    print(f"\n  Пайплайн #{pipeline_id}  [{pipeline['status'].upper()}]  ref: {pipeline.get('ref')}\n")
    print(f"  {'Стадия':<15} {'Джоб':<30} {'Статус':<12} {'ID':<10} {'Причина'}")
    print(f"  {'-'*90}")
    failed_ids = []
    for j in jobs:
        reason = j.get("failure_reason") or ""
        print(f"  {j.get('stage','?'):<15} {j['name']:<30} {j['status']:<12} {str(j.get('id','')):<10} {reason}")
        if j.get("status") == "failed":
            failed_ids.append(j["id"])
    print()
    if failed_ids:
        print(f"  Для логов упавших джобов: workflow job-log {project_id} <job_id>")
        print(f"  Или сразу все причины:    workflow pipeline-failures {project_id} {pipeline_id}\n")


def _extract_error_tail(trace: str, tail_lines: int = 80) -> str:
    """Strip ANSI escapes and return last N non-empty lines of trace."""
    import re
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", trace)
    clean = re.sub(r"\r", "\n", clean)
    lines = [ln.rstrip() for ln in clean.split("\n") if ln.strip()]
    return "\n".join(lines[-tail_lines:])


def cmd_job_log(project_id: int, job_id: int, tail_lines: int | None = None, full: bool = False):
    client = _get_client()
    try:
        job = client.get_job(project_id, job_id)
    except Exception:
        job = None
    trace = client.get_job_log(project_id, job_id, tail_chars=0 if full else 8000)
    if job:
        print(f"\n  Job #{job_id}  {job.get('name')}  [{job.get('status','?').upper()}]"
              f"  stage={job.get('stage','?')}  failure_reason={job.get('failure_reason') or '—'}\n")
    else:
        print(f"\n  Job #{job_id}\n")
    if tail_lines:
        print(_extract_error_tail(trace, tail_lines=tail_lines))
    else:
        print(trace)
    print()


def cmd_pipeline_failures(project_id: int, pipeline_id: int, tail_lines: int = 60):
    client = _get_client()
    pipeline = client.get_pipeline(project_id, pipeline_id)
    jobs = client.list_pipeline_jobs(project_id, pipeline_id)
    failed = [j for j in jobs if j.get("status") == "failed"]
    print(f"\n  Пайплайн #{pipeline_id}  [{pipeline['status'].upper()}]  ref: {pipeline.get('ref')}")
    print(f"  Упавших джобов: {len(failed)}\n")
    if not failed:
        print("  (нет failed-джобов)\n")
        return
    for j in failed:
        print(f"  ── job #{j['id']}  {j['name']}  stage={j.get('stage','?')}"
              f"  failure_reason={j.get('failure_reason') or '—'}")
        try:
            trace = client.get_job_log(project_id, j["id"], tail_chars=12000)
            tail = _extract_error_tail(trace, tail_lines=tail_lines)
            print(tail)
        except Exception as e:
            print(f"  (не удалось получить trace: {e})")
        print()


def cmd_envs(project_id: int):
    client = _get_client()
    envs = client.list_environments(project_id)
    print(f"\n  Окружения проекта {project_id} ({len(envs)}):\n")
    if not envs:
        print("  (нет — деплой идёт через CI, посмотри `pipelines`)\n")
        return
    for env in envs:
        full = client.get_environment(project_id, env["id"])
        print(fmt_env(full))
    print()


def cmd_ci(project_id: int):
    try:
        content = _get_client().get_file(project_id, ".gitlab-ci.yml")
        print(f"\n  .gitlab-ci.yml проекта {project_id}:\n")
        print(content)
    except Exception as e:
        print(f"\n  Файл .gitlab-ci.yml не найден или ошибка: {e}\n")


def cmd_edit_mr(project_id: int, mr_iid: int, title: str = None, desc: str = None):
    from modules.vcs.providers.gitlab import _mr as _to_mr_model
    kwargs = {}
    if title is not None:
        kwargs["title"] = title
    if desc is not None:
        kwargs["description"] = desc
    if not kwargs:
        print("  Укажи хотя бы один параметр: --title=... или --desc=...")
        return
    mr = _get_client().update_mr(project_id, mr_iid, **kwargs)
    print("\n  MR обновлён:")
    print(fmt_mr(_to_mr_model(mr)))


def cmd_deploy(project_id: int, branch: str, qa_slot: str, play_manual: bool = True):
    client = _get_client()
    assert_valid_qa_slot(qa_slot)
    client.get_branch(project_id, branch)
    pipeline = client.trigger_pipeline(project_id, branch, inputs={"environment": qa_slot})
    print("\n  Пайплайн запущен:")
    print(f"  id:  {pipeline['id']}")
    print(f"  ref: {pipeline['ref']}")
    print(f"  url: {pipeline['web_url']}")
    if play_manual:
        cmd_deploy_play_manual(project_id, pipeline["id"])
    else:
        print(f"\n  Следующий шаг: deploy-play-manual {project_id} {pipeline['id']}\n")


def cmd_deploy_auto(project_id: int, branch: str, play_manual: bool = True):
    client = _get_client()
    client.get_branch(project_id, branch)
    pipeline = client.trigger_pipeline(project_id, branch, inputs={"environment": "auto"})
    print("\n  Пайплайн запущен (auto):")
    print(f"  id:  {pipeline['id']}")
    print(f"  ref: {pipeline['ref']}")
    print(f"  url: {pipeline['web_url']}")
    if play_manual:
        cmd_deploy_play_manual(project_id, pipeline["id"])
    else:
        print(f"\n  Следующий шаг: deploy-play-manual {project_id} {pipeline['id']}\n")


def cmd_deploy_play_manual(project_id: int, pipeline_id: int):
    import time
    client = _get_client()
    manual_jobs = []
    for attempt in range(6):
        jobs = client.list_pipeline_jobs(project_id, pipeline_id)
        manual_jobs = [j for j in jobs if j.get("status") == "manual"]
        if manual_jobs:
            break
        if attempt < 5:
            time.sleep(3)
    if not manual_jobs:
        print(f"\n  Manual jobs не найдены в пайплайне #{pipeline_id}.")
        print("  Возможно, пайплайн ещё не создал джобы — попробуй через несколько секунд.\n")
        return
    print(f"\n  Запускаю manual jobs в пайплайне #{pipeline_id}:")
    for job in manual_jobs:
        played = client.play_job(project_id, job["id"])
        print(f"  ▶ {job.get('name')} (job #{job.get('id')}) → {played.get('status', '?')}")
    print()


def cmd_recent_deploys(project_id: int):
    from shared.config import ALLOWED_QA_SLOTS
    from modules.gitlab.gitops_qa import latest_gitops_qa_deploys, latest_auto_deploy

    client = _get_client()
    slots, had_error = latest_gitops_qa_deploys(client, project_id, ALLOWED_QA_SLOTS)
    auto = latest_auto_deploy(client, project_id)
    print(f"\n  Последние QA / auto деплои проекта {project_id}:\n")
    if had_error:
        print("  Внимание: часть GitLab read-запросов не удалась, статус неполный.\n")
    print(f"  {'Слот':<10} {'Ветка':<40} {'Pipeline':<12} {'Дата':<12} Source")
    print(f"  {'-'*110}")
    for slot in sorted(ALLOWED_QA_SLOTS):
        deploy = slots.get(slot)
        if deploy is None:
            print(f"  {slot:<10} {'—':<40} {'нет данных':<12} {'—':<12} —")
            continue
        pipeline = deploy.get("pipeline") or {}
        ref = deploy.get("ref") or "?"
        status = pipeline.get("status") or "?"
        deployed_at = (deploy.get("deployed_at") or "")[:10] or "—"
        values_file = deploy.get("gitops_values_file") or "—"
        print(f"  {slot:<10} {ref[:38]:<40} {status:<12} {deployed_at:<12} {values_file}")
    if auto:
        ref = auto.get("ref") or "?"
        status = auto.get("pipeline_status") or "?"
        deployed_at = (auto.get("deployed_at") or "")[:10] or "—"
        source = f"pipeline #{auto.get('pipeline_id')}"
        print(f"  {'auto':<10} {ref[:38]:<40} {status:<12} {deployed_at:<12} {source}")
    else:
        print(f"  {'auto':<10} {'—':<40} {'нет данных':<12} {'—':<12} (no auto-deploys in last 20 успешных пайплайнов)")
    print()


def cmd_rules(project_id: int):
    client = _get_client()
    print(f"\n  Правила и шаблоны для проекта {project_id}:\n")
    try:
        push_rules = client.get_push_rules(project_id)
        print("  Push rules:")
        for k, v in push_rules.items():
            if v and k not in ("id", "created_at"):
                print(f"    {k}: {v}")
    except Exception:
        print("  Push rules: не настроены или нет доступа")
    templates = client.list_mr_templates(project_id)
    if templates:
        print(f"\n  Шаблоны MR ({len(templates)}):")
        for t in templates:
            print(f"    {t['name']}")
            try:
                content = client.get_file(project_id, f".gitlab/merge_request_templates/{t['name']}")
                for line in content.splitlines()[:20]:
                    print(f"      {line}")
                if len(content.splitlines()) > 20:
                    print("      ...")
            except Exception:
                pass
    else:
        print("\n  Шаблоны MR: не найдены (.gitlab/merge_request_templates/)")
    for fname in ["CONTRIBUTING.md", "docs/CONTRIBUTING.md", ".gitlab/CONTRIBUTING.md"]:
        try:
            content = client.get_file(project_id, fname)
            print(f"\n  {fname}:\n")
            for line in content.splitlines()[:40]:
                print(f"    {line}")
            if len(content.splitlines()) > 40:
                print("    ...")
            break
        except Exception:
            pass
    print()


# ------------------------------------------------------------------
# Command router
# ------------------------------------------------------------------

GITLAB_COMMANDS = {
    "info", "projects", "mrs", "mr", "mr-changes", "mr-diff", "mr-notes",
    "mr-discussions", "mr-inline-comment", "pipelines", "pipeline",
    "job-log", "pipeline-failures", "envs",
    "branches", "ci", "rules", "open-mr", "edit-mr", "comment",
    "deploy", "deploy-auto", "deploy-play-manual", "recent-deploys",
}

# Commands whose first positional argument is a repo token (slug / numeric
# project_id / owner-repo). `info` and `projects` take no repo arg. `mrs` is
# special: with a positional the arg IS the repo; with none it lists my MRs.
_NO_REPO_COMMANDS = {"projects"}
_GITLAB_ONLY_COMMANDS = {
    "projects", "pipeline", "job-log", "pipeline-failures", "envs", "ci",
    "rules", "edit-mr", "deploy", "deploy-auto", "deploy-play-manual",
    "recent-deploys",
}


def _resolve_repo_token(cmd: str, parts: list):
    """
    Resolve provider/account/RepoRef from the command's repo token.

    Extracts `--provider=` / `--account=` / `--project=` flags, normalizes the
    repo token into parts[1] (so downstream positional indexing is unchanged),
    and resolves via `resolve_repo`. Returns
    ``(provider, account, ref, parts)``; `ref` is None for repo-less calls.
    Raises RepoResolveError for an unknown token.
    """
    provider_flag = account_flag = project_flag = None
    rest = []
    for p in parts:
        if p.startswith("--provider="):
            provider_flag = p.split("=", 1)[1]
        elif p.startswith("--account="):
            account_flag = p.split("=", 1)[1]
        elif p.startswith("--project="):
            project_flag = p.split("=", 1)[1]
        else:
            rest.append(p)
    parts = rest

    if project_flag is not None:
        if len(parts) >= 2 and not parts[1].startswith("--"):
            parts[1] = project_flag
        else:
            parts.insert(1, project_flag)

    # Determine the repo token. `info` / `projects` and a bare `mrs` take none.
    repo_token = None
    if cmd not in ("info",) and cmd not in _NO_REPO_COMMANDS:
        if len(parts) >= 2 and not parts[1].startswith("--"):
            repo_token = parts[1]

    provider, account, ref = resolve_repo(repo_token, provider=provider_flag, account=account_flag)
    return provider, account, ref, parts


def run_command(cmd: str, parts: list, jira_client=None) -> bool:
    """Route a VCS command. Returns True if handled, False otherwise."""
    if cmd not in GITLAB_COMMANDS:
        return False

    try:
        provider, account, ref, parts = _resolve_repo_token(cmd, parts)
    except RepoResolveError as e:
        print(f"\n  {e}\n")
        return True

    global _active_account
    _active_account = account

    try:
        if cmd in _GITLAB_ONLY_COMMANDS and not _require_gitlab(provider, cmd):
            return True

        if cmd == "info":
            cmd_info(get_vcs_client(provider, account))
        elif cmd == "projects":
            cmd_projects()
        elif cmd == "mrs":
            client = get_vcs_client(provider, account)
            if ref is not None:
                flags = parts[2:]
                state = next((p[8:] for p in flags if p.startswith("--state=")), "opened")
                source = next((p[9:] for p in flags if p.startswith("--source=")), None)
                target = next((p[9:] for p in flags if p.startswith("--target=")), None)
                cmd_mrs(client, ref, state=state, source_branch=source, target_branch=target)
            else:
                cmd_mrs(client)
        elif cmd == "mr":
            if len(parts) < 3:
                print("  Использование: mr <repo> <mr_iid>")
            else:
                cmd_mr_detail(get_vcs_client(provider, account), ref, int(parts[2]), provider)
        elif cmd == "pipelines":
            flags = [p for p in parts[2:] if p.startswith("--")]
            args = [p for p in parts[2:] if not p.startswith("--")]
            ref_branch = args[0] if args else None
            limit_flag = next((p for p in flags if p.startswith("--limit=")), None)
            limit = int(limit_flag[8:]) if limit_flag else None
            cmd_pipelines(get_vcs_client(provider, account), ref, ref_branch, limit)
        elif cmd == "pipeline":
            cmd_pipeline_detail(ref.require_project_id(), int(parts[2]))
        elif cmd == "job-log":
            if len(parts) < 3:
                print("  Использование: job-log <repo> <job_id> [--tail=N] [--full]")
            else:
                full = "--full" in parts[3:]
                tail_flag = next((p for p in parts[3:] if p.startswith("--tail=")), None)
                tail = int(tail_flag[7:]) if tail_flag else None
                cmd_job_log(ref.require_project_id(), int(parts[2]), tail_lines=tail, full=full)
        elif cmd == "pipeline-failures":
            if len(parts) < 3:
                print("  Использование: pipeline-failures <repo> <pipeline_id> [--tail=N]")
            else:
                tail_flag = next((p for p in parts[3:] if p.startswith("--tail=")), None)
                tail = int(tail_flag[7:]) if tail_flag else 60
                cmd_pipeline_failures(ref.require_project_id(), int(parts[2]), tail_lines=tail)
        elif cmd == "envs":
            cmd_envs(ref.require_project_id())
        elif cmd == "branches":
            cmd_branches(get_vcs_client(provider, account), ref, provider)
        elif cmd == "ci":
            cmd_ci(ref.require_project_id())
        elif cmd == "rules":
            cmd_rules(ref.require_project_id())
        elif cmd == "open-mr":
            if len(parts) < 4:
                print("  Использование: open-mr <repo> <source_branch> <target_branch> [--desc=...]")
            else:
                desc = next((p[7:] for p in parts[4:] if p.startswith("--desc=")), "")
                cmd_open_mr(get_vcs_client(provider, account), ref, parts[2], parts[3], desc)
        elif cmd == "edit-mr":
            if len(parts) < 3:
                print("  Использование: edit-mr <repo> <mr_iid> [--title=...] [--desc=...]")
            else:
                title = next((p[8:] for p in parts[3:] if p.startswith("--title=")), None)
                desc = next((p[7:] for p in parts[3:] if p.startswith("--desc=")), None)
                try:
                    cmd_edit_mr(ref.require_project_id(), int(parts[2]), title, desc)
                except MrStateChangeForbiddenError as e:
                    print(f"  Ошибка: {e}")
        elif cmd == "comment":
            if len(parts) < 4:
                print("  Использование: comment <repo> <mr_iid> <text>")
            else:
                cmd_comment(get_vcs_client(provider, account), ref, int(parts[2]), " ".join(parts[3:]))
        elif cmd == "mr-inline-comment":
            if len(parts) < 6:
                print("  Использование: mr-inline-comment <repo> <mr_iid> --file=<path> --new-line=N|--old-line=N --body=<text>")
            else:
                parser = argparse.ArgumentParser(prog="mr-inline-comment", add_help=False)
                parser.add_argument("--file", required=True)
                parser.add_argument("--new-line", type=int)
                parser.add_argument("--old-line", type=int)
                parser.add_argument("--body", required=True)
                args = parser.parse_args(parts[3:])
                if (args.new_line is None) == (args.old_line is None):
                    print("  Укажи ровно один параметр: --new-line=N или --old-line=N")
                else:
                    line_type = "new" if args.new_line is not None else "old"
                    line = args.new_line if args.new_line is not None else args.old_line
                    cmd_mr_inline_comment(get_vcs_client(provider, account), ref, int(parts[2]), args.file, line_type, line, args.body)
        elif cmd == "mr-changes":
            if len(parts) < 3:
                print("  Использование: mr-changes <repo> <mr_iid>")
            else:
                cmd_mr_changes(get_vcs_client(provider, account), ref, int(parts[2]))
        elif cmd == "mr-diff":
            if len(parts) < 3:
                print("  Использование: mr-diff <repo> <mr_iid> [--file=<path>]")
            else:
                file_filter = next((p[7:] for p in parts[3:] if p.startswith("--file=")), None)
                cmd_mr_diff(get_vcs_client(provider, account), ref, int(parts[2]), path_filter=file_filter)
        elif cmd == "mr-notes":
            if len(parts) < 3:
                print("  Использование: mr-notes <repo> <mr_iid>")
            else:
                cmd_mr_notes(get_vcs_client(provider, account), ref, int(parts[2]))
        elif cmd == "mr-discussions":
            if len(parts) < 3:
                print("  Использование: mr-discussions <repo> <mr_iid>")
            else:
                cmd_mr_discussions(get_vcs_client(provider, account), ref, int(parts[2]))
        elif cmd == "recent-deploys":
            if len(parts) < 2:
                print("  Использование: recent-deploys <repo>")
            else:
                cmd_recent_deploys(ref.require_project_id())
        elif cmd == "deploy":
            if len(parts) < 4:
                print("  Использование: deploy <repo> <branch> <qa_slot> [--no-play-manual]")
            else:
                play_manual = "--no-play-manual" not in parts[4:]
                try:
                    cmd_deploy(ref.require_project_id(), parts[2], parts[3], play_manual=play_manual)
                except InvalidSlotError as e:
                    print(f"  Ошибка: {e}")
        elif cmd == "deploy-auto":
            if len(parts) < 3:
                print("  Использование: deploy-auto <repo> <branch> [--no-play-manual]")
            else:
                play_manual = "--no-play-manual" not in parts[3:]
                cmd_deploy_auto(ref.require_project_id(), parts[2], play_manual=play_manual)
        elif cmd == "deploy-play-manual":
            if len(parts) < 3:
                print("  Использование: deploy-play-manual <repo> <pipeline_id>")
            else:
                cmd_deploy_play_manual(ref.require_project_id(), int(parts[2]))
    except Exception as e:
        print(f"  Ошибка: {e}\n")

    return True
