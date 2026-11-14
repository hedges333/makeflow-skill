#!/usr/bin/env python3
"""Hermetic unit tests for BaseScript lifecycle (SCAR-ARCH-05)."""

import ast
import io
import os
import sys
import unittest

# Ensure repo root is on sys.path for direct execution
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
_LIB_DIR = os.path.join(_REPO_ROOT, "lib")
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.base_script import (
    BaseScript,
    HelpExit,
    ReflexArgumentError,
    SilentArgumentParser,
)


class ConcreteSuccessScript(BaseScript):

  def __init__(self):
    super().__init__()
    self.log = []

  def parse_args(self, args=None):
    super().parse_args(args)
    self.log.append("parse_args")

  def setup(self):
    self.log.append("setup")

  def run(self):
    self.log.append("run")
    self.stdout.write("Operation complete\n")
    return 0

  def cleanup(self, exit_code: int):
    self.log.append(f"cleanup:{exit_code}")


class ConcreteCustomExitScript(BaseScript):

  def __init__(self, exit_code: int = 42):
    super().__init__()
    self.target_code = exit_code
    self.cleaned_with = None

  def run(self):
    return self.target_code

  def cleanup(self, exit_code: int):
    self.cleaned_with = exit_code


class ConcreteFailingRunScript(BaseScript):

  def __init__(self):
    super().__init__()
    self.cleaned_with = None

  def run(self):
    raise RuntimeError("Intentional failure in run()")

  def cleanup(self, exit_code: int):
    self.cleaned_with = exit_code


class ConcreteFailingSetupScript(BaseScript):

  def __init__(self):
    super().__init__()
    self.cleaned_with = None

  def setup(self):
    raise ValueError("Intentional failure in setup()")

  def cleanup(self, exit_code: int):
    self.cleaned_with = exit_code


class CustomExitError(Exception):
  exit_code = 7


class NonIntegerExitError(Exception):
  exit_code = "not-an-integer"


class NoneExitError(Exception):
  exit_code = None


class ConcreteCustomExceptionScript(BaseScript):

  def run(self):
    raise CustomExitError("Custom exception with exit code 7")


class ConcreteNonIntegerExceptionScript(BaseScript):

  def __init__(self, exc):
    super().__init__()
    self.exc = exc

  def run(self):
    raise self.exc


class TestBaseScript(unittest.TestCase):

  def test_default_lifecycle_execution(self):
    """Verifies that execute() runs parse_args, setup, run, and cleanup in order."""
    script = ConcreteSuccessScript()
    out = io.StringIO()
    err = io.StringIO()

    exit_code = script.execute(args=["--test"], stdout=out, stderr=err)

    self.assertEqual(exit_code, 0)
    self.assertEqual(
        script.log, ["parse_args", "setup", "run", "cleanup:0"]
    )
    self.assertEqual(out.getvalue(), "Operation complete\n")
    self.assertEqual(err.getvalue(), "")

  def test_stream_redirection_isolation(self):
    """Verifies output goes exclusively to injected streams without leaking to OS streams."""
    out = io.StringIO()
    err = io.StringIO()
    script = ConcreteSuccessScript()

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    try:
      # Poison OS streams to guarantee neither is touched
      sys.stdout = None
      sys.stderr = None
      code = script.execute(args=[], stdout=out, stderr=err)
    finally:
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr

    self.assertEqual(code, 0)
    self.assertIn("Operation complete", out.getvalue())

  def test_custom_exit_code_handling(self):
    """Verifies custom exit codes are returned by execute() and passed to cleanup()."""
    script = ConcreteCustomExitScript(exit_code=42)
    out = io.StringIO()
    err = io.StringIO()

    exit_code = script.execute(args=[], stdout=out, stderr=err)

    self.assertEqual(exit_code, 42)
    self.assertEqual(script.cleaned_with, 42)

  def test_run_returning_none_defaults_to_zero(self):
    """Verifies run() returning None is treated as exit code 0."""
    class NoneScript(BaseScript):
      def __init__(self):
        super().__init__()
        self.cleaned_with = None

      def run(self):
        return None

      def cleanup(self, exit_code: int):
        self.cleaned_with = exit_code

    script = NoneScript()
    exit_code = script.execute(args=[], stdout=io.StringIO(), stderr=io.StringIO())
    self.assertEqual(exit_code, 0)
    self.assertEqual(script.cleaned_with, 0)

  def test_exception_in_run_trapped(self):
    """Verifies exceptions in run() are trapped, written to stderr, and cleanup is executed."""
    script = ConcreteFailingRunScript()
    out = io.StringIO()
    err = io.StringIO()

    exit_code = script.execute(args=[], stdout=out, stderr=err)

    self.assertEqual(exit_code, 1)
    self.assertEqual(script.cleaned_with, 1)
    self.assertIn("Intentional failure in run()", err.getvalue())

  def test_exception_in_setup_trapped(self):
    """Verifies exceptions in setup() are trapped, written to stderr, and cleanup is executed."""
    script = ConcreteFailingSetupScript()
    out = io.StringIO()
    err = io.StringIO()

    exit_code = script.execute(args=[], stdout=out, stderr=err)

    self.assertEqual(exit_code, 1)
    self.assertEqual(script.cleaned_with, 1)
    self.assertIn("Intentional failure in setup()", err.getvalue())

  def test_custom_exception_exit_code(self):
    """Verifies exception with exit_code attribute is mapped correctly."""
    script = ConcreteCustomExceptionScript()
    out = io.StringIO()
    err = io.StringIO()

    exit_code = script.execute(args=[], stdout=out, stderr=err)

    self.assertEqual(exit_code, 7)
    self.assertIn("Custom exception with exit code 7", err.getvalue())

  def test_custom_exception_non_integer_exit_code(self):
    """Verifies non-integer exit_code attributes safely fall back to 1 as int."""
    for bad_exc in (
        NonIntegerExitError("String exit code"),
        NoneExitError("None exit code"),
    ):
      script = ConcreteNonIntegerExceptionScript(bad_exc)
      out = io.StringIO()
      err = io.StringIO()

      exit_code = script.execute(args=[], stdout=out, stderr=err)

      self.assertIsInstance(exit_code, int)
      self.assertEqual(exit_code, 1)

  def test_parse_args_custom_list(self):
    """Verifies custom arguments list is received by parse_args()."""
    class ArgScript(BaseScript):
      def parse_args(self, args=None):
        super().parse_args(args)
        self.parsed = args

      def run(self):
        return len(self.parsed)

    script = ArgScript()
    exit_code = script.execute(
        args=["alpha", "beta", "gamma"],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    self.assertEqual(exit_code, 3)
    self.assertEqual(script.parsed, ["alpha", "beta", "gamma"])

  def test_fallback_to_sys_argv(self):
    """Verifies execute() falls back to sys.argv[1:] when args is None."""
    class ArgScript(BaseScript):
      def run(self):
        return 0

    script = ArgScript()
    orig_argv = sys.argv
    try:
      sys.argv = ["script.py", "foo", "bar"]
      script.execute(stdout=io.StringIO(), stderr=io.StringIO())
      self.assertEqual(script.raw_args, ["foo", "bar"])
    finally:
      sys.argv = orig_argv


class TestThreeLayerArchitectureInvariants(unittest.TestCase):
  """Verifies GH-169 / SCAR-ARCH-05 three-layer separation of lib/, scripts/, and tests/."""

  def test_lib_package_exists_and_has_no_shebangs(self):
    """Verifies lib/__init__.py exists and no file in lib/*.py starts with a shebang (#!)."""
    self.assertTrue(os.path.isdir(_LIB_DIR), "lib/ directory must exist")
    self.assertTrue(
        os.path.isfile(os.path.join(_LIB_DIR, "__init__.py")),
        "lib/__init__.py must exist",
    )
    py_files = sorted(
        f for f in os.listdir(_LIB_DIR) if f.endswith(".py")
    )
    self.assertGreaterEqual(len(py_files), 17, "Expected all shared library modules in lib/")
    for fname in py_files:
      fpath = os.path.join(_LIB_DIR, fname)
      with open(fpath, "r", encoding="utf-8") as fh:
        first_line = fh.readline()
      self.assertFalse(
          first_line.startswith("#!"),
          f"Library module lib/{fname} must not have an executable shebang header",
      )

  def test_lib_base_script_exports_argument_parser_primitives(self):
    """Verifies lib.base_script exports BaseScript, ReflexArgumentError, and SilentArgumentParser."""
    self.assertTrue(issubclass(ReflexArgumentError, Exception))
    parser = SilentArgumentParser(prog="test_prog")
    with self.assertRaises(ReflexArgumentError) as ctx:
      parser.error("bad flag")
    self.assertIn("bad flag", str(ctx.exception))

  def test_scripts_dir_contains_only_thin_cli_entrypoints(self):
    """Verifies scripts/ has zero test_*.py files, zero pure libraries, and validate_scar_candidate.py."""
    script_files = sorted(
        f for f in os.listdir(_SCRIPTS_DIR) if f.endswith(".py")
    )
    for fname in script_files:
      self.assertFalse(
          fname.startswith("test_"),
          f"scripts/{fname} has a 'test_' prefix; tests belong in tests/ and CLI validators must not start with test_",
      )
    self.assertNotIn("base_script.py", script_files)
    self.assertNotIn("safe_copy.py", script_files)
    self.assertNotIn("test_scar_candidate.py", script_files)
    self.assertIn("validate_scar_candidate.py", script_files)

  def test_scripts_have_shebang_main_guard_and_zero_cross_script_imports(self):
    """Verifies every scripts/*.py file has a shebang, __main__ guard, and zero imports from other scripts."""
    script_files = sorted(
        f for f in os.listdir(_SCRIPTS_DIR) if f.endswith(".py")
    )
    script_module_names = {os.path.splitext(f)[0] for f in script_files}
    # Also forbid importing removed legacy script names directly as top-level modules
    forbidden_script_modules = script_module_names | {
        "base_script",
        "safe_copy",
        "test_scar_candidate",
    }

    for fname in script_files:
      fpath = os.path.join(_SCRIPTS_DIR, fname)
      with open(fpath, "r", encoding="utf-8") as fh:
        source = fh.read()
      self.assertTrue(
          source.startswith("#!/usr/bin/env python3"),
          f"scripts/{fname} must start with #!/usr/bin/env python3",
      )
      self.assertIn(
          'if __name__ == "__main__":',
          source,
          f"scripts/{fname} must have an if __name__ == '__main__': entrypoint",
      )
      tree = ast.parse(source, filename=fpath)
      for node in ast.walk(tree):
        if isinstance(node, ast.Import):
          for alias in node.names:
            top_mod = alias.name.split(".")[0]
            self.assertNotIn(
                top_mod,
                forbidden_script_modules,
                f"scripts/{fname} must not import peer script module '{alias.name}'; import from lib.* instead",
            )
            self.assertNotEqual(
                top_mod,
                "scripts",
                f"scripts/{fname} must not import from 'scripts.*'; import from lib.* instead",
            )
        elif isinstance(node, ast.ImportFrom):
          if node.module:
            top_mod = node.module.split(".")[0]
            self.assertNotIn(
                top_mod,
                forbidden_script_modules,
                f"scripts/{fname} must not import from peer script module '{node.module}'; import from lib.* instead",
            )
            self.assertNotEqual(
                top_mod,
                "scripts",
                f"scripts/{fname} must not import from 'scripts.*'; import from lib.* instead",
            )


class TestCliHelpAndSubcommandUsage(unittest.TestCase):
  """Verifies GH-170 --help and subcommand usage output across all 12 CLI BaseScript runners."""

  def test_help_exit_exception_contract(self):
    """Verifies HelpExit is exported from lib.base_script with exit_code == 0."""
    self.assertTrue(issubclass(HelpExit, Exception))
    self.assertEqual(HelpExit.exit_code, 0)
    exc = HelpExit("usage: test\n")
    self.assertEqual(exc.exit_code, 0)
    self.assertEqual(str(exc), "usage: test\n")

  def test_silent_argument_parser_buffers_help_and_raises_help_exit(self):
    """Verifies SilentArgumentParser raises HelpExit on root/subparser --help and ReflexArgumentError on errors."""
    parser = SilentArgumentParser(
        prog="demo.py",
        description="Demo tool description.",
    )
    parser.add_argument("--flag", help="Demo root flag.")
    subparsers = parser.add_subparsers(
        dest="subcmd", parser_class=SilentArgumentParser
    )
    sub = subparsers.add_parser("run-job", description="Run job subcommand.")
    sub.add_argument("--target", help="Target path for run-job.")

    with self.assertRaises(HelpExit) as root_ctx:
      parser.parse_args(["--help"])
    self.assertIn("usage: demo.py", str(root_ctx.exception))
    self.assertIn("Demo tool description.", str(root_ctx.exception))
    self.assertIn("--flag", str(root_ctx.exception))

    with self.assertRaises(HelpExit) as sub_ctx:
      parser.parse_args(["run-job", "--help"])
    self.assertIn("usage: demo.py run-job", str(sub_ctx.exception))
    self.assertIn("--target", str(sub_ctx.exception))

    with self.assertRaises(ReflexArgumentError) as err_ctx:
      parser.parse_args(["--unknown-option"])
    self.assertEqual(err_ctx.exception.exit_code, 1)

    with self.assertRaises(ReflexArgumentError) as exit_ctx:
      parser.exit(status=2, message="custom error exit")
    self.assertEqual(exit_ctx.exception.exit_code, 1)
    self.assertIn("custom error exit", str(exit_ctx.exception))

  def test_base_script_execute_handles_help_exit_hermetically(self):
    """Verifies BaseScript.execute(['--help']) skips setup/run, calls cleanup(0), and writes to stdout."""
    class HelpDemoScript(BaseScript):
      def __init__(self):
        super().__init__()
        self.events = []

      def parse_args(self, args=None):
        super().parse_args(args)
        self.events.append("parse_args")
        parser = SilentArgumentParser(prog="help_demo.py", description="Help demo.")
        parser.add_argument("--item", help="Item option.")
        parser.parse_args(self.raw_args)

      def setup(self):
        self.events.append("setup")

      def run(self):
        self.events.append("run")
        return 0

      def cleanup(self, exit_code: int):
        self.events.append(f"cleanup:{exit_code}")

    script = HelpDemoScript()
    out = io.StringIO()
    err = io.StringIO()
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
      sys.stdout = None
      sys.stderr = None
      code = script.execute(["--help"], stdout=out, stderr=err)
    finally:
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr

    self.assertEqual(code, 0)
    self.assertEqual(script.events, ["parse_args", "cleanup:0"])
    self.assertIn("usage: help_demo.py", out.getvalue())
    self.assertIn("--item", out.getvalue())
    self.assertEqual(err.getvalue(), "")

  def test_all_twelve_cli_scripts_root_help(self):
    """Verifies all 12 CLI BaseScript classes return 0 and print usage on --help and -h."""
    from lib.ast_scrub import AstScrubScript
    from lib.commit_msg import CommitMsgScript
    from lib.env_preflight import EnvPreflightScript
    from lib.reflex import EmitReflexScript
    from lib.scar_candidate import ScarCandidateTestScript
    from lib.scars_db import SyncSyntheticScarsScript
    from lib.signal_trap import SignalTrapScript
    from lib.static_check import StaticCheckScript
    from lib.tdd_guard import TddGuardScript
    from lib.terminal_title import TerminalTitleScript
    from lib.workflow_gates import WorkflowGatesScript
    from lib.workspace_mutex import WorkspaceMutexScript

    script_classes = [
        (CommitMsgScript, "commit_msg.py"),
        (WorkflowGatesScript, "workflow_gates.py"),
        (TddGuardScript, "tdd_guard.py"),
        (WorkspaceMutexScript, "workspace_mutex.py"),
        (TerminalTitleScript, "terminal_title.py"),
        (ScarCandidateTestScript, "validate_scar_candidate.py"),
        (StaticCheckScript, "static_check.py"),
        (EmitReflexScript, "emit_reflex.py"),
        (EnvPreflightScript, "env_preflight.py"),
        (AstScrubScript, "ast_scrub.py"),
        (SyncSyntheticScarsScript, "sync_synthetic_scars.py"),
        (SignalTrapScript, "signal_trap.py"),
    ]

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
      sys.stdout = None
      sys.stderr = None
      for cls, expected_prog in script_classes:
        for flag in ("--help", "-h"):
          with self.subTest(script=cls.__name__, flag=flag):
            out = io.StringIO()
            err = io.StringIO()
            code = cls().execute([flag], stdout=out, stderr=err)
            self.assertEqual(code, 0, f"{cls.__name__}({flag}) returned {code}, err={err.getvalue()}")
            self.assertEqual(err.getvalue(), "")
            help_text = out.getvalue()
            self.assertIn("usage:", help_text.lower())
            self.assertIn(expected_prog, help_text)
    finally:
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr

  def test_all_twenty_four_subcommands_help(self):
    """Verifies all 24 subcommands across the 5 multi-command CLI scripts support --help and -h."""
    from lib.commit_msg import CommitMsgScript
    from lib.tdd_guard import TddGuardScript
    from lib.terminal_title import TerminalTitleScript
    from lib.workflow_gates import WorkflowGatesScript
    from lib.workspace_mutex import WorkspaceMutexScript

    subcommand_matrix = [
        (CommitMsgScript, ["validate", "format"]),
        (
            WorkflowGatesScript,
            [
                "init-makefile",
                "plugin-files",
                "skill-budget",
                "intake",
                "release-intake",
                "release-lock",
                "approved-tree",
                "commit-msg",
                "terminal-title",
            ],
        ),
        (
            TddGuardScript,
            ["witness-red", "verify-green", "verify-citation"],
        ),
        (
            WorkspaceMutexScript,
            [
                "acquire",
                "release",
                "status",
                "check-remote",
                "check-dependencies",
                "claim-issue",
            ],
        ),
        (
            TerminalTitleScript,
            ["detect", "info", "set", "reset"],
        ),
    ]

    total_subcommands = sum(len(subs) for _, subs in subcommand_matrix)
    self.assertEqual(total_subcommands, 24)

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
      sys.stdout = None
      sys.stderr = None
      for cls, subcommands in subcommand_matrix:
        for sub in subcommands:
          for flag in ("--help", "-h"):
            with self.subTest(script=cls.__name__, subcommand=sub, flag=flag):
              out = io.StringIO()
              err = io.StringIO()
              code = cls().execute([sub, flag], stdout=out, stderr=err)
              self.assertEqual(code, 0, f"{cls.__name__} {sub} {flag} returned {code}, err={err.getvalue()}")
              self.assertEqual(err.getvalue(), "")
              help_text = out.getvalue()
              self.assertIn("usage:", help_text.lower())
              self.assertIn(sub, help_text)
              if cls is CommitMsgScript and sub == "format":
                self.assertIn("--file-change PATH DESC RELATION", help_text)
    finally:
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr

  def test_unknown_flag_falsification_across_twelve_cli_scripts(self):
    """Verifies passing --unknown-flag-xyz fails with non-zero exit code and Error: on stderr."""
    from lib.ast_scrub import AstScrubScript
    from lib.commit_msg import CommitMsgScript
    from lib.env_preflight import EnvPreflightScript
    from lib.reflex import EmitReflexScript
    from lib.scar_candidate import ScarCandidateTestScript
    from lib.scars_db import SyncSyntheticScarsScript
    from lib.signal_trap import SignalTrapScript
    from lib.static_check import StaticCheckScript
    from lib.tdd_guard import TddGuardScript
    from lib.terminal_title import TerminalTitleScript
    from lib.workflow_gates import WorkflowGatesScript
    from lib.workspace_mutex import WorkspaceMutexScript

    expected_exit_codes = [
        (CommitMsgScript, 1),
        (WorkflowGatesScript, 1),
        (TddGuardScript, 1),
        (WorkspaceMutexScript, 1),
        (TerminalTitleScript, 1),
        (ScarCandidateTestScript, 2),
        (StaticCheckScript, 1),
        (EmitReflexScript, 1),
        (EnvPreflightScript, 1),
        (AstScrubScript, 1),
        (SyncSyntheticScarsScript, 2),
        (SignalTrapScript, 1),
    ]

    for cls, expected_code in expected_exit_codes:
      with self.subTest(script=cls.__name__):
        out = io.StringIO()
        err = io.StringIO()
        code = cls().execute(["--unknown-flag-xyz"], stdout=out, stderr=err)
        self.assertEqual(code, expected_code)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Error:", err.getvalue())

  def test_signal_trap_script_rejects_unexpected_positional_args(self):
    """Verifies SignalTrapScript strictly accepts only its documented flags and rejects positional args."""
    from lib.signal_trap import SignalTrapScript

    script = SignalTrapScript()
    script.run = lambda: 99
    out = io.StringIO()
    err = io.StringIO()
    code = script.execute(
        args=["unexpected-positional-arg"], stdout=out, stderr=err
    )
    self.assertEqual(code, 1)
    self.assertEqual(out.getvalue(), "")
    self.assertIn("Error:", err.getvalue())


if __name__ == "__main__":
  unittest.main()
