#!/usr/bin/env python3
"""Hermetic unit and boundary tests for the Black-Box Harness Boundary guard (GH-222, SCAR-PROC-124)."""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.harness_boundary import (
    HARNESS_INTERNAL_RELPATHS,
    POST_PLAN_TARGETS,
    PRE_IMPLEMENTATION_TARGETS,
    RUNTIME_LOCK_STATE_RELPATHS,
    evaluate_harness_read_boundary,
    find_makeflow_plugin_root,
    is_plugin_cache_path,
    plan_mentions_relpath,
)


@contextlib.contextmanager
def _isolated_env():
  """Isolates HOME, cwd, and MAKEFILE_WORKFLOW_* variables for hermetic tests.

  Yields:
    Tuple of ``(hook_cwd, fake_home)``.
  """
  with (
      tempfile.TemporaryDirectory() as hook_cwd,
      tempfile.TemporaryDirectory() as fake_home,
  ):
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("MAKEFILE_WORKFLOW_")
    }
    clean_env["HOME"] = fake_home
    with (
        mock.patch.dict(os.environ, clean_env, clear=True),
        mock.patch("os.getcwd", return_value=hook_cwd),
    ):
      yield hook_cwd, fake_home


def _write_file(path, content):
  """Creates parent directories and writes UTF-8 text to path."""
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as f:
    f.write(content)


def _init_makeflow_checkout(root_dir, target="T30", plan_text=None):
  """Builds a minimal hermetic Makeflow plugin repository fixture in root_dir.

  Args:
    root_dir: Temporary directory path acting as the Makeflow repository root.
    target: Optional canonical target ID to write into active_target.json.
    plan_text: Optional content for .workflow/state/20_plan.md.

  Returns:
    Path to the created ``.workflow/state`` directory.
  """
  _write_file(
      os.path.join(root_dir, "plugin.json"),
      json.dumps({"name": "makeflow", "version": "1.0.0"}) + "\n",
  )
  _write_file(os.path.join(root_dir, "hooks.json"), "{}\n")
  _write_file(os.path.join(root_dir, "default_config.json"), "{}\n")
  _write_file(os.path.join(root_dir, "mcp_config.json"), "{}\n")
  _write_file(os.path.join(root_dir, "README.md"), "# Makeflow\n")
  _write_file(os.path.join(root_dir, "CHANGELOG.md"), "# Changelog\n")
  _write_file(os.path.join(root_dir, "Makefile"), "test:\n\t@true\n")
  _write_file(os.path.join(root_dir, "CLAUDE.md"), "# Claude\n")
  _write_file(os.path.join(root_dir, "rules", "AGENTS.md"), "# Agents\n")
  _write_file(
      os.path.join(root_dir, "skills", "makeflow", "SKILL.md"), "# Skill\n"
  )
  _write_file(
      os.path.join(
          root_dir, "skills", "makeflow", "references", "scars", "process.md"
      ),
      "# Process\n",
  )
  _write_file(os.path.join(root_dir, "agents", "tdd-worker.md"), "# TDD\n")
  _write_file(
      os.path.join(root_dir, "tests", "test_harness_boundary.py"), "# Tests\n"
  )
  for rel in HARNESS_INTERNAL_RELPATHS:
    fpath = os.path.join(root_dir, rel)
    if not os.path.exists(fpath):
      _write_file(fpath, f"# {rel}\n")

  state_dir = os.path.join(root_dir, ".workflow", "state")
  os.makedirs(state_dir, exist_ok=True)
  _write_file(
      os.path.join(state_dir, "10_ticket.json"),
      json.dumps({"ticket_id": "GH-222", "title": "Black-box boundary"}) + "\n",
  )
  _write_file(
      os.path.join(state_dir, "99_next_action.md"),
      f"# 99_next_action\n\n- Active Target: Target {target[1:]}\n"
      if target
      else "# 99_next_action\n",
  )
  _write_file(os.path.join(state_dir, "40_critic_review.md"), "# Review\n")
  _write_file(os.path.join(state_dir, "Makefile"), "all:\n\t@true\n")
  _write_file(os.path.join(state_dir, "gate_entry.json"), "{}\n")
  if target is not None:
    _write_file(
        os.path.join(state_dir, "active_target.json"),
        json.dumps({"target": target, "previousTarget": "T00"}) + "\n",
    )
  if plan_text is not None:
    _write_file(os.path.join(state_dir, "20_plan.md"), plan_text)
  return state_dir


def _run_git(repo_dir, *args):
  """Runs a Git command inside an isolated temp repository."""
  env = {
      **os.environ,
      "GIT_AUTHOR_NAME": "Test Author",
      "GIT_AUTHOR_EMAIL": "test@example.com",
      "GIT_COMMITTER_NAME": "Test Author",
      "GIT_COMMITTER_EMAIL": "test@example.com",
  }
  proc = subprocess.run(
      ["git", *args],
      cwd=repo_dir,
      capture_output=True,
      text=True,
      check=False,
      env=env,
      timeout=10,
  )
  if proc.returncode != 0:
    raise AssertionError(
        f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr}"
    )
  return proc.stdout


def _read_payload(ws_dir, target_path, arg_key="Path", conversation_id="sup-1"):
  """Builds a PreToolUse read_file payload dictionary."""
  payload = {
      "workspacePaths": [ws_dir],
      "toolCall": {
          "name": "read_file",
          "args": {arg_key: target_path},
      },
  }
  if conversation_id is not None:
    payload["conversationId"] = conversation_id
  return payload


class TestBlackBoxHarnessBoundary(unittest.TestCase):
  """Comprehensive boundary tests for lib/harness_boundary.py (GH-222)."""

  def test_all_14_targets_phase_window_and_completed_session(self):
    """Allows T00-T20 and inactive/completed sessions while guarding T30-T90."""
    expected_pre = frozenset({"T00", "T10", "T15", "T20"})
    expected_post = frozenset({
        "T30",
        "T40",
        "T42",
        "T45",
        "T50",
        "T60",
        "T70",
        "T75",
        "T80",
        "T90",
    })
    self.assertEqual(PRE_IMPLEMENTATION_TARGETS, expected_pre)
    self.assertEqual(POST_PLAN_TARGETS, expected_post)

    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(ws_dir, target="T00")
      gov_file = os.path.join(ws_dir, "lib", "turn_boundary.py")
      lock_file = os.path.join(state_dir, "gate_entry.json")

      for tid in sorted(expected_pre):
        with self.subTest(phase="pre", target=tid):
          _write_file(
              os.path.join(state_dir, "active_target.json"),
              json.dumps({"target": tid, "previousTarget": "T00"}) + "\n",
          )
          for target_file in (gov_file, lock_file):
            ok, reason = evaluate_harness_read_boundary(
                _read_payload(ws_dir, target_file), state_dir, [ws_dir]
            )
            self.assertTrue(
                ok, f"Expected allow during {tid} for {target_file}, got {reason}"
            )
            self.assertEqual(reason, "")

      for tid in sorted(expected_post):
        with self.subTest(phase="post", target=tid):
          _write_file(
              os.path.join(state_dir, "active_target.json"),
              json.dumps({"target": tid, "previousTarget": "T20"}) + "\n",
          )
          for target_file in (gov_file, lock_file):
            ok, reason = evaluate_harness_read_boundary(
                _read_payload(ws_dir, target_file), state_dir, [ws_dir]
            )
            self.assertFalse(
                ok, f"Expected deny during {tid} for {target_file}"
            )
            self.assertIn("[SCAR-ID: PROC-124]", reason)
            self.assertIn(tid, reason)

      # Completed 99_next_action.md unlocks even if a stale active_target.json remains
      _write_file(
          os.path.join(state_dir, "active_target.json"),
          json.dumps({"target": "T90", "previousTarget": "T80"}) + "\n",
      )
      _write_file(
          os.path.join(state_dir, "99_next_action.md"),
          "# Target 90 Finished: COMPLETE\n\n- Status: COMPLETE (make finish satisfied)\n",
      )
      for target_file in (gov_file, lock_file):
        ok, reason = evaluate_harness_read_boundary(
            _read_payload(ws_dir, target_file), state_dir, [ws_dir]
        )
        self.assertTrue(
            ok, f"Expected allow on completed session for {target_file}, got {reason}"
        )

    # Inactive workspace without .workflow/state directory is allowed
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      _write_file(
          os.path.join(ws_dir, "plugin.json"),
          json.dumps({"name": "makeflow", "version": "1.0.0"}) + "\n",
      )
      gov_file = os.path.join(ws_dir, "lib", "turn_boundary.py")
      _write_file(gov_file, "# turn boundary\n")
      ok, reason = evaluate_harness_read_boundary(
          _read_payload(ws_dir, gov_file),
          os.path.join(ws_dir, ".workflow", "state"),
          [ws_dir],
      )
      self.assertTrue(ok, f"Expected allow without state_dir, got {reason}")

  def test_out_of_scope_governance_files_denied_with_vector_4_reflex(self):
    """Denies out-of-scope Depth-0 reads across guarded Makeflow files with SCAR-PROC-124 reflex."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(
          ws_dir, target="T30", plan_text="# Plan\n\nEdit `README.md` only.\n"
      )
      guarded_samples = (
          "lib/turn_boundary.py",
          "scripts/pre_tool_guard.py",
          "lib/pre_tool_guard.py",
          "lib/workflow_gates.py",
          "lib/commit_msg.py",
          "lib/tdd_guard.py",
          "default_config.json",
          "hooks.json",
          "lib/harness_boundary.py",
          "mcp/server.py",
          "mcp_config.json",
          "lib/session_start.py",
          "scripts/session_start.py",
          "lib/workspace_mutex.py",
          "scripts/workspace_mutex.py",
          "lib/review_promotion.py",
          "lib/ticket_state.py",
      )
      for rel in guarded_samples:
        with self.subTest(rel=rel):
          abs_path = os.path.join(ws_dir, rel)
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, abs_path), state_dir, [ws_dir]
          )
          self.assertFalse(ok, f"Expected deny for out-of-scope {rel}")
          for token in (
              "[SCAR-ID: PROC-124]",
              "OUT_OF_SCOPE_HARNESS_INSPECTION",
              "TRIGGER (THIS):",
              "REJECTED (TRAP):",
              "REFLEX (ACTION):",
              "SOCRATIC PROBE:",
              "make -f .workflow/state/Makefile",
              rel,
              "T30",
          ):
            self.assertIn(token, reason, f"Missing {token!r} in reflex for {rel}")

      # Verify Path, file_path, and path arg keys all work cleanly
      for arg_key in ("Path", "file_path", "path"):
        with self.subTest(arg_key=arg_key):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(
                  ws_dir,
                  os.path.join(ws_dir, "lib", "turn_boundary.py"),
                  arg_key=arg_key,
              ),
              state_dir,
              [ws_dir],
          )
          self.assertFalse(ok, f"Expected deny for arg_key={arg_key}")
          self.assertIn("OUT_OF_SCOPE_HARNESS_INSPECTION", reason)

  def test_internal_runtime_lock_state_denied_unconditionally_even_if_in_plan(self):
    """Denies gate_entry.json, active_target.json, active_subagent.json, and state Makefile even if listed in 20_plan.md."""
    expected_locks = frozenset({
        ".workflow/state/gate_entry.json",
        ".workflow/state/active_target.json",
        ".workflow/state/active_subagent.json",
        ".workflow/state/Makefile",
    })
    self.assertEqual(RUNTIME_LOCK_STATE_RELPATHS, expected_locks)

    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      plan_prose = (
          "# Plan\n"
          "- `.workflow/state/gate_entry.json`\n"
          "- `.workflow/state/active_target.json`\n"
          "- `.workflow/state/active_subagent.json`\n"
          "- `.workflow/state/Makefile`\n"
      )
      state_dir = _init_makeflow_checkout(ws_dir, target="T45", plan_text=plan_prose)
      _write_file(
          os.path.join(state_dir, "active_subagent.json"),
          json.dumps({"supervisorConversationId": "sup-1", "typeName": "tdd-worker"})
          + "\n",
      )

      for rel in sorted(expected_locks):
        with self.subTest(rel=rel):
          abs_lock = os.path.join(ws_dir, rel)
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, abs_lock, conversation_id="sup-1"),
              state_dir,
              [ws_dir],
          )
          self.assertFalse(
              ok, f"Expected unconditional deny for runtime lock {rel}"
          )
          self.assertIn("[SCAR-ID: PROC-124]", reason)
          self.assertIn("OUT_OF_SCOPE_HARNESS_INSPECTION", reason)
          self.assertIn("make -f .workflow/state/Makefile", reason)

  def test_always_readable_docs_cards_tests_and_agents_allowed(self):
    """Allows workflow docs, Memento cards, 10_ticket.json, README, CHANGELOG, tests, and agents during T30-T90."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(
          ws_dir, target="T30", plan_text="# Plan\n"
      )
      always_allowed = (
          "skills/makeflow/SKILL.md",
          "skills/makeflow/references/scars/process.md",
          "rules/AGENTS.md",
          ".workflow/state/20_plan.md",
          ".workflow/state/99_next_action.md",
          ".workflow/state/40_critic_review.md",
          ".workflow/state/10_ticket.json",
          "README.md",
          "CHANGELOG.md",
          "Makefile",
          "CLAUDE.md",
          "tests/test_harness_boundary.py",
          "agents/tdd-worker.md",
      )
      for rel in always_allowed:
        with self.subTest(rel=rel):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertTrue(ok, f"Expected always-readable allow for {rel}, got {reason}")

  def test_plan_allowlist_matching_and_negative_substring_traps(self):
    """Allows files explicitly named in 20_plan.md while rejecting unlisted siblings and substring traps."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      ws_real = os.path.realpath(ws_dir)
      plan_text = (
          "# Plan\n"
          "- Edit `lib/config.py` for settings.\n"
          "- Update ./lib/shell_safety.py safely.\n"
          f"- Inspect [workflow_gates](file://{ws_real}/lib/workflow_gates.py#L1-L20).\n"
          "| lib/reflex.py | Edit | Library |\n"
          "- Unrelated path traps: other/lib/commit_msg.py, lib/tdd_guard.py.bak,\n"
          "  mylib/git_index.py, lib/git_safety.py2,\n"
          f"  file://{ws_real}/other/lib/base_script.py.\n"
      )
      state_dir = _init_makeflow_checkout(ws_dir, target="T30", plan_text=plan_text)

      allowed_relpaths = (
          "lib/config.py",
          "lib/shell_safety.py",
          "lib/workflow_gates.py",
          "lib/reflex.py",
      )
      for rel in allowed_relpaths:
        with self.subTest(kind="allowed", rel=rel):
          self.assertTrue(plan_mentions_relpath(plan_text, rel, plugin_root=ws_dir))
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertTrue(ok, f"Expected allow for planned {rel}, got {reason}")

      denied_relpaths = (
          "lib/turn_boundary.py",
          "lib/commit_msg.py",
          "lib/tdd_guard.py",
          "lib/git_index.py",
          "lib/git_safety.py",
          "lib/base_script.py",
      )
      for rel in denied_relpaths:
        with self.subTest(kind="denied_trap_or_unlisted", rel=rel):
          self.assertFalse(plan_mentions_relpath(plan_text, rel, plugin_root=ws_dir))
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertFalse(
              ok, f"Expected deny for unlisted/substring-trap {rel}, got allow"
          )
          self.assertIn("[SCAR-ID: PROC-124]", reason)

  def test_git_diff_staged_untracked_and_branch_committed_allowlist(self):
    """Allows unstaged, staged, untracked, and main...HEAD committed files even when absent from 20_plan.md."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(
          ws_dir, target="T30", plan_text="# Plan\n\nNo code paths listed.\n"
      )
      _run_git(ws_dir, "init", "-b", "main")
      _run_git(ws_dir, "add", "-A")
      # Keep .workflow untracked like real repos do via .git/info/exclude
      _write_file(
          os.path.join(ws_dir, ".git", "info", "exclude"),
          ".workflow/\n",
      )
      _run_git(ws_dir, "rm", "-r", "--cached", "--ignore-unmatch", ".workflow")
      # Remove lib/harness_boundary.py before initial commit so we can test it as untracked
      untracked_rel = "lib/harness_boundary.py"
      os.unlink(os.path.join(ws_dir, untracked_rel))
      _run_git(ws_dir, "rm", "--cached", "--ignore-unmatch", untracked_rel)
      _run_git(ws_dir, "commit", "-m", "chore: initial fixture")

      # Branch off main and commit lib/commit_msg.py
      _run_git(ws_dir, "checkout", "-b", "feat/test-boundary")
      _write_file(
          os.path.join(ws_dir, "lib", "commit_msg.py"),
          "# committed change on branch\n",
      )
      _run_git(ws_dir, "add", "lib/commit_msg.py")
      _run_git(ws_dir, "commit", "-m", "feat: branch commit")

      # Staged change: lib/reflex.py
      _write_file(os.path.join(ws_dir, "lib", "reflex.py"), "# staged change\n")
      _run_git(ws_dir, "add", "lib/reflex.py")

      # Unstaged change: lib/config.py
      _write_file(os.path.join(ws_dir, "lib", "config.py"), "# unstaged change\n")

      # Untracked new file: lib/harness_boundary.py
      _write_file(os.path.join(ws_dir, untracked_rel), "# untracked new file\n")

      for rel in (
          "lib/commit_msg.py",
          "lib/reflex.py",
          "lib/config.py",
          untracked_rel,
      ):
        with self.subTest(git_in_scope=rel):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertTrue(
              ok, f"Expected Git in-scope allow for {rel}, got {reason}"
          )

      # Untouched file absent from plan and Git diff stays denied
      ok, reason = evaluate_harness_read_boundary(
          _read_payload(ws_dir, os.path.join(ws_dir, "lib", "turn_boundary.py")),
          state_dir,
          [ws_dir],
      )
      self.assertFalse(ok, "Expected untouched lib/turn_boundary.py to stay denied")
      self.assertIn("[SCAR-ID: PROC-124]", reason)

  def test_child_subagent_isolation_and_empty_supervisor_cid_guard(self):
    """Allows verified child subagents while denying Depth-0 supervisor and empty supervisorConversationId."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(ws_dir, target="T30", plan_text="# Plan\n")
      gov_file = os.path.join(ws_dir, "lib", "turn_boundary.py")
      lock_file = os.path.join(state_dir, "gate_entry.json")

      _write_file(
          os.path.join(state_dir, "active_subagent.json"),
          json.dumps({
              "supervisorConversationId": "sup-1",
              "typeName": "tdd-worker",
          })
          + "\n",
      )

      # Distinct child subagent conversationId is unblocked on both governance source and lock state
      for target_file in (gov_file, lock_file):
        ok, reason = evaluate_harness_read_boundary(
            _read_payload(ws_dir, target_file, conversation_id="worker-2"),
            state_dir,
            [ws_dir],
        )
        self.assertTrue(
            ok, f"Expected child subagent allow for {target_file}, got {reason}"
        )

      # Same supervisor conversationId stays denied
      for target_file in (gov_file, lock_file):
        ok, reason = evaluate_harness_read_boundary(
            _read_payload(ws_dir, target_file, conversation_id="sup-1"),
            state_dir,
            [ws_dir],
        )
        self.assertFalse(ok, f"Expected supervisor deny for {target_file}")
        self.assertIn("[SCAR-ID: PROC-124]", reason)

      # Missing conversationId stays denied
      ok, _ = evaluate_harness_read_boundary(
          _read_payload(ws_dir, gov_file, conversation_id=None),
          state_dir,
          [ws_dir],
      )
      self.assertFalse(ok, "Expected deny when caller conversationId is missing")

      # Empty supervisorConversationId must NOT classify caller as child subagent
      _write_file(
          os.path.join(state_dir, "active_subagent.json"),
          json.dumps({
              "supervisorConversationId": "",
              "typeName": "tdd-worker",
          })
          + "\n",
      )
      ok, reason = evaluate_harness_read_boundary(
          _read_payload(ws_dir, gov_file, conversation_id="worker-2"),
          state_dir,
          [ws_dir],
      )
      self.assertFalse(
          ok,
          "Empty supervisorConversationId must not falsely classify caller as child",
      )
      self.assertIn("[SCAR-ID: PROC-124]", reason)

  def test_external_consumer_repo_isolation_and_plugin_cache_denial(self):
    """Allows consumer repos' own lib/scripts files during T30 while denying installed plugin cache internals."""
    with (
        _isolated_env() as (_hook_cwd, fake_home),
        tempfile.TemporaryDirectory() as consumer_ws,
    ):
      state_dir = os.path.join(consumer_ws, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "active_target.json"),
          json.dumps({"target": "T30", "previousTarget": "T20"}) + "\n",
      )
      _write_file(
          os.path.join(state_dir, "20_plan.md"),
          "# Plan\n- `lib/turn_boundary.py`\n",
      )
      _write_file(
          os.path.join(state_dir, "99_next_action.md"),
          "# 99_next_action\n- Active Target: Target 30\n",
      )

      consumer_files = (
          os.path.join(consumer_ws, "lib", "config.py"),
          os.path.join(consumer_ws, "scripts", "workflow_gates.py"),
          os.path.join(consumer_ws, "lib", "turn_boundary.py"),
      )
      for fpath in consumer_files:
        _write_file(fpath, "# consumer app code\n")
        self.assertIsNone(find_makeflow_plugin_root(fpath))
        ok, reason = evaluate_harness_read_boundary(
            _read_payload(consumer_ws, fpath), state_dir, [consumer_ws]
        )
        self.assertTrue(
            ok, f"Expected consumer repo allow for {fpath}, got {reason}"
        )

      # Installed plugin cache governance files are denied even from a consumer repo
      # and even when lib/turn_boundary.py is mentioned in 20_plan.md
      cache_denied = (
          os.path.join(
              fake_home,
              ".gemini",
              "config",
              "plugins",
              "makeflow",
              "lib",
              "turn_boundary.py",
          ),
          os.path.join(
              fake_home,
              ".gemini",
              "plugins",
              "makeflow",
              "scripts",
              "pre_tool_guard.py",
          ),
          os.path.join(
              fake_home,
              ".claude",
              "plugins",
              "makeflow",
              "hooks.json",
          ),
      )
      for cache_file in cache_denied:
        with self.subTest(cache_file=cache_file):
          _write_file(cache_file, "# installed plugin file\n")
          self.assertTrue(is_plugin_cache_path(cache_file))
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(consumer_ws, cache_file), state_dir, [consumer_ws]
          )
          self.assertFalse(
              ok, f"Expected plugin cache deny for {cache_file}"
          )
          self.assertIn("[SCAR-ID: PROC-124]", reason)
          self.assertIn("OUT_OF_SCOPE_HARNESS_INSPECTION", reason)

      # Always-readable docs inside plugin cache remain allowed
      cache_skill = os.path.join(
          fake_home,
          ".gemini",
          "config",
          "plugins",
          "makeflow",
          "skills",
          "makeflow",
          "SKILL.md",
      )
      _write_file(cache_skill, "# Skill\n")
      ok, reason = evaluate_harness_read_boundary(
          _read_payload(consumer_ws, cache_skill), state_dir, [consumer_ws]
      )
      self.assertTrue(
          ok, f"Expected plugin cache SKILL.md allow, got {reason}"
      )

  def test_path_normalization_relative_dot_segments_and_symlinks(self):
    """Normalizes relative paths, dot segments, and symlinks via realpath before evaluating boundary."""
    with (
        _isolated_env(),
        tempfile.TemporaryDirectory() as ws_dir,
        tempfile.TemporaryDirectory() as external_dir,
    ):
      state_dir = _init_makeflow_checkout(ws_dir, target="T30", plan_text="# Plan\n")
      gov_file = os.path.join(ws_dir, "lib", "turn_boundary.py")
      lock_file = os.path.join(state_dir, "gate_entry.json")
      readme_file = os.path.join(ws_dir, "README.md")

      gov_link = os.path.join(external_dir, "innocent_notes.txt")
      lock_link = os.path.join(ws_dir, "docs_link.md")
      readme_link = os.path.join(external_dir, "readme_alias.md")
      os.symlink(gov_file, gov_link)
      os.symlink(lock_file, lock_link)
      os.symlink(readme_file, readme_link)

      denied_paths = (
          "lib/turn_boundary.py",
          "./lib/./turn_boundary.py",
          "lib/../lib/turn_boundary.py",
          ".workflow/state/../state/gate_entry.json",
          gov_link,
          lock_link,
      )
      for raw_target in denied_paths:
        with self.subTest(raw_target=raw_target):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, raw_target), state_dir, [ws_dir]
          )
          self.assertFalse(
              ok, f"Expected normalized/symlink deny for {raw_target!r}"
          )
          self.assertIn("[SCAR-ID: PROC-124]", reason)

      ok, reason = evaluate_harness_read_boundary(
          _read_payload(ws_dir, readme_link), state_dir, [ws_dir]
      )
      self.assertTrue(
          ok, f"Expected symlink to README.md allow, got {reason}"
      )

  def test_plan_mentions_relpath_preserves_dot_prefixed_paths(self):
    """Preserves leading dots in dot-prefixed paths while rejecting bare non-dot names (NBO-1)."""
    dot_plan = (
        "# Plan\n"
        "- Edit `.workflow/config.json` for test databases.\n"
        "- Update `./.github/workflows/ci.yml` for lint checks.\n"
    )
    bare_plan = (
        "# Plan\n"
        "- Edit `workflow/config.json` for app settings.\n"
        "- Update `github/workflows/ci.yml` for docs.\n"
    )

    for rel in (
        ".workflow/config.json",
        "./.workflow/config.json",
        ".github/workflows/ci.yml",
        "./.github/workflows/ci.yml",
    ):
      with self.subTest(plan="dot_plan", rel=rel):
        self.assertTrue(
            plan_mentions_relpath(dot_plan, rel),
            f"Expected dot_plan to match {rel!r}",
        )
      with self.subTest(plan="bare_plan", rel=rel):
        self.assertFalse(
            plan_mentions_relpath(bare_plan, rel),
            f"Expected bare_plan NOT to match dot-prefixed {rel!r}",
        )

    for bare_rel in ("workflow/config.json", "github/workflows/ci.yml"):
      with self.subTest(plan="dot_plan_rejects_bare", rel=bare_rel):
        self.assertFalse(
            plan_mentions_relpath(dot_plan, bare_rel),
            f"Expected dot_plan NOT to match bare {bare_rel!r}",
        )

  def test_third_party_plugin_cache_allowed_while_makeflow_plugin_denied(self):
    """Allows third-party plugin cache files while continuing to deny Makeflow cache internals (NBO-2)."""
    with (
        _isolated_env() as (_hook_cwd, fake_home),
        tempfile.TemporaryDirectory() as consumer_ws,
    ):
      state_dir = os.path.join(consumer_ws, ".workflow", "state")
      _write_file(
          os.path.join(state_dir, "active_target.json"),
          json.dumps({"target": "T30", "previousTarget": "T20"}) + "\n",
      )
      _write_file(
          os.path.join(state_dir, "99_next_action.md"),
          "# 99_next_action\n- Active Target: Target 30\n",
      )

      third_party_files = (
          os.path.join(
              fake_home,
              ".gemini",
              "config",
              "plugins",
              "other-plugin",
              "hooks.json",
          ),
          os.path.join(
              fake_home,
              ".gemini",
              "plugins",
              "third-party",
              "lib",
              "turn_boundary.py",
          ),
          os.path.join(
              fake_home,
              ".claude",
              "plugins",
              "vendor-tool",
              "lib",
              "config.py",
          ),
      )
      for fpath in third_party_files:
        with self.subTest(kind="third_party_allowed", fpath=fpath):
          _write_file(fpath, "# third-party plugin file\n")
          self.assertTrue(is_plugin_cache_path(fpath))
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(consumer_ws, fpath), state_dir, [consumer_ws]
          )
          self.assertTrue(
              ok, f"Expected third-party plugin allow for {fpath}, got {reason}"
          )

      # Makeflow plugin cache files remain denied both without and with plugin.json
      makeflow_no_manifest = os.path.join(
          fake_home,
          ".gemini",
          "config",
          "plugins",
          "makeflow",
          "lib",
          "turn_boundary.py",
      )
      _write_file(makeflow_no_manifest, "# makeflow without plugin.json\n")
      ok, reason = evaluate_harness_read_boundary(
          _read_payload(consumer_ws, makeflow_no_manifest),
          state_dir,
          [consumer_ws],
      )
      self.assertFalse(
          ok, "Expected deny for makeflow plugin cache without plugin.json"
      )
      self.assertIn("[SCAR-ID: PROC-124]", reason)

      makeflow_with_manifest_dir = os.path.join(
          fake_home, ".gemini", "plugins", "makeflow-dev"
      )
      _write_file(
          os.path.join(makeflow_with_manifest_dir, "plugin.json"),
          json.dumps({"name": "makeflow", "version": "1.0.0"}) + "\n",
      )
      makeflow_with_manifest = os.path.join(
          makeflow_with_manifest_dir, "lib", "turn_boundary.py"
      )
      _write_file(makeflow_with_manifest, "# makeflow with plugin.json\n")
      ok, reason = evaluate_harness_read_boundary(
          _read_payload(consumer_ws, makeflow_with_manifest),
          state_dir,
          [consumer_ws],
      )
      self.assertFalse(
          ok, "Expected deny for makeflow plugin cache with plugin.json"
      )
      self.assertIn("[SCAR-ID: PROC-124]", reason)

  def test_git_origin_main_fallback_when_local_main_ref_absent(self):
    """Allows committed HEAD edits relative to origin/main when local main branch is absent (NBO-3)."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(
          ws_dir, target="T30", plan_text="# Plan\n\nNo code paths listed.\n"
      )
      _run_git(ws_dir, "init", "-b", "main")
      _write_file(
          os.path.join(ws_dir, ".git", "info", "exclude"),
          ".workflow/\n",
      )
      _run_git(ws_dir, "add", "-A")
      _run_git(ws_dir, "commit", "-m", "chore: base fixture")
      base_sha = _run_git(ws_dir, "rev-parse", "HEAD").strip()
      _run_git(ws_dir, "update-ref", "refs/remotes/origin/main", base_sha)

      # Create feature commit, delete local main, and check out detached HEAD
      _run_git(ws_dir, "checkout", "-b", "feat/worktree-branch")
      _write_file(
          os.path.join(ws_dir, "lib", "commit_msg.py"),
          "# committed change against origin/main\n",
      )
      _run_git(ws_dir, "add", "lib/commit_msg.py")
      _run_git(ws_dir, "commit", "-m", "feat: update commit_msg")
      _run_git(ws_dir, "branch", "-D", "main")
      _run_git(ws_dir, "checkout", "--detach", "HEAD")

      ok, reason = evaluate_harness_read_boundary(
          _read_payload(ws_dir, os.path.join(ws_dir, "lib", "commit_msg.py")),
          state_dir,
          [ws_dir],
      )
      self.assertTrue(
          ok,
          f"Expected allow for committed edit relative to origin/main, got {reason}",
      )

      ok, reason = evaluate_harness_read_boundary(
          _read_payload(ws_dir, os.path.join(ws_dir, "lib", "turn_boundary.py")),
          state_dir,
          [ws_dir],
      )
      self.assertFalse(
          ok, "Expected untouched lib/turn_boundary.py to stay denied"
      )
      self.assertIn("[SCAR-ID: PROC-124]", reason)

  def test_empty_workspace_paths_falls_back_to_cwd_resolution(self):
    """Falls back to cwd resolution when workspace_paths contains only empty strings (NBO-4)."""
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(
          ws_dir, target="T30", plan_text="# Plan\n"
      )
      with mock.patch("os.getcwd", return_value=ws_dir):
        for empty_ws in ([""], ["   "]):
          with self.subTest(workspace_paths=empty_ws):
            payload = {
                "workspacePaths": empty_ws,
                "conversationId": "sup-1",
                "toolCall": {
                    "name": "read_file",
                    "args": {"Path": "lib/turn_boundary.py"},
                },
            }
            ok, reason = evaluate_harness_read_boundary(
                payload, state_dir, empty_ws
            )
            self.assertFalse(
                ok,
                f"Expected deny for relative governance path with workspace_paths={empty_ws!r}",
            )
            self.assertIn("[SCAR-ID: PROC-124]", reason)

  def test_mcp_server_transitive_internals_guarded_at_t30_with_scope_escapes(
      self,
  ):
    """Verifies MCP transitive internals are denied out-of-scope at T30 and allowed via plan, Git diff, or child subagent (GH-300)."""
    target_relpaths = (
        "lib/ci_polling.py",
        "lib/scars_db.py",
        "lib/scar_metadata.py",
        "lib/scar_ontology.py",
    )
    with _isolated_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = _init_makeflow_checkout(
          ws_dir, target="T30", plan_text="# Plan\n\nEdit `README.md` only.\n"
      )
      for rel in target_relpaths:
        _write_file(os.path.join(ws_dir, rel), f"# {rel}\n")

      # 1. Out-of-scope Depth-0 supervisor reads are denied at T30
      for rel in target_relpaths:
        with self.subTest(case="out_of_scope_deny", rel=rel):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertFalse(ok, f"Expected deny for out-of-scope {rel}")
          self.assertIn("[SCAR-ID: PROC-124]", reason)
          self.assertIn("OUT_OF_SCOPE_HARNESS_INSPECTION", reason)
          self.assertIn(rel, reason)

      # 2. Reads explicitly listed in 20_plan.md are allowed
      plan_lines = "\n".join(f"- `{rel}`" for rel in target_relpaths)
      _write_file(
          os.path.join(state_dir, "20_plan.md"),
          f"# Plan\n\n{plan_lines}\n",
      )
      for rel in target_relpaths:
        with self.subTest(case="plan_listed_allow", rel=rel):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertTrue(
              ok, f"Expected plan-listed allow for {rel}, got {reason}"
          )

      # 3. Reads modified in active Git diff (absent from 20_plan.md) are allowed
      _write_file(
          os.path.join(state_dir, "20_plan.md"),
          "# Plan\n\nNo code paths listed.\n",
      )
      _run_git(ws_dir, "init", "-b", "main")
      _write_file(
          os.path.join(ws_dir, ".git", "info", "exclude"),
          ".workflow/\n",
      )
      _run_git(ws_dir, "add", "-A")
      _run_git(ws_dir, "commit", "-m", "chore: initial fixture")

      # Stage two files and leave two files unstaged
      for rel in target_relpaths[:2]:
        _write_file(os.path.join(ws_dir, rel), f"# staged change {rel}\n")
        _run_git(ws_dir, "add", rel)
      for rel in target_relpaths[2:]:
        _write_file(os.path.join(ws_dir, rel), f"# unstaged change {rel}\n")

      for rel in target_relpaths:
        with self.subTest(case="git_diff_allow", rel=rel):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(ws_dir, os.path.join(ws_dir, rel)),
              state_dir,
              [ws_dir],
          )
          self.assertTrue(
              ok, f"Expected Git diff allow for {rel}, got {reason}"
          )

      # 4. Child subagent reads are allowed when clean and absent from 20_plan.md
      _run_git(ws_dir, "reset", "--hard", "HEAD")
      _write_file(
          os.path.join(state_dir, "active_subagent.json"),
          json.dumps({
              "supervisorConversationId": "sup-1",
              "typeName": "tdd-worker",
          })
          + "\n",
      )
      for rel in target_relpaths:
        with self.subTest(case="child_subagent_allow", rel=rel):
          ok, reason = evaluate_harness_read_boundary(
              _read_payload(
                  ws_dir,
                  os.path.join(ws_dir, rel),
                  conversation_id="worker-2",
              ),
              state_dir,
              [ws_dir],
          )
          self.assertTrue(
              ok, f"Expected child subagent allow for {rel}, got {reason}"
          )
          ok_sup, reason_sup = evaluate_harness_read_boundary(
              _read_payload(
                  ws_dir,
                  os.path.join(ws_dir, rel),
                  conversation_id="sup-1",
              ),
              state_dir,
              [ws_dir],
          )
          self.assertFalse(
              ok_sup, f"Expected supervisor deny for clean {rel}"
          )
          self.assertIn("[SCAR-ID: PROC-124]", reason_sup)

  def test_plan_path_helpers_reexported_from_plan_validation(self):
    """GH-331 / SCAR-API-0002: harness_boundary re-exports canonical plan helpers from lib.plan_validation."""
    import lib.harness_boundary as hb
    import lib.plan_validation as pv

    self.assertIs(hb.plan_mentions_relpath, pv.plan_mentions_relpath)
    self.assertIs(hb.plan_allows_edit_relpath, pv.plan_allows_edit_relpath)

  def test_expanded_agent_plugin_caches_and_symlinks_detected(self):
    """GH-331: Detects ~/.codex/plugins, ~/.cursor/plugins, ~/.copilot/plugins, and symlinks into them."""
    with (
        _isolated_env() as (_hook_cwd, fake_home),
        tempfile.TemporaryDirectory() as ws_dir,
    ):
      for rel_cache in (
          os.path.join(".codex", "plugins", "makeflow", "lib", "tdd_guard.py"),
          os.path.join(".cursor", "plugins", "makeflow", "lib", "config.py"),
          os.path.join(
              ".copilot", "plugins", "makeflow", "scripts", "pre_tool_guard.py"
          ),
      ):
        with self.subTest(rel_cache=rel_cache):
          cache_file = os.path.join(fake_home, rel_cache)
          _write_file(cache_file, "# installed plugin file\n")
          self.assertTrue(
              is_plugin_cache_path(cache_file),
              f"Expected is_plugin_cache_path True for {cache_file}",
          )
          link_path = os.path.join(
              ws_dir, f"link_{os.path.basename(cache_file)}"
          )
          if os.path.lexists(link_path):
            os.unlink(link_path)
          os.symlink(cache_file, link_path)
          self.assertTrue(
              is_plugin_cache_path(link_path),
              f"Expected symlink {link_path} -> {cache_file} to be detected as plugin cache",
          )


if __name__ == "__main__":
  unittest.main()
