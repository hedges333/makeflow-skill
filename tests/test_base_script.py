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

from lib.base_script import BaseScript, ReflexArgumentError, SilentArgumentParser


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


if __name__ == "__main__":
  unittest.main()
