"""Shared Target 90 commit and workspace state detection helpers."""

import os
import re

from lib.active_state import ActiveStateStore
from lib.shell_safety import (
    _expand_shell_subcommands,
    _parse_git_subcommand,
    _split_git_args,
    _tokenize_subcommand,
    _unwrap_command_tokens,
)

TARGET_90_HEADER_RE = re.compile(
    r"^\s*(?:docs\((?:history|scars|changelog|release)\)|chore\(release\)):",
    re.IGNORECASE,
)

_FEATURE_OR_FIX_HEADER_RE = re.compile(
    r"^\s*(?:feat|fix)(?:\([^)]+\))?!?:",
    re.IGNORECASE,
)

TARGET_90_PATH_FILES = (
    ".workflow/history.json",
    ".workflow/history.md",
    "history.json",
    "history.md",
)

TARGET_90_PATH_DIRS = (".workflow/scars",)

TARGET_90_STATE_FILE_MARKERS = (
    "CHANGELOG.md",
    "changelog.md",
    "plugin.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "package.json",
    "pubspec.yaml",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "composer.json",
    "deno.json",
    "VERSION",
)

TARGET_90_NEXT_ACTION_PATTERNS = (
    (r"(?im)^-\s*Active Target:\s*(?:Target\s*90|T90)\b", 0),
    (r"^\s*-\s*\[>\]\s*(?:Target\s*90|T90)\b", re.M),
    (r"(?im)^#\s*Next Action:.*(?:COMPLETE|make finish satisfied)", 0),
)

_COMMIT_SHORT_VALUE_CHARS = "mFcCtX"
_COMMIT_LONG_VALUE_FLAGS = frozenset({
    "--message",
    "--file",
    "--reuse-message",
    "--reedit-message",
    "--fixup",
    "--squash",
    "--template",
    "--cleanup",
    "--date",
    "--author",
    "--trailer",
    "--pathspec-from-file",
})


def is_target_90_next_action_text(text):
  """Return True if 99_next_action.md text indicates Target 90 or COMPLETE state."""
  if not text:
    return False
  return any(
      re.search(pattern, text, flags)
      for pattern, flags in TARGET_90_NEXT_ACTION_PATTERNS
  )


def is_target_90_state_dir(state_dir):
  """Return True if state_dir indicates an active Target 90 workflow state.

  Reads canonical ``active_target.json`` via ``ActiveStateStore`` first
  (SCAR-ARCH-10). Explicit ``ACTIVE``/``PARKED``/``COMPLETE`` lifecycle states
  are authoritative over ``99_next_action.md``. Falls back to legacy Markdown
  inspection only when ``active_target.json`` is absent or lacks explicit
  status fields.

  Args:
    state_dir: Path to ``.workflow/state``.

  Returns:
    True when Target 90 is active before ``finish``, otherwise False.
  """
  if not state_dir or not os.path.isdir(state_dir):
    return False
  active_state = ActiveStateStore(state_dir).load()
  if active_state is not None:
    if active_state.is_complete():
      return False
    if active_state.has_explicit_status():
      return active_state.is_target_90()
    next_action_card = os.path.join(state_dir, "99_next_action.md")
    if not os.path.isfile(next_action_card):
      return active_state.is_target_90()
  next_action_card = os.path.join(state_dir, "99_next_action.md")
  if not os.path.isfile(next_action_card):
    return False
  try:
    with open(next_action_card, "r", encoding="utf-8") as f:
      txt = f.read()
    return is_target_90_next_action_text(txt)
  except (OSError, UnicodeDecodeError):
    return False


def _normalize_repo_relpath(raw_path):
  """Normalize slashes, quotes, and leading './' for path classification."""
  norm = raw_path.strip().strip("'\"").replace("\\", "/")
  while norm.startswith("./"):
    norm = norm[2:]
  return norm.rstrip("/")


def _classify_target90_path(raw_path):
  """Return 'path', 'state', or 'other' for a commit or add path argument."""
  norm = _normalize_repo_relpath(raw_path)
  if not norm or norm in {".", ".."} or any(ch in norm for ch in "*?["):
    return "other"
  if any(
      norm == m or norm.endswith("/" + m) for m in TARGET_90_PATH_FILES
  ) or any(
      norm == d
      or norm.startswith(d + "/")
      or ("/" + d + "/") in norm
      or norm.endswith("/" + d)
      for d in TARGET_90_PATH_DIRS
  ):
    return "path"
  if any(
      norm == m or norm.endswith("/" + m) for m in TARGET_90_STATE_FILE_MARKERS
  ):
    return "state"
  return "other"


def _extract_commit_args(sub_args):
  """Extract commit messages, pathspec positionals, and --all flag from git commit args."""
  messages = []
  path_args = []
  has_all = False
  i = 0
  n = len(sub_args)
  while i < n:
    tok = sub_args[i]
    if tok == "--":
      path_args.extend(sub_args[i + 1 :])
      break
    if tok.startswith("--"):
      if tok in ("--all", "--amend"):
        has_all = True
      elif tok == "--message" and i + 1 < n:
        messages.append(sub_args[i + 1])
        i += 2
        continue
      elif tok.startswith("--message="):
        messages.append(tok.split("=", 1)[1])
      elif tok in _COMMIT_LONG_VALUE_FLAGS:
        i += 2
        continue
      i += 1
      continue
    if tok.startswith("-") and len(tok) > 1:
      for j in range(1, len(tok)):
        ch = tok[j]
        if ch == "a":
          has_all = True
        if ch in _COMMIT_SHORT_VALUE_CHARS:
          if ch == "m":
            val = (
                tok[j + 1 :]
                if j < len(tok) - 1
                else (sub_args[i + 1] if i + 1 < n else "")
            )
            messages.append(val)
          if j == len(tok) - 1:
            i += 1
          break
      i += 1
      continue
    path_args.append(tok)
    i += 1
  return messages, path_args, has_all


def _merge_staged_scope(current_scope, new_scope):
  """Combine staged path scopes across sequential git add calls."""
  if current_scope == "mixed" or new_scope == "mixed":
    return "mixed"
  if new_scope == "none":
    return current_scope
  if current_scope == "none":
    return new_scope
  if current_scope == "state" or new_scope == "state":
    return "state"
  return "path"


def update_staged_target90_scope(current_scope, subcmd, sub_args):
  """Track staged file scope across git subcommands within one command chain."""
  if subcmd == "commit" or subcmd == "reset":
    return "none"
  if subcmd in ("rm", "mv"):
    return "mixed"
  if subcmd != "add":
    return current_scope
  long_opts, short_opts, _, pos, _ = _split_git_args(
      sub_args,
      short_value_chars="",
      long_value_flags=("--pathspec-from-file", "--chmod"),
  )
  if (
      not pos
      or any(ch in short_opts for ch in ("A", "u", "p", "i"))
      or any(
          opt
          in {
              "--all",
              "--update",
              "--patch",
              "--interactive",
              "--pathspec-from-file",
          }
          or opt.startswith("--pathspec-from-file=")
          for opt in long_opts
      )
  ):
    return "mixed"
  combined = current_scope
  for path in pos:
    kind = _classify_target90_path(path)
    if kind == "other":
      return "mixed"
    combined = _merge_staged_scope(combined, kind)
  return combined


def classify_target_90_commit_subcommand(
    sub_args, is_t90_state=False, staged_scope="none"
):
  """Classify one tokenized git commit subcommand as Target 90 or Target 50."""
  messages, path_args, has_all = _extract_commit_args(sub_args)
  if has_all:
    return False
  has_t90_header = False
  if messages:
    stripped = messages[0].lstrip()
    subject = stripped.splitlines()[0].strip() if stripped else ""
    if _FEATURE_OR_FIX_HEADER_RE.match(subject):
      return False
    has_t90_header = TARGET_90_HEADER_RE.match(subject) is not None

  if path_args:
    kinds = [_classify_target90_path(p) for p in path_args]
    if any(k == "other" for k in kinds):
      return False
    if any(k == "path" for k in kinds):
      return True
    return bool(is_t90_state or has_t90_header)

  if has_t90_header:
    return staged_scope != "mixed"

  if staged_scope == "path":
    return True
  if staged_scope == "state" and is_t90_state:
    return True

  return False


def iter_subcommand_tokens_with_target90(cmd, is_t90_state=False):
  """Yield (tokens, is_git_commit, is_t90_commit) across shell subcommands."""
  staged_scope = "none"
  for subcmd_str in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd_str):
      is_git_commit = False
      is_t90_commit = False
      exe, rem = _unwrap_command_tokens(tokens)
      if exe == "git":
        subcmd, sub_args, _ = _parse_git_subcommand(rem)
        if subcmd == "commit":
          is_git_commit = True
          is_t90_commit = classify_target_90_commit_subcommand(
              sub_args,
              is_t90_state=is_t90_state,
              staged_scope=staged_scope,
          )
          staged_scope = "none"
        elif subcmd:
          staged_scope = update_staged_target90_scope(
              staged_scope, subcmd, sub_args
          )
      yield tokens, is_git_commit, is_t90_commit


def is_target_90_commit_command(cmd, is_t90_state=False):
  """Return True if cmd executes git commit(s) exclusively targeting Target 90."""
  if not cmd:
    return False
  found_commit = False
  for _, is_git_commit, is_t90_commit in iter_subcommand_tokens_with_target90(
      cmd, is_t90_state=is_t90_state
  ):
    if is_git_commit:
      found_commit = True
      if not is_t90_commit:
        return False
  return found_commit
