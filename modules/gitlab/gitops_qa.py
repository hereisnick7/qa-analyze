"""
GitOps QA and auto deploy discovery.

The current frontend QA flow does not create GitLab Deployment records. The
source of truth is the successful `update-image-tags` job log, where the shared
pipeline records the selected QA slot (`qa<N>`) and updated GitOps values file.

Auto deploys map to "development" mode in the CI script: the job log
contains `Mode: Default (Development)` and updates `.../values/development.yaml`.
We detect them by parsing the same job log and exposing them as `slot="auto"`.
GitLab does not echo `inputs.environment` back on the pipeline detail endpoint,
so the log is the only reliable source.
"""

from __future__ import annotations

import re
from typing import Any, Optional


UPDATE_IMAGE_TAGS_JOB = "update-image-tags"

_MODE_RE = re.compile(r"Mode:\s*Manual input detected\s*->\s*(?P<slot>qa\d+)", re.I)
_UPDATING_RE = re.compile(
    r"Updating\s+(?P<path>\S+/values/(?P<slot>qa\d+|development)\.yaml)\s+with tag\s+(?P<tag>[^\s.]+)",
    re.I,
)
_MANUAL_RE = re.compile(
    r"Manual:\s*Update\s+(?P<slot>qa\d+)\s+image tag to\s+(?P<tag>\S+)",
    re.I,
)
_COMMIT_RE = re.compile(
    r"(?:gitops\s+commit|commit\s+sha|commit)\s*:?\s*(?P<sha>[0-9a-f]{7,40})",
    re.I,
)


def parse_update_image_tags_log(log: str) -> Optional[dict]:
    """
    Parse a successful update-image-tags job log.

    Returns a compact dict with no raw log content, or None when the log does
    not look like a manual QA GitOps deploy.
    """
    slot = None
    tag = None
    values_file = None
    gitops_commit_sha = None

    mode_match = _MODE_RE.search(log)
    if mode_match:
        slot = mode_match.group("slot")

    updating_match = _UPDATING_RE.search(log)
    if updating_match:
        values_file = updating_match.group("path")
        slot = updating_match.group("slot") or slot
        tag = updating_match.group("tag")

    manual_match = _MANUAL_RE.search(log)
    if manual_match:
        slot = manual_match.group("slot") or slot
        tag = tag or manual_match.group("tag")

    commit_match = _COMMIT_RE.search(log)
    if commit_match:
        gitops_commit_sha = commit_match.group("sha")

    if not slot:
        return None

    # CI's "Default (Development)" mode lands in development.yaml; expose as auto.
    if slot == "development":
        slot = "auto"

    return {
        "slot": slot,
        "tag": tag,
        "values_file": values_file,
        "gitops_commit_sha": gitops_commit_sha,
        "source": "update-image-tags job log",
    }


def latest_gitops_qa_deploys(
    gitlab_client: Any,
    project_id: int,
    allowed_slots: set[str],
    limit: int = 100,
) -> tuple[dict[str, dict], bool]:
    """
    Return latest GitOps QA deploy per slot by scanning successful pipelines.

    GitLab returns pipelines newest-first, so the first parsed deploy for a slot
    wins. The boolean indicates whether any GitLab read failed; callers should
    keep classification conservative when it is true.
    """
    try:
        pipelines = gitlab_client.list_pipelines(project_id, status="success", limit=limit)
    except Exception:
        return {}, True

    by_slot: dict[str, dict] = {}
    had_error = False

    for pipeline in pipelines:
        pipeline_id = pipeline.get("id")
        if not pipeline_id:
            continue

        try:
            jobs = gitlab_client.list_pipeline_jobs(project_id, pipeline_id)
        except Exception:
            had_error = True
            continue

        for job in jobs:
            if job.get("name") != UPDATE_IMAGE_TAGS_JOB or job.get("status") != "success":
                continue

            try:
                parsed = parse_update_image_tags_log(
                    gitlab_client.get_job_log(project_id, job["id"])
                )
            except Exception:
                had_error = True
                continue

            if not parsed or parsed["slot"] not in allowed_slots or parsed["slot"] in by_slot:
                continue

            by_slot[parsed["slot"]] = _slot_activity(pipeline, job, parsed)
            if len(by_slot) == len(allowed_slots):
                return by_slot, had_error

    return by_slot, had_error


def latest_auto_deploy(
    gitlab_client: Any,
    project_id: int,
    limit: int = 50,
) -> Optional[dict]:
    """
    Return the most recent successful auto deploy.

    GitLab does not expose `inputs.environment` on the pipeline detail endpoint,
    so we identify auto deploys via the same `update-image-tags` job log used
    for QA slots: auto maps to `Mode: Default (Development)` and updates
    `.../values/development.yaml`, surfaced by `parse_update_image_tags_log` as
    `slot="auto"`.

    Scans up to `limit` recent successful pipelines. Each pipeline costs one
    job-list call plus one job-log call for the update-image-tags job, so this
    is heavier than a simple pipeline-detail scan — but it is the only reliable
    signal. Returns None on error or when no auto deploy is found.
    """
    try:
        pipelines = gitlab_client.list_pipelines(project_id, status="success", limit=limit)
    except Exception:
        return None

    for pipeline in pipelines:
        pipeline_id = pipeline.get("id")
        if not pipeline_id:
            continue

        try:
            jobs = gitlab_client.list_pipeline_jobs(project_id, pipeline_id)
        except Exception:
            continue

        for job in jobs:
            if job.get("name") != UPDATE_IMAGE_TAGS_JOB or job.get("status") != "success":
                continue
            try:
                parsed = parse_update_image_tags_log(
                    gitlab_client.get_job_log(project_id, job["id"])
                )
            except Exception:
                continue
            if not parsed or parsed["slot"] != "auto":
                continue

            deployed_at = (
                job.get("finished_at")
                or job.get("created_at")
                or pipeline.get("updated_at")
                or pipeline.get("created_at")
            )
            return {
                "ref": pipeline.get("ref"),
                "deployed_at": deployed_at,
                "pipeline_id": pipeline_id,
                "pipeline_url": pipeline.get("web_url"),
                "pipeline_status": pipeline.get("status"),
                "values_file": parsed.get("values_file"),
                "tag": parsed.get("tag"),
            }

    return None


def _slot_activity(pipeline: dict, job: dict, parsed: dict) -> dict:
    ref = pipeline.get("ref") or job.get("ref")
    deployed_at = (
        job.get("finished_at")
        or job.get("created_at")
        or pipeline.get("updated_at")
        or pipeline.get("created_at")
    )
    return {
        "slot": parsed["slot"],
        "pipeline": {
            "id": pipeline.get("id"),
            "status": pipeline.get("status"),
            "ref": ref,
            "sha": pipeline.get("sha"),
            "web_url": pipeline.get("web_url"),
        },
        "deploy_job": {
            "id": job.get("id"),
            "name": job.get("name"),
            "status": job.get("status"),
            "web_url": job.get("web_url"),
        },
        "ref": ref,
        "deployed_at": deployed_at,
        "gitops_tag": parsed.get("tag"),
        "gitops_values_file": parsed.get("values_file"),
        "gitops_commit_sha": parsed.get("gitops_commit_sha"),
        "source": parsed.get("source"),
    }
