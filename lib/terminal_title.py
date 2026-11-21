"""Dynamically detects terminal capabilities and updates terminal titles (GH-103, GH-113, GH-224).

Detects whether running in an interactive terminal supporting title changes (OSC 0,
tmux DCS passthrough, GNU screen), resolves directory name, active ticket ID, and issue tag,
and sets the terminal title to '{dir_name}/: {ticket_number} {issue_tag}'.
"""

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from lib.base_script import BaseScript, SilentArgumentParser
from lib.ticket_state import TicketStateStore

_BRANCH_TICKET_RE = re.compile(
    r"^(?:feat|fix|ticket)/(?:local-([1-9][0-9]*)|([1-9][0-9]*))(?:-|$)"
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
_PTY_DEVICE_RE = re.compile(
    r"^/dev/(?:pts/\d+|ttys\d+|pty[p-za-e][0-9a-f])$"
)


def _has_temp_segment_under_root(candidate: str, temp_root: str) -> bool:
  """Checks whether candidate lies beneath temp_root with a tmp-prefixed segment.

  Args:
    candidate: Normalized candidate path.
    temp_root: Normalized system temporary root path.

  Returns:
    True if any path component under temp_root begins with 'tmp', else False.
  """
  if not candidate or not temp_root:
    return False
  if not os.path.isabs(candidate) or not os.path.isabs(temp_root):
    return False
  norm_cand = os.path.normpath(candidate)
  norm_root = os.path.normpath(temp_root)
  try:
    if os.path.commonpath([norm_root, norm_cand]) != norm_root:
      return False
  except ValueError:
    return False
  if norm_cand == norm_root:
    return False
  rel = os.path.relpath(norm_cand, norm_root)
  parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]
  return any(part.lower().startswith("tmp") for part in parts)


def is_temporary_directory_name(path_or_name: str) -> bool:
  """Detects whether a directory path or name matches temporary directory patterns.

  Args:
    path_or_name: Directory path or basename candidate.

  Returns:
    True when path_or_name points to or lives inside a temporary directory.
  """
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
  real_temp = os.path.realpath(temp_dir)
  candidates = [norm]
  if os.path.isabs(norm):
    candidates.append(os.path.realpath(norm))
  for cand in candidates:
    for t_root in (temp_dir, real_temp):
      if _has_temp_segment_under_root(cand, t_root):
        return True
  return False


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_terminal_title(title: str) -> str:
  """Removes control characters, newlines, escapes, and backticks from title.

  Args:
    title: Raw terminal title or component string.

  Returns:
    Sanitized single-line string safe for terminal escape sequences.
  """
  if not title:
    return ""
  # Replace line breaks, carriage returns, and tabs with a single space
  cleaned = re.sub(r"[\r\n\t]+", " ", str(title))
  # Remove C0/C1 control characters and markdown backticks
  cleaned = _CONTROL_CHAR_RE.sub("", cleaned).replace("`", "")
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

  Args:
    stream: Optional output stream to check for TTY status.
    env: Optional environment variable mapping override.

  Returns:
    Tuple of (can_change, mechanism). Possible mechanisms are 'xterm_osc',
    'tmux', 'screen', or rejection reasons ('non_tty', 'disabled',
    'dumb_term', 'unsupported_term', 'missing_term', 'unrecognized_term').
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
  if term in ("linux", "cons25"):
    return False, "unsupported_term"

  # Multiplexer detection (checked before TERM=dumb in case subshell clamps TERM)
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

  if (
      target_env.get("WT_SESSION")
      or target_env.get("KITTY_WINDOW_ID")
      or target_env.get("ALACRITTY_LOG")
      or target_env.get("KONSOLE_VERSION")
      or target_env.get("VTE_VERSION")
  ):
    return True, "xterm_osc"

  if not term:
    return False, "missing_term"

  if term == "dumb":
    return False, "dumb_term"

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


def detect_directory_name(repo_root: Optional[str] = None) -> str:
  """Detects the bare unqualified directory name for a repository root.

  Args:
    repo_root: Optional repository path. Defaults to current working directory.

  Returns:
    Sanitized directory basename, or empty string for root or temporary paths.
  """
  root = os.path.abspath(repo_root or os.getcwd())
  base = os.path.basename(root)
  cleaned = sanitize_terminal_title(base)
  if (
      not cleaned
      or is_temporary_directory_name(base)
      or is_temporary_directory_name(root)
      or is_temporary_directory_name(cleaned)
  ):
    return ""
  return cleaned


def detect_ticket_id(
    repo_root: Optional[str] = None,
    branch: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
  """Detects active ticket ID from 10_ticket.json or Git branch name."""
  root = os.path.abspath(repo_root or os.getcwd())

  # 1. Structured JSON state .workflow/state/10_ticket.json
  meta = TicketStateStore.from_repo_root(root).load()
  if meta is not None and meta.ticket_id:
    return meta.ticket_id

  # 2. Git branch fallback
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

  return ""


def detect_issue_tag(
    repo_root: Optional[str] = None,
    branch: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
  """Extracts active issue tag from 10_ticket.json or Git branch slug."""
  root = os.path.abspath(repo_root or os.getcwd())

  def _clean_tag(raw: str) -> str:
    if not raw:
      return ""
    tag = sanitize_terminal_title(raw)
    without_type = re.sub(r"^\s*\[[A-Za-z0-9_-]+\]\s*", "", tag).strip()
    if without_type:
      tag = without_type
    return tag.strip(" -_")

  # 1. Structured JSON state .workflow/state/10_ticket.json
  meta = TicketStateStore.from_repo_root(root).load()
  if meta is not None and meta.has_distinct_title():
    candidate = _clean_tag(meta.title)
    if candidate and (
        not meta.ticket_id or candidate.lower() != meta.ticket_id.strip().lower()
    ):
      return candidate

  # 2. Git branch slug fallback
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
    ticket_id: str = "",
    dir_name: str = "",
    issue_tag: Optional[str] = None,
) -> str:
  """Formats title string using the simplified two-format contract.

  Args:
    ticket_id: Active ticket identifier (for example 'GH-224').
    dir_name: Unqualified directory name.
    issue_tag: Optional short issue description tag.

  Returns:
    '{dir_name}/: {ticket_id} {issue_tag}' (or without tag if empty or
    identical to ticket_id) when both dir_name and ticket_id are present,
    '{dir_name}/' when dir_name is present without ticket_id, or '' when
    dir_name is empty.
  """
  t = sanitize_terminal_title(ticket_id)
  d = sanitize_terminal_title(dir_name).rstrip("/\\").strip()
  if not d:
    return ""
  if not t:
    return f"{d}/"
  tag = sanitize_terminal_title(issue_tag) if issue_tag else ""
  if tag and tag.lower() != t.lower():
    return f"{d}/: {t} {tag}"
  return f"{d}/: {t}"


def _parse_proc_stat_entry(stat_text: str) -> Optional[Tuple[int, int]]:
  """Extracts (ppid, tty_nr) from a Linux /proc/<pid>/stat line.

  Args:
    stat_text: Raw contents of /proc/<pid>/stat.

  Returns:
    Tuple of (ppid, tty_nr) or None if malformed.
  """
  if not stat_text:
    return None
  rp = stat_text.rfind(")")
  if rp == -1:
    return None
  fields = stat_text[rp + 1 :].split()
  if len(fields) < 5:
    return None
  try:
    return int(fields[1]), int(fields[4])
  except ValueError:
    return None


def _decode_tty_nr(tty_nr: int) -> Optional[str]:
  """Decodes a Linux /proc tty_nr device number into a device path.

  Args:
    tty_nr: Raw tty_nr integer from /proc/<pid>/stat.

  Returns:
    Decoded device path (such as '/dev/pts/8' or '/dev/tty2'), or None.
  """
  if tty_nr <= 0 or not hasattr(os, "major") or not hasattr(os, "minor"):
    return None
  major = os.major(tty_nr)
  minor = os.minor(tty_nr)
  if 136 <= major <= 143:
    return f"/dev/pts/{(major - 136) * 256 + minor}"
  if major == 4 and 0 <= minor < 64:
    return f"/dev/tty{minor}"
  return None


def _parse_ps_entry(ps_text: str) -> Optional[Tuple[int, Optional[str]]]:
  """Extracts (ppid, tty_path) from `ps -p <pid> -o ppid=,tty=` output.

  Args:
    ps_text: Standard output from POSIX ps command.

  Returns:
    Tuple of (ppid, device_path_or_none) or None if unparseable.
  """
  if not ps_text:
    return None
  parts = ps_text.strip().split()
  if len(parts) < 2:
    return None
  try:
    ppid = int(parts[0])
  except ValueError:
    return None
  tty = parts[1].strip()
  if tty in ("", "?", "??", "-"):
    return ppid, None
  dev_path = tty if tty.startswith("/dev/") else f"/dev/{tty}"
  return ppid, dev_path


def _is_safe_pty_device(
    path: str,
    uid: int,
    lstat_fn: Callable[[str], os.stat_result],
) -> bool:
  """Validates pseudo-terminal path regex, character device mode, and UID ownership.

  Args:
    path: Candidate device path.
    uid: Required owning UID.
    lstat_fn: Stat lookup function (os.lstat in production).

  Returns:
    True if path is a safe same-UID pseudo-terminal character device.
  """
  if not path or not _PTY_DEVICE_RE.match(path):
    return False
  try:
    st = lstat_fn(path)
  except OSError:
    return False
  return stat.S_ISCHR(st.st_mode) and st.st_uid == uid


def find_ancestor_pty(
    start_pid: Optional[int] = None,
    *,
    max_depth: int = 32,
    proc_dir: str = "/proc",
    stat_reader: Optional[Callable[[int], Optional[str]]] = None,
    ps_runner: Optional[Callable[[int], Optional[str]]] = None,
    lstat_fn: Optional[Callable[[str], os.stat_result]] = None,
    uid: Optional[int] = None,
) -> Optional[str]:
  """Finds a safe same-UID outer ancestor pseudo-terminal device path.

  Args:
    start_pid: Starting process ID (defaults to os.getpid()).
    max_depth: Maximum number of ancestor hops to inspect.
    proc_dir: Root procfs directory path (defaults to '/proc').
    stat_reader: Optional callback returning /proc/<pid>/stat text for tests.
    ps_runner: Optional callback returning `ps` output text for tests.
    lstat_fn: Optional stat callback replacing os.lstat for tests.
    uid: Optional expected owning UID (defaults to os.getuid()).

  Returns:
    Validated outer pseudo-terminal path (for example '/dev/pts/8'), or None.
  """
  if os.name != "posix":
    return None
  if uid is None and not hasattr(os, "getuid"):
    return None
  current_uid = os.getuid() if uid is None else uid
  lstat_cb = os.lstat if lstat_fn is None else lstat_fn
  pid = os.getpid() if start_pid is None else start_pid
  if pid <= 0 or max_depth <= 0:
    return None

  use_proc = stat_reader is not None or os.path.isdir(proc_dir)

  def _read_entry(target_pid: int) -> Optional[Tuple[int, Optional[str]]]:
    if use_proc:
      raw_stat: Optional[str] = None
      if stat_reader is not None:
        raw_stat = stat_reader(target_pid)
      else:
        stat_path = os.path.join(proc_dir, str(target_pid), "stat")
        try:
          with open(stat_path, "r", encoding="utf-8") as f:
            raw_stat = f.read()
        except (OSError, UnicodeDecodeError):
          return None
      parsed = _parse_proc_stat_entry(raw_stat or "")
      if parsed is None:
        return None
      ppid, tty_nr = parsed
      return ppid, _decode_tty_nr(tty_nr)

    raw_ps: Optional[str] = None
    if ps_runner is not None:
      raw_ps = ps_runner(target_pid)
    else:
      try:
        proc = subprocess.run(
            ["ps", "-p", str(target_pid), "-o", "ppid=,tty="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if proc.returncode == 0:
          raw_ps = proc.stdout
      except (OSError, subprocess.SubprocessError):
        return None
    return _parse_ps_entry(raw_ps or "")

  start_info = _read_entry(pid)
  if start_info is None:
    return None
  next_pid, own_tty_path = start_info
  visited = {pid}

  for _ in range(max_depth):
    if next_pid <= 1 or next_pid in visited:
      break
    visited.add(next_pid)
    info = _read_entry(next_pid)
    if info is None:
      break
    ppid, dev_path = info
    if (
        dev_path
        and dev_path != own_tty_path
        and _is_safe_pty_device(dev_path, current_uid, lstat_cb)
    ):
      return dev_path
    next_pid = ppid

  return None


def _is_unmocked_os_stream(stream: Optional[TextIO]) -> bool:
  """Returns True when stream targets unmocked OS stdio rather than test doubles.

  Args:
    stream: Stream passed by caller or wrapped inside StreamProxy.

  Returns:
    True if stream is None (with unmocked OS TTYs) or wraps sys.__stderr__ /
    sys.__stdout__ without forced TTY mocking.
  """
  if stream is None:
    if (
        sys.stderr is not sys.__stderr__
        and getattr(sys.stderr, "isatty", lambda: False)()
    ):
      return False
    if (
        sys.stdout is not sys.__stdout__
        and getattr(sys.stdout, "isatty", lambda: False)()
    ):
      return False
    return True
  raw = stream
  if isinstance(stream, StreamProxy):
    if stream._force_isatty:
      return False
    raw = stream._stream
  if raw is None:
    return True
  return raw is sys.__stderr__ or raw is sys.__stdout__


def _resolve_capability_and_ancestor_pty(
    stream: Optional[TextIO],
    env: Dict[str, str],
) -> Tuple[bool, str, Optional[str]]:
  """Resolves terminal capability and outer ancestor PTY path when applicable.

  Args:
    stream: Optional output stream or StreamProxy.
    env: Environment variable mapping.

  Returns:
    Tuple of (can_change, mechanism, ancestor_pty_path_or_none).
  """
  can_change, mechanism = detect_terminal_capability(stream=stream, env=env)
  if mechanism in ("disabled", "unsupported_term") or not _is_unmocked_os_stream(
      stream
  ):
    return can_change, mechanism, None

  ancestor_pty = find_ancestor_pty()
  if not ancestor_pty:
    return can_change, mechanism, None

  if not can_change and mechanism == "non_tty":
    can_change, mechanism = detect_terminal_capability(stream=None, env=env)
  if not can_change and mechanism in ("dumb_term", "missing_term"):
    if env.get("TMUX"):
      mechanism = "tmux"
    elif env.get("STY"):
      mechanism = "screen"
    else:
      mechanism = "xterm_osc"
    can_change = True
  return can_change, mechanism, ancestor_pty


def _write_escape_to_pty(path: str, seq: str) -> bool:
  """Opens an ancestor PTY non-blockingly, checks isatty, and writes seq.

  Args:
    path: Validated pseudo-terminal device path.
    seq: Escape sequence text to encode and write.

  Returns:
    True if the complete sequence was written to a TTY descriptor, else False.
  """
  fd: Optional[int] = None
  try:
    fd = os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    if not os.isatty(fd):
      return False
    payload = seq.encode("utf-8")
    view = memoryview(payload)
    total_written = 0
    while total_written < len(payload):
      written = os.write(fd, view[total_written:])
      if written <= 0:
        return False
      total_written += written
    return True
  except (OSError, UnicodeEncodeError):
    return False
  finally:
    if fd is not None:
      try:
        os.close(fd)
      except OSError:
        pass


def set_terminal_title(
    title: Optional[str] = None,
    repo_root: Optional[str] = None,
    ticket_id: Optional[str] = None,
    dir_name: Optional[str] = None,
    issue_tag: Optional[str] = None,
    stream: Optional[TextIO] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
  """Detects capability, formats title, and applies terminal escape sequence.

  Args:
    title: Optional explicit full title override.
    repo_root: Optional repository root path.
    ticket_id: Optional ticket identifier override.
    dir_name: Optional directory name override.
    issue_tag: Optional short issue description tag override.
    stream: Optional output stream for TTY detection and escape emission.
    env: Optional environment variable mapping override.

  Returns:
    Tuple of (success: bool, applied_title: str).
  """
  target_env = os.environ if env is None else env
  root = os.path.abspath(repo_root or os.getcwd())

  if title:
    final_title = sanitize_terminal_title(title)
  else:
    t_id = (
        ticket_id
        if ticket_id is not None
        else detect_ticket_id(repo_root=root, env=target_env)
    )
    d_name = (
        dir_name
        if dir_name is not None
        else detect_directory_name(repo_root=root)
    )
    i_tag = issue_tag
    if i_tag is None and ticket_id is None:
      i_tag = detect_issue_tag(repo_root=root, env=target_env)
    final_title = format_terminal_title(t_id, d_name, i_tag)

  if not final_title:
    return False, ""

  # Refuse temporary directory patterns in title
  if is_temporary_directory_name(final_title) or _TEMP_IN_STRING_RE.search(final_title):
    return False, ""

  can_change, mechanism, ancestor_pty = _resolve_capability_and_ancestor_pty(
      stream=stream, env=target_env
  )
  if not can_change:
    return False, ""

  seq = generate_escape_sequence(final_title, mechanism)
  if not seq:
    return False, ""

  if ancestor_pty is not None:
    if _write_escape_to_pty(ancestor_pty, seq):
      return True, final_title
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
    if target_stream is None:
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
    self.directory: Optional[str] = None
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
    parser = SilentArgumentParser(
        prog="terminal_title.py",
        description="Detect terminal capabilities and update terminal window titles (GH-103, GH-113).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/terminal_title.py detect --json\n"
            "  python3 scripts/terminal_title.py info --repo-root .\n"
            "  python3 scripts/terminal_title.py set --ticket GH-170 --issue-tag 'cli help output'\n"
            "  python3 scripts/terminal_title.py reset"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit output as JSON.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        parser_class=SilentArgumentParser,
        help="Terminal title subcommand to execute.",
    )

    p_detect = subparsers.add_parser(
        "detect",
        help="Detect whether the current terminal supports title updates.",
        description="Detect whether the active terminal supports window/tab title changes and report the mechanism.",
        epilog="Example:\n  python3 scripts/terminal_title.py detect --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_detect.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit detection result as JSON.",
    )

    p_info = subparsers.add_parser(
        "info",
        help="Inspect resolved ticket ID, directory name, issue tag, and formatted title.",
        description="Display the detected ticket ID, directory name, issue tag, formatted title, and capability.",
        epilog="Example:\n  python3 scripts/terminal_title.py info --repo-root . --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_info.add_argument(
        "--ticket",
        default=None,
        help="Override ticket identifier.",
    )
    p_info.add_argument(
        "--directory",
        default=None,
        help="Override directory name.",
    )
    p_info.add_argument(
        "--issue-tag",
        default=None,
        help="Override short issue description tag.",
    )

    p_set = subparsers.add_parser(
        "set",
        help="Format and apply the terminal window/tab title for the active ticket.",
        description="Format and write the escape sequence to set the terminal window or tab title.",
        epilog="Example:\n  python3 scripts/terminal_title.py set --ticket GH-170 --directory makeflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_set.add_argument(
        "--ticket",
        default=None,
        help="Ticket identifier (for example GH-170 or LOCAL-1).",
    )
    p_set.add_argument(
        "--directory",
        default=None,
        help="Directory name.",
    )
    p_set.add_argument(
        "--issue-tag",
        default=None,
        help="Short issue description tag.",
    )
    p_set.add_argument(
        "--title",
        default=None,
        help="Explicit full title string override.",
    )

    p_reset = subparsers.add_parser(
        "reset",
        help="Reset the terminal title to the directory name.",
        description="Reset the terminal window or tab title to the directory name with trailing slash.",
        epilog="Example:\n  python3 scripts/terminal_title.py reset --repo-root .",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_reset.add_argument(
        "--directory",
        default=None,
        help="Explicit directory name override.",
    )

    for subparser, json_help in (
        (p_info, "Emit info result as JSON."),
        (p_set, "Emit set result as JSON."),
        (p_reset, "Emit reset result as JSON."),
    ):
      subparser.add_argument(
          "--repo-root",
          default=None,
          dest="sub_repo_root",
          help="Repository root directory.",
      )
      subparser.add_argument(
          "--json",
          action="store_true",
          dest="sub_json",
          help=json_help,
      )

    ns = parser.parse_args(self.raw_args)
    if not ns.command:
      parser.error("Specify a subcommand: detect, info, set, or reset.")

    self.command = ns.command
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
    self.repo_root = os.path.abspath(
        getattr(ns, "sub_repo_root", None)
        or getattr(ns, "repo_root", None)
        or os.getcwd()
    )
    self.ticket = getattr(ns, "ticket", None)
    self.directory = getattr(ns, "directory", None)
    self.issue_tag = getattr(ns, "issue_tag", None)
    self.title = getattr(ns, "title", None)

  def run(self) -> int:
    """Executes the selected terminal title subcommand.

    Returns:
      Process exit code (0 on handled subcommands, 1 on unknown command).
    """
    env = self.env_override if self.env_override is not None else os.environ
    stream_to_check = StreamProxy(self.stderr, force_isatty=self.mock_tty)

    can_change, mechanism, _ = _resolve_capability_and_ancestor_pty(
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

    dir_name = (
        self.directory
        if self.directory is not None
        else detect_directory_name(repo_root=self.repo_root)
    )

    if self.command == "reset":
      ok = False
      applied = ""
      reset_title = (
          format_terminal_title("", dir_name)
          if dir_name and not is_temporary_directory_name(dir_name)
          else ""
      )
      if reset_title:
        ok, applied = set_terminal_title(
            title=reset_title,
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
    issue_tag = self.issue_tag
    if issue_tag is None and self.ticket is None:
      issue_tag = detect_issue_tag(repo_root=self.repo_root, env=env)
    title = (
        sanitize_terminal_title(self.title)
        if self.title is not None
        else format_terminal_title(ticket_id, dir_name, issue_tag)
    )

    if self.command == "info":
      data = {
          "ticket_id": ticket_id,
          "directory_name": dir_name,
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
            f"Directory: {dir_name}\n"
            f"Issue Tag: {issue_tag}\n"
            f"Title: {title}\n"
            f"Supported: {can_change} ({mechanism})\n"
        )
      return 0

    if self.command == "set":
      ok = False
      applied = ""
      if title:
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
