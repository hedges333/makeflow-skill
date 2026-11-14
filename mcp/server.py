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
  from lib.scars_db import query_scars, resolve_scars_db_path
except ImportError:
  query_scars = None
  resolve_scars_db_path = None

from lib.config import (  # noqa: E402
    _normalize_str_list,
    _parse_csv_env,
    deep_merge,
    is_plugin_dir,
    is_ticket_active,
    load_default_config,
    map_to_agy_tier,
    resolve_workflow_config,
    resolve_workspace_dir,
)
from lib.shell_safety import (  # noqa: E402
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
    "T20": {"T30"},
    "T30": {"T40", "T20"},
    "T40": {"T42", "T45"},
    "T42": {"T30"},
    "T45": {"T50"},
    "T50": {"T60"},
    "T60": {"T70", "T40"},
    "T70": {"T75", "T30"},
    "T75": {"T80"},
    "T80": {"T90"},
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


def _evaluate_git_tokens_safety(tokens):
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
    if any(
        k in " ".join(tokens)
        for k in (
            "docs(history):",
            ".workflow/history",
            "history.json",
            "history.md",
        )
    ):
      return (
          True,
          "Requires human operator approval at Target 90 History Ledger Gate",
      )
    return True, "Requires human operator approval at Target 50 Commit Gate"

  return True, "OK"


def evaluate_git_safety(cmd):
  """Checks all shell subcommands for destructive Git operations."""
  last_reason = "OK"
  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      is_safe, reason = _evaluate_git_tokens_safety(tokens)
      if not is_safe:
        return False, reason
      if reason != "OK" and last_reason == "OK":
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
  cfg = resolve_workflow_config(ws)
  ci_cfg = cfg.get("ci", {})
  q_sec = (
      quiescence_sec
      if quiescence_sec is not None
      else ci_cfg.get("quiescenceSeconds", 180)
  )
  p_sec = (
      poll_interval_sec
      if poll_interval_sec is not None
      else ci_cfg.get("pollIntervalSeconds", 30)
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


def handle_initialize(request_id):
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "protocolVersion": "2024-11-05",
          "capabilities": {"tools": {}},
          "serverInfo": {
              "name": "makeflow-guard",
              "version": "1.4.1",
          },
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
                          " blockersCount (T40->T42 and T40->T45)."
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
                      "description": "Seconds of silence with no new bot activity required (overrides config)",
                  },
                  "pollIntervalSeconds": {
                      "type": "integer",
                      "description": "Seconds between status polls (overrides config)",
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
              " (FTS5) or keywords with category and language filters."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "query": {
                      "type": "string",
                      "description": "Keyword search query for scars (e.g. 'BaseScript', 'isolation')",
                  },
                  "category": {
                      "type": "string",
                      "description": "Optional category or slug filter (e.g. 'Testing', 'Architecture')",
                  },
                  "language": {
                      "type": "string",
                      "description": "Optional programming language filter (e.g. 'Python', 'Go')",
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
              },
              "required": ["query"],
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


def handle_tools_call(request_id, tool_name, arguments):
  arguments = arguments or {}

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
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": summary}]},
    }

  elif tool_name == "workflow_advance_target":
    current = arguments.get("currentTarget", "").strip()
    next_t = arguments.get("nextTarget", "").strip()
    evidence = arguments.get("evidence") or {}

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
        if req_card and not os.path.isfile(os.path.join(state_dir, req_card)):
          return _error_result(
              request_id,
              f"BLOCKED: Required state card '{req_card}' does not exist in"
              f" '{state_dir}' for transition {current} -> {next_t}.",
          )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": f"SUCCESS: Target transition {current} -> {next_t} passed.",
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
      git_safe, git_reason = evaluate_git_safety(cmd)
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
    if not query:
      return _error_result(
          request_id,
          "Missing required argument 'query'. Provide a keyword or search query.",
      )

    ws = resolve_workspace_dir(arguments)
    category = arguments.get("category")
    language = arguments.get("language")
    source = arguments.get("source")
    try:
      limit = int(arguments.get("limit", 3))
    except (ValueError, TypeError):
      limit = 3

    if resolve_scars_db_path is None or query_scars is None:
      return _error_result(
          request_id,
          "Scars query engine is unavailable. Ensure scripts/sync_synthetic_scars.py is present.",
      )

    explicit_db = arguments.get("dbPath") or arguments.get("db_path")
    db_path = resolve_scars_db_path(
        db_path=explicit_db,
        workspace_dir=ws,
        package_root=_REPO_ROOT,
    )

    if not os.path.isfile(db_path):
      return {
          "jsonrpc": "2.0",
          "id": request_id,
          "result": {
              "content": [{
                  "type": "text",
                  "text": json.dumps({
                      "status": "UNINITIALIZED",
                      "message": (
                          f"Scars database not found at '{db_path}'. "
                          "Bundled registry database is missing from skill package. "
                          "Reinstall makeflow plugin or run 'make sync-scars' (maintainer) to generate."
                      ),
                      "scars": [],
                  }, indent=2),
              }]
          },
      }

    results = query_scars(
        db_path=db_path,
        query=query,
        category=category,
        language=language,
        limit=limit,
        source=source,
    )

    if not results:
      is_empty = False
      conn = None
      try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scars")
        row = cur.fetchone()
        if row and row[0] == 0:
          is_empty = True
      except Exception:
        is_empty = True
      finally:
        if conn:
          conn.close()

      if is_empty:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "EMPTY",
                        "message": (
                            f"Scars database at '{db_path}' contains 0 scars. "
                            "Bundled registry database is unpopulated. "
                            "Run 'make sync-scars' (maintainer) to rebuild."
                        ),
                        "scars": [],
                    }, indent=2),
                }]
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "status": "OK",
                    "totalResults": len(results),
                    "scars": results,
                }, indent=2),
            }]
        },
    }

  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "error": {"code": -32601, "message": f"Method {tool_name} not found"},
  }


def main():
  for line in sys.stdin:
    if not line.strip():
      continue
    try:
      req = json.loads(line)
      req_id = req.get("id")
      method = req.get("method")

      if method == "initialize":
        resp = handle_initialize(req_id)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
      elif method == "notifications/initialized":
        pass
      elif method == "tools/list":
        resp = handle_tools_list(req_id)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
      elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        resp = handle_tools_call(req_id, name, args)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
      else:
        if req_id is not None:
          resp = {
              "jsonrpc": "2.0",
              "id": req_id,
              "error": {"code": -32601, "message": f"Unknown method: {method}"},
          }
          sys.stdout.write(json.dumps(resp) + "\n")
          sys.stdout.flush()
    except Exception as exc:
      err_resp = {
          "jsonrpc": "2.0",
          "id": None,
          "error": {"code": -32700, "message": f"Parse error: {exc}"},
      }
      sys.stdout.write(json.dumps(err_resp) + "\n")
      sys.stdout.flush()


if __name__ == "__main__":
  main()
