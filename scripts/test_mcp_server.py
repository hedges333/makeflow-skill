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

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_SCRIPT = os.path.join(REPO_ROOT, "mcp", "server.py")


def send_rpc(proc, msg):
  proc.stdin.write(json.dumps(msg) + "\n")
  proc.stdin.flush()
  return json.loads(proc.stdout.readline())


def run_test():
  with tempfile.TemporaryDirectory() as non_git_cwd:
    p = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=non_git_cwd,
        text=True,
    )
    try:
      # 1. Initialize
      init_resp = send_rpc(
          p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
      )
      assert init_resp["id"] == 1
      assert (
          init_resp["result"]["serverInfo"]["name"] == "makeflow-guard"
      )
      assert init_resp["result"]["serverInfo"]["version"] == "1.4.1"
      print("✔ MCP initialize handshake passed.")

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
      ):
        assert expected in tool_names
      assert len(tool_names) == 7
      print(f"✔ MCP tools/list returned {len(tool_names)} tools: {tool_names}")

      # 3. Safety check: production hostname and underscore prod_db
      for idx, bad_cmd in enumerate(
          (
              "psql -h production.db.internal myapp",
              "mariadb -u app myapp_prod",
          ),
          start=3,
      ):
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
        assert content_bad["safe"] is False
      print("✔ MCP safety check blocked production database commands.")

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
      assert content_good["safe"] is True
      print("✔ MCP safety check allowed safe test command.")

      # 5. Advance target: unapproved plan blocked, illegal T10->T80 jump blocked, valid T10->T15 allowed
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
      assert adv_bad["result"]["isError"] is True

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
      assert adv_jump["result"]["isError"] is True

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
      assert not adv_ok["result"].get("isError")
      print("✔ MCP workflow_advance_target enforced DAG edges and human gates.")

      # 5b. Advance target: on-disk state card enforcement
      with tempfile.TemporaryDirectory() as tmp_state_ws:
        tmp_state_dir = os.path.join(tmp_state_ws, ".workflow", "state")
        os.makedirs(tmp_state_dir)

        # Missing state card blocks transition
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
        assert adv_no_card["result"]["isError"] is True
        err_msg = adv_no_card["result"]["content"][0]["text"]
        assert "20_plan_approved.md" in err_msg

        # Present state card permits transition
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
        assert not adv_with_card["result"].get("isError")
        print("✔ MCP workflow_advance_target verified on-disk state cards.")

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
      assert not adv_t60_t40["result"].get("isError"), (
          f"Expected T60 -> T40 transition to succeed, got {adv_t60_t40}"
      )

      for bad_target in ("T10", "T20", "T50", "T80"):
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
        assert adv_t60_bad["result"].get("isError") is True, (
            f"Expected T60 -> {bad_target} transition to be blocked"
        )
      print("✔ MCP workflow_advance_target T60 -> T40 transition and T60 invalid jumps verified.")

      # 6. Verify tree with workspaceDir when server cwd is not a git repo
      with tempfile.TemporaryDirectory() as tmp_git_ws:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=tmp_git_ws,
            check=True,
        )
        with open(
            os.path.join(tmp_git_ws, "README.md"), "w", encoding="utf-8"
        ) as f:
          f.write("# Test\n")
        subprocess.run(
            ["git", "add", "README.md"],
            cwd=tmp_git_ws,
            check=True,
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
        assert not tree_resp["result"].get("isError")
        tree_data = json.loads(tree_resp["result"]["content"][0]["text"])
        assert len(tree_data["actualTree"]) == 40
        print("✔ MCP workflow_verify_tree resolved workspaceDir cleanly.")

      # 7. Get config with default workspaceDir
      with tempfile.TemporaryDirectory() as empty_ws:
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
        assert "models" in cfg_data
        assert "agyModelTiers" in cfg_data
        for role in (
            "adversarial-critic",
            "archaeology-worker",
            "tdd-worker",
            "triage-worker",
        ):
          assert (
              cfg_data["models"].get(role) == "inherit"
          ), f"Expected default models[{role}] == 'inherit', got {cfg_data['models'].get(role)}"
          assert (
              cfg_data["agyModelTiers"].get(role) == "inherit"
          ), f"Expected default agyModelTiers[{role}] == 'inherit', got {cfg_data['agyModelTiers'].get(role)}"
        assert "review" in cfg_data
        assert "ci" in cfg_data
        assert "database" in cfg_data
        assert "prodDatabases" in cfg_data["database"]
        assert "testDatabases" in cfg_data["database"]
        assert "prodHosts" in cfg_data["database"]
        assert "state" in cfg_data
        assert cfg_data["state"]["completedCardAction"] == "unlink"
        print("✔ MCP workflow_get_config returned valid default configuration.")

      # 8. Custom database configuration in workspaceDir for workflow_get_config and workflow_safety_check
      with tempfile.TemporaryDirectory() as ws_cfg_dir:
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
                  }
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
        assert ws_cfg["models"]["adversarial-critic"] == "gemini-4-argon-high"
        assert ws_cfg["agyModelTiers"]["adversarial-critic"] == "pro"
        assert ws_cfg["models"]["archaeology-worker"] == "gemini-3.8-flash-low"
        assert ws_cfg["agyModelTiers"]["archaeology-worker"] == "flash"
        assert ws_cfg["models"]["tdd-worker"] == "flash_lite"
        assert ws_cfg["agyModelTiers"]["tdd-worker"] == "flash_lite"
        assert ws_cfg["models"]["triage-worker"] == "pro"
        assert ws_cfg["agyModelTiers"]["triage-worker"] == "pro"
        assert ws_cfg["database"]["prodDatabases"] == ["billing_live"]
        assert ws_cfg["database"]["testDatabases"] == ["billing_sandbox", "billing"]
        assert ws_cfg["database"]["prodHosts"] == ["db.company.internal"]

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
          assert sc_data["safe"] is expected_safe, (
              f"Expected safe={expected_safe} for '{cmd_str}', got {sc_data}"
          )
      print("✔ MCP custom database safety checks passed.")

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
        assert git_sc_data["safe"] is expected_git_safe, (
            f"Expected safe={expected_git_safe} for '{git_cmd_str}', got {git_sc_data}"
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
        assert adv_sc_data["safe"] is True, f"Expected safe=True on '{adv_cmd}'"
        assert "Requires human operator approval" in adv_sc_data["reason"], (
            f"Expected operator approval reason on '{adv_cmd}', got {adv_sc_data}"
        )
      print("✔ MCP destructive Git command safety checks passed.")

      # 8c. workflow_query_scars JSON-RPC checks
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
      assert no_query_resp["result"]["isError"] is True
      assert "Missing required argument 'query'" in no_query_resp["result"]["content"][0]["text"]

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
      assert uninit_data["status"] == "UNINITIALIZED"
      assert "Bundled registry database is missing from skill package" in uninit_data["message"]
      assert uninit_data["scars"] == []

      # Case c: Query when database exists with sample scars
      test_scars_db = os.path.join(non_git_cwd, "test_scars.db")
      conn = sqlite3.connect(test_scars_db)
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
              source TEXT DEFAULT 'upstream',
              original_id TEXT,
              raw_hash TEXT
          )
      """)
      conn.execute("""
          CREATE VIRTUAL TABLE scars_fts USING fts5(
              id UNINDEXED,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              source UNINDEXED,
              original_id UNINDEXED
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
              "upstream",
              None,
              "hash-test-08",
          ),
      ]
      for s in test_scars:
        conn.execute("INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", s)
        conn.execute("INSERT INTO scars_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (s[0], s[1], s[2], s[6], s[8], s[9], s[11], s[12], s[13]))
      conn.commit()
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
      assert query_data["status"] == "OK"
      assert query_data["totalResults"] >= 1
      assert query_data["scars"][0]["id"] == "SCAR-ARCH-05"
      assert "source" in query_data["scars"][0]
      assert query_data["scars"][0]["source"] == "upstream"

      # Query with source filter
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
                  },
              },
          },
      )
      src_data = json.loads(src_resp["result"]["content"][0]["text"])
      assert src_data["status"] == "OK"
      assert src_data["totalResults"] >= 1
      assert src_data["scars"][0]["source"] == "upstream"

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
      assert cat_data["status"] == "OK"
      assert cat_data["totalResults"] == 1
      assert cat_data["scars"][0]["id"] == "SCAR-TEST-08"

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
                  },
              },
          },
      )
      lim_data = json.loads(lim_resp["result"]["content"][0]["text"])
      assert lim_data["status"] == "OK"
      assert len(lim_data["scars"]) == 1

      # Case d: Query when dbPath is omitted -> resolves to bundled package database
      default_resp = send_rpc(
          p,
          {
              "jsonrpc": "2.0",
              "id": 255,
              "method": "tools/call",
              "params": {
                  "name": "workflow_query_scars",
                  "arguments": {
                      "query": "SCAR",
                      "workspaceDir": non_git_cwd,
                  },
              },
          },
      )
      default_data = json.loads(default_resp["result"]["content"][0]["text"])
      assert default_data["status"] == "OK"
      assert len(default_data["scars"]) > 0

      print("✔ MCP workflow_query_scars JSON-RPC checks passed.")
    finally:
      p.kill()
      p.wait()

  # 9. Environment variable database overrides
  with tempfile.TemporaryDirectory() as env_cwd:
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
    p_env = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=env_cwd,
        env=custom_env,
        text=True,
    )
    try:
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
      assert "ledger_live" in env_cfg["database"]["prodDatabases"]
      assert "ledger_test" in env_cfg["database"]["testDatabases"]
      assert "live-cluster.internal" in env_cfg["database"]["prodHosts"]
      assert env_cfg["models"]["adversarial-critic"] == "gemini-4-argon-high"
      assert env_cfg["agyModelTiers"]["adversarial-critic"] == "pro"
      assert env_cfg["models"]["archaeology-worker"] == "flash_lite"
      assert env_cfg["agyModelTiers"]["archaeology-worker"] == "flash_lite"
      assert env_cfg["models"]["tdd-worker"] == "pro"
      assert env_cfg["agyModelTiers"]["tdd-worker"] == "pro"
      assert env_cfg["models"]["triage-worker"] == "flash"
      assert env_cfg["agyModelTiers"]["triage-worker"] == "flash"
      assert env_cfg["ci"]["quiescenceSeconds"] == 40
      assert env_cfg["ci"]["pollIntervalSeconds"] == 8
      assert env_cfg["state"]["completedCardAction"] == "archive"
      assert any("MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION=archive" in ov for ov in env_cfg["envOverrides"])

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
      assert json.loads(env_sc_bad["result"]["content"][0]["text"])["safe"] is False

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
      assert json.loads(env_sc_good["result"]["content"][0]["text"])["safe"] is True
      print("✔ MCP environment variable database overrides passed.")

    finally:
      p_env.kill()
      p_env.wait()

  # 10. Single-item partial config override & workflow_poll_ci tests
  with tempfile.TemporaryDirectory() as partial_ws:
    os.makedirs(os.path.join(partial_ws, ".workflow"))
    with open(
        os.path.join(partial_ws, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump({"ci": {"quiescenceSeconds": 20}}, f)

    p_partial = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=partial_ws,
        text=True,
    )
    try:
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
      assert partial_cfg["ci"]["quiescenceSeconds"] == 20
      assert partial_cfg["ci"]["pollIntervalSeconds"] == 30, (
          f"Expected default 30s poll interval, got {partial_cfg['ci']['pollIntervalSeconds']}"
      )
      assert partial_cfg["models"]["adversarial-critic"] == "inherit"
      assert partial_cfg["review"]["strictSixPillars"] is True
      print("✔ MCP recursive deep-merge single-item override passed.")

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
      assert "ciStatus" in poll_data
      assert "quiescenceSatisfied" in poll_data
      assert "readyToLand" in poll_data
      assert "unresolvedBotCommentsCount" in poll_data
      assert poll_data["mode"] == "pure_git"
      print("✔ MCP workflow_poll_ci fallback handling passed.")
    finally:
      p_partial.kill()
      p_partial.wait()

  # 11. In-memory unit tests for poll_ci_status GitHub PR parsing & quiescence
  sys.path.insert(0, REPO_ROOT)
  from mcp.server import poll_ci_status
  from unittest.mock import patch, MagicMock

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
  with patch("shutil.which", return_value="/usr/bin/gh"), patch(
      "subprocess.run"
  ) as mock_run:
    mock_run.return_value = MagicMock(
        stdout=json.dumps(mock_data_bot), stderr="", returncode=0
    )
    res = poll_ci_status(
        ws=REPO_ROOT,
        pr_number=123,
        quiescence_sec=10,
        max_wait_sec=1,
        hosting_mode="github",
    )
    assert res["ciStatus"] == "SUCCESS"
    assert res["readyToLand"] is False
    assert res["unresolvedBotCommentsCount"] == 1

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
  with patch("shutil.which", return_value="/usr/bin/gh"), patch(
      "subprocess.run"
  ) as mock_run:
    mock_run.return_value = MagicMock(
        stdout=json.dumps(mock_data_clean), stderr="", returncode=0
    )
    res = poll_ci_status(
        ws=REPO_ROOT,
        pr_number=123,
        quiescence_sec=10,
        max_wait_sec=1,
        hosting_mode="github",
    )
    assert res["ciStatus"] == "SUCCESS"
    assert res["readyToLand"] is True
    assert res["unresolvedBotCommentsCount"] == 0

  mock_data_fail = {
      "statusCheckRollup": [
          {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}
      ],
      "comments": [],
      "reviews": [],
  }
  with patch("shutil.which", return_value="/usr/bin/gh"), patch(
      "subprocess.run"
  ) as mock_run:
    mock_run.return_value = MagicMock(
        stdout=json.dumps(mock_data_fail), stderr="", returncode=0
    )
    res = poll_ci_status(
        ws=REPO_ROOT,
        pr_number=123,
        quiescence_sec=10,
        max_wait_sec=1,
        hosting_mode="github",
    )
    assert res["ciStatus"] == "FAILURE"
    assert res["failedChecks"] == ["lint"]
  print("✔ MCP poll_ci_status in-memory branch coverage passed.")

  # 15. Plugin directory isolation test
  with tempfile.TemporaryDirectory() as mock_plugin_dir, tempfile.TemporaryDirectory() as ws_dir:
    with open(os.path.join(mock_plugin_dir, "plugin.json"), "w", encoding="utf-8") as f:
      json.dump({"name": "makeflow"}, f)
    plugin_state = os.path.join(mock_plugin_dir, ".workflow", "state")
    os.makedirs(plugin_state)
    with open(os.path.join(plugin_state, "10_ticket.md"), "w", encoding="utf-8") as f:
      f.write("# Ticket\n- Ticket ID: GH-999\n")

    p_iso = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=mock_plugin_dir,
        text=True,
    )
    try:
      send_rpc(p_iso, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

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
      assert "GH-999" not in text_out, f"Leaked plugin cards: {text_out}"
      assert res_obj.get("isError") is True or "does not exist" in text_out or "BLOCKED" in text_out

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
      assert "GH-999" not in text_ws
      assert "does not exist" in text_ws

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
      text_empty_st = resp_empty_st.get("result", {}).get("content", [{}])[0].get("text", "")
      assert "clean (0 state cards)" in text_empty_st
      assert "Ready for Target 10 intake" in text_empty_st

      # GH-74: workflow_get_state on completed ticket
      with open(os.path.join(empty_st, "10_ticket.md"), "w", encoding="utf-8") as f:
        f.write("# Ticket Intake: GH-20\n")
      with open(os.path.join(empty_st, "99_next_action.md"), "w", encoding="utf-8") as f:
        f.write("# Target 90 Finished: COMPLETE\n- Status: COMPLETE (make finish satisfied)\n")
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
      text_comp_st = resp_comp_st.get("result", {}).get("content", [{}])[0].get("text", "")
      assert "Previous ticket is complete" in text_comp_st
      assert "Ready for Target 10 intake" in text_comp_st
    finally:
      p_iso.kill()
      p_iso.wait()
  print("✔ MCP plugin directory isolation passed.")

  # 16. Unit tests for detect_hosting_mode and poll_ci_status hosting mode integration (GH-76)
  from mcp.server import detect_hosting_mode

  # Case a: No remote (or git remote get-url origin returns non-zero) -> "pure_git"
  with patch("subprocess.run") as mock_run:
    mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: No such remote 'origin'")
    assert detect_hosting_mode("/fake/repo") == "pure_git"

  # Case b: Non-GitHub remote (e.g. git@gitlab.com:org/repo.git or https://gitlab.com/org/repo.git) -> "pure_git"
  for non_gh_url in [
      "git@gitlab.com:org/repo.git",
      "https://gitlab.com/org/repo.git",
      "https://bitbucket.org/org/repo.git",
      "file:///local/path/repo.git",
  ]:
    with patch("subprocess.run") as mock_run:
      mock_run.return_value = MagicMock(returncode=0, stdout=non_gh_url + "\n", stderr="")
      assert detect_hosting_mode("/fake/repo") == "pure_git", f"Failed for {non_gh_url}"

  # Case c: GitHub remote, but gh binary missing -> "pure_git"
  with patch("subprocess.run") as mock_run, patch("shutil.which", return_value=None):
    mock_run.return_value = MagicMock(returncode=0, stdout="git@github.com:org/repo.git\n", stderr="")
    assert detect_hosting_mode("/fake/repo") == "pure_git"

  # Case d: GitHub remote, gh present, but gh auth status non-zero -> "pure_git"
  def mock_subp_auth_fail(cmd, **kwargs):
    if cmd[:3] == ["git", "remote", "get-url"]:
      return MagicMock(returncode=0, stdout="https://github.com/org/repo.git\n", stderr="")
    if cmd[:3] == ["gh", "auth", "status"]:
      return MagicMock(returncode=1, stdout="", stderr="You are not logged into any GitHub hosts")
    return MagicMock(returncode=0, stdout="", stderr="")

  with patch("shutil.which", return_value="/usr/bin/gh"), patch(
      "subprocess.run", side_effect=mock_subp_auth_fail
  ):
    assert detect_hosting_mode("/fake/repo") == "pure_git"

  # Case e: GitHub remote, gh auth status 0, but gh repo view non-zero -> "pure_git"
  def mock_subp_repo_fail(cmd, **kwargs):
    if cmd[:3] == ["git", "remote", "get-url"]:
      return MagicMock(returncode=0, stdout="https://github.com/org/repo.git\n", stderr="")
    if cmd[:3] == ["gh", "auth", "status"]:
      return MagicMock(returncode=0, stdout="Logged in to github.com\n", stderr="")
    if cmd[:3] == ["gh", "repo", "view"]:
      return MagicMock(returncode=1, stdout="", stderr="GraphQL: Could not resolve to a Repository")
    return MagicMock(returncode=0, stdout="", stderr="")

  with patch("shutil.which", return_value="/usr/bin/gh"), patch(
      "subprocess.run", side_effect=mock_subp_repo_fail
  ):
    assert detect_hosting_mode("/fake/repo") == "pure_git"

  # Case f: GitHub remote, gh auth status 0, and gh repo view 0 -> "github"
  def mock_subp_all_pass(cmd, **kwargs):
    if cmd[:3] == ["git", "remote", "get-url"]:
      return MagicMock(returncode=0, stdout="https://github.com/org/repo.git\n", stderr="")
    if cmd[:3] == ["gh", "auth", "status"]:
      return MagicMock(returncode=0, stdout="Logged in to github.com\n", stderr="")
    if cmd[:3] == ["gh", "repo", "view"]:
      return MagicMock(returncode=0, stdout='{"name": "repo"}', stderr="")
    return MagicMock(returncode=0, stdout="", stderr="")

  with patch("shutil.which", return_value="/usr/bin/gh"), patch(
      "subprocess.run", side_effect=mock_subp_all_pass
  ):
    assert detect_hosting_mode("/fake/repo") == "github"

  # Case g: Integration test in poll_ci_status: when detect_hosting_mode returns "pure_git",
  # poll_ci_status returns mode: "pure_git" cleanly.
  with patch("mcp.server.detect_hosting_mode", return_value="pure_git"):
    res_pure = poll_ci_status(
        pr_number=99, ws="/fake/repo", quiescence_sec=10, max_wait_sec=1
    )
    assert res_pure["mode"] == "pure_git"
    assert res_pure["ciStatus"] == "SKIPPED"
    assert res_pure["readyToLand"] is True
    assert "Pure Git Mode" in res_pure["message"]

  print("✔ detect_hosting_mode 6 branch cases and poll_ci_status pure_git integration passed.")

  print("\nAll MCP Server test cases passed.")


class TestMcpServer(unittest.TestCase):
  """Unittest wrapper for MCP server test suite."""

  def test_mcp_server(self):
    run_test()


if __name__ == "__main__":
  run_test()
