#!/usr/bin/env python3
"""Makefile Workflow MCP Server.

Implements Model Context Protocol (MCP) JSON-RPC 2.0 over standard input/output.
Provides state queries, DAG transition checks, Git tree checks, safety checks,
and user configuration lookup.
"""

import json
import os
import sqlite3
import subprocess
import sys

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
    deep_merge,
    is_plugin_dir,
    is_ticket_active,
    load_default_config,
    map_to_agy_tier,
    resolve_workflow_config,
    resolve_workspace_dir,
)
from lib.ci_polling import (
    detect_hosting_mode,
    poll_ci_status,
)
from lib.git_safety import (
    _evaluate_git_tokens_safety,
    evaluate_git_safety,
)
from lib.parking_state import (
    ParkingStateStore,
    format_parking_status_label,
)
from lib.ticket_state import TICKET_JSON_FILENAME
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
from lib.state_machine import (
    ALL_TARGETS,
    GATE_EVIDENCE_KEYS,
    REQUIRED_STATE_CARDS,
    VALID_TRANSITIONS,
    advance_workflow_target,
    run_makefile_advance,
)
from lib.repo_tree import (
    DEFAULT_REPO_TREE_THRESHOLD,
    RepoTreeCollector,
    RepoTreeError,
)
from lib.workflow_gates import (
    inspect_main_sync,
)

DEFAULT_PLUGIN_VERSION = "0.0.1"


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
                          " humanCommitApproved (T50->T60 or T50->T70 in Pure Git Mode),"
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
                  "expectedBranch": {
                      "type": "string",
                      "description": (
                          "Optional expected PR head branch name for"
                          " multi-contributor PR-branch firewalling"
                      ),
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
                      "description": "Optional keyword or scar ID search query when at least one filter is provided (e.g. 'BaseScript', 'SCAR-ARCH-05')",
                  },
                  "category": {
                      "type": "string",
                      "description": "Optional category or slug filter (e.g. 'Testing', 'Architecture')",
                  },
                  "excludeCategories": {
                      "type": "array",
                      "items": {"type": "string"},
                      "description": (
                          "Optional list of category codes, prose aliases, or"
                          " 'WORKFLOW' to exclude from results"
                      ),
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
      {
          "name": "workflow_repo_tree",
          "description": (
              "Lists the repository Git file tree index (tracked and"
              " non-ignored untracked files) with SCAR-TOOL-16 volume triage."
          ),
          "inputSchema": {
              "type": "object",
              "properties": {
                  "workspaceDir": {
                      "type": "string",
                      "description": "Optional absolute path to the Git repository root",
                  },
                  "pathPrefix": {
                      "type": "string",
                      "description": "Optional repository-relative path prefix to filter returned files",
                  },
                  "approvedLimit": {
                      "type": "integer",
                      "description": "Optional user-approved maximum number of file paths to return (>= 1)",
                  },
                  "countOnly": {
                      "type": "boolean",
                      "description": "Set true to return only file counts and top-level directory counts without paths",
                  },
                  "noLimit": {
                      "type": "boolean",
                      "description": "Set true when the user explicitly approved returning the full unbounded index",
                  },
                  "threshold": {
                      "type": "integer",
                      "description": (
                          "Optional volume triage threshold (>= 1, default"
                          f" {DEFAULT_REPO_TREE_THRESHOLD})"
                      ),
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


def _parse_optional_positive_int_arg(arguments, field_name, default=None):
  """Validates an optional non-boolean positive integer argument."""
  raw = arguments.get(field_name)
  if raw is None:
    return default, None
  if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
    return (
        None,
        f"Invalid '{field_name}' argument '{raw}'. Provide a positive integer >= 1.",
    )
  return raw, None


def _parse_optional_bool_arg(arguments, field_name):
  """Validates an optional boolean argument defaulting to False."""
  raw = arguments.get(field_name)
  if raw is None:
    return False, None
  if not isinstance(raw, bool):
    return None, f"Invalid '{field_name}' argument '{raw}'. Provide a boolean."
  return raw, None


def _handle_workflow_repo_tree(request_id, arguments):
  """Handles read-only workflow_repo_tree MCP calls with SCAR-TOOL-16 triage."""
  ws = resolve_workspace_dir(arguments)
  if ws is None:
    return _error_result(
        request_id,
        "BLOCKED: Running inside plugin installation directory without a workspaceDir. "
        "Pass 'workspaceDir' with the absolute path to the Git repository.",
    )

  raw_prefix = arguments.get("pathPrefix")
  if raw_prefix is None:
    path_prefix = ""
  elif isinstance(raw_prefix, str):
    path_prefix = raw_prefix
  else:
    return _error_result(
        request_id,
        f"Invalid 'pathPrefix' argument '{raw_prefix}'. Provide a string.",
    )

  approved_limit, err = _parse_optional_positive_int_arg(
      arguments, "approvedLimit", default=None
  )
  if err is not None:
    return _error_result(request_id, err)

  threshold, err = _parse_optional_positive_int_arg(
      arguments, "threshold", default=DEFAULT_REPO_TREE_THRESHOLD
  )
  if err is not None:
    return _error_result(request_id, err)

  count_only, err = _parse_optional_bool_arg(arguments, "countOnly")
  if err is not None:
    return _error_result(request_id, err)

  no_limit, err = _parse_optional_bool_arg(arguments, "noLimit")
  if err is not None:
    return _error_result(request_id, err)

  if approved_limit is not None and no_limit:
    return _error_result(
        request_id,
        "Invalid arguments: 'approvedLimit' and 'noLimit' cannot both be set.",
    )
  if count_only and (approved_limit is not None or no_limit):
    return _error_result(
        request_id,
        "Invalid arguments: 'countOnly' cannot be combined with 'approvedLimit' or 'noLimit'.",
    )

  try:
    index = RepoTreeCollector(ws).collect(prefix=path_prefix)
  except RepoTreeError as exc:
    return _error_result(request_id, str(exc))

  payload = index.to_dict(
      approved_limit=approved_limit,
      threshold=threshold,
      count_only=count_only,
      no_limit=no_limit,
  )
  return {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
          "content": [{
              "type": "text",
              "text": json.dumps(payload, indent=2),
          }]
      },
  }


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

    files = sorted(
        f
        for f in os.listdir(state_dir)
        if f.endswith(".md") or f == TICKET_JSON_FILENAME
    )
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
    parking = ParkingStateStore(state_dir).load()
    if parking is not None and parking.parked:
      summary += (
          f"\nSESSION_STATUS: {format_parking_status_label(parking)}"
          f" (PR #{parking.pr_number},"
          f" branch={parking.head_branch or 'unknown'},"
          f" approvals={parking.approvals_count}/{parking.required_approvals})"
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

    ev = dict(evidence)
    ev.pop("allowInit", None)
    ev.pop("explicitFrom", None)
    transcript_path = (
        arguments.get("transcriptPath")
        or ev.get("transcriptPath")
        or ""
    ).strip()
    if transcript_path:
      ev["transcriptPath"] = transcript_path

    ws = ""
    state_dir = ""
    if arguments.get("workspaceDir") or arguments.get("stateDir"):
      resolved_ws = resolve_workspace_dir(arguments)
      if resolved_ws is None:
        return _error_result(
            request_id,
            "BLOCKED: Missing workspaceDir when running in plugin directory.",
        )
      ws = resolved_ws
      raw_state_dir = arguments.get("stateDir", ".workflow/state")
      state_dir = (
          raw_state_dir
          if os.path.isabs(raw_state_dir)
          else os.path.join(ws, raw_state_dir)
      )

    if state_dir and os.path.isfile(os.path.join(state_dir, "Makefile")):
      ok, msg = run_makefile_advance(
          current,
          next_t,
          evidence=ev,
          workspace_dir=ws,
          state_dir=state_dir,
      )
    else:
      ok, msg = advance_workflow_target(
          current,
          next_t,
          evidence=ev,
          workspace_dir=ws,
          state_dir=state_dir,
      )

    if not ok:
      return _error_result(request_id, msg)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": msg}]
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
    raw_expected_branch = arguments.get("expectedBranch")
    if raw_expected_branch is not None and not isinstance(
        raw_expected_branch, str
    ):
      return _error_result(
          request_id,
          f"Invalid 'expectedBranch' argument '{raw_expected_branch}'. Provide a string.",
      )
    res = poll_ci_status(
        pr_number=pr_num,
        ws=ws,
        quiescence_sec=q_sec,
        poll_interval_sec=p_sec,
        max_wait_sec=max_wait,
        expected_branch=raw_expected_branch,
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
    if "excludeCategories" in arguments:
      exclude_categories_raw = arguments.get("excludeCategories")
    elif "exclude_categories" in arguments:
      exclude_categories_raw = arguments.get("exclude_categories")
    else:
      exclude_categories_raw = None
    exclude_categories = None
    if exclude_categories_raw is not None:
      if not isinstance(exclude_categories_raw, list) or any(
          not isinstance(item, str) for item in exclude_categories_raw
      ):
        return _error_result(
            request_id,
            "Invalid 'excludeCategories' argument. Provide a list of category"
            " strings (for example ['WORKFLOW'] or ['PROC', 'GIT']).",
        )
      exclude_categories = [
          item.strip() for item in exclude_categories_raw if item.strip()
      ]
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
        and not exclude_categories
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
        exclude_categories=exclude_categories,
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

  elif tool_name == "workflow_repo_tree":
    return _handle_workflow_repo_tree(request_id, arguments)

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
