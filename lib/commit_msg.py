"""Conventional Commits formatter, parser, and validator (GH-112, SCAR-ARCH-05).

Enforces Conventional Commits 1.0.0 specification, standardized remote issue
trailers (Issue: #<N>, closes #<N>), local session trailers (Ticket: LOCAL-<N>),
and the 2-line changed files section format.

Subclasses BaseScript to provide CLI validate and format subcommands.
"""

import argparse
from dataclasses import dataclass, field
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple, Union

from lib.agent_paths import safe_abspath, safe_getcwd
from lib.base_script import BaseScript, SilentArgumentParser
from lib.reflex import emit_reflex
from lib.shell_safety import GIT_GLOBAL_VALUE_FLAGS, _unwrap_command_tokens
from lib.state_reports import load_archaeology_hosting_mode
from lib.ticket_state import TicketStateStore

CANONICAL_TYPES: Tuple[str, ...] = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)

_CANONICAL_TYPES_SET = frozenset(CANONICAL_TYPES)

_HEADER_RE = re.compile(
    r"^([a-z]+)(?:\(([a-zA-Z0-9_\-\./]+)\))?(!)?:\s+(.+)$"
)

_LOCAL_BRANCH_RE = re.compile(
    r"^(?:feat|fix|ticket)/local-(?:([1-9][0-9]*)(?:-|$)|.+$)"
)
_GH_CLOSE_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#\d+\b"
)
_BARE_ISSUE_RE = re.compile(r"(?:^|[\s(\[])#\d+\b")
_REMOTE_ISSUE_TRAILER_RE = re.compile(
    r"(?im)^(?:Issue:\s*#(\d+)|(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s*#(\d+))\s*$"
)
_INLINE_CLOSES_RE = re.compile(
    r"(?i)\((?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#\d+\)"
)
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_RECEIPT_HOSTING_MODE_RE = re.compile(
    r"^ {0,3}(?:[-*]\s+)?"
    r"(?:"
    r"(?:\*\*HOSTING_MODE\*\*:|\*\*HOSTING_MODE:\*\*|HOSTING_MODE:)"
    r"\s*"
    r"(?:`(?P<direct_bt>GITHUB|PURE_GIT)`"
    r"|\*\*(?P<direct_bold>GITHUB|PURE_GIT)\*\*"
    r"|(?P<direct_plain>GITHUB|PURE_GIT))"
    r"|"
    r"(?:\*\*(?:Session\s+)?Hosting\s+Mode\*\*:|\*\*(?:Session\s+)?Hosting\s+Mode:\*\*|(?:Session\s+)?Hosting\s+Mode:)"
    r"\s*"
    r"(?:`(?:HOSTING_MODE:\s*)?(?P<label_bt>GITHUB|PURE_GIT)`"
    r"|\*\*(?:HOSTING_MODE:\s*)?(?P<label_bold>GITHUB|PURE_GIT)\*\*"
    r"|(?:HOSTING_MODE:\s*)?(?P<label_plain>GITHUB|PURE_GIT))"
    r")"
    r"(?:\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?"
    r"\s*$",
    re.IGNORECASE,
)


@dataclass
class HeaderInfo:
  """Structured representation of a parsed Conventional Commits header."""

  type_name: str
  scope: Optional[str]
  is_breaking: bool
  description: str
  raw: str


@dataclass
class FileChange:
  """Represents a changed file entry with description and relation lines."""

  path: str
  description: str
  relation: str


@dataclass
class ValidationResult:
  """Result of commit message validation."""

  valid: bool
  errors: List[str] = field(default_factory=list)
  header: Optional[HeaderInfo] = None
  changed_files: List[FileChange] = field(default_factory=list)

  def __bool__(self) -> bool:
    return self.valid


@dataclass(frozen=True)
class CommitInvocation:
  """Parsed ``git commit`` invocation arguments for PreToolUse validation.

  Attributes:
    messages: Ordered inline ``-m`` / ``--message`` values.
    message_file: Path passed to ``-F`` / ``--file`` (including ``"-"``).
    repo_dir: Combined repository directory from global ``-C`` flags, if any.
    is_amend: True when ``--amend`` is specified.
    no_edit: True when ``--no-edit`` is the effective edit option (last flag wins).
    explicit_edit: True when ``--edit`` / ``-e`` is the effective edit option
      (last flag wins).
    alternate_message_flags: Alternate message-source flags (such as ``-C``,
      ``-c``, ``--reuse-message``, ``--reedit-message``, ``--fixup``,
      ``--squash``, ``-t``, ``--template``) present on the command.
  """

  messages: Tuple[str, ...] = ()
  message_file: Optional[str] = None
  repo_dir: Optional[str] = None
  is_amend: bool = False
  no_edit: bool = False
  explicit_edit: bool = False
  alternate_message_flags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HeadCommitCheckResult:
  """Result of post-commit HEAD message and approved tree verification.

  Attributes:
    ok: True when HEAD message and tree pass all post-commit invariants.
    scar_id: Canonical scar identifier (``"GIT-20"`` or ``"GIT-25"``) on failure.
    violation_code: Machine-readable violation code on failure.
    reason: Human-readable explanation of the failure.
    head_message: Raw ``git log -1 --pretty=%B HEAD`` message when readable.
    head_tree: Resolved ``git rev-parse HEAD^{tree}`` hash when readable.
    expected_tree: Expected tree hash read from ``approved_tree.txt``.
  """

  ok: bool
  scar_id: str = ""
  violation_code: str = ""
  reason: str = ""
  head_message: str = ""
  head_tree: str = ""
  expected_tree: str = ""


_COMMIT_LONG_ALT_MESSAGE_FLAGS = frozenset({
    "--reuse-message",
    "--reedit-message",
    "--fixup",
    "--squash",
    "--template",
})
_COMMIT_LONG_OTHER_VALUE_FLAGS = frozenset({
    "--cleanup",
    "--date",
    "--author",
    "--trailer",
    "--pathspec-from-file",
})
_COMMIT_SHORT_ALT_MESSAGE_CHARS = frozenset({"c", "C", "t"})
_COMMIT_SHORT_OTHER_VALUE_CHARS = frozenset({"X"})


def normalize_commit_message(msg: str) -> str:
  """Normalizes line endings and surrounding whitespace for commit comparisons.

  Args:
    msg: Raw commit message text.

  Returns:
    Commit message with CRLF converted to LF and outer whitespace stripped.
  """
  return (msg or "").replace("\r\n", "\n").strip()


def _parse_git_global_and_subcommand(
    git_args: Sequence[str],
) -> Tuple[Optional[str], List[str], Optional[str]]:
  """Splits global git options from the subcommand while capturing ``-C`` paths.

  Args:
    git_args: Token list following the ``git`` executable.

  Returns:
    Tuple of ``(subcommand, subcommand_args, combined_repo_dir)``.
  """
  idx = 0
  n = len(git_args)
  repo_dir: Optional[str] = None
  while idx < n:
    tok = git_args[idx]
    if tok == "--":
      if idx + 1 < n:
        return git_args[idx + 1], list(git_args[idx + 2 :]), repo_dir
      return None, [], repo_dir
    if tok == "-C":
      if idx + 1 < n:
        val = git_args[idx + 1]
        repo_dir = (
            val
            if os.path.isabs(val) or not repo_dir
            else os.path.join(repo_dir, val)
        )
      idx += 2
      continue
    if tok.startswith("-C") and len(tok) > 2:
      val = tok[2:]
      repo_dir = (
          val
          if os.path.isabs(val) or not repo_dir
          else os.path.join(repo_dir, val)
      )
      idx += 1
      continue
    if tok == "-c":
      idx += 2
      continue
    if tok.startswith("-c") and len(tok) > 2:
      idx += 1
      continue
    if tok in GIT_GLOBAL_VALUE_FLAGS:
      idx += 2
      continue
    if any(tok.startswith(flag + "=") for flag in GIT_GLOBAL_VALUE_FLAGS):
      idx += 1
      continue
    if tok.startswith("-"):
      idx += 1
      continue
    return tok, list(git_args[idx + 1 :]), repo_dir
  return None, [], repo_dir


def extract_commit_invocation(
    tokens: Sequence[str],
) -> Optional[CommitInvocation]:
  """Extracts structured commit arguments from a single tokenized shell command.

  Args:
    tokens: Tokenized single shell command (may include wrappers such as ``env``).

  Returns:
    CommitInvocation when ``tokens`` invokes ``git commit``, or None otherwise.
  """
  if not tokens:
    return None
  exe, rem = _unwrap_command_tokens(list(tokens))
  if not exe or os.path.basename(exe).lower() not in ("git", "git.exe"):
    return None
  subcmd, sub_args, repo_dir = _parse_git_global_and_subcommand(rem)
  if subcmd != "commit":
    return None

  messages: List[str] = []
  message_file: Optional[str] = None
  is_amend = False
  no_edit = False
  explicit_edit = False
  alternate_message_flags: List[str] = []
  i = 0
  n = len(sub_args)
  while i < n:
    tok = sub_args[i]
    if tok == "--":
      break
    if tok.startswith("--"):
      flag_name = tok.split("=", 1)[0]
      if tok == "--amend":
        is_amend = True
      elif tok == "--no-edit":
        no_edit = True
        explicit_edit = False
      elif tok == "--edit":
        no_edit = False
        explicit_edit = True
      elif tok == "--message":
        if i + 1 < n:
          messages.append(sub_args[i + 1])
          i += 2
          continue
      elif tok.startswith("--message="):
        messages.append(tok.split("=", 1)[1])
      elif tok == "--file":
        if i + 1 < n:
          message_file = sub_args[i + 1]
          i += 2
          continue
      elif tok.startswith("--file="):
        message_file = tok.split("=", 1)[1]
      elif flag_name in _COMMIT_LONG_ALT_MESSAGE_FLAGS:
        alternate_message_flags.append(flag_name)
        if "=" not in tok and i + 1 < n:
          i += 2
          continue
      elif tok in _COMMIT_LONG_OTHER_VALUE_FLAGS:
        i += 2
        continue
      i += 1
      continue
    if tok.startswith("-") and len(tok) > 1:
      for j in range(1, len(tok)):
        ch = tok[j]
        if ch == "e":
          no_edit = False
          explicit_edit = True
          continue
        if ch == "m":
          val = (
              tok[j + 1 :]
              if j < len(tok) - 1
              else (sub_args[i + 1] if i + 1 < n else "")
          )
          messages.append(val)
          if j == len(tok) - 1 and i + 1 < n:
            i += 1
          break
        if ch == "F":
          val = (
              tok[j + 1 :]
              if j < len(tok) - 1
              else (sub_args[i + 1] if i + 1 < n else "")
          )
          message_file = val
          if j == len(tok) - 1 and i + 1 < n:
            i += 1
          break
        if ch in _COMMIT_SHORT_ALT_MESSAGE_CHARS:
          alternate_message_flags.append(f"-{ch}")
          if j == len(tok) - 1 and i + 1 < n:
            i += 1
          break
        if ch in _COMMIT_SHORT_OTHER_VALUE_CHARS:
          if j == len(tok) - 1 and i + 1 < n:
            i += 1
          break
      i += 1
      continue
    i += 1

  return CommitInvocation(
      messages=tuple(messages),
      message_file=message_file,
      repo_dir=repo_dir,
      is_amend=is_amend,
      no_edit=no_edit,
      explicit_edit=explicit_edit,
      alternate_message_flags=tuple(alternate_message_flags),
  )


def _run_git_readonly(
    repo_root: str, *args: str
) -> Optional[subprocess.CompletedProcess]:
  """Runs a read-only git command inside repo_root.

  Args:
    repo_root: Directory to execute git inside.
    *args: Git command arguments.

  Returns:
    CompletedProcess on execution, or None when repo_root is unavailable or git
    cannot be spawned.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return None
  try:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
  except (subprocess.SubprocessError, OSError):
    return None


def read_head_commit_message(repo_root: str) -> Optional[str]:
  """Reads ``git log -1 --pretty=%B HEAD`` fail-closed inside ``repo_root``.

  Fails closed by returning None immediately when ``repo_root`` is empty,
  missing, or not a directory, or when ``git log`` returns a non-zero exit code.

  Args:
    repo_root: Existing Git repository directory path.

  Returns:
    Raw ``HEAD`` commit message text on success, or None on any error.
  """
  log_proc = _run_git_readonly(repo_root, "log", "-1", "--pretty=%B", "HEAD")
  if log_proc is None or log_proc.returncode != 0:
    return None
  return log_proc.stdout


def detect_commit_context(
    repo_root: str,
    state_dir: Optional[str] = None,
    ticket_id: Optional[str] = None,
    explicit_is_local: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
  """Resolves commit validation mode and ticket identifier from workspace state.

  Reads structured ``10_ticket.json`` via ``TicketStateStore`` (SCAR-ARCH-10),
  active Git branch, Git ``origin`` remote, and canonical
  ``15_archaeology_receipt.json`` hosting mode (falling back to legacy
  ``15_archaeology_receipt.md`` only when JSON is absent).

  Args:
    repo_root: Repository or workspace root directory.
    state_dir: Optional explicit ``.workflow/state`` directory path.
    ticket_id: Optional caller-provided ticket identifier override.
    explicit_is_local: Optional explicit boolean mode override.

  Returns:
    Tuple of ``(is_local, resolved_ticket_id)``.
  """
  eff_state_dir = state_dir or (
      os.path.join(repo_root, ".workflow", "state") if repo_root else ""
  )
  resolved_ticket: Optional[str] = (ticket_id or "").strip() or None
  ticket_meta = (
      TicketStateStore(eff_state_dir).load() if eff_state_dir else None
  )
  if resolved_ticket is None and ticket_meta and ticket_meta.ticket_id:
    resolved_ticket = ticket_meta.ticket_id

  git_dir_proc = _run_git_readonly(repo_root, "rev-parse", "--git-dir")
  in_git_repo = bool(git_dir_proc is not None and git_dir_proc.returncode == 0)

  branch_is_local = False
  if in_git_repo:
    branch_proc = _run_git_readonly(
        repo_root, "rev-parse", "--abbrev-ref", "HEAD"
    )
    b_name = ""
    if branch_proc is not None and branch_proc.returncode == 0:
      b_name = branch_proc.stdout.strip()
    else:
      sym_proc = _run_git_readonly(repo_root, "symbolic-ref", "--short", "HEAD")
      if sym_proc is not None and sym_proc.returncode == 0:
        b_name = sym_proc.stdout.strip()
    if b_name:
      m_branch = _LOCAL_BRANCH_RE.match(b_name)
      if m_branch:
        branch_is_local = True
        if m_branch.group(1) and (
            not resolved_ticket
            or not resolved_ticket.upper().startswith("LOCAL-")
        ):
          resolved_ticket = f"LOCAL-{m_branch.group(1)}"

  if explicit_is_local is not None:
    return explicit_is_local, resolved_ticket

  if resolved_ticket and resolved_ticket.upper().startswith("LOCAL-"):
    return True, resolved_ticket

  if ticket_meta and ticket_meta.source.lower() == "local":
    return True, resolved_ticket

  if branch_is_local:
    return True, resolved_ticket

  json_hosting_mode = (
      load_archaeology_hosting_mode(eff_state_dir) if eff_state_dir else None
  )
  if json_hosting_mode == "PURE_GIT":
    return True, resolved_ticket

  if json_hosting_mode is None:
    receipt_card = (
        os.path.join(eff_state_dir, "15_archaeology_receipt.md")
        if eff_state_dir
        else ""
    )
    if receipt_declares_pure_git(receipt_card):
      return True, resolved_ticket

  if in_git_repo:
    remote_proc = _run_git_readonly(repo_root, "remote", "get-url", "origin")
    if (
        remote_proc is None
        or remote_proc.returncode != 0
        or "github.com" not in (remote_proc.stdout or "").lower()
    ):
      return True, resolved_ticket
    return False, resolved_ticket

  if ticket_meta and (
      ticket_meta.source.lower() == "github"
      or ticket_meta.ticket_id.upper().startswith("GH-")
  ):
    return False, resolved_ticket

  return True, resolved_ticket


def validate_head_commit(
    repo_root: str,
    state_dir: Optional[str] = None,
    commit_msg_file: Optional[str] = None,
) -> HeadCommitCheckResult:
  """Validates ``HEAD`` commit message, ``50_commit_msg.txt`` match, and tree lock.

  Checks three post-commit invariants required when exiting Target 50:
  1. ``git log -1 --pretty=%B HEAD`` satisfies Conventional Commits 1.0.0 and
     remote/local trailer rules (SCAR-GIT-20 / SCAR-GIT-24).
  2. Normalized ``HEAD`` message equals normalized ``50_commit_msg.txt``.
  3. ``git rev-parse HEAD^{tree}`` equals ``approved_tree.txt`` (SCAR-GIT-23).

  Args:
    repo_root: Git repository root directory.
    state_dir: Optional explicit ``.workflow/state`` directory path.
    commit_msg_file: Optional explicit path to the approved commit message file.

  Returns:
    HeadCommitCheckResult describing pass/fail status and diagnostic fields.
  """
  eff_state_dir = state_dir or os.path.join(repo_root, ".workflow", "state")
  if commit_msg_file:
    msg_path = (
        commit_msg_file
        if os.path.isabs(commit_msg_file)
        else os.path.join(repo_root, commit_msg_file)
    )
  else:
    msg_path = os.path.join(eff_state_dir, "50_commit_msg.txt")
  tree_path = os.path.join(eff_state_dir, "approved_tree.txt")

  log_proc = _run_git_readonly(repo_root, "log", "-1", "--pretty=%B", "HEAD")
  if log_proc is None or log_proc.returncode != 0:
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-20",
        violation_code="INVALID_COMMIT_MESSAGE_CONTRACT",
        reason=(
            f"Failed to read HEAD commit message in {repo_root}: "
            f"{(log_proc.stderr or '').strip() if log_proc else 'git unavailable'}"
        ),
    )
  head_msg = log_proc.stdout

  is_local, ticket_id = detect_commit_context(
      repo_root, state_dir=eff_state_dir
  )
  val_res = validate_commit_message(
      head_msg, is_local=is_local, ticket_id=ticket_id
  )
  if not val_res.valid:
    mode_name = "Local session" if is_local else "Remote issue"
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-20",
        violation_code="INVALID_COMMIT_MESSAGE_CONTRACT",
        reason=(
            f"{mode_name} HEAD commit message failed validation: "
            f"{' '.join(val_res.errors)}"
        ),
        head_message=head_msg,
    )

  if not os.path.isfile(msg_path):
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-20",
        violation_code="COMMIT_MESSAGE_DRIFT",
        reason=f"Approved commit message file not found: {msg_path}",
        head_message=head_msg,
    )
  try:
    with open(msg_path, "r", encoding="utf-8", errors="replace") as f:
      expected_msg = f.read()
  except OSError as exc:
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-20",
        violation_code="COMMIT_MESSAGE_DRIFT",
        reason=f"Failed to read approved commit message file {msg_path}: {exc}",
        head_message=head_msg,
    )

  if normalize_commit_message(head_msg) != normalize_commit_message(
      expected_msg
  ):
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-20",
        violation_code="COMMIT_MESSAGE_DRIFT",
        reason=(
            "HEAD commit message does not match approved "
            ".workflow/state/50_commit_msg.txt."
        ),
        head_message=head_msg,
    )

  if not os.path.isfile(tree_path):
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-25",
        violation_code="UNAPPROVED_TREE_DRIFT",
        reason=f"Approved tree lock file not found: {tree_path}",
        head_message=head_msg,
    )
  try:
    with open(tree_path, "r", encoding="utf-8", errors="replace") as f:
      expected_tree = f.read().strip()
  except OSError as exc:
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-25",
        violation_code="UNAPPROVED_TREE_DRIFT",
        reason=f"Failed to read approved tree lock file {tree_path}: {exc}",
        head_message=head_msg,
    )
  if not expected_tree:
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-25",
        violation_code="UNAPPROVED_TREE_DRIFT",
        reason=f"Approved tree lock file is empty: {tree_path}",
        head_message=head_msg,
    )

  tree_proc = _run_git_readonly(repo_root, "rev-parse", "HEAD^{tree}")
  actual_tree = (tree_proc.stdout or "").strip() if tree_proc else ""
  if tree_proc is None or tree_proc.returncode != 0 or actual_tree != expected_tree:
    return HeadCommitCheckResult(
        ok=False,
        scar_id="GIT-25",
        violation_code="UNAPPROVED_TREE_DRIFT",
        reason=(
            f"HEAD^{{tree}} ({actual_tree or 'error'}) does not match "
            f"approved_tree.txt ({expected_tree})."
        ),
        head_message=head_msg,
        head_tree=actual_tree,
        expected_tree=expected_tree,
    )

  return HeadCommitCheckResult(
      ok=True,
      head_message=head_msg,
      head_tree=actual_tree,
      expected_tree=expected_tree,
  )


def parse_commit_header(line: str) -> Optional[HeaderInfo]:
  """Parses a single line against Conventional Commits 1.0.0 header rules.

  Returns HeaderInfo on success, or None if invalid.
  """
  clean = line.strip("\r\n")
  match = _HEADER_RE.match(clean)
  if not match:
    return None

  ctype, scope, breaking_char, desc = match.groups()
  if ctype not in _CANONICAL_TYPES_SET:
    return None

  clean_desc = desc.strip()
  if not clean_desc:
    return None

  return HeaderInfo(
      type_name=ctype,
      scope=scope if scope else None,
      is_breaking=bool(breaking_char),
      description=clean_desc,
      raw=clean,
  )


def parse_receipt_hosting_mode(content: str) -> Optional[str]:
  """Parses explicit hosting mode metadata from an archaeology receipt.

  Skips fenced (`` ``` `` or ``~~~``) and indented (4+ spaces or leading tab)
  code blocks, and matches only anchored metadata declaration lines. When any
  valid metadata declaration specifies ``GITHUB``, ``"GITHUB"`` takes
  precedence over any ``PURE_GIT`` declaration.

  Args:
    content: Raw Markdown contents of ``15_archaeology_receipt.md``.

  Returns:
    ``"GITHUB"`` when an explicit GitHub metadata declaration is present,
    ``"PURE_GIT"`` when an explicit Pure Git metadata declaration is present
    without an overriding GitHub declaration, or ``None`` otherwise.
  """
  if not content:
    return None

  found_pure_git = False
  in_fence = False
  fence_char = ""
  fence_len = 0

  for raw_line in content.splitlines():
    line = raw_line.rstrip("\r")
    fence_match = _FENCE_RE.match(line)
    if in_fence:
      if (
          fence_match
          and fence_match.group(1)[0] == fence_char
          and len(fence_match.group(1)) >= fence_len
          and not fence_match.group(2).strip()
      ):
        in_fence = False
        fence_char = ""
        fence_len = 0
      continue

    if fence_match:
      marker = fence_match.group(1)
      info = fence_match.group(2)
      # Single-line inline triple-backtick spans contain a closing backtick on the same line.
      if not (marker[0] == "`" and "`" in info):
        in_fence = True
        fence_char = marker[0]
        fence_len = len(marker)
      continue

    if line.startswith(("    ", "\t")):
      continue

    match = _RECEIPT_HOSTING_MODE_RE.match(line)
    if not match:
      continue

    mode = (
        match.group("direct_bt")
        or match.group("direct_bold")
        or match.group("direct_plain")
        or match.group("label_bt")
        or match.group("label_bold")
        or match.group("label_plain")
    )
    if mode and mode.upper() == "GITHUB":
      return "GITHUB"
    if mode and mode.upper() == "PURE_GIT":
      found_pure_git = True

  return "PURE_GIT" if found_pure_git else None


def receipt_declares_pure_git(receipt_path: str) -> bool:
  """Returns True when an archaeology receipt explicitly declares PURE_GIT mode.

  Args:
    receipt_path: Path to ``15_archaeology_receipt.md`` or equivalent receipt.

  Returns:
    True when the receipt metadata explicitly declares ``PURE_GIT`` without an
    overriding ``GITHUB`` declaration, or False when the file is missing,
    unreadable, or does not declare Pure Git mode.
  """
  if not receipt_path or not os.path.isfile(receipt_path):
    return False
  try:
    with open(receipt_path, "r", encoding="utf-8", errors="replace") as f:
      content = f.read()
  except OSError:
    return False
  return parse_receipt_hosting_mode(content) == "PURE_GIT"


def _parse_changed_files_section(
    lines: List[str], start_idx: int
) -> Tuple[List[FileChange], List[str], int]:
  """Parses the 'Files changed:' section starting at start_idx.

  Returns (parsed_files, error_messages, next_idx).
  """
  errors: List[str] = []
  files: List[FileChange] = []
  idx = start_idx + 1
  total = len(lines)

  while idx < total:
    raw_line = lines[idx]
    stripped = raw_line.strip()

    # Empty line terminates the section
    if not stripped:
      idx += 1
      break

    # If another section or trailer header starts without bullet or indentation, break
    if not raw_line.startswith((" ", "\t", "-")):
      break

    if raw_line.startswith("-"):
      # Parse item bullet line: '- <path>:'
      bullet_text = stripped[1:].strip()
      if not bullet_text.endswith(":"):
        errors.append(
            f"Changed file bullet must end with ':': '{stripped}'."
        )
      file_path = bullet_text[:-1].strip() if bullet_text.endswith(":") else bullet_text
      if not file_path:
        errors.append(f"Changed file path is missing in bullet: '{stripped}'.")

      # Collect indented child lines for this file item
      indented_lines: List[str] = []
      idx += 1
      while idx < total:
        next_raw = lines[idx]
        next_stripped = next_raw.strip()
        if not next_stripped:
          break
        if next_raw.startswith("-") or not next_raw.startswith((" ", "\t")):
          break
        indented_lines.append(next_stripped)
        idx += 1

      if len(indented_lines) != 2:
        errors.append(
            f"File '{file_path}' in 'Files changed:' must contain exactly 2 indented lines "
            f"(description and relation to request), found {len(indented_lines)} line(s)."
        )
      else:
        desc_line, rel_line = indented_lines[0], indented_lines[1]
        files.append(
            FileChange(
                path=file_path,
                description=desc_line,
                relation=rel_line,
            )
        )
    else:
      errors.append(
          f"Unexpected line in 'Files changed:' without bullet indentation: '{stripped}'."
      )
      idx += 1

  return files, errors, idx


def validate_commit_message(
    msg: str, is_local: bool, ticket_id: Optional[str] = None
) -> ValidationResult:
  """Validates a complete commit message for CC 1.0.0, trailers, and changed files."""
  errors: List[str] = []
  if not msg or not msg.strip():
    return ValidationResult(valid=False, errors=["Commit message is empty."])

  lines = msg.splitlines()
  header_line = lines[0] if lines else ""
  header = parse_commit_header(header_line)

  if header is None:
    errors.append(
        f"Subject line does not conform to Conventional Commits (v1.0.0): '{header_line}'. "
        f"Format: <type>(<scope>): <description>. Types allowed: {', '.join(CANONICAL_TYPES)}."
    )
  else:
    desc_body = _INLINE_CLOSES_RE.sub("", header.description).rstrip()
    if header.description.endswith(".") or desc_body.endswith("."):
      errors.append(
          "Subject line description must not end with a trailing period."
      )

  # Check changed files section if present
  changed_files: List[FileChange] = []
  for i, line in enumerate(lines):
    if line.strip().lower() == "files changed:":
      c_files, c_errs, _ = _parse_changed_files_section(lines, i)
      changed_files.extend(c_files)
      errors.extend(c_errs)
      break

  # Trailer validation depending on is_local
  if is_local:
    # Mode B: Local session task
    if _GH_CLOSE_RE.search(msg) or _BARE_ISSUE_RE.search(msg):
      errors.append(
          "Local session commit message contains 'closes #<N>' or bare '#<N>' GitHub issue syntax. "
          "Use 'Ticket: LOCAL-<N>' instead."
      )

    expected_local = None
    if ticket_id and ticket_id.startswith("LOCAL-"):
      expected_local = ticket_id
    elif ticket_id and ticket_id.isdigit():
      expected_local = f"LOCAL-{ticket_id}"

    if expected_local:
      local_re = re.compile(rf"(?m)^Ticket:\s*{re.escape(expected_local)}\s*$")
    else:
      local_re = re.compile(r"(?m)^Ticket:\s*LOCAL-[1-9][0-9]*\s*$")

    if not local_re.search(msg):
      needed = expected_local or "LOCAL-<N>"
      errors.append(
          f"Local session commit message is missing required 'Ticket: {needed}' line."
      )
  else:
    # Mode A: Remote tracked issue
    if re.search(r"(?im)^Ticket:\s*GH-", msg):
      errors.append(
          "Remote issue commit message contains forbidden 'Ticket: GH-<N>' syntax. "
          "Use 'Issue: #<N>' or 'closes #<N>' instead."
      )
    if re.search(r"(?im)^Ticket:\s*LOCAL-", msg):
      errors.append(
          "Remote issue commit message contains 'Ticket: LOCAL-<N>'. "
          "Local ticket trailers are forbidden on remote tracked issues."
      )

    has_issue_ref = False
    if _REMOTE_ISSUE_TRAILER_RE.search(msg):
      has_issue_ref = True
    elif _GH_CLOSE_RE.search(msg):
      has_issue_ref = True
    elif _INLINE_CLOSES_RE.search(header_line):
      has_issue_ref = True

    if not has_issue_ref:
      errors.append(
          "Remote issue commit message is missing required issue reference trailer "
          "('Issue: #<N>' or 'closes #<N>')."
      )

  return ValidationResult(
      valid=(len(errors) == 0),
      errors=errors,
      header=header,
      changed_files=changed_files,
  )


def format_commit_message(
    type_name: str,
    scope: Optional[str],
    description: str,
    body: Optional[str] = None,
    changed_files: Optional[
        List[Union[FileChange, Dict[str, str], Tuple[str, str, str]]]
    ] = None,
    trailers: Optional[Dict[str, str]] = None,
    is_breaking: bool = False,
    issue_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    is_local: bool = False,
    reviewed_by: Optional[str] = None,
) -> str:
  """Formats a compliant Conventional Commits message with standardized trailers."""
  clean_type = type_name.strip().lower()
  clean_scope = f"({scope.strip()})" if scope and scope.strip() else ""
  bang = "!" if is_breaking else ""
  clean_desc = description.strip().rstrip(".")

  # Header assembly
  if not is_local and issue_id:
    clean_issue = str(issue_id).strip().lstrip("#").replace("GH-", "").replace("gh-", "")
    if f"closes #{clean_issue}" not in clean_desc.lower():
      subject = f"{clean_type}{clean_scope}{bang}: {clean_desc} (closes #{clean_issue})"
    else:
      subject = f"{clean_type}{clean_scope}{bang}: {clean_desc}"
  else:
    subject = f"{clean_type}{clean_scope}{bang}: {clean_desc}"

  sections: List[str] = [subject]

  # Body assembly
  if body and body.strip():
    sections.append(body.strip())

  # Changed files section assembly
  if changed_files:
    file_lines = ["Files changed:"]
    for item in changed_files:
      if isinstance(item, FileChange):
        fpath, fdesc, frel = item.path, item.description, item.relation
      elif isinstance(item, dict):
        fpath = item.get("path", "")
        fdesc = item.get("description", "")
        frel = item.get("relation", "")
      elif isinstance(item, (tuple, list)) and len(item) >= 3:
        fpath, fdesc, frel = item[0], item[1], item[2]
      else:
        continue

      file_lines.append(f"- {fpath}:")
      file_lines.append(f"  {fdesc}")
      file_lines.append(f"  {frel}")

    sections.append("\n".join(file_lines))

  # Trailers assembly
  trailer_lines: List[str] = []
  if is_local:
    t_id = ticket_id or "LOCAL-1"
    if not t_id.startswith("LOCAL-"):
      t_id = f"LOCAL-{t_id}"
    trailer_lines.append(f"Ticket: {t_id}")
  else:
    if issue_id:
      clean_issue = str(issue_id).strip().lstrip("#").replace("GH-", "").replace("gh-", "")
      trailer_lines.append(f"Issue: #{clean_issue}")

  if reviewed_by:
    trailer_lines.append(f"Reviewed-by: {reviewed_by.strip()}")

  if trailers:
    for k, v in trailers.items():
      trailer_lines.append(f"{k.strip()}: {v.strip()}")

  if trailer_lines:
    sections.append("\n".join(trailer_lines))

  return "\n\n".join(sections) + "\n"


class CommitMsgScript(BaseScript):
  """BaseScript runner for commit message formatting and deterministic gate validation."""

  def __init__(self) -> None:
    super().__init__()
    self.subcommand: str = ""
    self.file_path: Optional[str] = None
    self.repo_root: str = safe_getcwd("") or ""
    self.as_json: bool = False
    self.ticket: Optional[str] = None
    self.is_local: Optional[bool] = None

    # Format-specific attributes
    self.type_name: str = ""
    self.scope: Optional[str] = None
    self.description: str = ""
    self.body: Optional[str] = None
    self.changed_files: List[FileChange] = []
    self.issue_id: Optional[str] = None
    self.is_breaking: bool = False
    self.reviewed_by: Optional[str] = None
    self.out_path: Optional[str] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="commit_msg.py",
        description="Format and validate Conventional Commits 1.0.0 messages and trailers.",
        epilog=(
            "Examples:\n"
            "  python3 scripts/commit_msg.py validate --file .workflow/state/50_commit_msg.txt\n"
            "  python3 scripts/commit_msg.py format --type feat --scope cli --desc 'add help output' \\\n"
            "    --issue 170 --file-change lib/base_script.py 'Add HelpExit.' 'Supports --help.' \\\n"
            "    --out .workflow/state/50_commit_msg.txt"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root directory (defaults to current working directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit validation or error output as JSON.",
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        parser_class=SilentArgumentParser,
        help="Subcommand to execute (validate or format).",
    )

    # validate subcommand
    p_val = subparsers.add_parser(
        "validate",
        help="Validate a commit message file or stdin against Conventional Commits 1.0.0.",
        description="Validate a commit message file or stdin against Conventional Commits 1.0.0 and trailer rules.",
        epilog=(
            "Examples:\n"
            "  python3 scripts/commit_msg.py validate --file .workflow/state/50_commit_msg.txt\n"
            "  python3 scripts/commit_msg.py validate --local --ticket LOCAL-1 --file .workflow/state/50_commit_msg.txt"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_val.add_argument(
        "--file",
        default=None,
        help="Path to commit message file (defaults to .workflow/state/50_commit_msg.txt or stdin).",
    )
    p_val.add_argument(
        "--ticket",
        default=None,
        help="Expected ticket identifier (for example GH-170 or LOCAL-1).",
    )
    p_val.add_argument(
        "--local",
        action="store_true",
        default=None,
        dest="local_flag",
        help="Validate in local session task mode (requires 'Ticket: LOCAL-<N>').",
    )
    p_val.add_argument(
        "--remote",
        action="store_false",
        default=None,
        dest="local_flag",
        help="Validate in remote issue mode (requires 'Issue: #<N>' or 'closes #<N>').",
    )
    p_val.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit validation result as JSON.",
    )

    # format subcommand
    p_fmt = subparsers.add_parser(
        "format",
        help="Format a compliant Conventional Commits 1.0.0 message.",
        description="Format a compliant Conventional Commits 1.0.0 message with changed files and trailers.",
        epilog=(
            "Examples:\n"
            "  python3 scripts/commit_msg.py format --type feat --scope cli --desc 'add help flags' \\\n"
            "    --issue 170 --file-change PATH DESC RELATION --out .workflow/state/50_commit_msg.txt\n"
            "  python3 scripts/commit_msg.py format --type fix --desc 'handle local task' --local --ticket LOCAL-2"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fmt.add_argument(
        "--type",
        required=True,
        dest="type_name",
        help="Conventional Commit type (feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert).",
    )
    p_fmt.add_argument(
        "--scope",
        default=None,
        help="Optional commit scope in parentheses.",
    )
    p_fmt.add_argument(
        "--desc",
        required=True,
        dest="description",
        help="Short subject line description without a trailing period.",
    )
    p_fmt.add_argument(
        "--body",
        default=None,
        help="Optional commit body paragraph.",
    )
    p_fmt.add_argument(
        "--issue",
        default=None,
        help="Remote issue number (for example 170 or #170).",
    )
    p_fmt.add_argument(
        "--ticket",
        default=None,
        help="Local session ticket ID (for example LOCAL-1).",
    )
    p_fmt.add_argument(
        "--local",
        action="store_true",
        default=None,
        dest="local_flag",
        help="Format commit message for a local session task.",
    )
    p_fmt.add_argument(
        "--remote",
        action="store_false",
        default=None,
        dest="local_flag",
        help="Format commit message for a remote tracked issue.",
    )
    p_fmt.add_argument(
        "--breaking",
        action="store_true",
        default=False,
        help="Mark commit as a breaking change with '!'.",
    )
    p_fmt.add_argument(
        "--reviewed-by",
        default=None,
        help="Optional 'Reviewed-by:' trailer value.",
    )
    p_fmt.add_argument(
        "--file-change",
        action="append",
        nargs=3,
        metavar=("PATH", "DESC", "RELATION"),
        default=[],
        dest="raw_changes",
        help="Add a 3-tuple entry (PATH DESC RELATION) to the 'Files changed:' section.",
    )
    p_fmt.add_argument(
        "--out",
        default=None,
        help="Write formatted commit message to file instead of stdout.",
    )
    p_fmt.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit errors in JSON format.",
    )

    ns = parser.parse_args(self.raw_args)
    if not ns.subcommand:
      parser.error("Specify a subcommand: validate or format.")

    self.subcommand = ns.subcommand
    self.repo_root = safe_abspath(ns.repo_root or safe_getcwd())
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))

    if self.subcommand == "validate":
      self.file_path = ns.file
      self.ticket = ns.ticket
      self.is_local = ns.local_flag
    elif self.subcommand == "format":
      self.type_name = ns.type_name
      self.scope = ns.scope
      self.description = ns.description
      self.body = ns.body
      self.issue_id = ns.issue
      self.ticket = ns.ticket
      self.is_local = ns.local_flag
      self.is_breaking = ns.breaking
      self.reviewed_by = ns.reviewed_by
      self.out_path = ns.out
      self.changed_files = [
          FileChange(path=c[0], description=c[1], relation=c[2])
          for c in getattr(ns, "raw_changes", []) or []
      ]

  def _detect_is_local(self) -> bool:
    """Detects whether current workspace context is local task mode."""
    is_local, _ = detect_commit_context(
        self.repo_root,
        ticket_id=self.ticket,
        explicit_is_local=self.is_local,
    )
    return is_local

  def _run_validate(self) -> int:
    msg_text = ""
    target_path = self.file_path
    if not target_path:
      default_state_msg = os.path.join(
          self.repo_root, ".workflow", "state", "50_commit_msg.txt"
      )
      if os.path.isfile(default_state_msg):
        target_path = default_state_msg

    if target_path:
      if not os.path.isfile(target_path):
        return emit_reflex(
            stderr=self.stderr,
            scar_id="GIT-20",
            violation_code="INVALID_COMMIT_MESSAGE_CONTRACT",
            trigger=f"Commit message file not found: {target_path}",
            rejected="Running commit validation without a readable commit message file.",
            reflex="Write the proposed commit message to .workflow/state/50_commit_msg.txt and re-run validation.",
            as_json=self.as_json,
        )
      try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
          msg_text = f.read()
      except OSError as exc:
        return emit_reflex(
            stderr=self.stderr,
            scar_id="GIT-20",
            violation_code="INVALID_COMMIT_MESSAGE_CONTRACT",
            trigger=f"Failed to read {target_path}: {exc}",
            rejected="Validating an unreadable commit message file.",
            reflex="Check file permissions on commit message file.",
            as_json=self.as_json,
        )
    else:
      # Read from stdin
      try:
        msg_text = sys.stdin.read()
      except OSError:
        msg_text = ""

    is_local = self._detect_is_local()
    res = validate_commit_message(
        msg_text, is_local=is_local, ticket_id=self.ticket
    )

    if not res.valid:
      error_summary = " ".join(res.errors)
      mode_name = "Local session" if is_local else "Remote issue"
      return emit_reflex(
          stderr=self.stderr,
          scar_id="GIT-20",
          violation_code="INVALID_COMMIT_MESSAGE_CONTRACT",
          trigger=f"{mode_name} commit message validation failed: {error_summary}",
          rejected="Committing message that violates Conventional Commits 1.0.0 or trailer invariants.",
          reflex="Fix commit message subject line and trailers in 50_commit_msg.txt per 05_pre_commit_hygiene.md.",
          as_json=self.as_json,
      )

    if self.as_json:
      self.stdout.write(
          json.dumps(
              {
                  "valid": True,
                  "type": res.header.type_name if res.header else None,
                  "scope": res.header.scope if res.header else None,
                  "description": res.header.description if res.header else None,
                  "changed_files_count": len(res.changed_files),
              }
          )
          + "\n"
      )
    return 0

  def _run_format(self) -> int:
    is_local = self._detect_is_local()
    formatted = format_commit_message(
        type_name=self.type_name,
        scope=self.scope,
        description=self.description,
        body=self.body,
        changed_files=self.changed_files,
        issue_id=self.issue_id,
        ticket_id=self.ticket,
        is_local=is_local,
        is_breaking=self.is_breaking,
        reviewed_by=self.reviewed_by,
    )

    if self.out_path:
      out_target = (
          self.out_path
          if os.path.isabs(self.out_path)
          else os.path.join(self.repo_root, self.out_path)
      )
      os.makedirs(os.path.dirname(out_target), exist_ok=True)
      with open(out_target, "w", encoding="utf-8") as f:
        f.write(formatted)
    else:
      self.stdout.write(formatted)
    return 0

  def run(self) -> int:
    if self.subcommand == "validate":
      return self._run_validate()
    if self.subcommand == "format":
      return self._run_format()
    raise ValueError(f"Unsupported subcommand: {self.subcommand}")
