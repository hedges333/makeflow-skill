#!/usr/bin/env python3
"""Makefile Workflow MCP Server.

Implements Model Context Protocol (MCP) JSON-RPC 2.0 over standard input/output.
Provides state queries, DAG transition checks, Git tree checks, safety checks,
and user configuration lookup.
"""

import copy
import datetime
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

try:
  from lib.scars_db import (
      load_session_scars,
      partition_session_scars,
      query_scars,
      resolve_scars_db_path,
      resolve_scars_db_paths,
      save_session_scars,
  )
except ImportError:
  query_scars = None
  resolve_scars_db_path = None
  resolve_scars_db_paths = None
  load_session_scars = None
  save_session_scars = None
  partition_session_scars = None

from lib.config import (
    _normalize_str_list,
    _parse_csv_env,
    _parse_non_negative_int,
    _parse_positive_int,
    deep_merge,
    is_plugin_dir,
    is_ticket_active,
    load_default_config,
    map_to_agy_tier,
    resolve_workflow_config,
    resolve_workspace_dir,
)
from lib.shell_safety import (
    DB_TOOLS,
    ENV_ASSIGN_RE,
    GIT_GLOBAL_VALUE_FLAGS,
    HOST_VALUE_RE,
    INLINE_QUERY_RE,
    PROD_TOKEN_RE,
    QUOTED_ARG_PAT,
    SHELL_KEYWORDS,
    SQLITE_CMD_RE,
    URI_USERINFO_RE,
    _contains_identifier,
    _evaluate_mcp_single_subcommand as _evaluate_single_subcommand,
    _expand_shell_subcommands,
    _extract_shell_substitutions,
    _has_clean_force_config,
    _has_prod_host_token,
    _has_push_force_config,
    _parse_git_subcommand,
    _preprocess_shell,
    _split_git_args,
    _split_shell_subcommands,
    _strip_identifier,
    _strip_shell_redirections,
    _tokenize_subcommand,
    _unwrap_command_tokens,
    evaluate_mcp_database_safety as evaluate_database_safety,
    is_database_command,
)
from lib.turn_boundary import (
    PAUSE_GATE_TARGETS,
    clear_gate_entry,
    record_gate_entry,
    verify_mcp_gate_exit,
)
from lib.workflow_gates import (
    fetch_and_inspect_main_sync,
    inspect_main_sync,
)
from lib.target90_guard import (
    is_target_90_state_dir,
    iter_subcommand_tokens_with_target90,
)

DEFAULT_PLUGIN_VERSION = "1.4.1"


def get_plugin_version(plugin_path=None):
  """Return plugin version from plugin.json with safe fallback when unreadable."""
  target = plugin_path or os.path.join(_REPO_ROOT, "plugin.json")
  try:
    with open(target, "r", encoding="utf-8") as f:
      data = json.load(f)
    if isinstance(data, dict):
      version = data.get("version")
      if isinstance(version, str) and version.strip():
        return version.strip()
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
    pass
  return DEFAULT_PLUGIN_VERSION

ALL_TARGETS = {
    "T00",
    "T10",
    "T15",
    "T20",
    "T30",
    "T40",
    "T42",
    "T45",
    "T50",
    "T60",
    "T70",
    "T75",
    "T80",
    "T90",
}

VALID_TRANSITIONS = {
    "T00": ALL_TARGETS - {"T00"},
    "T10": {"T15"},
    "T15": {"T20"},
    "T20": {"T30", "T15"},
    "T30": {"T40", "T20", "T15"},
    "T40": {"T42", "T45"},
    "T42": {"T30", "T20", "T15"},
    "T45": {"T50", "T30"},
    "T50": {"T60", "T30"},
    "T60": {"T70", "T40", "T30"},
    "T70": {"T75", "T30"},
    "T75": {"T80", "T30", "T40", "T70"},
    "T80": {"T90", "T30", "T40", "T60", "T70"},
    "T90": set(),
}

GATE_EVIDENCE_KEYS = {
    ("T20", "T30"): "humanPlanApproved",
    ("T42", "T30"): "humanBlockerTriageApproved",
    ("T45", "T50"): "humanReviewApproved",
    ("T50", "T60"): "humanCommitApproved",
    ("T60", "T70"): "humanPushApproved",
    ("T75", "T80"): "humanLandingApproved",
}

REQUIRED_STATE_CARDS = {
    ("T10", "T15"): "10_ticket.md",
    ("T15", "T20"): "15_archaeology_receipt.md",
    ("T20", "T30"): "20_plan_approved.md",
    ("T30", "T40"): "20_plan_approved.md",
    ("T40", "T45"): "40_critic_review.md",
    ("T40", "T42"): "40_critic_review.md",
    ("T45", "T50"): "45_review_approved.md",
    ("T70", "T75"): "70_bot_triage.md",
}


def _evaluate_git_tokens_safety(tokens, is_t90_commit=False):
  """Returns (safe: bool, reason: str) for MCP workflow_safety_check."""
  exe, rem = _unwrap_command_tokens(tokens)
  if not exe or exe == "SHELL_C":
    return True, "OK"

  if exe == "gh":
    _, _, _, gh_pos, _ = _split_git_args(
        rem,
        short_value_chars="R",
        long_value_flags=("--repo", "--hostname"),
    )
    if len(gh_pos) >= 2 and gh_pos[0] == "pr" and gh_pos[1] == "merge":
      return True, "Requires human operator approval at Target 75 Landing Gate"
    return True, "OK"

  if exe != "git":
    return True, "OK"

  subcmd, sub_args, git_configs = _parse_git_subcommand(rem)
  if not subcmd:
    return True, "OK"

  if subcmd == "reset":
    long_opts, _, _, _, _ = _split_git_args(sub_args)
    for bad_flag in ("--hard", "--merge", "--keep"):
      if bad_flag in long_opts:
        return False, f"Destructive Git command blocked: git reset {bad_flag}"
    return True, "OK"

  if subcmd == "clean":
    _, _, ordered_opts, _, _ = _split_git_args(
        sub_args,
        short_value_chars="e",
        long_value_flags=("--exclude",),
    )
    cfg_force = _has_clean_force_config(git_configs)
    dry_run = False
    force = cfg_force
    interactive = False
    for opt in ordered_opts:
      if opt in {"-n", "--dry-run"}:
        dry_run = True
      elif opt == "--no-dry-run":
        dry_run = False
      elif opt in {"-f", "--force"}:
        force = True
      elif opt == "--no-force":
        force = cfg_force
      elif opt in {"-i", "--interactive"}:
        interactive = True
      elif opt == "--no-interactive":
        interactive = False
    if (force or interactive) and not dry_run:
      return False, "Destructive Git command blocked: git clean"
    return True, "OK"

  if subcmd == "checkout":
    long_opts, short_opts, _, pos, has_dd = _split_git_args(
        sub_args,
        short_value_chars="bBU",
        long_value_flags=(
            "--orphan",
            "--conflict",
            "--inter-hunk-context",
            "--pathspec-from-file",
        ),
    )
    if (
        any(ch in short_opts for ch in ("f", "p", "2", "3"))
        or any(
            opt
            in {
                "--force",
                "--discard-changes",
                "--patch",
                "--ours",
                "--theirs",
                "--pathspec-from-file",
            }
            or opt.startswith("--pathspec-from-file=")
            for opt in long_opts
        )
    ):
      return False, "Destructive Git command blocked: git checkout --force"
    if has_dd:
      return False, "Destructive Git command blocked: git checkout -- <path>"
    if any(
        p in {".", ".."}
        or p.startswith(("./", "../"))
        or any(ch in p for ch in "*?[")
        for p in pos
    ):
      return False, "Destructive Git command blocked: path-based git checkout"
    creating_branch = (
        "b" in short_opts or "B" in short_opts or "--orphan" in long_opts
    )
    if not creating_branch and len(pos) >= 2:
      return False, "Destructive Git command blocked: git checkout <tree> <path>"
    return True, "OK"

  if subcmd == "switch":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args,
        short_value_chars="cC",
        long_value_flags=("--orphan", "--conflict"),
    )
    if (
        "f" in short_opts
        or "--force" in long_opts
        or "--discard-changes" in long_opts
    ):
      return False, "Destructive Git command blocked: git switch --discard-changes"
    return True, "OK"

  if subcmd == "restore":
    _, _, ordered_opts, _, _ = _split_git_args(
        sub_args,
        short_value_chars="s",
        long_value_flags=("--source", "--conflict", "--pathspec-from-file"),
    )
    staged = False
    worktree = False
    for opt in ordered_opts:
      if opt in {"-S", "--staged"}:
        staged = True
      elif opt == "--no-staged":
        staged = False
      elif opt in {"-W", "--worktree"}:
        worktree = True
      elif opt == "--no-worktree":
        worktree = False
    if not staged or worktree:
      return False, "Destructive Git command blocked: working-tree git restore"
    return True, "OK"

  if subcmd == "stash":
    _, _, _, pos, _ = _split_git_args(
        sub_args,
        short_value_chars="m",
        long_value_flags=("--message", "--pathspec-from-file"),
    )
    if pos and pos[0] in {"drop", "clear"}:
      return False, f"Destructive Git command blocked: git stash {pos[0]}"
    return True, "OK"

  if subcmd in {"filter-branch", "filter-repo"}:
    return False, f"Destructive Git command blocked: git {subcmd}"

  if subcmd == "reflog":
    _, _, _, pos, _ = _split_git_args(sub_args)
    if pos and pos[0] in {"delete", "expire"}:
      return False, f"Destructive Git command blocked: git reflog {pos[0]}"
    return True, "OK"

  if subcmd == "update-ref":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args, short_value_chars="m"
    )
    if "d" in short_opts or "--stdin" in long_opts:
      return False, "Destructive Git command blocked: git update-ref"
    return True, "OK"

  if subcmd == "push":
    long_opts, short_opts, _, pos, _ = _split_git_args(
        sub_args,
        short_value_chars="o",
        long_value_flags=("--repo", "--receive-pack", "--exec", "--push-option"),
    )
    has_force_flag = (
        "f" in short_opts
        or any(
            opt
            in {"--force", "--force-with-lease", "--force-if-includes", "--mirror"}
            or opt.startswith("--force-with-lease=")
            for opt in long_opts
        )
    )
    has_plus_refspec = (
        any(p.startswith("+") and len(p) > 1 for p in pos)
        or _has_push_force_config(git_configs)
    )
    if has_force_flag or has_plus_refspec:
      return False, "Destructive Git command blocked: force-push is forbidden"
    return True, "Requires human operator approval at Target 60 Push Gate"

  if subcmd == "rebase":
    return True, "Requires human operator approval before running git rebase"

  if subcmd == "branch":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args,
        short_value_chars="u",
        long_value_flags=("--set-upstream-to",),
    )
    has_force_short = any(ch in short_opts for ch in ("D", "M", "C", "f"))
    if has_force_short or "--force" in long_opts:
      return (
          True,
          "Requires human operator approval before running git branch force flags",
      )
    return True, "OK"

  if subcmd == "commit":
    if is_t90_commit:
      return (
          True,
          "Requires human operator approval at Target 90 History Ledger Gate",
      )
    return True, "Requires human operator approval at Target 50 Commit Gate"

  return True, "OK"


def evaluate_git_safety(cmd, state_dir=None):
  """Checks all shell subcommands for destructive Git operations."""
  is_t90_state = is_target_90_state_dir(state_dir)
  last_reason = "OK"
  for tokens, _, is_subcmd_t90 in iter_subcommand_tokens_with_target90(
      cmd, is_t90_state=is_t90_state
  ):
    is_safe, reason = _evaluate_git_tokens_safety(
        tokens, is_t90_commit=is_subcmd_t90
    )
    if not is_safe:
      return False, reason
    if reason != "OK" and (
        last_reason == "OK"
        or (
            "Target 90 History Ledger Gate" in last_reason
            and "Target 50 Commit Gate" in reason
        )
    ):
      last_reason = reason
  return True, last_reason


def detect_hosting_mode(repo_dir="."):
  """Detects if repo_dir uses GitHub hosting with working gh CLI or pure Git.

  Returns:
      "github" if origin remote points to GitHub, gh CLI is available,
      gh auth status succeeds, and gh repo view succeeds.
      "pure_git" otherwise.
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
    is_github = "github.com" in remote_url.lower()
    if not is_github:
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
    pr_number,
    ws,
    quiescence_sec=None,
    poll_interval_sec=None,
    max_wait_sec=600,
    hosting_mode=None,
):
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
  ci_completed_at = None

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
    failed_checks = []
    max_check_completed_at = None

    for c in checks:
      st = c.get("status", "").upper()
      con = (c.get("conclusion") or "").upper()
      name = c.get("name") or c.get("context") or "check"
      if st != "COMPLETED":
        all_completed = False
      elif con in (
          "FAILURE",
          "CANCELLED",
          "TIMED_OUT",
          "STARTUP_FAILURE",
          "ACTION_REQUIRED",
      ):
        has_failure = True
        failed_checks.append(name)

      completed_str = (
          c.get("completedAt") or c.get("updatedAt") or c.get("createdAt")
      )
      if completed_str:
        try:
          dt = datetime.datetime.fromisoformat(
              completed_str.replace("Z", "+00:00")
          )
          ts = dt.timestamp()
          if max_check_completed_at is None or ts > max_check_completed_at:
            max_check_completed_at = ts
        except (ValueError, TypeError, OSError):
          pass

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
    bot_comments = []

    for cm in raw_comments:
      author = (cm.get("author") or {}).get("login", "")
      if "[bot]" in author.lower() or author.lower() in (
          "github-actions",
          "coderabbitai",
          "codecov",
          "sonarcloud",
          "gemini-code-assist",
          "copilot",
      ):
        bot_comments.append({
            "type": "comment",
            "author": author,
            "body": cm.get("body", ""),
            "createdAt": cm.get("createdAt", ""),
        })

    for rv in raw_reviews:
      author = (rv.get("author") or {}).get("login", "")
      if "[bot]" in author.lower() or author.lower() in (
          "github-actions",
          "coderabbitai",
          "codecov",
          "sonarcloud",
          "gemini-code-assist",
          "copilot",
      ):
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
        if ts_str:
          try:
            dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            ts = dt.timestamp()
            if ts > latest_activity:
              latest_activity = min(now, ts)
          except (ValueError, TypeError, OSError):
            pass

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


def get_server_info():
  """Return dynamic MCP serverInfo metadata backed by plugin.json."""
  return {
      "name": "makeflow-guard",
      "version": get_plugin_version(),
  }


SERVER_INFO = get_server_info()

SUPPORTED_PROTOCOL_VERSIONS = (
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)

DEFAULT_INITIALIZE_PROTOCOL_VERSION = "2025-11-25"
UNSUPPORTED_PROTOCOL_VERSION_CODE = -32022
MCP_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
MCP_SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"


def handle_server_discover(request_id):
  """Returns stateless MCP 2026-07-28 discovery metadata.

  Args:
    request_id: Valid JSON-RPC 2.0 string or integer request identifier.

  Returns:
    JSON-RPC 2.0 response dictionary containing supportedVersions,
    capabilities, and serverInfo metadata.
  """
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
          "capabilities": {"tools": {}},
          "_meta": {
              MCP_SERVER_INFO_META_KEY: get_server_info(),
          },
      },
  }


def handle_initialize(request_id, params=None):
  """Negotiates a legacy stateful MCP protocol version with the client.

  Args:
    request_id: Valid JSON-RPC 2.0 string or integer request identifier.
    params: Optional initialize request parameters dictionary.

  Returns:
    JSON-RPC 2.0 response dictionary echoing a supported protocolVersion or
    falling back to DEFAULT_INITIALIZE_PROTOCOL_VERSION.
  """
  requested = (
      params.get("protocolVersion") if isinstance(params, dict) else None
  )
  negotiated = (
      requested
      if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
      else DEFAULT_INITIALIZE_PROTOCOL_VERSION
  )
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "protocolVersion": negotiated,
          "capabilities": {"tools": {}},
          "serverInfo": get_server_info(),
      },
  }


def handle_tools_list(request_id):
  tools = [
      {
          "name": "workflow_get_state",
          "description": (
              "Reads Memento state files in the workspace to find ticket ID,"
              " branch name, active target, and completed milestones."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "workspaceDir": {
                      "type": "string",
                      "description": "Absolute path to the project repository root",
                  },
                  "stateDir": {
                      "type": "string",
                      "description": (
                          "Path to the state directory (defaults to"
                          " .workflow/state inside workspaceDir)"
                      ),
                  },
              },
          },
      },
      {
          "name": "workflow_advance_target",
          "description": (
              "Checks whether prerequisites pass to move from currentTarget to"
              " nextTarget in the Makefile DAG."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "currentTarget": {
                      "type": "string",
                      "description": (
                          "Current target ID, for example T20, T30, T40, T50, T75"
                      ),
                  },
                  "nextTarget": {
                      "type": "string",
                      "description": (
                          "Next target ID, for example T30, T45, T60, T80"
                      ),
                  },
                  "evidence": {
                      "type": "object",
                      "description": (
                          "Proof of prerequisite completion: boolean gate keys"
                          " humanPlanApproved (T20->T30),"
                          " humanBlockerTriageApproved (T42->T30),"
                          " humanReviewApproved (T45->T50),"
                          " humanCommitApproved (T50->T60),"
                          " humanPushApproved (T60->T70), and"
                          " humanLandingApproved (T75->T80), plus integer"
                          " blockersCount (T40->T42 and T40->T45) and optional"
                          " boolean allowMainConflict and skipMainFetch."
                      ),
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
                  "stateDir": {
                      "type": "string",
                      "description": (
                          "Path to the state directory (defaults to"
                          " .workflow/state inside workspaceDir)"
                      ),
                  },
              },
              "required": ["currentTarget", "nextTarget"],
          },
      },
      {
          "name": "workflow_verify_tree",
          "description": (
              "Computes the Git Merkle tree hash with 'git write-tree' in"
              " workspaceDir and compares it to expectedApprovedTree."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "expectedApprovedTree": {
                      "type": "string",
                      "description": "Expected 40-character Git tree hash",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Absolute path to the Git repository root",
                  },
              },
              "required": ["expectedApprovedTree"],
          },
      },
      {
          "name": "workflow_safety_check",
          "description": (
              "Checks a shell command or connection string to make sure that"
              " it does not connect to a production database or run a"
              " destructive Git command."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "commandLine": {
                      "type": "string",
                      "description": "Shell command line to inspect",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
              },
              "required": ["commandLine"],
          },
      },
      {
          "name": "workflow_get_config",
          "description": (
              "Reads active workflow configuration and model assignments from"
              " workspaceDir, user configuration, or environment variables."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  }
              },
          },
      },
      {
          "name": "workflow_poll_ci",
          "description": (
              "Polls GitHub pull request checks and review comments until CI"
              " completes and the review bot quiescence window passes."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "prNumber": {
                      "type": "integer",
                      "description": "Pull request number to monitor",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
                  "quiescenceSeconds": {
                      "type": "integer",
                      "description": (
                          "Seconds of silence with no new bot activity required"
                          " (0 disables quiescence wait; overrides config)"
                      ),
                  },
                  "pollIntervalSeconds": {
                      "type": "integer",
                      "description": (
                          "Seconds between status polls (>= 1; overrides config)"
                      ),
                  },
                  "maxWaitSeconds": {
                      "type": "integer",
                      "description": "Maximum seconds before polling timeout (default 600)",
                  },
              },
              "required": ["prNumber"],
          },
      },
      {
          "name": "workflow_query_scars",
          "description": (
              "Queries public synthetic scars database using full-text search"
              " (FTS5) or keywords with category, language, framework,"
              " diagnostic code, and workflow phase filters."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "query": {
                      "type": "string",
                      "description": "Keyword or scar ID search query (e.g. 'BaseScript', 'SCAR-ARCH-05')",
                  },
                  "category": {
                      "type": "string",
                      "description": "Optional category or slug filter (e.g. 'Testing', 'Architecture')",
                  },
                  "language": {
                      "type": "string",
                      "description": "Optional programming language filter (e.g. 'python', 'dart', 'go')",
                  },
                  "framework": {
                      "type": "string",
                      "description": "Optional framework or library filter (e.g. 'flutter', 'riverpod', 'pytest')",
                  },
                  "diagnosticCode": {
                      "type": "string",
                      "description": "Optional diagnostic, signal, or error code filter (e.g. 'StateError', 'SIGTERM')",
                  },
                  "workflowPhase": {
                      "type": "string",
                      "description": "Optional workflow phase filter (e.g. 'Target 30: tdd', 'T30')",
                  },
                  "mfTarget": {
                      "type": "integer",
                      "description": "Optional integer Makeflow workflow target filter (e.g. 15, 20, 30, 40)",
                  },
                  "limit": {
                      "type": "integer",
                      "description": "Maximum number of scars to return (default 3)",
                  },
                  "source": {
                      "type": "string",
                      "description": "Optional source filter (e.g. 'makeflow', 'upstream')",
                  },
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional project root directory",
                  },
                  "dbPath": {
                      "type": "string",
                      "description": "Optional explicit SQLite scars database path",
                  },
                  "refreshLoaded": {
                      "type": "boolean",
                      "description": "Set true to re-emit full scar text even if already loaded in this session",
                  },
              },
          },
      },
  ]
  return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}


def _error_result(request_id, message):
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "isError": True,
          "content": [{"type": "text", "text": message}],
      },
  }


def _normalize_object_arg(raw_value, field_name):
  """Normalizes an optional dictionary or JSON-object string payload.

  Args:
    raw_value: Raw argument value (None, dict, JSON string, or invalid type).
    field_name: Human-readable parameter name for error messages.

  Returns:
    Tuple of (normalized_dict, error_message). On error, normalized_dict is
    None and error_message is a non-empty string.
  """
  if raw_value is None:
    return {}, None
  if isinstance(raw_value, dict):
    return raw_value, None
  if isinstance(raw_value, str):
    stripped = raw_value.strip()
    if not stripped:
      return {}, None
    try:
      parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
      return (
          None,
          f"Invalid '{field_name}': expected JSON object, got invalid JSON string ({exc}).",
      )
    if not isinstance(parsed, dict):
      return (
          None,
          f"Invalid '{field_name}': expected JSON object, got {type(parsed).__name__}.",
      )
    return parsed, None
  return (
      None,
      f"Invalid '{field_name}': expected JSON object, got {type(raw_value).__name__}.",
  )


def handle_tools_call(request_id, tool_name, arguments):
  """Executes a named MCP tool call and returns a JSON-RPC 2.0 response.

  Args:
    request_id: Valid JSON-RPC 2.0 string or integer request identifier.
    tool_name: Registered MCP tool name.
    arguments: Optional dictionary or JSON-encoded object string of arguments.

  Returns:
    JSON-RPC 2.0 response dictionary correlated with request_id.
  """
  arguments, arg_err = _normalize_object_arg(arguments, "arguments")
  if arg_err is not None:
    return _error_result(request_id, arg_err)

  if tool_name == "workflow_get_state":
    ws = resolve_workspace_dir(arguments)
    if ws is None:
      return _error_result(
          request_id,
          "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
          "Pass 'workspaceDir' with the absolute path to your project workspace.",
      )
    raw_state_dir = arguments.get("stateDir", ".workflow/state")
    state_dir = (
        raw_state_dir
        if os.path.isabs(raw_state_dir)
        else os.path.join(ws, raw_state_dir)
    )
    if not os.path.isdir(state_dir):
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": (
                      f"State directory '{state_dir}' does not exist."
                      " Ready for Target 10 intake."
                  ),
              }]
          },
      }

    files = sorted(f for f in os.listdir(state_dir) if f.endswith(".md"))
    if not files:
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": (
                      f"State directory '{state_dir}' is clean (0 state cards)."
                      " Ready for Target 10 intake."
                  ),
              }]
          },
      }

    if not is_ticket_active(state_dir):
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": (
                      f"Previous ticket is complete in '{state_dir}'."
                      " Ready for Target 10 intake."
                  ),
              }]
          },
      }

    summary = f"Found {len(files)} state card(s) in {state_dir}:\n" + "\n".join(
        f"- {f}" for f in files
    )
    sync = inspect_main_sync(ws)
    sync_status = sync.get("status", "NO_REMOTE")
    upstream_ref = sync.get("upstream_ref") or "origin/main"
    behind_count = sync.get("behind_count", 0)
    if sync_status == "CONFLICT":
      files_str = ", ".join(sync.get("conflicting_files") or []) or "unknown"
      summary += (
          f"\nMAIN_SYNC: CONFLICT with {upstream_ref} "
          f"(behind={behind_count}, conflicting_files=[{files_str}])"
      )
    elif sync_status == "BEHIND_CLEAN":
      summary += (
          f"\nMAIN_SYNC: BEHIND_CLEAN with {upstream_ref} "
          f"(behind={behind_count}, conflicts=0)"
      )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": summary}]},
    }

  elif tool_name == "workflow_advance_target":
    current = str(arguments.get("currentTarget") or "").strip()
    next_t = str(arguments.get("nextTarget") or "").strip()
    evidence, evidence_err = _normalize_object_arg(
        arguments.get("evidence"), "evidence"
    )
    if evidence_err is not None:
      return _error_result(request_id, evidence_err)

    if current not in VALID_TRANSITIONS:
      return _error_result(
          request_id,
          f"BLOCKED: Unknown currentTarget '{current}'. Use T00 through T90.",
      )
    if next_t not in ALL_TARGETS:
      return _error_result(
          request_id,
          f"BLOCKED: Unknown nextTarget '{next_t}'. Use T00 through T90.",
      )
    if next_t not in VALID_TRANSITIONS[current]:
      allowed = sorted(VALID_TRANSITIONS[current])
      return _error_result(
          request_id,
          f"BLOCKED: Transition {current} -> {next_t} is not valid in the"
          f" Makefile DAG. Allowed next targets from {current}: {allowed}.",
      )

    gate_key = GATE_EVIDENCE_KEYS.get((current, next_t))
    if gate_key and not evidence.get(gate_key):
      return _error_result(
          request_id,
          f"BLOCKED: Cannot move {current} -> {next_t} without"
          f" {gate_key} = true.",
      )

    if current == "T40" and next_t == "T45":
      blockers = evidence.get("blockersCount", 1)
      if blockers > 0:
        return _error_result(
            request_id,
            f"BLOCKED: Critic reported {blockers} blocker(s). Route to T42"
            " (triaged-blockers) before T45.",
        )

    if current == "T40" and next_t == "T42":
      blockers = evidence.get("blockersCount", 1)
      if blockers == 0:
        return _error_result(
            request_id,
            "BLOCKED: Critic reported 0 blockers. Route to T45"
            " (approved-review) instead of T42.",
        )

    state_dir = ""
    main_sync_notice = ""
    transcript_path = (
        arguments.get("transcriptPath")
        or evidence.get("transcriptPath")
        or ""
    ).strip()
    if arguments.get("workspaceDir") or arguments.get("stateDir"):
      ws = resolve_workspace_dir(arguments)
      if ws is None:
        return _error_result(
            request_id,
            "BLOCKED: Missing workspaceDir when running in plugin directory.",
        )
      raw_state_dir = arguments.get("stateDir", ".workflow/state")
      state_dir = (
          raw_state_dir
          if os.path.isabs(raw_state_dir)
          else os.path.join(ws, raw_state_dir)
      )
      if os.path.isdir(state_dir):
        req_card = REQUIRED_STATE_CARDS.get((current, next_t))
        alt_cards = {
            ("T15", "T20"): ("20_plan.md", "20_plan_approved.md"),
            ("T40", "T45"): ("45_review_approved.md",),
        }.get((current, next_t), ())
        if (
            req_card
            and not os.path.isfile(os.path.join(state_dir, req_card))
            and not any(
                os.path.isfile(os.path.join(state_dir, ac)) for ac in alt_cards
            )
        ):
          return _error_result(
              request_id,
              f"BLOCKED: Required state card '{req_card}' does not exist in"
              f" '{state_dir}' for transition {current} -> {next_t}.",
          )

      sync = (
          inspect_main_sync(ws)
          if evidence.get("skipMainFetch")
          else fetch_and_inspect_main_sync(ws)
      )
      sync_status = sync.get("status", "NO_REMOTE")
      upstream_ref = sync.get("upstream_ref") or "origin/main"
      behind_count = sync.get("behind_count", 0)
      conflicting_files = sync.get("conflicting_files") or []
      if sync_status == "CONFLICT" and not evidence.get("allowMainConflict"):
        files_str = ", ".join(conflicting_files) or "unknown"
        if current in ("T70", "T75", "T80") or next_t in ("T70", "T75", "T80"):
          guidance = (
              "Stop tool calls without altering Git state (SCAR-GIT-25), report"
              f" conflicting files ({files_str}) to the operator, and request"
              f" approval to merge {upstream_ref} (git fetch origin main &&"
              " git merge origin/main) and return to T30/T40 for re-verification."
          )
        else:
          guidance = (
              "Stop tool calls without altering Git state (SCAR-GIT-25), report"
              f" conflicting files ({files_str}) to the operator, and request"
              f" approval to run git rebase {upstream_ref} (and re-lock"
              " approved_tree.txt + re-run T40 critic review if already past T40)."
          )
        return _error_result(
            request_id,
            f"BLOCKED: [SCAR-GIT-20] UPSTREAM_MAIN_CONFLICT on transition"
            f" {current} -> {next_t}: branch is behind {upstream_ref} by"
            f" {behind_count} commit(s) with merge conflicts in"
            f" [{files_str}]. {guidance}",
        )
      elif sync_status == "BEHIND_CLEAN":
        if next_t in ("T15", "T20", "T30", "T40"):
          main_sync_notice = (
              f" MAIN_SYNC_NOTICE: {upstream_ref} advanced by {behind_count}"
              " commit(s) with 0 conflicts (BEHIND_CLEAN). You may offer an"
              f" operator-approved 'git rebase {upstream_ref}' (SCAR-GIT-25)"
              f" now while in pre-review targets ({current} -> {next_t}) before"
              " locking approved_tree.txt at T40."
          )
        elif next_t in ("T42", "T45", "T50"):
          main_sync_notice = (
              f" MAIN_SYNC_NOTICE: {upstream_ref} advanced by {behind_count}"
              " commit(s) with 0 conflicts (BEHIND_CLEAN). Preserve"
              " .workflow/state/approved_tree.txt (SCAR-GIT-23); do not rebase"
              " during locked-tree gates unless returning to T40 for critic"
              " re-audit."
          )
        elif next_t == "T60":
          main_sync_notice = (
              f" MAIN_SYNC_NOTICE: {upstream_ref} advanced by {behind_count}"
              " commit(s) with 0 conflicts (BEHIND_CLEAN). Branch merges"
              " cleanly; rebasing now would invalidate approved_tree.txt and"
              " require T60 -> T40 critic re-audit."
          )
        else:
          main_sync_notice = (
              f" MAIN_SYNC_NOTICE: {upstream_ref} advanced by {behind_count}"
              " commit(s) with 0 conflicts (BEHIND_CLEAN). Per SCAR-GIT-18 and"
              " SCAR-GIT-25, do not rebase or force-push an open pull request"
              " when mergeable is CLEAN."
          )

    if gate_key:
      ok, reason = verify_mcp_gate_exit(
          state_dir, current, next_t, transcript_path=transcript_path
      )
      if not ok:
        return _error_result(request_id, reason)

    if state_dir and os.path.isdir(state_dir):
      if next_t in PAUSE_GATE_TARGETS:
        record_gate_entry(
            state_dir, current, next_t, transcript_path=transcript_path
        )
      elif current in PAUSE_GATE_TARGETS or gate_key:
        clear_gate_entry(state_dir, current)

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": (
                    f"SUCCESS: Target transition {current} -> {next_t} passed."
                    f"{main_sync_notice}"
                ),
            }]
        },
    }

  elif tool_name == "workflow_verify_tree":
    expected = arguments.get("expectedApprovedTree", "").strip()
    ws = resolve_workspace_dir(arguments)
    if ws is None:
      return _error_result(
          request_id,
          "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
          "Pass 'workspaceDir' with the path to the Git repository.",
      )
    try:
      res = subprocess.run(
          ["git", "write-tree"],
          cwd=ws,
          capture_output=True,
          text=True,
          check=True,
      )
      actual = res.stdout.strip()
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": json.dumps({
                      "matched": actual == expected,
                      "actualTree": actual,
                      "expectedTree": expected,
                      "workspaceDir": ws,
                  }),
              }]
          },
      }
    except subprocess.CalledProcessError as exc:
      stderr = (exc.stderr or "").strip()
      return _error_result(
          request_id,
          f"git write-tree failed in '{ws}': {stderr}. Pass workspaceDir with"
          " the path to the Git repository.",
      )
    except Exception as exc:
      return _error_result(request_id, f"git write-tree error in '{ws}': {exc}")

  elif tool_name == "workflow_safety_check":
    cmd = arguments.get("commandLine", "")
    ws = resolve_workspace_dir(arguments)
    cfg = resolve_workflow_config(ws)
    is_safe, reason = evaluate_database_safety(cmd, cfg.get("database"))
    if is_safe:
      explicit_ws = arguments.get("workspaceDir")
      state_dir = (
          os.path.join(ws, ".workflow", "state")
          if ws and (explicit_ws or not is_plugin_dir(ws))
          else None
      )
      git_safe, git_reason = evaluate_git_safety(cmd, state_dir=state_dir)
      if not git_safe:
        is_safe, reason = False, git_reason
      elif reason == "OK" and git_reason != "OK":
        reason = git_reason
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"safe": is_safe, "reason": reason}),
                }
            ]
        },
    }

  elif tool_name == "workflow_get_config":
    ws = resolve_workspace_dir(arguments)
    cfg = resolve_workflow_config(ws)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps(cfg, indent=2),
            }]
        },
    }

  elif tool_name == "workflow_poll_ci":
    pr_num = arguments.get("prNumber", 0)
    ws = resolve_workspace_dir(arguments)
    if ws is None:
      return _error_result(
          request_id,
          "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
          "Pass 'workspaceDir' with the path to the Git repository.",
      )
    q_sec = arguments.get("quiescenceSeconds")
    p_sec = arguments.get("pollIntervalSeconds")
    for field_name, field_val, min_val, hint in (
        (
            "quiescenceSeconds",
            q_sec,
            0,
            "Provide an integer >= 0 (0 disables quiescence wait).",
        ),
        ("pollIntervalSeconds", p_sec, 1, "Provide an integer >= 1."),
    ):
      if field_val is not None and (
          isinstance(field_val, bool)
          or not isinstance(field_val, int)
          or field_val < min_val
      ):
        return _error_result(
            request_id,
            f"Invalid '{field_name}' argument '{field_val}'. {hint}",
        )
    max_wait = arguments.get("maxWaitSeconds", 600)
    res = poll_ci_status(
        pr_number=pr_num,
        ws=ws,
        quiescence_sec=q_sec,
        poll_interval_sec=p_sec,
        max_wait_sec=max_wait,
    )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps(res, indent=2),
            }]
        },
    }

  elif tool_name == "workflow_query_scars":
    query = (arguments.get("query") or "").strip()
    category = arguments.get("category")
    language = arguments.get("language")
    source = arguments.get("source")
    framework = arguments.get("framework")
    diagnostic_code = arguments.get("diagnosticCode") or arguments.get(
        "diagnostic_code"
    )
    workflow_phase = arguments.get("workflowPhase") or arguments.get(
        "workflow_phase"
    )
    mf_target_raw = arguments.get("mfTarget")
    if mf_target_raw is None:
      mf_target_raw = arguments.get("mf_target")
    mf_target = None
    if mf_target_raw is not None and mf_target_raw != "":
      if isinstance(mf_target_raw, bool):
        return _error_result(
            request_id,
            f"Invalid 'mfTarget' argument '{mf_target_raw}'. Provide an integer workflow target (e.g. 15, 20, 30).",
        )
      try:
        mf_target = int(mf_target_raw)
      except (ValueError, TypeError):
        return _error_result(
            request_id,
            f"Invalid 'mfTarget' argument '{mf_target_raw}'. Provide an integer workflow target (e.g. 15, 20, 30).",
        )
    if (
        not query
        and not any(
            val
            for val in (
                category,
                language,
                source,
                framework,
                diagnostic_code,
                workflow_phase,
            )
        )
        and mf_target is None
    ):
      return _error_result(
          request_id,
          "Missing required argument 'query' or filter. Provide a search query"
          " or at least one filter (category, language, source, framework,"
          " diagnosticCode, workflowPhase, mfTarget).",
      )

    ws = resolve_workspace_dir(arguments)
    try:
      limit = int(arguments.get("limit", 3))
    except (ValueError, TypeError):
      limit = 3

    if resolve_scars_db_path is None or query_scars is None:
      return _error_result(
          request_id,
          "Scars query engine is unavailable. Ensure lib/scars_db.py is present.",
      )

    explicit_db = arguments.get("dbPath") or arguments.get("db_path")
    if explicit_db:
      explicit_abs = os.path.abspath(os.path.expanduser(explicit_db))
      if not os.path.isfile(explicit_abs):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "UNINITIALIZED",
                        "message": (
                            f"Scars database not found at '{explicit_abs}'. "
                            "Bundled registry database is missing from skill package. "
                            "Reinstall makeflow plugin or run 'make sync-scars' (maintainer) to generate."
                        ),
                        "scars": [],
                    }, indent=2),
                }]
            },
        }
      db_paths = [explicit_abs]
    else:
      db_paths = (
          resolve_scars_db_paths(
              db_path=None,
              workspace_dir=ws,
              package_root=_REPO_ROOT,
          )
          if resolve_scars_db_paths is not None
          else []
      )

    if not db_paths:
      fallback_path = resolve_scars_db_path(
          db_path=explicit_db,
          workspace_dir=ws,
          package_root=_REPO_ROOT,
      )
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": json.dumps({
                      "status": "UNINITIALIZED",
                      "message": (
                          f"Scars database not found at '{fallback_path}'. "
                          "Bundled registry database is missing from skill package. "
                          "Reinstall makeflow plugin or run 'make sync-scars' (maintainer) to generate."
                      ),
                      "scars": [],
                  }, indent=2),
              }]
          },
      }

    has_explicit_ws = bool(
        arguments.get("workspaceDir") or arguments.get("workspace_dir")
    )
    if ws and not explicit_db and (has_explicit_ws or not is_plugin_dir(ws)):
      state_dir = os.path.join(ws, ".workflow", "state")
    else:
      state_dir = None
    session_key = (
        f"explicit:{os.path.abspath(explicit_db)}"
        if explicit_db
        else f"ws:{ws or ''}"
    )
    loaded_ids = (
        load_session_scars(state_dir=state_dir, session_key=session_key)
        if load_session_scars is not None
        else set()
    )
    refresh_loaded = bool(
        arguments.get("refreshLoaded") or arguments.get("refresh_loaded")
    )

    raw_results = query_scars(
        db_path=db_paths,
        query=query,
        category=category,
        language=language,
        limit=max(1, limit) + len(loaded_ids),
        source=source,
        framework=framework,
        diagnostic_code=diagnostic_code,
        workflow_phase=workflow_phase,
        mf_target=mf_target,
    )

    if not raw_results:
      all_empty = True
      for p_db in db_paths:
        conn = None
        try:
          conn = sqlite3.connect(p_db)
          cur = conn.cursor()
          cur.execute("SELECT COUNT(*) FROM scars")
          row = cur.fetchone()
          if row and row[0] > 0:
            all_empty = False
            break
        except sqlite3.Error:
          pass
        finally:
          if conn:
            conn.close()

      if all_empty:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "EMPTY",
                        "message": (
                            f"Scars database at '{db_paths[0]}' contains 0 scars. "
                            "Bundled registry database is unpopulated. "
                            "Run 'make sync-scars' (maintainer) to rebuild."
                        ),
                        "scars": [],
                    }, indent=2),
                }]
            },
        }

    if partition_session_scars is not None:
      scars_out, already_loaded_out, updated_loaded = partition_session_scars(
          raw_results,
          loaded_ids,
          limit=limit,
          refresh_loaded=refresh_loaded,
      )
      if save_session_scars is not None:
        save_session_scars(
            updated_loaded, state_dir=state_dir, session_key=session_key
        )
    else:
      scars_out = raw_results[: max(1, limit)]
      already_loaded_out = []

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "status": "OK",
                    "totalResults": len(scars_out),
                    "scars": scars_out,
                    "alreadyLoaded": already_loaded_out,
                    "searchedDatabases": db_paths,
                }, indent=2),
            }]
        },
    }

  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "error": {"code": -32601, "message": f"Method {tool_name} not found"},
  }


def _is_valid_request_id(request_id):
  """Checks whether request_id is a non-boolean integer or string."""
  if isinstance(request_id, bool):
    return False
  return isinstance(request_id, (int, str))


def _jsonrpc_error(request_id, code, message, data=None):
  """Builds a JSON-RPC 2.0 error response correlated with request_id."""
  err_obj = {"code": code, "message": message}
  if data is not None:
    err_obj["data"] = data
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "error": err_obj,
  }


def _extract_requested_protocol_version(req, params):
  """Extracts per-request MCP protocol version from params or top-level _meta."""
  params_meta = params.get("_meta") if isinstance(params, dict) else None
  if isinstance(params_meta, dict):
    version = params_meta.get(MCP_PROTOCOL_VERSION_META_KEY)
    if version is not None:
      return version
  req_meta = req.get("_meta") if isinstance(req, dict) else None
  if isinstance(req_meta, dict):
    return req_meta.get(MCP_PROTOCOL_VERSION_META_KEY)
  return None


def dispatch_jsonrpc_line(line, stderr=None):
  """Parses and dispatches a single JSON-RPC 2.0 line without null-id frames.

  Args:
    line: Raw single-line text read from standard input.
    stderr: Optional text stream for parse, framing, and internal diagnostics.

  Returns:
    JSON-RPC 2.0 response dictionary for valid requests, or None for blank
    lines, notifications, malformed payloads, and invalid request identifiers.
  """
  err_stream = stderr if stderr is not None else sys.stderr
  if not line or not line.strip():
    return None

  try:
    req = json.loads(line)
  except (json.JSONDecodeError, ValueError, TypeError) as exc:
    err_stream.write(f"[makeflow-mcp] Invalid JSON frame ignored: {exc}\n")
    err_stream.flush()
    return None

  if not isinstance(req, dict):
    err_stream.write(
        f"[makeflow-mcp] Non-object JSON-RPC frame ignored: {type(req).__name__}\n"
    )
    err_stream.flush()
    return None

  method = req.get("method")
  if isinstance(method, str) and method.startswith("notifications/"):
    return None

  if "id" not in req:
    return None

  req_id = req.get("id")
  if req_id is None:
    err_stream.write("[makeflow-mcp] Null request id ignored\n")
    err_stream.flush()
    return None

  if not _is_valid_request_id(req_id):
    err_stream.write(
        f"[makeflow-mcp] Invalid request id type ignored: {type(req_id).__name__}\n"
    )
    err_stream.flush()
    return None

  if not isinstance(method, str) or not method.strip():
    return _jsonrpc_error(
        req_id, -32600, "Invalid Request: method must be a non-empty string"
    )

  try:
    raw_params = req.get("params", {})
    if raw_params is None:
      params = {}
    elif not isinstance(raw_params, dict):
      return _jsonrpc_error(
          req_id, -32602, "Invalid params: expected JSON object"
      )
    else:
      params = raw_params

    if method == "server/discover":
      return handle_server_discover(req_id)

    if method == "initialize":
      return handle_initialize(req_id, params)

    requested_version = _extract_requested_protocol_version(req, params)
    if requested_version is not None and (
        not isinstance(requested_version, str)
        or requested_version not in SUPPORTED_PROTOCOL_VERSIONS
    ):
      return _jsonrpc_error(
          req_id,
          UNSUPPORTED_PROTOCOL_VERSION_CODE,
          "Unsupported protocol version",
          data={
              "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
              "requested": requested_version,
          },
      )

    if method == "ping":
      return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
      return handle_tools_list(req_id)

    if method == "resources/list":
      return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}

    if method == "resources/templates/list":
      return {
          "jsonrpc": "2.0",
          "id": req_id,
          "result": {"resourceTemplates": []},
      }

    if method == "prompts/list":
      return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}

    if method == "tools/call":
      name = params.get("name")
      if not isinstance(name, str) or not name.strip():
        return _jsonrpc_error(
            req_id,
            -32602,
            "Invalid params: tools/call requires a non-empty string 'name'",
        )
      args = params.get("arguments", {})
      return handle_tools_call(req_id, name, args)

    return _jsonrpc_error(req_id, -32601, f"Unknown method: {method}")
  except Exception as exc:
    err_stream.write(
        f"[makeflow-mcp] Internal error handling method '{method}' (id={req_id!r}): {exc}\n"
    )
    err_stream.flush()
    return _jsonrpc_error(req_id, -32603, f"Internal error: {exc}")


def main():
  """Reads JSON-RPC 2.0 requests from stdin and writes responses to stdout."""
  for line in sys.stdin:
    resp = dispatch_jsonrpc_line(line, stderr=sys.stderr)
    if resp is not None:
      sys.stdout.write(json.dumps(resp) + "\n")
      sys.stdout.flush()


if __name__ == "__main__":
  main()
