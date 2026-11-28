"""Behavioral unit tests for deleted-CWD resilience across Makeflow modules (GH-328).

Exercises both real OS deleted working directories (ENOENT from Linux kernel)
and injected FileNotFoundError via mock.patch('os.getcwd') to verify upstream
path safety across lib/agent_paths.py, lib/config.py, lib/scars_db.py,
lib/ci_polling.py, mcp/server.py, hook hot paths, and CLI utilities.
"""

from contextlib import contextmanager
import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

import lib.agent_paths as agent_paths_mod
import lib.ast_scrub as ast_scrub_mod
import lib.ci_polling as ci_polling_mod
import lib.commit_msg as commit_msg_mod
import lib.config as config_mod
import lib.harness_boundary as harness_boundary_mod
import lib.local_tickets as local_tickets_mod
import lib.pre_tool_guard as pre_tool_guard_mod
import lib.scar_miner as scar_miner_mod
import lib.scars_db as scars_db_mod
import lib.session_start as session_start_mod
import lib.static_check as static_check_mod
import lib.terminal_title as terminal_title_mod
import lib.workspace_mutex as workspace_mutex_mod
import mcp.server as mcp_server_mod


@contextmanager
def _deleted_cwd():
  """Temporarily chdir into an ephemeral directory and unlink it, restoring CWD on exit."""
  orig_cwd = os.getcwd()
  with tempfile.TemporaryDirectory() as outer_dir:
    ephemeral_dir = os.path.join(outer_dir, "unlinked_cwd")
    os.mkdir(ephemeral_dir)
    os.chdir(ephemeral_dir)
    try:
      os.rmdir(ephemeral_dir)
      yield ephemeral_dir
    finally:
      os.chdir(orig_cwd)


class TestSafeCwdAndConfigResilience(unittest.TestCase):
  """Slice 1: Verifies safe_getcwd, safe_abspath, and lib/config.py under deleted CWD."""

  def test_safe_getcwd_exported_and_returns_live_cwd(self):
    """Verifies safe_getcwd is exported from lib.agent_paths and returns current directory."""
    safe_getcwd = getattr(agent_paths_mod, "safe_getcwd", None)
    self.assertIsNotNone(safe_getcwd, "safe_getcwd must be exported by lib.agent_paths")
    expected = os.getcwd()
    self.assertEqual(safe_getcwd(), expected)

  def test_safe_getcwd_real_deleted_cwd_returns_none_or_fallback(self):
    """Verifies safe_getcwd catches real kernel ENOENT and returns None or explicit fallback."""
    safe_getcwd = agent_paths_mod.safe_getcwd
    with _deleted_cwd():
      with self.assertRaises(FileNotFoundError):
        os.getcwd()
      self.assertIsNone(safe_getcwd())
      self.assertEqual(safe_getcwd(fallback="/fallback/root"), "/fallback/root")

  def test_safe_getcwd_respects_mock_patch_os_getcwd(self):
    """Verifies safe_getcwd delegates directly to os.getcwd so mock.patch('os.getcwd') works."""
    safe_getcwd = agent_paths_mod.safe_getcwd
    with mock.patch(
        "os.getcwd",
        side_effect=FileNotFoundError(2, "No such file or directory"),
    ) as mocked_getcwd:
      self.assertIsNone(safe_getcwd())
      self.assertEqual(safe_getcwd(fallback=""), "")
      self.assertEqual(mocked_getcwd.call_count, 2)

    with mock.patch("os.getcwd", side_effect=PermissionError(13, "Permission denied")):
      self.assertEqual(safe_getcwd(fallback="/safe"), "/safe")

  def test_safe_abspath_handles_absolute_relative_and_empty_paths_under_deleted_cwd(self):
    """Verifies safe_abspath normalizes paths safely with or without base_dir when CWD is deleted."""
    safe_abspath = getattr(agent_paths_mod, "safe_abspath", None)
    self.assertIsNotNone(safe_abspath, "safe_abspath must be exported by lib.agent_paths")

    with tempfile.TemporaryDirectory() as live_dir:
      orig_cwd = os.getcwd()
      os.chdir(live_dir)
      try:
        self.assertEqual(
            safe_abspath("sub/file.db"),
            os.path.join(os.path.realpath(live_dir), "sub", "file.db"),
        )
        self.assertEqual(safe_abspath(""), os.path.realpath(live_dir))
      finally:
        os.chdir(orig_cwd)

    with _deleted_cwd():
      self.assertEqual(safe_abspath("/alpha/beta/../gamma/"), "/alpha/gamma")
      self.assertEqual(
          safe_abspath("rel/scars.db", base_dir="/opt/workspace"),
          "/opt/workspace/rel/scars.db",
      )
      self.assertEqual(
          safe_abspath("", base_dir="/opt/workspace/sub/.."),
          "/opt/workspace",
      )
      self.assertEqual(safe_abspath("rel/../scars.db"), "scars.db")
      self.assertEqual(safe_abspath(""), "")
      self.assertEqual(safe_abspath(None), "")

  def test_config_five_cwd_resolvers_tolerate_real_deleted_cwd(self):
    """Verifies all five lib/config.py CWD resolvers degrade cleanly under real kernel ENOENT."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fake_home = os.path.join(tmp_root, "home")
      artifact_dir = os.path.join(tmp_root, "artifact")
      artifact_state = os.path.join(artifact_dir, "state")
      os.makedirs(fake_home, exist_ok=True)
      os.makedirs(artifact_state, exist_ok=True)

      clean_env = {
          k: v
          for k, v in os.environ.items()
          if k != "MAKEFILE_WORKFLOW_WORKSPACE"
      }
      with mock.patch.dict(os.environ, clean_env, clear=True):
        with _deleted_cwd():
          defaults = config_mod.load_default_config(plugin_root=None)
          self.assertIsInstance(defaults, dict)
          self.assertIn("models", defaults)
          self.assertIn("scars", defaults)

          rel_defaults = config_mod.load_default_config(plugin_root="relative/plugin")
          self.assertIsInstance(rel_defaults, dict)
          self.assertIn("models", rel_defaults)

          candidates = config_mod._iter_cascaded_config_candidates(
              [], agent="gemini", home_dir=fake_home, allow_cwd_fallback=True
          )
          layer_kinds = [kind for _, _, kind in candidates]
          self.assertEqual(layer_kinds, ["legacy", "user_global"])

          self.assertEqual(config_mod.find_state_dir([], None), "")
          self.assertEqual(
              config_mod.find_state_dir([], artifact_dir), artifact_state
          )
          self.assertEqual(config_mod.resolve_state_dir({}), "")
          self.assertIsNone(config_mod.resolve_workspace_dir({}))

          resolved = config_mod.resolve_workflow_config(
              workspace_dir=None, home_dir=fake_home
          )
          self.assertEqual(resolved["configSource"], "defaults")
          self.assertIn("scars", resolved)

  def test_config_five_cwd_resolvers_tolerate_mocked_os_getcwd_enoent(self):
    """Verifies all five lib/config.py CWD resolvers handle mocked os.getcwd FileNotFoundError."""
    with tempfile.TemporaryDirectory() as fake_home:
      clean_env = {
          k: v
          for k, v in os.environ.items()
          if k != "MAKEFILE_WORKFLOW_WORKSPACE"
      }
      with mock.patch.dict(os.environ, clean_env, clear=True):
        with mock.patch(
            "os.getcwd",
            side_effect=FileNotFoundError(2, "No such file or directory"),
        ):
          defaults = config_mod.load_default_config(plugin_root=None)
          self.assertIsInstance(defaults, dict)
          self.assertIn("ci", defaults)

          candidates = config_mod._iter_cascaded_config_candidates(
              [], agent="gemini", home_dir=fake_home, allow_cwd_fallback=True
          )
          self.assertEqual([kind for _, _, kind in candidates], ["legacy", "user_global"])
          self.assertEqual(config_mod.find_state_dir([], None), "")
          self.assertEqual(config_mod.resolve_state_dir({}), "")
          self.assertIsNone(config_mod.resolve_workspace_dir({}))


class TestMcpAndScarsDeletedCwdResilience(unittest.TestCase):
  """Slice 2: Verifies scars session ledger, poll_ci_status PathContext, and MCP handlers under deleted CWD."""

  def _seed_temp_scar_db(self, db_path: str) -> None:
    """Seeds a minimal hermetic SQLite scar database inside a temp directory."""
    scars_db_mod.upsert_scar(
        db_path,
        {
            "id": "SCAR-LOCAL-TEST-0001",
            "title": "Hermetic deleted CWD test scar",
            "category": "TEST",
            "wound": "Deleted working directory broke scar lookup.",
            "trap": "Relative keys were passed to os.path.abspath.",
            "reflex": "Keep logical session keys verbatim and guard CWD lookups.",
            "socratic_probe": "Does scar lookup succeed when CWD is unlinked?",
            "workflow_phases": ["Target 30: tdd"],
            "source": "local",
        },
    )

  def test_scars_session_ledger_preserves_logical_session_key_under_deleted_cwd(self):
    """Verifies load_session_scars and save_session_scars keep logical session_key verbatim without CWD."""
    logical_key = "ws:/hermetic/deleted-cwd-project"
    empty_ws_key = "ws:"
    explicit_key = "explicit:/hermetic/custom.db"
    scars_db_mod._IN_MEMORY_LOADED_SCARS.pop(logical_key, None)
    scars_db_mod._IN_MEMORY_LOADED_SCARS.pop(empty_ws_key, None)
    scars_db_mod._IN_MEMORY_LOADED_SCARS.pop(explicit_key, None)
    scars_db_mod._IN_MEMORY_LOADED_SCARS.pop("default", None)

    try:
      with _deleted_cwd():
        scars_db_mod.save_session_scars(
            ["SCAR-ARCH-0001"], state_dir=None, session_key=logical_key
        )
        self.assertIn(logical_key, scars_db_mod._IN_MEMORY_LOADED_SCARS)
        self.assertEqual(
            scars_db_mod.load_session_scars(
                state_dir=None, session_key=logical_key
            ),
            {"SCAR-ARCH-0001"},
        )

        scars_db_mod.save_session_scars(
            ["SCAR-API-0001"], state_dir=None, session_key=empty_ws_key
        )
        self.assertIn(empty_ws_key, scars_db_mod._IN_MEMORY_LOADED_SCARS)
        self.assertEqual(
            scars_db_mod.load_session_scars(
                state_dir=None, session_key=empty_ws_key
            ),
            {"SCAR-API-0001"},
        )

        scars_db_mod.save_session_scars(
            ["SCAR-API-0002"], state_dir=None, session_key=explicit_key
        )
        self.assertIn(explicit_key, scars_db_mod._IN_MEMORY_LOADED_SCARS)
        self.assertEqual(
            scars_db_mod.load_session_scars(
                state_dir=None, session_key=explicit_key
            ),
            {"SCAR-API-0002"},
        )

        scars_db_mod.save_session_scars(["SCAR-TEST-0001"])
        self.assertIn("default", scars_db_mod._IN_MEMORY_LOADED_SCARS)
        self.assertEqual(
            scars_db_mod.load_session_scars(), {"SCAR-TEST-0001"}
        )
    finally:
      for k in (logical_key, empty_ws_key, explicit_key, "default"):
        scars_db_mod._IN_MEMORY_LOADED_SCARS.pop(k, None)

  def test_poll_ci_status_keyword_only_context_validation_and_positional_compat(self):
    """Verifies poll_ci_status accepts keyword-only PathContext, validates type, and keeps positional compat."""
    sig = inspect.signature(ci_polling_mod.poll_ci_status)
    self.assertIn("context", sig.parameters)
    self.assertEqual(
        sig.parameters["context"].kind, inspect.Parameter.KEYWORD_ONLY
    )

    with tempfile.TemporaryDirectory() as ws_dir:
      with self.assertRaises(TypeError):
        ci_polling_mod.poll_ci_status(
            0, ws_dir, hosting_mode="pure_git", context="invalid-context"
        )

      # Backward-compatible positional invocation with existing parameters
      pos_res = ci_polling_mod.poll_ci_status(0, ws_dir, 0, 1, 1, "pure_git")
      self.assertEqual(pos_res["mode"], "pure_git")
      self.assertEqual(pos_res["ciStatus"], "SKIPPED")

      ctx = agent_paths_mod.PathContext(
          workspace_dir=ws_dir, plugin_root=REPO_ROOT
      )
      with _deleted_cwd():
        with mock.patch.object(
            ci_polling_mod,
            "resolve_workflow_config",
            wraps=config_mod.resolve_workflow_config,
        ) as wrapped_cfg:
          res = ci_polling_mod.poll_ci_status(
              0, ws_dir, hosting_mode="pure_git", context=ctx
          )
      self.assertEqual(res["mode"], "pure_git")
      self.assertEqual(res["ciStatus"], "SKIPPED")
      wrapped_cfg.assert_called_once_with(context=ctx)

  def test_mcp_workflow_query_scars_all_modes_under_deleted_cwd(self):
    """Verifies workflow_query_scars handles omitted workspaceDir, explicit/relative/missing dbPath under deleted CWD."""
    with tempfile.TemporaryDirectory() as tmp_root:
      ws_dir = os.path.join(tmp_root, "workspace")
      rel_db_dir = os.path.join(ws_dir, "local_scars")
      os.makedirs(rel_db_dir, exist_ok=True)
      abs_db = os.path.join(rel_db_dir, "custom.db")
      self._seed_temp_scar_db(abs_db)

      clean_env = {
          k: v
          for k, v in os.environ.items()
          if k != "MAKEFILE_WORKFLOW_WORKSPACE"
      }
      with mock.patch.dict(os.environ, clean_env, clear=True):
        with _deleted_cwd():
          # 1. Omitted workspaceDir -> bundled scars DB via _REPO_ROOT and logical session_key "ws:"
          resp_omitted = mcp_server_mod.handle_tools_call(
              101,
              "workflow_query_scars",
              {
                  "language": "python",
                  "workflowPhase": "Target 30: tdd",
                  "limit": 2,
                  "refreshLoaded": True,
              },
          )
          self.assertFalse(resp_omitted["result"].get("isError", False))
          payload_omitted = json.loads(resp_omitted["result"]["content"][0]["text"])
          self.assertEqual(payload_omitted["status"], "OK")
          self.assertGreaterEqual(payload_omitted["totalResults"], 1)

          # 2. Explicit absolute dbPath
          resp_abs = mcp_server_mod.handle_tools_call(
              102,
              "workflow_query_scars",
              {
                  "query": "SCAR-LOCAL-TEST-0001",
                  "dbPath": abs_db,
                  "refreshLoaded": True,
              },
          )
          self.assertFalse(resp_abs["result"].get("isError", False))
          payload_abs = json.loads(resp_abs["result"]["content"][0]["text"])
          self.assertEqual(payload_abs["status"], "OK")
          self.assertEqual(payload_abs["scars"][0]["id"], "SCAR-LOCAL-TEST-0001")

          # 3. Relative dbPath anchored to workspaceDir
          resp_rel = mcp_server_mod.handle_tools_call(
              103,
              "workflow_query_scars",
              {
                  "query": "SCAR-LOCAL-TEST-0001",
                  "workspaceDir": ws_dir,
                  "dbPath": "local_scars/custom.db",
                  "refreshLoaded": True,
              },
          )
          self.assertFalse(resp_rel["result"].get("isError", False))
          payload_rel = json.loads(resp_rel["result"]["content"][0]["text"])
          self.assertEqual(payload_rel["status"], "OK")
          self.assertEqual(payload_rel["searchedDatabases"], [abs_db])

          # 4. Missing optional dbPath (relative) returns UNINITIALIZED without FileNotFoundError
          resp_missing = mcp_server_mod.handle_tools_call(
              104,
              "workflow_query_scars",
              {
                  "query": "SCAR-LOCAL-TEST-0001",
                  "workspaceDir": ws_dir,
                  "dbPath": "local_scars/does_not_exist.db",
              },
          )
          self.assertFalse(resp_missing["result"].get("isError", False))
          payload_missing = json.loads(resp_missing["result"]["content"][0]["text"])
          self.assertEqual(payload_missing["status"], "UNINITIALIZED")
          self.assertEqual(payload_missing["scars"], [])

  def test_mcp_poll_ci_safety_check_and_get_config_under_deleted_cwd(self):
    """Verifies workflow_poll_ci, workflow_safety_check, and workflow_get_config thread PathContext under deleted CWD."""
    with tempfile.TemporaryDirectory() as ws_dir:
      clean_env = {
          k: v
          for k, v in os.environ.items()
          if k != "MAKEFILE_WORKFLOW_WORKSPACE"
      }
      with mock.patch.dict(os.environ, clean_env, clear=True):
        with _deleted_cwd():
          # 1. workflow_poll_ci threads PathContext(workspace_dir=ws, plugin_root=_REPO_ROOT)
          with mock.patch.object(
              mcp_server_mod,
              "poll_ci_status",
              wraps=ci_polling_mod.poll_ci_status,
          ) as wrapped_poll:
            resp_ci = mcp_server_mod.handle_tools_call(
                201,
                "workflow_poll_ci",
                {"prNumber": 0, "workspaceDir": ws_dir, "maxWaitSeconds": 1},
            )
          self.assertFalse(resp_ci["result"].get("isError", False))
          ci_payload = json.loads(resp_ci["result"]["content"][0]["text"])
          self.assertEqual(ci_payload["mode"], "pure_git")
          self.assertEqual(ci_payload["ciStatus"], "SKIPPED")
          passed_ctx = wrapped_poll.call_args.kwargs.get("context")
          self.assertEqual(
              passed_ctx,
              agent_paths_mod.PathContext(
                  workspace_dir=ws_dir, plugin_root=mcp_server_mod._REPO_ROOT
              ),
          )

          # 2. workflow_safety_check threads PathContext(workspace_dir=ws, plugin_root=_REPO_ROOT)
          with mock.patch.object(
              mcp_server_mod,
              "resolve_workflow_config",
              wraps=config_mod.resolve_workflow_config,
          ) as wrapped_cfg:
            resp_safe = mcp_server_mod.handle_tools_call(
                202,
                "workflow_safety_check",
                {"commandLine": "git status", "workspaceDir": ws_dir},
            )
          self.assertFalse(resp_safe["result"].get("isError", False))
          safe_payload = json.loads(resp_safe["result"]["content"][0]["text"])
          self.assertTrue(safe_payload["safe"])
          wrapped_cfg.assert_called_once_with(
              context=agent_paths_mod.PathContext(
                  workspace_dir=ws_dir, plugin_root=mcp_server_mod._REPO_ROOT
              )
          )

          # 3. workflow_get_config with omitted workspaceDir succeeds under deleted CWD
          resp_cfg = mcp_server_mod.handle_tools_call(
              203,
              "workflow_get_config",
              {},
          )
          self.assertFalse(resp_cfg["result"].get("isError", False))
          cfg_payload = json.loads(resp_cfg["result"]["content"][0]["text"])
          self.assertIn("models", cfg_payload)
          self.assertIn("scars", cfg_payload)


class TestHookHotPathDeletedCwdResilience(unittest.TestCase):
  """Slice 3: Verifies pre_tool_guard, harness_boundary, and session_start under deleted CWD."""

  def test_pre_tool_guard_preserves_fail_closed_checks_under_deleted_cwd(self):
    """Verifies pre_tool_guard never raises FileNotFoundError or fails open when CWD is deleted."""
    with _deleted_cwd():
      self.assertIsNone(pre_tool_guard_mod._resolve_effective_state_dir(None))

      safe_cd, reason_cd = pre_tool_guard_mod.evaluate_self_modification_safety(
          "cd /var/tmp && echo bad > .workflow/state/Makefile",
          workspace_paths=[],
      )
      self.assertFalse(safe_cd)
      self.assertIn("Makefile", reason_cd)

      benign_cd, _ = pre_tool_guard_mod.evaluate_self_modification_safety(
          "cd relative_subdir && ls",
          workspace_paths=[],
      )
      self.assertTrue(benign_cd)

      self.assertEqual(
          pre_tool_guard_mod._resolve_direct_target_paths("relative/file.py", []),
          ["relative/file.py"],
      )

      git_decision = pre_tool_guard_mod.evaluate_tool_call({
          "workspacePaths": [],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git reset --hard HEAD"},
          },
      })
      self.assertEqual(git_decision["decision"], "deny")

      write_gov_decision = pre_tool_guard_mod.evaluate_tool_call({
          "workspacePaths": [],
          "toolCall": {
              "name": "write_file",
              "args": {"Path": ".workflow/state/Makefile"},
          },
      })
      self.assertEqual(write_gov_decision["decision"], "deny")

      write_benign_decision = pre_tool_guard_mod.evaluate_tool_call({
          "workspacePaths": [],
          "toolCall": {
              "name": "write_file",
              "args": {"Path": "relative/benign.py"},
          },
      })
      self.assertEqual(write_benign_decision["decision"], "allow")

      out = io.StringIO()
      err = io.StringIO()
      stdin_payload = json.dumps({
          "workspacePaths": [],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "rm -rf .workflow/state/Makefile"},
          },
      })
      rc = pre_tool_guard_mod.PreToolGuardScript().execute(
          args=[], stdout=out, stderr=err, stdin=io.StringIO(stdin_payload)
      )
      self.assertEqual(rc, 0)
      hook_out = json.loads(out.getvalue())
      self.assertEqual(hook_out["decision"], "deny")

  def test_harness_boundary_resolves_relative_paths_and_blocks_locks_under_deleted_cwd(self):
    """Verifies harness_boundary handles relative targets with empty workspacePaths under deleted CWD."""
    with tempfile.TemporaryDirectory() as tmp_root:
      state_dir = os.path.join(tmp_root, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      with open(
          os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
      ) as fh:
        json.dump(
            {
                "version": 1,
                "target": "T30",
                "status": "ACTIVE",
                "ticket": "GH-328",
            },
            fh,
        )

      with _deleted_cwd():
        candidates = harness_boundary_mod._resolve_target_candidates(
            "relative/path.py", []
        )
        self.assertEqual(candidates, ["relative/path.py"])

        lock_allowed, lock_reason = (
            harness_boundary_mod.evaluate_harness_read_boundary(
                {
                    "workspacePaths": [],
                    "toolCall": {
                        "name": "read_file",
                        "args": {"Path": ".workflow/state/active_target.json"},
                    },
                },
                state_dir=state_dir,
                workspace_paths=[],
            )
        )
        self.assertFalse(lock_allowed)
        self.assertIn("SCAR-PROC-124", lock_reason)

        benign_allowed, benign_reason = (
            harness_boundary_mod.evaluate_harness_read_boundary(
                {
                    "workspacePaths": [],
                    "toolCall": {
                        "name": "read_file",
                        "args": {"Path": "relative/benign.py"},
                    },
                },
                state_dir=state_dir,
                workspace_paths=[],
            )
        )
        self.assertTrue(benign_allowed)
        self.assertEqual(benign_reason, "")

  def test_session_start_emits_notes_cleanly_under_deleted_cwd(self):
    """Verifies SessionStartScript emits status notes for clean and active states under deleted CWD."""
    with tempfile.TemporaryDirectory() as tmp_root:
      artifact_dir = os.path.join(tmp_root, "brain")
      brain_state = os.path.join(artifact_dir, "state")
      os.makedirs(brain_state, exist_ok=True)
      with open(
          os.path.join(brain_state, "active_target.json"), "w", encoding="utf-8"
      ) as fh:
        json.dump(
            {
                "version": 1,
                "target": "T30",
                "status": "ACTIVE",
                "ticket": "GH-328",
            },
            fh,
        )
      with open(
          os.path.join(brain_state, "10_ticket.md"), "w", encoding="utf-8"
      ) as fh:
        fh.write("# Ticket GH-328\n")

      with _deleted_cwd():
        clean_notes = session_start_mod.SessionStartScript()._build_notes([], "")
        self.assertTrue(
            any("Workspace is clean" in note for note in clean_notes),
            f"Expected clean-workspace note, got: {clean_notes}",
        )

        active_notes = session_start_mod.SessionStartScript()._build_notes(
            [], artifact_dir
        )
        self.assertTrue(
            any("state card(s)" in note for note in active_notes),
            f"Expected active state-card note, got: {active_notes}",
        )

        out = io.StringIO()
        err = io.StringIO()
        rc = session_start_mod.SessionStartScript().execute(
            args=[],
            stdout=out,
            stderr=err,
            stdin=io.StringIO(json.dumps({"workspacePaths": []})),
        )
        self.assertEqual(rc, 0)
        emitted = json.loads(out.getvalue())
        self.assertIn("injectSteps", emitted)
        self.assertGreaterEqual(len(emitted["injectSteps"]), 1)


class TestCliUtilitiesDeletedCwdResilience(unittest.TestCase):
  """Slice 4: Verifies all six CLI utility modules tolerate deleted CWD."""

  def test_terminal_title_helpers_and_script_tolerate_deleted_cwd(self):
    """Verifies terminal_title helpers, constructor, and parse_args under deleted CWD."""
    with tempfile.TemporaryDirectory() as ws_dir:
      named_dir = os.path.join(ws_dir, "demo_project")
      os.makedirs(named_dir, exist_ok=True)
      with _deleted_cwd():
        self.assertEqual(terminal_title_mod.detect_project_name(repo_root=None), "")
        self.assertEqual(
            terminal_title_mod.detect_project_name(
                repo_root=None,
                git_remote_url="https://github.com/org/my-project.git",
            ),
            "my-project",
        )
        self.assertEqual(terminal_title_mod.detect_directory_name(repo_root=None), "")
        self.assertEqual(terminal_title_mod.detect_ticket_id(repo_root=None), "")
        self.assertEqual(
            terminal_title_mod.detect_ticket_id(
                repo_root=None, branch="feat/328-safe-cwd"
            ),
            "GH-328",
        )
        self.assertEqual(terminal_title_mod.detect_issue_tag(repo_root=None), "")
        self.assertEqual(
            terminal_title_mod.detect_issue_tag(
                repo_root=None, branch="feat/328-safe-cwd"
            ),
            "safe cwd",
        )
        ok, applied = terminal_title_mod.set_terminal_title(
            repo_root=None, stream=io.StringIO()
        )
        self.assertFalse(ok)
        self.assertEqual(applied, "")

        script = terminal_title_mod.TerminalTitleScript()
        self.assertEqual(script.repo_root, "")
        script.parse_args(["info", "--repo-root", named_dir])
        self.assertEqual(script.repo_root, os.path.normpath(named_dir))
        script.parse_args(["detect"])
        self.assertEqual(script.repo_root, "")

  def test_workspace_mutex_and_local_tickets_tolerate_deleted_cwd(self):
    """Verifies WorkspaceMutex, WorkspaceMutexScript, LocalTicketStore, and LocalTicketScript under deleted CWD."""
    with tempfile.TemporaryDirectory() as tmp_root:
      ws_dir = os.path.join(tmp_root, "repo")
      lock_dir = os.path.join(tmp_root, "locks")
      os.makedirs(ws_dir, exist_ok=True)
      os.makedirs(lock_dir, exist_ok=True)

      with _deleted_cwd():
        mutex = workspace_mutex_mod.WorkspaceMutex(
            repo_root=None, lock_dir=lock_dir
        )
        self.assertEqual(mutex.repo_root, "")

        wm_script = workspace_mutex_mod.WorkspaceMutexScript()
        self.assertEqual(wm_script.repo_root, "")
        wm_script.parse_args(
            ["status", "--ticket", "GH-328", "--repo-root", ws_dir, "--lock-dir", lock_dir]
        )
        self.assertEqual(wm_script.repo_root, os.path.normpath(ws_dir))
        wm_script.parse_args(["status", "--ticket", "GH-328"])
        self.assertEqual(wm_script.repo_root, "")

        store = local_tickets_mod.LocalTicketStore(
            repo_root=None, lock_dir=lock_dir
        )
        self.assertEqual(store.repo_root, "")

        lt_script = local_tickets_mod.LocalTicketScript()
        self.assertEqual(lt_script.repo_root, "")
        lt_script.parse_args(["list", "--repo-root", ws_dir, "--lock-dir", lock_dir])
        self.assertEqual(lt_script.repo_root, os.path.normpath(ws_dir))
        lt_script.parse_args(["list"])
        self.assertEqual(lt_script.repo_root, "")

  def test_scar_miner_save_and_save_config_tolerate_deleted_cwd(self):
    """Verifies ScarMinerScript paths, save, and save-config degrade cleanly under deleted CWD."""
    with tempfile.TemporaryDirectory() as tmp_root:
      ws_dir = os.path.join(tmp_root, "repo")
      fake_home = os.path.join(tmp_root, "home")
      custom_db = os.path.join(tmp_root, "custom_scars.db")
      os.makedirs(ws_dir, exist_ok=True)
      os.makedirs(fake_home, exist_ok=True)

      with _deleted_cwd():
        # 1. Omitted --workspace on project save reports ValueError instead of FileNotFoundError
        out_save = io.StringIO()
        err_save = io.StringIO()
        rc_save = scar_miner_mod.ScarMinerScript().execute(
            args=[
                "save",
                "--tier",
                "project",
                "--home",
                fake_home,
                "--title",
                "Test title",
            ],
            stdout=out_save,
            stderr=err_save,
        )
        self.assertNotEqual(rc_save, 0)
        self.assertNotIn("No such file or directory", err_save.getvalue())
        self.assertIn("--workspace", err_save.getvalue())

        # 2. Omitted --workspace on project-local save-config reports ValueError instead of FileNotFoundError
        out_cfg_err = io.StringIO()
        err_cfg_err = io.StringIO()
        rc_cfg_err = scar_miner_mod.ScarMinerScript().execute(
            args=[
                "save-config",
                "--db",
                custom_db,
                "--scope",
                "project-local",
                "--home",
                fake_home,
            ],
            stdout=out_cfg_err,
            stderr=err_cfg_err,
        )
        self.assertNotEqual(rc_cfg_err, 0)
        self.assertNotIn("No such file or directory", err_cfg_err.getvalue())
        self.assertIn("workspace_dir", err_cfg_err.getvalue())

        # 3. Explicit --workspace succeeds under deleted CWD
        out_cfg_ok = io.StringIO()
        err_cfg_ok = io.StringIO()
        rc_cfg_ok = scar_miner_mod.ScarMinerScript().execute(
            args=[
                "save-config",
                "--db",
                custom_db,
                "--scope",
                "project-local",
                "--workspace",
                ws_dir,
                "--home",
                fake_home,
            ],
            stdout=out_cfg_ok,
            stderr=err_cfg_ok,
        )
        self.assertEqual(rc_cfg_ok, 0, f"stderr={err_cfg_ok.getvalue()}")
        cfg_receipt = json.loads(out_cfg_ok.getvalue())
        self.assertEqual(cfg_receipt["scope"], "project-local")

  def test_static_check_and_ast_scrub_tolerate_deleted_cwd(self):
    """Verifies StaticCheckScript and AstScrubScript use lazy repo_root defaults under deleted CWD."""
    with tempfile.TemporaryDirectory() as ws_dir:
      sample_py = os.path.join(ws_dir, "clean_mod.py")
      with open(sample_py, "w", encoding="utf-8") as fh:
        fh.write("VALUE = 1\n")

      with _deleted_cwd():
        sc = static_check_mod.StaticCheckScript()
        self.assertEqual(sc.repo_root, "")
        sc.parse_args(["--repo-root", ws_dir])
        self.assertEqual(sc.repo_root, os.path.normpath(ws_dir))
        sc.parse_args([])
        self.assertEqual(sc.repo_root, "")

        sc_out = io.StringIO()
        sc_err = io.StringIO()
        with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
          rc_sc = static_check_mod.StaticCheckScript().execute(
              args=["--repo-root", ws_dir], stdout=sc_out, stderr=sc_err
          )
        self.assertEqual(rc_sc, 0, f"stderr={sc_err.getvalue()}")

        ast_sc = ast_scrub_mod.AstScrubScript()
        self.assertEqual(ast_sc.repo_root, "")
        ast_sc.parse_args(["--repo-root", ws_dir, "--all"])
        self.assertEqual(ast_sc.repo_root, os.path.normpath(ws_dir))
        ast_sc.parse_args([])
        self.assertEqual(ast_sc.repo_root, "")

        ast_out = io.StringIO()
        ast_err = io.StringIO()
        rc_ast = ast_scrub_mod.AstScrubScript().execute(
            args=["--repo-root", ws_dir, "--all"], stdout=ast_out, stderr=ast_err
        )
        self.assertEqual(rc_ast, 0, f"stderr={ast_err.getvalue()}")


class TestRoundOneNboRollupDeletedCwdResilience(unittest.TestCase):
  """Slice 5: Verifies Round 1 NBO-1 through NBO-4 under deleted CWD."""

  def test_commit_msg_script_init_parse_args_and_validate_under_deleted_cwd(self):
    """NBO-1: Verifies CommitMsgScript handles __init__, parse_args, and validate under deleted CWD."""
    with tempfile.TemporaryDirectory() as ws_dir:
      msg_file = os.path.join(ws_dir, "commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as fh:
        fh.write(
            "fix(paths): guard deleted CWD lookups\n\n"
            "Files changed:\n"
            "- lib/commit_msg.py:\n"
            "  Guards repo_root resolution with safe_getcwd and safe_abspath.\n"
            "  Prevents ENOENT crashes when CWD is deleted.\n\n"
            "Issue: #328\n"
        )

      with _deleted_cwd():
        script = commit_msg_mod.CommitMsgScript()
        self.assertEqual(script.repo_root, "")

        script.parse_args(["--repo-root", ws_dir, "validate", "--file", msg_file])
        self.assertEqual(script.repo_root, os.path.normpath(ws_dir))

        script.parse_args(["validate", "--file", msg_file])
        self.assertEqual(script.repo_root, "")

        out = io.StringIO()
        err = io.StringIO()
        rc = commit_msg_mod.CommitMsgScript().execute(
            args=[
                "--repo-root",
                ws_dir,
                "validate",
                "--remote",
                "--ticket",
                "GH-328",
                "--file",
                msg_file,
            ],
            stdout=out,
            stderr=err,
        )
        self.assertEqual(rc, 0, f"stdout={out.getvalue()} stderr={err.getvalue()}")

  def test_mcp_resolve_confined_state_dir_relative_workspace_under_deleted_cwd(self):
    """NBO-2: Verifies _resolve_confined_state_dir tolerates relative workspaceDir under deleted CWD."""
    with _deleted_cwd():
      resolved_default, err_default = mcp_server_mod._resolve_confined_state_dir(
          "relative_ws", None
      )
      self.assertIsNone(err_default)
      self.assertEqual(
          resolved_default, os.path.join("relative_ws", ".workflow", "state")
      )

      resolved_rel, err_rel = mcp_server_mod._resolve_confined_state_dir(
          "relative_ws", ".workflow/state"
      )
      self.assertIsNone(err_rel)
      self.assertEqual(
          resolved_rel, os.path.join("relative_ws", ".workflow", "state")
      )

      escaped_rel, err_escape = mcp_server_mod._resolve_confined_state_dir(
          "relative_ws", "../escape"
      )
      self.assertIsNone(escaped_rel)
      self.assertIsNotNone(err_escape)
      self.assertIn("BLOCKED", err_escape)

      escaped_abs, err_abs = mcp_server_mod._resolve_confined_state_dir(
          "relative_ws", "/etc/passwd"
      )
      self.assertIsNone(escaped_abs)
      self.assertIsNotNone(err_abs)
      self.assertIn("BLOCKED", err_abs)

  def test_poll_ci_status_binds_ws_when_context_workspace_dir_is_none_under_deleted_cwd(self):
    """NBO-3: Verifies poll_ci_status binds ws via replace() when context.workspace_dir is None."""
    with tempfile.TemporaryDirectory() as ws_dir:
      ctx = agent_paths_mod.PathContext(
          workspace_dir=None, plugin_root=REPO_ROOT
      )
      with _deleted_cwd():
        with mock.patch.object(
            ci_polling_mod,
            "resolve_workflow_config",
            wraps=config_mod.resolve_workflow_config,
        ) as wrapped_cfg:
          res = ci_polling_mod.poll_ci_status(
              0, ws_dir, hosting_mode="pure_git", context=ctx
          )
      self.assertEqual(res["mode"], "pure_git")
      self.assertEqual(res["ciStatus"], "SKIPPED")
      wrapped_cfg.assert_called_once_with(
          context=agent_paths_mod.PathContext(
              workspace_dir=ws_dir, plugin_root=REPO_ROOT
          )
      )

  def test_workspace_mutex_acquire_and_release_workspace_under_deleted_cwd(self):
    """NBO-4: Verifies WorkspaceMutex.acquire and release_workspace tolerate empty/relative paths under deleted CWD."""
    with tempfile.TemporaryDirectory() as tmp_root:
      lock_dir = os.path.join(tmp_root, "locks")
      os.makedirs(lock_dir, exist_ok=True)

      with _deleted_cwd():
        mutex = workspace_mutex_mod.WorkspaceMutex(
            repo_root=None, lock_dir=lock_dir
        )
        self.assertEqual(mutex.repo_root, "")

        acq_empty = mutex.acquire("GH-328", branch_name="fix/328-safe-cwd")
        self.assertTrue(acq_empty["acquired"])
        self.assertEqual(acq_empty["session"]["workspace_path"], "")

        released_empty = mutex.release_workspace()
        self.assertEqual(released_empty, [])
        self.assertTrue(mutex.release("GH-328"))

        acq_rel = mutex.acquire(
            "GH-329", workspace_path="rel_ws", branch_name="feat/rel"
        )
        self.assertTrue(acq_rel["acquired"])
        self.assertEqual(acq_rel["session"]["workspace_path"], "rel_ws")

        acq_reentrant = mutex.acquire(
            "GH-329", workspace_path="rel_ws", branch_name="feat/rel"
        )
        self.assertTrue(acq_reentrant["acquired"])
        self.assertTrue(acq_reentrant.get("reentrant"))

        released_rel = mutex.release_workspace(workspace_path="rel_ws")
        self.assertEqual(released_rel, ["GH-329"])


if __name__ == "__main__":
  unittest.main()
