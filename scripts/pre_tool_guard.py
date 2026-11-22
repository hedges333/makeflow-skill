#!/usr/bin/env python3
"""PreToolUse hook script for Antigravity.

Blocks production database commands and destructive Git commands, and gates
git commit, git push, gh pr merge, git rebase, and git branch -D.
"""

import json
import os
import re
import shlex
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.config import (
    _normalize_str_list,
    _parse_csv_env,
    is_plugin_dir,
    is_ticket_active,
    load_database_config,
    resolve_state_dir,
)
from lib.target90_guard import (
    is_target_90_commit_command,
    is_target_90_state_dir,
    iter_subcommand_tokens_with_target90,
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
from lib.turn_boundary import (
    PAUSE_GATE_EXITS,
    PAUSE_GATE_TARGETS,
    VALID_TRANSITIONS,
    clear_gate_entry,
    evaluate_advance_target_turn_boundary,
    evaluate_ask_question_turn_boundary,
    evaluate_command_turn_boundary,
    evaluate_subagent_airlock,
    extract_advance_target_calls,
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


def _evaluate_git_tokens(
    tokens,
    has_review,
    is_history_msg=False,
    is_t90_state=False,
    state_dir=None,
    transcript_path="",
):
  """Returns ('deny'|'force_ask'|'allow', reason) for a single command's tokens."""
  exe, rem = _unwrap_command_tokens(tokens)
  if not exe or exe == "SHELL_C":
    return "allow", ""

  if exe == "gh":
    _, _, _, gh_pos, _ = _split_git_args(
        rem,
        short_value_chars="R",
        long_value_flags=("--repo", "--hostname"),
    )
    if len(gh_pos) >= 2 and gh_pos[0] == "pr" and gh_pos[1] == "merge":
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
    return "allow", ""

  if exe != "git":
    return "allow", ""

  subcmd, sub_args, git_configs = _parse_git_subcommand(rem)
  if not subcmd:
    return "allow", ""

  if subcmd == "reset":
    long_opts, _, _, _, _ = _split_git_args(sub_args)
    for bad_flag in ("--hard", "--merge", "--keep"):
      if bad_flag in long_opts:
        return (
            "deny",
            f"Blocked destructive Git command 'git reset {bad_flag}'. "
            "Do not discard uncommitted working-tree changes.",
        )
    return "allow", ""

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
      return (
          "deny",
          "Blocked destructive Git command 'git clean'. "
          "Do not delete untracked working-tree files automatically.",
      )
    return "allow", ""

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
      return (
          "deny",
          "Blocked 'git checkout' with worktree-overwriting flags. "
          "Do not overwrite uncommitted working-tree changes.",
      )
    if has_dd:
      return (
          "deny",
          "Blocked 'git checkout -- <path>'. "
          "Do not overwrite working-tree files with git checkout.",
      )
    if any(
        p in {".", ".."}
        or p.startswith(("./", "../"))
        or any(ch in p for ch in "*?[")
        for p in pos
    ):
      return (
          "deny",
          "Blocked path-based 'git checkout'. "
          "Do not overwrite working-tree files or directories.",
      )
    creating_branch = (
        "b" in short_opts or "B" in short_opts or "--orphan" in long_opts
    )
    if not creating_branch and len(pos) >= 2:
      return (
          "deny",
          "Blocked 'git checkout <tree> <path>'. "
          "Do not overwrite working-tree files with git checkout.",
      )
    return "allow", ""

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
      return (
          "deny",
          "Blocked 'git switch' with force or discard-changes. "
          "Do not discard uncommitted working-tree edits.",
      )
    return "allow", ""

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
      return (
          "deny",
          "Blocked working-tree 'git restore'. "
          "Only 'git restore --staged' is allowed to unstage index entries.",
      )
    return "allow", ""

  if subcmd == "stash":
    _, _, _, pos, _ = _split_git_args(
        sub_args,
        short_value_chars="m",
        long_value_flags=("--message", "--pathspec-from-file"),
    )
    if pos and pos[0] in {"drop", "clear"}:
      return (
          "deny",
          f"Blocked 'git stash {pos[0]}'. Do not delete stashed changes.",
      )
    return "allow", ""

  if subcmd in {"filter-branch", "filter-repo"}:
    return (
        "deny",
        f"Blocked '{subcmd}'. History-rewriting repository filters are forbidden.",
    )

  if subcmd == "reflog":
    _, _, _, pos, _ = _split_git_args(sub_args)
    if pos and pos[0] in {"delete", "expire"}:
      return (
          "deny",
          f"Blocked 'git reflog {pos[0]}'. Do not erase Git recovery reflogs.",
      )
    return "allow", ""

  if subcmd == "update-ref":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args, short_value_chars="m"
    )
    if "d" in short_opts or "--stdin" in long_opts:
      return (
          "deny",
          "Blocked 'git update-ref'. Do not delete or batch-rewrite Git references.",
      )
    return "allow", ""

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
      return (
          "deny",
          "Blocked force-push command. "
          "Force-pushing is forbidden across all branches in the agent workflow.",
      )
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

  if subcmd == "rebase":
    return (
        "force_ask",
        "Git Rebase Gate: git rebase rewrites commit history. "
        "Make sure that upstream merge conflicts require a rebase before you approve.",
    )

  if subcmd == "branch":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args,
        short_value_chars="u",
        long_value_flags=("--set-upstream-to",),
    )
    has_force_short = any(ch in short_opts for ch in ("D", "M", "C", "f"))
    if has_force_short or "--force" in long_opts:
      return (
          "force_ask",
          "Git Branch Force Gate: force-deleting, force-renaming, or force-moving "
          "a branch requires operator approval.",
      )
    return "allow", ""

  if subcmd == "commit":
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

GOVERNANCE_FILE_RELPATHS = frozenset({
    "scripts/tdd_guard.py",
    "scripts/pre_tool_guard.py",
    "scripts/workflow_gates.py",
    "scripts/emit_reflex.py",
    "lib/base_script.py",
    "lib/git_index.py",
    "lib/regression_gate.py",
    "lib/tdd_guard.py",
    "lib/workflow_gates.py",
    "lib/state_machine.py",
    "lib/reflex.py",
    "lib/config.py",
    "lib/shell_safety.py",
    "lib/target90_guard.py",
    "lib/turn_boundary.py",
    "hooks.json",
    *STATE_GOVERNANCE_RELPATHS,
})


def _normalize_gov_path(raw_token):
  if not raw_token or not isinstance(raw_token, str):
    return ""
  token = raw_token.strip().strip("'\"").replace("\\", "/")
  while token.startswith("./"):
    token = token[2:]
  return token.rstrip("/")


def _is_governance_target(raw_token):
  token = _normalize_gov_path(raw_token)
  if not token:
    return False

  if token in GOVERNANCE_FILE_RELPATHS:
    return True

  for rel in GOVERNANCE_FILE_RELPATHS:
    if token.endswith("/" + rel):
      return True

  return False


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

  # 1. Check output redirections in preprocessed command and any subshell strings
  preprocessed = _preprocess_shell(cmd)
  all_cmds = [preprocessed] + _extract_shell_substitutions(preprocessed)
  for c in all_cmds:
    redir_targets = _extract_redirection_targets(c)
    for target in redir_targets:
      if _is_governance_target(target):
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
          norm_arg = _normalize_gov_path(arg)
          if (
              _is_governance_target(arg)
              or norm_arg in {"scripts", "scripts/", "lib", "lib/"}
              or norm_arg.endswith("/scripts")
              or norm_arg.endswith("/lib")
          ):
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
          if _is_governance_target(arg):
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
            if _is_governance_target(arg):
              return (
                  False,
                  f"Blocked self-modification attempt on makeflow governance file '{arg}' (sed -i).",
              )

      if base_exe == "tee":
        for arg in rem:
          if arg.startswith("-"):
            continue
          if _is_governance_target(arg):
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
              or os.path.isdir(dest)
              or (
                  workspace_paths
                  and any(
                      os.path.isdir(os.path.join(ws, norm_dest))
                      for ws in workspace_paths
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
              if _is_governance_target(effective_dest):
                return (
                    False,
                    f"Blocked self-modification attempt on makeflow governance file '{effective_dest}' ({base_exe}).",
                )
          else:
            if _is_governance_target(dest):
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


_KNOWN_PLUGIN_CACHE_PREFIXES = tuple(
    os.path.abspath(os.path.expanduser(p))
    for p in (
        "~/.gemini/config/plugins",
        "~/.gemini/plugins",
        "~/.claude/plugins",
    )
)


def _find_makeflow_plugin_root(abs_target):
  """Returns ancestor directory containing makeflow plugin.json, or None."""
  curr = os.path.dirname(os.path.abspath(abs_target))
  while curr and curr != os.path.dirname(curr):
    pj = os.path.join(curr, "plugin.json")
    if os.path.isfile(pj):
      try:
        with open(pj, "r", encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict) and data.get("name") == "makeflow":
          return curr
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    curr = os.path.dirname(curr)
  return None


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
  if git_res.get("decision") == "deny":
    return git_res
  if (
      shell_adv_calls
      and git_res.get("decision") == "force_ask"
      and any(a["nextTarget"] in PAUSE_GATE_TARGETS for a in shell_adv_calls)
  ):
    return {
        "decision": "deny",
        "reason": (
            "BLOCKED: [SCAR-PROC-102] Cannot bundle pause-gate entry with a"
            " gated Git command in the same shell invocation. Stop tool calls"
            " and wait for explicit human approval."
        ),
    }
  if shell_adv_calls:
    _commit_advance_calls_state(shell_adv_calls, state_dir, transcript_path)
  return git_res


def main():
  try:
    input_text = sys.stdin.read()
    if not input_text.strip():
      print(json.dumps({"decision": "allow"}))
      return

    payload = json.loads(input_text)
    result = evaluate_tool_call(payload)
    print(json.dumps(result))
  except Exception as exc:
    print(json.dumps({"decision": "allow", "reason": f"Hook error: {exc}"}))


if __name__ == "__main__":
  main()
