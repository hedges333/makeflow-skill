"""Repository hosting mode discovery and GitHub CI quiescence polling (GH-213, GH-47).

Detects whether a repository operates in GitHub CLI mode or Pure Git Mode and
polls PR-scoped checks, review bot comments, peer review approvals, CODEOWNERS
rules, and session parking readiness.
"""

from dataclasses import dataclass
import datetime
import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, FrozenSet, List, Optional, Tuple
import urllib.parse

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

_STATUS_CONTEXT_FAILURES: FrozenSet[str] = frozenset({
    "FAILURE",
    "ERROR",
})

_DEFAULT_BRANCH_NAMES: FrozenSet[str] = frozenset({
    "",
    "HEAD",
    "main",
    "master",
})

_PR_GRAPHQL_QUERY_TEMPLATE = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      url
      state
      updatedAt
      isDraft
      headRefName
      headRefOid
      baseRefName
      mergeable
      mergeStateStatus
      reviewDecision
__BRANCH_PROTECTION_FRAGMENT__
      commits(last: 1) {
        nodes {
          commit {
            oid
            statusCheckRollup {
              contexts(first: 100) {
                nodes {
                  __typename
                  ... on CheckRun {
                    name
                    status
                    conclusion
                    completedAt
                    startedAt
                  }
                  ... on StatusContext {
                    context
                    state
                    createdAt
                  }
                }
              }
            }
          }
        }
      }
      reviewRequests(first: 50) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Team { slug combinedSlug name }
          }
        }
      }
      latestOpinionatedReviews(first: 50) {
        nodes {
          author { login }
          state
          body
          submittedAt
          commit { oid }
        }
      }
      reviews(last: 50) {
        nodes {
          author { login }
          state
          body
          submittedAt
          commit { oid }
        }
      }
      reviewThreads(first: 100) {
        nodes {
          isResolved
          isOutdated
          path
          line
          comments(first: 20) {
            nodes {
              author { login }
              body
              createdAt
            }
          }
        }
      }
      comments(last: 100) {
        nodes {
          author { login }
          body
          createdAt
        }
      }
    }
  }
}
""".strip()

_BRANCH_PROTECTION_FRAGMENT = """      baseRef {
        branchProtectionRule {
          requiredApprovingReviewCount
          dismissesStaleReviews
          requiresCodeOwnerReviews
        }
      }"""

_PR_CORE_GRAPHQL_QUERY = _PR_GRAPHQL_QUERY_TEMPLATE.replace(
    "__BRANCH_PROTECTION_FRAGMENT__\n", ""
)
_PR_WITH_PROTECTION_GRAPHQL_QUERY = _PR_GRAPHQL_QUERY_TEMPLATE.replace(
    "__BRANCH_PROTECTION_FRAGMENT__", _BRANCH_PROTECTION_FRAGMENT
)
_PR_STATUS_GRAPHQL_QUERY = _PR_CORE_GRAPHQL_QUERY


@dataclass(frozen=True)
class BranchProtectionPolicy:
  """Immutable branch protection review rules resolved for a PR base branch.

  Attributes:
    required_approving_review_count: Minimum approvals required by GitHub.
    dismisses_stale_reviews: True when new commits dismiss prior approvals.
    requires_code_owner_reviews: True when CODEOWNERS reviews are required.
    source: Resolution source (``graphql``, ``rest``, ``fallback``, or ``none``).
  """

  required_approving_review_count: int = 0
  dismisses_stale_reviews: bool = False
  requires_code_owner_reviews: bool = False
  source: str = "none"


@dataclass(frozen=True)
class PrReviewSummary:
  """Immutable summary of human peer reviews, CODEOWNERS, and review threads.

  Attributes:
    review_decision: GitHub ``reviewDecision`` string (for example ``APPROVED``).
    approvals_count: Count of non-stale human ``APPROVED`` reviews.
    required_approvals: Effective approval threshold (GitHub + config floor).
    dismisses_stale_reviews: True when stale approvals were filtered by HEAD OID.
    require_code_owner_reviews: True when CODEOWNERS reviews are required.
    pending_reviewers: Tuple of pending requested reviewer logins or team slugs.
    changes_requested_by: Tuple of human reviewers requesting changes.
    unresolved_threads_count: Count of unresolved PR review threads.
    unresolved_threads: Tuple of passive metadata dicts for unresolved threads.
    policy_source: Branch protection policy resolution source.
  """

  review_decision: str = ""
  approvals_count: int = 0
  required_approvals: int = 0
  dismisses_stale_reviews: bool = False
  require_code_owner_reviews: bool = False
  pending_reviewers: Tuple[str, ...] = ()
  changes_requested_by: Tuple[str, ...] = ()
  unresolved_threads_count: int = 0
  unresolved_threads: Tuple[Dict[str, Any], ...] = ()
  policy_source: str = "none"

  def to_result_fields(self) -> Dict[str, Any]:
    """Serializes review fields into the camelCase poll_ci_status response keys.

    Returns:
      Dictionary of review metadata keys suitable for merging into poll output.
    """
    return {
        "reviewDecision": self.review_decision,
        "approvalsCount": self.approvals_count,
        "requiredApprovals": self.required_approvals,
        "dismissesStaleReviews": self.dismisses_stale_reviews,
        "requireCodeOwnerReviews": self.require_code_owner_reviews,
        "pendingReviewers": list(self.pending_reviewers),
        "changesRequestedBy": list(self.changes_requested_by),
        "unresolvedThreadsCount": self.unresolved_threads_count,
        "unresolvedThreads": list(self.unresolved_threads),
        "policySource": self.policy_source,
    }


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


def _detect_workspace_branch(ws: str) -> str:
  """Reads the active Git branch from ``.git/HEAD`` without spawning subprocesses.

  Supports both standard ``.git`` directories and linked Git worktree files
  pointing via ``gitdir:``.

  Args:
    ws: Workspace root directory path.

  Returns:
    Current branch name string, or empty string if unavailable or detached.
  """
  if not ws or not os.path.isdir(ws):
    return ""
  dot_git = os.path.join(ws, ".git")
  if not os.path.exists(dot_git):
    return ""
  head_path = ""
  try:
    if os.path.isdir(dot_git):
      head_path = os.path.join(dot_git, "HEAD")
    elif os.path.isfile(dot_git):
      with open(dot_git, "r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline().strip()
      if first_line.startswith("gitdir:"):
        raw_dir = first_line[len("gitdir:") :].strip()
        git_dir = (
            raw_dir if os.path.isabs(raw_dir) else os.path.join(ws, raw_dir)
        )
        head_path = os.path.join(git_dir, "HEAD")
    if not head_path or not os.path.isfile(head_path):
      return ""
    with open(head_path, "r", encoding="utf-8", errors="replace") as f:
      head_line = f.readline().strip()
    prefix = "ref: refs/heads/"
    if head_line.startswith(prefix):
      return head_line[len(prefix) :].strip()
  except OSError:
    return ""
  return ""


def _unwrap_nodes(value: Any) -> List[Dict[str, Any]]:
  """Extracts node dictionaries from either a GraphQL connection or a plain list.

  Args:
    value: GraphQL ``{"nodes": [...]}`` dict, raw list, or None.

  Returns:
    List of dictionary entries.
  """
  if isinstance(value, dict) and isinstance(value.get("nodes"), list):
    return [item for item in value["nodes"] if isinstance(item, dict)]
  if isinstance(value, list):
    return [item for item in value if isinstance(item, dict)]
  return []


def _extract_graphql_checks(pr_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
  """Extracts CheckRun and StatusContext nodes for the PR HEAD commit.

  Args:
    pr_obj: GraphQL ``pullRequest`` dictionary.

  Returns:
    List of status check context dictionaries.
  """
  commits = _unwrap_nodes(pr_obj.get("commits"))
  if commits:
    commit = commits[-1].get("commit")
    if isinstance(commit, dict):
      rollup = commit.get("statusCheckRollup")
      if isinstance(rollup, dict):
        return _unwrap_nodes(rollup.get("contexts"))
  return _unwrap_nodes(pr_obj.get("statusCheckRollup"))


def _normalize_pr_payload(raw_data: Any) -> Tuple[Dict[str, Any], bool]:
  """Normalizes GraphQL or legacy flat ``gh pr view`` JSON into one PR dict.

  Args:
    raw_data: Decoded JSON object returned by ``gh api graphql`` or ``gh pr view``.

  Returns:
    Tuple of ``(normalized_pr_dict, is_graphql)``.

  Raises:
    ValueError: When ``raw_data`` is not a dict or a GraphQL query returns null PR.
  """
  if not isinstance(raw_data, dict):
    raise ValueError("Expected JSON object from gh CLI output.")

  if "data" in raw_data and isinstance(raw_data.get("data"), dict):
    repo = raw_data["data"].get("repository")
    pr_obj = repo.get("pullRequest") if isinstance(repo, dict) else None
    if not isinstance(pr_obj, dict):
      raise ValueError("Pull request not found in GraphQL response.")
    checks = _extract_graphql_checks(pr_obj)
    base_ref = pr_obj.get("baseRef")
    bp_rule = (
        base_ref.get("branchProtectionRule")
        if isinstance(base_ref, dict)
        else None
    )
    return (
        {
            "number": pr_obj.get("number"),
            "url": pr_obj.get("url") or "",
            "state": pr_obj.get("state") or "",
            "updatedAt": pr_obj.get("updatedAt") or "",
            "isDraft": bool(pr_obj.get("isDraft", False)),
            "headRefName": pr_obj.get("headRefName") or "",
            "headRefOid": pr_obj.get("headRefOid") or "",
            "baseRefName": pr_obj.get("baseRefName") or "",
            "mergeable": pr_obj.get("mergeable"),
            "mergeStateStatus": pr_obj.get("mergeStateStatus"),
            "reviewDecision": (pr_obj.get("reviewDecision") or "").upper(),
            "branchProtectionRule": bp_rule if isinstance(bp_rule, dict) else None,
            "checks": checks,
            "reviewRequests": _unwrap_nodes(pr_obj.get("reviewRequests")),
            "latestOpinionatedReviews": _unwrap_nodes(
                pr_obj.get("latestOpinionatedReviews")
            ),
            "reviews": _unwrap_nodes(pr_obj.get("reviews")),
            "reviewThreads": _unwrap_nodes(pr_obj.get("reviewThreads")),
            "comments": _unwrap_nodes(pr_obj.get("comments")),
        },
        True,
    )

  # Legacy flat `gh pr view --json` payload fallback
  return (
      {
          "number": raw_data.get("number"),
          "url": raw_data.get("url") or "",
          "state": raw_data.get("state") or "",
          "updatedAt": raw_data.get("updatedAt") or "",
          "isDraft": bool(raw_data.get("isDraft", False)),
          "headRefName": raw_data.get("headRefName") or "",
          "headRefOid": raw_data.get("headRefOid") or "",
          "baseRefName": raw_data.get("baseRefName") or "",
          "mergeable": raw_data.get("mergeable"),
          "mergeStateStatus": raw_data.get("mergeStateStatus"),
          "reviewDecision": (raw_data.get("reviewDecision") or "").upper(),
          "branchProtectionRule": None,
          "checks": _unwrap_nodes(raw_data.get("statusCheckRollup")),
          "reviewRequests": _unwrap_nodes(raw_data.get("reviewRequests")),
          "latestOpinionatedReviews": _unwrap_nodes(
              raw_data.get("latestOpinionatedReviews")
          ),
          "reviews": _unwrap_nodes(raw_data.get("reviews")),
          "reviewThreads": _unwrap_nodes(raw_data.get("reviewThreads")),
          "comments": _unwrap_nodes(raw_data.get("comments")),
      },
      False,
  )


def _is_branch_protection_permission_error(errors: Any) -> bool:
  """Returns True when GraphQL errors are solely FORBIDDEN/branchProtectionRule denials.

  Args:
    errors: Raw ``errors`` field from a GraphQL JSON response.

  Returns:
    True when every error entry represents a non-admin permission denial on
    ``branchProtectionRule``.
  """
  if not isinstance(errors, list) or not errors:
    return False
  for err in errors:
    if not isinstance(err, dict):
      return False
    err_type = str(err.get("type") or "").upper()
    path = err.get("path")
    path_has_bp = isinstance(path, list) and any(
        str(seg) == "branchProtectionRule" for seg in path
    )
    msg = str(err.get("message") or "").lower()
    is_bp_denied = (
        err_type == "FORBIDDEN"
        or path_has_bp
        or "branchprotectionrule" in msg
        or "not accessible by" in msg
    )
    if not is_bp_denied:
      return False
  return True


def _parse_gh_graphql_result(
    res: subprocess.CompletedProcess,
) -> Tuple[Dict[str, Any], bool]:
  """Parses a ``gh api graphql`` CompletedProcess, allowing partial FORBIDDEN data.

  When a non-admin token lacks ``Administration: Read`` on
  ``baseRef.branchProtectionRule``, ``gh api graphql`` prints partial ``data``
  alongside a ``FORBIDDEN`` ``errors`` array and exits non-zero. This helper
  accepts that partial ``pullRequest`` payload so callers degrade cleanly to
  REST/local fallbacks instead of aborting.

  Args:
    res: ``subprocess.CompletedProcess`` returned by ``gh api graphql``.

  Returns:
    Tuple of ``(normalized_pr_dict, is_graphql)``.

  Raises:
    subprocess.CalledProcessError: When ``res.returncode != 0`` and no usable
      partial ``pullRequest`` payload was returned.
    json.JSONDecodeError: When stdout is malformed JSON on exit 0.
    ValueError: When JSON structure is invalid or pullRequest is null.
  """
  if res.returncode == 0:
    raw_data = json.loads(res.stdout)
    return _normalize_pr_payload(raw_data)

  raw_stdout = (res.stdout or "").strip()
  if raw_stdout:
    try:
      partial = json.loads(raw_stdout)
    except (json.JSONDecodeError, ValueError, TypeError):
      partial = None
    if isinstance(partial, dict) and _is_branch_protection_permission_error(
        partial.get("errors")
    ):
      data_node = partial.get("data")
      repo_node = (
          data_node.get("repository") if isinstance(data_node, dict) else None
      )
      pr_node = (
          repo_node.get("pullRequest") if isinstance(repo_node, dict) else None
      )
      if isinstance(pr_node, dict):
        return _normalize_pr_payload(partial)

  raise subprocess.CalledProcessError(
      returncode=res.returncode,
      cmd=getattr(res, "args", ["gh", "api", "graphql"]),
      output=res.stdout,
      stderr=res.stderr,
  )


def _fetch_rest_branch_protection(
    base_ref: str, ws: str
) -> BranchProtectionPolicy:
  """Queries GitHub REST branch protection rules with graceful 403/404 fallback.

  Args:
    base_ref: Target base branch name (for example ``main`` or ``release/2.0``).
    ws: Workspace root directory path.

  Returns:
    ``BranchProtectionPolicy`` resolved from REST, or a safe ``fallback`` policy
    when access is denied (HTTP 403), unprotected (HTTP 404), or unreadable.
  """
  if not base_ref:
    return BranchProtectionPolicy(source="fallback")
  encoded_ref = urllib.parse.quote(base_ref, safe="")
  try:
    res = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/branches/{encoded_ref}/protection",
        ],
        cwd=ws,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
      return BranchProtectionPolicy(source="fallback")
    parsed = json.loads(res.stdout or "{}")
    if not isinstance(parsed, dict):
      return BranchProtectionPolicy(source="fallback")
    pr_reviews = parsed.get("required_pull_request_reviews")
    if not isinstance(pr_reviews, dict):
      return BranchProtectionPolicy(source="rest")
    return BranchProtectionPolicy(
        required_approving_review_count=_parse_non_negative_int(
            pr_reviews.get("required_approving_review_count"), 0
        ),
        dismisses_stale_reviews=bool(
            pr_reviews.get("dismiss_stale_reviews", False)
        ),
        requires_code_owner_reviews=bool(
            pr_reviews.get("require_code_owner_reviews", False)
        ),
        source="rest",
    )
  except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
    return BranchProtectionPolicy(source="fallback")


def _resolve_branch_protection_policy(
    pr_data: Dict[str, Any],
    is_graphql: bool,
    enable_peer_reviews: bool,
    ws: str,
) -> BranchProtectionPolicy:
  """Resolves branch protection rules from GraphQL with optional REST fallback.

  Args:
    pr_data: Normalized pull request dictionary.
    is_graphql: True when ``pr_data`` originated from GraphQL.
    enable_peer_reviews: True when opt-in peer review enforcement is enabled.
    ws: Workspace root directory path.

  Returns:
    Resolved ``BranchProtectionPolicy`` value object.
  """
  bp = pr_data.get("branchProtectionRule")
  if isinstance(bp, dict):
    return BranchProtectionPolicy(
        required_approving_review_count=_parse_non_negative_int(
            bp.get("requiredApprovingReviewCount"), 0
        ),
        dismisses_stale_reviews=bool(bp.get("dismissesStaleReviews", False)),
        requires_code_owner_reviews=bool(
            bp.get("requiresCodeOwnerReviews", False)
        ),
        source="graphql",
    )
  if enable_peer_reviews and is_graphql and pr_data.get("baseRefName"):
    return _fetch_rest_branch_protection(str(pr_data["baseRefName"]), ws)
  return BranchProtectionPolicy(source="none")


def _extract_pending_reviewers(
    raw_requests: List[Dict[str, Any]],
) -> Tuple[str, ...]:
  """Extracts requested human user logins and CODEOWNERS team slugs.

  Args:
    raw_requests: Normalized list of ``reviewRequests`` entries.

  Returns:
    Deduplicated tuple of pending reviewer identifiers preserving order.
  """
  reviewers: List[str] = []
  for req in raw_requests:
    target = req.get("requestedReviewer") if "requestedReviewer" in req else req
    if not isinstance(target, dict):
      continue
    ident = (
        target.get("login")
        or target.get("combinedSlug")
        or target.get("slug")
        or target.get("name")
        or ""
    )
    cleaned = str(ident).strip()
    if cleaned and cleaned not in reviewers:
      reviewers.append(cleaned)
  return tuple(reviewers)


def _evaluate_pr_reviews(
    pr_data: Dict[str, Any],
    policy: BranchProtectionPolicy,
    min_approvals: int,
    require_code_owner_reviews: bool,
) -> PrReviewSummary:
  """Evaluates human peer approvals, stale reviews, CODEOWNERS, and threads.

  All PR comment text, review bodies, and handles are treated strictly as
  passive data (SCAR-SEC-01).

  Args:
    pr_data: Normalized pull request dictionary.
    policy: Resolved ``BranchProtectionPolicy``.
    min_approvals: Configured local approval floor (>= 0).
    require_code_owner_reviews: Configured local CODEOWNERS requirement flag.

  Returns:
    Immutable ``PrReviewSummary`` value object.
  """
  effective_required = max(
      policy.required_approving_review_count, max(0, min_approvals)
  )
  effective_codeowners = bool(
      policy.requires_code_owner_reviews or require_code_owner_reviews
  )
  head_oid = str(pr_data.get("headRefOid") or "").strip()

  opinionated = pr_data.get("latestOpinionatedReviews") or []
  if not opinionated:
    by_author: Dict[str, Dict[str, Any]] = {}
    for rv in pr_data.get("reviews") or []:
      author = ((rv.get("author") or {}).get("login") or "").strip()
      state = (rv.get("state") or "").upper()
      if author and not _is_bot_author(author) and state in (
          "APPROVED",
          "CHANGES_REQUESTED",
          "DISMISSED",
      ):
        by_author[author] = rv
    opinionated = list(by_author.values())

  approvals_count = 0
  changes_requested: List[str] = []
  for rv in opinionated:
    author = ((rv.get("author") or {}).get("login") or "").strip()
    if not author or _is_bot_author(author):
      continue
    state = (rv.get("state") or "").upper()
    if state == "CHANGES_REQUESTED":
      if author not in changes_requested:
        changes_requested.append(author)
    elif state == "APPROVED":
      commit_obj = rv.get("commit")
      review_oid = (
          str(commit_obj.get("oid") or "").strip()
          if isinstance(commit_obj, dict)
          else ""
      )
      if (
          policy.dismisses_stale_reviews
          and head_oid
          and review_oid != head_oid
      ):
        continue
      approvals_count += 1

  pending_reviewers = _extract_pending_reviewers(
      pr_data.get("reviewRequests") or []
  )

  unresolved_threads: List[Dict[str, Any]] = []
  for thread in pr_data.get("reviewThreads") or []:
    if bool(thread.get("isResolved", False)):
      continue
    thread_comments = _unwrap_nodes(thread.get("comments"))
    first_cm = thread_comments[0] if thread_comments else {}
    author = ((first_cm.get("author") or {}).get("login") or "").strip()
    unresolved_threads.append({
        "path": str(thread.get("path") or ""),
        "line": thread.get("line"),
        "isOutdated": bool(thread.get("isOutdated", False)),
        "author": author,
        "body": str(first_cm.get("body") or ""),
    })

  return PrReviewSummary(
      review_decision=str(pr_data.get("reviewDecision") or "").upper(),
      approvals_count=approvals_count,
      required_approvals=effective_required,
      dismisses_stale_reviews=policy.dismisses_stale_reviews,
      require_code_owner_reviews=effective_codeowners,
      pending_reviewers=pending_reviewers,
      changes_requested_by=tuple(changes_requested),
      unresolved_threads_count=len(unresolved_threads),
      unresolved_threads=tuple(unresolved_threads),
      policy_source=policy.source,
  )


def _evaluate_checks(
    checks: List[Dict[str, Any]],
) -> Tuple[bool, bool, List[str], Optional[float]]:
  """Evaluates both CheckRun and legacy StatusContext items.

  Args:
    checks: Normalized list of check run and status context dictionaries.

  Returns:
    Tuple of ``(all_completed, has_failure, failed_checks, max_completed_ts)``.
  """
  all_completed = True
  has_failure = False
  failed_checks: List[str] = []
  max_check_completed_at: Optional[float] = None

  for check in checks:
    is_status_context = check.get("__typename") == "StatusContext" or (
        "state" in check and "status" not in check
    )
    if is_status_context:
      state = (check.get("state") or "").upper()
      name = check.get("context") or check.get("name") or "status"
      if state in _STATUS_CONTEXT_FAILURES:
        has_failure = True
        failed_checks.append(name)
      elif state != "SUCCESS":
        all_completed = False
    else:
      status = (check.get("status") or "").upper()
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

  return all_completed, has_failure, failed_checks, max_check_completed_at


def _build_parking_reason(
    ci_status: str,
    quiescence_ok: bool,
    review_summary: PrReviewSummary,
    is_draft: bool,
    merge_state: str,
) -> str:
  """Builds a concise human-readable parking reason for pending PR conditions.

  Args:
    ci_status: Current CI status string.
    quiescence_ok: True when post-CI quiescence is satisfied.
    review_summary: Evaluated ``PrReviewSummary``.
    is_draft: True when the pull request is still in draft state.
    merge_state: GitHub ``mergeStateStatus`` string.

  Returns:
    Short description of the primary condition blocking landing.
  """
  if ci_status in ("PENDING", "TIMEOUT"):
    return "Awaiting long-running CI pipeline"
  if not quiescence_ok:
    return "Awaiting CI bot quiescence window"
  if is_draft:
    return "Pull request is still marked as draft"
  if (
      review_summary.required_approvals > 0
      and review_summary.approvals_count < review_summary.required_approvals
  ):
    return (
        f"Awaiting peer review approvals: "
        f"{review_summary.approvals_count}/{review_summary.required_approvals}"
    )
  if review_summary.require_code_owner_reviews and review_summary.pending_reviewers:
    return (
        "Awaiting CODEOWNERS review: "
        + ", ".join(review_summary.pending_reviewers)
    )
  if review_summary.unresolved_threads_count > 0:
    return (
        f"Awaiting resolution of {review_summary.unresolved_threads_count} "
        "review thread(s)"
    )
  if review_summary.review_decision == "REVIEW_REQUIRED":
    return "Awaiting required peer review approval"
  if merge_state == "BLOCKED":
    return "Pull request merge is blocked by repository protection rules"
  return "Awaiting external review or CI completion"


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
    expected_branch: Optional[str] = None,
    enable_peer_reviews: Optional[bool] = None,
    min_approvals: Optional[int] = None,
    require_code_owner_reviews: Optional[bool] = None,
    enable_session_parking: Optional[bool] = None,
) -> Dict[str, Any]:
  """Polls PR-scoped GitHub checks, bot comments, and opt-in peer reviews.

  Preserves sole-developer zero-wait defaults when ``ci.enablePeerReviews`` and
  ``ci.enableSessionParking`` are ``false`` while always enforcing PR-branch
  firewalling (``headRefName`` verification).

  Args:
    pr_number: Pull request number, or 0 for pure-Git fallback.
    ws: Workspace root directory path.
    quiescence_sec: Optional override for post-CI quiescence seconds (>= 0).
    poll_interval_sec: Optional override for polling sleep interval seconds (>= 1).
    max_wait_sec: Maximum total seconds before timeout.
    hosting_mode: Optional hosting mode override (``"github"`` or ``"pure_git"``).
    expected_branch: Optional expected PR head branch name for firewalling.
    enable_peer_reviews: Optional override for ``ci.enablePeerReviews``.
    min_approvals: Optional override for ``ci.minApprovals``.
    require_code_owner_reviews: Optional override for ``ci.requireCodeOwnerReviews``.
    enable_session_parking: Optional override for ``ci.enableSessionParking``.

  Returns:
    Dictionary describing CI completion, bot comments, peer review status,
    branch firewalling, session parking recommendation, and landing readiness.
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
  eff_peer_reviews = (
      bool(enable_peer_reviews)
      if enable_peer_reviews is not None
      else bool(ci_cfg.get("enablePeerReviews", False))
  )
  eff_min_approvals = (
      _parse_non_negative_int(min_approvals, 0)
      if min_approvals is not None
      else _parse_non_negative_int(ci_cfg.get("minApprovals"), 0)
  )
  eff_codeowners = (
      bool(require_code_owner_reviews)
      if require_code_owner_reviews is not None
      else bool(ci_cfg.get("requireCodeOwnerReviews", False))
  )
  eff_parking = (
      bool(enable_session_parking)
      if enable_session_parking is not None
      else bool(ci_cfg.get("enableSessionParking", False))
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
        "sessionStatus": "READY",
        "parkingRecommended": False,
        "unresolvedBotCommentsCount": 0,
        "botComments": [],
        "message": (
            "Pure Git Mode: Repository is not GitHub-hosted, GitHub CLI is not"
            " available, or PR number is 0. Run local tests (make all) for"
            " verification."
        ),
    }

  resolved_expected_branch = (
      expected_branch.strip()
      if isinstance(expected_branch, str) and expected_branch.strip()
      else ""
  )
  if not resolved_expected_branch:
    detected = _detect_workspace_branch(ws)
    if detected not in _DEFAULT_BRANCH_NAMES:
      resolved_expected_branch = detected

  start_time = time.time()
  ci_completed_at: Optional[float] = None
  last_common_meta: Dict[str, Any] = {}
  graphql_query = (
      _PR_WITH_PROTECTION_GRAPHQL_QUERY
      if eff_peer_reviews
      else _PR_CORE_GRAPHQL_QUERY
  )

  while True:
    now = time.time()
    elapsed_total = now - start_time
    if elapsed_total > max_wait_sec:
      return {
          "mode": "github",
          "ciStatus": "TIMEOUT",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "sessionStatus": "PARKED" if eff_parking else "PENDING",
          "parkingRecommended": eff_parking,
          "parkingReason": (
              "Awaiting long-running CI pipeline (timed out)"
              if eff_parking
              else ""
          ),
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          **last_common_meta,
          "message": (
              f"Timed out after {int(elapsed_total)}s waiting for CI or"
              " quiescence."
          ),
      }

    try:
      cmd = [
          "gh",
          "api",
          "graphql",
          "-F",
          "owner={owner}",
          "-F",
          "name={repo}",
          "-F",
          f"number={pr_number}",
          "-f",
          f"query={graphql_query}",
      ]
      res = subprocess.run(
          cmd, cwd=ws, capture_output=True, text=True, check=False
      )
      pr_data, is_graphql = _parse_gh_graphql_result(res)
    except subprocess.CalledProcessError as exc:
      stderr = (exc.stderr or "").strip()
      return {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "sessionStatus": "ERROR",
          "parkingRecommended": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"gh api graphql failed: {stderr}",
      }
    except (
        subprocess.SubprocessError,
        OSError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as exc:
      return {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "sessionStatus": "ERROR",
          "parkingRecommended": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": f"Error parsing gh output: {exc}",
      }

    pr_head_branch = str(pr_data.get("headRefName") or "").strip()
    pr_url = str(pr_data.get("url") or "").strip()
    pr_state = str(pr_data.get("state") or "").upper()
    if (
        resolved_expected_branch
        and pr_head_branch
        and pr_head_branch != resolved_expected_branch
    ):
      return {
          "mode": "github",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "branchMismatch": True,
          "expectedBranch": resolved_expected_branch,
          "actualBranch": pr_head_branch,
          "headBranch": pr_head_branch,
          "prUrl": pr_url,
          "prState": pr_state,
          "sessionStatus": "ERROR",
          "parkingRecommended": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": (
              f"PR #{pr_number} headRefName '{pr_head_branch}' does not match"
              f" expected branch '{resolved_expected_branch}' (PR branch"
              " firewall)."
          ),
      }

    checks = pr_data.get("checks") or []
    all_completed, has_failure, failed_checks, max_check_completed_at = (
        _evaluate_checks(checks)
    )

    policy = _resolve_branch_protection_policy(
        pr_data, is_graphql, eff_peer_reviews, ws
    )
    review_summary = _evaluate_pr_reviews(
        pr_data,
        policy,
        min_approvals=eff_min_approvals,
        require_code_owner_reviews=eff_codeowners,
    )
    common_meta: Dict[str, Any] = {
        "prUrl": pr_url,
        "prState": pr_state,
        "headBranch": pr_head_branch,
        "isDraft": bool(pr_data.get("isDraft", False)),
        "branchMismatch": False,
        "mergeable": pr_data.get("mergeable"),
        "mergeStateStatus": pr_data.get("mergeStateStatus"),
        **review_summary.to_result_fields(),
    }
    last_common_meta = common_meta

    if pr_state in ("MERGED", "CLOSED"):
      ci_terminal = (
          "FAILURE" if has_failure else ("SUCCESS" if all_completed else "PENDING")
      )
      return {
          "mode": "github",
          "ciStatus": ci_terminal,
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "sessionStatus": f"PR_{pr_state}",
          "parkingRecommended": False,
          "parkingReason": "",
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          **common_meta,
          "message": f"Pull request #{pr_number} is already {pr_state}.",
      }

    if has_failure:
      return {
          "mode": "github",
          "ciStatus": "FAILURE",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "failedChecks": failed_checks,
          "sessionStatus": "REMEDIATION_REQUIRED",
          "parkingRecommended": False,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          **common_meta,
          "message": f"CI checks failed: {failed_checks}",
      }

    raw_comments = pr_data.get("comments") or []
    raw_reviews = pr_data.get("reviews") or []
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
        state = (rv.get("state") or "").upper()
        if state not in ("APPROVED", "DISMISSED"):
          bot_comments.append({
              "type": "review",
              "author": author,
              "body": rv.get("body", ""),
              "state": state,
              "submittedAt": rv.get("submittedAt", ""),
          })

    mergeable_str = str(pr_data.get("mergeable") or "").upper()
    merge_state_str = str(pr_data.get("mergeStateStatus") or "").upper()
    has_conflict = mergeable_str == "CONFLICTING" or merge_state_str == "DIRTY"
    has_changes_requested = eff_peer_reviews and (
        review_summary.review_decision == "CHANGES_REQUESTED"
        or bool(review_summary.changes_requested_by)
    )

    has_checks = len(checks) > 0 or elapsed_total >= 15 or max_wait_sec <= 1
    if all_completed and has_checks:
      if ci_completed_at is None:
        if max_check_completed_at is not None:
          ci_completed_at = min(now, max_check_completed_at)
        else:
          pr_updated_ts = _parse_iso_timestamp(pr_data.get("updatedAt"))
          ci_completed_at = (
              min(now, pr_updated_ts) if pr_updated_ts is not None else now
          )

      latest_activity = ci_completed_at
      for bc in bot_comments:
        ts_str = bc.get("createdAt") or bc.get("submittedAt")
        ts = _parse_iso_timestamp(ts_str)
        if ts is not None and ts > latest_activity:
          latest_activity = min(now, ts)

      elapsed_since_activity = now - latest_activity
      quiescence_ok = elapsed_since_activity >= q_sec
      if quiescence_ok or max_wait_sec <= 1:
        remediation_required = (
            has_conflict or has_changes_requested or len(bot_comments) > 0
        )

        peer_review_pending = False
        if eff_peer_reviews:
          approvals_needed = (
              review_summary.required_approvals > 0
              and review_summary.approvals_count
              < review_summary.required_approvals
          )
          review_required_gh = (
              review_summary.review_decision == "REVIEW_REQUIRED"
          )
          codeowners_pending = (
              review_summary.require_code_owner_reviews
              and bool(review_summary.pending_reviewers)
          )
          threads_unresolved = review_summary.unresolved_threads_count > 0
          draft_pr = bool(pr_data.get("isDraft", False))
          merge_blocked = merge_state_str == "BLOCKED"
          peer_review_pending = (
              approvals_needed
              or review_required_gh
              or codeowners_pending
              or threads_unresolved
              or draft_pr
              or merge_blocked
          )

        ready_to_land = (
            quiescence_ok
            and not remediation_required
            and not peer_review_pending
        )

        if remediation_required:
          session_status = "REMEDIATION_REQUIRED"
          parking_recommended = False
          parking_reason = ""
        elif ready_to_land:
          session_status = "READY"
          parking_recommended = False
          parking_reason = ""
        else:
          parking_reason = _build_parking_reason(
              "SUCCESS",
              quiescence_ok,
              review_summary,
              bool(pr_data.get("isDraft", False)),
              merge_state_str,
          )
          parking_recommended = eff_parking
          session_status = (
              "PARKED"
              if eff_parking
              else ("WAITING_REVIEW" if peer_review_pending else "PENDING")
          )

        if ready_to_land:
          msg = (
              "CI completed successfully and"
              f" {int(elapsed_since_activity)}s quiescence satisfied."
          )
        elif has_changes_requested:
          msg = (
              "Changes requested by reviewer(s): "
              f"{list(review_summary.changes_requested_by)}."
          )
        elif has_conflict:
          msg = "Pull request has merge conflicts requiring remediation."
        elif not quiescence_ok:
          msg = (
              f"CI completed; {int(elapsed_since_activity)}s of {q_sec}s"
              " quiescence elapsed."
          )
        elif peer_review_pending:
          msg = parking_reason + "."
        else:
          msg = (
              "CI completed successfully with"
              f" {len(bot_comments)} unresolved bot comment(s)."
          )

        return {
            "mode": "github",
            "ciStatus": "SUCCESS",
            "quiescenceSatisfied": quiescence_ok,
            "readyToLand": ready_to_land,
            "sessionStatus": session_status,
            "parkingRecommended": parking_recommended,
            "parkingReason": parking_reason if parking_recommended else "",
            "unresolvedBotCommentsCount": len(bot_comments),
            "botComments": bot_comments,
            **common_meta,
            "message": msg,
        }

    if max_wait_sec <= 1:
      ci_status = "PENDING" if not all_completed else "SUCCESS"
      if has_changes_requested or has_conflict:
        remediation_msg = (
            "Changes requested by reviewer(s): "
            f"{list(review_summary.changes_requested_by)}."
            if has_changes_requested
            else "Pull request has merge conflicts requiring remediation."
        )
        return {
            "mode": "github",
            "ciStatus": ci_status,
            "quiescenceSatisfied": False,
            "readyToLand": False,
            "sessionStatus": "REMEDIATION_REQUIRED",
            "parkingRecommended": False,
            "parkingReason": "",
            "unresolvedBotCommentsCount": len(bot_comments),
            "botComments": bot_comments,
            **common_meta,
            "message": remediation_msg,
        }
      parking_reason = _build_parking_reason(
          ci_status,
          False,
          review_summary,
          bool(pr_data.get("isDraft", False)),
          merge_state_str,
      )
      return {
          "mode": "github",
          "ciStatus": ci_status,
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "sessionStatus": "PARKED" if eff_parking else "PENDING",
          "parkingRecommended": eff_parking,
          "parkingReason": parking_reason if eff_parking else "",
          "unresolvedBotCommentsCount": len(bot_comments),
          "botComments": bot_comments,
          **common_meta,
          "message": "CI in progress or awaiting quiescence window.",
      }

    time.sleep(p_sec)
