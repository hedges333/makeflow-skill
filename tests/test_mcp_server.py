#!/usr/bin/env python3
"""Test harness for the Makefile Workflow MCP Server.

Checks JSON-RPC 2.0 communication over standard input/output:
1. initialize
2. tools/list
3. workflow_safety_check (hostname prod, underscore prod_db, safe test command)
4. workflow_advance_target (blocked gate, blocked illegal DAG jump, valid step)
5. workflow_verify_tree with workspaceDir from a non-git working directory
6. workflow_get_state and workflow_get_config with workspaceDir
"""

import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)
SERVER_SCRIPT = os.path.join(REPO_ROOT, "mcp", "server.py")


def send_rpc(proc, msg):
  proc.stdin.write(json.dumps(msg) + "\n")
  proc.stdin.flush()
  return json.loads(proc.stdout.readline())


def _stop_mcp_server(proc):
  """Terminates an MCP server subprocess and closes its stdio pipes.

  Args:
    proc: Running or exited subprocess.Popen handle.
  """
  if proc.poll() is None:
    try:
      proc.kill()
    except ProcessLookupError:
      pass  # Process exited before kill() arrived.
  try:
    proc.wait(timeout=5)
  except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait()
  for pipe in (proc.stdin, proc.stdout, proc.stderr):
    if pipe is not None and not pipe.closed:
      pipe.close()


def _start_mcp_server(test_case, cwd, env=None):
  """Starts the MCP stdio server and registers pipe cleanup on test_case.

  Args:
    test_case: Active unittest.TestCase registering cleanup callbacks.
    cwd: Working directory for the child MCP server process.
    env: Optional environment variable mapping for the child process.

  Returns:
    Started subprocess.Popen handle connected via text stdio pipes.
  """
  proc = subprocess.Popen(
      [sys.executable, SERVER_SCRIPT],
      stdin=subprocess.PIPE,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      cwd=cwd,
      env=env,
      text=True,
  )
  test_case.addCleanup(_stop_mcp_server, proc)
  return proc


def _make_temp_dir(test_case):
  """Creates a temporary directory cleaned up after server shutdown.

  Args:
    test_case: Active unittest.TestCase registering cleanup callbacks.

  Returns:
    Absolute path to the temporary directory.
  """
  tmp_dir = tempfile.TemporaryDirectory()
  test_case.addCleanup(tmp_dir.cleanup)
  return tmp_dir.name


class TestMcpServer(unittest.TestCase):
  """Unittest suite for MCP server JSON-RPC protocol and safety handlers (GH-216)."""

  def test_initialize_and_tools_list(self):
    """Checks MCP initialize handshake version and tools/list enumeration."""
    non_git_cwd = _make_temp_dir(self)
    p = _start_mcp_server(self, non_git_cwd)

    # 1. Initialize
    init_resp = send_rpc(
        p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    self.assertEqual(init_resp["id"], 1)
    self.assertEqual(
        init_resp["result"]["serverInfo"]["name"], "makeflow-guard"
    )
    with open(os.path.join(REPO_ROOT, "plugin.json"), encoding="utf-8") as f:
      expected_version = json.load(f)["version"]
    self.assertEqual(
        init_resp["result"]["serverInfo"]["version"], expected_version
    )

    # 2. Tools list
    tools_resp = send_rpc(
        p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    tool_names = [t["name"] for t in tools_resp["result"]["tools"]]
    for expected in (
        "workflow_get_state",
        "workflow_advance_target",
        "workflow_verify_tree",
        "workflow_safety_check",
        "workflow_get_config",
        "workflow_poll_ci",
        "workflow_query_scars",
        "workflow_repo_tree",
    ):
      with self.subTest(tool=expected):
        self.assertIn(expected, tool_names)
    self.assertEqual(len(tool_names), 8)

  def test_safety_check_prod_and_safe_commands(self):
    """Checks workflow_safety_check blocks prod DBs and allows safe test commands."""
    non_git_cwd = _make_temp_dir(self)
    p = _start_mcp_server(self, non_git_cwd)

    # 3. Safety check: production hostname and underscore prod_db
    for idx, bad_cmd in enumerate(
        (
            "psql -h production.db.internal myapp",
            "mariadb -u app myapp_prod",
        ),
        start=3,
    ):
      with self.subTest(command=bad_cmd):
        bad_resp = send_rpc(
            p,
            {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": "workflow_safety_check",
                    "arguments": {"commandLine": bad_cmd},
                },
            },
        )
        content_bad = json.loads(bad_resp["result"]["content"][0]["text"])
        self.assertIs(content_bad["safe"], False)

    # 4. Safety check: safe test command
    good_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "workflow_safety_check",
                "arguments": {
                    "commandLine": "pytest tests/ --db=sqlite:///:memory:"
                },
            },
        },
    )
    content_good = json.loads(good_resp["result"]["content"][0]["text"])
    self.assertIs(content_good["safe"], True)

  def test_advance_target_dag_and_state_cards(self):
    """Checks workflow_advance_target DAG edges, state cards, and T60 -> T40 transitions."""
    non_git_cwd = _make_temp_dir(self)
    p = _start_mcp_server(self, non_git_cwd)

    # 5. Advance target: unapproved plan blocked, illegal T10->T80 jump blocked, valid T20->T30 allowed
    adv_bad = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "workflow_advance_target",
                "arguments": {
                    "currentTarget": "T20",
                    "nextTarget": "T30",
                    "evidence": {"humanPlanApproved": False},
                },
            },
        },
    )
    self.assertIs(adv_bad["result"]["isError"], True)

    adv_jump = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "workflow_advance_target",
                "arguments": {
                    "currentTarget": "T10",
                    "nextTarget": "T80",
                },
            },
        },
    )
    self.assertIs(adv_jump["result"]["isError"], True)

    adv_ok = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "workflow_advance_target",
                "arguments": {
                    "currentTarget": "T20",
                    "nextTarget": "T30",
                    "evidence": {"humanPlanApproved": True},
                },
            },
        },
    )
    self.assertFalse(adv_ok["result"].get("isError"))

    # 5b. Advance target: on-disk state card enforcement
    tmp_state_ws = _make_temp_dir(self)
    tmp_state_dir = os.path.join(tmp_state_ws, ".workflow", "state")
    os.makedirs(tmp_state_dir)

    adv_no_card = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 80,
            "method": "tools/call",
            "params": {
                "name": "workflow_advance_target",
                "arguments": {
                    "currentTarget": "T20",
                    "nextTarget": "T30",
                    "workspaceDir": tmp_state_ws,
                    "evidence": {"humanPlanApproved": True},
                },
            },
        },
    )
    self.assertIs(adv_no_card["result"]["isError"], True)
    err_msg = adv_no_card["result"]["content"][0]["text"]
    self.assertIn("20_plan_approved.md", err_msg)

    with open(
        os.path.join(tmp_state_dir, "20_plan_approved.md"),
        "w",
        encoding="utf-8",
    ) as f:
      f.write("# Plan Approved\n")

    adv_with_card = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 81,
            "method": "tools/call",
            "params": {
                "name": "workflow_advance_target",
                "arguments": {
                    "currentTarget": "T20",
                    "nextTarget": "T30",
                    "workspaceDir": tmp_state_ws,
                    "evidence": {"humanPlanApproved": True},
                },
            },
        },
    )
    self.assertFalse(adv_with_card["result"].get("isError"))

    # 5c. Advance target: T60 -> T40 (pre-push rebase critic re-audit) and T60 invalid jumps
    adv_t60_t40 = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 82,
            "method": "tools/call",
            "params": {
                "name": "workflow_advance_target",
                "arguments": {
                    "currentTarget": "T60",
                    "nextTarget": "T40",
                },
            },
        },
    )
    self.assertFalse(
        adv_t60_t40["result"].get("isError"),
        f"Expected T60 -> T40 transition to succeed, got {adv_t60_t40}",
    )

    for bad_target in ("T10", "T20", "T50", "T80"):
      with self.subTest(bad_target=bad_target):
        adv_t60_bad = send_rpc(
            p,
            {
                "jsonrpc": "2.0",
                "id": 83,
                "method": "tools/call",
                "params": {
                    "name": "workflow_advance_target",
                    "arguments": {
                        "currentTarget": "T60",
                        "nextTarget": bad_target,
                    },
                },
            },
        )
        self.assertIs(
            adv_t60_bad["result"].get("isError"),
            True,
            f"Expected T60 -> {bad_target} transition to be blocked",
        )

  def test_verify_tree_non_git_cwd_and_default_config(self):
    """Checks workflow_verify_tree from non-git cwd and default workflow_get_config."""
    non_git_cwd = _make_temp_dir(self)
    p = _start_mcp_server(self, non_git_cwd)

    # 6. Verify tree with workspaceDir when server cwd is not a git repo
    tmp_git_ws = _make_temp_dir(self)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=tmp_git_ws,
        check=True,
        capture_output=True,
        text=True,
    )
    with open(
        os.path.join(tmp_git_ws, "README.md"), "w", encoding="utf-8"
    ) as f:
      f.write("# Test\n")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=tmp_git_ws,
        check=True,
        capture_output=True,
        text=True,
    )
    tree_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "workflow_verify_tree",
                "arguments": {
                    "expectedApprovedTree": "0" * 40,
                    "workspaceDir": tmp_git_ws,
                },
            },
        },
    )
    self.assertFalse(tree_resp["result"].get("isError"))
    tree_data = json.loads(tree_resp["result"]["content"][0]["text"])
    self.assertEqual(len(tree_data["actualTree"]), 40)

    # 7. Get config with default workspaceDir
    empty_ws = _make_temp_dir(self)
    cfg_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": empty_ws},
            },
        },
    )
    cfg_data = json.loads(cfg_resp["result"]["content"][0]["text"])
    self.assertIn("models", cfg_data)
    self.assertIn("agyModelTiers", cfg_data)
    for role in (
        "adversarial-critic",
        "archaeology-worker",
        "tdd-worker",
        "triage-worker",
    ):
      with self.subTest(role=role):
        self.assertEqual(
            cfg_data["models"].get(role),
            "inherit",
            f"Expected default models[{role}] == 'inherit', got {cfg_data['models'].get(role)}",
        )
        self.assertEqual(
            cfg_data["agyModelTiers"].get(role),
            "inherit",
            f"Expected default agyModelTiers[{role}] == 'inherit', got {cfg_data['agyModelTiers'].get(role)}",
        )
    self.assertIn("review", cfg_data)
    self.assertIn("ci", cfg_data)
    self.assertIn("database", cfg_data)
    self.assertIn("prodDatabases", cfg_data["database"])
    self.assertIn("testDatabases", cfg_data["database"])
    self.assertIn("prodHosts", cfg_data["database"])
    self.assertIn("state", cfg_data)
    self.assertEqual(cfg_data["state"]["completedCardAction"], "unlink")

  def test_custom_database_config_and_git_safety_checks(self):
    """Checks custom database config filtering and SCAR-GIT-25 git command safety checks."""
    non_git_cwd = _make_temp_dir(self)
    p = _start_mcp_server(self, non_git_cwd)

    # 8. Custom database configuration in workspaceDir for workflow_get_config and workflow_safety_check
    ws_cfg_dir = _make_temp_dir(self)
    os.makedirs(os.path.join(ws_cfg_dir, ".workflow"))
    with open(
        os.path.join(ws_cfg_dir, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump(
          {
              "models": {
                  "adversarial-critic": "gemini-4-argon-high",
                  "archaeology-worker": "gemini-3.8-flash-low",
                  "tdd-worker": "flash_lite",
                  "triage-worker": "pro",
              },
              "database": {
                  "prodDatabases": ["billing_live", 1, True, None],
                  "testDatabases": ["billing_sandbox", "billing", 1, False],
                  "prodHosts": ["db.company.internal", None],
              },
          },
          f,
      )

    ws_cfg_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": ws_cfg_dir},
            },
        },
    )
    ws_cfg = json.loads(ws_cfg_resp["result"]["content"][0]["text"])
    self.assertEqual(ws_cfg["models"]["adversarial-critic"], "gemini-4-argon-high")
    self.assertEqual(ws_cfg["agyModelTiers"]["adversarial-critic"], "pro")
    self.assertEqual(ws_cfg["models"]["archaeology-worker"], "gemini-3.8-flash-low")
    self.assertEqual(ws_cfg["agyModelTiers"]["archaeology-worker"], "flash")
    self.assertEqual(ws_cfg["models"]["tdd-worker"], "flash_lite")
    self.assertEqual(ws_cfg["agyModelTiers"]["tdd-worker"], "flash_lite")
    self.assertEqual(ws_cfg["models"]["triage-worker"], "pro")
    self.assertEqual(ws_cfg["agyModelTiers"]["triage-worker"], "pro")
    self.assertEqual(ws_cfg["database"]["prodDatabases"], ["billing_live"])
    self.assertEqual(
        ws_cfg["database"]["testDatabases"], ["billing_sandbox", "billing"]
    )
    self.assertEqual(
        ws_cfg["database"]["prodHosts"], ["db.company.internal"]
    )

    for req_id, cmd_str, expected_safe in (
        (12, "psql -h localhost billing_live", False),
        (13, "psql -h db.company.internal billing_sandbox", False),
        (
            14,
            "psql -h localhost billing_sandbox -c 'SELECT prod FROM invoices'",
            True,
        ),
        (15, "psql -h production.db.internal billing_sandbox", False),
        (16, "pg_dump myapp_prod | psql billing_sandbox", False),
        (17, 'psql -h "prod.db.internal" billing_sandbox', False),
        (18, "PGHOST=prod.db.internal psql billing_sandbox", False),
        (19, 'psql "host=prod.db.internal dbname=billing_sandbox"', False),
        (20, "psql -h localhost billing-prod", False),
        (21, "pg_restore -d myapp_prod billing_sandbox.dump", False),
        (
            22,
            'psql "postgresql://prod_user:secret@localhost:5432/billing_sandbox" -c "SELECT prod FROM t"',
            True,
        ),
    ):
      with self.subTest(command=cmd_str):
        sc_resp = send_rpc(
            p,
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {
                    "name": "workflow_safety_check",
                    "arguments": {
                        "commandLine": cmd_str,
                        "workspaceDir": ws_cfg_dir,
                    },
                },
            },
        )
        sc_data = json.loads(sc_resp["result"]["content"][0]["text"])
        self.assertIs(
            sc_data["safe"],
            expected_safe,
            f"Expected safe={expected_safe} for '{cmd_str}', got {sc_data}",
        )

    # 8b. Destructive Git safety checks via workflow_safety_check (SCAR-GIT-25)
    for git_idx, (git_cmd_str, expected_git_safe) in enumerate(
        (
            ("git reset --hard", False),
            ("git -- reset --hard", False),
            ("git clean -fd", False),
            ("git clean -i", False),
            ("git clean -n --no-dry-run -fd", False),
            ("git -c clean.requireForce=false clean -d", False),
            ("git checkout -- README.md", False),
            ("git checkout .", False),
            ("git checkout -p", False),
            ("git checkout --ours README.md", False),
            ("git checkout --theirs README.md", False),
            ("git checkout --pathspec-from-file=paths.txt", False),
            ("git switch --discard-changes main", False),
            ("git restore README.md", False),
            ("git restore --staged --no-staged README.md", False),
            ("git stash drop", False),
            ("git filter-branch --all", False),
            ("git reflog delete HEAD@{1}", False),
            ("git update-ref -d refs/heads/feat/old", False),
            ("git update-ref --stdin", False),
            ("git push --force origin feat/2-test", False),
            ("git push --force-with-lease origin feat/2-test", False),
            ("git push --mirror origin", False),
            ("git push origin +HEAD:feat/2-test", False),
            ("git -c remote.origin.push=+HEAD:main push origin", False),
            ("git status && git reset --hard", False),
            ('echo "$(git clean -fd)"', False),
            ('bash -euo pipefail -c "git reset --hard"', False),
            ('sh -c -- "git reset --hard"', False),
            ('bash -co pipefail "git reset --hard"', False),
            ('eval "git reset --hard"', False),
            ('env -S "git reset --hard"', False),
            ('for f in a; do git checkout -- "$f"; done', False),
            ("git 2>&1 reset --hard", False),
            ("psql 2>&1 -h production.db.internal myapp", False),
            ("# Don't fail\ngit reset --hard", False),
            ("echo $'a\\'b';git reset --hard", False),
            ("(cd /tmp && git reset --hard)", False),
            ("git status --short", True),
            ("git checkout main 2>&1", True),
            ("git checkout main 2>/dev/null", True),
            ("git checkout -b feat/2-test", True),
            ("git restore --staged README.md", True),
            ("git clean -nd", True),
            ("git branch -d feat/1-done", True),
            ('echo "do not run git reset --hard"', True),
            ('git commit -m "block git reset --hard"', True),
        ),
        start=100,
    ):
      with self.subTest(command=git_cmd_str):
        git_sc_resp = send_rpc(
            p,
            {
                "jsonrpc": "2.0",
                "id": git_idx,
                "method": "tools/call",
                "params": {
                    "name": "workflow_safety_check",
                    "arguments": {"commandLine": git_cmd_str},
                },
            },
        )
        git_sc_data = json.loads(git_sc_resp["result"]["content"][0]["text"])
        self.assertIs(
            git_sc_data["safe"],
            expected_git_safe,
            f"Expected safe={expected_git_safe} for '{git_cmd_str}', got {git_sc_data}",
        )

    for adv_idx, adv_cmd in enumerate(
        (
            "git push -u origin feat/2-test",
            "git rebase main",
            "git branch -D feat/old",
            "git branch -M old main",
            'git commit -m "feat: add safety check"',
            "gh pr merge 2 --squash",
            "echo ok && git rebase main",
        ),
        start=200,
    ):
      with self.subTest(command=adv_cmd):
        adv_sc_resp = send_rpc(
            p,
            {
                "jsonrpc": "2.0",
                "id": adv_idx,
                "method": "tools/call",
                "params": {
                    "name": "workflow_safety_check",
                    "arguments": {"commandLine": adv_cmd},
                },
            },
        )
        adv_sc_data = json.loads(adv_sc_resp["result"]["content"][0]["text"])
        self.assertIs(
            adv_sc_data["safe"], True, f"Expected safe=True on '{adv_cmd}'"
        )
        self.assertIn(
            "Requires human operator approval",
            adv_sc_data["reason"],
            f"Expected operator approval reason on '{adv_cmd}', got {adv_sc_data}",
        )

  def test_workflow_query_scars_jsonrpc_and_loaded_cache(self):
    """Checks workflow_query_scars JSON-RPC queries, filters, and loaded_scars.json cache."""
    non_git_cwd = _make_temp_dir(self)
    p = _start_mcp_server(self, non_git_cwd)

    # Case a: Query without query argument -> error
    no_query_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 250,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {"workspaceDir": non_git_cwd},
            },
        },
    )
    self.assertIs(no_query_resp["result"]["isError"], True)
    self.assertIn(
        "Missing required argument 'query'",
        no_query_resp["result"]["content"][0]["text"],
    )

    # Case b: Query when database does not exist -> UNINITIALIZED status
    uninit_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 251,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "BaseScript",
                    "workspaceDir": non_git_cwd,
                    "dbPath": os.path.join(non_git_cwd, "nonexistent.db"),
                },
            },
        },
    )
    uninit_data = json.loads(uninit_resp["result"]["content"][0]["text"])
    self.assertEqual(uninit_data["status"], "UNINITIALIZED")
    self.assertIn(
        "Bundled registry database is missing from skill package",
        uninit_data["message"],
    )
    self.assertEqual(uninit_data["scars"], [])

    # Case c: Query when database exists with sample scars
    test_scars_db = os.path.join(non_git_cwd, "test_scars.db")
    conn = sqlite3.connect(test_scars_db)
    try:
      conn.execute("""
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT,
              source TEXT DEFAULT 'upstream',
              original_id TEXT,
              raw_hash TEXT
          )
      """)
      conn.execute("""
          CREATE VIRTUAL TABLE scars_fts USING fts5(
              id,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              socratic_probe,
              source UNINDEXED,
              original_id
          )
      """)
      test_scars = [
          (
              "SCAR-ARCH-05",
              "BaseScript Lifecycle Template Method Pattern",
              "Architecture",
              "architecture",
              "public",
              json.dumps(["T30", "T50"]),
              "Implementing CLI scripts or command runners in Python",
              "graduated",
              "Direct sys.exit calls in business logic prevent test execution",
              "Running CLI scripts that terminate test suites",
              "Wrap all CLI scripts in BaseScript execute template",
              "Inherit from BaseScript and override run and cleanup",
              "Did you inherit from BaseScript?",
              "upstream",
              None,
              "hash-arch-05",
          ),
          (
              "SCAR-TEST-08",
              "Hermetic In-Memory Test Isolation",
              "Testing",
              "testing",
              "public",
              json.dumps(["T30"]),
              "Writing unit and integration tests without network sockets",
              "graduated",
              "Tests connecting to live databases or remote APIs fail intermittently",
              "Using unmocked socket connections or live databases in unit tests",
              "Use tempfile.TemporaryDirectory and mock urllib.request",
              "Point tests to temporary directories or in-memory SQLite fixtures",
              "Do your tests run hermetically in temporary directories?",
              "upstream",
              None,
              "hash-test-08",
          ),
          (
              "SCAR-UNPROC-99",
              "Unprocessed BaseScript Draft",
              "Architecture",
              "architecture",
              "public",
              json.dumps(["T30"]),
              "Draft unprocessed scar",
              "experimental",
              None,
              "Draft trap",
              "",
              "Draft reflex",
              None,
              "upstream",
              None,
              "hash-unproc-99",
          ),
      ]
      for s in test_scars:
        conn.execute(
            "INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            s,
        )
        conn.execute(
            "INSERT INTO scars_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                s[0],
                s[1],
                s[2],
                s[6],
                s[8] or "",
                s[9] or "",
                s[11] or "",
                s[12] or "",
                s[13],
                s[14] or "",
            ),
        )
      conn.commit()
    finally:
      conn.close()

    # Query matching BaseScript
    query_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 252,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "BaseScript",
                    "dbPath": test_scars_db,
                },
            },
        },
    )
    query_data = json.loads(query_resp["result"]["content"][0]["text"])
    self.assertEqual(query_data["status"], "OK")
    self.assertEqual(query_data["totalResults"], 1)
    self.assertEqual(query_data["scars"][0]["id"], "SCAR-ARCH-05")
    self.assertIn("wound", query_data["scars"][0])
    self.assertIn("source", query_data["scars"][0])
    self.assertEqual(query_data["scars"][0]["source"], "upstream")
    self.assertEqual(query_data.get("alreadyLoaded"), [])
    self.assertIn(
        os.path.abspath(test_scars_db),
        query_data.get("searchedDatabases", []),
    )

    # Query with source filter (with refreshLoaded=True to get full scar fields again)
    src_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 256,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "BaseScript",
                    "source": "upstream",
                    "dbPath": test_scars_db,
                    "refreshLoaded": True,
                },
            },
        },
    )
    src_data = json.loads(src_resp["result"]["content"][0]["text"])
    self.assertEqual(src_data["status"], "OK")
    self.assertGreaterEqual(src_data["totalResults"], 1)
    self.assertEqual(src_data["scars"][0]["source"], "upstream")
    self.assertIn("wound", src_data["scars"][0])

    # Query with category filter
    cat_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 253,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "isolation",
                    "category": "Testing",
                    "dbPath": test_scars_db,
                },
            },
        },
    )
    cat_data = json.loads(cat_resp["result"]["content"][0]["text"])
    self.assertEqual(cat_data["status"], "OK")
    self.assertEqual(cat_data["totalResults"], 1)
    self.assertEqual(cat_data["scars"][0]["id"], "SCAR-TEST-08")

    # Query with limit filter
    lim_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 254,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "SCAR",
                    "limit": 1,
                    "dbPath": test_scars_db,
                    "refreshLoaded": True,
                },
            },
        },
    )
    lim_data = json.loads(lim_resp["result"]["content"][0]["text"])
    self.assertEqual(lim_data["status"], "OK")
    self.assertEqual(len(lim_data["scars"]), 1)

    # Case d: Query when dbPath is omitted -> resolves stacked databases including workspace and bundled
    ws_cache_dir = os.path.join(non_git_cwd, ".workflow", "cache")
    ws_state_dir = os.path.join(non_git_cwd, ".workflow", "state")
    os.makedirs(ws_cache_dir, exist_ok=True)
    os.makedirs(ws_state_dir, exist_ok=True)
    with open(
        os.path.join(ws_state_dir, "10_ticket.md"), "w", encoding="utf-8"
    ) as tf:
      tf.write("# Ticket: GH-192\n")
    ws_scars_db = os.path.join(ws_cache_dir, "scars_registry.db")
    shutil.copyfile(test_scars_db, ws_scars_db)

    default_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 255,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "BaseScript",
                    "workspaceDir": non_git_cwd,
                    "limit": 2,
                },
            },
        },
    )
    default_data = json.loads(default_resp["result"]["content"][0]["text"])
    self.assertEqual(default_data["status"], "OK")
    self.assertGreater(len(default_data["scars"]), 0)
    self.assertIn(
        os.path.abspath(ws_scars_db),
        default_data.get("searchedDatabases", []),
    )
    self.assertGreaterEqual(len(default_data.get("searchedDatabases", [])), 2)
    self.assertTrue(
        os.path.isfile(os.path.join(ws_state_dir, "loaded_scars.json"))
    )

    # Second query in same workspace session returns compact reminder for already-loaded SCAR-ARCH-05
    second_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 257,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {
                    "query": "SCAR-ARCH-05",
                    "workspaceDir": non_git_cwd,
                    "limit": 1,
                },
            },
        },
    )
    second_data = json.loads(second_resp["result"]["content"][0]["text"])
    self.assertEqual(second_data["status"], "OK")
    self.assertGreaterEqual(len(second_data["alreadyLoaded"]), 1)
    self.assertEqual(second_data["alreadyLoaded"][0]["id"], "SCAR-ARCH-05")
    self.assertIs(second_data["alreadyLoaded"][0]["alreadyLoaded"], True)
    self.assertNotIn("wound", second_data["alreadyLoaded"][0])

  def test_env_var_overrides_and_partial_config_poll_ci(self):
    """Checks environment variable overrides, recursive config deep-merge, and poll_ci fallback."""
    # 9. Environment variable database overrides
    env_cwd = _make_temp_dir(self)
    custom_env = dict(os.environ)
    custom_env["MAKEFILE_WORKFLOW_PROD_DBS"] = "ledger_live"
    custom_env["MAKEFILE_WORKFLOW_TEST_DBS"] = "ledger_test"
    custom_env["MAKEFILE_WORKFLOW_PROD_HOSTS"] = "live-cluster.internal"
    custom_env["MAKEFILE_WORKFLOW_CRITIC_MODEL"] = "gemini-4-argon-high"
    custom_env["MAKEFILE_WORKFLOW_WORKER_MODEL"] = "flash_lite"
    custom_env["MAKEFILE_WORKFLOW_TDD_MODEL"] = "pro"
    custom_env["MAKEFILE_WORKFLOW_TRIAGE_MODEL"] = "flash"
    custom_env["MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS"] = "40"
    custom_env["MAKEFILE_WORKFLOW_CI_POLL_INTERVAL"] = "8"
    custom_env["MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION"] = "archive"
    p_env = _start_mcp_server(self, env_cwd, env=custom_env)
    send_rpc(
        p_env, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    env_cfg_resp = send_rpc(
        p_env,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": env_cwd},
            },
        },
    )
    env_cfg = json.loads(env_cfg_resp["result"]["content"][0]["text"])
    self.assertIn("ledger_live", env_cfg["database"]["prodDatabases"])
    self.assertIn("ledger_test", env_cfg["database"]["testDatabases"])
    self.assertIn("live-cluster.internal", env_cfg["database"]["prodHosts"])
    self.assertEqual(
        env_cfg["models"]["adversarial-critic"], "gemini-4-argon-high"
    )
    self.assertEqual(env_cfg["agyModelTiers"]["adversarial-critic"], "pro")
    self.assertEqual(env_cfg["models"]["archaeology-worker"], "flash_lite")
    self.assertEqual(
        env_cfg["agyModelTiers"]["archaeology-worker"], "flash_lite"
    )
    self.assertEqual(env_cfg["models"]["tdd-worker"], "pro")
    self.assertEqual(env_cfg["agyModelTiers"]["tdd-worker"], "pro")
    self.assertEqual(env_cfg["models"]["triage-worker"], "flash")
    self.assertEqual(env_cfg["agyModelTiers"]["triage-worker"], "flash")
    self.assertEqual(env_cfg["ci"]["quiescenceSeconds"], 40)
    self.assertEqual(env_cfg["ci"]["pollIntervalSeconds"], 8)
    self.assertEqual(env_cfg["state"]["completedCardAction"], "archive")
    self.assertTrue(
        any(
            "MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION=archive" in ov
            for ov in env_cfg["envOverrides"]
        )
    )

    env_sc_bad = send_rpc(
        p_env,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "workflow_safety_check",
                "arguments": {
                    "commandLine": "psql -h localhost ledger_live",
                    "workspaceDir": env_cwd,
                },
            },
        },
    )
    self.assertIs(
        json.loads(env_sc_bad["result"]["content"][0]["text"])["safe"], False
    )

    env_sc_good = send_rpc(
        p_env,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "workflow_safety_check",
                "arguments": {
                    "commandLine": (
                        "psql -h localhost ledger_test -c 'SELECT prod FROM tx'"
                    ),
                    "workspaceDir": env_cwd,
                },
            },
        },
    )
    self.assertIs(
        json.loads(env_sc_good["result"]["content"][0]["text"])["safe"], True
    )

    # 10. Single-item partial config override & workflow_poll_ci tests
    partial_ws = _make_temp_dir(self)
    os.makedirs(os.path.join(partial_ws, ".workflow"))
    with open(
        os.path.join(partial_ws, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump({"ci": {"quiescenceSeconds": 20}}, f)

    p_partial = _start_mcp_server(self, partial_ws)
    send_rpc(
        p_partial,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    partial_cfg_resp = send_rpc(
        p_partial,
        {
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": partial_ws},
            },
        },
    )
    partial_cfg = json.loads(partial_cfg_resp["result"]["content"][0]["text"])
    self.assertEqual(partial_cfg["ci"]["quiescenceSeconds"], 20)
    self.assertEqual(
        partial_cfg["ci"]["pollIntervalSeconds"],
        30,
        f"Expected default 30s poll interval, got {partial_cfg['ci']['pollIntervalSeconds']}",
    )
    self.assertEqual(partial_cfg["models"]["adversarial-critic"], "inherit")
    self.assertIs(partial_cfg["review"]["strictSixPillars"], True)

    # Test workflow_poll_ci in pure git / missing PR fallback mode
    poll_resp = send_rpc(
        p_partial,
        {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "tools/call",
            "params": {
                "name": "workflow_poll_ci",
                "arguments": {
                    "prNumber": 0,
                    "workspaceDir": partial_ws,
                },
            },
        },
    )
    poll_data = json.loads(poll_resp["result"]["content"][0]["text"])
    self.assertIn("ciStatus", poll_data)
    self.assertIn("quiescenceSatisfied", poll_data)
    self.assertIn("readyToLand", poll_data)
    self.assertIn("unresolvedBotCommentsCount", poll_data)
    self.assertEqual(poll_data["mode"], "pure_git")

  def test_poll_ci_status_in_memory_branches(self):
    """Checks poll_ci_status GitHub PR parsing and quiescence branches in memory."""
    from lib.ci_polling import poll_ci_status

    mock_data_bot = {
        "statusCheckRollup": [{
            "name": "ci",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "completedAt": "2026-11-01T08:00:00Z",
        }],
        "comments": [{
            "author": {"login": "coderabbitai[bot]"},
            "body": "Consider refactoring",
            "createdAt": "2026-11-01T08:01:00Z",
        }],
        "reviews": [],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run"
    ) as mock_run:
      mock_run.return_value = mock.MagicMock(
          stdout=json.dumps(mock_data_bot), stderr="", returncode=0
      )
      res = poll_ci_status(
          ws=REPO_ROOT,
          pr_number=123,
          quiescence_sec=10,
          max_wait_sec=1,
          hosting_mode="github",
      )
      self.assertEqual(res["ciStatus"], "SUCCESS")
      self.assertIs(res["readyToLand"], False)
      self.assertEqual(res["unresolvedBotCommentsCount"], 1)

    mock_data_clean = {
        "statusCheckRollup": [{
            "name": "ci",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "completedAt": "2026-11-01T07:00:00Z",
        }],
        "comments": [],
        "reviews": [],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run"
    ) as mock_run:
      mock_run.return_value = mock.MagicMock(
          stdout=json.dumps(mock_data_clean), stderr="", returncode=0
      )
      res = poll_ci_status(
          ws=REPO_ROOT,
          pr_number=123,
          quiescence_sec=10,
          max_wait_sec=1,
          hosting_mode="github",
      )
      self.assertEqual(res["ciStatus"], "SUCCESS")
      self.assertIs(res["readyToLand"], True)
      self.assertEqual(res["unresolvedBotCommentsCount"], 0)

    mock_data_fail = {
        "statusCheckRollup": [
            {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}
        ],
        "comments": [],
        "reviews": [],
    }
    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run"
    ) as mock_run:
      mock_run.return_value = mock.MagicMock(
          stdout=json.dumps(mock_data_fail), stderr="", returncode=0
      )
      res = poll_ci_status(
          ws=REPO_ROOT,
          pr_number=123,
          quiescence_sec=10,
          max_wait_sec=1,
          hosting_mode="github",
      )
      self.assertEqual(res["ciStatus"], "FAILURE")
      self.assertEqual(res["failedChecks"], ["lint"])

  def test_plugin_directory_isolation_and_state_cards(self):
    """Checks plugin directory isolation and GH-74 workflow_get_state empty/complete cards."""
    mock_plugin_dir = _make_temp_dir(self)
    ws_dir = _make_temp_dir(self)
    with open(
        os.path.join(mock_plugin_dir, "plugin.json"), "w", encoding="utf-8"
    ) as f:
      json.dump({"name": "makeflow"}, f)
    plugin_state = os.path.join(mock_plugin_dir, ".workflow", "state")
    os.makedirs(plugin_state)
    with open(
        os.path.join(plugin_state, "10_ticket.md"), "w", encoding="utf-8"
    ) as f:
      f.write("# Ticket\n- Ticket ID: GH-999\n")

    p_iso = _start_mcp_server(self, mock_plugin_dir)
    send_rpc(
        p_iso, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )

    # Calling workflow_get_state with {} in plugin dir returns error or does not leak plugin cards
    resp_empty = send_rpc(
        p_iso,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_state",
                "arguments": {},
            },
        },
    )
    res_obj = resp_empty.get("result", {})
    text_out = res_obj.get("content", [{}])[0].get("text", "")
    self.assertNotIn("GH-999", text_out, f"Leaked plugin cards: {text_out}")
    self.assertTrue(
        res_obj.get("isError") is True
        or "does not exist" in text_out
        or "BLOCKED" in text_out
    )

    # Calling workflow_get_state with workspaceDir pointing to pristine workspace
    resp_ws = send_rpc(
        p_iso,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_state",
                "arguments": {"workspaceDir": ws_dir},
            },
        },
    )
    text_ws = resp_ws.get("result", {}).get("content", [{}])[0].get("text", "")
    self.assertNotIn("GH-999", text_ws)
    self.assertIn("does not exist", text_ws)

    # GH-74: workflow_get_state on empty state directory
    empty_st = os.path.join(ws_dir, ".workflow", "state")
    os.makedirs(empty_st)
    resp_empty_st = send_rpc(
        p_iso,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_state",
                "arguments": {"workspaceDir": ws_dir},
            },
        },
    )
    text_empty_st = (
        resp_empty_st.get("result", {}).get("content", [{}])[0].get("text", "")
    )
    self.assertIn("clean (0 state cards)", text_empty_st)
    self.assertIn("Ready for Target 10 intake", text_empty_st)

    # GH-74: workflow_get_state on completed ticket
    with open(
        os.path.join(empty_st, "10_ticket.md"), "w", encoding="utf-8"
    ) as f:
      f.write("# Ticket Intake: GH-20\n")
    with open(
        os.path.join(empty_st, "99_next_action.md"), "w", encoding="utf-8"
    ) as f:
      f.write(
          "# Target 90 Finished: COMPLETE\n- Status: COMPLETE (make finish satisfied)\n"
      )
    resp_comp_st = send_rpc(
        p_iso,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_state",
                "arguments": {"workspaceDir": ws_dir},
            },
        },
    )
    text_comp_st = (
        resp_comp_st.get("result", {}).get("content", [{}])[0].get("text", "")
    )
    self.assertIn("Previous ticket is complete", text_comp_st)
    self.assertIn("Ready for Target 10 intake", text_comp_st)

  def test_detect_hosting_mode_and_pure_git_poll_ci(self):
    """Checks detect_hosting_mode branches and poll_ci_status pure_git mode (GH-76)."""
    from lib.ci_polling import detect_hosting_mode, poll_ci_status

    # Case a: No remote (or git remote get-url origin returns non-zero) -> "pure_git"
    with mock.patch("subprocess.run") as mock_run:
      mock_run.return_value = mock.MagicMock(
          returncode=128, stdout="", stderr="fatal: No such remote 'origin'"
      )
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    # Case b: Non-GitHub remote -> "pure_git"
    for non_gh_url in (
        "git@gitlab.com:org/repo.git",
        "https://gitlab.com/org/repo.git",
        "https://bitbucket.org/org/repo.git",
        "file:///local/path/repo.git",
    ):
      with self.subTest(non_gh_url=non_gh_url):
        with mock.patch("subprocess.run") as mock_run:
          mock_run.return_value = mock.MagicMock(
              returncode=0, stdout=non_gh_url + "\n", stderr=""
          )
          self.assertEqual(
              detect_hosting_mode("/fake/repo"),
              "pure_git",
              f"Failed for {non_gh_url}",
          )

    # Case c: GitHub remote, but gh binary missing -> "pure_git"
    with mock.patch("subprocess.run") as mock_run, mock.patch(
        "shutil.which", return_value=None
    ):
      mock_run.return_value = mock.MagicMock(
          returncode=0, stdout="git@github.com:org/repo.git\n", stderr=""
      )
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    # Case d: GitHub remote, gh present, but gh auth status non-zero -> "pure_git"
    def mock_subp_auth_fail(cmd, **kwargs):
      if cmd[:3] == ["git", "remote", "get-url"]:
        return mock.MagicMock(
            returncode=0, stdout="https://github.com/org/repo.git\n", stderr=""
        )
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(
            returncode=1,
            stdout="",
            stderr="You are not logged into any GitHub hosts",
        )
      return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=mock_subp_auth_fail
    ):
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    # Case e: GitHub remote, gh auth status 0, but gh repo view non-zero -> "pure_git"
    def mock_subp_repo_fail(cmd, **kwargs):
      if cmd[:3] == ["git", "remote", "get-url"]:
        return mock.MagicMock(
            returncode=0, stdout="https://github.com/org/repo.git\n", stderr=""
        )
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(
            returncode=0, stdout="Logged in to github.com\n", stderr=""
        )
      if cmd[:3] == ["gh", "repo", "view"]:
        return mock.MagicMock(
            returncode=1,
            stdout="",
            stderr="GraphQL: Could not resolve to a Repository",
        )
      return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=mock_subp_repo_fail
    ):
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    # Case f: GitHub remote, gh auth status 0, and gh repo view 0 -> "github"
    def mock_subp_all_pass(cmd, **kwargs):
      if cmd[:3] == ["git", "remote", "get-url"]:
        return mock.MagicMock(
            returncode=0, stdout="https://github.com/org/repo.git\n", stderr=""
        )
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(
            returncode=0, stdout="Logged in to github.com\n", stderr=""
        )
      if cmd[:3] == ["gh", "repo", "view"]:
        return mock.MagicMock(
            returncode=0, stdout='{"name": "repo"}', stderr=""
        )
      return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=mock_subp_all_pass
    ):
      self.assertEqual(detect_hosting_mode("/fake/repo"), "github")

    # Case g: Integration test in poll_ci_status: pure_git returns mode: "pure_git"
    with mock.patch("lib.ci_polling.detect_hosting_mode", return_value="pure_git"):
      res_pure = poll_ci_status(
          pr_number=99, ws="/fake/repo", quiescence_sec=10, max_wait_sec=1
      )
      self.assertEqual(res_pure["mode"], "pure_git")
      self.assertEqual(res_pure["ciStatus"], "SKIPPED")
      self.assertIs(res_pure["readyToLand"], True)
      self.assertIn("Pure Git Mode", res_pure["message"])


class TestMcpServerScarImporterAndSimpleEnglish(unittest.TestCase):
  """Tests synthetic-scar-importer config and Simple English workflow_query_scars (GH-142)."""

  def test_scar_importer_config_and_simple_english_query(self):
    """Checks synthetic-scar-importer config overrides and Simple English query fields."""
    tmp_dir = _make_temp_dir(self)
    p = _start_mcp_server(self, tmp_dir)
    send_rpc(
        p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    # 1. Default config includes synthetic-scar-importer == "inherit"
    empty_ws = os.path.join(tmp_dir, "empty_ws")
    os.makedirs(empty_ws, exist_ok=True)
    cfg_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": empty_ws},
            },
        },
    )
    cfg_data = json.loads(cfg_resp["result"]["content"][0]["text"])
    self.assertEqual(
        cfg_data["models"].get("synthetic-scar-importer"), "inherit"
    )
    self.assertEqual(
        cfg_data["agyModelTiers"].get("synthetic-scar-importer"), "inherit"
    )

    # 2. Workspace .workflow/config.json overrides synthetic-scar-importer
    custom_ws = os.path.join(tmp_dir, "custom_ws")
    os.makedirs(os.path.join(custom_ws, ".workflow"), exist_ok=True)
    with open(
        os.path.join(custom_ws, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump({"models": {"synthetic-scar-importer": "flash"}}, f)
    ws_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": custom_ws},
            },
        },
    )
    ws_data = json.loads(ws_resp["result"]["content"][0]["text"])
    self.assertEqual(
        ws_data["models"].get("synthetic-scar-importer"), "flash"
    )
    self.assertEqual(
        ws_data["agyModelTiers"].get("synthetic-scar-importer"), "flash"
    )

    # 3. workflow_query_scars returns Simple English transformed fields and socraticProbe
    se_db = os.path.join(tmp_dir, "se_scars.db")
    conn = sqlite3.connect(se_db)
    try:
      conn.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              raw_wound TEXT,
              raw_trap TEXT,
              raw_circuit_breaker TEXT,
              raw_reflex TEXT,
              raw_hash TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT,
              source TEXT DEFAULT 'upstream',
              original_id TEXT
          )
          """
      )
      conn.execute(
          """
          CREATE VIRTUAL TABLE scars_fts USING fts5(
              id UNINDEXED,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              socratic_probe,
              source UNINDEXED,
              original_id UNINDEXED
          )
          """
      )
      conn.execute(
          "INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          (
              "SCAR-SE-01",
              "Simple English Query Test",
              "Architecture",
              "architecture",
              "public",
              json.dumps(["T20"]),
              "Testing Simple English MCP query",
              "graduated",
              "Verbose raw wound.",
              "Verbose raw trap.",
              "Verbose raw circuit breaker.",
              "Verbose raw reflex.",
              "hash-se-01",
              "Clear Simple English wound.",
              "Clear Simple English trap.",
              "If check fails, stop.",
              "Run the test first.",
              "Did you run the test before editing code?",
              "upstream",
              None,
          ),
      )
      conn.execute(
          "INSERT INTO scars_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          (
              "SCAR-SE-01",
              "Simple English Query Test",
              "Architecture",
              "Testing Simple English MCP query",
              "Clear Simple English wound.",
              "Clear Simple English trap.",
              "Run the test first.",
              "Did you run the test before editing code?",
              "upstream",
              None,
          ),
      )
      conn.commit()
    finally:
      conn.close()

    q_resp = send_rpc(
        p,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "workflow_query_scars",
                "arguments": {"query": "Simple English", "dbPath": se_db},
            },
        },
    )
    q_data = json.loads(q_resp["result"]["content"][0]["text"])
    self.assertEqual(q_data["status"], "OK")
    self.assertEqual(len(q_data["scars"]), 1)
    scar = q_data["scars"][0]
    self.assertEqual(scar["wound"], "Clear Simple English wound.")
    self.assertEqual(scar["trap"], "Clear Simple English trap.")
    self.assertEqual(scar["circuitBreaker"], "If check fails, stop.")
    self.assertEqual(scar["reflex"], "Run the test first.")
    self.assertEqual(
        scar["socraticProbe"],
        "Did you run the test before editing code?",
    )

    # 4. Environment variable MAKEFILE_WORKFLOW_SCAR_IMPORTER_MODEL override
    custom_env = dict(os.environ)
    custom_env["MAKEFILE_WORKFLOW_SCAR_IMPORTER_MODEL"] = "flash_lite"
    p_env = _start_mcp_server(self, tmp_dir, env=custom_env)
    send_rpc(
        p_env,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    env_resp = send_rpc(
        p_env,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": empty_ws},
            },
        },
    )
    env_data = json.loads(env_resp["result"]["content"][0]["text"])
    self.assertEqual(
        env_data["models"].get("synthetic-scar-importer"), "flash_lite"
    )
    self.assertEqual(
        env_data["agyModelTiers"].get("synthetic-scar-importer"),
        "flash_lite",
    )


class TestGateEvidenceKeysEnumeration(unittest.TestCase):
  """Tests schema bijection, runtime enforcement, and reference coverage for GATE_EVIDENCE_KEYS (GH-154)."""

  def test_gate_evidence_keys_schema_runtime_and_docs(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from mcp.server import (
        GATE_EVIDENCE_KEYS,
        handle_tools_call,
        handle_tools_list,
    )

    expected_keys = {
        "humanPlanApproved",
        "humanBlockerTriageApproved",
        "humanReviewApproved",
        "humanCommitApproved",
        "humanPushApproved",
        "humanLandingApproved",
    }
    self.assertEqual(set(GATE_EVIDENCE_KEYS.values()), expected_keys)

    # 1. Schema Bijection: workflow_advance_target evidence description lists all 6 keys and blockersCount
    tools_resp = handle_tools_list(1)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    adv_tool = tools["workflow_advance_target"]
    evidence_desc = adv_tool["inputSchema"]["properties"]["evidence"][
        "description"
    ]
    for key in sorted(expected_keys) + ["blockersCount"]:
      self.assertIn(
          key,
          evidence_desc,
          f"Expected canonical evidence key '{key}' in workflow_advance_target schema description: {evidence_desc}",
      )

    # 2. Runtime Gate Enforcement Across All 6 Transitions
    for (current_t, next_t), gate_key in GATE_EVIDENCE_KEYS.items():
      for bad_evidence in ({}, {gate_key: False}):
        resp_blocked = handle_tools_call(
            1,
            "workflow_advance_target",
            {
                "currentTarget": current_t,
                "nextTarget": next_t,
                "evidence": bad_evidence,
            },
        )
        self.assertTrue(
            resp_blocked["result"].get("isError"),
            f"Expected {current_t} -> {next_t} with evidence={bad_evidence} to fail",
        )
        err_text = resp_blocked["result"]["content"][0]["text"]
        self.assertIn(gate_key, err_text)

      resp_ok = handle_tools_call(
          1,
          "workflow_advance_target",
          {
              "currentTarget": current_t,
              "nextTarget": next_t,
              "evidence": {gate_key: True},
          },
      )
      self.assertFalse(
          resp_ok["result"].get("isError"),
          f"Expected {current_t} -> {next_t} with {gate_key}=True to succeed, got {resp_ok}",
      )

    # 3. Reference Documentation Enumeration
    refs_dir = os.path.join(REPO_ROOT, "skills", "makeflow", "references")
    quickstart_path = os.path.join(
        refs_dir, "00_quickstart_and_mental_model.md"
    )
    with open(quickstart_path, "r", encoding="utf-8") as f:
      quickstart_text = f.read()
    for key in sorted(expected_keys) + ["blockersCount"]:
      self.assertIn(
          key,
          quickstart_text,
          f"Expected '{key}' in 00_quickstart_and_mental_model.md",
      )

    phase_doc_expectations = {
        "02_archaeology_and_planning.md": ("humanPlanApproved",),
        "04_six_pillars_adversarial_review.md": (
            "humanBlockerTriageApproved",
            "humanReviewApproved",
            "blockersCount",
        ),
        "05_pre_commit_hygiene.md": ("humanCommitApproved",),
        "06_clean_commit_and_pr.md": ("humanPushApproved",),
        "07_ci_and_bot_triage.md": ("humanLandingApproved",),
    }
    for doc_name, keys in phase_doc_expectations.items():
      doc_path = os.path.join(refs_dir, doc_name)
      with open(doc_path, "r", encoding="utf-8") as f:
        doc_text = f.read()
      for key in keys:
        self.assertIn(
            key,
            doc_text,
            f"Expected '{key}' in {doc_name}",
        )


class TestMcpServerMultiDimensionalQueryScars(unittest.TestCase):
  """Tests workflow_query_scars multi-dimensional filters and metadata fields (GH-145)."""

  def test_workflow_query_scars_multidimensional_filters(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from lib.scars_db import SyncSyntheticScarsScript, enrich_scars_database
    from mcp.server import handle_tools_call, handle_tools_list

    tools_resp = handle_tools_list(1)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    q_props = tools["workflow_query_scars"]["inputSchema"]["properties"]
    for expected_prop in (
        "query",
        "category",
        "language",
        "framework",
        "diagnosticCode",
        "workflowPhase",
        "limit",
        "source",
        "workspaceDir",
        "dbPath",
    ):
      self.assertIn(expected_prop, q_props)

    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_multidim.db")
      conn = sqlite3.connect(db_path)
      conn.close()

      script = SyncSyntheticScarsScript()
      script.db_path = db_path
      script._init_db_schema()
      cur = script.conn.cursor()
      cur.execute(
          """
          INSERT INTO scars (
              id, title, category, category_slug, visibility,
              workflow_phases, applies_when, graduation_status,
              raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
              wound, trap, circuit_breaker, reflex, socratic_probe,
              source, original_id
          ) VALUES (
              'SCAR-RIV-01',
              'Riverpod Completer Reentrancy Guard',
              'RIV',
              'riv',
              'public',
              '["Target 20: plan", "Target 30: tdd"]',
              'Using Flutter Riverpod with Completer',
              'graduated',
              'Reentrancy deadlock causes `StateError` and `UNDEFINED_GETTER`.',
              'Calling complete twice.',
              'If completed, stop.',
              'Check `!completer.isCompleted` in Dart.',
              'hash-riv-01',
              'Simple wound.',
              'Simple trap.',
              'If completed, stop.',
              'Check completer in Dart.',
              'Did you check isCompleted?',
              'upstream',
              NULL
          )
          """
      )
      cur.execute(
          """
          INSERT INTO scars (
              id, title, category, category_slug, visibility,
              workflow_phases, applies_when, graduation_status,
              raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
              wound, trap, circuit_breaker, reflex, socratic_probe,
              source, original_id
          ) VALUES (
              'SCAR-PY-02',
              'Pytest FastAPI Worker Signal Trap',
              'SYS',
              'sys',
              'public',
              '["Target 30: tdd", "Target 40: review"]',
              'Running Python FastAPI and pytest in Docker with SQLite',
              'graduated',
              'Uncaught `SIGTERM` causes `EADDRINUSE` and `FileExistsError`.',
              'Missing signal trap.',
              '',
              'Trap `SIGTERM` in Python.',
              'hash-py-02',
              'Simple signal wound.',
              'Simple signal trap.',
              '',
              'Trap SIGTERM in Python.',
              'Did you trap SIGTERM?',
              'upstream',
              NULL
          )
          """
      )
      script.conn.commit()
      script.cleanup(0)

      enrich_scars_database(db_path)

      # 1. Exact ID query via FTS5
      id_resp = handle_tools_call(
          10,
          "workflow_query_scars",
          {"query": "SCAR-RIV-01", "dbPath": db_path},
      )
      self.assertFalse(id_resp["result"].get("isError"))
      id_data = json.loads(id_resp["result"]["content"][0]["text"])
      self.assertEqual(id_data["status"], "OK")
      self.assertEqual(id_data["scars"][0]["id"], "SCAR-RIV-01")
      for meta_field in (
          "keywords",
          "languages",
          "frameworks",
          "diagnosticCodes",
          "diagnostic_codes",
          "workflowPhases",
          "workflow_phases",
      ):
        self.assertIn(meta_field, id_data["scars"][0])

      # 2. Filter by framework
      fw_resp = handle_tools_call(
          11,
          "workflow_query_scars",
          {"query": "Completer", "framework": "riverpod", "dbPath": db_path},
      )
      fw_data = json.loads(fw_resp["result"]["content"][0]["text"])
      self.assertEqual(fw_data["totalResults"], 1)
      self.assertEqual(fw_data["scars"][0]["id"], "SCAR-RIV-01")

      # 3. Filter by diagnosticCode
      diag_resp = handle_tools_call(
          12,
          "workflow_query_scars",
          {"query": "SIGTERM", "diagnosticCode": "EADDRINUSE", "dbPath": db_path},
      )
      diag_data = json.loads(diag_resp["result"]["content"][0]["text"])
      self.assertEqual(diag_data["totalResults"], 1)
      self.assertEqual(diag_data["scars"][0]["id"], "SCAR-PY-02")

      # 4. Filter by workflowPhase
      wp_resp = handle_tools_call(
          13,
          "workflow_query_scars",
          {
              "query": "",
              "workflowPhase": "Target 40: review",
              "dbPath": db_path,
          },
      )
      wp_data = json.loads(wp_resp["result"]["content"][0]["text"])
      self.assertEqual(wp_data["totalResults"], 1)
      self.assertEqual(wp_data["scars"][0]["id"], "SCAR-PY-02")

      wp_short_resp = handle_tools_call(
          131,
          "workflow_query_scars",
          {
              "query": "",
              "workflowPhase": "T40",
              "dbPath": db_path,
          },
      )
      wp_short_data = json.loads(wp_short_resp["result"]["content"][0]["text"])
      self.assertEqual(wp_short_data["totalResults"], 1)
      self.assertEqual(wp_short_data["scars"][0]["id"], "SCAR-PY-02")

      wp_neg_resp = handle_tools_call(
          132,
          "workflow_query_scars",
          {
              "query": "Python",
              "workflowPhase": "Target 99: nonexistent",
              "dbPath": db_path,
          },
      )
      wp_neg_data = json.loads(wp_neg_resp["result"]["content"][0]["text"])
      self.assertEqual(wp_neg_data["totalResults"], 0)
      self.assertEqual(wp_neg_data["scars"], [])

      # 5. Filter-only query (omitting query)
      filter_only_resp = handle_tools_call(
          14,
          "workflow_query_scars",
          {"framework": "flutter", "dbPath": db_path},
      )
      filter_only_data = json.loads(
          filter_only_resp["result"]["content"][0]["text"]
      )
      self.assertEqual(filter_only_data["totalResults"], 1)
      self.assertEqual(filter_only_data["scars"][0]["id"], "SCAR-RIV-01")


class TestMcpServerOntologyAndMfTarget(unittest.TestCase):
  """Tests GH-193 mfTarget MCP schema/filter and ssId/ssTargets/mfTargets fields."""

  def test_workflow_query_scars_mf_target_and_ss_id_fields(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from lib.scars_db import SyncSyntheticScarsScript, enrich_scars_database
    from mcp.server import handle_tools_call, handle_tools_list

    tools_resp = handle_tools_list(1)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    q_props = tools["workflow_query_scars"]["inputSchema"]["properties"]
    self.assertIn("mfTarget", q_props)
    self.assertEqual(q_props["mfTarget"]["type"], "integer")

    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_ontology.db")
      script = SyncSyntheticScarsScript()
      script.db_path = db_path
      script._init_db_schema()
      cur = script.conn.cursor()
      cur.execute(
          """
          INSERT INTO scars (
              id, ss_id, title, category, category_slug, visibility,
              workflow_phases, ss_targets, mf_targets, applies_when,
              graduation_status, raw_wound, raw_trap, raw_circuit_breaker,
              raw_reflex, raw_hash, wound, trap, circuit_breaker, reflex,
              socratic_probe, source, original_id
          ) VALUES (
              'SCAR-LANG-DART-ARCH-0005',
              'SCAR-ARCH-05',
              'Layer Boundary Guard',
              'ARCH',
              'arch',
              'public',
              '["Target 15: archaeology", "Target 20: plan", "Target 30: tdd", "Target 40: review"]',
              '["Target 3", "Target 4"]',
              '[15, 20, 30, 40]',
              'Writing Dart architecture boundaries',
              'graduated',
              'Raw wound.',
              'Raw trap.',
              'If boundary breaks, stop.',
              'Check imports in Dart.',
              'hash-arch-0005',
              'Simple wound.',
              'Simple trap.',
              'If boundary breaks, stop.',
              'Check imports in Dart.',
              'Did you check the boundary?',
              'upstream',
              NULL
          )
          """
      )
      cur.execute(
          """
          INSERT INTO scars (
              id, ss_id, title, category, category_slug, visibility,
              workflow_phases, ss_targets, mf_targets, applies_when,
              graduation_status, raw_wound, raw_trap, raw_circuit_breaker,
              raw_reflex, raw_hash, wound, trap, circuit_breaker, reflex,
              socratic_probe, source, original_id
          ) VALUES (
              'SCAR-LANG-PYTHON-STATE-0002',
              'SCAR-RIV-02',
              'Isolated State Mutation Guard',
              'STATE',
              'state',
              'public',
              '["Target 20: plan", "Target 30: tdd"]',
              '["Target 2", "Target 3"]',
              '[20, 30]',
              'Managing Python state stores',
              'graduated',
              'Raw wound.',
              'Raw trap.',
              'If state mutates, stop.',
              'Keep state pure in Python.',
              'hash-state-0002',
              'Simple wound.',
              'Simple trap.',
              'If state mutates, stop.',
              'Keep state pure in Python.',
              'Did you keep state pure?',
              'upstream',
              NULL
          )
          """
      )
      script.conn.commit()
      script.conn.close()

      enrich_scars_database(db_path)

      resp_t15 = handle_tools_call(
          301,
          "workflow_query_scars",
          {"query": "", "mfTarget": 15, "dbPath": db_path},
      )
      data_t15 = json.loads(resp_t15["result"]["content"][0]["text"])
      self.assertEqual(data_t15["totalResults"], 1)
      scar_t15 = data_t15["scars"][0]
      self.assertEqual(scar_t15["id"], "SCAR-LANG-DART-ARCH-0005")
      self.assertEqual(scar_t15["ssId"], "SCAR-ARCH-05")
      self.assertEqual(scar_t15["ssTargets"], ["Target 3", "Target 4"])
      self.assertEqual(scar_t15["mfTargets"], [15, 20, 30, 40])

      resp_t20 = handle_tools_call(
          302,
          "workflow_query_scars",
          {
              "query": "",
              "mfTarget": 20,
              "limit": 5,
              "refreshLoaded": True,
              "dbPath": db_path,
          },
      )
      data_t20 = json.loads(resp_t20["result"]["content"][0]["text"])
      self.assertEqual(data_t20["totalResults"], 2)

      resp_ssid = handle_tools_call(
          303,
          "workflow_query_scars",
          {"query": "SCAR-ARCH-05", "refreshLoaded": True, "dbPath": db_path},
      )
      data_ssid = json.loads(resp_ssid["result"]["content"][0]["text"])
      self.assertEqual(data_ssid["totalResults"], 1)
      self.assertEqual(data_ssid["scars"][0]["id"], "SCAR-LANG-DART-ARCH-0005")

      resp_t75 = handle_tools_call(
          304,
          "workflow_query_scars",
          {"query": "", "mfTarget": 75, "dbPath": db_path},
      )
      data_t75 = json.loads(resp_t75["result"]["content"][0]["text"])
      self.assertEqual(data_t75["totalResults"], 0)


class TestMcpPauseGateTurnBoundary(unittest.TestCase):
  """Tests server-side pause-gate entry recording and same-turn chaining block (GH-186)."""

  def test_mcp_pause_gate_entry_and_same_turn_chaining_blocked(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from mcp.server import handle_tools_call

    with tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      for card in (
          "20_plan_approved.md",
          "45_review_approved.md",
          "70_bot_triage.md",
      ):
        with open(os.path.join(state_dir, card), "w", encoding="utf-8") as f:
          f.write("# Card\n")

      # 1. Enter T75 from T70 -> writes gate_entry.json
      enter_t75 = handle_tools_call(
          301,
          "workflow_advance_target",
          {
              "currentTarget": "T70",
              "nextTarget": "T75",
              "workspaceDir": ws_dir,
          },
      )
      self.assertFalse(enter_t75["result"].get("isError"))
      gate_entry_path = os.path.join(state_dir, "gate_entry.json")
      self.assertTrue(os.path.isfile(gate_entry_path))
      with open(gate_entry_path, "r", encoding="utf-8") as f:
        entry_data = json.load(f)
      self.assertEqual(entry_data["gate"], "T75")
      self.assertFalse(entry_data["humanTurnVerified"])

      # 2. Immediately chain T75 -> T80 in the same turn -> BLOCKED (SCAR-PROC-102)
      chain_t80 = handle_tools_call(
          302,
          "workflow_advance_target",
          {
              "currentTarget": "T75",
              "nextTarget": "T80",
              "workspaceDir": ws_dir,
              "evidence": {"humanLandingApproved": True},
          },
      )
      self.assertTrue(chain_t80["result"].get("isError"))
      self.assertIn(
          "SCAR-PROC-102", chain_t80["result"]["content"][0]["text"]
      )

      # 3. Provide transcript with an intervening USER_INPUT step -> ALLOWED & clears gate_entry.json
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps({
                "step_index": 10,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{
                    "name": "call_mcp_tool",
                    "args": {
                        "ToolName": "workflow_advance_target",
                        "Arguments": {
                            "currentTarget": "T70",
                            "nextTarget": "T75",
                        },
                    },
                }],
            })
            + "\n"
        )
        f.write(
            json.dumps({
                "step_index": 11,
                "type": "USER_INPUT",
                "content": "Approved. Merge it.",
            })
            + "\n"
        )

      ok_t80 = handle_tools_call(
          303,
          "workflow_advance_target",
          {
              "currentTarget": "T75",
              "nextTarget": "T80",
              "workspaceDir": ws_dir,
              "transcriptPath": transcript_path,
              "evidence": {"humanLandingApproved": True},
          },
      )
      self.assertFalse(ok_t80["result"].get("isError"))
      self.assertFalse(os.path.exists(gate_entry_path))

      # 4. Also verify T40 -> T45 followed immediately by T45 -> T50 is blocked
      enter_t45 = handle_tools_call(
          304,
          "workflow_advance_target",
          {
              "currentTarget": "T40",
              "nextTarget": "T45",
              "workspaceDir": ws_dir,
              "evidence": {"blockersCount": 0},
          },
      )
      self.assertFalse(enter_t45["result"].get("isError"))

      chain_t50 = handle_tools_call(
          305,
          "workflow_advance_target",
          {
              "currentTarget": "T45",
              "nextTarget": "T50",
              "workspaceDir": ws_dir,
              "evidence": {"humanReviewApproved": True},
          },
      )
      self.assertTrue(chain_t50["result"].get("isError"))
      self.assertIn(
          "SCAR-PROC-102", chain_t50["result"]["content"][0]["text"]
      )

      # 5. Also verify T15 -> T20 followed immediately by T20 -> T30 is blocked
      enter_t20 = handle_tools_call(
          306,
          "workflow_advance_target",
          {
              "currentTarget": "T15",
              "nextTarget": "T20",
              "workspaceDir": ws_dir,
          },
      )
      self.assertFalse(enter_t20["result"].get("isError"))

      chain_t30 = handle_tools_call(
          307,
          "workflow_advance_target",
          {
              "currentTarget": "T20",
              "nextTarget": "T30",
              "workspaceDir": ws_dir,
              "evidence": {"humanPlanApproved": True},
          },
      )
      self.assertTrue(chain_t30["result"].get("isError"))
      self.assertIn(
          "SCAR-PROC-102", chain_t30["result"]["content"][0]["text"]
      )

  def test_mcp_declarative_recovery_transitions_and_gate_entry_cleanup(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from mcp.server import handle_tools_call

    recovery_edges = [
        ("T20", "T15"),
        ("T30", "T15"),
        ("T42", "T20"),
        ("T42", "T15"),
        ("T45", "T30"),
        ("T50", "T30"),
        ("T60", "T30"),
        ("T60", "T40"),
        ("T75", "T30"),
        ("T75", "T40"),
        ("T75", "T70"),
        ("T80", "T30"),
        ("T80", "T40"),
        ("T80", "T60"),
        ("T80", "T70"),
    ]
    for curr_t, rec_t in recovery_edges:
      resp = handle_tools_call(
          400,
          "workflow_advance_target",
          {"currentTarget": curr_t, "nextTarget": rec_t},
      )
      self.assertFalse(
          resp["result"].get("isError"),
          f"Expected recovery transition {curr_t} -> {rec_t} to succeed, got {resp}",
      )

    # Verify recovering out of a pause gate clears gate_entry.json
    with tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "70_bot_triage.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Triage\n")

      handle_tools_call(
          401,
          "workflow_advance_target",
          {
              "currentTarget": "T70",
              "nextTarget": "T75",
              "workspaceDir": ws_dir,
          },
      )
      gate_entry_path = os.path.join(state_dir, "gate_entry.json")
      self.assertTrue(os.path.isfile(gate_entry_path))

      rec_resp = handle_tools_call(
          402,
          "workflow_advance_target",
          {
              "currentTarget": "T75",
              "nextTarget": "T30",
              "workspaceDir": ws_dir,
          },
      )
      self.assertFalse(rec_resp["result"].get("isError"))
      self.assertFalse(os.path.exists(gate_entry_path))


class TestMcpMainSyncTransitions(unittest.TestCase):
  """Tests upstream origin/main sync and conflict checks in workflow_advance_target and workflow_get_state (GH-195)."""

  def _init_repo_with_bare_origin(self, base_dir: str):
    bare_dir = os.path.join(base_dir, "origin.git")
    clone1 = os.path.join(base_dir, "clone1")
    clone2 = os.path.join(base_dir, "clone2")
    os.makedirs(bare_dir, exist_ok=True)
    os.makedirs(clone1, exist_ok=True)

    subprocess.run(
        ["git", "init", "--bare", "-q"],
        cwd=bare_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "init", "-q"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Makeflow Test"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    with open(os.path.join(clone1, "shared.txt"), "w", encoding="utf-8") as f:
      f.write("initial content\n")
    subprocess.run(
        ["git", "add", "shared.txt"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "chore: initial commit"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", bare_dir],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "checkout", "-q", "-b", "feat/195-early-sync"],
        cwd=clone1,
        check=True,
        capture_output=True,
        text=True,
    )
    exclude_path = os.path.join(clone1, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
    with open(exclude_path, "a", encoding="utf-8") as f:
      f.write("\n.workflow/state/\n.workflow/archive/\n")

    subprocess.run(
        ["git", "clone", "-q", "-b", "main", bare_dir, clone2],
        cwd=base_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "clone2@example.com"],
        cwd=clone2,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Clone Two"],
        cwd=clone2,
        check=True,
        capture_output=True,
        text=True,
    )
    return clone1, clone2

  def test_mcp_main_sync_behind_clean_and_conflict_transitions(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from mcp.server import handle_tools_call

    with tempfile.TemporaryDirectory() as base_dir:
      clone1, clone2 = self._init_repo_with_bare_origin(base_dir)
      state_dir = os.path.join(clone1, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      for card in (
          "10_ticket.md",
          "15_archaeology_receipt.md",
          "20_plan_approved.md",
          "40_critic_review.md",
          "45_review_approved.md",
          "70_bot_triage.md",
      ):
        with open(os.path.join(state_dir, card), "w", encoding="utf-8") as f:
          f.write(f"# {card}\n")

      # 1. Advance origin/main with a non-conflicting file in clone2
      with open(os.path.join(clone2, "other.txt"), "w", encoding="utf-8") as f:
        f.write("clean upstream update\n")
      subprocess.run(
          ["git", "add", "other.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: add other.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )

      # Pre-review transition T20 -> T30 succeeds and advises rebase before T40
      resp_t20_t30 = handle_tools_call(
          501,
          "workflow_advance_target",
          {
              "currentTarget": "T20",
              "nextTarget": "T30",
              "workspaceDir": clone1,
              "evidence": {"humanPlanApproved": True},
          },
      )
      self.assertFalse(resp_t20_t30["result"].get("isError"))
      text_t20_t30 = resp_t20_t30["result"]["content"][0]["text"]
      self.assertIn("MAIN_SYNC_NOTICE", text_t20_t30)
      self.assertIn("git rebase origin/main", text_t20_t30)

      # Locked-tree transition T40 -> T45 succeeds and warns about approved_tree.txt lock
      resp_t40_t45 = handle_tools_call(
          502,
          "workflow_advance_target",
          {
              "currentTarget": "T40",
              "nextTarget": "T45",
              "workspaceDir": clone1,
              "evidence": {"blockersCount": 0, "skipMainFetch": True},
          },
      )
      self.assertFalse(resp_t40_t45["result"].get("isError"))
      text_t40_t45 = resp_t40_t45["result"]["content"][0]["text"]
      self.assertIn("MAIN_SYNC_NOTICE", text_t40_t45)
      self.assertIn("approved_tree.txt", text_t40_t45)

      # Open PR transition T70 -> T75 succeeds and cites SCAR-GIT-18 against rebasing open PRs
      resp_t70_t75 = handle_tools_call(
          503,
          "workflow_advance_target",
          {
              "currentTarget": "T70",
              "nextTarget": "T75",
              "workspaceDir": clone1,
              "evidence": {"skipMainFetch": True},
          },
      )
      self.assertFalse(resp_t70_t75["result"].get("isError"))
      text_t70_t75 = resp_t70_t75["result"]["content"][0]["text"]
      self.assertIn("MAIN_SYNC_NOTICE", text_t70_t75)
      self.assertIn("SCAR-GIT-18", text_t70_t75)

      # workflow_get_state reports MAIN_SYNC: BEHIND_CLEAN
      state_behind = handle_tools_call(
          504,
          "workflow_get_state",
          {"workspaceDir": clone1},
      )
      self.assertIn(
          "MAIN_SYNC: BEHIND_CLEAN",
          state_behind["result"]["content"][0]["text"],
      )

      # 2. Advance origin/main with a conflicting edit to shared.txt
      with open(os.path.join(clone2, "shared.txt"), "w", encoding="utf-8") as f:
        f.write("upstream conflicting edit\n")
      subprocess.run(
          ["git", "add", "shared.txt"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: upstream shared.txt conflict"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "push", "-q", "origin", "main"],
          cwd=clone2,
          check=True,
          capture_output=True,
          text=True,
      )

      # Uncommitted working-tree edit to shared.txt at T30 -> T40 is BLOCKED
      with open(os.path.join(clone1, "shared.txt"), "w", encoding="utf-8") as f:
        f.write("local uncommitted T30 edit\n")

      resp_t30_t40_blocked = handle_tools_call(
          505,
          "workflow_advance_target",
          {
              "currentTarget": "T30",
              "nextTarget": "T40",
              "workspaceDir": clone1,
          },
      )
      self.assertTrue(resp_t30_t40_blocked["result"].get("isError"))
      err_t30_t40 = resp_t30_t40_blocked["result"]["content"][0]["text"]
      self.assertIn("UPSTREAM_MAIN_CONFLICT", err_t30_t40)
      self.assertIn("shared.txt", err_t30_t40)
      self.assertIn("SCAR-GIT-20", err_t30_t40)
      self.assertIn("SCAR-GIT-25", err_t30_t40)

      # workflow_get_state reports MAIN_SYNC: CONFLICT and shared.txt
      state_conflict = handle_tools_call(
          506,
          "workflow_get_state",
          {"workspaceDir": clone1},
      )
      state_conflict_text = state_conflict["result"]["content"][0]["text"]
      self.assertIn("MAIN_SYNC: CONFLICT", state_conflict_text)
      self.assertIn("shared.txt", state_conflict_text)

      # Commit shared.txt locally and test T50 -> T60 block and allowMainConflict override
      subprocess.run(
          ["git", "add", "shared.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: commit local shared.txt"],
          cwd=clone1,
          check=True,
          capture_output=True,
          text=True,
      )
      gate_entry_file = os.path.join(state_dir, "gate_entry.json")
      if os.path.exists(gate_entry_file):
        os.unlink(gate_entry_file)

      resp_t50_t60_blocked = handle_tools_call(
          507,
          "workflow_advance_target",
          {
              "currentTarget": "T50",
              "nextTarget": "T60",
              "workspaceDir": clone1,
              "evidence": {
                  "humanCommitApproved": True,
                  "skipMainFetch": True,
              },
          },
      )
      self.assertTrue(resp_t50_t60_blocked["result"].get("isError"))
      self.assertIn(
          "UPSTREAM_MAIN_CONFLICT",
          resp_t50_t60_blocked["result"]["content"][0]["text"],
      )

      resp_t50_t60_override = handle_tools_call(
          508,
          "workflow_advance_target",
          {
              "currentTarget": "T50",
              "nextTarget": "T60",
              "workspaceDir": clone1,
              "evidence": {
                  "humanCommitApproved": True,
                  "skipMainFetch": True,
                  "allowMainConflict": True,
              },
          },
      )
      self.assertFalse(resp_t50_t60_override["result"].get("isError"))


def _load_mcp_server_module():
  """Imports mcp.server after adding REPO_ROOT to sys.path.

  Returns:
    Imported mcp.server module.
  """
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  import mcp.server as mcp_server

  return mcp_server


def _build_scars_fixture_db(db_path):
  """Creates a temporary SQLite FTS5 database with one Python Target 30 scar.

  Args:
    db_path: Filesystem path where the temporary fixture DB will be created.
  """
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scars_db import SyncSyntheticScarsScript, enrich_scars_database

  script = SyncSyntheticScarsScript()
  script.db_path = db_path
  script._init_db_schema()
  cur = script.conn.cursor()
  cur.execute(
      """
      INSERT INTO scars (
          id, ss_id, title, category, category_slug, visibility,
          workflow_phases, ss_targets, mf_targets, applies_when,
          graduation_status, raw_wound, raw_trap, raw_circuit_breaker,
          raw_reflex, raw_hash, wound, trap, circuit_breaker, reflex,
          socratic_probe, source, original_id
      ) VALUES (
          'SCAR-LANG-PYTHON-TEST-0099',
          'SCAR-TEST-99',
          'Hermetic JSON-RPC Framing Guard',
          'TEST',
          'test',
          'public',
          '["Target 30: tdd", "Target 40: review"]',
          '["Target 3", "Target 4"]',
          '[30, 40]',
          'Writing Python MCP server tests',
          'graduated',
          'Raw wound.',
          'Raw trap.',
          'If null id frame appears, stop.',
          'Keep JSON-RPC responses correlated in Python.',
          'hash-framing-guard',
          'Simple wound.',
          'Simple trap.',
          'If null id frame appears, stop.',
          'Keep JSON-RPC responses correlated in Python.',
          'Did you verify response id correlation?',
          'upstream',
          NULL
      )
      """
  )
  script.conn.commit()
  script.conn.close()
  enrich_scars_database(db_path)


class TestMcpDualEraProtocolAndScarsSchema(unittest.TestCase):
  """Tests dual-era MCP JSON-RPC framing and scars schema behavior."""

  def _load_mcp_server(self):
    """Imports mcp.server after adding REPO_ROOT to sys.path."""
    return _load_mcp_server_module()

  def _build_scars_fixture_db(self, db_path):
    """Creates a temporary SQLite FTS5 database with one Python Target 30 scar."""
    _build_scars_fixture_db(db_path)

  def test_protocol_constants_and_server_discover(self):
    """Checks SUPPORTED_PROTOCOL_VERSIONS and stateless server/discover."""
    import io

    mcp_server = self._load_mcp_server()
    self.assertTrue(hasattr(mcp_server, "SUPPORTED_PROTOCOL_VERSIONS"))
    self.assertTrue(hasattr(mcp_server, "DEFAULT_INITIALIZE_PROTOCOL_VERSION"))
    self.assertTrue(hasattr(mcp_server, "handle_server_discover"))
    self.assertTrue(hasattr(mcp_server, "dispatch_jsonrpc_line"))

    expected_versions = (
        "2026-07-28",
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    )
    self.assertEqual(mcp_server.SUPPORTED_PROTOCOL_VERSIONS, expected_versions)
    self.assertEqual(
        mcp_server.DEFAULT_INITIALIZE_PROTOCOL_VERSION, "2025-11-25"
    )

    with open(os.path.join(REPO_ROOT, "plugin.json"), encoding="utf-8") as f:
      expected_version = json.load(f)["version"]

    direct = mcp_server.handle_server_discover(101)
    self.assertEqual(direct["id"], 101)
    self.assertEqual(
        direct["result"]["supportedVersions"], list(expected_versions)
    )
    self.assertEqual(direct["result"]["capabilities"], {"tools": {}})
    self.assertEqual(
        direct["result"]["_meta"]["io.modelcontextprotocol/serverInfo"],
        {"name": "makeflow-guard", "version": expected_version},
    )

    for idx, probe_ver in enumerate(("2026-07-28", "2027-06-01"), start=201):
      err_buf = io.StringIO()
      resp = mcp_server.dispatch_jsonrpc_line(
          json.dumps({
              "jsonrpc": "2.0",
              "id": idx,
              "method": "server/discover",
              "params": {
                  "_meta": {
                      "io.modelcontextprotocol/protocolVersion": probe_ver
                  }
              },
          }),
          stderr=err_buf,
      )
      self.assertIsNotNone(resp)
      self.assertEqual(resp["id"], idx)
      self.assertNotIn("error", resp)
      self.assertEqual(
          resp["result"]["supportedVersions"], list(expected_versions)
      )
      self.assertEqual(resp["result"]["capabilities"], {"tools": {}})
      self.assertEqual(
          resp["result"]["_meta"]["io.modelcontextprotocol/serverInfo"],
          {"name": "makeflow-guard", "version": expected_version},
      )

  def test_per_request_meta_protocol_version_negotiation(self):
    """Checks per-request _meta protocol version acceptance and -32022 error."""
    import io

    mcp_server = self._load_mcp_server()
    self.assertTrue(hasattr(mcp_server, "dispatch_jsonrpc_line"))
    self.assertTrue(hasattr(mcp_server, "SUPPORTED_PROTOCOL_VERSIONS"))

    expected_versions = list(mcp_server.SUPPORTED_PROTOCOL_VERSIONS)
    methods = (
        "ping",
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/templates/list",
        "prompts/list",
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
      req_counter = 300
      for method in methods:
        base_params = (
            {
                "name": "workflow_get_config",
                "arguments": {"workspaceDir": tmp_dir},
            }
            if method == "tools/call"
            else {}
        )
        for accepted_ver in ("2026-07-28", "2025-11-25", None, "OMIT"):
          req_counter += 1
          params = dict(base_params)
          if accepted_ver != "OMIT":
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": accepted_ver
            }
          resp = mcp_server.dispatch_jsonrpc_line(
              json.dumps({
                  "jsonrpc": "2.0",
                  "id": req_counter,
                  "method": method,
                  "params": params,
              }),
              stderr=io.StringIO(),
          )
          self.assertEqual(resp["id"], req_counter)
          self.assertIn("result", resp)
          self.assertNotIn("error", resp)

        req_counter += 1
        bad_params = dict(base_params)
        bad_params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": "2027-06-01"
        }
        rejected = mcp_server.dispatch_jsonrpc_line(
            json.dumps({
                "jsonrpc": "2.0",
                "id": req_counter,
                "method": method,
                "params": bad_params,
            }),
            stderr=io.StringIO(),
        )
        self.assertEqual(rejected["id"], req_counter)
        self.assertEqual(rejected["error"]["code"], -32022)
        self.assertEqual(
            rejected["error"]["message"], "Unsupported protocol version"
        )
        self.assertEqual(
            rejected["error"]["data"],
            {"supported": expected_versions, "requested": "2027-06-01"},
        )

        req_counter += 1
        top_rejected = mcp_server.dispatch_jsonrpc_line(
            json.dumps({
                "jsonrpc": "2.0",
                "id": req_counter,
                "method": method,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2027-06-01"
                },
                "params": dict(base_params),
            }),
            stderr=io.StringIO(),
        )
        self.assertEqual(top_rejected["id"], req_counter)
        self.assertEqual(top_rejected["error"]["code"], -32022)
        self.assertEqual(
            top_rejected["error"]["message"], "Unsupported protocol version"
        )
        self.assertEqual(
            top_rejected["error"]["data"],
            {"supported": expected_versions, "requested": "2027-06-01"},
        )

  def test_handle_initialize_version_negotiation(self):
    """Checks legacy initialize protocol version negotiation and fallbacks."""
    import io

    mcp_server = self._load_mcp_server()
    self.assertTrue(hasattr(mcp_server, "dispatch_jsonrpc_line"))

    supported = (
        "2026-07-28",
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    )
    for idx, ver in enumerate(supported, start=1):
      resp = mcp_server.handle_initialize(idx, {"protocolVersion": ver})
      self.assertEqual(resp["id"], idx)
      self.assertEqual(resp["result"]["protocolVersion"], ver)

    wire_init = mcp_server.dispatch_jsonrpc_line(
        json.dumps({
            "jsonrpc": "2.0",
            "id": "init-wire",
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }),
        stderr=io.StringIO(),
    )
    self.assertEqual(wire_init["id"], "init-wire")
    self.assertEqual(wire_init["result"]["protocolVersion"], "2025-11-25")

    self.assertEqual(
        mcp_server.handle_initialize(10)["result"]["protocolVersion"],
        "2025-11-25",
    )
    self.assertEqual(
        mcp_server.handle_initialize(11, None)["result"]["protocolVersion"],
        "2025-11-25",
    )
    self.assertEqual(
        mcp_server.handle_initialize(12, {})["result"]["protocolVersion"],
        "2025-11-25",
    )
    self.assertEqual(
        mcp_server.handle_initialize(13, {"protocolVersion": "2027-06-01"})[
            "result"
        ]["protocolVersion"],
        "2025-11-25",
    )

  def test_dispatch_jsonrpc_line_framing_and_lifecycle(self):
    """Checks JSON-RPC 2.0 framing, notification silence, and lifecycle methods."""
    import io

    mcp_server = self._load_mcp_server()
    self.assertTrue(hasattr(mcp_server, "dispatch_jsonrpc_line"))

    for blank in ("", "   \t\n"):
      blank_err = io.StringIO()
      self.assertIsNone(
          mcp_server.dispatch_jsonrpc_line(blank, stderr=blank_err)
      )
      self.assertEqual(blank_err.getvalue(), "")

    for bad_raw in ("{bad", "[]", "null", "42", "true"):
      bad_err = io.StringIO()
      self.assertIsNone(
          mcp_server.dispatch_jsonrpc_line(bad_raw, stderr=bad_err)
      )
      self.assertNotEqual(bad_err.getvalue().strip(), "")

    notifications = (
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
        {"jsonrpc": "2.0", "id": 5, "method": "notifications/cancelled"},
        {"jsonrpc": "2.0", "method": "ping"},
        {"jsonrpc": "2.0", "id": None, "method": "ping"},
    )
    for note in notifications:
      self.assertIsNone(
          mcp_server.dispatch_jsonrpc_line(
              json.dumps(note), stderr=io.StringIO()
          )
      )

    for bad_id in (True, False, [1], {"k": 1}, 1.5):
      id_err = io.StringIO()
      self.assertIsNone(
          mcp_server.dispatch_jsonrpc_line(
              json.dumps({"jsonrpc": "2.0", "id": bad_id, "method": "ping"}),
              stderr=id_err,
          )
      )
      self.assertNotEqual(id_err.getvalue().strip(), "")

    for req_id, bad_method_frame in (
        (401, {"jsonrpc": "2.0", "id": 401}),
        ("m-402", {"jsonrpc": "2.0", "id": "m-402", "method": 123}),
        (403, {"jsonrpc": "2.0", "id": 403, "method": "   "}),
    ):
      resp = mcp_server.dispatch_jsonrpc_line(
          json.dumps(bad_method_frame), stderr=io.StringIO()
      )
      self.assertEqual(resp["id"], req_id)
      self.assertEqual(resp["error"]["code"], -32600)

    ping_resp = mcp_server.dispatch_jsonrpc_line(
        json.dumps(
            {"jsonrpc": "2.0", "id": 410, "method": "ping", "params": None}
        ),
        stderr=io.StringIO(),
    )
    self.assertEqual(ping_resp, {"jsonrpc": "2.0", "id": 410, "result": {}})

    res_resp = mcp_server.dispatch_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 411, "method": "resources/list"}),
        stderr=io.StringIO(),
    )
    self.assertEqual(
        res_resp, {"jsonrpc": "2.0", "id": 411, "result": {"resources": []}}
    )

    tmpl_resp = mcp_server.dispatch_jsonrpc_line(
        json.dumps(
            {"jsonrpc": "2.0", "id": 412, "method": "resources/templates/list"}
        ),
        stderr=io.StringIO(),
    )
    self.assertEqual(
        tmpl_resp,
        {"jsonrpc": "2.0", "id": 412, "result": {"resourceTemplates": []}},
    )

    prompts_resp = mcp_server.dispatch_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 413, "method": "prompts/list"}),
        stderr=io.StringIO(),
    )
    self.assertEqual(
        prompts_resp, {"jsonrpc": "2.0", "id": 413, "result": {"prompts": []}}
    )

    unknown_resp = mcp_server.dispatch_jsonrpc_line(
        json.dumps({"jsonrpc": "2.0", "id": 414, "method": "unknown/method"}),
        stderr=io.StringIO(),
    )
    self.assertEqual(unknown_resp["id"], 414)
    self.assertEqual(unknown_resp["error"]["code"], -32601)

    for idx, bad_params in enumerate(([1], "bad", 99), start=420):
      bad_p_resp = mcp_server.dispatch_jsonrpc_line(
          json.dumps({
              "jsonrpc": "2.0",
              "id": idx,
              "method": "tools/list",
              "params": bad_params,
          }),
          stderr=io.StringIO(),
      )
      self.assertEqual(bad_p_resp["id"], idx)
      self.assertEqual(bad_p_resp["error"]["code"], -32602)

    for idx, call_params in enumerate(
        ({}, {"name": ""}, {"name": "   "}, {"name": 42}), start=430
    ):
      bad_call = mcp_server.dispatch_jsonrpc_line(
          json.dumps({
              "jsonrpc": "2.0",
              "id": idx,
              "method": "tools/call",
              "params": call_params,
          }),
          stderr=io.StringIO(),
      )
      self.assertEqual(bad_call["id"], idx)
      self.assertEqual(bad_call["error"]["code"], -32602)

  def test_tools_call_stringified_arguments_and_internal_errors(self):
    """Checks stringified arguments, evidence normalization, and -32603 errors."""
    import io
    from unittest import mock

    mcp_server = self._load_mcp_server()
    self.assertTrue(hasattr(mcp_server, "dispatch_jsonrpc_line"))

    with tempfile.TemporaryDirectory() as tmp_dir:
      for idx, raw_args in enumerate(
          ("{}", "", json.dumps({"workspaceDir": tmp_dir})), start=501
      ):
        resp = mcp_server.dispatch_jsonrpc_line(
            json.dumps({
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": "workflow_get_config",
                    "arguments": raw_args,
                },
            }),
            stderr=io.StringIO(),
        )
        self.assertEqual(resp["id"], idx)
        self.assertFalse(resp["result"].get("isError"))
        parsed_cfg = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("models", parsed_cfg)

      state_resp = mcp_server.dispatch_jsonrpc_line(
          json.dumps({
              "jsonrpc": "2.0",
              "id": "state-str",
              "method": "tools/call",
              "params": {
                  "name": "workflow_get_state",
                  "arguments": json.dumps({"workspaceDir": tmp_dir}),
              },
          }),
          stderr=io.StringIO(),
      )
      self.assertEqual(state_resp["id"], "state-str")
      self.assertFalse(state_resp["result"].get("isError"))

      for idx, bad_args in enumerate(("{bad", "[1]", 123, []), start=510):
        bad_resp = mcp_server.dispatch_jsonrpc_line(
            json.dumps({
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": "workflow_get_config",
                    "arguments": bad_args,
                },
            }),
            stderr=io.StringIO(),
        )
        self.assertEqual(bad_resp["id"], idx)
        self.assertIs(bad_resp["result"].get("isError"), True)

      adv_ok = mcp_server.handle_tools_call(
          520,
          "workflow_advance_target",
          {
              "currentTarget": "T40",
              "nextTarget": "T45",
              "evidence": json.dumps({"blockersCount": 0}),
          },
      )
      self.assertEqual(adv_ok["id"], 520)
      self.assertFalse(adv_ok["result"].get("isError"))
      self.assertIn("SUCCESS", adv_ok["result"]["content"][0]["text"])

      for idx, empty_ev in enumerate(("", "{}"), start=521):
        adv_empty = mcp_server.handle_tools_call(
            idx,
            "workflow_advance_target",
            {
                "currentTarget": "T10",
                "nextTarget": "T15",
                "evidence": empty_ev,
            },
        )
        self.assertEqual(adv_empty["id"], idx)
        self.assertFalse(adv_empty["result"].get("isError"))

      for idx, bad_ev in enumerate(("{bad", "[1]", 123, []), start=530):
        adv_bad = mcp_server.handle_tools_call(
            idx,
            "workflow_advance_target",
            {
                "currentTarget": "T40",
                "nextTarget": "T45",
                "evidence": bad_ev,
            },
        )
        self.assertEqual(adv_bad["id"], idx)
        self.assertIs(adv_bad["result"].get("isError"), True)

      boom_err = io.StringIO()
      with mock.patch.object(
          mcp_server,
          "handle_tools_list",
          side_effect=RuntimeError("boom-internal-error"),
      ):
        boom_resp = mcp_server.dispatch_jsonrpc_line(
            json.dumps({"jsonrpc": "2.0", "id": "boom-1", "method": "tools/list"}),
            stderr=boom_err,
        )
      self.assertEqual(boom_resp["id"], "boom-1")
      self.assertEqual(boom_resp["error"]["code"], -32603)
      self.assertIn("boom-internal-error", boom_err.getvalue())

  def test_workflow_query_scars_optional_schema_and_fallback(self):
    """Checks optional query schema, filter-only queries, and scars_db error."""
    from unittest import mock

    mcp_server = self._load_mcp_server()
    tools_resp = mcp_server.handle_tools_list(1)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    scars_schema = tools["workflow_query_scars"]["inputSchema"]
    self.assertNotIn("required", scars_schema)

    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "scars_fixture.db")
      self._build_scars_fixture_db(db_path)

      for idx, filter_args in enumerate(
          (
              {"mfTarget": 30, "dbPath": db_path, "refreshLoaded": True},
              {
                  "workflowPhase": "Target 30: tdd",
                  "dbPath": db_path,
                  "refreshLoaded": True,
              },
              {"language": "python", "dbPath": db_path, "refreshLoaded": True},
          ),
          start=601,
      ):
        resp = mcp_server.handle_tools_call(
            idx, "workflow_query_scars", filter_args
        )
        self.assertEqual(resp["id"], idx)
        self.assertFalse(resp["result"].get("isError"))
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["totalResults"], 1)
        self.assertEqual(payload["scars"][0]["id"], "SCAR-LANG-PYTHON-TEST-0099")

      for idx, empty_args in enumerate(({}, {"workspaceDir": tmp_dir}), start=610):
        empty_resp = mcp_server.handle_tools_call(
            idx, "workflow_query_scars", empty_args
        )
        self.assertEqual(empty_resp["id"], idx)
        self.assertIs(empty_resp["result"].get("isError"), True)
        self.assertIn(
            "Missing required argument 'query'",
            empty_resp["result"]["content"][0]["text"],
        )

      with mock.patch.object(mcp_server, "query_scars", None):
        missing_engine_resp = mcp_server.handle_tools_call(
            620,
            "workflow_query_scars",
            {"query": "Framing", "workspaceDir": tmp_dir},
        )
      self.assertEqual(missing_engine_resp["id"], 620)
      self.assertIs(missing_engine_resp["result"].get("isError"), True)
      fallback_msg = missing_engine_resp["result"]["content"][0]["text"]
      self.assertIn("lib/scars_db.py", fallback_msg)
      self.assertNotIn("scripts/sync_synthetic_scars.py", fallback_msg)

  def test_stdio_subprocess_wire_sequence_never_emits_null_id(self):
    """Checks mixed stdio wire traffic exits cleanly with zero null-id frames."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "stdio_scars.db")
      self._build_scars_fixture_db(db_path)

      wire_lines = [
          json.dumps({
              "jsonrpc": "2.0",
              "id": 1,
              "method": "server/discover",
              "params": {
                  "_meta": {
                      "io.modelcontextprotocol/protocolVersion": "2026-07-28"
                  }
              },
          }),
          "{malformed json line",
          json.dumps({
              "jsonrpc": "2.0",
              "method": "notifications/initialized",
              "params": {},
          }),
          json.dumps({
              "jsonrpc": "2.0",
              "id": 2,
              "method": "ping",
              "params": {
                  "_meta": {
                      "io.modelcontextprotocol/protocolVersion": "2026-07-28"
                  }
              },
          }),
          json.dumps({
              "jsonrpc": "2.0",
              "id": 3,
              "method": "tools/call",
              "params": {
                  "name": "workflow_get_config",
                  "arguments": "{}",
              },
          }),
          json.dumps({
              "jsonrpc": "2.0",
              "id": 4,
              "method": "tools/call",
              "params": {
                  "name": "workflow_get_state",
                  "arguments": {"workspaceDir": tmp_dir},
              },
          }),
          json.dumps({
              "jsonrpc": "2.0",
              "id": 5,
              "method": "tools/call",
              "params": {
                  "name": "workflow_query_scars",
                  "arguments": {"mfTarget": 30, "dbPath": db_path},
              },
          }),
      ]

      proc = subprocess.run(
          [sys.executable, SERVER_SCRIPT],
          input="\n".join(wire_lines) + "\n",
          cwd=tmp_dir,
          capture_output=True,
          text=True,
          check=False,
          timeout=10,
      )
      self.assertEqual(proc.returncode, 0)
      raw_out_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
      for raw_ln in raw_out_lines:
        self.assertNotIn('"id": null', raw_ln)
        self.assertNotIn('"id":null', raw_ln)

      responses = [json.loads(ln) for ln in raw_out_lines]
      self.assertEqual([r.get("id") for r in responses], [1, 2, 3, 4, 5])
      self.assertEqual(
          responses[0]["result"]["supportedVersions"][0], "2026-07-28"
      )
      self.assertEqual(responses[1]["result"], {})
      self.assertFalse(responses[2]["result"].get("isError"))
      self.assertFalse(responses[3]["result"].get("isError"))
      scars_payload = json.loads(responses[4]["result"]["content"][0]["text"])
      self.assertEqual(scars_payload["status"], "OK")
      self.assertEqual(scars_payload["totalResults"], 1)
      self.assertNotEqual(proc.stderr.strip(), "")


class TestMcpDynamicVersionAndTarget90Parity(unittest.TestCase):
  """Tests dynamic plugin.json version loading and Target 90 MCP safety parity."""

  def test_get_plugin_version_dynamic_and_fallback(self):
    from unittest.mock import patch

    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    import mcp.server as mcp_server
    from mcp.server import get_plugin_version, handle_initialize, handle_server_discover

    with open(os.path.join(REPO_ROOT, "plugin.json"), encoding="utf-8") as f:
      expected_version = json.load(f)["version"]

    self.assertEqual(get_plugin_version(), expected_version)
    init_resp = handle_initialize(101)
    self.assertEqual(init_resp["result"]["serverInfo"]["version"], expected_version)
    discover_resp = handle_server_discover(102)
    self.assertEqual(
        discover_resp["result"]["_meta"]["io.modelcontextprotocol/serverInfo"][
            "version"
        ],
        expected_version,
    )

    with patch.object(mcp_server, "get_plugin_version", return_value="9.8.7"):
      patched_init = handle_initialize(103)
      self.assertEqual(patched_init["result"]["serverInfo"]["version"], "9.8.7")
      patched_discover = handle_server_discover(104)
      self.assertEqual(
          patched_discover["result"]["_meta"][
              "io.modelcontextprotocol/serverInfo"
          ]["version"],
          "9.8.7",
      )

    with tempfile.TemporaryDirectory() as td:
      custom_manifest = os.path.join(td, "plugin.json")
      with open(custom_manifest, "w", encoding="utf-8") as f:
        json.dump({"name": "makeflow", "version": "9.8.7"}, f)
      self.assertEqual(get_plugin_version(custom_manifest), "9.8.7")

      missing_manifest = os.path.join(td, "missing.json")
      self.assertEqual(get_plugin_version(missing_manifest), "0.0.1")

      corrupt_manifest = os.path.join(td, "corrupt.json")
      with open(corrupt_manifest, "w", encoding="utf-8") as f:
        f.write("{invalid json")
      self.assertEqual(get_plugin_version(corrupt_manifest), "0.0.1")

      list_manifest = os.path.join(td, "list.json")
      with open(list_manifest, "w", encoding="utf-8") as f:
        json.dump(["1.5.0"], f)
      self.assertEqual(get_plugin_version(list_manifest), "0.0.1")

      blank_manifest = os.path.join(td, "blank.json")
      with open(blank_manifest, "w", encoding="utf-8") as f:
        json.dump({"name": "makeflow", "version": "   "}, f)
      self.assertEqual(get_plugin_version(blank_manifest), "0.0.1")

      int_manifest = os.path.join(td, "int.json")
      with open(int_manifest, "w", encoding="utf-8") as f:
        json.dump({"name": "makeflow", "version": 123}, f)
      self.assertEqual(get_plugin_version(int_manifest), "0.0.1")

  def test_workflow_safety_check_target90_changelog_and_release_parity(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from lib.git_safety import evaluate_git_safety
    from mcp.server import handle_tools_call

    for cmd in (
        "git commit -m 'docs(changelog): update unreleased notes'",
        "git commit -m 'docs(release): cut v1.5.0'",
        "git commit -m 'chore(release): bump manifests to v1.5.0'",
        "git commit -m 'docs(scars): add project scar'",
        "git commit -m 'chore: add local scar' .workflow/scars/local.md",
        "git commit -m 'docs(changelog): document fix: edge case'",
        "git commit -S -m 'docs(changelog): signed update'",
    ):
      safe, reason = evaluate_git_safety(cmd)
      self.assertTrue(safe)
      self.assertIn("Target 90 History Ledger Gate", reason, msg=cmd)

    for t50_cmd in (
        "git commit -m 'feat: add api' CHANGELOG.md",
        "git commit -m 'feat(hooks): handle docs(changelog): commits'",
        "git commit -m 'fix(ledger): handle .workflow/history'",
        "git commit -m 'docs(changelog): sneak' src/app.py",
        "git commit -am 'docs(changelog): update unreleased notes'",
        "git commit --all -m 'docs(release): cut v1.5.0'",
        "git commit -S src/app.py -m 'docs(changelog): sneak'",
        "git commit --amend -m 'docs(changelog): amend'",
        "git rm src/app.py && git commit -m 'docs(changelog): remove'",
        "# docs(changelog): note\ngit commit -m 'feat: unreviewed'",
        "git commit -m 'docs(changelog): a' && git commit -m 'feat: b'",
    ):
      feat_safe, feat_reason = evaluate_git_safety(t50_cmd)
      self.assertTrue(feat_safe)
      self.assertIn("Target 50 Commit Gate", feat_reason, msg=t50_cmd)

    with tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "# Next Action: COMPLETE (make finish satisfied)\n"
            "- Active Target: Target 90 (finish)\n"
        )

      for req_idx, cmd in enumerate(
          (
              "git commit -m 'chore: finalize notes' CHANGELOG.md",
              "git add CHANGELOG.md && git commit -m 'chore: release notes'",
          ),
          start=601,
      ):
        resp_t90 = handle_tools_call(
            req_idx,
            "workflow_safety_check",
            {"commandLine": cmd, "workspaceDir": ws_dir},
        )
        payload_t90 = json.loads(resp_t90["result"]["content"][0]["text"])
        self.assertTrue(payload_t90["safe"], msg=cmd)
        self.assertIn(
            "Target 90 History Ledger Gate", payload_t90["reason"], msg=cmd
        )

      for req_idx, cmd in enumerate(
          (
              "git commit -m 'feat: add api' CHANGELOG.md",
              "git add src/app.py CHANGELOG.md && git commit -m 'chore: release notes'",
              "git commit -m 'docs(changelog): a' && git commit -m 'feat: b'",
              "git commit -m 'feat(hooks): handle docs(changelog): commits'",
              "git commit -am 'docs(changelog): update unreleased notes'",
              "git commit --all -m 'docs(release): cut v1.5.0'",
              "git commit -S src/app.py -m 'docs(changelog): sneak'",
              "git commit --amend -m 'docs(changelog): amend'",
              "git rm src/app.py && git commit -m 'docs(changelog): remove'",
          ),
          start=610,
      ):
        resp_feat = handle_tools_call(
            req_idx,
            "workflow_safety_check",
            {"commandLine": cmd, "workspaceDir": ws_dir},
        )
        payload_feat = json.loads(resp_feat["result"]["content"][0]["text"])
        self.assertTrue(payload_feat["safe"], msg=cmd)
        self.assertIn("Target 50 Commit Gate", payload_feat["reason"], msg=cmd)


class TestMcpCiQuiescenceZeroAndValidation(unittest.TestCase):
  """Regression tests for ci.quiescenceSeconds=0 and CI arg validation in MCP server (GH-217)."""

  def _clean_env(self, home_dir, **extra):
    env = dict(os.environ)
    env["HOME"] = home_dir
    for key in (
        "MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS",
        "MAKEFILE_WORKFLOW_CI_POLL_INTERVAL",
        "MAKEFILE_WORKFLOW_WORKSPACE",
    ):
      env.pop(key, None)
    env.update(extra)
    return env

  def _call_mcp_tools(self, cwd, env, calls):
    proc = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
    )
    try:
      send_rpc(
          proc,
          {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
      )
      responses = []
      for req_id, (tool_name, arguments) in enumerate(calls, start=2):
        responses.append(
            send_rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                },
            )
        )
      return responses
    finally:
      proc.kill()
      proc.wait()
      for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
          pipe.close()

  def test_workflow_get_config_quiescence_zero_and_env_override_stdio(self):
    """Tests stdio workflow_get_config with JSON 0 and env override '0' (GH-217)."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      wf_dir = os.path.join(ws_dir, ".workflow")
      os.makedirs(wf_dir)
      cfg_path = os.path.join(wf_dir, "config.json")

      # 1. JSON quiescenceSeconds=0
      with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"ci": {"quiescenceSeconds": 0}}, f)
      [resp_json] = self._call_mcp_tools(
          ws_dir,
          self._clean_env(fake_home),
          [("workflow_get_config", {"workspaceDir": ws_dir})],
      )
      cfg_json = json.loads(resp_json["result"]["content"][0]["text"])
      self.assertIs(type(cfg_json["ci"]["quiescenceSeconds"]), int)
      self.assertEqual(cfg_json["ci"]["quiescenceSeconds"], 0)
      self.assertEqual(cfg_json["ci"]["pollIntervalSeconds"], 30)

      # 2. Env MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS="0" overrides JSON 120
      with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"ci": {"quiescenceSeconds": 120}}, f)
      [resp_env] = self._call_mcp_tools(
          ws_dir,
          self._clean_env(
              fake_home, MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS="0"
          ),
          [("workflow_get_config", {"workspaceDir": ws_dir})],
      )
      cfg_env = json.loads(resp_env["result"]["content"][0]["text"])
      self.assertIs(type(cfg_env["ci"]["quiescenceSeconds"]), int)
      self.assertEqual(cfg_env["ci"]["quiescenceSeconds"], 0)
      self.assertIn(
          "MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS=0",
          cfg_env["envOverrides"],
      )

  def test_workflow_get_config_sanitizes_invalid_ci_fields_stdio(self):
    """Tests stdio workflow_get_config sanitizes invalid JSON and env CI values (GH-217)."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      wf_dir = os.path.join(ws_dir, ".workflow")
      os.makedirs(wf_dir)
      cfg_path = os.path.join(wf_dir, "config.json")

      invalid_payloads = (
          {"ci": {"quiescenceSeconds": True}},
          {"ci": {"quiescenceSeconds": False}},
          {"ci": {"quiescenceSeconds": -5}},
          {"ci": {"quiescenceSeconds": "bad"}},
          {"ci": {"pollIntervalSeconds": 0}},
          {"ci": None},
      )
      for payload in invalid_payloads:
        with open(cfg_path, "w", encoding="utf-8") as f:
          json.dump(payload, f)
        [resp] = self._call_mcp_tools(
            ws_dir,
            self._clean_env(fake_home),
            [("workflow_get_config", {"workspaceDir": ws_dir})],
        )
        cfg = json.loads(resp["result"]["content"][0]["text"])
        self.assertIs(type(cfg["ci"]["quiescenceSeconds"]), int)
        self.assertEqual(cfg["ci"]["quiescenceSeconds"], 180)
        self.assertIs(type(cfg["ci"]["pollIntervalSeconds"]), int)
        self.assertEqual(cfg["ci"]["pollIntervalSeconds"], 30)

      # "ci": null + env quiescence=0 + invalid env pollInterval=0
      with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"ci": None}, f)
      [resp_null_env] = self._call_mcp_tools(
          ws_dir,
          self._clean_env(
              fake_home,
              MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS="0",
              MAKEFILE_WORKFLOW_CI_POLL_INTERVAL="0",
          ),
          [("workflow_get_config", {"workspaceDir": ws_dir})],
      )
      cfg_null_env = json.loads(resp_null_env["result"]["content"][0]["text"])
      self.assertIs(type(cfg_null_env["ci"]["quiescenceSeconds"]), int)
      self.assertEqual(cfg_null_env["ci"]["quiescenceSeconds"], 0)
      self.assertEqual(cfg_null_env["ci"]["pollIntervalSeconds"], 30)
      self.assertIn(
          "MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS=0",
          cfg_null_env["envOverrides"],
      )
      self.assertNotIn(
          "MAKEFILE_WORKFLOW_CI_POLL_INTERVAL=0",
          cfg_null_env["envOverrides"],
      )

      # Valid JSON pollIntervalSeconds=15 preserved when env pollInterval="0"
      with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"ci": {"pollIntervalSeconds": 15}}, f)
      [resp_preserve] = self._call_mcp_tools(
          ws_dir,
          self._clean_env(fake_home, MAKEFILE_WORKFLOW_CI_POLL_INTERVAL="0"),
          [("workflow_get_config", {"workspaceDir": ws_dir})],
      )
      cfg_preserve = json.loads(resp_preserve["result"]["content"][0]["text"])
      self.assertEqual(cfg_preserve["ci"]["pollIntervalSeconds"], 15)
      self.assertEqual(cfg_preserve["envOverrides"], [])

  def test_workflow_poll_ci_validates_explicit_arguments_stdio(self):
    """Tests stdio workflow_poll_ci rejects invalid quiescenceSeconds and pollIntervalSeconds (GH-217)."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      calls = []
      for bad_q in (-1, True, False, "bad"):
        calls.append((
            "workflow_poll_ci",
            {
                "prNumber": 0,
                "workspaceDir": ws_dir,
                "quiescenceSeconds": bad_q,
            },
        ))
      for bad_p in (0, -1, False, True, "bad"):
        calls.append((
            "workflow_poll_ci",
            {
                "prNumber": 0,
                "workspaceDir": ws_dir,
                "pollIntervalSeconds": bad_p,
            },
        ))
      calls.append((
          "workflow_poll_ci",
          {
              "prNumber": 0,
              "workspaceDir": ws_dir,
              "quiescenceSeconds": 0,
          },
      ))

      responses = self._call_mcp_tools(ws_dir, self._clean_env(fake_home), calls)
      invalid_count = len(responses) - 1
      for idx in range(invalid_count):
        self.assertIs(
            responses[idx]["result"].get("isError"),
            True,
            f"Expected isError=True for call {calls[idx]}",
        )
      valid_resp = responses[-1]
      self.assertFalse(valid_resp["result"].get("isError"))
      valid_payload = json.loads(valid_resp["result"]["content"][0]["text"])
      self.assertEqual(valid_payload["mode"], "pure_git")

  def test_poll_ci_status_quiescence_zero_returns_without_sleeping(self):
    """Tests poll_ci_status returns SUCCESS on poll 1 without sleeping when quiescenceSeconds=0 (GH-217)."""
    from lib.ci_polling import poll_ci_status

    completed_iso = "2026-11-15T12:00:00Z"
    base_ts = datetime.datetime(
        2026, 11, 15, 12, 0, 0, tzinfo=datetime.timezone.utc
    ).timestamp()
    mock_data_clean = {
        "statusCheckRollup": [{
            "name": "ci",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "completedAt": completed_iso,
        }],
        "comments": [],
        "reviews": [],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }

    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      wf_dir = os.path.join(ws_dir, ".workflow")
      os.makedirs(wf_dir)
      cfg_path = os.path.join(wf_dir, "config.json")

      scenarios = (
          ({"ci": {"quiescenceSeconds": 0}}, {}, None),
          (
              {"ci": {"quiescenceSeconds": 120}},
              {"MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS": "0"},
              None,
          ),
          ({"ci": {"quiescenceSeconds": 120}}, {}, 0),
      )

      for cfg_payload, env_extra, explicit_q in scenarios:
        with open(cfg_path, "w", encoding="utf-8") as f:
          json.dump(cfg_payload, f)
        now = [base_ts]

        def advance_past_timeout(_sec, clock=now):
          clock[0] += 601

        with mock.patch.dict(
            os.environ, self._clean_env(fake_home, **env_extra), clear=True
        ):
          with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
              "subprocess.run"
          ) as mock_run, mock.patch(
              "time.time", side_effect=lambda clock=now: clock[0]
          ), mock.patch(
              "time.sleep", side_effect=advance_past_timeout
          ) as mock_sleep:
            mock_run.return_value = mock.MagicMock(
                stdout=json.dumps(mock_data_clean), stderr="", returncode=0
            )
            res = poll_ci_status(
                pr_number=123,
                ws=ws_dir,
                quiescence_sec=explicit_q,
                max_wait_sec=600,
                hosting_mode="github",
            )
            self.assertEqual(res["ciStatus"], "SUCCESS")
            self.assertIs(res["quiescenceSatisfied"], True)
            self.assertIs(res["readyToLand"], True)
            self.assertEqual(mock_run.call_count, 1)
            mock_sleep.assert_not_called()

  def test_poll_ci_status_sanitizes_invalid_poll_interval_in_memory(self):
    """Tests direct poll_ci_status sanitizes invalid poll_interval_sec (0, -1, True) to config value (GH-217)."""
    from lib.ci_polling import poll_ci_status

    base_ts = datetime.datetime(
        2026, 11, 15, 12, 0, 0, tzinfo=datetime.timezone.utc
    ).timestamp()
    in_progress_data = {
        "statusCheckRollup": [{"name": "ci", "status": "IN_PROGRESS"}],
        "comments": [],
        "reviews": [],
    }
    completed_data = {
        "statusCheckRollup": [{
            "name": "ci",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "completedAt": "2026-11-15T12:00:15Z",
        }],
        "comments": [],
        "reviews": [],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }

    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      wf_dir = os.path.join(ws_dir, ".workflow")
      os.makedirs(wf_dir)
      with open(
          os.path.join(wf_dir, "config.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {"ci": {"pollIntervalSeconds": 15, "quiescenceSeconds": 0}}, f
        )

      for bad_poll in (0, -1, True):
        now = [base_ts]
        sleep_calls = []

        def fake_sleep(sec, clock=now, calls=sleep_calls):
          if isinstance(sec, (int, float)) and sec < 0:
            raise ValueError("sleep length must be non-negative")
          calls.append((type(sec), sec))
          clock[0] += max(sec, 1)
          if len(calls) > 2:
            clock[0] += 1000

        with mock.patch.dict(
            os.environ, self._clean_env(fake_home), clear=True
        ):
          with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
              "subprocess.run",
              side_effect=[
                  mock.MagicMock(
                      stdout=json.dumps(in_progress_data), stderr="", returncode=0
                  ),
                  mock.MagicMock(
                      stdout=json.dumps(completed_data), stderr="", returncode=0
                  ),
              ],
          ), mock.patch(
              "time.time", side_effect=lambda clock=now: clock[0]
          ), mock.patch(
              "time.sleep", side_effect=fake_sleep
          ):
            res = poll_ci_status(
                pr_number=123,
                ws=ws_dir,
                poll_interval_sec=bad_poll,
                max_wait_sec=600,
                hosting_mode="github",
            )
            self.assertEqual(sleep_calls, [(int, 15)])
            self.assertEqual(res["ciStatus"], "SUCCESS")
            self.assertIs(res["quiescenceSatisfied"], True)
            self.assertIs(res["readyToLand"], True)


class TestMcpQueryScarsDescriptionClarification(unittest.TestCase):
  """Tests workflow_query_scars query wording and filter fallback (GH-212)."""

  def test_workflow_query_scars_query_description_clarifies_filters(self):
    """Checks optional query description wording and filter fallback (GH-212)."""
    mcp_server = _load_mcp_server_module()
    tools_resp = mcp_server.handle_tools_list(1)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    scars_schema = tools["workflow_query_scars"]["inputSchema"]
    self.assertNotIn("required", scars_schema)

    props = scars_schema["properties"]
    query_desc = props["query"]["description"]
    self.assertTrue(
        query_desc.startswith("Optional"),
        f"Expected query description to start with 'Optional', got {query_desc!r}",
    )
    self.assertIn("when at least one filter", query_desc.lower())

    for prop_name in (
        "category",
        "language",
        "framework",
        "diagnosticCode",
        "workflowPhase",
        "mfTarget",
        "source",
        "workspaceDir",
        "dbPath",
    ):
      self.assertTrue(
          props[prop_name]["description"].startswith("Optional "),
          f"Expected {prop_name} description to start with 'Optional '",
      )

    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "scars_fixture.db")
      _build_scars_fixture_db(db_path)

      for idx, filter_args in enumerate(
          (
              {
                  "category": "TEST",
                  "dbPath": db_path,
                  "workspaceDir": tmp_dir,
                  "refreshLoaded": True,
              },
              {
                  "query": "   ",
                  "category": "TEST",
                  "dbPath": db_path,
                  "workspaceDir": tmp_dir,
                  "refreshLoaded": True,
              },
          ),
          start=701,
      ):
        resp = mcp_server.handle_tools_call(
            idx, "workflow_query_scars", filter_args
        )
        self.assertEqual(resp["id"], idx)
        self.assertFalse(resp["result"].get("isError"))
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["totalResults"], 1)
        self.assertEqual(payload["scars"][0]["id"], "SCAR-LANG-PYTHON-TEST-0099")

      expected_error = (
          "Missing required argument 'query' or filter. Provide a search query"
          " or at least one filter (category, language, source, framework,"
          " diagnosticCode, workflowPhase, mfTarget)."
      )
      for idx, empty_args in enumerate(
          ({}, {"query": "   ", "workspaceDir": tmp_dir}), start=710
      ):
        empty_resp = mcp_server.handle_tools_call(
            idx, "workflow_query_scars", empty_args
        )
        self.assertEqual(empty_resp["id"], idx)
        self.assertIs(empty_resp["result"].get("isError"), True)
        self.assertEqual(
            empty_resp["result"]["content"][0]["text"], expected_error
        )


class TestMcpRepoTreeTool(unittest.TestCase):
  """Tests read-only workflow_repo_tree MCP tool schema, isolation, and SCAR-TOOL-16 triage (GH-246)."""

  @staticmethod
  def _init_git_repo(repo_dir: str) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )

  def test_workflow_repo_tree_schema_and_plugin_dir_isolation(self):
    """Checks workflow_repo_tree inputSchema properties and workspaceDir isolation."""
    mcp_server = _load_mcp_server_module()
    tools_resp = mcp_server.handle_tools_list(1)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    self.assertIn("workflow_repo_tree", tools)
    schema = tools["workflow_repo_tree"]["inputSchema"]
    props = schema["properties"]
    for prop in (
        "workspaceDir",
        "pathPrefix",
        "approvedLimit",
        "countOnly",
        "noLimit",
        "threshold",
    ):
      with self.subTest(prop=prop):
        self.assertIn(prop, props)

    with mock.patch.object(mcp_server, "resolve_workspace_dir", return_value=None):
      blocked = mcp_server.handle_tools_call(2, "workflow_repo_tree", {})
      self.assertIs(blocked["result"].get("isError"), True)
      self.assertIn("workspaceDir", blocked["result"]["content"][0]["text"])

  def test_workflow_repo_tree_stdio_greenfield_populated_and_triage(self):
    """Tests workflow_repo_tree over stdio JSON-RPC for empty, populated, and large trees."""
    non_git_cwd = _make_temp_dir(self)
    proc = _start_mcp_server(self, non_git_cwd)

    # 1. Non-Git workspace returns isError=True cleanly
    err_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {"workspaceDir": non_git_cwd},
            },
        },
    )
    self.assertIs(err_resp["result"].get("isError"), True)

    # 2. Empty greenfield Git repository returns 0 of 0 files
    greenfield = _make_temp_dir(self)
    self._init_git_repo(greenfield)
    empty_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {"workspaceDir": greenfield},
            },
        },
    )
    self.assertFalse(empty_resp["result"].get("isError"))
    empty_payload = json.loads(empty_resp["result"]["content"][0]["text"])
    self.assertEqual(empty_payload["totalCount"], 0)
    self.assertEqual(empty_payload["filteredCount"], 0)
    self.assertEqual(empty_payload["denominator"], "0 of 0 files")
    self.assertIs(empty_payload["triageRequired"], False)
    self.assertEqual(empty_payload["files"], [])

    # 3. Populated Git repository with tracked, untracked, spaces, and .gitignore
    repo_dir = _make_temp_dir(self)
    self._init_git_repo(repo_dir)
    with open(os.path.join(repo_dir, ".gitignore"), "w", encoding="utf-8") as f:
      f.write("*.tmp\n")
    os.makedirs(os.path.join(repo_dir, "lib"))
    os.makedirs(os.path.join(repo_dir, "tests"))
    for rel in ("lib/a.py", "lib/b.py", "tests/test_a.py"):
      with open(os.path.join(repo_dir, rel), "w", encoding="utf-8") as f:
        f.write("# file\n")
    subprocess.run(
        ["git", "add", ".gitignore", "lib/a.py"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init", "-q"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    with open(os.path.join(repo_dir, "skip.tmp"), "w", encoding="utf-8") as f:
      f.write("ignored\n")

    # 3a. Exceeding threshold without override returns triageRequired=True and empty files
    triage_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {"workspaceDir": repo_dir, "threshold": 2},
            },
        },
    )
    self.assertFalse(triage_resp["result"].get("isError"))
    triage_payload = json.loads(triage_resp["result"]["content"][0]["text"])
    self.assertIs(triage_payload["triageRequired"], True)
    self.assertEqual(triage_payload["totalCount"], 4)
    self.assertEqual(triage_payload["files"], [])
    self.assertEqual(triage_payload["topLevelCounts"], {".": 1, "lib": 2, "tests": 1})

    # 3b. Path 1: pathPrefix narrows results within threshold
    prefix_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {
                    "workspaceDir": repo_dir,
                    "pathPrefix": "lib",
                    "threshold": 2,
                },
            },
        },
    )
    self.assertFalse(prefix_resp["result"].get("isError"))
    prefix_payload = json.loads(prefix_resp["result"]["content"][0]["text"])
    self.assertIs(prefix_payload["triageRequired"], False)
    self.assertEqual(prefix_payload["files"], ["lib/a.py", "lib/b.py"])
    self.assertEqual(prefix_payload["denominator"], "2 of 4 files")

    # 3c. Path 2: approvedLimit returns bounded slice explicitly marked truncated
    limit_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {
                    "workspaceDir": repo_dir,
                    "approvedLimit": 2,
                    "threshold": 2,
                },
            },
        },
    )
    self.assertFalse(limit_resp["result"].get("isError"))
    limit_payload = json.loads(limit_resp["result"]["content"][0]["text"])
    self.assertIs(limit_payload["triageRequired"], False)
    self.assertIs(limit_payload["truncated"], True)
    self.assertEqual(limit_payload["files"], [".gitignore", "lib/a.py"])

    # 3d. Path 3: countOnly returns counts without files
    count_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {
                    "workspaceDir": repo_dir,
                    "countOnly": True,
                    "threshold": 2,
                },
            },
        },
    )
    self.assertFalse(count_resp["result"].get("isError"))
    count_payload = json.loads(count_resp["result"]["content"][0]["text"])
    self.assertIs(count_payload["triageRequired"], False)
    self.assertIs(count_payload["countOnly"], True)
    self.assertEqual(count_payload["files"], [])

    # 3e. Path 4: noLimit returns full index excluding ignored files
    full_resp = send_rpc(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {
                "name": "workflow_repo_tree",
                "arguments": {
                    "workspaceDir": repo_dir,
                    "noLimit": True,
                    "threshold": 2,
                },
            },
        },
    )
    self.assertFalse(full_resp["result"].get("isError"))
    full_payload = json.loads(full_resp["result"]["content"][0]["text"])
    self.assertIs(full_payload["triageRequired"], False)
    self.assertEqual(
        full_payload["files"],
        [".gitignore", "lib/a.py", "lib/b.py", "tests/test_a.py"],
    )

    # 4. Invalid argument types return isError=True
    for idx, bad_args in enumerate(
        (
            {"workspaceDir": repo_dir, "approvedLimit": 0},
            {"workspaceDir": repo_dir, "approvedLimit": True},
            {"workspaceDir": repo_dir, "threshold": "2"},
            {"workspaceDir": repo_dir, "threshold": False},
            {"workspaceDir": repo_dir, "countOnly": "yes"},
            {"workspaceDir": repo_dir, "noLimit": 1},
            {"workspaceDir": repo_dir, "pathPrefix": 123},
            {"workspaceDir": repo_dir, "approvedLimit": 2, "noLimit": True},
            {"workspaceDir": repo_dir, "countOnly": True, "noLimit": True},
        ),
        start=20,
    ):
      with self.subTest(bad_args=bad_args):
        bad_resp = send_rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": "workflow_repo_tree",
                    "arguments": bad_args,
                },
            },
        )
        self.assertIs(bad_resp["result"].get("isError"), True)


class TestMcpPureGitTransitions(unittest.TestCase):
  """Tests Pure Git Mode T50 -> T70 workflow_advance_target and schema description (GH-248)."""

  def _init_temp_git_repo(self, repo_dir: str, with_remote: bool = False) -> None:
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Makeflow Test"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_dir, check=True)
    with open(os.path.join(repo_dir, "README.md"), "w", encoding="utf-8") as f:
      f.write("# Test\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "chore: initial"],
        cwd=repo_dir,
        check=True,
    )
    if with_remote:
      bare_dir = os.path.join(os.path.dirname(repo_dir), "remote.git")
      os.makedirs(bare_dir, exist_ok=True)
      subprocess.run(
          ["git", "init", "--bare", "-q", "-b", "main"],
          cwd=bare_dir,
          check=True,
      )
      subprocess.run(
          ["git", "remote", "add", "upstream", bare_dir],
          cwd=repo_dir,
          check=True,
      )

  def test_mcp_pure_git_t50_to_t70_allowed_without_remote_and_blocked_with_remote(
      self,
  ):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from lib.state_machine import read_active_target
    from mcp.server import handle_tools_call

    with tempfile.TemporaryDirectory() as tmp:
      no_remote_repo = os.path.join(tmp, "no_remote")
      self._init_temp_git_repo(no_remote_repo, with_remote=False)
      state_dir = os.path.join(no_remote_repo, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      with open(
          os.path.join(state_dir, "15_archaeology_receipt.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Receipt\nHOSTING_MODE: PURE_GIT\n")

      for bad_ev in ({}, {"humanCommitApproved": False}):
        blocked_no_ev = handle_tools_call(
            801,
            "workflow_advance_target",
            {
                "currentTarget": "T50",
                "nextTarget": "T70",
                "workspaceDir": no_remote_repo,
                "evidence": bad_ev,
            },
        )
        self.assertTrue(blocked_no_ev["result"].get("isError"))
        self.assertIn(
            "humanCommitApproved",
            blocked_no_ev["result"]["content"][0]["text"],
        )

      ok_resp = handle_tools_call(
          802,
          "workflow_advance_target",
          {
              "currentTarget": "T50",
              "nextTarget": "T70",
              "workspaceDir": no_remote_repo,
              "evidence": {"humanCommitApproved": True},
          },
      )
      self.assertFalse(ok_resp["result"].get("isError"), ok_resp)
      self.assertEqual(
          read_active_target(state_dir).get("skippedTargets"), ["T60"]
      )

      remote_repo = os.path.join(tmp, "with_remote")
      self._init_temp_git_repo(remote_repo, with_remote=True)
      remote_state = os.path.join(remote_repo, ".workflow", "state")
      os.makedirs(remote_state, exist_ok=True)
      with open(
          os.path.join(remote_state, "15_archaeology_receipt.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Receipt\nHOSTING_MODE: PURE_GIT\n")

      blocked_remote = handle_tools_call(
          803,
          "workflow_advance_target",
          {
              "currentTarget": "T50",
              "nextTarget": "T70",
              "workspaceDir": remote_repo,
              "evidence": {"humanCommitApproved": True},
          },
      )
      self.assertTrue(blocked_remote["result"].get("isError"))
      err_txt = blocked_remote["result"]["content"][0]["text"]
      self.assertIn("SCAR-GIT-30", err_txt)
      self.assertIn("T60", err_txt)

  def test_mcp_tools_list_documents_t50_to_t70_pure_git_mode(self):
    if REPO_ROOT not in sys.path:
      sys.path.insert(0, REPO_ROOT)
    from mcp.server import handle_tools_list

    tools_resp = handle_tools_list(804)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    desc = tools["workflow_advance_target"]["inputSchema"]["properties"][
        "evidence"
    ]["description"]
    self.assertIn("T50->T60", desc)
    self.assertIn("T50->T70", desc)
    self.assertIn("Pure Git", desc)


class TestMcpQueryScarsExcludeCategories(unittest.TestCase):
  """Tests workflow_query_scars excludeCategories schema, filtering, and validation (GH-252)."""

  def _build_scars_db(self, db_path: str) -> None:
    """Populate a temporary SQLite scars database with universal dev and workflow scars."""
    conn = sqlite3.connect(db_path)
    try:
      conn.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              ss_id TEXT,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              ss_targets TEXT,
              mf_targets TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT,
              source TEXT DEFAULT 'upstream',
              original_id TEXT,
              raw_hash TEXT,
              keywords TEXT,
              languages TEXT,
              frameworks TEXT,
              diagnostic_codes TEXT
          )
          """
      )
      conn.execute(
          """
          CREATE VIRTUAL TABLE scars_fts USING fts5(
              id,
              ss_id,
              original_id,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              socratic_probe,
              keywords,
              languages,
              frameworks,
              diagnostic_codes,
              workflow_phases,
              source UNINDEXED
          )
          """
      )
      rows = [
          ("SCAR-ARCH-0001", "ARCH", "arch", "Did you check cohesion?"),
          ("SCAR-API-0001", "API", "api", "Is the parameter composable?"),
          ("SCAR-TEST-0001", "TEST", "test", "Is the test isolated?"),
          ("SCAR-ARCH-0098", "ARCH", "arch", ""),
          ("SCAR-PROC-0001", "PROC", "proc", "Did you run the gate?"),
          ("SCAR-REVIEW-0001", "REVIEW", "review", "Did critic audit?"),
          ("SCAR-GIT-0001", "GIT", "git", "Did you lock the tree?"),
      ]
      phases = json.dumps(["Target 15: archaeology", "Target 20: plan", "Target 30: tdd"])
      for scar_id, cat, slug, probe in rows:
        conn.execute(
            """
            INSERT INTO scars (
                id, ss_id, title, category, category_slug, visibility,
                workflow_phases, ss_targets, mf_targets, applies_when,
                graduation_status, wound, trap, circuit_breaker, reflex,
                socratic_probe, source, original_id, raw_hash, keywords,
                languages, frameworks, diagnostic_codes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scar_id,
                None,
                f"{scar_id} title",
                cat,
                slug,
                "public",
                phases,
                phases,
                json.dumps([15, 20, 30]),
                "General development",
                "graduated",
                "Observed defect wound.",
                "Observed shortcut trap.",
                "",
                "Apply the permanent reflex.",
                probe,
                "upstream",
                None,
                f"hash-{scar_id}",
                json.dumps([cat.lower()]),
                json.dumps(["universal"]),
                json.dumps([]),
                json.dumps([]),
            ),
        )
        conn.execute(
            """
            INSERT INTO scars_fts (
                id, ss_id, original_id, title, category, applies_when,
                wound, trap, reflex, socratic_probe, keywords, languages,
                frameworks, diagnostic_codes, workflow_phases, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scar_id,
                "",
                "",
                f"{scar_id} title",
                cat,
                "General development",
                "Observed defect wound.",
                "Observed shortcut trap.",
                "Apply the permanent reflex.",
                probe or "",
                cat.lower(),
                "universal",
                "",
                "",
                "Target 15: archaeology Target 20: plan Target 30: tdd",
                "upstream",
            ),
        )
      conn.commit()
    finally:
      conn.close()

  def test_workflow_query_scars_schema_declares_exclude_categories(self):
    mcp_server = _load_mcp_server_module()
    tools_resp = mcp_server.handle_tools_list(901)
    tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
    props = tools["workflow_query_scars"]["inputSchema"]["properties"]
    self.assertIn("excludeCategories", props)
    ex_schema = props["excludeCategories"]
    self.assertEqual(ex_schema.get("type"), "array")
    self.assertEqual(ex_schema.get("items"), {"type": "string"})
    self.assertTrue(
        ex_schema.get("description", "").startswith("Optional "),
        f"Expected Optional description, got {ex_schema!r}",
    )

  def test_workflow_query_scars_excludes_workflow_camel_and_snake_case(self):
    mcp_server = _load_mcp_server_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_exclude.db")
      self._build_scars_db(db_path)

      for req_id, key_name in ((902, "excludeCategories"), (903, "exclude_categories")):
        with self.subTest(key_name=key_name):
          resp = mcp_server.handle_tools_call(
              req_id,
              "workflow_query_scars",
              {
                  "language": "universal",
                  "workflowPhase": "Target 20: plan",
                  key_name: ["WORKFLOW"],
                  "limit": 10,
                  "dbPath": db_path,
                  "workspaceDir": tmp_dir,
                  "refreshLoaded": True,
              },
          )
          self.assertFalse(resp["result"].get("isError"), resp)
          payload = json.loads(resp["result"]["content"][0]["text"])
          self.assertEqual(payload["status"], "OK")
          returned_ids = {row["id"] for row in payload["scars"]}
          self.assertEqual(
              returned_ids,
              {"SCAR-ARCH-0001", "SCAR-API-0001", "SCAR-TEST-0001"},
          )

  def test_workflow_query_scars_rejects_invalid_exclude_categories(self):
    mcp_server = _load_mcp_server_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_exclude_err.db")
      self._build_scars_db(db_path)

      invalid_values = (
          "PROC",
          "",
          True,
          False,
          123,
          0,
          {"WORKFLOW": True},
          ["PROC", 42],
          ["PROC", True],
          ["PROC", None],
          ["PROC", {"WORKFLOW": True}],
      )
      for idx, bad_val in enumerate(invalid_values, start=910):
        with self.subTest(bad_val=bad_val):
          resp = mcp_server.handle_tools_call(
              idx,
              "workflow_query_scars",
              {
                  "language": "universal",
                  "excludeCategories": bad_val,
                  "dbPath": db_path,
                  "workspaceDir": tmp_dir,
              },
          )
          self.assertIs(resp["result"].get("isError"), True, resp)
          err_text = resp["result"]["content"][0]["text"]
          self.assertIn("excludeCategories", err_text)

  def test_workflow_query_scars_standalone_exclude_categories_and_empty_rejection(self):
    """Standalone excludeCategories succeeds without positive filters; empty or blank lists fail (GH-252)."""
    mcp_server = _load_mcp_server_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_exclude_standalone.db")
      self._build_scars_db(db_path)

      resp = mcp_server.handle_tools_call(
          930,
          "workflow_query_scars",
          {
              "excludeCategories": ["WORKFLOW"],
              "limit": 10,
              "dbPath": db_path,
              "workspaceDir": tmp_dir,
              "refreshLoaded": True,
          },
      )
      self.assertFalse(resp["result"].get("isError"), resp)
      payload = json.loads(resp["result"]["content"][0]["text"])
      self.assertEqual(payload["status"], "OK")
      self.assertEqual(
          {row["id"] for row in payload["scars"]},
          {"SCAR-ARCH-0001", "SCAR-API-0001", "SCAR-TEST-0001"},
      )

      for req_id, empty_val in (
          (931, []),
          (932, ["   "]),
          (933, ["", " \t "]),
      ):
        with self.subTest(empty_val=empty_val):
          err_resp = mcp_server.handle_tools_call(
              req_id,
              "workflow_query_scars",
              {
                  "excludeCategories": empty_val,
                  "dbPath": db_path,
                  "workspaceDir": tmp_dir,
              },
          )
          self.assertIs(err_resp["result"].get("isError"), True, err_resp)
          err_text = err_resp["result"]["content"][0]["text"]
          self.assertIn("Missing required argument 'query' or filter", err_text)

  def test_workflow_query_scars_empty_exclude_list_and_camel_case_precedence(self):
    """Empty excludeCategories returns unfiltered scars and overrides snake_case exclude_categories (GH-252)."""
    mcp_server = _load_mcp_server_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_exclude_precedence.db")
      self._build_scars_db(db_path)

      all_processed_ids = {
          "SCAR-ARCH-0001",
          "SCAR-API-0001",
          "SCAR-TEST-0001",
          "SCAR-PROC-0001",
          "SCAR-REVIEW-0001",
          "SCAR-GIT-0001",
      }

      resp_empty = mcp_server.handle_tools_call(
          940,
          "workflow_query_scars",
          {
              "language": "universal",
              "excludeCategories": [],
              "limit": 10,
              "dbPath": db_path,
              "workspaceDir": tmp_dir,
              "refreshLoaded": True,
          },
      )
      self.assertFalse(resp_empty["result"].get("isError"), resp_empty)
      payload_empty = json.loads(resp_empty["result"]["content"][0]["text"])
      self.assertEqual(payload_empty["status"], "OK")
      self.assertEqual(
          {row["id"] for row in payload_empty["scars"]},
          all_processed_ids,
      )

      resp_override = mcp_server.handle_tools_call(
          941,
          "workflow_query_scars",
          {
              "language": "universal",
              "excludeCategories": [],
              "exclude_categories": ["WORKFLOW"],
              "limit": 10,
              "dbPath": db_path,
              "workspaceDir": tmp_dir,
              "refreshLoaded": True,
          },
      )
      self.assertFalse(resp_override["result"].get("isError"), resp_override)
      payload_override = json.loads(resp_override["result"]["content"][0]["text"])
      self.assertEqual(payload_override["status"], "OK")
      self.assertEqual(
          {row["id"] for row in payload_override["scars"]},
          all_processed_ids,
      )

  def test_workflow_query_scars_combined_category_and_exclude_categories(self):
    """Combined category and excludeCategories handles compatible and contradictory MCP queries (GH-252)."""
    mcp_server = _load_mcp_server_module()
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "mcp_exclude_combined.db")
      self._build_scars_db(db_path)

      resp_compat = mcp_server.handle_tools_call(
          950,
          "workflow_query_scars",
          {
              "category": "ARCH",
              "excludeCategories": ["WORKFLOW"],
              "limit": 10,
              "dbPath": db_path,
              "workspaceDir": tmp_dir,
              "refreshLoaded": True,
          },
      )
      self.assertFalse(resp_compat["result"].get("isError"), resp_compat)
      payload_compat = json.loads(resp_compat["result"]["content"][0]["text"])
      self.assertEqual(payload_compat["status"], "OK")
      self.assertEqual(
          {row["id"] for row in payload_compat["scars"]},
          {"SCAR-ARCH-0001"},
      )

      resp_contra = mcp_server.handle_tools_call(
          951,
          "workflow_query_scars",
          {
              "category": "PROC",
              "excludeCategories": ["WORKFLOW"],
              "limit": 10,
              "dbPath": db_path,
              "workspaceDir": tmp_dir,
              "refreshLoaded": True,
          },
      )
      self.assertFalse(resp_contra["result"].get("isError"), resp_contra)
      payload_contra = json.loads(resp_contra["result"]["content"][0]["text"])
      self.assertEqual(payload_contra["status"], "OK")
      self.assertEqual(payload_contra["totalResults"], 0)
      self.assertEqual(payload_contra["scars"], [])


if __name__ == "__main__":
  unittest.main()
