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

from lib.config import (  # noqa: E402
    _normalize_str_list,
    _parse_csv_env,
    is_plugin_dir,
    load_database_config,
    resolve_state_dir,
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
    _evaluate_single_subcommand,
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
    evaluate_database_safety,
    is_database_command,
)


def _detect_target_90(cmd, state_dir=None):
  """Detects whether command is a Target 90 history ledger or local scar operation."""
  is_history_msg = (
      "docs(history):" in cmd
      or "docs(scars):" in cmd
      or ".workflow/history" in cmd
      or ".workflow/scars" in cmd
      or "history.json" in cmd
      or "history.md" in cmd
  )
  is_t90_state = False
  if state_dir is None:
    if not is_plugin_dir(os.getcwd()):
      cwd_state = os.path.join(os.getcwd(), ".workflow", "state")
      if os.path.isdir(cwd_state):
        state_dir = cwd_state

  if state_dir and os.path.isdir(state_dir):
    next_action_card = os.path.join(state_dir, "99_next_action.md")
    if os.path.isfile(next_action_card):
      try:
        with open(next_action_card, "r", encoding="utf-8") as f:
          txt = f.read()
        if (
            re.search(r"(?im)^-\s*Active Target:\s*(?:Target\s*90|T90)\b", txt)
            or re.search(r"^\s*-\s*\[>\]\s*(?:Target\s*90|T90)\b", txt, re.M)
            or re.search(
                r"(?im)^#\s*Next Action:.*(?:COMPLETE|make finish satisfied)",
                txt,
            )
        ):
          is_t90_state = True
      except (OSError, UnicodeDecodeError):
        pass
  return is_history_msg, is_t90_state


def _evaluate_git_tokens(
    tokens, has_review, is_history_msg=False, is_t90_state=False
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


def evaluate_git_command(cmd, has_review, state_dir=None):
  """Evaluates all subcommands with deny > force_ask > allow precedence."""
  is_history_msg, is_t90_state = _detect_target_90(cmd, state_dir)
  first_ask_reason = None
  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      decision, reason = _evaluate_git_tokens(
          tokens,
          has_review,
          is_history_msg=is_history_msg,
          is_t90_state=is_t90_state,
      )
      if decision == "deny":
        return {"decision": "deny", "reason": reason}
      if decision == "force_ask" and first_ask_reason is None:
        first_ask_reason = reason
  if first_ask_reason is not None:
    return {"decision": "force_ask", "reason": first_ask_reason}
  return {"decision": "allow"}


GOVERNANCE_FILE_RELPATHS = frozenset({
    "scripts/tdd_guard.py",
    "scripts/pre_tool_guard.py",
    "scripts/workflow_gates.py",
    "scripts/emit_reflex.py",
    "lib/base_script.py",
    "lib/tdd_guard.py",
    "lib/workflow_gates.py",
    "lib/reflex.py",
    "lib/config.py",
    "lib/shell_safety.py",
    "hooks.json",
    ".workflow/state/Makefile",
    ".workflow/state/30_tdd_receipt.json",
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


def _extract_redirection_targets(cmd):
  """Extracts output redirection target paths from a shell command string."""
  targets = []
  quote = None
  escaped = False
  i = 0
  n = len(cmd)
  while i < n:
    ch = cmd[i]
    if escaped:
      escaped = False
      i += 1
      continue
    if ch == "\\" and quote != "'":
      escaped = True
      i += 1
      continue
    if quote:
      if ch == quote:
        quote = None
      i += 1
      continue
    if ch in ("'", '"'):
      quote = ch
      i += 1
      continue

    op_start = -1
    op_len = 0
    if ch.isdigit() and (i == 0 or cmd[i - 1] in " \t\r\n;|&()"):
      k = i
      while k < n and cmd[k].isdigit():
        k += 1
      if k < n and cmd[k] == ">":
        op_start = i
        if cmd[k : k + 2] in (">>", ">|"):
          op_len = (k - i) + 2
        else:
          op_len = (k - i) + 1
    elif ch == ">":
      op_start = i
      if cmd[i : i + 2] in (">>", ">|"):
        op_len = 2
      else:
        op_len = 1
    elif cmd[i : i + 2] == "&>":
      op_start = i
      if cmd[i : i + 3] == "&>>":
        op_len = 3
      else:
        op_len = 2

    if op_start != -1:
      j = op_start + op_len
      while j < n and cmd[j] in (" ", "\t"):
        j += 1
      w_buf = []
      w_quote = None
      w_esc = False
      while j < n:
        wc = cmd[j]
        if w_esc:
          w_buf.append(wc)
          w_esc = False
          j += 1
          continue
        if wc == "\\" and w_quote != "'":
          w_esc = True
          j += 1
          continue
        if w_quote:
          if wc == w_quote:
            w_quote = None
          else:
            w_buf.append(wc)
          j += 1
          continue
        if wc in ("'", '"'):
          w_quote = wc
          j += 1
          continue
        if wc in (" ", "\t", "\r", "\n", ";", "|", "&", "<", ">", ")"):
          break
        w_buf.append(wc)
        j += 1
      target = "".join(w_buf).strip()
      if target:
        targets.append(target)
      i = j
      continue

    i += 1
  return targets


def evaluate_self_modification_safety(cmd, workspace_paths=None):
  """Evaluates whether cmd attempts to truncate, overwrite, or delete makeflow governance files."""
  if not cmd or not isinstance(cmd, str):
    return True, ""

  # 1. Check output redirections in raw command and any subshell strings
  all_cmds = [cmd] + _extract_shell_substitutions(cmd)
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


def evaluate_tool_call(payload):
  tool_call = payload.get("toolCall", {})
  tool_name = tool_call.get("name", "")
  args = tool_call.get("args", {})

  if tool_name != "run_command":
    return {"decision": "allow"}

  cmd = args.get("CommandLine", "").strip()
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
  workspace_paths = payload.get("workspacePaths") or []
  self_safe, self_reason = evaluate_self_modification_safety(
      cmd, workspace_paths
  )
  if not self_safe:
    return {
        "decision": "deny",
        "reason": self_reason,
    }

  # 3. Destructive Git guards and milestone pause gates
  state_dir = resolve_state_dir(payload)
  review_card = os.path.join(state_dir, "45_review_approved.md") if state_dir else ""
  has_review = bool(review_card and os.path.isfile(review_card))
  return evaluate_git_command(cmd, has_review, state_dir=state_dir)


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
