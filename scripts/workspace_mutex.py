#!/usr/bin/env python3
"""Multi-Workspace Ticket Mutual Exclusion and Collision Detection (GH-100, SCAR-SYS-08).

Provides atomic directory locking across multiple checkouts of the same repository,
local session registry tracking, stale process ID recovery, and remote branch/issue
conflict checks.
"""

import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

from base_script import BaseScript
from emit_reflex import SilentArgumentParser
from signal_trap import SignalTrap

logger = logging.getLogger(__name__)

_DEFAULT_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
_DEFAULT_LOCK_DIR = os.path.expanduser("~/.workflow/locks")


class WorkspaceMutex:
  """Mutual exclusion and collision guard for multi-workspace Makeflow sessions."""

  def __init__(
      self,
      repo_root: Optional[str] = None,
      lock_dir: Optional[str] = None,
  ) -> None:
    self.repo_root: str = os.path.abspath(repo_root or os.getcwd())
    raw_lock_dir = (
        lock_dir
        or os.environ.get("WORKFLOW_LOCK_DIR")
        or _DEFAULT_LOCK_DIR
    )
    self.lock_dir: str = os.path.abspath(os.path.expanduser(raw_lock_dir))

  def get_repo_hash(self) -> str:
    """Compute repository identity hash via initial git commit or repo path SHA-256."""
    try:
      proc = subprocess.run(
          ["git", "rev-list", "--max-parents=0", "HEAD"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().splitlines()[0]
    except (subprocess.SubprocessError, OSError):
      pass

    try:
      proc = subprocess.run(
          ["git", "rev-parse", "HEAD"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError):
      pass

    return hashlib.sha256(self.repo_root.encode("utf-8")).hexdigest()

  def lock_path(self, ticket_id: str) -> str:
    """Return the absolute path to the atomic directory lock."""
    clean_ticket = ticket_id.strip()
    repo_hash = self.get_repo_hash()
    return os.path.join(self.lock_dir, f"{repo_hash}-{clean_ticket}.lock")

  def is_pid_alive(self, pid: int) -> bool:
    """Check if process ID is actively running using POSIX signal 0."""
    if pid <= 0:
      return False
    try:
      os.kill(pid, 0)
      return True
    except ProcessLookupError:
      return False
    except PermissionError:
      return True
    except OSError as exc:
      if exc.errno == errno.ESRCH:
        return False
      if exc.errno == errno.EPERM:
        return True
      return False

  def _read_session(self, lock_dir_path: str) -> Dict[str, Any]:
    session_file = os.path.join(lock_dir_path, "session.json")
    if os.path.isfile(session_file):
      try:
        with open(session_file, "r", encoding="utf-8") as f:
          return json.load(f)
      except (OSError, json.JSONDecodeError):
        pass
    return {}

  def _clean_lock_dir(self, lock_dir_path: str) -> None:
    """Remove contents of a lock directory and the directory itself."""
    try:
      if os.path.isdir(lock_dir_path):
        for fname in os.listdir(lock_dir_path):
          try:
            os.unlink(os.path.join(lock_dir_path, fname))
          except OSError:
            pass
        os.rmdir(lock_dir_path)
    except (FileNotFoundError, OSError):
      pass

  def is_session_active(self, session_data: Dict[str, Any]) -> bool:
    """Determine if a recorded workspace session is actively working or stale."""
    pid = session_data.get("pid")
    if pid is not None and self.is_pid_alive(int(pid)):
      return True

    ws = session_data.get("workspace_path")
    if not ws or not os.path.isdir(ws):
      return False

    ticket_card = os.path.join(ws, ".workflow", "state", "10_ticket.md")
    if not os.path.isfile(ticket_card):
      return False

    ticket_id = str(session_data.get("ticket_id", "")).strip()
    clean_num = ""
    m = re.search(r"\d+", ticket_id)
    if m:
      clean_num = m.group(0)

    try:
      with open(ticket_card, "r", encoding="utf-8", errors="replace") as f:
        ticket_content = f.read()

      matched = False
      if ticket_id and ticket_id in ticket_content:
        matched = True
      elif clean_num and f"GH-{clean_num}" in ticket_content:
        matched = True
      elif clean_num and f"LOCAL-{clean_num}" in ticket_content:
        matched = True

      if not matched:
        return False

      next_action = os.path.join(ws, ".workflow", "state", "99_next_action.md")
      if os.path.isfile(next_action):
        with open(next_action, "r", encoding="utf-8", errors="replace") as f:
          na_content = f.read()
        if "COMPLETE" in na_content:
          return False

      return True
    except OSError:
      return False

  def acquire(
      self,
      ticket_id: str,
      workspace_path: Optional[str] = None,
      branch_name: Optional[str] = None,
      pid: Optional[int] = None,
  ) -> Dict[str, Any]:
    """Acquire atomic directory lock for ticket.

    Returns dict with 'acquired' bool and session or collision details.
    """
    os.makedirs(self.lock_dir, exist_ok=True)
    lp = self.lock_path(ticket_id)
    ws = os.path.abspath(workspace_path or self.repo_root)
    p = os.getpid() if pid is None else int(pid)

    current_branch = branch_name
    if current_branch is None:
      try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ws,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0:
          current_branch = proc.stdout.strip()
      except (subprocess.SubprocessError, OSError):
        current_branch = ""

    for _ in range(5):
      try:
        os.mkdir(lp)
        # Lock acquired successfully
        session_data = {
            "repo_root": self.repo_root,
            "repo_hash": self.get_repo_hash(),
            "ticket_id": ticket_id,
            "workspace_path": ws,
            "branch_name": current_branch or "",
            "pid": p,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        session_file = os.path.join(lp, "session.json")
        try:
          with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)
          return {
              "acquired": True,
              "lock_path": lp,
              "session": session_data,
          }
        except (FileNotFoundError, OSError):
          continue
      except FileExistsError:
        session_data = {}
        for _ in range(3):
          session_data = self._read_session(lp)
          if session_data:
            break
          time.sleep(0.05)

        if session_data:
          existing_ws = session_data.get("workspace_path")
          if existing_ws and os.path.abspath(existing_ws) == ws:
            return {
                "acquired": True,
                "reentrant": True,
                "lock_path": lp,
                "session": session_data,
            }
          if self.is_session_active(session_data):
            return {
                "acquired": False,
                "reason": "COLLISION",
                "lock_path": lp,
                "session": session_data,
            }

        # Stale lock from dead process or uninitialized directory
        self._clean_lock_dir(lp)

    return {
        "acquired": False,
        "reason": "ACQUIRE_TIMEOUT",
        "lock_path": lp,
        "session": self._read_session(lp),
    }

  def release(self, ticket_id: str) -> bool:
    """Release atomic directory lock and remove session metadata."""
    lp = self.lock_path(ticket_id)
    if not os.path.exists(lp):
      return False
    self._clean_lock_dir(lp)
    return not os.path.exists(lp)

  def release_workspace(self, workspace_path: Optional[str] = None) -> List[str]:
    """Release all locks owned by the given workspace path."""
    ws = os.path.abspath(workspace_path or self.repo_root)
    released = []
    if not os.path.isdir(self.lock_dir):
      return released
    for fname in os.listdir(self.lock_dir):
      if fname.endswith(".lock"):
        lp = os.path.join(self.lock_dir, fname)
        if os.path.isdir(lp):
          sess = self._read_session(lp)
          sess_ws = sess.get("workspace_path")
          if sess_ws and os.path.abspath(sess_ws) == ws:
            ticket = sess.get("ticket_id", "")
            self._clean_lock_dir(lp)
            released.append(ticket or fname)
    return released

  def register_signal_trap(self, ticket_id: str) -> None:
    """Register lock directory with SignalTrap for cleanup on termination."""
    lp = self.lock_path(ticket_id)
    trap = SignalTrap.get_instance()
    trap.register_lock_dir(lp)
    trap.register_cleanup(self.release, ticket_id)

  def check_remote(
      self, ticket_id: str, hosting_mode: str = "GITHUB"
  ) -> Dict[str, Any]:
    """Check remote repository or tracker for concurrent work."""
    mode = hosting_mode.upper()
    if mode == "GITHUB":
      m = re.search(r"\d+", ticket_id)
      issue_num = m.group(0) if m else ticket_id
      try:
        proc = subprocess.run(
            ["gh", "issue", "view", issue_num, "--json", "assignees,labels"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode == 0:
          data = json.loads(proc.stdout)
          raw_assignees = data.get("assignees", [])
          assignees = [
              a.get("login") for a in raw_assignees if isinstance(a, dict) and a.get("login")
          ]
          raw_labels = data.get("labels", [])
          labels = [
              l.get("name") for l in raw_labels if isinstance(l, dict) and l.get("name")
          ]
          has_in_progress = any(
              "in-progress" in l.lower() or "in progress" in l.lower()
              for l in labels
          )
          if assignees or has_in_progress:
            return {
                "conflict": True,
                "hosting_mode": "GITHUB",
                "reason": "REMOTE_ASSIGNED_OR_IN_PROGRESS",
                "assignees": assignees,
                "labels": labels,
            }
          return {
              "conflict": False,
              "hosting_mode": "GITHUB",
              "assignees": assignees,
              "labels": labels,
          }
        return {
            "conflict": False,
            "hosting_mode": "GITHUB",
            "error": (proc.stderr or "").strip(),
        }
      except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        return {
            "conflict": False,
            "hosting_mode": "GITHUB",
            "error": str(exc),
        }

    if mode == "PURE_GIT":
      try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", f"*/{ticket_id}-*"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        refs = [
            ln.strip() for ln in proc.stdout.splitlines() if ln.strip()
        ]
        if refs:
          return {
              "conflict": True,
              "hosting_mode": "PURE_GIT",
              "reason": "REMOTE_BRANCH_EXISTS",
              "refs": refs,
          }
        return {
            "conflict": False,
            "hosting_mode": "PURE_GIT",
            "refs": [],
        }
      except (subprocess.SubprocessError, OSError) as exc:
        return {
            "conflict": False,
            "hosting_mode": "PURE_GIT",
            "error": str(exc),
        }

    return {"conflict": False, "hosting_mode": mode}

  def check_dependencies(
      self, ticket_id: str, hosting_mode: str = "GITHUB"
  ) -> Dict[str, Any]:
    """Check if a ticket is blocked by open upstream issues."""
    mode = hosting_mode.upper()
    if mode != "GITHUB":
      return {
          "blocked": False,
          "blocking_count": 0,
          "open_blockers": [],
          "hosting_mode": hosting_mode,
      }

    m = re.search(r"\d+", ticket_id)
    if not m:
      return {
          "blocked": False,
          "blocking_count": 0,
          "open_blockers": [],
          "hosting_mode": hosting_mode,
      }

    issue_num = int(m.group(0))
    query = """
query($owner: String!, $repo: String!, $issue: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $issue) {
      issueDependenciesSummary {
        totalBlockedBy
      }
      blockedBy(first: 20) {
        totalCount
        nodes {
          number
          title
          state
        }
      }
    }
  }
}
"""
    try:
      proc = subprocess.run(
          [
              "gh",
              "api",
              "graphql",
              "-F",
              "owner=:owner",
              "-F",
              "repo=:repo",
              "-F",
              f"issue={issue_num}",
              "-f",
              f"query={query}",
          ],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
          timeout=10,
      )
      if proc.returncode == 0:
        data = json.loads(proc.stdout)
        issue_data = (
            data.get("data", {}).get("repository", {}).get("issue") or {}
        )
        blocked_by = issue_data.get("blockedBy", {})
        nodes = blocked_by.get("nodes", []) if isinstance(blocked_by, dict) else []
        open_blockers = [
            n for n in nodes if isinstance(n, dict) and n.get("state") == "OPEN"
        ]
        return {
            "blocked": len(open_blockers) > 0,
            "blocking_count": len(open_blockers),
            "open_blockers": open_blockers,
            "hosting_mode": "GITHUB",
        }
      return {
          "blocked": False,
          "blocking_count": 0,
          "open_blockers": [],
          "hosting_mode": "GITHUB",
          "error": (proc.stderr or "").strip(),
      }
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
      return {
          "blocked": False,
          "blocking_count": 0,
          "open_blockers": [],
          "hosting_mode": "GITHUB",
          "error": str(exc),
      }

  def claim_issue(self, ticket_id: str) -> Dict[str, Any]:
    """Claim a GitHub issue by assigning @me and adding in-progress label."""
    m = re.search(r"\d+", ticket_id)
    issue_num = m.group(0) if m else ticket_id
    try:
      proc = subprocess.run(
          [
              "gh",
              "issue",
              "edit",
              issue_num,
              "--add-assignee",
              "@me",
              "--add-label",
              "in-progress",
          ],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          check=False,
          timeout=10,
      )
      if proc.returncode == 0:
        return {"claimed": True, "ticket_id": ticket_id, "issue_num": issue_num}
      return {
          "claimed": False,
          "ticket_id": ticket_id,
          "issue_num": issue_num,
          "error": (proc.stderr or "").strip(),
      }
    except (subprocess.SubprocessError, OSError) as exc:
      return {
          "claimed": False,
          "ticket_id": ticket_id,
          "issue_num": issue_num,
          "error": str(exc),
      }


class WorkspaceMutexScript(BaseScript):
  """BaseScript lifecycle runner for WorkspaceMutex CLI commands."""

  def __init__(self) -> None:
    super().__init__()
    self.repo_root: str = _DEFAULT_REPO_ROOT
    self.lock_dir: Optional[str] = None
    self.ticket: str = ""
    self.branch: Optional[str] = None
    self.pid: Optional[int] = None
    self.mode: str = "GITHUB"
    self.as_json: bool = False
    self.subcommand: str = ""

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(prog="workspace_mutex.py", add_help=False)
    parser.add_argument("--repo-root", default=_DEFAULT_REPO_ROOT)
    parser.add_argument("--lock-dir", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")

    subparsers = parser.add_subparsers(
        dest="subcommand", parser_class=SilentArgumentParser
    )

    p_acq = subparsers.add_parser("acquire", add_help=False)
    p_acq.add_argument("--ticket", required=True)
    p_acq.add_argument("--branch", default=None)
    p_acq.add_argument("--pid", type=int, default=None)
    p_acq.add_argument("--repo-root", default=None)
    p_acq.add_argument("--lock-dir", default=None)
    p_acq.add_argument("--json", action="store_true", dest="sub_json")

    p_rel = subparsers.add_parser("release", add_help=False)
    p_rel.add_argument("--ticket", required=True)
    p_rel.add_argument("--repo-root", default=None)
    p_rel.add_argument("--lock-dir", default=None)
    p_rel.add_argument("--json", action="store_true", dest="sub_json")

    p_stat = subparsers.add_parser("status", add_help=False)
    p_stat.add_argument("--ticket", required=True)
    p_stat.add_argument("--repo-root", default=None)
    p_stat.add_argument("--lock-dir", default=None)
    p_stat.add_argument("--json", action="store_true", dest="sub_json")

    p_rem = subparsers.add_parser("check-remote", add_help=False)
    p_rem.add_argument("--ticket", required=True)
    p_rem.add_argument("--mode", default="GITHUB")
    p_rem.add_argument("--repo-root", default=None)
    p_rem.add_argument("--json", action="store_true", dest="sub_json")

    p_dep = subparsers.add_parser("check-dependencies", add_help=False)
    p_dep.add_argument("--ticket", required=True)
    p_dep.add_argument("--mode", default="GITHUB")
    p_dep.add_argument("--repo-root", default=None)
    p_dep.add_argument("--json", action="store_true", dest="sub_json")

    p_claim = subparsers.add_parser("claim-issue", add_help=False)
    p_claim.add_argument("--ticket", required=True)
    p_claim.add_argument("--repo-root", default=None)
    p_claim.add_argument("--json", action="store_true", dest="sub_json")

    ns = parser.parse_args(self.raw_args)
    if not ns.subcommand:
      parser.error(
          "Specify a subcommand: acquire, release, status, check-remote, check-dependencies, or claim-issue."
      )

    self.subcommand = ns.subcommand
    self.repo_root = os.path.abspath(ns.repo_root or _DEFAULT_REPO_ROOT)
    self.lock_dir = getattr(ns, "lock_dir", None)
    self.ticket = getattr(ns, "ticket", "")
    self.branch = getattr(ns, "branch", None)
    self.pid = getattr(ns, "pid", None)
    self.mode = getattr(ns, "mode", "GITHUB")
    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))

  def run(self) -> int:
    mutex = WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir)

    if self.subcommand == "acquire":
      res = mutex.acquire(
          ticket_id=self.ticket,
          workspace_path=self.repo_root,
          branch_name=self.branch,
          pid=self.pid,
      )
      if self.as_json:
        self.stdout.write(json.dumps(res, indent=2) + "\n")
      else:
        if res.get("acquired"):
          self.stdout.write(f"Acquired lock: {res.get('lock_path')}\n")
        else:
          self.stderr.write(
              f"Failed to acquire lock: {res.get('reason')} "
              f"held by PID {res.get('session', {}).get('pid')} "
              f"in {res.get('session', {}).get('workspace_path')}\n"
          )
      return 0 if res.get("acquired") else 2

    if self.subcommand == "release":
      released = mutex.release(self.ticket)
      if self.as_json:
        self.stdout.write(json.dumps({"released": released, "ticket": self.ticket}, indent=2) + "\n")
      else:
        self.stdout.write(f"Released lock for ticket {self.ticket}: {released}\n")
      return 0

    if self.subcommand == "status":
      lp = mutex.lock_path(self.ticket)
      locked = os.path.isdir(lp)
      session_data = mutex._read_session(lp) if locked else {}
      alive = mutex.is_pid_alive(session_data.get("pid", 0)) if locked else False
      data = {
          "ticket": self.ticket,
          "locked": locked,
          "lock_path": lp,
          "alive": alive,
          "session": session_data,
      }
      if self.as_json:
        self.stdout.write(json.dumps(data, indent=2) + "\n")
      else:
        self.stdout.write(f"Ticket {self.ticket} locked={locked} alive={alive}\n")
      return 0

    if self.subcommand == "check-remote":
      res = mutex.check_remote(self.ticket, hosting_mode=self.mode)
      if self.as_json:
        self.stdout.write(json.dumps(res, indent=2) + "\n")
      else:
        self.stdout.write(f"Remote check conflict: {res.get('conflict')}\n")
      return 2 if res.get("conflict") else 0

    if self.subcommand == "check-dependencies":
      res = mutex.check_dependencies(self.ticket, hosting_mode=self.mode)
      if self.as_json:
        self.stdout.write(json.dumps(res, indent=2) + "\n")
      else:
        self.stdout.write(f"Dependencies check blocked: {res.get('blocked')}\n")
      return 2 if res.get("blocked") else 0

    if self.subcommand == "claim-issue":
      res = mutex.claim_issue(self.ticket)
      if self.as_json:
        self.stdout.write(json.dumps(res, indent=2) + "\n")
      else:
        self.stdout.write(f"Claim issue {self.ticket}: {res.get('claimed')}\n")
      return 0 if res.get("claimed") else 1

    return 1


if __name__ == "__main__":
  sys.exit(WorkspaceMutexScript().execute())
