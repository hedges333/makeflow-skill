#!/usr/bin/env python3
"""Conventional Commits formatter, parser, and validator (GH-112, SCAR-ARCH-05).

Enforces Conventional Commits 1.0.0 specification, standardized remote issue
trailers (Issue: #<N>, closes #<N>), local session trailers (Ticket: LOCAL-<N>),
and the 2-line changed files section format.

Subclasses BaseScript to provide CLI validate and format subcommands.
"""

from dataclasses import dataclass, field
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple, Union

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

from base_script import BaseScript
from emit_reflex import SilentArgumentParser, emit_reflex

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
    if header.description.endswith("."):
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
    self.repo_root: str = os.getcwd()
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
    parser = SilentArgumentParser(prog="commit_msg.py", add_help=False)
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--json", action="store_true", dest="as_json")

    subparsers = parser.add_subparsers(
        dest="subcommand", parser_class=SilentArgumentParser
    )

    # validate subcommand
    p_val = subparsers.add_parser("validate", add_help=False)
    p_val.add_argument("--file", default=None)
    p_val.add_argument("--ticket", default=None)
    p_val.add_argument("--local", action="store_true", default=None, dest="local_flag")
    p_val.add_argument("--remote", action="store_false", default=None, dest="local_flag")
    p_val.add_argument("--json", action="store_true", dest="sub_json")

    # format subcommand
    p_fmt = subparsers.add_parser("format", add_help=False)
    p_fmt.add_argument("--type", required=True, dest="type_name")
    p_fmt.add_argument("--scope", default=None)
    p_fmt.add_argument("--desc", required=True, dest="description")
    p_fmt.add_argument("--body", default=None)
    p_fmt.add_argument("--issue", default=None)
    p_fmt.add_argument("--ticket", default=None)
    p_fmt.add_argument("--local", action="store_true", default=None, dest="local_flag")
    p_fmt.add_argument("--remote", action="store_false", default=None, dest="local_flag")
    p_fmt.add_argument("--breaking", action="store_true", default=False)
    p_fmt.add_argument("--reviewed-by", default=None)
    p_fmt.add_argument(
        "--file-change",
        action="append",
        nargs=3,
        metavar=("PATH", "DESC", "RELATION"),
        default=[],
        dest="raw_changes",
    )
    p_fmt.add_argument("--out", default=None)
    p_fmt.add_argument("--json", action="store_true", dest="sub_json")

    ns = parser.parse_args(self.raw_args)
    if not ns.subcommand:
      parser.error("Specify a subcommand: validate or format.")

    self.subcommand = ns.subcommand
    self.repo_root = os.path.abspath(ns.repo_root)
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
    if self.is_local is not None:
      return self.is_local

    if self.ticket and self.ticket.startswith("LOCAL-"):
      return True

    # Check git branch
    try:
      branch_proc = subprocess.run(
          ["git", "rev-parse", "--abbrev-ref", "HEAD"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
      )
      if branch_proc.returncode == 0:
        b_name = branch_proc.stdout.strip()
        if _LOCAL_BRANCH_RE.match(b_name):
          return True
    except (subprocess.SubprocessError, OSError):
      pass

    # Check git remote
    try:
      remote_proc = subprocess.run(
          ["git", "remote", "get-url", "origin"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
      )
      if (
          remote_proc.returncode != 0
          or "github.com" not in (remote_proc.stdout or "").lower()
      ):
        return True
    except (subprocess.SubprocessError, OSError):
      return True

    # Check receipt card
    receipt_card = os.path.join(
        self.repo_root, ".workflow", "state", "15_archaeology_receipt.md"
    )
    if os.path.isfile(receipt_card):
      try:
        with open(receipt_card, "r", encoding="utf-8", errors="replace") as f:
          if "HOSTING_MODE: PURE_GIT" in f.read():
            return True
      except OSError:
        pass

    return False

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
      except OSError as exc:
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


if __name__ == "__main__":
  sys.exit(CommitMsgScript().execute())
