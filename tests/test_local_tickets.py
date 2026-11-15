#!/usr/bin/env python3
"""Hermetic unit and CLI boundary tests for Local Ticket Management (GH-124).

Tests collision-free next-ID allocation across history.json, .workflow/tickets/,
active WorkspaceMutex locks, and Git branches; CRUD operations; priority sorting;
and local dependency blocker detection at Target 10 intake.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

from lib.local_tickets import LocalTicketScript, LocalTicketStore
from lib.signal_trap import SignalTrap
from lib.workflow_gates import WorkflowGatesScript
from lib.workspace_mutex import WorkspaceMutex


class TestLocalTickets(unittest.TestCase):
  """Test suite for LocalTicketStore and LocalTicketScript."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.repo_root = os.path.join(self.temp_dir.name, "active_repo")
    self.other_root = os.path.join(self.temp_dir.name, "other_repo")
    self.lock_dir = os.path.join(self.temp_dir.name, "locks")
    os.makedirs(self.repo_root, exist_ok=True)
    os.makedirs(self.lock_dir, exist_ok=True)

    subprocess.run(["git", "init"], cwd=self.repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=self.repo_root,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=self.repo_root,
        capture_output=True,
        check=True,
    )
    dummy_file = os.path.join(self.repo_root, "init.txt")
    with open(dummy_file, "w", encoding="utf-8") as f:
      f.write("initial commit\n")
    subprocess.run(["git", "add", "init.txt"], cwd=self.repo_root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=self.repo_root, capture_output=True, check=True
    )

    subprocess.run(
        ["git", "clone", self.repo_root, self.other_root],
        capture_output=True,
        check=True,
    )

    exclude_file = os.path.join(self.repo_root, ".git", "info", "exclude")
    with open(exclude_file, "a", encoding="utf-8") as f:
      f.write(".workflow/state/\n.workflow/archive/\n.workflow/tickets/\n")

    if hasattr(SignalTrap, "reset_instance"):
      SignalTrap.reset_instance()

  def tearDown(self):
    if hasattr(SignalTrap, "reset_instance"):
      SignalTrap.reset_instance()
    self.temp_dir.cleanup()

  def test_next_ticket_id_empty_repo(self):
    """TestCase 1: next_ticket_id returns LOCAL-1 in an empty repository."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    self.assertEqual(store.next_ticket_id(), "LOCAL-1")

  def test_next_ticket_id_across_history_tickets_locks_and_branches(self):
    """TestCase 2: next_ticket_id avoids collisions across history, tickets, locks, and branches."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)

    # 1. From .workflow/history.json
    wf_dir = os.path.join(self.repo_root, ".workflow")
    os.makedirs(wf_dir, exist_ok=True)
    with open(os.path.join(wf_dir, "history.json"), "w", encoding="utf-8") as f:
      json.dump([{"id": "GH-50"}, {"id": "LOCAL-3"}], f)
    self.assertEqual(store.next_ticket_id(), "LOCAL-4")

    # 2. From .workflow/tickets/LOCAL-7.json
    store.create_ticket(title="Ticket seven", ticket_id="LOCAL-7")
    self.assertEqual(store.next_ticket_id(), "LOCAL-8")

    # 3. From active WorkspaceMutex lock in another clone
    other_mutex = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    other_mutex.acquire(
        ticket_id="LOCAL-12",
        workspace_path=self.other_root,
        branch_name="feat/local-12-active",
        pid=os.getpid(),
    )
    self.assertEqual(store.next_ticket_id(), "LOCAL-13")

    # 4. From Git branch feat/local-20-branch
    subprocess.run(
        ["git", "branch", "feat/local-20-branch"],
        cwd=self.repo_root,
        capture_output=True,
        check=True,
    )
    self.assertEqual(store.next_ticket_id(), "LOCAL-21")

  def test_create_view_and_close_ticket(self):
    """TestCase 3: create_ticket, get_ticket, is_ticket_open, and close_ticket lifecycle."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    created = store.create_ticket(
        title="Add local queue",
        body="Detailed specification",
        priority="p1",
        labels=["core", "offline"],
        blocked_by=["15", "local-14"],
    )
    self.assertEqual(created["id"], "LOCAL-1")
    self.assertEqual(created["title"], "Add local queue")
    self.assertEqual(created["status"], "OPEN")
    self.assertEqual(created["priority"], "P1")
    self.assertEqual(created["labels"], ["core", "offline"])
    self.assertEqual(created["blocked_by"], ["LOCAL-15", "LOCAL-14"])
    self.assertTrue(os.path.isfile(store.ticket_path("LOCAL-1")))

    fetched = store.get_ticket("local-1")
    self.assertIsNotNone(fetched)
    self.assertEqual(fetched["id"], "LOCAL-1")
    self.assertTrue(store.is_ticket_open("LOCAL-1"))

    closed = store.close_ticket("1")
    self.assertIsNotNone(closed)
    self.assertEqual(closed["status"], "CLOSED")
    self.assertFalse(store.is_ticket_open("LOCAL-1"))

    self.assertIsNone(store.get_ticket("LOCAL-999"))
    self.assertIsNone(store.close_ticket("LOCAL-999"))

    with self.assertRaises(ValueError):
      store.create_ticket(title="   ")
    with self.assertRaises(ValueError):
      store.create_ticket(title="Bad priority", priority="P9")

  def test_is_ticket_open_respects_history_json(self):
    """TestCase 4: Tickets recorded in .workflow/history.json are treated as closed."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    store.create_ticket(title="Legacy open file", ticket_id="LOCAL-2")
    self.assertTrue(store.is_ticket_open("LOCAL-2"))

    wf_dir = os.path.join(self.repo_root, ".workflow")
    os.makedirs(wf_dir, exist_ok=True)
    with open(os.path.join(wf_dir, "history.json"), "w", encoding="utf-8") as f:
      json.dump([{"id": "LOCAL-2"}], f)

    self.assertFalse(store.is_ticket_open("LOCAL-2"))

  def test_list_tickets_priority_sorting_and_active_filtering(self):
    """TestCase 5: list_tickets sorts by P0..P3 priority and filters active locks and branches."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    store.create_ticket(title="Unlabeled task", priority="", ticket_id="LOCAL-1")
    store.create_ticket(title="Medium task", priority="P2", ticket_id="LOCAL-2")
    store.create_ticket(title="Critical task", priority="P0", ticket_id="LOCAL-3")
    store.create_ticket(title="High task", priority="P1", ticket_id="LOCAL-4")
    store.create_ticket(title="Low task", priority="P3", ticket_id="LOCAL-5")

    ordered = [t["id"] for t in store.list_tickets()]
    self.assertEqual(ordered, ["LOCAL-3", "LOCAL-4", "LOCAL-2", "LOCAL-5", "LOCAL-1"])

    # Lock LOCAL-3 in other_root
    other_mutex = WorkspaceMutex(repo_root=self.other_root, lock_dir=self.lock_dir)
    other_mutex.acquire(
        ticket_id="LOCAL-3",
        workspace_path=self.other_root,
        branch_name="feat/local-3-work",
        pid=os.getpid(),
    )
    filtered_lock = [t["id"] for t in store.list_tickets(include_active=False)]
    self.assertEqual(filtered_lock, ["LOCAL-4", "LOCAL-2", "LOCAL-5", "LOCAL-1"])

    # Create local branch for LOCAL-4
    subprocess.run(
        ["git", "branch", "fix/local-4-bugfix"],
        cwd=self.repo_root,
        capture_output=True,
        check=True,
    )
    filtered_branch = [t["id"] for t in store.list_tickets(include_active=False)]
    self.assertEqual(filtered_branch, ["LOCAL-2", "LOCAL-5", "LOCAL-1"])

    # include_active=True includes locked and branched tickets
    all_open = [t["id"] for t in store.list_tickets(include_active=True)]
    self.assertEqual(all_open, ["LOCAL-3", "LOCAL-4", "LOCAL-2", "LOCAL-5", "LOCAL-1"])

    # Close LOCAL-2 and check status filter
    store.close_ticket("LOCAL-2")
    closed_only = [t["id"] for t in store.list_tickets(status="CLOSED", include_active=True)]
    self.assertEqual(closed_only, ["LOCAL-2"])

  def test_check_local_dependencies_and_intake_gate_integration(self):
    """TestCase 6: check_local_dependencies blocks intake until upstream local ticket closes."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    store.create_ticket(title="Upstream schema", ticket_id="LOCAL-15")
    store.create_ticket(
        title="Downstream CLI",
        blocked_by=["LOCAL-15"],
        ticket_id="LOCAL-16",
    )

    dep = store.check_local_dependencies("LOCAL-16")
    self.assertTrue(dep["blocked"])
    self.assertEqual(dep["blocking_count"], 1)
    self.assertEqual(dep["open_blockers"][0]["number"], "LOCAL-15")
    self.assertEqual(dep["open_blockers"][0]["title"], "Upstream schema")

    # Intake without --allow-blocked fails with exit code 2
    err_stream = io.StringIO()
    code_blocked = WorkflowGatesScript().execute(
        args=[
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "intake",
            "--ticket",
            "LOCAL-16",
        ],
        stdout=io.StringIO(),
        stderr=err_stream,
    )
    self.assertEqual(code_blocked, 2)
    self.assertIn("INTAKE_DEPENDENCY_BLOCKED", err_stream.getvalue())
    self.assertIn("LOCAL-15", err_stream.getvalue())
    self.assertIn("Upstream schema", err_stream.getvalue())

    # Intake with --allow-blocked passes
    code_allowed = WorkflowGatesScript().execute(
        args=[
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "intake",
            "--ticket",
            "LOCAL-16",
            "--allow-blocked",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(code_allowed, 0)
    WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir).release("LOCAL-16")

    # Closing upstream unblocks intake without --allow-blocked
    store.close_ticket("LOCAL-15")
    dep_after = store.check_local_dependencies("LOCAL-16")
    self.assertFalse(dep_after["blocked"])
    code_unblocked = WorkflowGatesScript().execute(
        args=[
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "intake",
            "--ticket",
            "LOCAL-16",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(code_unblocked, 0)
    WorkspaceMutex(repo_root=self.repo_root, lock_dir=self.lock_dir).release("LOCAL-16")

  def test_cli_subcommands_and_help(self):
    """TestCase 7: LocalTicketScript CLI subcommands and --help contract."""
    script = LocalTicketScript()

    for sub in ([], ["next-id"], ["create"], ["list"], ["view"], ["close"]):
      out_h = io.StringIO()
      err_h = io.StringIO()
      code_h = script.execute(args=sub + ["--help"], stdout=out_h, stderr=err_h)
      self.assertEqual(code_h, 0)
      self.assertIn("usage:", out_h.getvalue().lower())

    # next-id
    out_next = io.StringIO()
    code_next = script.execute(
        args=[
            "next-id",
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "--json",
        ],
        stdout=out_next,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_next, 0)
    self.assertEqual(json.loads(out_next.getvalue())["ticket_id"], "LOCAL-1")

    # create
    out_create = io.StringIO()
    code_create = script.execute(
        args=[
            "create",
            "--title",
            "CLI Task",
            "--priority",
            "P1",
            "--label",
            "backend",
            "--labels",
            "api,auth",
            "--blocked-by",
            "LOCAL-9",
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "--json",
        ],
        stdout=out_create,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_create, 0)
    created_data = json.loads(out_create.getvalue())
    self.assertEqual(created_data["id"], "LOCAL-1")
    self.assertEqual(created_data["labels"], ["backend", "api", "auth"])
    self.assertEqual(created_data["blocked_by"], ["LOCAL-9"])

    # list
    out_list = io.StringIO()
    code_list = script.execute(
        args=[
            "list",
            "--repo-root",
            self.repo_root,
            "--lock-dir",
            self.lock_dir,
            "--json",
        ],
        stdout=out_list,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_list, 0)
    listed = json.loads(out_list.getvalue())
    self.assertEqual(len(listed), 1)
    self.assertEqual(listed[0]["id"], "LOCAL-1")

    # view
    out_view = io.StringIO()
    code_view = script.execute(
        args=[
            "view",
            "--ticket",
            "LOCAL-1",
            "--repo-root",
            self.repo_root,
            "--json",
        ],
        stdout=out_view,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_view, 0)
    self.assertEqual(json.loads(out_view.getvalue())["title"], "CLI Task")

    # view missing
    code_view_missing = script.execute(
        args=["view", "--ticket", "LOCAL-404", "--repo-root", self.repo_root],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(code_view_missing, 1)

    # close
    out_close = io.StringIO()
    code_close = script.execute(
        args=[
            "close",
            "--ticket",
            "LOCAL-1",
            "--repo-root",
            self.repo_root,
            "--json",
        ],
        stdout=out_close,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_close, 0)
    self.assertEqual(json.loads(out_close.getvalue())["status"], "CLOSED")

    # close missing
    code_close_missing = script.execute(
        args=["close", "--ticket", "LOCAL-404", "--repo-root", self.repo_root],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(code_close_missing, 1)

  def test_close_ticket_with_resolution_comment_and_cli(self):
    """TestCase 8: close_ticket and CLI close persist resolution_comment via --comment and --comment-file."""
    store = LocalTicketStore(repo_root=self.repo_root, lock_dir=self.lock_dir)
    store.create_ticket(title="First task", ticket_id="LOCAL-1")
    closed_1 = store.close_ticket(
        "LOCAL-1",
        comment="Target 90: Workflow Complete\n\nSummary of delivered changes.",
    )
    self.assertIsNotNone(closed_1)
    self.assertEqual(closed_1["status"], "CLOSED")
    self.assertEqual(
        closed_1["resolution_comment"],
        "Target 90: Workflow Complete\n\nSummary of delivered changes.",
    )
    reloaded_1 = store.get_ticket("LOCAL-1")
    self.assertIsNotNone(reloaded_1)
    self.assertEqual(
        reloaded_1["resolution_comment"],
        "Target 90: Workflow Complete\n\nSummary of delivered changes.",
    )

    store.create_ticket(title="Second task", ticket_id="LOCAL-2")
    closed_2 = store.close_ticket("LOCAL-2")
    self.assertIsNotNone(closed_2)
    self.assertEqual(closed_2["status"], "CLOSED")
    self.assertNotIn("resolution_comment", closed_2)

    store.create_ticket(title="Third task", ticket_id="LOCAL-3")
    script = LocalTicketScript()
    out_3 = io.StringIO()
    code_3 = script.execute(
        args=[
            "close",
            "--ticket",
            "LOCAL-3",
            "--comment",
            "Target 90: Workflow Complete - high-level summary",
            "--repo-root",
            self.repo_root,
            "--json",
        ],
        stdout=out_3,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_3, 0)
    data_3 = json.loads(out_3.getvalue())
    self.assertEqual(data_3["status"], "CLOSED")
    self.assertEqual(
        data_3["resolution_comment"],
        "Target 90: Workflow Complete - high-level summary",
    )

    store.create_ticket(title="Fourth task", ticket_id="LOCAL-4")
    comment_file_path = os.path.join(self.temp_dir.name, "resolution_comment.md")
    with open(comment_file_path, "w", encoding="utf-8") as f:
      f.write(
          "Target 90: Workflow Complete\n\nDelivered CLI and ledger comment updates.\n"
      )
    out_4 = io.StringIO()
    code_4 = script.execute(
        args=[
            "close",
            "--ticket",
            "LOCAL-4",
            "--comment-file",
            comment_file_path,
            "--repo-root",
            self.repo_root,
            "--json",
        ],
        stdout=out_4,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_4, 0)
    data_4 = json.loads(out_4.getvalue())
    self.assertEqual(
        data_4["resolution_comment"],
        "Target 90: Workflow Complete\n\nDelivered CLI and ledger comment updates.",
    )

    out_view_4 = io.StringIO()
    code_view_4 = script.execute(
        args=["view", "--ticket", "LOCAL-4", "--repo-root", self.repo_root],
        stdout=out_view_4,
        stderr=io.StringIO(),
    )
    self.assertEqual(code_view_4, 0)
    self.assertIn(
        "Resolution: Target 90: Workflow Complete\n\nDelivered CLI and ledger comment updates.",
        out_view_4.getvalue(),
    )

  def test_makefile_wires_test_local_tickets_target(self):
    """TestCase 9: Root Makefile wires test-local-tickets and test discovery across TESTS_DIR."""
    makefile_path = os.path.join(_REPO_ROOT, "Makefile")
    with open(makefile_path, "r", encoding="utf-8") as f:
      mk_text = f.read()

    phony_lines = [
        line for line in mk_text.splitlines() if line.startswith(".PHONY:")
    ]
    self.assertTrue(
        any("test-local-tickets" in line for line in phony_lines),
        "Makefile .PHONY must include test-local-tickets",
    )
    self.assertIn(
        "test-local-tickets:",
        mk_text,
        "Makefile must define test-local-tickets: target",
    )
    self.assertIn(
        "test_local_tickets.py",
        mk_text,
        "Makefile test-local-tickets recipe must invoke test_local_tickets.py",
    )
    lines = mk_text.splitlines()
    test_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("test:")), None
    )
    self.assertIsNotNone(test_idx, "Makefile must define test: target")
    prereqs = lines[test_idx][len("test:") :].strip().split()
    self.assertEqual(
        prereqs,
        ["test-static"],
        "Makefile test: target must depend solely on test-static",
    )
    recipe_lines = []
    for line in lines[test_idx + 1 :]:
      if line.startswith("\t") or not line.strip() or line.lstrip().startswith("#"):
        recipe_lines.append(line)
      else:
        break
    test_recipe = "\n".join(recipe_lines)
    self.assertIn(
        "unittest discover",
        test_recipe,
        "Makefile test: recipe must run unittest discover",
    )
    self.assertIn(
        "$(TESTS_DIR)",
        test_recipe,
        "Makefile test: recipe must discover tests across $(TESTS_DIR)",
    )


if __name__ == "__main__":
  unittest.main()
