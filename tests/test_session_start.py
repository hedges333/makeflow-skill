#!/usr/bin/env python3
"""Hermetic unit tests for SessionStartScript and scripts/session_start.py wrapper (GH-203)."""

import ast
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.base_script import BaseScript
from lib.config import (
    find_state_dir,
    get_workflow_config,
    is_plugin_dir,
    is_ticket_active,
    load_default_config,
)
from lib.session_start import (
    SessionStartScript,
    main,
    set_dynamic_terminal_title,
)
from lib.ticket_state import TICKET_JSON_FILENAME
from lib.workflow_gates import refresh_state_makefile_if_ignored


_LIB_SESSION_START_PATH = os.path.join(_REPO_ROOT, "lib", "session_start.py")
_SCRIPT_SESSION_START_PATH = os.path.join(
    _REPO_ROOT, "scripts", "session_start.py"
)


def _clean_env(fake_home: str, **overrides: str) -> dict:
  """Builds an isolated environment dictionary without host Makeflow variables."""
  env = {
      key: val
      for key, val in os.environ.items()
      if not key.startswith("MAKEFILE_WORKFLOW_")
  }
  env["HOME"] = fake_home
  env["XDG_CONFIG_HOME"] = os.path.join(fake_home, ".config")
  env.update(overrides)
  return env


def _load_script_module():
  """Loads scripts/session_start.py dynamically to inspect wrapper exports."""
  spec = importlib.util.spec_from_file_location(
      "session_start_wrapper_test", _SCRIPT_SESSION_START_PATH
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class _ExplodingStdin:
  """Sentinel stream that fails if read unexpectedly (for example during --help)."""

  def read(self, *_args, **_kwargs):
    raise AssertionError("stdin.read() must not be called for --help")


class TestSessionStartScript(unittest.TestCase):
  """Hermetic in-memory tests for lib.session_start.SessionStartScript (GH-203)."""

  def _execute_isolated(self, payload_text: str, args=None):
    """Runs SessionStartScript in memory with poisoned OS streams."""
    in_stream = io.StringIO(payload_text)
    out_stream = io.StringIO()
    err_stream = io.StringIO()
    orig_stdin = sys.stdin
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    try:
      sys.stdin = None
      sys.stdout = None
      sys.stderr = None
      code = SessionStartScript().execute(
          args=[] if args is None else args,
          stdout=out_stream,
          stderr=err_stream,
          stdin=in_stream,
      )
    finally:
      sys.stdin = orig_stdin
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr
    return code, out_stream.getvalue(), err_stream.getvalue()

  def test_subclasses_base_script(self):
    """Verifies SessionStartScript inherits from BaseScript (GH-203, SCAR-ARCH-05)."""
    self.assertTrue(issubclass(SessionStartScript, BaseScript))

  def test_active_ticket_and_config_restoration(self):
    """Verifies active state cards, config summary, and terminal title update in memory (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as ws_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("lib.terminal_title.set_terminal_title") as mock_title,
    ):
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
      ) as fh:
        fh.write("# GH-203\n")
      with open(
          os.path.join(state_dir, TICKET_JSON_FILENAME), "w", encoding="utf-8"
      ) as fh:
        json.dump({"ticket_id": "GH-203", "title": "Refactor"}, fh)
      with open(
          os.path.join(ws_dir, ".workflow", "config.json"),
          "w",
          encoding="utf-8",
      ) as fh:
        json.dump(
            {
                "models": {
                    "adversarial-critic": "critic-pro",
                    "tdd-worker": "tdd-fast",
                },
                "ci": {"quiescenceSeconds": 45, "pollIntervalSeconds": 10},
                "database": {
                    "prodDatabases": ["orders_live"],
                    "testDatabases": ["orders_test"],
                    "prodHosts": ["db.prod.internal"],
                },
            },
            fh,
        )

      code, out_text, err_text = self._execute_isolated(
          json.dumps({"workspacePaths": [ws_dir]})
      )

      self.assertEqual(code, 0)
      self.assertEqual(err_text, "")
      data = json.loads(out_text)
      msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
      self.assertIn("2 state card(s)", msg)
      self.assertIn(state_dir, msg)
      self.assertIn("critic=critic-pro", msg)
      self.assertIn("tdd=tdd-fast", msg)
      self.assertIn("ci.quiescence=45s", msg)
      self.assertIn("ci.poll=10s", msg)
      self.assertIn("prodDatabases=['orders_live']", msg)
      self.assertIn("testDatabases=['orders_test']", msg)
      self.assertIn("prodHosts=['db.prod.internal']", msg)
      mock_title.assert_called_once_with(repo_root=ws_dir)

  def test_parked_session_injects_local_resume_note_without_network(self):
    """Reads 70_parking_state.json locally and injects PARKED status note without subprocess calls (GH-47)."""
    from lib.parking_state import ParkingState, ParkingStateStore

    with (
        tempfile.TemporaryDirectory() as ws_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("lib.terminal_title.set_terminal_title"),
        mock.patch(
            "lib.session_start.refresh_state_makefile_if_ignored",
            return_value=False,
        ),
        mock.patch("subprocess.run") as mock_subp,
    ):
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "70_bot_triage.md"), "w", encoding="utf-8"
      ) as fh:
        fh.write("# Target 70\n")
      ParkingStateStore(state_dir).save(
          ParkingState(
              parked=True,
              pr_number=47,
              head_branch="feat/47-parked",
              reason="Awaiting peer review approvals: 1/2",
              ci_status="SUCCESS",
              approvals_count=1,
              required_approvals=2,
          )
      )

      code, out_text, err_text = self._execute_isolated(
          json.dumps({"workspacePaths": [ws_dir]})
      )
      self.assertEqual(code, 0)
      self.assertEqual(err_text, "")
      mock_subp.assert_not_called()
      data = json.loads(out_text)
      msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
      self.assertIn("PARKED (Awaiting peer review approvals: 1/2)", msg)
      self.assertIn("PR #47", msg)
      self.assertIn("resume-parked", msg)

  def test_clean_workspace_and_complete_state_report_ready_for_intake(self):
    """Verifies empty and COMPLETE workspaces report clean Target 10 readiness (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as ws_clean,
        tempfile.TemporaryDirectory() as ws_complete,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("lib.terminal_title.set_terminal_title") as mock_title,
    ):
      complete_state = os.path.join(ws_complete, ".workflow", "state")
      os.makedirs(complete_state)
      with open(
          os.path.join(complete_state, "99_next_action.md"),
          "w",
          encoding="utf-8",
      ) as fh:
        fh.write("# Target 90 Finished: COMPLETE\n")

      for ws_path in (ws_clean, ws_complete):
        with self.subTest(workspace=ws_path):
          code, out_text, err_text = self._execute_isolated(
              json.dumps({"workspacePaths": [ws_path]})
          )
          self.assertEqual(code, 0)
          self.assertEqual(err_text, "")
          data = json.loads(out_text)
          msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
          self.assertIn(
              "Makefile Workflow: Workspace is clean and ready for Target 10 intake.",
              msg,
          )
      mock_title.assert_not_called()

  def test_empty_stdin_and_non_dict_json_payloads_default_safely(self):
    """Verifies blank stdin and non-dict JSON payloads coerce to empty dicts (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as work_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("os.getcwd", return_value=work_dir),
    ):
      for raw_input in ("", "   \n", "[]", '"scalar"', "42"):
        with self.subTest(raw_input=raw_input):
          code, out_text, err_text = self._execute_isolated(raw_input)
          self.assertEqual(code, 0)
          self.assertEqual(err_text, "")
          data = json.loads(out_text)
          msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
          self.assertIn("Workspace is clean and ready for Target 10 intake.", msg)

  def test_fail_open_on_invalid_json_stdin(self):
    """Verifies malformed JSON stdin fails open with {} on stdout and exit 0 (GH-203, SCAR-TEST-24)."""
    code, out_text, err_text = self._execute_isolated("{not valid json")
    self.assertEqual(code, 0)
    self.assertEqual(err_text, "")
    self.assertEqual(json.loads(out_text), {})

  def test_fail_open_on_unexpected_runtime_exception(self):
    """Verifies unexpected runtime failures fail open with {} on stdout and exit 0 (GH-203, SCAR-TEST-24)."""
    with mock.patch(
        "lib.session_start.find_state_dir",
        side_effect=RuntimeError("Simulated failure"),
    ):
      code, out_text, err_text = self._execute_isolated(
          json.dumps({"workspacePaths": ["/fake/repo"]})
      )
    self.assertEqual(code, 0)
    self.assertEqual(err_text, "")
    self.assertEqual(json.loads(out_text), {})

  def test_non_list_workspace_paths_coerced_safely(self):
    """Verifies non-list workspacePaths values coerce to [] and fall back to cwd without crashing (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as work_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("os.getcwd", return_value=work_dir),
        mock.patch("lib.terminal_title.set_terminal_title"),
    ):
      state_dir = os.path.join(work_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
      ) as fh:
        fh.write("# GH-203\n")

      for bad_workspace_paths in ("not-a-list", 123, {"path": work_dir}, True):
        with self.subTest(workspacePaths=bad_workspace_paths):
          code, out_text, err_text = self._execute_isolated(
              json.dumps({"workspacePaths": bad_workspace_paths})
          )
          self.assertEqual(code, 0)
          self.assertEqual(err_text, "")
          data = json.loads(out_text)
          msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
          self.assertIn("1 state card(s)", msg)
          self.assertIn(state_dir, msg)

  def test_non_string_artifact_directory_path_coerced_safely(self):
    """Verifies non-string artifactDirectoryPath values coerce to empty string without crashing (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as work_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("os.getcwd", return_value=work_dir),
    ):
      for bad_artifact_dir in (123, ["x"], {"dir": work_dir}, None):
        with self.subTest(artifactDirectoryPath=bad_artifact_dir):
          code, out_text, err_text = self._execute_isolated(
              json.dumps({"artifactDirectoryPath": bad_artifact_dir})
          )
          self.assertEqual(code, 0)
          self.assertEqual(err_text, "")
          data = json.loads(out_text)
          msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
          self.assertIn(
              "Workspace is clean and ready for Target 10 intake.", msg
          )

  def test_valid_artifact_directory_path_fallback_discovers_active_state(self):
    """Verifies artifactDirectoryPath fallback discovers active state cards in <artifact_dir>/state (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as work_dir,
        tempfile.TemporaryDirectory() as artifact_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch("os.getcwd", return_value=work_dir),
        mock.patch("lib.terminal_title.set_terminal_title") as mock_title,
    ):
      artifact_state_dir = os.path.join(artifact_dir, "state")
      os.makedirs(artifact_state_dir)
      with open(
          os.path.join(artifact_state_dir, "10_ticket.md"),
          "w",
          encoding="utf-8",
      ) as fh:
        fh.write("# GH-203\n")
      with open(
          os.path.join(artifact_state_dir, TICKET_JSON_FILENAME),
          "w",
          encoding="utf-8",
      ) as fh:
        json.dump({"ticket_id": "GH-203", "title": "Artifact fallback"}, fh)

      code, out_text, err_text = self._execute_isolated(
          json.dumps(
              {"workspacePaths": [], "artifactDirectoryPath": artifact_dir}
          )
      )

      self.assertEqual(code, 0)
      self.assertEqual(err_text, "")
      data = json.loads(out_text)
      msg = data["injectSteps"][0]["systemMessage"]["systemMessage"]
      self.assertIn("2 state card(s)", msg)
      self.assertIn(artifact_state_dir, msg)
      mock_title.assert_called_once_with(repo_root=work_dir)

  def test_refresh_state_makefile_invoked_for_workspace_and_non_plugin_cwd(self):
    """Verifies refresh_state_makefile_if_ignored runs for workspace roots or non-plugin cwd (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as ws_a,
        tempfile.TemporaryDirectory() as ws_b,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
    ):
      with mock.patch(
          "lib.session_start.refresh_state_makefile_if_ignored"
      ) as mock_refresh:
        code, _out, err = self._execute_isolated(
            json.dumps({"workspacePaths": [ws_a, ws_b, "", 123]})
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(
            mock_refresh.call_args_list, [mock.call(ws_a), mock.call(ws_b)]
        )

      # Empty workspacePaths falls back to cwd when cwd is not a plugin dir
      with (
          mock.patch("os.getcwd", return_value=ws_a),
          mock.patch("lib.session_start.is_plugin_dir", return_value=False),
          mock.patch(
              "lib.session_start.refresh_state_makefile_if_ignored"
          ) as mock_refresh_cwd,
      ):
        code, _out, _err = self._execute_isolated("{}")
        self.assertEqual(code, 0)
        mock_refresh_cwd.assert_called_once_with(ws_a)

      # Empty workspacePaths skips refresh when cwd is a plugin dir
      with (
          mock.patch("os.getcwd", return_value=ws_a),
          mock.patch("lib.session_start.is_plugin_dir", return_value=True),
          mock.patch(
              "lib.session_start.refresh_state_makefile_if_ignored"
          ) as mock_refresh_plugin,
      ):
        code, _out, _err = self._execute_isolated("{}")
        self.assertEqual(code, 0)
        mock_refresh_plugin.assert_not_called()

  def test_help_flags_exit_zero_without_reading_stdin(self):
    """Verifies --help and -h print usage to stdout and exit 0 without blocking on stdin (GH-203)."""
    orig_stdin, orig_stdout, orig_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
      sys.stdin = None
      sys.stdout = None
      sys.stderr = None
      for flag in ("--help", "-h"):
        with self.subTest(flag=flag):
          out_stream = io.StringIO()
          err_stream = io.StringIO()
          code = SessionStartScript().execute(
              args=[flag],
              stdout=out_stream,
              stderr=err_stream,
              stdin=_ExplodingStdin(),
          )
          self.assertEqual(code, 0)
          self.assertEqual(err_stream.getvalue(), "")
          self.assertIn("usage:", out_stream.getvalue().lower())
          self.assertIn("session_start.py", out_stream.getvalue())
    finally:
      sys.stdin = orig_stdin
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr

  def test_unknown_flag_returns_nonzero_with_error_on_stderr(self):
    """Verifies unknown CLI flags fail cleanly with exit 1 and Error: on stderr (GH-203)."""
    code, out_text, err_text = self._execute_isolated(
        "{}", args=["--unknown-flag-xyz"]
    )
    self.assertEqual(code, 1)
    self.assertEqual(out_text, "")
    self.assertIn("Error:", err_text)


class TestSessionStartHelpersAndMain(unittest.TestCase):
  """Unit tests for set_dynamic_terminal_title and main() compatibility entrypoint (GH-203)."""

  def test_set_dynamic_terminal_title_catches_only_import_and_os_errors(self):
    """Verifies set_dynamic_terminal_title swallows ImportError/OSError and re-raises others (GH-203)."""
    for expected_exc in (
        ImportError("missing terminal_title"),
        OSError("tty unavailable"),
    ):
      with mock.patch(
          "lib.terminal_title.set_terminal_title", side_effect=expected_exc
      ):
        set_dynamic_terminal_title("/fake/repo")

    for unexpected_exc in (
        TypeError("unexpected type error"),
        RuntimeError("unexpected runtime error"),
    ):
      with mock.patch(
          "lib.terminal_title.set_terminal_title", side_effect=unexpected_exc
      ):
        with self.assertRaises(type(unexpected_exc)):
          set_dynamic_terminal_title("/fake/repo")

  def test_main_defaults_args_to_empty_list_ignoring_sys_argv(self):
    """Verifies main() ignores unrelated sys.argv flags when args is None (GH-203)."""
    with (
        tempfile.TemporaryDirectory() as ws_dir,
        tempfile.TemporaryDirectory() as fake_home,
        mock.patch.dict(os.environ, _clean_env(fake_home), clear=True),
        mock.patch.object(sys, "argv", ["unittest", "-v", "--not-a-hook-flag"]),
    ):
      out_stream = io.StringIO()
      err_stream = io.StringIO()
      in_stream = io.StringIO(json.dumps({"workspacePaths": [ws_dir]}))
      code = main(stdout=out_stream, stderr=err_stream, stdin=in_stream)
      self.assertEqual(code, 0)
      self.assertEqual(err_stream.getvalue(), "")
      data = json.loads(out_stream.getvalue())
      self.assertIn("injectSteps", data)

  def test_main_forwards_explicit_args_and_streams(self):
    """Verifies main() forwards explicit args and injected streams cleanly (GH-203)."""
    out_stream = io.StringIO()
    err_stream = io.StringIO()
    code = main(
        args=["--help"],
        stdout=out_stream,
        stderr=err_stream,
        stdin=_ExplodingStdin(),
    )
    self.assertEqual(code, 0)
    self.assertEqual(err_stream.getvalue(), "")
    self.assertIn("session_start.py", out_stream.getvalue())


class TestSessionStartWrapperInvariants(unittest.TestCase):
  """Verifies SCAR-ARCH-05 layer separation and SCAR-GENUI-0008 re-export parity (GH-203)."""

  def test_lib_module_has_no_shebang(self):
    """Verifies lib/session_start.py contains no executable shebang header (GH-203, SCAR-ARCH-05)."""
    with open(_LIB_SESSION_START_PATH, "r", encoding="utf-8") as fh:
      first_line = fh.readline()
    self.assertFalse(first_line.startswith("#!"))

  def test_scripts_wrapper_is_thin_and_delegates_to_base_script(self):
    """Verifies scripts/session_start.py is a thin CLI wrapper without local business logic (GH-203, SCAR-ARCH-05)."""
    with open(_SCRIPT_SESSION_START_PATH, "r", encoding="utf-8") as fh:
      source = fh.read()

    self.assertTrue(source.startswith("#!/usr/bin/env python3"))
    self.assertIn('if __name__ == "__main__":', source)
    self.assertIn("sys.exit(SessionStartScript().execute())", source)

    tree = ast.parse(source, filename=_SCRIPT_SESSION_START_PATH)
    defined_names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    self.assertEqual(
        defined_names,
        [],
        f"Thin wrapper scripts/session_start.py must not define business logic locally: {defined_names}",
    )

  def test_scripts_wrapper_reexport_bijection(self):
    """Verifies scripts/session_start.py __all__ bijectively exports every planned symbol (GH-203, SCAR-GENUI-0008)."""
    wrapper = _load_script_module()
    expected_exports = {
        "SessionStartScript": SessionStartScript,
        "TICKET_JSON_FILENAME": TICKET_JSON_FILENAME,
        "find_state_dir": find_state_dir,
        "get_workflow_config": get_workflow_config,
        "is_plugin_dir": is_plugin_dir,
        "is_ticket_active": is_ticket_active,
        "json": json,
        "load_default_config": load_default_config,
        "main": main,
        "refresh_state_makefile_if_ignored": refresh_state_makefile_if_ignored,
        "set_dynamic_terminal_title": set_dynamic_terminal_title,
    }
    self.assertEqual(set(wrapper.__all__), set(expected_exports.keys()))
    for name, expected_obj in expected_exports.items():
      with self.subTest(export=name):
        self.assertTrue(hasattr(wrapper, name))
        self.assertIs(getattr(wrapper, name), expected_obj)


if __name__ == "__main__":
  unittest.main()
