#!/usr/bin/env python3
"""Hermetic unit and CLI boundary tests for Local Ticket Management (GH-124).

Tests collision-free next-ID allocation across history.json, .workflow/tickets/,
active WorkspaceMutex locks, and Git branches; CRUD operations; priority sorting;
and local dependency blocker detection at Target 10 intake.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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


class TestLocalTicketsSubparserRepoRootGH243(unittest.TestCase):
  """Hermetic tests for subparser --repo-root and --lock-dir resolution (GH-243)."""

  def setUp(self):
    """Create isolated temporary repo and lock directories without git clones."""
    self.temp_dir = tempfile.TemporaryDirectory()
    self.target_repo = os.path.join(self.temp_dir.name, "target_repo")
    self.cwd_repo = os.path.join(self.temp_dir.name, "cwd_repo")
    self.other_repo = os.path.join(self.temp_dir.name, "other_repo")
    self.locks_dir = os.path.join(self.temp_dir.name, "locks")
    self.parent_locks_dir = os.path.join(self.temp_dir.name, "parent_locks")
    self.env_locks_dir = os.path.join(self.temp_dir.name, "env_locks")
    for directory in (
        self.target_repo,
        self.cwd_repo,
        self.other_repo,
        self.locks_dir,
        self.parent_locks_dir,
        self.env_locks_dir,
    ):
      os.makedirs(directory, exist_ok=True)
    self._default_tickets_dir = os.path.join(_REPO_ROOT, ".workflow", "tickets")
    self._default_tickets_existed = os.path.isdir(self._default_tickets_dir)
    self._default_tickets_before = (
        set(os.listdir(self._default_tickets_dir))
        if self._default_tickets_existed
        else set()
    )

  def tearDown(self):
    """Remove any stray default-repo ticket writes and clean temporary directories."""
    if os.path.isdir(self._default_tickets_dir):
      for entry in os.listdir(self._default_tickets_dir):
        if entry not in self._default_tickets_before:
          stray_path = os.path.join(self._default_tickets_dir, entry)
          if os.path.isdir(stray_path):
            shutil.rmtree(stray_path)
          else:
            os.unlink(stray_path)
      if not self._default_tickets_existed and not os.listdir(
          self._default_tickets_dir
      ):
        os.rmdir(self._default_tickets_dir)
    self.temp_dir.cleanup()

  def _run_cli(self, args, cwd=None):
    """Execute LocalTicketScript in-process with patched os.getcwd and WORKFLOW_LOCK_DIR.

    Args:
      args: CLI argument list passed to LocalTicketScript.execute().
      cwd: Optional working directory path for os.getcwd mock.

    Returns:
      Tuple of (exit_code, stdout_text, stderr_text).
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        mock.patch("os.getcwd", return_value=cwd or self.cwd_repo),
        mock.patch.dict(os.environ, {"WORKFLOW_LOCK_DIR": self.env_locks_dir}),
    ):
      code = LocalTicketScript().execute(args=args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()

  def _read_ticket_json(self, repo_root, ticket_id):
    """Read a persisted ticket JSON file directly from disk.

    Args:
      repo_root: Repository root containing .workflow/tickets/.
      ticket_id: Ticket ID stem (for example 'LOCAL-1').

    Returns:
      Parsed JSON dictionary for the ticket file.
    """
    path = os.path.join(repo_root, ".workflow", "tickets", f"{ticket_id}.json")
    self.assertTrue(os.path.isfile(path), f"Expected ticket file on disk: {path}")
    with open(path, "r", encoding="utf-8") as f:
      return json.load(f)

  def _assert_no_default_repo_writes(self):
    """Assert that _DEFAULT_REPO_ROOT/.workflow/tickets was never modified."""
    if not self._default_tickets_existed:
      self.assertFalse(
          os.path.exists(self._default_tickets_dir),
          f"Unexpected writes in plugin default tickets dir: {self._default_tickets_dir}",
      )
      return
    after = set(os.listdir(self._default_tickets_dir))
    self.assertEqual(
        after,
        self._default_tickets_before,
        "Unexpected writes in plugin default tickets dir.",
    )

  def _assert_full_lifecycle(
      self,
      build_args,
      expected_repo,
      title,
      comment,
      untouched_repos=(),
  ):
    """Exercise next-id, create, list, view, and close against expected_repo.

    Args:
      build_args: Callable taking subcommand arg list and returning full CLI args.
      expected_repo: Repository root where ticket JSON files must persist.
      title: Ticket title for the created ticket.
      comment: Resolution comment for closing the ticket.
      untouched_repos: Iterable of repo paths that must have no .workflow/tickets dir.
    """
    code_next, out_next, err_next = self._run_cli(build_args(["next-id", "--json"]))
    self.assertEqual(code_next, 0, err_next)
    self.assertEqual(json.loads(out_next)["ticket_id"], "LOCAL-1")

    code_create, out_create, err_create = self._run_cli(
        build_args(
            ["create", "--title", title, "--priority", "P1", "--json"]
        )
    )
    self.assertEqual(code_create, 0, err_create)
    created = json.loads(out_create)
    self.assertEqual(created["id"], "LOCAL-1")
    disk_created = self._read_ticket_json(expected_repo, "LOCAL-1")
    self.assertEqual(disk_created["id"], "LOCAL-1")
    self.assertEqual(disk_created["title"], title)
    self.assertEqual(disk_created["status"], "OPEN")
    self.assertEqual(disk_created["priority"], "P1")

    code_list, out_list, err_list = self._run_cli(build_args(["list", "--json"]))
    self.assertEqual(code_list, 0, err_list)
    listed = json.loads(out_list)
    self.assertEqual([t["id"] for t in listed], ["LOCAL-1"])

    code_view, out_view, err_view = self._run_cli(
        build_args(["view", "--ticket", "LOCAL-1", "--json"])
    )
    self.assertEqual(code_view, 0, err_view)
    viewed = json.loads(out_view)
    self.assertEqual(viewed["id"], "LOCAL-1")
    self.assertEqual(viewed["title"], title)

    code_close, out_close, err_close = self._run_cli(
        build_args(
            ["close", "--ticket", "LOCAL-1", "--comment", comment, "--json"]
        )
    )
    self.assertEqual(code_close, 0, err_close)
    closed = json.loads(out_close)
    self.assertEqual(closed["status"], "CLOSED")
    disk_closed = self._read_ticket_json(expected_repo, "LOCAL-1")
    self.assertEqual(disk_closed["status"], "CLOSED")
    self.assertEqual(disk_closed["resolution_comment"], comment)

    for untouched in untouched_repos:
      self.assertFalse(
          os.path.exists(os.path.join(untouched, ".workflow", "tickets")),
          f"Unexpected writes in untouched repo: {untouched}",
      )
    self._assert_no_default_repo_writes()

  def _make_arg_builder(self, repo_root, lock_dir, position):
    """Return a CLI arg builder placing --repo-root and --lock-dir pre- or post-subcommand.

    Args:
      repo_root: Target repository root path.
      lock_dir: Target workspace mutex lock directory.
      position: Either 'pre' (before subcommand) or 'post' (after subcommand).

    Returns:
      Callable accepting subcommand arg list and returning full CLI args.
    """
    flags = ["--repo-root", repo_root, "--lock-dir", lock_dir]

    def _build(sub_args):
      if position == "pre":
        return [*flags, *sub_args]
      return [*sub_args, *flags]

    return _build

  def test_initial_repo_root_matches_cwd_gh243(self):
    """LocalTicketScript initializes self.repo_root from os.getcwd() rather than plugin root (GH-243)."""
    with mock.patch("os.getcwd", return_value=self.cwd_repo):
      script = LocalTicketScript()
    self.assertEqual(script.repo_root, os.path.abspath(self.cwd_repo))
    self.assertNotEqual(script.repo_root, _REPO_ROOT)

  def test_pre_and_post_subcommand_repo_root_all_subcommands_gh243(self):
    """Pre- and post-subcommand --repo-root target the specified repo across all 5 subcommands (GH-243)."""
    cwd_store = LocalTicketStore(repo_root=self.cwd_repo, lock_dir=self.locks_dir)
    cwd_store.create_ticket(title="CWD sentinel", ticket_id="LOCAL-9")

    post_repo = os.path.join(self.temp_dir.name, "post_target_repo")
    os.makedirs(post_repo, exist_ok=True)

    modes = (
        ("pre", self.target_repo, "Pre-subcommand ticket", "Closed pre-subcommand"),
        ("post", post_repo, "Post-subcommand ticket", "Closed post-subcommand"),
    )

    for mode, expected_repo, title, comment in modes:
      with self.subTest(mode=mode):
        self._assert_full_lifecycle(
            build_args=self._make_arg_builder(expected_repo, self.locks_dir, mode),
            expected_repo=expected_repo,
            title=title,
            comment=comment,
        )
        cwd_tickets = os.listdir(os.path.join(self.cwd_repo, ".workflow", "tickets"))
        self.assertEqual(cwd_tickets, ["LOCAL-9.json"])
        self.assertEqual(
            self._read_ticket_json(self.cwd_repo, "LOCAL-9")["status"],
            "OPEN",
        )

  def test_omitted_repo_root_defaults_to_cwd_all_subcommands_gh243(self):
    """Omitting --repo-root resolves to os.getcwd() across all 5 subcommands (GH-243)."""
    code_bare, out_bare, err_bare = self._run_cli(["next-id", "--json"])
    self.assertEqual(code_bare, 0, err_bare)
    self.assertEqual(json.loads(out_bare)["ticket_id"], "LOCAL-1")

    self._assert_full_lifecycle(
        build_args=lambda sub: [*sub, "--lock-dir", self.locks_dir],
        expected_repo=self.cwd_repo,
        title="CWD default ticket",
        comment="Closed in cwd",
        untouched_repos=(self.target_repo,),
    )

  def test_pre_and_post_subcommand_lock_dir_all_subcommands_gh243(self):
    """Pre- and post-subcommand --lock-dir honor WorkspaceMutex locks across all 5 subcommands (GH-243)."""
    for mode in ("pre", "post"):
      with self.subTest(mode=mode):
        mode_repo = os.path.join(self.temp_dir.name, f"lock_{mode}_repo")
        mode_locks = os.path.join(self.temp_dir.name, f"lock_{mode}_dir")
        os.makedirs(mode_repo, exist_ok=True)
        os.makedirs(mode_locks, exist_ok=True)

        store = LocalTicketStore(repo_root=mode_repo, lock_dir=mode_locks)
        store.create_ticket(title="Unlocked open", ticket_id="LOCAL-1")
        store.create_ticket(title="Locked open", ticket_id="LOCAL-2")

        mutex = WorkspaceMutex(repo_root=mode_repo, lock_dir=mode_locks)
        mutex.acquire(
            ticket_id="LOCAL-2",
            workspace_path=mode_repo,
            branch_name="feat/local-2-active",
            pid=os.getpid(),
        )
        mutex.acquire(
            ticket_id="LOCAL-10",
            workspace_path=mode_repo,
            branch_name="feat/local-10-active",
            pid=os.getpid(),
        )
        WorkspaceMutex(
            repo_root=mode_repo, lock_dir=self.env_locks_dir
        ).acquire(
            ticket_id="LOCAL-50",
            workspace_path=mode_repo,
            branch_name="feat/local-50-env",
            pid=os.getpid(),
        )

        build_args = self._make_arg_builder(mode_repo, mode_locks, mode)

        code_next, out_next, err_next = self._run_cli(
            build_args(["next-id", "--json"])
        )
        self.assertEqual(code_next, 0, err_next)
        self.assertEqual(json.loads(out_next)["ticket_id"], "LOCAL-11")

        code_list, out_list, err_list = self._run_cli(
            build_args(["list", "--json"])
        )
        self.assertEqual(code_list, 0, err_list)
        self.assertEqual(
            [t["id"] for t in json.loads(out_list)],
            ["LOCAL-1"],
        )

        code_create, out_create, err_create = self._run_cli(
            build_args(["create", "--title", "Lock aware task", "--json"])
        )
        self.assertEqual(code_create, 0, err_create)
        created = json.loads(out_create)
        self.assertEqual(created["id"], "LOCAL-11")
        self.assertEqual(
            self._read_ticket_json(mode_repo, "LOCAL-11")["title"],
            "Lock aware task",
        )

        code_view, out_view, err_view = self._run_cli(
            build_args(["view", "--ticket", "LOCAL-11", "--json"])
        )
        self.assertEqual(code_view, 0, err_view)
        self.assertEqual(json.loads(out_view)["title"], "Lock aware task")

        code_close, out_close, err_close = self._run_cli(
            build_args(
                [
                    "close",
                    "--ticket",
                    "LOCAL-11",
                    "--comment",
                    "Lock aware close",
                    "--json",
                ]
            )
        )
        self.assertEqual(code_close, 0, err_close)
        self.assertEqual(json.loads(out_close)["status"], "CLOSED")
        self.assertEqual(
            self._read_ticket_json(mode_repo, "LOCAL-11")["status"],
            "CLOSED",
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.cwd_repo, ".workflow", "tickets"))
        )
        self._assert_no_default_repo_writes()

  def test_subcommand_repo_root_and_lock_dir_precedence_gh243(self):
    """Subcommand --repo-root and --lock-dir override parent flags without clobbering sibling parent flags (GH-243)."""
    mix_repo = os.path.join(self.temp_dir.name, "mix_repo")
    mix_locks = os.path.join(self.temp_dir.name, "mix_locks")
    os.makedirs(mix_repo, exist_ok=True)
    os.makedirs(mix_locks, exist_ok=True)

    mix_store = LocalTicketStore(repo_root=mix_repo, lock_dir=mix_locks)
    mix_store.create_ticket(title="Mix open", ticket_id="LOCAL-1")
    mix_store.create_ticket(title="Mix locked", ticket_id="LOCAL-2")
    mix_mutex = WorkspaceMutex(repo_root=mix_repo, lock_dir=mix_locks)
    mix_mutex.acquire(
        ticket_id="LOCAL-2",
        workspace_path=mix_repo,
        branch_name="feat/local-2-mix",
        pid=os.getpid(),
    )
    mix_mutex.acquire(
        ticket_id="LOCAL-10",
        workspace_path=mix_repo,
        branch_name="feat/local-10-mix",
        pid=os.getpid(),
    )

    code_mix_next, out_mix_next, err_mix_next = self._run_cli(
        [
            "--repo-root",
            mix_repo,
            "--json",
            "next-id",
            "--lock-dir",
            mix_locks,
        ]
    )
    self.assertEqual(code_mix_next, 0, err_mix_next)
    self.assertEqual(json.loads(out_mix_next)["ticket_id"], "LOCAL-11")

    code_mix_list, out_mix_list, err_mix_list = self._run_cli(
        [
            "--lock-dir",
            mix_locks,
            "list",
            "--repo-root",
            mix_repo,
            "--json",
        ]
    )
    self.assertEqual(code_mix_list, 0, err_mix_list)
    self.assertEqual(
        [t["id"] for t in json.loads(out_mix_list)],
        ["LOCAL-1"],
    )

    other_store = LocalTicketStore(
        repo_root=self.other_repo, lock_dir=self.locks_dir
    )
    other_store.create_ticket(title="Parent repo sentinel", ticket_id="LOCAL-20")

    code_create, out_create, err_create = self._run_cli(
        [
            "--repo-root",
            self.other_repo,
            "create",
            "--title",
            "Subcommand wins",
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_create, 0, err_create)
    self.assertEqual(json.loads(out_create)["id"], "LOCAL-1")
    self.assertEqual(
        self._read_ticket_json(self.target_repo, "LOCAL-1")["title"],
        "Subcommand wins",
    )
    self.assertEqual(
        os.listdir(os.path.join(self.other_repo, ".workflow", "tickets")),
        ["LOCAL-20.json"],
    )

    code_next, out_next, err_next = self._run_cli(
        [
            "--repo-root",
            self.other_repo,
            "next-id",
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_next, 0, err_next)
    self.assertEqual(json.loads(out_next)["ticket_id"], "LOCAL-2")

    code_list, out_list, err_list = self._run_cli(
        [
            "--repo-root",
            self.other_repo,
            "list",
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_list, 0, err_list)
    self.assertEqual([t["id"] for t in json.loads(out_list)], ["LOCAL-1"])

    code_view, out_view, err_view = self._run_cli(
        [
            "--repo-root",
            self.other_repo,
            "view",
            "--ticket",
            "LOCAL-1",
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_view, 0, err_view)
    self.assertEqual(json.loads(out_view)["title"], "Subcommand wins")

    code_close, out_close, err_close = self._run_cli(
        [
            "--repo-root",
            self.other_repo,
            "close",
            "--ticket",
            "LOCAL-1",
            "--repo-root",
            self.target_repo,
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_close, 0, err_close)
    self.assertEqual(json.loads(out_close)["status"], "CLOSED")
    self.assertEqual(
        self._read_ticket_json(self.target_repo, "LOCAL-1")["status"],
        "CLOSED",
    )
    self.assertEqual(
        self._read_ticket_json(self.other_repo, "LOCAL-20")["status"],
        "OPEN",
    )

    prec_lock_repo = os.path.join(self.temp_dir.name, "prec_lock_repo")
    os.makedirs(prec_lock_repo, exist_ok=True)
    prec_store = LocalTicketStore(
        repo_root=prec_lock_repo, lock_dir=self.locks_dir
    )
    prec_store.create_ticket(title="Ticket one", ticket_id="LOCAL-1")
    prec_store.create_ticket(title="Ticket two", ticket_id="LOCAL-2")

    parent_mutex = WorkspaceMutex(
        repo_root=prec_lock_repo, lock_dir=self.parent_locks_dir
    )
    parent_mutex.acquire(
        ticket_id="LOCAL-2",
        workspace_path=prec_lock_repo,
        branch_name="feat/local-2-parent",
        pid=os.getpid(),
    )
    parent_mutex.acquire(
        ticket_id="LOCAL-40",
        workspace_path=prec_lock_repo,
        branch_name="feat/local-40-parent",
        pid=os.getpid(),
    )

    sub_mutex = WorkspaceMutex(repo_root=prec_lock_repo, lock_dir=self.locks_dir)
    sub_mutex.acquire(
        ticket_id="LOCAL-1",
        workspace_path=prec_lock_repo,
        branch_name="feat/local-1-sub",
        pid=os.getpid(),
    )
    sub_mutex.acquire(
        ticket_id="LOCAL-20",
        workspace_path=prec_lock_repo,
        branch_name="feat/local-20-sub",
        pid=os.getpid(),
    )

    code_lock_next, out_lock_next, err_lock_next = self._run_cli(
        [
            "--repo-root",
            prec_lock_repo,
            "--lock-dir",
            self.parent_locks_dir,
            "next-id",
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_lock_next, 0, err_lock_next)
    self.assertEqual(json.loads(out_lock_next)["ticket_id"], "LOCAL-21")

    code_lock_list, out_lock_list, err_lock_list = self._run_cli(
        [
            "--repo-root",
            prec_lock_repo,
            "--lock-dir",
            self.parent_locks_dir,
            "list",
            "--lock-dir",
            self.locks_dir,
            "--json",
        ]
    )
    self.assertEqual(code_lock_list, 0, err_lock_list)
    self.assertEqual(
        [t["id"] for t in json.loads(out_lock_list)],
        ["LOCAL-2"],
    )

    self.assertFalse(
        os.path.exists(os.path.join(self.cwd_repo, ".workflow", "tickets"))
    )
    self._assert_no_default_repo_writes()


if __name__ == "__main__":
  unittest.main()
