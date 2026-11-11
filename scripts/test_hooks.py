#!/usr/bin/env python3
"""Test suite for SessionStart and PreToolUse hook scripts."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRE_TOOL = os.path.join(REPO_ROOT, "scripts", "pre_tool_guard.py")
SESSION_START = os.path.join(REPO_ROOT, "scripts", "session_start.py")
MCP_SERVER = os.path.join(REPO_ROOT, "mcp", "server.py")


def run_hook(script_path, payload, cwd=None, env=None):
  proc = subprocess.run(
      [sys.executable, script_path],
      input=json.dumps(payload),
      text=True,
      capture_output=True,
      cwd=cwd or REPO_ROOT,
      env=env,
      check=True,
  )
  return json.loads(proc.stdout)


def test_pre_tool_guard():
  # 1. Hostname with 'production' is denied
  r1 = run_hook(
      PRE_TOOL,
      {
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "psql -h production.db.internal myapp"},
          }
      },
  )
  assert r1["decision"] == "deny"

  # 2. Underscore database name 'myapp_prod' with mariadb is denied
  r2 = run_hook(
      PRE_TOOL,
      {
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "mariadb -u root myapp_prod"},
          }
      },
  )
  assert r2["decision"] == "deny"

  # 3. Safe *_test database and 'product' table name are allowed
  r3 = run_hook(
      PRE_TOOL,
      {
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "psql -h localhost myapp_test -c 'SELECT * FROM product'"
                  )
              },
          }
      },
  )
  assert r3["decision"] == "allow"

  # 4. Force-push to main is denied
  r4 = run_hook(
      PRE_TOOL,
      {
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git push --force origin main"},
          }
      },
  )
  assert r4["decision"] == "deny"

  # 5. git commit checks workspacePaths/.workflow/state/ even when cwd is /tmp
  with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as hook_cwd:
    r5_missing = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: add test'"},
            },
        },
        cwd=hook_cwd,
    )
    assert r5_missing["decision"] == "force_ask"
    assert "missing" in r5_missing["reason"]

    state_dir = os.path.join(ws_dir, ".workflow", "state")
    os.makedirs(state_dir)
    with open(os.path.join(state_dir, "45_review_approved.md"), "w", encoding="utf-8") as f:
      f.write("APPROVED\n")

    r5_present = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: add test'"},
            },
        },
        cwd=hook_cwd,
    )
    assert r5_present["decision"] == "force_ask"
    assert "present" in r5_present["reason"]

    # Regression test for BLOCKER-2: Realistic 99_next_action.md with static milestone list
    with open(
        os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
    ) as f:
      f.write(
          "# 99_next_action: Live Memento Readerboard\n\n"
          "- Ticket: [GH-42: Fix auth timeout](https://github.com/org/repo/issues/42)\n"
          "- Branch: feat/42-auth-timeout\n"
          "- Active Target: Target 50 (commit)\n"
          "- Last Completed Step: Target 45 (approved-review)\n\n"
          "## Milestone Progress\n"
          "- [x] T00: read-init-d\n"
          "- [x] T10: ticket-assigned\n"
          "- [x] T15: archaeology-verified\n"
          "- [x] T20: approved-plan\n"
          "- [x] T30: implementation-diff\n"
          "- [x] T40: adversarial-review\n"
          "- [x] T45: approved-review\n"
          "- [>] T50: commit\n"
          "- [ ] T60: push\n"
          "- [ ] T70: bot-quiescence\n"
          "- [ ] T75: ready-to-land\n"
          "- [ ] T80: land\n"
          "- [ ] T90: finish (History Ledger)\n"
      )

    r5_milestones_present = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: regular feature commit'"},
            },
        },
        cwd=hook_cwd,
    )
    assert r5_milestones_present["decision"] == "force_ask"
    assert "Target 50 Commit Gate" in r5_milestones_present["reason"]
    assert "Target 90 History Ledger Gate" not in r5_milestones_present["reason"]
    assert "present" in r5_milestones_present["reason"]

    # Also test missing review card in presence of 99_next_action.md
    os.remove(os.path.join(state_dir, "45_review_approved.md"))
    r5_milestones_missing = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: regular feature commit'"},
            },
        },
        cwd=hook_cwd,
    )
    assert r5_milestones_missing["decision"] == "force_ask"
    assert "Target 50 Commit Gate" in r5_milestones_missing["reason"]
    assert "Target 90 History Ledger Gate" not in r5_milestones_missing["reason"]
    assert "missing" in r5_milestones_missing["reason"]

    # Restore 45_review_approved.md
    with open(os.path.join(state_dir, "45_review_approved.md"), "w", encoding="utf-8") as f:
      f.write("APPROVED\n")

    # 6. git push and gh pr merge always prompt with force_ask
    r6 = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push -u origin feat/1-test"},
            },
        },
        cwd=hook_cwd,
    )
    assert r6["decision"] == "force_ask"

    r7 = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "gh pr merge 1 --squash"},
            },
        },
        cwd=hook_cwd,
    )
    assert r7["decision"] == "force_ask"

    # 6b. Target 90 History Ledger commit and push gates
    # a) Commit with docs(history): message returns Target 90 History Ledger Gate
    r_t90_commit_msg = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": "git commit -m 'docs(history): record GH-4'"
                },
            },
        },
        cwd=hook_cwd,
    )
    assert r_t90_commit_msg["decision"] == "force_ask"
    assert "Target 90 History Ledger Gate" in r_t90_commit_msg["reason"]
    assert "Target 50 Commit Gate" not in r_t90_commit_msg["reason"]

    # Commit under Target 90 state card (99_next_action.md indicating Target 90)
    with open(
        os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
    ) as f:
      f.write("# 99_next_action\n- Active Target: Target 90 (finish)\n")

    r_t90_commit_state = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        "git commit -m 'chore: update ledger'"
                        " .workflow/history.json"
                    )
                },
            },
        },
        cwd=hook_cwd,
    )
    assert r_t90_commit_state["decision"] == "force_ask"
    assert "Target 90 History Ledger Gate" in r_t90_commit_state["reason"]
    assert "Target 50 Commit Gate" not in r_t90_commit_state["reason"]

    # b) Push command on branch main under Target 90 state returns Target 90 History Push Gate
    r_t90_push = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push origin main"},
            },
        },
        cwd=hook_cwd,
    )
    assert r_t90_push["decision"] == "force_ask"
    assert "Target 90 History Push Gate" in r_t90_push["reason"]
    assert "Target 60 Push Gate" not in r_t90_push["reason"]

    # 7. Configured prodDatabases, prodHosts, and testDatabases in .workflow/config.json
    with open(
        os.path.join(ws_dir, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump(
          {
              "database": {
                  "prodDatabases": ["billing_live"],
                  "testDatabases": ["myapp_test"],
                  "prodHosts": ["db.company.internal"],
              }
          },
          f,
      )

    r8_custom_prod = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "psql -h localhost billing_live"},
            },
        },
        cwd=hook_cwd,
    )
    assert r8_custom_prod["decision"] == "deny"

    r9_custom_host = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "psql -h db.company.internal myapp_test"},
            },
        },
        cwd=hook_cwd,
    )
    assert r9_custom_host["decision"] == "deny"

    r10_test_db_prod_col = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        "psql -h localhost myapp_test -c 'SELECT prod FROM items'"
                    )
                },
            },
        },
        cwd=hook_cwd,
    )
    assert r10_test_db_prod_col["decision"] == "allow"

    r10_prod_host_still_denied = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        "psql -h prod.db.internal myapp_test -c 'SELECT 1'"
                    )
                },
            },
        },
        cwd=hook_cwd,
    )
    assert r10_prod_host_still_denied["decision"] == "deny"

    # 8. Environment variable database overrides
    custom_env = dict(os.environ)
    custom_env["MAKEFILE_WORKFLOW_PROD_DBS"] = "core_live,analytics_main"
    custom_env["MAKEFILE_WORKFLOW_TEST_DBS"] = "analytics_sandbox"
    custom_env["MAKEFILE_WORKFLOW_PROD_HOSTS"] = "rds.live.internal"

    r11_env_prod = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "psql -h localhost core_live"},
            },
        },
        cwd=hook_cwd,
        env=custom_env,
    )
    assert r11_env_prod["decision"] == "deny"

    r11_env_host = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": "psql -h rds.live.internal analytics_sandbox"
                },
            },
        },
        cwd=hook_cwd,
        env=custom_env,
    )
    assert r11_env_host["decision"] == "deny"

    r11_env_test = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        "psql -h localhost analytics_sandbox -c 'SELECT prod FROM events'"
                    )
                },
            },
        },
        cwd=hook_cwd,
        env=custom_env,
    )
    assert r11_env_test["decision"] == "allow"

    # 9. Compound commands, quoted/PGHOST/host= flags, hyphenated names, multi-DB args, URI userinfo@, and non-string JSON items
    with open(
        os.path.join(ws_dir, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump(
          {
              "database": {
                  "prodDatabases": [1, True, None, "billing-live"],
                  "testDatabases": [1, False, None, "myapp_test", "billing"],
                  "prodHosts": [1, None, "db.company.internal"],
              }
          },
          f,
      )

    for blocked_cmd in (
        "pg_dump myapp_prod | psql myapp_test",
        "psql myapp_test -c 'SELECT 1' && psql myapp_prod -c 'DROP TABLE users'",
        'psql -h "prod.db.internal" myapp_test',
        "psql --host='prod.db.internal' myapp_test",
        "PGHOST=prod.db.internal psql myapp_test",
        "psql -hprod.db.internal myapp_test",
        'psql "host=prod.db.internal dbname=myapp_test"',
        "psql -h localhost billing-prod",
        "psql -h localhost billing-live",
        "pg_restore -d myapp_prod myapp_test.dump",
        'sqlite3 prod.db ".backup myapp_test.db"',
        "psql -h 127.0.0.1 myapp_prod",
    ):
      res_blocked = run_hook(
          PRE_TOOL,
          {
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": blocked_cmd},
              },
          },
          cwd=hook_cwd,
      )
      assert res_blocked["decision"] == "deny", (
          f"Expected deny for '{blocked_cmd}', got {res_blocked}"
      )

    for allowed_cmd in (
        'psql "postgresql://prod_user:secret@localhost:5432/myapp_test" -c "SELECT prod FROM items"',
        "psql -h 127.0.0.1 myapp_test -c 'SELECT 1; SELECT prod FROM items'",
        'sqlite3 myapp_test.db "SELECT prod FROM items"',
    ):
      res_allowed = run_hook(
          PRE_TOOL,
          {
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": allowed_cmd},
              },
          },
          cwd=hook_cwd,
      )
      assert res_allowed["decision"] == "allow", (
          f"Expected allow for '{allowed_cmd}', got {res_allowed}"
      )

    # 10. Destructive Git command guards (SCAR-GIT-25): deny work-discarding & force-push, force_ask rebase & branch -D, allow normal workflow Git commands
    for denied_git_cmd in (
        "git reset --hard",
        "git reset --hard HEAD~1",
        "git reset --merge",
        "git reset --keep",
        "git clean -f",
        "git clean -fd",
        "git clean -xfd",
        "git clean --force",
        "git checkout -- README.md",
        "git checkout HEAD -- scripts/pre_tool_guard.py",
        "git checkout .",
        "git checkout ./scripts",
        "git checkout HEAD README.md",
        "git checkout -f main",
        "git checkout --force main",
        "git switch -f main",
        "git switch --force main",
        "git switch --discard-changes main",
        "git restore README.md",
        "git restore .",
        "git restore -W README.md",
        "git restore --staged --worktree README.md",
        "git restore -SW README.md",
        "git stash drop",
        "git stash drop stash@{0}",
        "git stash clear",
        "git filter-branch --tree-filter 'rm -f passwords.txt' HEAD",
        "git filter-repo --path README.md",
        "git reflog delete HEAD@{1}",
        "git reflog expire --expire=now --all",
        "git update-ref -d refs/heads/feat/old",
        "git push --force origin feat/2-test",
        "git push -f origin feat/2-test",
        "git push -uf origin feat/2-test",
        "git push --force-with-lease origin feat/2-test",
        "git push --force-with-lease=feat/2-test:abcdef origin feat/2-test",
        "git push --force-if-includes origin feat/2-test",
        "git push origin +HEAD:feat/2-test",
        "git push origin +feat/2-test",
        "git --no-pager push --force origin main",
        "git push --force-with-lease origin main",
        "git push origin +HEAD:main",
        "git push --force-if-includes origin main",
        "git push origin +main",
        "git --exec-path /path reset --hard",
        "git --exec-path /path push --force origin main",
        "git --attr-source HEAD reset --hard",
        "git -C /tmp reset --hard",
        "git -C/tmp reset --hard",
        "GIT_PAGER=cat git clean -fd",
        "env GIT_TERMINAL_PROMPT=0 git reset --hard",
        "git status && git reset --hard",
        "git rebase main && git reset --hard",
        'echo "$(git reset --hard)"',
        'bash -c "git clean -fd"',
        'bash -euo pipefail -c "git reset --hard"',
        "(cd /tmp && git reset --hard)",
        "timeout 30 git reset --hard",
        "nice -n 10 git clean -fd",
        "git checkout '*.py'",
        "git update-ref --stdin",
        "git clean -i",
        "git clean --interactive",
        "git checkout -p",
        "git checkout --patch",
        "git checkout --discard-changes main",
        "git push --mirror origin",
        # BLOCKER-1 regressions
        "git -- reset --hard",
        "git -- clean -fd",
        "git -- push --force origin main",
        "git clean -n --no-dry-run -fd",
        "git restore --staged --no-staged README.md",
        "git checkout --ours README.md",
        "git checkout --theirs README.md",
        "git checkout -2 README.md",
        "git checkout -3 README.md",
        "git checkout --pathspec-from-file=paths.txt",
        "git checkout --pathspec-from-file paths.txt",
        "git -c clean.requireForce=false clean -d",
        "git -cclean.requireForce=0 clean -d",
        "git -c remote.origin.push=+HEAD:main push origin",
        # BLOCKER-2 redirection bypass regressions
        "git 2>&1 reset --hard",
        "psql 2>&1 -h prod.db.internal myapp",
        ">/dev/null git reset --hard",
        "> /dev/null git clean -fd",
        "git 2>/dev/null reset --hard",
        # BLOCKER-3 compound shell keywords, sh -c flag order, eval, env -S
        'for f in README.md; do git checkout -- "$f"; done',
        "if ! make test; then git reset --hard; fi",
        "if false; then :; else git clean -fd; fi",
        "! git reset --hard",
        'sh -c -- "git reset --hard"',
        'bash -c -e "git reset --hard"',
        'bash -co pipefail "git reset --hard"',
        'bash -O extglob -c "git reset --hard"',
        'eval "git reset --hard"',
        "eval git reset --hard",
        'env -S "git reset --hard"',
        'env -S"git reset --hard"',
        'env --split-string="git reset --hard"',
        'env --split-string "git reset --hard"',
        # BLOCKER-4 multiline comments with apostrophes, ANSI-C quotes, unspaced fallback
        "# Don't fail if dirty\ngit reset --hard",
        "# it's step 1\ngit reset --hard\n# it's step 2",
        "echo $'a\\'b';git reset --hard",
        'echo "unterminated;git reset --hard',
        'bash -c "psql -h prod.db.internal myapp"',
        'git commit -m "unterminated && git reset --hard',
    ):
      res_git_deny = run_hook(
          PRE_TOOL,
          {
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": denied_git_cmd},
              },
          },
          cwd=hook_cwd,
      )
      assert res_git_deny["decision"] == "deny", (
          f"Expected deny for '{denied_git_cmd}', got {res_git_deny}"
      )

    for ask_git_cmd in (
        "git rebase main",
        "git rebase origin/main",
        "git -C /tmp rebase main",
        "git branch -D feat/old",
        "git branch -df feat/old",
        "git branch -d -f feat/old",
        "git branch --delete --force feat/old",
        "git branch -f main HEAD~2",
        "git branch -M old main",
        "git branch -C old main",
        'git -C . commit -m "bypass"',
        'git --exec-path /path commit -m "test"',
        'git --attr-source HEAD commit -m "test"',
        'git -- commit -m "feat: test"',
        "echo ok && git rebase main",
        'git commit -m "block git reset --hard and git clean -fd"',
    ):
      res_git_ask = run_hook(
          PRE_TOOL,
          {
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": ask_git_cmd},
              },
          },
          cwd=hook_cwd,
      )
      assert res_git_ask["decision"] == "force_ask", (
          f"Expected force_ask for '{ask_git_cmd}', got {res_git_ask}"
      )

    for safe_git_cmd in (
        "git status --short",
        "git diff --staged",
        "git add -A",
        "git write-tree",
        "git checkout main",
        "git checkout main 2>/dev/null",
        "git checkout main 2>&1",
        "git checkout main > ./checkout.log",
        "git checkout main &>/dev/null",
        "git checkout main 2>/dev/null || git checkout -b main",
        "git restore -W --no-worktree -S README.md",
        "git checkout feat/2-git-command-protections",
        "git checkout -b feat/2-git-command-protections",
        "git checkout -b feat/2-git-command-protections origin/main",
        "git switch main",
        "git switch -c feat/2-git-command-protections",
        "git reset HEAD README.md",
        "git reset --soft HEAD~1",
        "git restore --staged README.md",
        "git restore -S .",
        "git clean -n",
        "git clean -nd",
        "git clean -n -fd",
        "git clean -n -efoo",
        "git stash",
        "git stash pop",
        "git stash list",
        "git branch -d feat/1-done",
        "git branch -d feat/1-Destroy",
        "git merge --ff-only feat/1-done",
        "git merge-tree $(git merge-base HEAD origin/main) HEAD origin/main",
        'echo "do not run git reset --hard or git push --force"',
        "echo 'never run $(git reset --hard) or git commit'",
    ):
      res_git_safe = run_hook(
          PRE_TOOL,
          {
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": safe_git_cmd},
              },
          },
          cwd=hook_cwd,
      )
      assert res_git_safe["decision"] == "allow", (
          f"Expected allow for '{safe_git_cmd}', got {res_git_safe}"
      )

  print("✔ PreToolUse hook assertions passed.")


def test_session_start():
  with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as hook_cwd:
    # 1. Valid config with models and database lists
    state_dir = os.path.join(ws_dir, ".workflow", "state")
    os.makedirs(state_dir)
    with open(os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8") as f:
      f.write("# Ticket 1\n")
    with open(
        os.path.join(ws_dir, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump(
          {
              "models": {"adversarial-critic": "gemini-4-argon-high"},
              "ci": {
                  "quiescenceSeconds": 45,
                  "pollIntervalSeconds": 15,
              },
              "database": {
                  "prodDatabases": ["billing_live", 1, None],
                  "testDatabases": ["billing_test"],
              },
          },
          f,
      )

    res = run_hook(
        SESSION_START,
        {"workspacePaths": [ws_dir]},
        cwd=hook_cwd,
    )
    steps = res.get("injectSteps", [])
    assert len(steps) == 1
    msg = steps[0]["systemMessage"]["systemMessage"]
    assert "1 state card(s)" in msg
    assert "gemini-4-argon-high" in msg
    assert "billing_live" in msg
    assert "None" not in msg
    assert "ci.quiescence=45s" in msg
    assert "ci.poll=15s" in msg

    # 1b. Single-item partial config override (preserves default poll interval)
    with open(
        os.path.join(ws_dir, ".workflow", "config.json"),
        "w",
        encoding="utf-8",
    ) as f:
      json.dump({"ci": {"quiescenceSeconds": 20}}, f)

    res_partial = run_hook(
        SESSION_START,
        {"workspacePaths": [ws_dir]},
        cwd=hook_cwd,
    )
    msg_partial = res_partial["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "ci.quiescence=20s" in msg_partial
    assert "ci.poll=30s" in msg_partial

    # 2. Environment variable database overrides in SessionStart
    env_override = dict(os.environ)
    env_override["MAKEFILE_WORKFLOW_PROD_DBS"] = "warehouse_live"
    env_override["MAKEFILE_WORKFLOW_TEST_DBS"] = "warehouse_test"
    env_override["MAKEFILE_WORKFLOW_PROD_HOSTS"] = "warehouse.internal"
    env_override["MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS"] = "25"
    env_override["MAKEFILE_WORKFLOW_CI_POLL_INTERVAL"] = "5"
    res_env = run_hook(
        SESSION_START,
        {"workspacePaths": [ws_dir]},
        cwd=hook_cwd,
        env=env_override,
    )
    msg_env = res_env["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "warehouse_live" in msg_env
    assert "warehouse_test" in msg_env
    assert "warehouse.internal" in msg_env
    assert "ci.quiescence=25s" in msg_env
    assert "ci.poll=5s" in msg_env

    # 3. Malformed or non-dict config values must never drop Target 00 state card restoration
    for bad_cfg_raw in (
        "{not valid json",
        json.dumps(["not", "a", "dict"]),
        json.dumps({"models": None, "database": "invalid_string"}),
        json.dumps({"models": "bad", "database": {"prodDatabases": "not_a_list"}}),
    ):
      with open(
          os.path.join(ws_dir, ".workflow", "config.json"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write(bad_cfg_raw)
      res_bad = run_hook(
          SESSION_START,
          {"workspacePaths": [ws_dir]},
          cwd=hook_cwd,
          env=env_override,
      )
      steps_bad = res_bad.get("injectSteps", [])
      assert len(steps_bad) == 1, f"Dropped state card prompt on {bad_cfg_raw}"
      assert "1 state card(s)" in steps_bad[0]["systemMessage"]["systemMessage"]

    # 4. GH-152: SessionStart must not emit external simple-english plugin or ASD-STE100 advisories
    with tempfile.TemporaryDirectory(prefix="fake_home_") as fake_home:
      env_no_se = dict(os.environ)
      env_no_se["HOME"] = fake_home
      res_no_se = run_hook(
          SESSION_START,
          {"workspacePaths": [ws_dir]},
          cwd=hook_cwd,
          env=env_no_se,
      )
      msg_no_se = res_no_se["injectSteps"][0]["systemMessage"]["systemMessage"]
      assert "simple-english" not in msg_no_se.lower()
      assert "asd-ste100" not in msg_no_se.lower()

    # 5. GH-74: Ticket lifecycle and clean intake check in SessionStart
    # 5a. Empty state directory reports clean and ready for Target 10 intake
    with tempfile.TemporaryDirectory() as empty_ws:
      empty_state = os.path.join(empty_ws, ".workflow", "state")
      os.makedirs(empty_state)
      res_empty = run_hook(
          SESSION_START,
          {"workspacePaths": [empty_ws]},
          cwd=hook_cwd,
      )
      steps_empty = res_empty.get("injectSteps", [])
      assert len(steps_empty) == 1
      msg_empty = steps_empty[0]["systemMessage"]["systemMessage"]
      assert "clean and ready for Target 10 intake" in msg_empty
      assert "Read all state cards at Target 00" not in msg_empty

    # 5b. Completed ticket (99_next_action.md indicating COMPLETE) reports clean intake readiness
    with tempfile.TemporaryDirectory() as completed_ws:
      comp_state = os.path.join(completed_ws, ".workflow", "state")
      os.makedirs(comp_state)
      with open(os.path.join(comp_state, "10_ticket.md"), "w", encoding="utf-8") as f:
        f.write("# Ticket Intake: GH-10\n")
      with open(os.path.join(comp_state, "99_next_action.md"), "w", encoding="utf-8") as f:
        f.write("# Target 90 Finished: COMPLETE\n- Status: COMPLETE (make finish satisfied)\n")
      res_comp = run_hook(
          SESSION_START,
          {"workspacePaths": [completed_ws]},
          cwd=hook_cwd,
      )
      steps_comp = res_comp.get("injectSteps", [])
      assert len(steps_comp) == 1
      msg_comp = steps_comp[0]["systemMessage"]["systemMessage"]
      assert "clean and ready for Target 10 intake" in msg_comp
      assert "Read all state cards at Target 00" not in msg_comp

    # 5c. Active ticket in progress reports state cards and prompts Target 00
    with tempfile.TemporaryDirectory() as active_ws:
      active_state = os.path.join(active_ws, ".workflow", "state")
      os.makedirs(active_state)
      with open(os.path.join(active_state, "10_ticket.md"), "w", encoding="utf-8") as f:
        f.write("# Ticket Intake: GH-11\n")
      with open(os.path.join(active_state, "99_next_action.md"), "w", encoding="utf-8") as f:
        f.write("# 99_next_action: Live Memento Readerboard\n- Active Target: Target 15\n- Last Completed Step: Target 10\n")
      res_active = run_hook(
          SESSION_START,
          {"workspacePaths": [active_ws]},
          cwd=hook_cwd,
      )
      steps_active = res_active.get("injectSteps", [])
      assert len(steps_active) == 1
      msg_active = steps_active[0]["systemMessage"]["systemMessage"]
      assert "2 state card(s)" in msg_active
      assert "Read all state cards at Target 00" in msg_active

  print("✔ SessionStart hook assertions passed.")


def _load_module(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_hook_mcp_parity():
  guard_mod = _load_module("pre_tool_guard", PRE_TOOL)
  mcp_mod = _load_module("mcp_server", MCP_SERVER)

  parity_commands = (
      "git reset --hard",
      "git -- reset --hard",
      "git clean -fd",
      "git clean -n --no-dry-run -fd",
      "git clean -nd",
      "git checkout -- README.md",
      "git checkout --ours README.md",
      "git checkout main 2>&1",
      "git checkout main 2>/dev/null",
      "git 2>&1 reset --hard",
      "git restore README.md",
      "git restore --staged README.md",
      "git restore --staged --no-staged README.md",
      "git stash drop",
      "git update-ref --stdin",
      "git push --force origin feat/2-test",
      "git push --mirror origin",
      "git push -u origin feat/2-test",
      "git --no-pager push --force origin main",
      "git push --force-with-lease origin main",
      "git push origin +HEAD:main",
      "git push --force-if-includes origin main",
      "git push origin +main",
      'git -C . commit -m "bypass"',
      "git --exec-path /path reset --hard",
      'git --exec-path /path commit -m "test"',
      "git --attr-source HEAD reset --hard",
      'git --attr-source HEAD commit -m "test"',
      "git rebase main",
      "git branch -D feat/old",
      "git branch -M old main",
      "git branch -d feat/1-done",
      'git commit -m "feat: add guard"',
      "gh pr merge 2 --squash",
      'for f in README.md; do git checkout -- "$f"; done',
      'sh -c -- "git reset --hard"',
      'eval "git reset --hard"',
      'env -S "git reset --hard"',
      "# Don't fail\ngit reset --hard",
      "echo $'a\\'b';git reset --hard",
  )

  for cmd in parity_commands:
    hook_res = guard_mod.evaluate_git_command(cmd, has_review=True)
    mcp_safe, mcp_reason = mcp_mod.evaluate_git_safety(cmd)
    assert (hook_res["decision"] == "deny") == (not mcp_safe), (
        f"Deny parity mismatch on '{cmd}': hook={hook_res}, mcp=({mcp_safe}, {mcp_reason})"
    )
    assert (hook_res["decision"] == "force_ask") == (
        mcp_safe and mcp_reason.startswith("Requires human operator approval")
    ), (
        f"Gate parity mismatch on '{cmd}': hook={hook_res}, mcp=({mcp_safe}, {mcp_reason})"
    )

  print("✔ Hook and MCP Git guard parity assertions passed.")


def test_plugin_directory_isolation():
  """Tests that plugin installation directory never leaks state cards to user workspaces."""
  guard_mod = _load_module("pre_tool_guard", PRE_TOOL)

  with tempfile.TemporaryDirectory() as mock_plugin_dir, tempfile.TemporaryDirectory() as ws_dir:
    # 1. Setup mock plugin directory with leftover state cards
    with open(os.path.join(mock_plugin_dir, "plugin.json"), "w", encoding="utf-8") as f:
      json.dump({"name": "makeflow"}, f)
    plugin_state = os.path.join(mock_plugin_dir, ".workflow", "state")
    os.makedirs(plugin_state)
    with open(os.path.join(plugin_state, "10_ticket.md"), "w", encoding="utf-8") as f:
      f.write("# Ticket\n- Ticket ID: GH-999\n")
    with open(os.path.join(plugin_state, "99_next_action.md"), "w", encoding="utf-8") as f:
      f.write("# Target 90 Finished: COMPLETE\n- Status: COMPLETE (make finish satisfied)\n- Active Target: Target 90\n")
    with open(os.path.join(plugin_state, "45_review_approved.md"), "w", encoding="utf-8") as f:
      f.write("# Approved\n")

    # 2. Test session_start: pristine workspace reports ZERO cards and does not mention plugin state cards
    res = run_hook(
        SESSION_START,
        {"workspacePaths": [ws_dir]},
        cwd=mock_plugin_dir,
    )
    if "injectSteps" in res:
      msg = res["injectSteps"][0]["systemMessage"]["systemMessage"]
      assert "state card(s)" not in msg, f"Leaked state cards into session message: {msg}"
      assert "GH-999" not in msg

    # 3. Test pre_tool_guard: commit in pristine workspace is Target 50, NOT Target 90
    commit_res = run_hook(
        PRE_TOOL,
        {
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: test isolation'"},
            },
        },
        cwd=mock_plugin_dir,
    )
    assert commit_res["decision"] == "force_ask"
    assert "Target 50 Commit Gate" in commit_res["reason"], f"Wrong gate: {commit_res['reason']}"
    assert "Target 90" not in commit_res["reason"]

    # 4. Test resolve_state_dir: strictly returns workspace state, never plugin dir
    state_dir = guard_mod.resolve_state_dir({"workspacePaths": [ws_dir]})
    assert state_dir == os.path.join(ws_dir, ".workflow", "state")
    assert state_dir != plugin_state

    # When workspacePaths is empty and cwd is plugin directory, resolve_state_dir returns ""
    old_cwd = os.getcwd()
    try:
      os.chdir(mock_plugin_dir)
      state_dir_no_ws = guard_mod.resolve_state_dir({})
      assert state_dir_no_ws == "", f"Expected empty string, got: {state_dir_no_ws}"
    finally:
      os.chdir(old_cwd)

  print("✔ Plugin directory state isolation assertions passed.")


class TestTarget90ScarsHook(unittest.TestCase):
  """Unit tests for Target 90 .workflow/scars/ and docs(scars): detection (GH-93)."""

  def test_detect_target_90_recognizes_workflow_scars_and_docs_scars(self):
    guard_mod = _load_module("pre_tool_guard", PRE_TOOL)
    with tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# 99_next_action\n- Active Target: Target 90 (finish)\n")

      is_hist_1, is_t90_1 = guard_mod._detect_target_90(
          "git commit -m 'docs(scars): record project lesson' .workflow/scars/local.md",
          state_dir=state_dir,
      )
      self.assertTrue(is_hist_1)
      self.assertTrue(is_t90_1)

      is_hist_2, is_t90_2 = guard_mod._detect_target_90(
          "git commit -m 'chore: add local scar' .workflow/scars/auth.md",
          state_dir=state_dir,
      )
      self.assertTrue(is_hist_2)
      self.assertTrue(is_t90_2)

      is_hist_3, is_t90_3 = guard_mod._detect_target_90(
          "git commit -m 'docs(scars): add project scar'",
          state_dir=state_dir,
      )
      self.assertTrue(is_hist_3)
      self.assertTrue(is_t90_3)

      # Negative check: normal feature commit does not match history/scars message
      is_hist_4, _ = guard_mod._detect_target_90(
          "git commit -m 'feat: add login'",
          state_dir=state_dir,
      )
      self.assertFalse(is_hist_4)

  def test_pre_tool_guard_allows_target_90_scars_commit_gate(self):
    with (
        tempfile.TemporaryDirectory() as ws_dir,
        tempfile.TemporaryDirectory() as hook_cwd,
    ):
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "# Next Action: COMPLETE (make finish satisfied)\n"
            "- Active Target: Target 90 (finish)\n"
        )

      for cmd in (
          "git commit -m 'docs(scars): record local scar' .workflow/scars/local.md",
          "git commit -m 'chore: add project scar' .workflow/scars/db.md",
      ):
        res = run_hook(
            PRE_TOOL,
            {
                "workspacePaths": [ws_dir],
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                },
            },
            cwd=hook_cwd,
        )
        self.assertEqual(res["decision"], "force_ask")
        self.assertIn("Target 90 History Ledger Gate", res["reason"])
        self.assertNotIn("Target 50 Commit Gate", res["reason"])


class TestPreToolGuardSelfModification(unittest.TestCase):
  """Unit tests verifying pre_tool_guard denies self-modification of governance files."""

  def test_denies_shell_redirection_targeting_governance_files(self):
    bad_commands = [
        "echo 'hacked' > scripts/tdd_guard.py",
        "echo 'hacked' >> scripts/pre_tool_guard.py",
        "cat /dev/null > scripts/workflow_gates.py",
        "echo '{}' > hooks.json",
        "cat bad.mk >> .workflow/state/Makefile",
        "echo 'tamper' > ./scripts/tdd_guard.py",
        "echo 'bad' 1> hooks.json",
        "true >| .workflow/state/Makefile",
        "echo 'x' &> scripts/workflow_gates.py",
    ]
    for cmd in bad_commands:
      res = run_hook(
          PRE_TOOL,
          {
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              }
          },
      )
      self.assertEqual(res["decision"], "deny", f"Expected deny for cmd={cmd!r}")
      self.assertIn(
          "Blocked self-modification attempt on makeflow governance file",
          res["reason"],
          f"Expected blocked self-modification reason for cmd={cmd!r}",
      )

  def test_denies_removal_and_truncation_of_governance_files(self):
    bad_commands = [
        "rm scripts/tdd_guard.py",
        "rm -f scripts/pre_tool_guard.py",
        "rm -rf scripts/workflow_gates.py",
        "rm hooks.json",
        "rm -f .workflow/state/Makefile",
        "rm -rf scripts/",
        "unlink hooks.json",
        "truncate -s 0 scripts/tdd_guard.py",
        "truncate -s 0 hooks.json",
        "truncate -s 0 .workflow/state/Makefile",
        "sed -i 's/foo/bar/' scripts/tdd_guard.py",
        "sed -i 's/foo/bar/' scripts/workflow_gates.py",
        "sed -i.bak 's/foo/bar/' hooks.json",
        "sed -i 's/foo/bar/' .workflow/state/Makefile",
    ]
    for cmd in bad_commands:
      res = run_hook(
          PRE_TOOL,
          {
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              }
          },
      )
      self.assertEqual(res["decision"], "deny", f"Expected deny for cmd={cmd!r}")
      self.assertIn(
          "Blocked self-modification attempt on makeflow governance file",
          res["reason"],
          f"Expected blocked self-modification reason for cmd={cmd!r}",
      )

  def test_allows_safe_operations_on_non_governance_files(self):
    safe_commands = [
        "echo 'print(1)' > src/app.py",
        "rm -f /tmp/test_scratch.txt",
        "cat scripts/tdd_guard.py",
        "cat hooks.json",
        "cat .workflow/state/Makefile",
        "python3 scripts/tdd_guard.py --help",
        "grep -n 'def ' scripts/workflow_gates.py",
    ]
    for cmd in safe_commands:
      res = run_hook(
          PRE_TOOL,
          {
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              }
          },
      )
      self.assertEqual(res["decision"], "allow", f"Expected allow for safe cmd={cmd!r}")

  def test_allows_copying_and_moving_into_governance_directories(self):
    allowed_commands = [
        "cp file.txt .workflow/state/",
        "cp helper.py scripts/",
        "mv file.txt .workflow/state/",
        "mv helper.py scripts/",
        "cp 10_ticket.md .workflow/state/",
        "cp 20_plan.md .workflow/state/",
    ]
    for cmd in allowed_commands:
      res = run_hook(
          PRE_TOOL,
          {
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              }
          },
      )
      self.assertEqual(res["decision"], "allow", f"Expected allow for cmd={cmd!r}, got: {res}")

  def test_denies_copying_and_moving_over_protected_governance_files(self):
    denied_commands = [
        "cp evil.py scripts/tdd_guard.py",
        "cp fake_makefile .workflow/state/Makefile",
        "cp fake_receipt.json .workflow/state/30_tdd_receipt.json",
        "mv evil.py scripts/workflow_gates.py",
        "mv evil.py scripts/base_script.py",
        "mv evil.py scripts/emit_reflex.py",
        "mv evil.py hooks.json",
    ]
    for cmd in denied_commands:
      res = run_hook(
          PRE_TOOL,
          {
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              }
          },
      )
      self.assertEqual(res["decision"], "deny", f"Expected deny for cmd={cmd!r}")
      self.assertIn(
          "Blocked self-modification attempt on makeflow governance file",
          res["reason"],
          f"Expected blocked self-modification reason for cmd={cmd!r}",
      )


class TestPreToolGuardSpecificExceptions(unittest.TestCase):
  """Unit tests verifying pre_tool_guard catches only expected exceptions (GH-121)."""

  @classmethod
  def setUpClass(cls):
    cls.guard = _load_module("pre_tool_guard", PRE_TOOL)

  def test_is_plugin_dir_handles_expected_exceptions(self):
    # 1. json.JSONDecodeError: invalid JSON in plugin.json
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        f.write("{invalid json")
      self.assertFalse(self.guard.is_plugin_dir(td))

    # 2. UnicodeDecodeError: invalid UTF-8 bytes in plugin.json
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
      self.assertFalse(self.guard.is_plugin_dir(td))

    # 3. OSError: file read I/O error
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        f.write("{}")
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        self.assertFalse(self.guard.is_plugin_dir(td))

  def test_is_plugin_dir_does_not_suppress_unrelated_exceptions(self):
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        f.write('{"name": "makeflow"}')
      with mock.patch.object(self.guard.json, "load", side_effect=TypeError("Synthetic TypeError")):
        with self.assertRaises(TypeError):
          self.guard.is_plugin_dir(td)
      with mock.patch.object(self.guard.json, "load", side_effect=RuntimeError("Synthetic RuntimeError")):
        with self.assertRaises(RuntimeError):
          self.guard.is_plugin_dir(td)

  def test_load_database_config_handles_expected_exceptions(self):
    # 1. json.JSONDecodeError: invalid JSON in config.json
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "w", encoding="utf-8") as f:
        f.write("{invalid json")
      res = self.guard.load_database_config({"workspacePaths": [td]})
      self.assertEqual(res["prodDatabases"], [])
      self.assertEqual(res["testDatabases"], [])

    # 2. UnicodeDecodeError: invalid UTF-8 bytes in config.json
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
      res = self.guard.load_database_config({"workspacePaths": [td]})
      self.assertEqual(res["prodDatabases"], [])
      self.assertEqual(res["testDatabases"], [])

    # 3. OSError: file read I/O error
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "w", encoding="utf-8") as f:
        f.write("{}")
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        res = self.guard.load_database_config({"workspacePaths": [td]})
        self.assertEqual(res["prodDatabases"], [])
        self.assertEqual(res["testDatabases"], [])

  def test_load_database_config_does_not_suppress_unrelated_exceptions(self):
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "w", encoding="utf-8") as f:
        f.write('{"database": {}}')
      with mock.patch.object(self.guard.json, "load", side_effect=TypeError("Synthetic TypeError")):
        with self.assertRaises(TypeError):
          self.guard.load_database_config({"workspacePaths": [td]})
      with mock.patch.object(self.guard.json, "load", side_effect=RuntimeError("Synthetic RuntimeError")):
        with self.assertRaises(RuntimeError):
          self.guard.load_database_config({"workspacePaths": [td]})

  def test_detect_target_90_handles_expected_exceptions(self):
    # 1. UnicodeDecodeError: invalid UTF-8 bytes in 99_next_action.md
    with tempfile.TemporaryDirectory() as td:
      card = os.path.join(td, "99_next_action.md")
      with open(card, "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
      is_hist, is_t90 = self.guard._detect_target_90("git commit -m 'feat: test'", state_dir=td)
      self.assertFalse(is_t90)

    # 2. OSError: file read I/O error
    with tempfile.TemporaryDirectory() as td:
      card = os.path.join(td, "99_next_action.md")
      with open(card, "w", encoding="utf-8") as f:
        f.write("# 99_next_action\n- Active Target: Target 90\n")
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        is_hist, is_t90 = self.guard._detect_target_90("git commit -m 'feat: test'", state_dir=td)
        self.assertFalse(is_t90)

  def test_detect_target_90_does_not_suppress_unrelated_exceptions(self):
    with tempfile.TemporaryDirectory() as td:
      card = os.path.join(td, "99_next_action.md")
      with open(card, "w", encoding="utf-8") as f:
        f.write("# 99_next_action\n- Active Target: Target 90\n")
      with mock.patch.object(self.guard.re, "search", side_effect=TypeError("Synthetic TypeError")):
        with self.assertRaises(TypeError):
          self.guard._detect_target_90("git commit -m 'feat: test'", state_dir=td)
      with mock.patch.object(self.guard.re, "search", side_effect=RuntimeError("Synthetic RuntimeError")):
        with self.assertRaises(RuntimeError):
          self.guard._detect_target_90("git commit -m 'feat: test'", state_dir=td)


class TestSessionStartSpecificExceptions(unittest.TestCase):
  """Unit tests verifying session_start catches only expected exceptions (GH-130)."""

  @classmethod
  def setUpClass(cls):
    cls.session_start = _load_module("session_start", SESSION_START)

  def test_load_default_config_handles_expected_exceptions(self):
    # 1. json.JSONDecodeError
    with mock.patch("os.path.isfile", return_value=True):
      with mock.patch("builtins.open", mock.mock_open(read_data="{invalid json")):
        res = self.session_start.load_default_config()
        self.assertEqual(res["models"]["tdd-worker"], "inherit")

    # 2. UnicodeDecodeError
    with mock.patch("os.path.isfile", return_value=True):
      with mock.patch(
          "builtins.open",
          side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
      ):
        res = self.session_start.load_default_config()
        self.assertEqual(res["models"]["tdd-worker"], "inherit")

    # 3. OSError
    with mock.patch("os.path.isfile", return_value=True):
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        res = self.session_start.load_default_config()
        self.assertEqual(res["models"]["tdd-worker"], "inherit")

  def test_load_default_config_does_not_suppress_unrelated_exceptions(self):
    with mock.patch("os.path.isfile", return_value=True):
      with mock.patch("builtins.open", mock.mock_open(read_data="{}")):
        with mock.patch.object(
            self.session_start.json,
            "load",
            side_effect=TypeError("Synthetic TypeError"),
        ):
          with self.assertRaises(TypeError):
            self.session_start.load_default_config()
        with mock.patch.object(
            self.session_start.json,
            "load",
            side_effect=RuntimeError("Synthetic RuntimeError"),
        ):
          with self.assertRaises(RuntimeError):
            self.session_start.load_default_config()

  def test_is_plugin_dir_handles_expected_exceptions(self):
    # 1. json.JSONDecodeError: invalid JSON in plugin.json
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        f.write("{invalid json")
      self.assertFalse(self.session_start.is_plugin_dir(td))

    # 2. UnicodeDecodeError: invalid UTF-8 bytes in plugin.json
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
      self.assertFalse(self.session_start.is_plugin_dir(td))

    # 3. OSError: file read I/O error
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        f.write("{}")
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        self.assertFalse(self.session_start.is_plugin_dir(td))

  def test_is_plugin_dir_does_not_suppress_unrelated_exceptions(self):
    with tempfile.TemporaryDirectory() as td:
      manifest = os.path.join(td, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        f.write('{"name": "makeflow"}')
      with mock.patch.object(
          self.session_start.json,
          "load",
          side_effect=TypeError("Synthetic TypeError"),
      ):
        with self.assertRaises(TypeError):
          self.session_start.is_plugin_dir(td)
      with mock.patch.object(
          self.session_start.json,
          "load",
          side_effect=RuntimeError("Synthetic RuntimeError"),
      ):
        with self.assertRaises(RuntimeError):
          self.session_start.is_plugin_dir(td)

  def test_get_workflow_config_handles_expected_exceptions(self):
    # 1. json.JSONDecodeError: invalid JSON in config.json
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "w", encoding="utf-8") as f:
        f.write("{invalid json")
      cfg_dict, src = self.session_start.get_workflow_config([td])
      self.assertEqual(cfg_dict["models"]["tdd-worker"], "inherit")

    # 2. UnicodeDecodeError: invalid UTF-8 bytes in config.json
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
      cfg_dict, src = self.session_start.get_workflow_config([td])
      self.assertEqual(cfg_dict["models"]["tdd-worker"], "inherit")

    # 3. OSError: file read I/O error
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "w", encoding="utf-8") as f:
        f.write("{}")
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        cfg_dict, src = self.session_start.get_workflow_config([td])
        self.assertEqual(cfg_dict["models"]["tdd-worker"], "inherit")

  def test_get_workflow_config_does_not_suppress_unrelated_exceptions(self):
    with tempfile.TemporaryDirectory() as td:
      wf_dir = os.path.join(td, ".workflow")
      os.makedirs(wf_dir)
      cfg = os.path.join(wf_dir, "config.json")
      with open(cfg, "w", encoding="utf-8") as f:
        f.write('{"models": {}}')
      with mock.patch.object(
          self.session_start.json,
          "load",
          side_effect=TypeError("Synthetic TypeError"),
      ):
        with self.assertRaises(TypeError):
          self.session_start.get_workflow_config([td])
      with mock.patch.object(
          self.session_start.json,
          "load",
          side_effect=RuntimeError("Synthetic RuntimeError"),
      ):
        with self.assertRaises(RuntimeError):
          self.session_start.get_workflow_config([td])

  def test_is_ticket_active_handles_expected_exceptions(self):
    # 1. UnicodeDecodeError: invalid UTF-8 in 99_next_action.md
    with tempfile.TemporaryDirectory() as td:
      card = os.path.join(td, "99_next_action.md")
      with open(card, "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
      self.assertTrue(self.session_start.is_ticket_active(td))

    # 2. OSError: file read I/O error
    with tempfile.TemporaryDirectory() as td:
      card = os.path.join(td, "99_next_action.md")
      with open(card, "w", encoding="utf-8") as f:
        f.write("# Target 90 Finished: COMPLETE\n")
      with mock.patch("builtins.open", side_effect=OSError("Disk failure")):
        self.assertTrue(self.session_start.is_ticket_active(td))

  def test_is_ticket_active_does_not_suppress_unrelated_exceptions(self):
    with tempfile.TemporaryDirectory() as td:
      card = os.path.join(td, "99_next_action.md")
      with open(card, "w", encoding="utf-8") as f:
        f.write("# Target 90 Finished: COMPLETE\n")
      mock_file = mock.MagicMock()
      mock_file.__enter__.return_value.read.side_effect = TypeError("Synthetic TypeError")
      with mock.patch("builtins.open", return_value=mock_file):
        with self.assertRaises(TypeError):
          self.session_start.is_ticket_active(td)
      mock_file.__enter__.return_value.read.side_effect = RuntimeError("Synthetic RuntimeError")
      with mock.patch("builtins.open", return_value=mock_file):
        with self.assertRaises(RuntimeError):
          self.session_start.is_ticket_active(td)

  def test_dynamic_terminal_title_handles_expected_exceptions(self):
    # 1. ImportError in set_dynamic_terminal_title
    with mock.patch(
        "terminal_title.set_terminal_title",
        side_effect=ImportError("No module named terminal_title"),
    ):
      getattr(self.session_start, "set_dynamic_terminal_title", lambda p: None)("/fake/ws")
    # 2. OSError in set_dynamic_terminal_title
    with mock.patch(
        "terminal_title.set_terminal_title",
        side_effect=OSError("TTY write error"),
    ):
      getattr(self.session_start, "set_dynamic_terminal_title", lambda p: None)("/fake/ws")

  def test_dynamic_terminal_title_does_not_suppress_unrelated_exceptions(self):
    set_dyn = getattr(self.session_start, "set_dynamic_terminal_title", None)
    if set_dyn is not None:
      with mock.patch(
          "terminal_title.set_terminal_title",
          side_effect=TypeError("Synthetic TypeError"),
      ):
        with self.assertRaises(TypeError):
          set_dyn("/fake/ws")
      with mock.patch(
          "terminal_title.set_terminal_title",
          side_effect=RuntimeError("Synthetic RuntimeError"),
      ):
        with self.assertRaises(RuntimeError):
          set_dyn("/fake/ws")

  def test_main_dynamic_terminal_title_integration(self):
    with tempfile.TemporaryDirectory() as td:
      state_dir = os.path.join(td, ".workflow", "state")
      os.makedirs(state_dir)
      with open(os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8") as f:
        f.write("# Ticket\n")
      for exc in (ImportError("Synthetic ImportError"), OSError("Synthetic OSError")):
        with mock.patch("terminal_title.set_terminal_title", side_effect=exc):
          with mock.patch("sys.stdin", io.StringIO(json.dumps({"workspacePaths": [td]}))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
              self.session_start.main()
              res = json.loads(mock_out.getvalue())
              self.assertIn("injectSteps", res)


if __name__ == "__main__":
  test_pre_tool_guard()
  test_session_start()
  test_hook_mcp_parity()
  test_plugin_directory_isolation()
  suite = unittest.TestSuite()
  suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestTarget90ScarsHook))
  suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestPreToolGuardSelfModification))
  suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestPreToolGuardSpecificExceptions))
  suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestSessionStartSpecificExceptions))
  runner = unittest.TextTestRunner(verbosity=2, failfast=True)
  if not runner.run(suite).wasSuccessful():
    sys.exit(1)
