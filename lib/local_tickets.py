"""Directory-per-ticket local backlog and dependency manager (GH-124).

Stores local tickets as individual JSON files in .workflow/tickets/LOCAL-<N>.json
to prevent binary SQLite merge conflicts across concurrent work clone directories.
Provides collision-free next-ID allocation across history.json, .workflow/tickets/,
active WorkspaceMutex locks, and Git branches, plus Mode B priority sorting and
Target 10 local dependency blocker checks.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

from lib.agent_paths import safe_abspath, safe_getcwd
from lib.base_script import BaseScript, SilentArgumentParser
from lib.workspace_mutex import WorkspaceMutex

_LOCAL_ID_RE = re.compile(r"^(?:local-)?([1-9][0-9]*)$", re.IGNORECASE)
_LOCAL_NUM_RE = re.compile(r"^LOCAL-([1-9][0-9]*)$", re.IGNORECASE)
_BRANCH_LOCAL_RE = re.compile(
    r"(?:^|[\s/])(?:feat|fix|ticket)/local-([1-9][0-9]*)(?:-|$)",
    re.IGNORECASE,
)
_VALID_PRIORITIES = {"", "P0", "P1", "P2", "P3"}
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class LocalTicketStore:
  """Manages .workflow/tickets/LOCAL-<N>.json files and local dependency resolution."""

  def __init__(
      self,
      repo_root: Optional[str] = None,
      lock_dir: Optional[str] = None,
  ) -> None:
    self.repo_root: str = safe_abspath(repo_root or safe_getcwd())
    self.lock_dir: Optional[str] = lock_dir
    self.tickets_dir: str = os.path.join(self.repo_root, ".workflow", "tickets")

  @staticmethod
  def normalize_ticket_id(ticket_id: str) -> str:
    """Normalize '16', 'local-16', or 'LOCAL-16' to canonical 'LOCAL-16'."""
    clean = (ticket_id or "").strip()
    m = _LOCAL_ID_RE.match(clean)
    if m:
      return f"LOCAL-{int(m.group(1))}"
    return clean.upper()

  @staticmethod
  def _extract_local_num(ticket_id: str) -> Optional[int]:
    m = _LOCAL_NUM_RE.match((ticket_id or "").strip())
    if m:
      return int(m.group(1))
    return None

  def ticket_path(self, ticket_id: str) -> str:
    """Return the absolute path to .workflow/tickets/<NORMALIZED_ID>.json."""
    norm_id = self.normalize_ticket_id(ticket_id)
    return os.path.join(self.tickets_dir, f"{norm_id}.json")

  def _completed_history_ids(self) -> Set[str]:
    """Return normalized ticket IDs recorded in .workflow/history.json."""
    history_path = os.path.join(self.repo_root, ".workflow", "history.json")
    completed: Set[str] = set()
    if not os.path.isfile(history_path):
      return completed
    try:
      with open(history_path, "r", encoding="utf-8") as f:
        data = json.load(f)
      if isinstance(data, list):
        for entry in data:
          if isinstance(entry, dict) and entry.get("id"):
            completed.add(self.normalize_ticket_id(str(entry["id"])))
    except (OSError, json.JSONDecodeError, ValueError):
      return completed
    return completed

  def _git_branch_local_nums(self) -> Set[int]:
    """Return integer N values from local and remote Git branches matching */local-<N>-*."""
    nums: Set[int] = set()
    try:
      proc = subprocess.run(
          ["git", "branch", "-a"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      if proc.returncode == 0 and proc.stdout:
        for line in proc.stdout.splitlines():
          for m in _BRANCH_LOCAL_RE.finditer(line.strip()):
            nums.add(int(m.group(1)))
    except (subprocess.SubprocessError, OSError, ValueError):
      return nums
    return nums

  def _active_locked_local_nums(self) -> Set[int]:
    """Return integer N values from active WorkspaceMutex locks for this repository."""
    nums: Set[int] = set()
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)
    if not os.path.isdir(mutex.lock_dir):
      return nums
    repo_hash = mutex.get_repo_hash()
    prefix = f"{repo_hash}-"
    try:
      for fname in os.listdir(mutex.lock_dir):
        if not (fname.startswith(prefix) and fname.endswith(".lock")):
          continue
        lp = os.path.join(mutex.lock_dir, fname)
        if not os.path.isdir(lp):
          continue
        sess = mutex._read_session(lp)
        if sess and not mutex.is_session_active(sess):
          continue
        raw_tid = sess.get("ticket_id") or fname[len(prefix) : -len(".lock")]
        norm_tid = self.normalize_ticket_id(str(raw_tid))
        num = self._extract_local_num(norm_tid)
        if num is not None:
          nums.add(num)
    except OSError:
      return nums
    return nums

  def next_ticket_id(self) -> str:
    """Allocate the next collision-free LOCAL-<N> identifier across all sources."""
    max_n = 0

    for tid in self._completed_history_ids():
      num = self._extract_local_num(tid)
      if num is not None and num > max_n:
        max_n = num

    if os.path.isdir(self.tickets_dir):
      try:
        for fname in os.listdir(self.tickets_dir):
          if not fname.lower().endswith(".json"):
            continue
          stem = fname[:-5]
          norm_tid = self.normalize_ticket_id(stem)
          num = self._extract_local_num(norm_tid)
          if num is not None and num > max_n:
            max_n = num
      except OSError:
        max_n = max_n

    for num in self._active_locked_local_nums():
      if num > max_n:
        max_n = num

    for num in self._git_branch_local_nums():
      if num > max_n:
        max_n = num

    return f"LOCAL-{max_n + 1}"

  def create_ticket(
      self,
      title: str,
      body: str = "",
      priority: str = "",
      labels: Optional[List[str]] = None,
      blocked_by: Optional[List[str]] = None,
      ticket_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Create and persist a new local ticket JSON record."""
    clean_title = (title or "").strip()
    if not clean_title:
      raise ValueError("Ticket title must not be empty.")

    norm_priority = (priority or "").strip().upper()
    if norm_priority not in _VALID_PRIORITIES:
      raise ValueError(
          f"Invalid priority '{priority}'. Allowed values: P0, P1, P2, P3, or empty."
      )

    norm_id = (
        self.normalize_ticket_id(ticket_id)
        if (ticket_id and ticket_id.strip())
        else self.next_ticket_id()
    )

    norm_labels: List[str] = []
    for raw_label in labels or []:
      clean_label = str(raw_label).strip()
      if clean_label and clean_label not in norm_labels:
        norm_labels.append(clean_label)

    norm_blocked_by: List[str] = []
    for raw_blocker in blocked_by or []:
      clean_blocker = str(raw_blocker).strip()
      if clean_blocker:
        nb = self.normalize_ticket_id(clean_blocker)
        if nb not in norm_blocked_by:
          norm_blocked_by.append(nb)

    os.makedirs(self.tickets_dir, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    ticket_data: Dict[str, Any] = {
        "id": norm_id,
        "title": clean_title,
        "status": "OPEN",
        "priority": norm_priority,
        "labels": norm_labels,
        "blocked_by": norm_blocked_by,
        "body": body or "",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    path = self.ticket_path(norm_id)
    with open(path, "w", encoding="utf-8") as f:
      json.dump(ticket_data, f, indent=2)
      f.write("\n")
    return ticket_data

  def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
    """Read and return a local ticket record, or None if it does not exist."""
    path = self.ticket_path(ticket_id)
    if not os.path.isfile(path):
      return None
    try:
      with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
      if isinstance(data, dict):
        return data
    except (OSError, json.JSONDecodeError):
      return None
    return None

  def close_ticket(
      self,
      ticket_id: str,
      comment: Optional[str] = None,
  ) -> Optional[Dict[str, Any]]:
    """Mark a local ticket CLOSED, optionally record a resolution comment, and update updated_at.

    Args:
      ticket_id: Local ticket identifier (for example 'LOCAL-1' or '1').
      comment: Optional high-level resolution summary recorded at Target 90.

    Returns:
      Updated ticket dictionary if found, or None if the ticket file does not exist.
    """
    ticket = self.get_ticket(ticket_id)
    if ticket is None:
      return None
    norm_id = self.normalize_ticket_id(str(ticket.get("id") or ticket_id))
    ticket["id"] = norm_id
    ticket["status"] = "CLOSED"
    if comment is not None and str(comment).strip():
      ticket["resolution_comment"] = str(comment).strip()
    ticket["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = self.ticket_path(norm_id)
    with open(path, "w", encoding="utf-8") as f:
      json.dump(ticket, f, indent=2)
      f.write("\n")
    return ticket

  def is_ticket_open(self, ticket_id: str) -> bool:
    """Return True if the ticket exists with status OPEN and is not in history.json."""
    norm_id = self.normalize_ticket_id(ticket_id)
    if norm_id in self._completed_history_ids():
      return False
    ticket = self.get_ticket(norm_id)
    if ticket is None:
      return False
    return str(ticket.get("status", "")).upper() == "OPEN"

  def list_tickets(
      self,
      status: Optional[str] = "OPEN",
      include_active: bool = False,
  ) -> List[Dict[str, Any]]:
    """List local tickets filtered by status and active session/branch state."""
    if not os.path.isdir(self.tickets_dir):
      return []

    completed_ids = self._completed_history_ids()
    active_lock_nums = set() if include_active else self._active_locked_local_nums()
    active_branch_nums = set() if include_active else self._git_branch_local_nums()
    filter_status = (status or "ALL").strip().upper()

    results: List[Dict[str, Any]] = []
    try:
      fnames = sorted(os.listdir(self.tickets_dir))
    except OSError:
      return []

    for fname in fnames:
      if not fname.lower().endswith(".json"):
        continue
      fpath = os.path.join(self.tickets_dir, fname)
      try:
        with open(fpath, "r", encoding="utf-8") as f:
          ticket = json.load(f)
      except (OSError, json.JSONDecodeError):
        continue
      if not isinstance(ticket, dict):
        continue

      tid = self.normalize_ticket_id(str(ticket.get("id") or fname[:-5]))
      ticket["id"] = tid
      eff_status = str(ticket.get("status", "OPEN")).upper()
      if tid in completed_ids:
        eff_status = "CLOSED"

      if filter_status != "ALL" and eff_status != filter_status:
        continue

      if not include_active:
        num = self._extract_local_num(tid)
        if num is not None and (num in active_lock_nums or num in active_branch_nums):
          continue

      results.append(ticket)

    def _sort_key(item: Dict[str, Any]) -> Tuple[int, int, str]:
      prio = str(item.get("priority") or "").strip().upper()
      rank = _PRIORITY_RANK.get(prio, 4)
      tid = str(item.get("id") or "")
      num = self._extract_local_num(tid)
      return (rank, num if num is not None else 999999, tid)

    results.sort(key=_sort_key)
    return results

  def check_local_dependencies(self, ticket_id: str) -> Dict[str, Any]:
    """Check if a local ticket is blocked by any open upstream tickets."""
    ticket = self.get_ticket(ticket_id)
    if ticket is None:
      return {
          "blocked": False,
          "blocking_count": 0,
          "open_blockers": [],
      }

    open_blockers: List[Dict[str, Any]] = []
    for raw_upstream in ticket.get("blocked_by", []) or []:
      upstream_id = self.normalize_ticket_id(str(raw_upstream))
      if self.is_ticket_open(upstream_id):
        upstream_ticket = self.get_ticket(upstream_id) or {}
        open_blockers.append(
            {
                "number": upstream_id,
                "title": str(upstream_ticket.get("title") or upstream_id),
                "state": "OPEN",
            }
        )

    return {
        "blocked": len(open_blockers) > 0,
        "blocking_count": len(open_blockers),
        "open_blockers": open_blockers,
    }


class LocalTicketScript(BaseScript):
  """BaseScript CLI runner for local ticket management."""

  def __init__(self) -> None:
    super().__init__()
    self.repo_root: str = safe_getcwd("") or ""
    self.lock_dir: Optional[str] = None
    self.as_json: bool = False
    self.subcommand: str = ""
    self.title: str = ""
    self.body: str = ""
    self.priority: str = ""
    self.labels: List[str] = []
    self.blocked_by: List[str] = []
    self.ticket_id: Optional[str] = None
    self.ticket: str = ""
    self.status: str = "OPEN"
    self.include_active: bool = False
    self.comment: Optional[str] = None
    self.comment_file: Optional[str] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="local_tickets.py",
        description="Directory-per-ticket local backlog and dependency manager (GH-124).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/local_tickets.py next-id --json\n"
            "  python3 scripts/local_tickets.py create --title 'Add parser' --priority P1\n"
            "  python3 scripts/local_tickets.py list --json\n"
            "  python3 scripts/local_tickets.py view --ticket LOCAL-1\n"
            "  python3 scripts/local_tickets.py close --ticket LOCAL-1"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root directory.",
    )
    parser.add_argument(
        "--lock-dir",
        default=None,
        help="Directory for workspace mutual exclusion locks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit output as JSON.",
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        parser_class=SilentArgumentParser,
        help="Local ticket subcommand to execute.",
    )

    p_next = subparsers.add_parser(
        "next-id",
        help="Allocate the next collision-free LOCAL-<N> ticket ID.",
        description="Scan history.json, .workflow/tickets/, active locks, and Git branches for the next LOCAL-<N> ID.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p_create = subparsers.add_parser(
        "create",
        help="Create a new local ticket in .workflow/tickets/LOCAL-<N>.json.",
        description="Create a new local ticket file with priority, labels, and optional blocking dependencies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_create.add_argument("--title", required=True, help="Ticket title.")
    p_create.add_argument("--body", default="", help="Ticket body or description.")
    p_create.add_argument(
        "--priority",
        default="",
        help="Optional priority label: P0, P1, P2, or P3.",
    )
    p_create.add_argument(
        "--label",
        action="append",
        default=[],
        dest="label_list",
        help="Label to attach (may be specified multiple times).",
    )
    p_create.add_argument(
        "--labels",
        default="",
        help="Comma-separated list of labels to attach.",
    )
    p_create.add_argument(
        "--blocked-by",
        action="append",
        default=[],
        dest="blocked_by_list",
        help="Upstream local ticket ID that blocks this ticket (repeatable or comma-separated).",
    )
    p_create.add_argument(
        "--id",
        default=None,
        dest="ticket_id",
        help="Optional explicit ticket ID (defaults to next-id).",
    )

    p_list = subparsers.add_parser(
        "list",
        help="List local tickets sorted by priority (P0..P3).",
        description="List local tickets filtered by status and active workspace locks or branches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_list.add_argument(
        "--status",
        default="OPEN",
        help="Filter by ticket status: OPEN, CLOSED, or ALL (default OPEN).",
    )
    p_list.add_argument(
        "--all",
        action="store_true",
        dest="include_active",
        help="Include tickets with active workspace locks or Git branches.",
    )

    p_view = subparsers.add_parser(
        "view",
        help="View details of a local ticket.",
        description="Read and display a local ticket from .workflow/tickets/<ID>.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_view.add_argument("--ticket", required=True, help="Ticket ID to view.")

    p_close = subparsers.add_parser(
        "close",
        help="Mark a local ticket CLOSED.",
        description="Update status to CLOSED and optionally record a resolution comment in .workflow/tickets/<ID>.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_close.add_argument("--ticket", required=True, help="Ticket ID to close.")
    p_close.add_argument(
        "--comment",
        default=None,
        help="Optional resolution summary comment.",
    )
    p_close.add_argument(
        "--comment-file",
        default=None,
        dest="comment_file",
        help="Optional path to file containing resolution summary comment.",
    )

    for subparser, json_help in (
        (p_next, "Emit next ticket ID as JSON."),
        (p_create, "Emit created ticket as JSON."),
        (p_list, "Emit ticket list as JSON."),
        (p_view, "Emit ticket details as JSON."),
        (p_close, "Emit closed ticket as JSON."),
    ):
      subparser.add_argument(
          "--repo-root",
          default=None,
          dest="sub_repo_root",
          help="Repository root directory.",
      )
      subparser.add_argument(
          "--lock-dir",
          default=None,
          dest="sub_lock_dir",
          help="Workspace lock directory.",
      )
      subparser.add_argument(
          "--json",
          action="store_true",
          dest="sub_json",
          help=json_help,
      )

    ns = parser.parse_args(self.raw_args)
    if not ns.subcommand:
      parser.error("Specify a subcommand: next-id, create, list, view, or close.")

    self.subcommand = ns.subcommand
    self.repo_root = safe_abspath(
        getattr(ns, "sub_repo_root", None)
        or getattr(ns, "repo_root", None)
        or safe_getcwd()
    )
    self.lock_dir = (
        getattr(ns, "sub_lock_dir", None)
        or getattr(ns, "lock_dir", None)
    )
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
    self.title = getattr(ns, "title", "")
    self.body = getattr(ns, "body", "")
    self.priority = getattr(ns, "priority", "")
    self.ticket_id = getattr(ns, "ticket_id", None)
    self.ticket = getattr(ns, "ticket", "")
    self.status = getattr(ns, "status", "OPEN")
    self.include_active = bool(getattr(ns, "include_active", False))
    self.comment = getattr(ns, "comment", None)
    self.comment_file = getattr(ns, "comment_file", None)

    combined_labels: List[str] = []
    for item in getattr(ns, "label_list", []) or []:
      for part in str(item).split(","):
        if part.strip():
          combined_labels.append(part.strip())
    raw_csv_labels = getattr(ns, "labels", "") or ""
    for part in str(raw_csv_labels).split(","):
      if part.strip():
        combined_labels.append(part.strip())
    self.labels = combined_labels

    combined_blockers: List[str] = []
    for item in getattr(ns, "blocked_by_list", []) or []:
      for part in str(item).split(","):
        if part.strip():
          combined_blockers.append(part.strip())
    self.blocked_by = combined_blockers

  def run(self) -> int:
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)

    if self.subcommand == "next-id":
      tid = store.next_ticket_id()
      if self.as_json:
        self.stdout.write(json.dumps({"ticket_id": tid}, indent=2) + "\n")
      else:
        self.stdout.write(f"{tid}\n")
      return 0

    if self.subcommand == "create":
      ticket = store.create_ticket(
          title=self.title,
          body=self.body,
          priority=self.priority,
          labels=self.labels,
          blocked_by=self.blocked_by,
          ticket_id=self.ticket_id,
      )
      if self.as_json:
        self.stdout.write(json.dumps(ticket, indent=2) + "\n")
      else:
        self.stdout.write(f"Created {ticket['id']}: {ticket['title']}\n")
      return 0

    if self.subcommand == "list":
      tickets = store.list_tickets(
          status=self.status,
          include_active=self.include_active,
      )
      if self.as_json:
        self.stdout.write(json.dumps(tickets, indent=2) + "\n")
      else:
        if not tickets:
          self.stdout.write("No matching local tickets.\n")
        else:
          for t in tickets:
            prio = t.get("priority") or "-"
            self.stdout.write(
                f"{t['id']}\t{prio}\t{t.get('status', 'OPEN')}\t{t.get('title', '')}\n"
            )
      return 0

    if self.subcommand == "view":
      ticket = store.get_ticket(self.ticket)
      if ticket is None:
        self.stderr.write(f"Ticket not found: {self.ticket}\n")
        return 1
      if self.as_json:
        self.stdout.write(json.dumps(ticket, indent=2) + "\n")
      else:
        self.stdout.write(
            f"ID: {ticket['id']}\n"
            f"Title: {ticket.get('title', '')}\n"
            f"Status: {ticket.get('status', '')}\n"
            f"Priority: {ticket.get('priority') or 'unlabeled'}\n"
        )
        if ticket.get("resolution_comment"):
          self.stdout.write(f"Resolution: {ticket['resolution_comment']}\n")
      return 0

    if self.subcommand == "close":
      comment_text = self.comment
      if self.comment_file:
        with open(self.comment_file, "r", encoding="utf-8") as f:
          comment_text = f.read()
      ticket = store.close_ticket(self.ticket, comment=comment_text)
      if ticket is None:
        self.stderr.write(f"Ticket not found: {self.ticket}\n")
        return 1
      if self.as_json:
        self.stdout.write(json.dumps(ticket, indent=2) + "\n")
      else:
        self.stdout.write(f"Closed {ticket['id']}: {ticket.get('title', '')}\n")
      return 0

    return 1
