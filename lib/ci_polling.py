"""Repository hosting mode discovery and GitHub CI quiescence polling (GH-213).

Detects whether a repository operates in GitHub CLI mode or Pure Git Mode and
polls pull request status checks, review bot comments, and quiescence windows.
"""

import datetime
import json
import shutil
import subprocess
import time
from typing import Any, Dict, FrozenSet, List, Optional

from lib.config import (
    _parse_non_negative_int,
    _parse_positive_int,
    resolve_workflow_config,
)

_BOT_AUTHOR_LOGINS: FrozenSet[str] = frozenset({
    "github-actions",
    "coderabbitai",
    "codecov",
    "sonarcloud",
    "gemini-code-assist",
    "copilot",
})

_FAILURE_CONCLUSIONS: FrozenSet[str] = frozenset({
    "FAILURE",
    "CANCELLED",
    "TIMED_OUT",
    "STARTUP_FAILURE",
    "ACTION_REQUIRED",
})


def _parse_iso_timestamp(ts_str: Optional[str]) -> Optional[float]:
  """Parses an ISO-8601 timestamp string into a POSIX epoch float.

  Args:
    ts_str: ISO-8601 timestamp string (optionally ending in ``Z``), or None.

  Returns:
    POSIX timestamp float, or None when ``ts_str`` is empty or invalid.
  """
  if not ts_str:
    return None
  try:
    dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return dt.timestamp()
  except (ValueError, TypeError, OSError):
    return None


def _is_bot_author(author: str) -> bool:
  """Checks whether a GitHub login belongs to an automated review or CI bot.

  Args:
    author: GitHub login string from a PR comment or review payload.

  Returns:
    True when the login contains ``[bot]`` or matches a known review bot login.
  """
  lowered = (author or "").lower()
  return "[bot]" in lowered or lowered in _BOT_AUTHOR_LOGINS


def detect_hosting_mode(repo_dir: str = ".") -> str:
  """Detects whether ``repo_dir`` uses GitHub CLI hosting or Pure Git Mode.

  Args:
    repo_dir: Path to the Git repository directory to inspect.

  Returns:
    ``"github"`` if ``origin`` points to GitHub, ``gh`` is installed on
    ``PATH``, ``gh auth status`` succeeds, and ``gh repo view`` succeeds.
    Returns ``"pure_git"`` otherwise.
  """
  try:
    res = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if res.returncode != 0:
      return "pure_git"
    remote_url = (res.stdout or "").strip()
    if not remote_url:
      return "pure_git"
    if "github.com" not in remote_url.lower():
      return "pure_git"

    if not shutil.which("gh"):
      return "pure_git"

    auth_res = subprocess.run(
        ["gh", "auth", "status"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if auth_res.returncode != 0:
      return "pure_git"

    repo_res = subprocess.run(
        ["gh", "repo", "view"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if repo_res.returncode != 0:
      return "pure_git"

    return "github"
  except (subprocess.SubprocessError, OSError):
    return "pure_git"


def poll_ci_status(
    pr_number: int,
    ws: str,
    quiescence_sec: Optional[int] = None,
    poll_interval_sec: Optional[int] = None,
    max_wait_sec: int = 600,
    hosting_mode: Optional[str] = None,
) -> Dict[str, Any]:
  """Polls GitHub pull request checks and review comments until CI and quiescence pass.

  Args:
    pr_number: Pull request number, or 0 for pure-Git fallback.
    ws: Workspace root directory path.
    quiescence_sec: Optional override for post-CI quiescence seconds (>= 0).
    poll_interval_sec: Optional override for polling sleep interval seconds (>= 1).
    max_wait_sec: Maximum total seconds before timeout.
    hosting_mode: Optional hosting mode override (``"github"`` or ``"pure_git"``).

  Returns:
    Dictionary describing CI completion, bot comments, and landing readiness.
  """
  cfg = resolve_workflow_config(ws)
  ci_cfg = cfg.get("ci", {})
  default_q = _parse_non_negative_int(ci_cfg.get("quiescenceSeconds"), 180)
  default_p = _parse_positive_int(ci_cfg.get("pollIntervalSeconds"), 30)
  q_sec = (
      _parse_non_negative_int(quiescence_sec, default_q)
      if quiescence_sec is not None
      else default_q
  )
  p_sec = (
      _parse_positive_int(poll_interval_sec, default_p)
      if poll_interval_sec is not None
      else default_p
  )

  effective_mode = (
      hosting_mode if hosting_mode is not None else detect_hosting_mode(ws)
  )
  if effective_mode == "pure_git" or pr_number <= 0:
    return {
        "mode": "pure_git",
        "ciStatus": "SKIPPED",
        "quiescenceSatisfied": True,
        "readyToLand": True,
        "unresolvedBotCommentsCount": 0,
        "botComments": [],
        "message": (
            "Pure Git Mode: Repository is not GitHub-hosted, GitHub CLI is not"
            " available, or PR number is 0. Run local tests (make all) for"
            " verification."
        ),
    }

  start_time = time.time()
  ci_completed_at: Optional[float] = None

  while True:
    now = time.time()
    elapsed_total = now - start_time
    if elapsed_total > max_wait_sec:
      return {
          "mode": "github",
          "ciStatus": "TIMEOUT",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": (
              f"Timed out after {int(elapsed_total)}s waiting for CI or"
              " quiescence."
          ),
      }

    try:
      cmd = [
          "gh",
          "pr",
          "view",
          str(pr_number),
          "--json",
          "statusCheckRollup,comments,reviews,state,mergeable,mergeStateStatus",
      ]
      res = subprocess.run(
          cmd, cwd=ws, capture_output=True, text=True, check=True
      )
      data = json.loads(res.stdout)
    except subprocess.CalledProcessError as exc:
      stderr = (exc.stderr or "").strip()
      return {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"gh pr view failed: {stderr}",
      }
    except Exception as exc:
      return {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"Error parsing gh output: {exc}",
      }

    checks = data.get("statusCheckRollup") or []
    all_completed = True
    has_failure = False
    failed_checks: List[str] = []
    max_check_completed_at: Optional[float] = None

    for check in checks:
      status = check.get("status", "").upper()
      conclusion = (check.get("conclusion") or "").upper()
      name = check.get("name") or check.get("context") or "check"
      if status != "COMPLETED":
        all_completed = False
      elif conclusion in _FAILURE_CONCLUSIONS:
        has_failure = True
        failed_checks.append(name)

      completed_str = (
          check.get("completedAt")
          or check.get("updatedAt")
          or check.get("createdAt")
      )
      ts = _parse_iso_timestamp(completed_str)
      if ts is not None and (
          max_check_completed_at is None or ts > max_check_completed_at
      ):
        max_check_completed_at = ts

    if has_failure:
      return {
          "mode": "github",
          "ciStatus": "FAILURE",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "failedChecks": failed_checks,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"CI checks failed: {failed_checks}",
      }

    raw_comments = data.get("comments") or []
    raw_reviews = data.get("reviews") or []
    bot_comments: List[Dict[str, Any]] = []

    for cm in raw_comments:
      author = (cm.get("author") or {}).get("login", "")
      if _is_bot_author(author):
        bot_comments.append({
            "type": "comment",
            "author": author,
            "body": cm.get("body", ""),
            "createdAt": cm.get("createdAt", ""),
        })

    for rv in raw_reviews:
      author = (rv.get("author") or {}).get("login", "")
      if _is_bot_author(author):
        state = rv.get("state", "").upper()
        if state not in ("APPROVED", "DISMISSED"):
          bot_comments.append({
              "type": "review",
              "author": author,
              "body": rv.get("body", ""),
              "state": state,
              "submittedAt": rv.get("submittedAt", ""),
          })

    has_checks = len(checks) > 0 or elapsed_total >= 15
    if all_completed and has_checks:
      if ci_completed_at is None:
        if max_check_completed_at is not None:
          ci_completed_at = min(now, max_check_completed_at)
        else:
          ci_completed_at = now

      latest_activity = ci_completed_at
      for bc in bot_comments:
        ts_str = bc.get("createdAt") or bc.get("submittedAt")
        ts = _parse_iso_timestamp(ts_str)
        if ts is not None and ts > latest_activity:
          latest_activity = min(now, ts)

      elapsed_since_activity = now - latest_activity
      if elapsed_since_activity >= q_sec or max_wait_sec <= 1:
        return {
            "mode": "github",
            "ciStatus": "SUCCESS",
            "quiescenceSatisfied": True,
            "readyToLand": len(bot_comments) == 0,
            "unresolvedBotCommentsCount": len(bot_comments),
            "botComments": bot_comments,
            "mergeable": data.get("mergeable"),
            "mergeStateStatus": data.get("mergeStateStatus"),
            "message": (
                "CI completed successfully and"
                f" {int(elapsed_since_activity)}s quiescence satisfied."
            ),
        }

    if max_wait_sec <= 1:
      return {
          "mode": "github",
          "ciStatus": "PENDING" if not all_completed else "SUCCESS",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "unresolvedBotCommentsCount": len(bot_comments),
          "botComments": bot_comments,
          "message": "CI in progress or awaiting quiescence window.",
      }

    time.sleep(p_sec)
