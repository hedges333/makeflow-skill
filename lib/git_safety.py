"""Shared Git and GitHub CLI command safety classification helpers (GH-213).

Provides structured token classification for destructive Git guards and workflow
approval gates so ``mcp/server.py`` and ``scripts/pre_tool_guard.py`` share one
source of truth without circular dependencies on ``lib/shell_safety.py``.
"""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from lib.shell_safety import (
    _has_clean_force_config,
    _has_push_force_config,
    _parse_git_subcommand,
    _split_git_args,
    _unwrap_command_tokens,
)
from lib.target90_guard import (
    is_target_90_state_dir,
    iter_subcommand_tokens_with_target90,
)


@dataclass(frozen=True)
class GitTokenVerdict:
  """Structured classification result for one tokenized shell command.

  Attributes:
    decision: Classification outcome (``"allow"``, ``"deny"``, or
      ``"force_ask"``).
    rule: Canonical rule identifier for downstream policy rendering.
    detail: Optional rule-specific token detail such as a flag or subcommand.
    positionals: Positional arguments captured when downstream callers inspect
      target refs (for example ``git push`` destination refs).
  """

  decision: str
  rule: str
  detail: str = ""
  positionals: Tuple[str, ...] = ()


_ALLOW_VERDICT = GitTokenVerdict(decision="allow", rule="ok")

_MCP_DENY_REASONS = {
    "git_clean": "Destructive Git command blocked: git clean",
    "git_checkout_force": "Destructive Git command blocked: git checkout --force",
    "git_checkout_double_dash": (
        "Destructive Git command blocked: git checkout -- <path>"
    ),
    "git_checkout_path": (
        "Destructive Git command blocked: path-based git checkout"
    ),
    "git_checkout_tree_path": (
        "Destructive Git command blocked: git checkout <tree> <path>"
    ),
    "git_switch_force": (
        "Destructive Git command blocked: git switch --discard-changes"
    ),
    "git_restore_worktree": (
        "Destructive Git command blocked: working-tree git restore"
    ),
    "git_update_ref": "Destructive Git command blocked: git update-ref",
    "git_push_force": (
        "Destructive Git command blocked: force-push is forbidden"
    ),
}

_MCP_GATE_REASONS = {
    "gh_pr_merge": (
        "Requires human operator approval at Target 75 Landing Gate"
    ),
    "git_push": "Requires human operator approval at Target 60 Push Gate",
    "git_rebase": "Requires human operator approval before running git rebase",
    "git_branch_force": (
        "Requires human operator approval before running git branch force flags"
    ),
}


def classify_git_tokens(tokens: Sequence[str]) -> GitTokenVerdict:
  """Classifies one command token sequence against all 14 Git and gh rules.

  Args:
    tokens: Tokenized single shell command (may include wrapper prefixes such
      as ``env`` or ``sudo``).

  Returns:
    Immutable ``GitTokenVerdict`` describing whether the command is allowed,
    blocked as destructive, or gated for human operator approval.
  """
  exe, rem = _unwrap_command_tokens(tokens)
  if not exe or exe == "SHELL_C":
    return _ALLOW_VERDICT

  if exe == "gh":
    _, _, _, gh_pos, _ = _split_git_args(
        rem,
        short_value_chars="R",
        long_value_flags=("--repo", "--hostname"),
    )
    if len(gh_pos) >= 2 and gh_pos[0] == "pr" and gh_pos[1] == "merge":
      return GitTokenVerdict(
          decision="force_ask",
          rule="gh_pr_merge",
          positionals=tuple(gh_pos),
      )
    return _ALLOW_VERDICT

  if exe != "git":
    return _ALLOW_VERDICT

  subcmd, sub_args, git_configs = _parse_git_subcommand(rem)
  if not subcmd:
    return _ALLOW_VERDICT

  if subcmd == "reset":
    long_opts, _, _, _, _ = _split_git_args(sub_args)
    for bad_flag in ("--hard", "--merge", "--keep"):
      if bad_flag in long_opts:
        return GitTokenVerdict(
            decision="deny",
            rule="git_reset",
            detail=bad_flag,
        )
    return _ALLOW_VERDICT

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
      return GitTokenVerdict(decision="deny", rule="git_clean")
    return _ALLOW_VERDICT

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
      return GitTokenVerdict(decision="deny", rule="git_checkout_force")
    if has_dd:
      return GitTokenVerdict(decision="deny", rule="git_checkout_double_dash")
    if any(
        p in {".", ".."}
        or p.startswith(("./", "../"))
        or any(ch in p for ch in "*?[")
        for p in pos
    ):
      return GitTokenVerdict(decision="deny", rule="git_checkout_path")
    creating_branch = (
        "b" in short_opts or "B" in short_opts or "--orphan" in long_opts
    )
    if not creating_branch and len(pos) >= 2:
      return GitTokenVerdict(decision="deny", rule="git_checkout_tree_path")
    return _ALLOW_VERDICT

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
      return GitTokenVerdict(decision="deny", rule="git_switch_force")
    return _ALLOW_VERDICT

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
      return GitTokenVerdict(decision="deny", rule="git_restore_worktree")
    return _ALLOW_VERDICT

  if subcmd == "stash":
    _, _, _, pos, _ = _split_git_args(
        sub_args,
        short_value_chars="m",
        long_value_flags=("--message", "--pathspec-from-file"),
    )
    if pos and pos[0] in {"drop", "clear"}:
      return GitTokenVerdict(
          decision="deny",
          rule="git_stash",
          detail=pos[0],
      )
    return _ALLOW_VERDICT

  if subcmd in {"filter-branch", "filter-repo"}:
    return GitTokenVerdict(
        decision="deny",
        rule="git_filter",
        detail=subcmd,
    )

  if subcmd == "reflog":
    _, _, _, pos, _ = _split_git_args(sub_args)
    if pos and pos[0] in {"delete", "expire"}:
      return GitTokenVerdict(
          decision="deny",
          rule="git_reflog",
          detail=pos[0],
      )
    return _ALLOW_VERDICT

  if subcmd == "update-ref":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args, short_value_chars="m"
    )
    if "d" in short_opts or "--stdin" in long_opts:
      return GitTokenVerdict(decision="deny", rule="git_update_ref")
    return _ALLOW_VERDICT

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
            in {
                "--force",
                "--force-with-lease",
                "--force-if-includes",
                "--mirror",
            }
            or opt.startswith("--force-with-lease=")
            for opt in long_opts
        )
    )
    has_plus_refspec = (
        any(p.startswith("+") and len(p) > 1 for p in pos)
        or _has_push_force_config(git_configs)
    )
    if has_force_flag or has_plus_refspec:
      return GitTokenVerdict(
          decision="deny",
          rule="git_push_force",
          positionals=tuple(pos),
      )
    return GitTokenVerdict(
        decision="force_ask",
        rule="git_push",
        positionals=tuple(pos),
    )

  if subcmd == "rebase":
    return GitTokenVerdict(decision="force_ask", rule="git_rebase")

  if subcmd == "branch":
    long_opts, short_opts, _, _, _ = _split_git_args(
        sub_args,
        short_value_chars="u",
        long_value_flags=("--set-upstream-to",),
    )
    has_force_short = any(ch in short_opts for ch in ("D", "M", "C", "f"))
    if has_force_short or "--force" in long_opts:
      return GitTokenVerdict(decision="force_ask", rule="git_branch_force")
    return _ALLOW_VERDICT

  if subcmd == "commit":
    return GitTokenVerdict(decision="force_ask", rule="git_commit")

  return _ALLOW_VERDICT


def _evaluate_git_tokens_safety(
    tokens: Sequence[str],
    is_t90_commit: bool = False,
) -> Tuple[bool, str]:
  """Evaluates one tokenized command for MCP ``workflow_safety_check``.

  Args:
    tokens: Tokenized single shell command.
    is_t90_commit: True when the command is classified as a Target 90 ledger,
      scar, changelog, or release commit.

  Returns:
    Tuple of ``(safe, reason)`` where ``safe`` is False for blocked destructive
    commands and ``reason`` is ``"OK"`` or a human-readable block/gate message.
  """
  verdict = classify_git_tokens(tokens)
  if verdict.decision == "allow":
    return True, "OK"

  if verdict.decision == "deny":
    if verdict.rule == "git_reset":
      return False, f"Destructive Git command blocked: git reset {verdict.detail}"
    if verdict.rule == "git_stash":
      return False, f"Destructive Git command blocked: git stash {verdict.detail}"
    if verdict.rule == "git_filter":
      return False, f"Destructive Git command blocked: git {verdict.detail}"
    if verdict.rule == "git_reflog":
      return False, f"Destructive Git command blocked: git reflog {verdict.detail}"
    return False, _MCP_DENY_REASONS[verdict.rule]

  if verdict.rule == "git_commit":
    if is_t90_commit:
      return (
          True,
          "Requires human operator approval at Target 90 History Ledger Gate",
      )
    return True, "Requires human operator approval at Target 50 Commit Gate"

  return True, _MCP_GATE_REASONS[verdict.rule]


def evaluate_git_safety(
    cmd: str,
    state_dir: Optional[str] = None,
) -> Tuple[bool, str]:
  """Checks all shell subcommands for destructive Git operations and gates.

  Args:
    cmd: Raw shell command line string.
    state_dir: Optional path to ``.workflow/state`` used for Target 90 state
      detection.

  Returns:
    Tuple of ``(safe, reason)`` with deny taking precedence over approval gates
    and Target 50 commit gates taking precedence over Target 90 gates when mixed.
  """
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
