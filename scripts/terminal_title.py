#!/usr/bin/env python3
"""Dynamically detects terminal capabilities and updates terminal titles (GH-103, GH-113).

Detects whether running in an interactive terminal supporting title changes (OSC 0,
tmux DCS passthrough, GNU screen), resolves project name, active ticket ID, and issue tag,
and sets the terminal title to '{ticket_number} {project_name}: {issue_tag}'.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, TextIO, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

from base_script import BaseScript
from emit_reflex import SilentArgumentParser

_TICKET_CARD_RE = re.compile(
    r"(?i)(?:-\s*\*\*)?Ticket\s*ID(?:\*\*)?:\s*([A-Za-z0-9_-]+)"
)
_TICKET_INTAKE_RE = re.compile(
    r"(?i)#\s*Ticket\s*Intake:\s*([A-Za-z0-9_-]+)"
)
_TICKET_BULLET_RE = re.compile(
    r"(?i)-\s*Ticket:\s*\[([A-Za-z0-9_-]+):"
)
_NEXT_ACTION_RE = re.compile(
    r"(?i)-\s*Ticket:\s*\[([A-Za-z0-9_-]+):"
)
_BRANCH_TICKET_RE = re.compile(
    r"^(?:feat|fix|ticket)/(?:local-([1-9][0-9]*)|([1-9][0-9]*))(?:-|$)"
)
_CARD_ISSUE_BULLET_RE = re.compile(
    r"(?i)-\s*Ticket:\s*\[[A-Za-z0-9_-]+:\s*(.+?)(?:\]\([^\)\n]*\)|\](?:\s*\(|\s*$))"
)
_CARD_INTAKE_HEADER_RE = re.compile(
    r"(?i)#\s*Ticket\s*Intake:\s*[A-Za-z0-9_-]+:\s*([^\n]+)"
)
_CARD_DESC_RE = re.compile(
    r"(?i)##\s*Description\s*\n+([^\n#]+)"
)
_BRANCH_SLUG_RE = re.compile(
    r"^(?:feat|fix|ticket)/(?:local-[1-9][0-9]*|[1-9][0-9]*)-(.*)$"
)
_BRANCH_GENERIC_SLUG_RE = re.compile(
    r"^(?:feat|fix|ticket)/(.*)$"
)
_TEMP_PATTERN_RE = re.compile(
    r"^tmp(?:[a-z0-5]{8}|[a-z0-9_]{6,})$", re.IGNORECASE
)
_TEMP_IN_STRING_RE = re.compile(
    r"(?:^|[/\\]|\btmp)tmp(?:[a-z0-5]{8}|[a-z0-9_]{6,})\b", re.IGNORECASE
)


def is_temporary_directory_name(path_or_name: str) -> bool:
  """Detects whether a directory path or name matches temporary directory patterns."""
  if not path_or_name:
    return False
  clean = str(path_or_name).strip()
  base = os.path.basename(clean.rstrip("/\\"))
  if _TEMP_PATTERN_RE.match(base):
    return True
  norm = os.path.normpath(clean)
  norm_fwd = norm.replace("\\", "/")
  if norm_fwd.startswith("/tmp/tmp") or "/tmp/tmp" in norm_fwd:
    return True
  temp_dir = tempfile.gettempdir()
  if (norm.startswith(temp_dir) or norm.startswith(os.path.realpath(temp_dir))) and base.startswith("tmp"):
    return True
  return False


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_terminal_title(title: str) -> str:
  """Removes control characters, newlines, and escape sequences from title."""
  if not title:
    return ""
  # Replace line breaks, carriage returns, and tabs with a single space
  cleaned = re.sub(r"[\r\n\t]+", " ", str(title))
  # Remove remaining C0 and C1 control characters (including \x07 BEL and \x1b ESC)
  cleaned = _CONTROL_CHAR_RE.sub("", cleaned)
  # Collapse multiple consecutive whitespace characters and strip
  return re.sub(r"\s+", " ", cleaned).strip()


class StreamProxy:
  """Proxy wrapper for text streams with optional isatty override."""

  def __init__(self, stream: TextIO, force_isatty: bool = False):
    self._stream = stream
    self._force_isatty = force_isatty

  def isatty(self) -> bool:
    if self._force_isatty:
      return True
    return getattr(self._stream, "isatty", lambda: False)()

  def write(self, s: str) -> int:
    return self._stream.write(s)

  def flush(self) -> None:
    if hasattr(self._stream, "flush"):
      self._stream.flush()

  def __getattr__(self, name: str) -> Any:
    return getattr(self._stream, name)


def detect_terminal_capability(
    stream: Optional[TextIO] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
  """Detects if terminal window or tab title can be changed and returns mechanism.

  Returns:
      Tuple of (can_change: bool, mechanism: str).
      Possible mechanisms: 'xterm_osc', 'tmux', 'screen', or rejection reasons
      ('non_tty', 'disabled', 'dumb_term', 'unsupported_term', 'missing_term', 'unrecognized_term').
  """
  target_env = os.environ if env is None else env

  # Opt-out variable
  disabled = target_env.get("MAKEFLOW_DISABLE_TERMINAL_TITLE", "").strip().lower()
  if disabled in ("1", "true", "yes", "on"):
    return False, "disabled"

  # Stream interactive check
  if stream is not None:
    is_tty = getattr(stream, "isatty", lambda: False)()
    if not is_tty:
      return False, "non_tty"

  term = target_env.get("TERM", "").strip()
  if not term:
    return False, "missing_term"

  if term == "dumb":
    return False, "dumb_term"

  if term in ("linux", "cons25"):
    return False, "unsupported_term"

  # Multiplexer detection
  if target_env.get("TMUX") or term.startswith("tmux"):
    return True, "tmux"

  if target_env.get("STY") or term.startswith("screen"):
    return True, "screen"

  # Modern terminal emulators and IDEs
  term_prog = target_env.get("TERM_PROGRAM", "").strip()
  if term_prog in (
      "iTerm.app",
      "Apple_Terminal",
      "vscode",
      "WezTerm",
      "Hyper",
      "ghostty",
      "Tabby",
  ):
    return True, "xterm_osc"

  if target_env.get("WT_SESSION"):
    return True, "xterm_osc"

  if (
      target_env.get("KITTY_WINDOW_ID")
      or target_env.get("ALACRITTY_LOG")
      or target_env.get("KONSOLE_VERSION")
      or target_env.get("VTE_VERSION")
  ):
    return True, "xterm_osc"

  # Known standard XTerm-compatible terminals
  if term.startswith((
      "xterm",
      "rxvt",
      "alacritty",
      "kitty",
      "foot",
      "contour",
      "st-",
      "st",
  )):
    return True, "xterm_osc"

  return False, "unrecognized_term"


def generate_escape_sequence(title: str, mechanism: str) -> str:
  """Generates escape sequence string for setting terminal window/tab title."""
  if not title or not mechanism:
    return ""
  safe_title = sanitize_terminal_title(title)
  if not safe_title:
    return ""
  if mechanism == "xterm_osc":
    return f"\033]0;{safe_title}\007"
  if mechanism == "tmux":
    return f"\033Ptmux;\033\033]0;{safe_title}\007\033\\"
  if mechanism == "screen":
    return f"\033k{safe_title}\033\\"
  return ""


def detect_project_name(
    repo_root: Optional[str] = None,
    git_remote_url: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
  """Detects project name from Git remote origin, local package manifests, or directory name."""
  root = os.path.abspath(repo_root or os.getcwd())

  # Check Git remote origin URL
  remote_url = git_remote_url
  if remote_url is None:
    try:
      proc = subprocess.run(
          ["git", "config", "--get", "remote.origin.url"],
          cwd=root,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      if proc.returncode == 0:
        remote_url = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
      remote_url = None

  if remote_url:
    cleaned = remote_url.strip()
    if cleaned.endswith(".git"):
      cleaned = cleaned[:-4]
    if ":" in cleaned and not cleaned.startswith(("http://", "https://")):
      parts = cleaned.split(":")[-1].split("/")
      candidate = parts[-1].strip()
      if candidate and not is_temporary_directory_name(candidate):
        return candidate
    else:
      parts = cleaned.rstrip("/").split("/")
      candidate = parts[-1].strip()
      if candidate and not is_temporary_directory_name(candidate):
        return candidate

  # Check local package manifests
  manifest_candidates = [
      ("plugin.json", "json", "name"),
      ("package.json", "json", "name"),
  ]
  for fname, ftype, key in manifest_candidates:
    fpath = os.path.join(root, fname)
    if os.path.isfile(fpath):
      try:
        with open(fpath, "r", encoding="utf-8") as f:
          if ftype == "json":
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get(key), str):
              name_val = data[key].strip()
              if name_val and not is_temporary_directory_name(name_val):
                return name_val
      except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass

  base = os.path.basename(root)
  if is_temporary_directory_name(base) or is_temporary_directory_name(root):
    return ""
  return base


def detect_ticket_id(
    repo_root: Optional[str] = None,
    branch: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
  """Detects active ticket ID from state cards, Git branch name, or next action card."""
  root = os.path.abspath(repo_root or os.getcwd())

  # 1. State card .workflow/state/10_ticket.md
  card_10 = os.path.join(root, ".workflow", "state", "10_ticket.md")
  if os.path.isfile(card_10):
    try:
      with open(card_10, "r", encoding="utf-8") as f:
        content = f.read()
      for regex in (_TICKET_CARD_RE, _TICKET_BULLET_RE, _TICKET_INTAKE_RE):
        m = regex.search(content)
        if m:
          return m.group(1).strip()
    except (OSError, UnicodeDecodeError):
      pass

  # 2. Git branch
  current_branch = branch
  if current_branch is None:
    try:
      proc = subprocess.run(
          ["git", "branch", "--show-current"],
          cwd=root,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      if proc.returncode == 0:
        current_branch = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
      current_branch = None

  if current_branch:
    bm = _BRANCH_TICKET_RE.match(current_branch)
    if bm:
      local_num, gh_num = bm.groups()
      if local_num:
        return f"LOCAL-{local_num}"
      if gh_num:
        return f"GH-{gh_num}"

  # 3. Next action readerboard .workflow/state/99_next_action.md
  card_99 = os.path.join(root, ".workflow", "state", "99_next_action.md")
  if os.path.isfile(card_99):
    try:
      with open(card_99, "r", encoding="utf-8") as f:
        content = f.read()
      m = _NEXT_ACTION_RE.search(content)
      if m:
        return m.group(1).strip()
    except (OSError, UnicodeDecodeError):
      pass

  return ""


def detect_issue_tag(
    repo_root: Optional[str] = None,
    branch: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
  """Extracts active issue tag/description from state cards or Git branch slug."""
  root = os.path.abspath(repo_root or os.getcwd())

  def _clean_tag(raw: str) -> str:
    if not raw:
      return ""
    tag = sanitize_terminal_title(raw)
    without_type = re.sub(r"^\s*\[[A-Za-z0-9_-]+\]\s*", "", tag).strip()
    if without_type:
      tag = without_type
    return tag.strip(" -_")

  # 1. State card .workflow/state/10_ticket.md
  card_10 = os.path.join(root, ".workflow", "state", "10_ticket.md")
  if os.path.isfile(card_10):
    try:
      with open(card_10, "r", encoding="utf-8") as f:
        content = f.read()
      m_bullet = _CARD_ISSUE_BULLET_RE.search(content)
      if m_bullet:
        candidate = _clean_tag(m_bullet.group(1))
        if candidate:
          return candidate
      m_intake = _CARD_INTAKE_HEADER_RE.search(content)
      if m_intake:
        candidate = _clean_tag(m_intake.group(1))
        if candidate:
          return candidate
      m_desc = _CARD_DESC_RE.search(content)
      if m_desc:
        candidate = _clean_tag(m_desc.group(1))
        if candidate:
          return candidate
    except (OSError, UnicodeDecodeError):
      pass

  # 2. Readerboard .workflow/state/99_next_action.md
  card_99 = os.path.join(root, ".workflow", "state", "99_next_action.md")
  if os.path.isfile(card_99):
    try:
      with open(card_99, "r", encoding="utf-8") as f:
        content = f.read()
      m_bullet = _CARD_ISSUE_BULLET_RE.search(content)
      if m_bullet:
        candidate = _clean_tag(m_bullet.group(1))
        if candidate:
          return candidate
    except (OSError, UnicodeDecodeError):
      pass

  # 3. Git branch slug
  current_branch = branch
  if current_branch is None:
    try:
      proc = subprocess.run(
          ["git", "branch", "--show-current"],
          cwd=root,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      if proc.returncode == 0:
        current_branch = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
      current_branch = None

  if current_branch:
    bm = _BRANCH_SLUG_RE.match(current_branch)
    if not bm:
      bm = _BRANCH_GENERIC_SLUG_RE.match(current_branch)
    if bm:
      slug = bm.group(1).replace("-", " ").replace("_", " ")
      candidate = _clean_tag(slug)
      if candidate:
        return candidate

  return ""


def format_terminal_title(
    ticket_id: str,
    project_name: str,
    issue_tag: Optional[str] = None,
) -> str:
  """Formats title string as '{ticket_id} {project_name}: {issue_tag}'."""
  t = sanitize_terminal_title(ticket_id)
  p = sanitize_terminal_title(project_name)
  tag = ""
  if issue_tag:
    tag = sanitize_terminal_title(issue_tag).strip()

  if tag:
    if t and p:
      return f"{t} {p}: {tag}"
    if t:
      return f"{t}: {tag}"
    if p:
      return f"{p}: {tag}"
    return tag

  if t and p:
    return f"{t} {p}"
  if t:
    return t
  if p:
    return p
  return ""


def set_terminal_title(
    title: Optional[str] = None,
    repo_root: Optional[str] = None,
    ticket_id: Optional[str] = None,
    project_name: Optional[str] = None,
    issue_tag: Optional[str] = None,
    stream: Optional[TextIO] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
  """Detects capability, formats title, and applies terminal escape sequence.

  Returns:
      Tuple of (success: bool, applied_title: str).
  """
  target_env = os.environ if env is None else env
  root = os.path.abspath(repo_root or os.getcwd())

  final_title = title
  if not final_title:
    t_id = ticket_id if ticket_id is not None else detect_ticket_id(repo_root=root, env=target_env)
    p_name = project_name if project_name is not None else detect_project_name(repo_root=root, env=target_env)
    i_tag = issue_tag
    if i_tag is None and ticket_id is None:
      i_tag = detect_issue_tag(repo_root=root, env=target_env)
    final_title = format_terminal_title(t_id, p_name, i_tag)

  if not final_title:
    return False, ""

  # Refuse temporary directory patterns in title
  if is_temporary_directory_name(final_title) or _TEMP_IN_STRING_RE.search(final_title):
    return False, ""

  opened_file = None
  target_stream = stream
  if target_stream is None:
    if getattr(sys.stderr, "isatty", lambda: False)():
      target_stream = sys.stderr
    elif getattr(sys.stdout, "isatty", lambda: False)():
      target_stream = sys.stdout
    elif os.name == "posix":
      try:
        opened_file = open("/dev/tty", "w", encoding="utf-8")
        target_stream = opened_file
      except OSError:
        target_stream = None

  try:
    can_change, mechanism = detect_terminal_capability(
        stream=target_stream, env=target_env
    )
    if not can_change:
      return False, ""

    seq = generate_escape_sequence(final_title, mechanism)
    if not seq or target_stream is None:
      return False, ""

    target_stream.write(seq)
    if hasattr(target_stream, "flush"):
      target_stream.flush()
    return True, final_title
  except (OSError, UnicodeEncodeError):
    return False, ""
  finally:
    if opened_file is not None:
      try:
        opened_file.close()
      except OSError:
        pass


class TerminalTitleScript(BaseScript):
  """CLI runner for terminal title detection, inspection, and update (SCAR-ARCH-05)."""

  def __init__(self) -> None:
    super().__init__()
    self.command: str = ""
    self.repo_root: str = os.getcwd()
    self.ticket: Optional[str] = None
    self.project: Optional[str] = None
    self.issue_tag: Optional[str] = None
    self.title: Optional[str] = None
    self.as_json: bool = False
    self.env_override: Optional[Dict[str, str]] = None
    self.mock_tty: bool = False

  def execute(
      self,
      args: Optional[List[str]] = None,
      stdout: Optional[TextIO] = None,
      stderr: Optional[TextIO] = None,
      env_override: Optional[Dict[str, str]] = None,
      mock_tty: bool = False,
  ) -> int:
    self.env_override = env_override
    self.mock_tty = mock_tty
    target_out = stdout if stdout is not None else self.stdout
    target_err = stderr if stderr is not None else self.stderr
    return super().execute(args=args, stdout=target_out, stderr=target_err)

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(prog="terminal_title.py", add_help=False)
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--json", action="store_true", dest="as_json")

    subparsers = parser.add_subparsers(
        dest="command", parser_class=SilentArgumentParser
    )

    p_detect = subparsers.add_parser("detect", add_help=False)
    p_detect.add_argument("--json", action="store_true", dest="sub_json")

    p_info = subparsers.add_parser("info", add_help=False)
    p_info.add_argument("--ticket", default=None)
    p_info.add_argument("--project", default=None)
    p_info.add_argument("--issue-tag", default=None)
    p_info.add_argument("--repo-root", default=None)
    p_info.add_argument("--json", action="store_true", dest="sub_json")

    p_set = subparsers.add_parser("set", add_help=False)
    p_set.add_argument("--ticket", default=None)
    p_set.add_argument("--project", default=None)
    p_set.add_argument("--issue-tag", default=None)
    p_set.add_argument("--title", default=None)
    p_set.add_argument("--repo-root", default=None)
    p_set.add_argument("--json", action="store_true", dest="sub_json")

    p_reset = subparsers.add_parser("reset", add_help=False)
    p_reset.add_argument("--project", default=None)
    p_reset.add_argument("--repo-root", default=None)
    p_reset.add_argument("--json", action="store_true", dest="sub_json")

    ns = parser.parse_args(self.raw_args)
    if not ns.command:
      parser.error("Specify a subcommand: detect, info, set, or reset.")

    self.command = ns.command
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
    if getattr(ns, "repo_root", None):
      self.repo_root = os.path.abspath(ns.repo_root)
    self.ticket = getattr(ns, "ticket", None)
    self.project = getattr(ns, "project", None)
    self.issue_tag = getattr(ns, "issue_tag", None)
    self.title = getattr(ns, "title", None)

  def run(self) -> int:
    env = self.env_override if self.env_override is not None else os.environ
    stream_to_check = StreamProxy(self.stderr, force_isatty=self.mock_tty)

    can_change, mechanism = detect_terminal_capability(
        stream=stream_to_check, env=env
    )

    if self.command == "detect":
      if self.as_json:
        self.stdout.write(
            json.dumps({"can_change": can_change, "mechanism": mechanism}) + "\n"
        )
      else:
        self.stdout.write(
            f"can_change={can_change} mechanism={mechanism}\n"
        )
      return 0

    if self.command == "reset":
      project_name = self.project or detect_project_name(
          repo_root=self.repo_root, env=env
      )
      ok = False
      applied = ""
      if project_name and not is_temporary_directory_name(project_name):
        ok, applied = set_terminal_title(
            title=project_name,
            repo_root=self.repo_root,
            stream=stream_to_check,
            env=env,
        )
      if self.as_json:
        self.stdout.write(
            json.dumps(
                {
                    "success": ok,
                    "title": applied,
                    "mechanism": mechanism,
                }
            )
            + "\n"
        )
      else:
        if ok:
          self.stdout.write(f"Reset terminal title to: {applied}\n")
        else:
          self.stdout.write(
              f"Could not reset terminal title (mechanism: {mechanism})\n"
          )
      return 0

    ticket_id = self.ticket or detect_ticket_id(repo_root=self.repo_root, env=env)
    project_name = self.project or detect_project_name(
        repo_root=self.repo_root, env=env
    )
    issue_tag = self.issue_tag
    if issue_tag is None and self.ticket is None:
      issue_tag = detect_issue_tag(repo_root=self.repo_root, env=env)
    title = self.title or format_terminal_title(
        ticket_id, project_name, issue_tag
    )

    if self.command == "info":
      data = {
          "ticket_id": ticket_id,
          "project_name": project_name,
          "issue_tag": issue_tag,
          "title": title,
          "can_change": can_change,
          "mechanism": mechanism,
      }
      if self.as_json:
        self.stdout.write(json.dumps(data) + "\n")
      else:
        self.stdout.write(
            f"Ticket: {ticket_id}\n"
            f"Project: {project_name}\n"
            f"Issue Tag: {issue_tag}\n"
            f"Title: {title}\n"
            f"Supported: {can_change} ({mechanism})\n"
        )
      return 0

    if self.command == "set":
      ok, applied = set_terminal_title(
          title=title,
          repo_root=self.repo_root,
          stream=stream_to_check,
          env=env,
      )
      if self.as_json:
        self.stdout.write(
            json.dumps(
                {
                    "success": ok,
                    "title": applied,
                    "mechanism": mechanism,
                }
            )
            + "\n"
        )
      else:
        if ok:
          self.stdout.write(f"Updated terminal title to: {applied}\n")
        else:
          self.stdout.write(
              f"Could not update terminal title (mechanism: {mechanism})\n"
          )
      return 0

    return 1


if __name__ == "__main__":
  sys.exit(TerminalTitleScript().execute())
