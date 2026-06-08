"""
GitLab API client. Read-методы — без ограничений; write-методы проходят через
guards.py (запрет на прод-ветки, на смену состояния MR и т.п.).
"""

import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote
from typing import Any, Optional
from dotenv import load_dotenv
from modules.gitlab.guards import (
    assert_no_agent_attribution,
    assert_not_protected_ref,
    assert_no_mr_state_change,
)
from shared.paths import workflow_home
from shared.config_loader import load_module_env

# Lookup order: config/<module>/.env (new), modules/<module>/.env (legacy backup).
_HOME = workflow_home()
load_dotenv(dotenv_path=_HOME / "config" / "gitlab" / ".env")
load_dotenv(dotenv_path=_HOME / "modules" / "gitlab" / ".env")

# Legacy module-level constants kept for backward-compat / test patching.
# Connection config is now resolved per-account inside GitLabClient.__init__;
# methods read self.base_url, not this global.
GITLAB_HOST = os.getenv("GITLAB_HOST", "")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
BASE_URL = f"https://{GITLAB_HOST}/api/v4"

# Global write safety toggle — account-independent on purpose.
WRITE_ENABLED = os.getenv("GITLAB_WRITE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


class GitLabClient:
    def __init__(self, account: str = "default"):
        env = load_module_env("gitlab", account)
        host = env.get("GITLAB_HOST", "")
        token = env.get("GITLAB_TOKEN", "")
        if not host or not token:
            raise RuntimeError(
                f"Set GITLAB_HOST and GITLAB_TOKEN for account '{account}' in "
                f"config/gitlab/.env (bare keys, or GITLAB__{account.upper()}__* for multi-account)"
            )
        self.account = account
        self.host = host
        self.base_url = f"https://{host}/api/v4"
        self.session = requests.Session()
        self.session.headers.update({"PRIVATE-TOKEN": token})
        _adapter = HTTPAdapter(
            max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504]),
        )
        self.session.mount("https://", _adapter)
        self.session.mount("http://", _adapter)
        self._projects_cache: Optional[list] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=(5, 15))
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, path: str, params: Optional[dict] = None, max_pages: int = 5) -> list:
        params = dict(params or {})
        params.setdefault("per_page", 50)
        results = []
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

    def _post(self, path: str, data: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, json=data, timeout=(5, 15))
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, data: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.put(url, json=data, timeout=(5, 15))
        resp.raise_for_status()
        return resp.json()

    def _write_guard(self):
        if not WRITE_ENABLED:
            raise NotImplementedError(
                "GitLab write operations are disabled. Set GITLAB_WRITE_ENABLED=true in .env "
                "and request the write action explicitly in the current chat."
            )

    # ------------------------------------------------------------------
    # User / auth
    # ------------------------------------------------------------------

    def current_user(self) -> dict:
        return self._get("/user")

    # ------------------------------------------------------------------
    # Groups & projects
    # ------------------------------------------------------------------

    def list_groups(self) -> list:
        return self._paginate("/groups", {"all_available": False})

    def list_projects(self, group_id: Optional[int] = None) -> list:
        if group_id:
            return self._paginate(f"/groups/{group_id}/projects", {"include_subgroups": True})
        if self._projects_cache is None:
            self._projects_cache = self._paginate(
                "/projects", {"membership": True, "order_by": "last_activity_at"}
            )
        return self._projects_cache

    def get_project(self, project_id: int) -> dict:
        return self._get(f"/projects/{project_id}")

    # ------------------------------------------------------------------
    # Merge Requests
    # ------------------------------------------------------------------

    def list_mrs(
        self,
        project_id: int,
        state: str = "opened",
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
    ) -> list:
        params = {"state": state}
        if source_branch:
            params["source_branch"] = source_branch
        if target_branch:
            params["target_branch"] = target_branch
        return self._paginate(f"/projects/{project_id}/merge_requests", params)

    def list_my_mrs(self, state: str = "opened") -> list:
        return self._paginate("/merge_requests", {"scope": "created_by_me", "state": state})

    def list_assigned_mrs(self, state: str = "opened") -> list:
        return self._paginate("/merge_requests", {"scope": "assigned_to_me", "state": state})

    def list_review_mrs(self) -> list:
        return self._paginate("/merge_requests", {"scope": "assigned_to_me", "state": "opened", "reviewer_id": "Any"})

    def get_mr(self, project_id: int, mr_iid: int) -> dict:
        return self._get(f"/projects/{project_id}/merge_requests/{mr_iid}")

    def get_mr_changes(self, project_id: int, mr_iid: int) -> dict:
        return self._get(f"/projects/{project_id}/merge_requests/{mr_iid}/changes")

    def get_mr_versions(self, project_id: int, mr_iid: int) -> list:
        return self._get(f"/projects/{project_id}/merge_requests/{mr_iid}/versions")

    def get_mr_diff_refs(self, project_id: int, mr_iid: int) -> dict:
        mr = self.get_mr(project_id, mr_iid)
        diff_refs = mr.get("diff_refs") or {}
        if diff_refs:
            return diff_refs

        versions = self.get_mr_versions(project_id, mr_iid)
        if not versions:
            return {}
        latest = versions[0]
        return {
            "base_sha": latest.get("base_commit_sha"),
            "start_sha": latest.get("start_commit_sha"),
            "head_sha": latest.get("head_commit_sha"),
        }

    def get_mr_approvals(self, project_id: int, mr_iid: int) -> dict:
        return self._get(f"/projects/{project_id}/merge_requests/{mr_iid}/approvals")

    def get_mr_pipelines(self, project_id: int, mr_iid: int) -> list:
        return self._get(f"/projects/{project_id}/merge_requests/{mr_iid}/pipelines")

    def list_mr_notes(self, project_id: int, mr_iid: int) -> list:
        return self._paginate(f"/projects/{project_id}/merge_requests/{mr_iid}/notes")

    def list_mr_discussions(self, project_id: int, mr_iid: int) -> list:
        return self._paginate(f"/projects/{project_id}/merge_requests/{mr_iid}/discussions")

    # ------------------------------------------------------------------
    # Branches & protection rules
    # ------------------------------------------------------------------

    def list_branches(self, project_id: int) -> list:
        return self._paginate(f"/projects/{project_id}/repository/branches")

    def get_branch(self, project_id: int, branch: str) -> dict:
        return self._get(f"/projects/{project_id}/repository/branches/{quote(branch, safe='')}")

    def list_protected_branches(self, project_id: int) -> list:
        return self._paginate(f"/projects/{project_id}/protected_branches")

    def get_push_rules(self, project_id: int) -> dict:
        return self._get(f"/projects/{project_id}/push_rule")

    # ------------------------------------------------------------------
    # CI/CD & Pipelines
    # ------------------------------------------------------------------

    def version(self) -> str:
        return self._get("/version").get("version", "?")

    def list_pipelines(
        self,
        project_id: int,
        ref: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list:
        params = {}
        if ref:
            params["ref"] = ref
        if status:
            params["status"] = status
        if limit:
            max_pages = (limit + 49) // 50
            results = self._paginate(f"/projects/{project_id}/pipelines", params, max_pages=max_pages)
            return results[:limit]
        return self._paginate(f"/projects/{project_id}/pipelines", params, max_pages=2)

    def get_pipeline(self, project_id: int, pipeline_id: int) -> dict:
        return self._get(f"/projects/{project_id}/pipelines/{pipeline_id}")

    def list_pipeline_jobs(self, project_id: int, pipeline_id: int) -> list:
        return self._paginate(f"/projects/{project_id}/pipelines/{pipeline_id}/jobs")

    def get_pipeline_environment(self, project_id: int, pipeline_id: int) -> Optional[str]:
        """
        Tries to extract the target QA environment from pipeline detail.
        Checks (in order):
          1. pipeline["inputs"]["environment"] — dict shape (GitLab inputs API)
          2. pipeline["inputs"] as list — [{"key": "environment", "value": ...}]
          3. pipeline["variables"] as list — same key lookup, also checks CI_ENVIRONMENT_NAME
        Returns None if the field is not present in any supported shape.
        Falls back to None (not a silent guess) so callers can use the deployments
        endpoint as a more reliable source when inputs are absent.
        """
        try:
            pipeline = self.get_pipeline(project_id, pipeline_id)
        except Exception:
            return None

        inputs = pipeline.get("inputs")
        if isinstance(inputs, dict):
            return inputs.get("environment")
        if isinstance(inputs, list):
            for item in inputs:
                if isinstance(item, dict) and item.get("key") == "environment":
                    return item.get("value")

        variables = pipeline.get("variables")
        if isinstance(variables, list):
            for var in variables:
                if isinstance(var, dict) and var.get("key") in ("environment", "CI_ENVIRONMENT_NAME"):
                    return var.get("value")

        return None

    def get_job_log(self, project_id: int, job_id: int, tail_chars: int = 4000) -> str:
        url = f"{self.base_url}/projects/{project_id}/jobs/{job_id}/trace"
        resp = self.session.get(url, timeout=(5, 15))
        resp.raise_for_status()
        if tail_chars and len(resp.text) > tail_chars:
            return f"[обрезано до последних {tail_chars} символов]\n...\n" + resp.text[-tail_chars:]
        return resp.text

    def get_job(self, project_id: int, job_id: int) -> dict:
        return self._get(f"/projects/{project_id}/jobs/{job_id}")

    # ------------------------------------------------------------------
    # Environments & Deployments
    # ------------------------------------------------------------------

    def list_environments(self, project_id: int) -> list:
        return self._paginate(f"/projects/{project_id}/environments")

    def get_environment(self, project_id: int, env_id: int) -> dict:
        return self._get(f"/projects/{project_id}/environments/{env_id}")

    def list_deployments(self, project_id: int, environment: Optional[str] = None) -> list:
        params = {"order_by": "created_at", "sort": "desc"}
        if environment:
            params["environment"] = environment
        return self._paginate(f"/projects/{project_id}/deployments", params, max_pages=2)

    def recent_qa_deploys(self, project_id: int) -> dict:
        """Legacy fallback: GitLab Deployment records are not the QA source of truth."""
        from shared.config import ALLOWED_QA_SLOTS
        result = {}
        for slot in sorted(ALLOWED_QA_SLOTS):
            deploys = self.list_deployments(project_id, environment=slot)
            result[slot] = deploys[0] if deploys else None
        return result

    # ------------------------------------------------------------------
    # Repository files
    # ------------------------------------------------------------------

    def get_file(self, project_id: int, file_path: str, ref: str = "HEAD") -> str:
        import base64
        data = self._get(
            f"/projects/{project_id}/repository/files/{requests.utils.quote(file_path, safe='')}",
            {"ref": ref},
        )
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    def list_files(self, project_id: int, path: str = "", ref: str = "HEAD") -> list:
        return self._paginate(
            f"/projects/{project_id}/repository/tree",
            {"path": path, "ref": ref, "recursive": False},
        )

    def list_mr_templates(self, project_id: int) -> list:
        try:
            return self.list_files(project_id, ".gitlab/merge_request_templates")
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Write — MR
    # ------------------------------------------------------------------

    def create_mr(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> dict:
        assert_no_agent_attribution(title)
        assert_no_agent_attribution(description)
        self._write_guard()
        return self._post(
            f"/projects/{project_id}/merge_requests",
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
        )

    def update_mr(self, project_id: int, mr_iid: int, **kwargs) -> dict:
        assert_no_mr_state_change(kwargs.get("state_event"))
        assert_no_agent_attribution(kwargs.get("title", ""))
        assert_no_agent_attribution(kwargs.get("description", ""))
        self._write_guard()
        return self._put(f"/projects/{project_id}/merge_requests/{mr_iid}", kwargs)

    def comment_mr(self, project_id: int, mr_iid: int, body: str) -> dict:
        assert_no_agent_attribution(body)
        self._write_guard()
        return self._post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
            {"body": body},
        )

    def create_mr_discussion(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
        position: dict,
    ) -> dict:
        assert_no_agent_attribution(body)
        self._write_guard()
        return self._post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            {"body": body, "position": position},
        )

    # ------------------------------------------------------------------
    # Write — Pipelines
    # ------------------------------------------------------------------

    def trigger_pipeline(
        self,
        project_id: int,
        ref: str,
        variables: Optional[dict] = None,
        inputs: Optional[dict] = None,
    ) -> dict:
        assert_not_protected_ref(ref)
        self._write_guard()
        payload: dict = {"ref": ref}
        if variables:
            payload["variables"] = [
                {"key": k, "value": v} for k, v in variables.items()
            ]
        if inputs:
            payload["inputs"] = inputs
        return self._post(f"/projects/{project_id}/pipeline", payload)

    def play_job(self, project_id: int, job_id: int) -> dict:
        self._write_guard()
        return self._post(f"/projects/{project_id}/jobs/{job_id}/play")
