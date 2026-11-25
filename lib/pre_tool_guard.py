"""PreToolUse safety hook library for Antigravity (SCAR-ARCH-05).

Guards production database commands, destructive Git operations, Makeflow
governance files, subagent halt airlocks, and milestone pause gates.
"""

import json
import os
import re
from typing import List, Optional, Tuple

from lib.base_script import BaseScript, SilentArgumentParser
from lib.commit_msg import (
    detect_commit_context,
    extract_commit_invocation,
    read_head_commit_message,
    validate_commit_message,
)
from lib.config import (
    _normalize_str_list,
    _parse_csv_env,
    is_plugin_dir,
    is_ticket_active,
    load_database_config,
    resolve_state_dir,
)
from lib.git_safety import (
    classify_git_tokens,
)
from lib.harness_boundary import (
    KNOWN_PLUGIN_CACHE_PREFIXES as _KNOWN_PLUGIN_CACHE_PREFIXES,
    MAKEFLOW_REPO_MARKERS as _MAKEFLOW_REPO_MARKERS,
    MAKEFLOW_REPO_ROOT_REAL as _MAKEFLOW_REPO_ROOT_REAL,
    PLUGIN_CACHE_RAW_PREFIXES as _PLUGIN_CACHE_RAW_PREFIXES,
    evaluate_harness_read_boundary,
    find_makeflow_plugin_root as _find_makeflow_plugin_root,
    is_plugin_cache_path as _is_plugin_cache_path,
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
    _evaluate_single_subcommand,
    _expand_shell_subcommands,
    _extract_redirection_targets,
    _extract_shell_substitutions,
    _has_clean_force_config,
    _has_prod_host_token,
    _has_push_force_config,
    _parse_git_subcommand,
    _preprocess_shell,
    _split_git_args,
    _split_shell_subcommands,
    _strip_identifier,
    _strip_line_continuations,
    _strip_shell_redirections,
    _tokenize_subcommand,
    _unwrap_command_tokens,
    evaluate_database_safety,
    is_database_command,
)
from lib.target90_guard import (
    is_target_90_commit_command,
    is_target_90_state_dir,
    iter_subcommand_tokens_with_target90,
)
from lib.turn_boundary import (
    PAUSE_GATE_EXITS,
    PAUSE_GATE_TARGETS,
    VALID_TRANSITIONS,
    clear_gate_entry,
    detect_latest_target,
    evaluate_advance_target_turn_boundary,
    evaluate_ask_question_turn_boundary,
    evaluate_command_turn_boundary,
    evaluate_subagent_airlock,
    extract_advance_target_calls,
    load_transcript_steps,
    mark_gate_human_verified,
    read_gate_entry,
    record_gate_entry,
)


def _resolve_effective_state_dir(state_dir=None):
  """Resolves workflow state directory while preserving plugin-dir isolation."""
  if state_dir is not None:
    return state_dir
  if not is_plugin_dir(os.getcwd()):
    cwd_state = os.path.join(os.getcwd(), ".workflow", "state")
    if os.path.isdir(cwd_state):
      return cwd_state
  return None


def _detect_target_90(cmd, state_dir=None):
  """Detects whether command is a Target 90 ledger, scar, changelog, or release operation."""
  effective_state_dir = _resolve_effective_state_dir(state_dir)
  is_t90_state = is_target_90_state_dir(effective_state_dir)
  is_history_msg = is_target_90_commit_command(cmd, is_t90_state=is_t90_state)
  return is_history_msg, is_t90_state


_HOOK_DENY_REASONS = {
    "git_clean": (
        "Blocked destructive Git command 'git clean'. "
        "Do not delete untracked working-tree files automatically."
    ),
    "git_checkout_force": (
        "Blocked 'git checkout' with worktree-overwriting flags. "
        "Do not overwrite uncommitted working-tree changes."
    ),
    "git_checkout_double_dash": (
        "Blocked 'git checkout -- <path>'. "
        "Do not overwrite working-tree files with git checkout."
    ),
    "git_checkout_path": (
        "Blocked path-based 'git checkout'. "
        "Do not overwrite working-tree files or directories."
    ),
    "git_checkout_tree_path": (
        "Blocked 'git checkout <tree> <path>'. "
        "Do not overwrite working-tree files with git checkout."
    ),
    "git_switch_force": (
        "Blocked 'git switch' with force or discard-changes. "
        "Do not discard uncommitted working-tree edits."
    ),
    "git_restore_worktree": (
        "Blocked working-tree 'git restore'. "
        "Only 'git restore --staged' is allowed to unstage index entries."
    ),
    "git_update_ref": (
        "Blocked 'git update-ref'. "
        "Do not delete or batch-rewrite Git references."
    ),
    "git_push_force": (
        "Blocked force-push command. "
        "Force-pushing is forbidden across all branches in the agent workflow."
    ),
}


_TARGET_80_NEXT_ACTION_RE = re.compile(
    r"(?im)^-\s*Active Target:\s*(?:Target\s*80|T80)\b"
)


def _derive_workspace_root(state_dir: str) -> str:
  """Derives the workspace root directory from a ``.workflow/state`` path.

  Args:
    state_dir: Workflow state directory path.

  Returns:
    Absolute workspace root directory path.
  """
  abs_state = os.path.abspath(state_dir)
  parent = os.path.dirname(abs_state)
  if os.path.basename(abs_state) == "state" and os.path.basename(parent) == ".workflow":
    return os.path.dirname(parent)
  return parent


def _is_post_landing_target(state_dir: str, transcript_path: str = "") -> bool:
  """Returns True when the active workflow target is T80 (land) or T90 (finish).

  Args:
    state_dir: Active ``.workflow/state`` directory path.
    transcript_path: Optional path to the session transcript JSONL.

  Returns:
    True when Target 80 or Target 90 is active so landing/history commits are
    not validated against Target 50 feature commit rules.
  """
  active_path = os.path.join(state_dir, "active_target.json")
  if os.path.isfile(active_path):
    try:
      with open(active_path, "r", encoding="utf-8") as f:
        data = json.load(f)
      if isinstance(data, dict):
        raw_target = data.get("target")
        if isinstance(raw_target, str) and raw_target.strip():
          return raw_target.strip() in ("T80", "T90")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
      pass

  next_action = os.path.join(state_dir, "99_next_action.md")
  if os.path.isfile(next_action):
    try:
      with open(next_action, "r", encoding="utf-8") as f:
        if _TARGET_80_NEXT_ACTION_RE.search(f.read()):
          return True
    except (OSError, UnicodeDecodeError):
      pass
    return False

  if transcript_path:
    steps = load_transcript_steps(transcript_path)
    if detect_latest_target(steps, state_dir=state_dir) in ("T80", "T90"):
      return True

  return False


def _should_validate_commit_message(
    effective_state_dir: Optional[str],
    is_history_msg: bool,
    is_t90_state: bool,
    transcript_path: str = "",
) -> bool:
  """Returns True when PreToolUse should validate commit message syntax.

  Args:
    effective_state_dir: Resolved workflow state directory, if any.
    is_history_msg: True when command is a Target 90 history/release commit.
    is_t90_state: True when workspace is in Target 90 / COMPLETE state.
    transcript_path: Optional transcript path for T80/T90 fallback detection.

  Returns:
    True only for active non-T80/T90 ticket workspaces.
  """
  if is_history_msg or is_t90_state:
    return False
  if not effective_state_dir or not is_ticket_active(effective_state_dir):
    return False
  if _is_post_landing_target(effective_state_dir, transcript_path):
    return False
  return True


def _validate_pre_tool_commit_tokens(
    tokens: List[str], effective_state_dir: str
) -> Tuple[bool, str]:
  """Validates a single ``git commit`` command's message before execution.

  Args:
    tokens: Tokenized single shell subcommand.
    effective_state_dir: Active ``.workflow/state`` directory path.

  Returns:
    Tuple of ``(is_valid, deny_reason)``.
  """
  inv = extract_commit_invocation(tokens)
  if inv is None:
    return True, ""

  workspace_root = _derive_workspace_root(effective_state_dir)
  if inv.repo_dir:
    commit_repo_dir = (
        inv.repo_dir
        if os.path.isabs(inv.repo_dir)
        else os.path.abspath(os.path.join(workspace_root, inv.repo_dir))
    )
  else:
    commit_repo_dir = workspace_root

  is_local, ticket_id = detect_commit_context(
      commit_repo_dir, state_dir=effective_state_dir
  )

  msg_text = ""
  if inv.alternate_message_flags:
    flags_str = ", ".join(inv.alternate_message_flags)
    return (
        False,
        "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Alternate"
        f" commit message flag(s) ({flags_str}) cannot be validated in"
        " PreToolUse. Write .workflow/state/50_commit_msg.txt and run 'git"
        " commit -F .workflow/state/50_commit_msg.txt'.",
    )
  if inv.explicit_edit:
    return (
        False,
        "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Interactive"
        " editor flag ('--edit' / '-e') cannot be validated in PreToolUse."
        " Use 'git commit -F .workflow/state/50_commit_msg.txt' without"
        " '--edit' or '-e'.",
    )
  if inv.message_file is not None and inv.messages:
    return (
        False,
        "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Cannot"
        " combine '-m' / '--message' with '-F' / '--file' in the same 'git"
        " commit' invocation. Use '-F .workflow/state/50_commit_msg.txt'.",
    )
  if inv.message_file is not None:
    if inv.message_file == "-":
      return (
          False,
          "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Reading a"
          " commit message from stdin ('-F -' / '--file=-') cannot be validated"
          " in PreToolUse. Write the message to"
          " .workflow/state/50_commit_msg.txt and run 'git commit -F"
          " .workflow/state/50_commit_msg.txt'.",
      )
    msg_file_path = (
        inv.message_file
        if os.path.isabs(inv.message_file)
        else os.path.abspath(os.path.join(commit_repo_dir, inv.message_file))
    )
    if not os.path.isfile(msg_file_path):
      return (
          False,
          "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Commit"
          f" message file not found: '{inv.message_file}'. Write"
          " .workflow/state/50_commit_msg.txt before committing.",
      )
    try:
      with open(msg_file_path, "r", encoding="utf-8", errors="replace") as f:
        msg_text = f.read()
    except OSError as exc:
      return (
          False,
          "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Failed to"
          f" read commit message file '{inv.message_file}': {exc}",
      )
  elif inv.messages:
    msg_text = "\n\n".join(inv.messages)
  elif inv.is_amend and inv.no_edit:
    head_msg = read_head_commit_message(commit_repo_dir)
    if head_msg is None:
      return (
          False,
          "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Could not"
          " read HEAD commit message for 'git commit --amend --no-edit'"
          f" in '{commit_repo_dir}'.",
      )
    msg_text = head_msg
  else:
    return (
        False,
        "BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: Bare 'git"
        " commit' without '-F .workflow/state/50_commit_msg.txt' or a valid"
        " '-m' message cannot be validated. Use 'git commit -F"
        " .workflow/state/50_commit_msg.txt'.",
    )

  val_res = validate_commit_message(
      msg_text, is_local=is_local, ticket_id=ticket_id
  )
  if not val_res.valid:
    mode_name = "Local session" if is_local else "Remote issue"
    err_summary = " ".join(val_res.errors)
    return (
        False,
        f"BLOCKED: [SCAR-GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT: {mode_name}"
        f" commit message validation failed: {err_summary} Format"
        " .workflow/state/50_commit_msg.txt per 05_pre_commit_hygiene.md.",
    )
  return True, ""


def _evaluate_git_tokens(
    tokens,
    has_review,
    is_history_msg=False,
    is_t90_state=False,
    state_dir=None,
    transcript_path="",
):
  """Returns ('deny'|'force_ask'|'allow', reason) for a single command's tokens."""
  verdict = classify_git_tokens(tokens)
  if verdict.decision == "allow":
    return "allow", ""

  if verdict.decision == "deny":
    if verdict.rule == "git_reset":
      return (
          "deny",
          f"Blocked destructive Git command 'git reset {verdict.detail}'. "
          "Do not discard uncommitted working-tree changes.",
      )
    if verdict.rule == "git_stash":
      return (
          "deny",
          f"Blocked 'git stash {verdict.detail}'. Do not delete stashed changes.",
      )
    if verdict.rule == "git_filter":
      return (
          "deny",
          f"Blocked '{verdict.detail}'. History-rewriting repository filters are forbidden.",
      )
    if verdict.rule == "git_reflog":
      return (
          "deny",
          f"Blocked 'git reflog {verdict.detail}'. Do not erase Git recovery reflogs.",
      )
    return "deny", _HOOK_DENY_REASONS[verdict.rule]

  if verdict.rule == "gh_pr_merge":
    if transcript_path or state_dir:
      ok, reason = evaluate_command_turn_boundary(
          "T75", transcript_path, state_dir=state_dir
      )
      if not ok:
        return "deny", reason
    return (
        "force_ask",
        "Target 75 Landing Gate: gh pr merge requires operator approval. "
        "Make sure that CI is green and the 180-second quiet period passed.",
    )

  if verdict.rule == "git_push":
    pos = verdict.positionals
    if is_t90_state and (
        "main" in pos
        or "master" in pos
        or any(p.endswith("/main") or p.endswith(":main") for p in pos)
    ):
      return (
          "force_ask",
          "Target 90 History Push Gate: Approve to push history ledger commit to origin/main.",
      )
    if (transcript_path or state_dir) and not is_t90_state:
      ok, reason = evaluate_command_turn_boundary(
          "T60", transcript_path, state_dir=state_dir
      )
      if not ok:
        return "deny", reason
    if has_review:
      reason = (
          "Target 60 Push Gate: 45_review_approved.md is present. "
          "Make sure that the branch merges cleanly before you approve."
      )
    else:
      reason = (
          "Target 60 Push Gate: 45_review_approved.md is missing in .workflow/state/. "
          "Make sure that review and commit gates passed before you approve."
      )
    return "force_ask", reason

  if verdict.rule == "git_rebase":
    return (
        "force_ask",
        "Git Rebase Gate: git rebase rewrites commit history. "
        "Make sure that upstream merge conflicts require a rebase before you approve.",
    )

  if verdict.rule == "git_branch_force":
    return (
        "force_ask",
        "Git Branch Force Gate: force-deleting, force-renaming, or force-moving "
        "a branch requires operator approval.",
    )

  if verdict.rule == "git_commit":
    if is_history_msg:
      return (
          "force_ask",
          "Target 90 History Ledger Gate: Approve to commit history ledger updates.",
      )
    if (transcript_path or state_dir) and not is_t90_state:
      ok, reason = evaluate_command_turn_boundary(
          "T50", transcript_path, state_dir=state_dir
      )
      if not ok:
        return "deny", reason
    effective_state_dir = _resolve_effective_state_dir(state_dir)
    if _should_validate_commit_message(
        effective_state_dir,
        is_history_msg=is_history_msg,
        is_t90_state=is_t90_state,
        transcript_path=transcript_path,
    ):
      msg_ok, msg_reason = _validate_pre_tool_commit_tokens(
          tokens, effective_state_dir
      )
      if not msg_ok:
        return "deny", msg_reason
    if has_review:
      reason = (
          "Target 50 Commit Gate: 45_review_approved.md is present. "
          "Approve to create the commit."
      )
    else:
      reason = (
          "Target 50 Commit Gate: 45_review_approved.md is missing in .workflow/state/. "
          "Make sure that Target 45 review passed before you approve."
      )
    return "force_ask", reason

  return "allow", ""


def evaluate_git_command(cmd, has_review, state_dir=None, transcript_path=""):
  """Evaluates all subcommands with deny > force_ask > allow precedence."""
  effective_state_dir = _resolve_effective_state_dir(state_dir)
  is_t90_state = is_target_90_state_dir(effective_state_dir)
  first_ask_reason = None
  for tokens, _, is_subcmd_t90 in iter_subcommand_tokens_with_target90(
      cmd, is_t90_state=is_t90_state
  ):
    decision, reason = _evaluate_git_tokens(
        tokens,
        has_review,
        is_history_msg=is_subcmd_t90,
        is_t90_state=is_t90_state,
        state_dir=state_dir,
        transcript_path=transcript_path,
    )
    if decision == "deny":
      return {"decision": "deny", "reason": reason}
    if decision == "force_ask" and (
        first_ask_reason is None
        or (
            "Target 90 History Ledger Gate" in first_ask_reason
            and "Target 50 Commit Gate" in reason
        )
    ):
      first_ask_reason = reason
  if first_ask_reason is not None:
    return {"decision": "force_ask", "reason": first_ask_reason}
  return {"decision": "allow"}


STATE_GOVERNANCE_RELPATHS = frozenset({
    ".workflow/state/Makefile",
    ".workflow/state/30_tdd_receipt.json",
    ".workflow/state/30_regression_receipt.json",
    ".workflow/state/gate_entry.json",
    ".workflow/state/active_target.json",
    ".workflow/state/active_subagent.json",
})

STATE_GOVERNANCE_DIR_RELPATHS = frozenset({
    ".workflow",
    ".workflow/state",
})

GOVERNANCE_FILE_RELPATHS = frozenset({
    "scripts/tdd_guard.py",
    "scripts/pre_tool_guard.py",
    "scripts/workflow_gates.py",
    "scripts/emit_reflex.py",
    "scripts/session_start.py",
    "lib/agent_paths.py",
    "lib/base_script.py",
    "lib/commit_msg.py",
    "lib/git_index.py",
    "lib/git_safety.py",
    "lib/pre_tool_guard.py",
    "lib/regression_gate.py",
    "lib/tdd_guard.py",
    "lib/workflow_gates.py",
    "lib/main_sync.py",
    "lib/state_makefile.py",
    "lib/plugin_gates.py",
    "lib/intake_gates.py",
    "lib/commit_gates.py",
    "lib/parking_gates.py",
    "lib/workflow_gates_cli.py",
    "lib/state_machine.py",
    "lib/reflex.py",
    "lib/config.py",
    "lib/shell_safety.py",
    "lib/target90_guard.py",
    "lib/turn_boundary.py",
    "lib/harness_boundary.py",
    "lib/session_start.py",
    "lib/ticket_state.py",
    "lib/workspace_mutex.py",
    "lib/repo_tree.py",
    "lib/safe_copy.py",
    "lib/signal_trap.py",
    "lib/parking_state.py",
    "hooks.json",
    *STATE_GOVERNANCE_RELPATHS,
})


def _normalize_gov_path(raw_token):
  """Normalizes a raw path token for governance path matching."""
  if not raw_token or not isinstance(raw_token, str):
    return ""
  token = raw_token.strip().strip("'\"").replace("\\", "/")
  while token.startswith("./"):
    token = token[2:]
  return token.rstrip("/")


def _extract_cd_roots(cmd, base_roots):
  """Extracts cumulative resolved directory targets from cd and pushd commands.

  Args:
    cmd: Shell command string to scan.
    base_roots: Optional workspace root paths for resolving relative targets.

  Returns:
    Deduplicated list of absolute directory paths referenced by cd or pushd.
  """
  active_roots = [
      os.path.abspath(os.path.expanduser(ws))
      for ws in (base_roots or [])
      if ws
  ]
  if not active_roots:
    active_roots = [os.getcwd()]
  seen_active = set(active_roots)
  extra = []
  seen_extra = set()
  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      exe, rem = _unwrap_command_tokens(tokens)
      if exe in ("cd", "pushd"):
        pos = [a for a in rem if not a.startswith("-")]
        if pos:
          resolved = _resolve_direct_target_paths(pos[0], active_roots)
        else:
          resolved = [os.path.abspath(os.path.expanduser("~"))]
        for path in resolved:
          if path not in seen_extra:
            seen_extra.add(path)
            extra.append(path)
          if path not in seen_active:
            seen_active.add(path)
            active_roots.append(path)
  return extra


def _is_protected_governance_file(raw_token, search_roots):
  """Checks whether raw_token targets a protected Makeflow governance file.

  Always protects .workflow/state runtime governance files in every repository
  and any file inside a Makeflow plugin cache directory. Protects plugin
  source governance files and Makeflow plugin.json only when they resolve
  inside a Makeflow plugin repository root.

  Args:
    raw_token: Raw path token from a shell redirection or command argument.
    search_roots: Optional workspace and cd root paths for resolving relative
      targets.

  Returns:
    True if mutating raw_token violates Makeflow governance protection. Returns
    False for external consumer repository files.
  """
  if _is_state_governance_target(raw_token):
    return True
  if not raw_token or not isinstance(raw_token, str):
    return False
  cleaned = raw_token.strip().strip("'\"")
  if not cleaned:
    return False
  abs_candidates = _resolve_direct_target_paths(cleaned, search_roots)
  for abs_target in abs_candidates:
    if _is_plugin_cache_path(abs_target):
      return True
    for cand in (abs_target, os.path.realpath(abs_target)):
      if _is_state_governance_target(cand):
        return True
      plugin_root = _find_makeflow_plugin_root(cand)
      if plugin_root is None:
        continue
      rel_to_plugin = os.path.relpath(cand, plugin_root).replace(os.sep, "/")
      if (
          rel_to_plugin in GOVERNANCE_FILE_RELPATHS
          or rel_to_plugin == "plugin.json"
      ):
        return True
  return False


def _is_protected_governance_dir(raw_token, search_roots):
  """Checks whether raw_token targets a protected governance directory.

  Always protects .workflow and .workflow/state directories in every repository
  and any path inside a Makeflow plugin cache directory. Protects scripts/ and
  lib/ directories only when they resolve inside a Makeflow plugin repository
  root.

  Args:
    raw_token: Raw path token from a destructive file command.
    search_roots: Optional workspace and cd root paths for resolving relative
      targets.

  Returns:
    True if raw_token resolves to .workflow, .workflow/state, a plugin cache
    directory, or scripts/ or lib/ inside a Makeflow plugin repository. Returns
    False otherwise.
  """
  if _is_state_governance_dir(raw_token):
    return True
  if not raw_token or not isinstance(raw_token, str):
    return False
  cleaned = raw_token.strip().strip("'\"")
  if not cleaned:
    return False
  for abs_target in _resolve_direct_target_paths(cleaned, search_roots):
    if _is_plugin_cache_path(abs_target):
      return True
    for cand in (abs_target, os.path.realpath(abs_target)):
      if _is_state_governance_dir(cand):
        return True
      plugin_root = _find_makeflow_plugin_root(cand)
      if plugin_root is None:
        continue
      rel_to_plugin = os.path.relpath(cand, plugin_root).replace(os.sep, "/")
      if rel_to_plugin in {"scripts", "lib"}:
        return True
  return False


def _collect_redirection_scan_commands(cmd):
  """Collects preprocessed command strings preserving output redirections.

  Includes the preprocessed top-level command, nested shell substitutions, and
  preprocessed SHELL_C payloads (such as bash -c, sh -c, and eval).

  Args:
    cmd: Shell command string to scan.

  Returns:
    Deduplicated list of command strings preserving output redirections.
  """
  preprocessed = _preprocess_shell(cmd)
  candidates = [preprocessed] + _extract_shell_substitutions(preprocessed)
  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      exe, rem = _unwrap_command_tokens(tokens)
      if exe == "SHELL_C" and rem and rem[0]:
        payload = _preprocess_shell(rem[0])
        candidates.append(payload)
        candidates.extend(_extract_shell_substitutions(payload))
  seen = set()
  unique = []
  for item in candidates:
    if item and item not in seen:
      seen.add(item)
      unique.append(item)
  return unique


def evaluate_self_modification_safety(cmd, workspace_paths=None):
  """Evaluates whether cmd attempts to truncate, overwrite, or delete makeflow governance files.

  Args:
    cmd: Raw shell command string.
    workspace_paths: Optional list of active workspace root paths.

  Returns:
    Tuple of ``(is_safe, deny_reason)``.
  """
  if not cmd or not isinstance(cmd, str):
    return True, ""

  cd_roots = _extract_cd_roots(cmd, workspace_paths)
  if cd_roots:
    base = list(workspace_paths) if workspace_paths else [os.getcwd()]
    search_roots = base + cd_roots
  else:
    search_roots = workspace_paths

  # 1. Check output redirections in preprocessed command, subshells, and bash -c/sh -c payloads
  for c in _collect_redirection_scan_commands(cmd):
    redir_targets = _extract_redirection_targets(c)
    for target in redir_targets:
      if _is_protected_governance_file(target, search_roots):
        return (
            False,
            f"Blocked self-modification attempt on makeflow governance file '{target}' (redirection).",
        )

  # 2. Check destructive CLI commands across expanded subcommands
  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      exe, rem = _unwrap_command_tokens(tokens)
      if not exe:
        continue
      base_exe = os.path.basename(exe).lower()

      if base_exe in {"rm", "unlink", "shred"}:
        for arg in rem:
          if arg.startswith("-"):
            continue
          if _is_protected_governance_file(
              arg, search_roots
          ) or _is_protected_governance_dir(arg, search_roots):
            return (
                False,
                f"Blocked self-modification attempt on makeflow governance file '{arg}' ({base_exe}).",
            )

      if base_exe == "truncate":
        skip_next = False
        for arg in rem:
          if skip_next:
            skip_next = False
            continue
          if arg in {"-s", "--size", "-o", "--reference"}:
            skip_next = True
            continue
          if arg.startswith("-"):
            continue
          if _is_protected_governance_file(arg, search_roots):
            return (
                False,
                f"Blocked self-modification attempt on makeflow governance file '{arg}' (truncate).",
            )

      if base_exe == "sed":
        has_inplace = any(
            arg in {"-i", "--in-place"}
            or (arg.startswith("-") and "i" in arg and not arg.startswith("--"))
            or arg.startswith("--in-place=")
            for arg in rem
        )
        if has_inplace:
          for arg in rem:
            if arg.startswith("-"):
              continue
            if _is_protected_governance_file(arg, search_roots):
              return (
                  False,
                  f"Blocked self-modification attempt on makeflow governance file '{arg}' (sed -i).",
              )

      if base_exe == "tee":
        for arg in rem:
          if arg.startswith("-"):
            continue
          if _is_protected_governance_file(arg, search_roots):
            return (
                False,
                f"Blocked self-modification attempt on makeflow governance file '{arg}' (tee).",
            )

      if base_exe in {"mv", "cp"}:
        pos_args = [a for a in rem if not a.startswith("-")]
        if len(pos_args) >= 2:
          dest = pos_args[-1]
          srcs = pos_args[:-1]
          norm_dest = _normalize_gov_path(dest)
          is_dir = (
              dest.endswith("/")
              or dest.endswith("\\")
              or norm_dest in {"scripts", "lib", ".workflow", ".workflow/state"}
              or norm_dest.endswith("/scripts")
              or norm_dest.endswith("/lib")
              or norm_dest.endswith("/.workflow")
              or norm_dest.endswith("/.workflow/state")
              or os.path.isdir(os.path.expanduser(dest))
              or (
                  search_roots
                  and any(
                      ws
                      and os.path.isdir(
                          os.path.join(os.path.expanduser(ws), norm_dest)
                      )
                      for ws in search_roots
                  )
              )
              or len(srcs) > 1
          )
          if is_dir:
            for src in srcs:
              src_base = os.path.basename(src.rstrip("/\\"))
              effective_dest = (
                  f"{norm_dest}/{src_base}" if norm_dest else src_base
              )
              if _is_protected_governance_file(effective_dest, search_roots):
                return (
                    False,
                    f"Blocked self-modification attempt on makeflow governance file '{effective_dest}' ({base_exe}).",
                )
          else:
            if _is_protected_governance_file(dest, search_roots):
              return (
                  False,
                  f"Blocked self-modification attempt on makeflow governance file '{dest}' ({base_exe}).",
              )

  return True, ""


def _is_state_governance_target(raw_token):
  """Returns True when raw_token targets a runtime .workflow/state governance file."""
  token = _normalize_gov_path(raw_token)
  if not token:
    return False
  if token in STATE_GOVERNANCE_RELPATHS:
    return True
  return any(token.endswith("/" + rel) for rel in STATE_GOVERNANCE_RELPATHS)


def _is_state_governance_dir(raw_token):
  """Checks whether raw_token targets the .workflow or .workflow/state directory.

  Args:
    raw_token: Raw or resolved path string to inspect.

  Returns:
    True if raw_token matches .workflow or .workflow/state. Returns False
    otherwise.
  """
  token = _normalize_gov_path(raw_token)
  if not token:
    return False
  if token in STATE_GOVERNANCE_DIR_RELPATHS:
    return True
  return any(token.endswith("/" + rel) for rel in STATE_GOVERNANCE_DIR_RELPATHS)


def _resolve_direct_target_paths(target_path, workspace_paths):
  """Resolves absolute candidate paths for a write_file/edit_file target."""
  expanded = os.path.expanduser(target_path)
  if os.path.isabs(expanded):
    return [os.path.abspath(expanded)]
  if workspace_paths:
    return [
        os.path.abspath(os.path.join(os.path.expanduser(ws), target_path))
        for ws in workspace_paths
        if ws
    ]
  return [os.path.abspath(target_path)]


def _check_direct_file_governance(tool_name, args, workspace_paths, state_dir):
  """Checks write_file/edit_file calls against protected governance paths."""
  if tool_name not in (
      "write_file",
      "edit_file",
      "ReplaceFileContent",
      "MultiReplaceFileContent",
  ):
    return None
  target_path = str(args.get("Path") or args.get("TargetFile") or "").strip()
  if not target_path:
    return None
  deny_resp = {
      "decision": "deny",
      "reason": (
          "Blocked self-modification attempt on makeflow governance file"
          f" '{target_path}' ({tool_name})."
      ),
  }
  if _is_state_governance_target(target_path):
    return deny_resp

  norm_workspaces = {
      os.path.abspath(os.path.expanduser(ws))
      for ws in (workspace_paths or [])
      if ws
  }
  for abs_target in _resolve_direct_target_paths(target_path, workspace_paths):
    if any(
        abs_target == pref or abs_target.startswith(pref + os.sep)
        for pref in _KNOWN_PLUGIN_CACHE_PREFIXES
    ):
      return deny_resp
    plugin_root = _find_makeflow_plugin_root(abs_target)
    if plugin_root is None:
      continue
    if not os.path.exists(os.path.join(plugin_root, ".git")):
      return deny_resp
    rel_to_plugin = os.path.relpath(abs_target, plugin_root).replace(
        os.sep, "/"
    )
    if rel_to_plugin in GOVERNANCE_FILE_RELPATHS:
      checkout_state = os.path.join(plugin_root, ".workflow", "state")
      active = is_ticket_active(checkout_state) or (
          plugin_root in norm_workspaces
          and bool(state_dir)
          and is_ticket_active(state_dir)
      )
      if not active:
        return deny_resp
  return None


def _resolve_adv_state_dir(adv, default_state_dir):
  """Resolves effective state directory for an extracted advance call."""
  if adv["stateDir"]:
    return (
        adv["stateDir"]
        if os.path.isabs(adv["stateDir"]) or not adv["workspaceDir"]
        else os.path.join(adv["workspaceDir"], adv["stateDir"])
    )
  if adv["workspaceDir"]:
    return os.path.join(adv["workspaceDir"], ".workflow", "state")
  return default_state_dir


def _validate_advance_calls_guard(adv_calls, default_state_dir, transcript_path):
  """Validates turn boundaries and pause-gate bundling without mutating disk."""
  entered_gates = set()
  for adv in adv_calls:
    curr_t = adv["currentTarget"]
    next_t = adv["nextTarget"]
    eff_state_dir = _resolve_adv_state_dir(adv, default_state_dir)
    if curr_t in entered_gates or (
        len(adv_calls) > 1 and (curr_t, next_t) in PAUSE_GATE_EXITS
    ):
      return {
          "decision": "deny",
          "reason": (
              f"BLOCKED: [SCAR-PROC-102] Cannot bundle pause-gate entry and"
              f" exit ({curr_t} -> {next_t}) in a single command without an"
              " intervening human USER_INPUT turn."
          ),
      }
    if (curr_t, next_t) in PAUSE_GATE_EXITS:
      ok, reason = evaluate_advance_target_turn_boundary(
          curr_t, next_t, transcript_path, state_dir=eff_state_dir
      )
      if not ok:
        return {"decision": "deny", "reason": reason}
    if next_t in PAUSE_GATE_TARGETS:
      entered_gates.add(next_t)
  return None


def _commit_advance_calls_state(adv_calls, default_state_dir, transcript_path):
  """Persists gate_entry.json transitions after all PreToolUse guards pass."""
  for adv in adv_calls:
    curr_t = adv["currentTarget"]
    next_t = adv["nextTarget"]
    ev = adv.get("evidence") if isinstance(adv.get("evidence"), dict) else {}
    is_valid_finish = (
        next_t == "T90"
        and bool(ev.get("complete"))
        and curr_t in ("T80", "T90")
    )
    if (
        next_t not in VALID_TRANSITIONS.get(curr_t, set())
        and not is_valid_finish
    ):
      continue
    eff_state_dir = _resolve_adv_state_dir(adv, default_state_dir)
    if not eff_state_dir:
      continue
    req_exit_key = PAUSE_GATE_EXITS.get((curr_t, next_t))
    exit_approved = (not req_exit_key) or bool(ev.get(req_exit_key))
    if (curr_t, next_t) in PAUSE_GATE_EXITS and exit_approved:
      mark_gate_human_verified(
          eff_state_dir, curr_t, transcript_path=transcript_path
      )
    if next_t in PAUSE_GATE_TARGETS:
      if exit_approved or read_gate_entry(eff_state_dir) is None:
        record_gate_entry(
            eff_state_dir, curr_t, next_t, transcript_path=transcript_path
        )
    elif (
        curr_t in PAUSE_GATE_TARGETS
        and (curr_t, next_t) not in PAUSE_GATE_EXITS
    ):
      clear_gate_entry(eff_state_dir, curr_t)


def evaluate_tool_call(payload):
  """Evaluates a PreToolUse hook payload and returns a decision dictionary.

  Args:
    payload: Parsed JSON hook payload dictionary containing ``toolCall``,
      ``workspacePaths``, and optional ``transcriptPath``.

  Returns:
    Decision dictionary with ``decision`` (``"allow"``, ``"deny"``, or
    ``"force_ask"``) and optional ``reason``.
  """
  tool_call = payload.get("toolCall", {})
  tool_name = tool_call.get("name", "")
  args = tool_call.get("args", {})
  transcript_path = payload.get("transcriptPath") or ""
  workspace_paths = payload.get("workspacePaths") or []
  state_dir = resolve_state_dir(payload)

  airlock_ok, airlock_reason = evaluate_subagent_airlock(payload, state_dir)
  if not airlock_ok:
    return {"decision": "deny", "reason": airlock_reason}

  if tool_name not in ("run_command", "Bash", "bash", "shell"):
    if tool_name == "ask_question":
      ok, reason = evaluate_ask_question_turn_boundary(transcript_path)
      if not ok:
        return {"decision": "deny", "reason": reason}
      return {"decision": "allow"}
    if tool_name == "read_file":
      ok, reason = evaluate_harness_read_boundary(
          payload, state_dir, workspace_paths=workspace_paths
      )
      if not ok:
        return {"decision": "deny", "reason": reason}
      return {"decision": "allow"}
    gov_deny = _check_direct_file_governance(
        tool_name, args, workspace_paths, state_dir
    )
    if gov_deny is not None:
      return gov_deny
    adv_calls = extract_advance_target_calls(
        [tool_call], default_state_dir=state_dir
    )
    if adv_calls:
      deny = _validate_advance_calls_guard(adv_calls, state_dir, transcript_path)
      if deny is not None:
        return deny
      _commit_advance_calls_state(adv_calls, state_dir, transcript_path)
    return {"decision": "allow"}

  cmd = str(
      args.get("CommandLine") or args.get("command") or args.get("cmd") or ""
  ).strip()
  if not cmd:
    return {"decision": "allow"}

  # 1. Production database guard
  db_cfg = load_database_config(payload)
  is_safe, deny_reason = evaluate_database_safety(cmd, db_cfg)
  if not is_safe:
    return {
        "decision": "deny",
        "reason": deny_reason,
    }

  # 2. Self-modification guard
  self_safe, self_reason = evaluate_self_modification_safety(
      cmd, workspace_paths
  )
  if not self_safe:
    return {
        "decision": "deny",
        "reason": self_reason,
    }

  # 3. Shell Makeflow advance turn-boundary and pause-gate bundling guard (read-only)
  shell_adv_calls = extract_advance_target_calls(
      [tool_call], default_state_dir=state_dir
  )
  if shell_adv_calls:
    adv_deny = _validate_advance_calls_guard(
        shell_adv_calls, state_dir, transcript_path
    )
    if adv_deny is not None:
      return adv_deny

  # 4. Destructive Git guards and milestone pause gates
  review_card = os.path.join(state_dir, "45_review_approved.md") if state_dir else ""
  has_review = bool(review_card and os.path.isfile(review_card))
  git_res = evaluate_git_command(
      cmd, has_review, state_dir=state_dir, transcript_path=transcript_path
  )
  enters_pause_gate = bool(
      shell_adv_calls
      and any(a["nextTarget"] in PAUSE_GATE_TARGETS for a in shell_adv_calls)
  )
  is_commit_msg_deny = (
      git_res.get("decision") == "deny"
      and "INVALID_COMMIT_MESSAGE_CONTRACT" in str(git_res.get("reason") or "")
  )
  if enters_pause_gate and (
      git_res.get("decision") == "force_ask" or is_commit_msg_deny
  ):
    return {
        "decision": "deny",
        "reason": (
            "BLOCKED: [SCAR-PROC-102] Cannot bundle pause-gate entry with a"
            " gated Git command in the same shell invocation. Stop tool calls"
            " and wait for explicit human approval."
        ),
    }
  if git_res.get("decision") == "deny":
    return git_res
  if shell_adv_calls:
    _commit_advance_calls_state(shell_adv_calls, state_dir, transcript_path)
  return git_res


class PreToolGuardScript(BaseScript):
  """BaseScript runner for the Antigravity PreToolUse safety hook."""

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parses CLI arguments using SilentArgumentParser.

    Args:
      args: Optional list of CLI argument strings.

    Raises:
      HelpExit: When ``--help`` or ``-h`` is requested.
      ReflexArgumentError: When unknown flags or unexpected arguments are
        provided.
    """
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="pre_tool_guard.py",
        description=(
            "PreToolUse hook guarding production databases, destructive Git"
            " commands, governance files, and workflow pause gates."
        ),
    )
    parser.parse_args(self.raw_args)

  def run(self) -> int:
    """Reads a JSON tool-call payload from stdin and writes a hook decision.

    Fails open on malformed JSON or runtime errors by writing an allow decision
    with the error reason to ``self.stdout`` and returning 0.

    Returns:
      Integer exit code ``0`` under the PreToolUse fail-open contract.
    """
    try:
      input_text = self.stdin.read()
      if not input_text.strip():
        self.stdout.write(json.dumps({"decision": "allow"}) + "\n")
        return 0

      payload = json.loads(input_text)
      result = evaluate_tool_call(payload)
      self.stdout.write(json.dumps(result) + "\n")
    except Exception as exc:  # pylint: disable=broad-except
      self.stdout.write(
          json.dumps({"decision": "allow", "reason": f"Hook error: {exc}"})
          + "\n"
      )
    return 0
