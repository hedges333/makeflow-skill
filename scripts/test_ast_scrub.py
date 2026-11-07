#!/usr/bin/env python3
"""Unit tests for the structural AST scrubber gate (SCAR-AST-01, GH-56).

Verifies AST scrubbing rules for:
1. Silent exception handlers (bare except:, broad except Exception: with pass/ellipsis/continue).
2. Trivial fallback returns (broad handlers returning None, {}, [], "", 0, False without logging/re-raising).
3. Legitimate exception handlers (specific exceptions or broad handlers that log/re-raise).
4. Production mock leakage (importing unittest.mock, MagicMock, pytest, mock in production code).
5. Exemption of test files from mock leakage.
6. BaseScript lifecycle runner in-memory stream injection and exit codes.
7. Subprocess CLI invocation and Vector 4 Reflex output format.
8. JSON serialization mode (--json).
9. Execution speed benchmark asserting sub-20ms execution on repo files.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)

# Note: In the Red phase, ast_scrub does not exist yet.
try:
  import ast_scrub
  from ast_scrub import (
      AstScrubScript,
      AstScrubberVisitor,
      Violation,
      scrub_file,
      scrub_source,
  )
except ImportError:
  ast_scrub = None
  AstScrubScript = None
  AstScrubberVisitor = None
  Violation = None
  scrub_file = None
  scrub_source = None


class TestAstScrubRedPhase(unittest.TestCase):
  """Ensures the module under test exists and can be imported."""

  def test_ast_scrub_module_present(self):
    self.assertIsNotNone(
        ast_scrub,
        "scripts/ast_scrub.py must exist and be importable (Red Phase check).",
    )


class TestAstScrubberSilentExceptions(unittest.TestCase):
  """Tests detection of silent bare and broad exception handlers."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_bare_except_pass_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        do_something()\n"
        "    except:\n"
        "        pass\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("SILENT_EXCEPTION_HANDLER", codes)

  def test_bare_except_ellipsis_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        do_something()\n"
        "    except:\n"
        "        ...\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("SILENT_EXCEPTION_HANDLER", codes)

  def test_bare_except_continue_is_rejected(self):
    code = (
        "for x in items:\n"
        "    try:\n"
        "        process(x)\n"
        "    except:\n"
        "        continue\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("SILENT_EXCEPTION_HANDLER", codes)

  def test_broad_except_exception_pass_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("SILENT_EXCEPTION_HANDLER", codes)

  def test_broad_except_base_exception_pass_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        risky()\n"
        "    except BaseException:\n"
        "        pass\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("SILENT_EXCEPTION_HANDLER", codes)

  def test_docstring_and_pass_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        '        """Ignore all errors silently."""\n'
        "        pass\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("SILENT_EXCEPTION_HANDLER", codes)


class TestAstScrubberTrivialFallbackReturns(unittest.TestCase):
  """Tests detection of trivial fallback returns in broad handlers."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_broad_except_return_none_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return fetch()\n"
        "    except Exception:\n"
        "        return None\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_bare_except_return_none_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return fetch()\n"
        "    except:\n"
        "        return None\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_broad_except_return_empty_dict_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return load_data()\n"
        "    except Exception:\n"
        "        return {}\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_broad_except_return_empty_list_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return load_items()\n"
        "    except Exception:\n"
        "        return []\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_broad_except_return_empty_str_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return get_title()\n"
        "    except Exception:\n"
        '        return ""\n'
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_broad_except_return_zero_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return count_items()\n"
        "    except Exception:\n"
        "        return 0\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_broad_except_return_false_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return check_valid()\n"
        "    except Exception:\n"
        "        return False\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)

  def test_broad_except_return_empty_tuple_is_rejected(self):
    code = (
        "def bad():\n"
        "    try:\n"
        "        return parse_pair()\n"
        "    except Exception:\n"
        "        return ()\n"
    )
    violations = scrub_source(code, filename="prod.py")
    codes = [v.code for v in violations]
    self.assertIn("TRIVIAL_FALLBACK_RETURN", codes)


class TestAstScrubberLegitimateHandlers(unittest.TestCase):
  """Tests that legitimate exception handlers are accepted."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_specific_exception_return_none_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        return cache[key]\n"
        "    except (KeyError, ValueError):\n"
        "        return None\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])

  def test_specific_filenotfound_return_empty_dict_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        with open('config.json') as f:\n"
        "            return json.load(f)\n"
        "    except (FileNotFoundError, json.JSONDecodeError):\n"
        "        return {}\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])

  def test_specific_exception_return_empty_tuple_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        return cache[key]\n"
        "    except KeyError:\n"
        "        return ()\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])

  def test_broad_handler_that_reraises_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        step()\n"
        "    except Exception as exc:\n"
        "        clean_up()\n"
        "        raise\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])

  def test_broad_handler_that_logs_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        step()\n"
        "    except Exception as exc:\n"
        '        logger.error(f"Failed: {exc}")\n'
        "        return None\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])

  def test_broad_handler_that_prints_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        step()\n"
        "    except Exception as exc:\n"
        '        print(f"Error: {exc}")\n'
        "        return False\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])

  def test_broad_handler_that_records_error_is_allowed(self):
    code = (
        "def good():\n"
        "    try:\n"
        "        step()\n"
        "    except Exception as exc:\n"
        "        errors.append(str(exc))\n"
        "        return []\n"
    )
    violations = scrub_source(code, filename="prod.py")
    self.assertEqual(violations, [])


class TestAstScrubberMockLeakage(unittest.TestCase):
  """Tests detection of mock library imports in production code and exemption of test files."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_import_unittest_mock_in_prod_is_rejected(self):
    code = "import unittest.mock\n"
    violations = scrub_source(code, filename="scripts/adapter.py")
    codes = [v.code for v in violations]
    self.assertIn("PRODUCTION_MOCK_LEAKAGE", codes)

  def test_from_unittest_mock_magicmock_in_prod_is_rejected(self):
    code = "from unittest.mock import MagicMock, patch\n"
    violations = scrub_source(code, filename="scripts/worker.py")
    codes = [v.code for v in violations]
    self.assertIn("PRODUCTION_MOCK_LEAKAGE", codes)

  def test_import_mock_in_prod_is_rejected(self):
    code = "import mock\n"
    violations = scrub_source(code, filename="scripts/safe_copy.py")
    codes = [v.code for v in violations]
    self.assertIn("PRODUCTION_MOCK_LEAKAGE", codes)

  def test_import_pytest_in_prod_is_rejected(self):
    code = "import pytest\n"
    violations = scrub_source(code, filename="scripts/pipeline.py")
    codes = [v.code for v in violations]
    self.assertIn("PRODUCTION_MOCK_LEAKAGE", codes)

  def test_mock_imports_allowed_in_test_file_prefix(self):
    code = "from unittest.mock import MagicMock, patch\nimport pytest\n"
    violations = scrub_source(code, filename="scripts/test_feature.py")
    self.assertEqual(violations, [])

  def test_mock_imports_allowed_in_test_file_suffix(self):
    code = "from unittest.mock import Mock\n"
    violations = scrub_source(code, filename="scripts/feature_test.py")
    self.assertEqual(violations, [])

  def test_mock_imports_allowed_in_tests_directory(self):
    code = "import unittest.mock\n"
    violations = scrub_source(code, filename="tests/unit/test_module.py")
    self.assertEqual(violations, [])


class TestAstScrubberCommentExemption(unittest.TestCase):
  """Tests that comment exemptions like # pylint: disable=broad-except or # noqa are honored."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_pylint_disable_broad_except_is_exempt(self):
    code = (
        "def safe_stderr():\n"
        "    try:\n"
        '        sys.stderr.write("msg")\n'
        "    except Exception:  # pylint: disable=broad-except\n"
        "        pass\n"
    )
    violations = scrub_source(code, filename="scripts/base_script.py")
    self.assertEqual(violations, [])

  def test_noqa_comment_is_exempt(self):
    code = (
        "def fallback():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:  # noqa\n"
        "        pass\n"
    )
    violations = scrub_source(code, filename="scripts/helper.py")
    self.assertEqual(violations, [])

  def test_ast_scrub_ignore_comment_is_exempt(self):
    code = (
        "def fallback():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:  # ast-scrub: ignore\n"
        "        return None\n"
    )
    violations = scrub_source(code, filename="scripts/helper.py")
    self.assertEqual(violations, [])


class TestAstScrubScriptLifecycle(unittest.TestCase):
  """Tests in-memory BaseScript lifecycle and stream injection."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_clean_file_returns_exit_zero(self):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("def clean():\n    return 42\n")
      clean_path = f.name
    try:
      out = io.StringIO()
      err = io.StringIO()
      script = AstScrubScript()
      code = script.execute(
          args=["--files", clean_path, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0)
      self.assertEqual(err.getvalue(), "")
    finally:
      if os.path.exists(clean_path):
        os.unlink(clean_path)

  def test_violation_returns_exit_code_two_and_reflex(self):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("def bad():\n    try:\n        run()\n    except:\n        pass\n")
      bad_path = f.name
    try:
      out = io.StringIO()
      err = io.StringIO()
      script = AstScrubScript()
      code = script.execute(
          args=["--files", bad_path, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      err_output = err.getvalue()
      self.assertIn("SCAR-ID: AST-01", err_output)
      self.assertIn("SILENT_EXCEPTION_HANDLER", err_output)
      self.assertIn("TRIGGER (THIS):", err_output)
      self.assertIn("REJECTED (TRAP):", err_output)
      self.assertIn("REFLEX (ACTION):", err_output)
    finally:
      if os.path.exists(bad_path):
        os.unlink(bad_path)

  def test_json_output_mode(self):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("def bad():\n    try:\n        run()\n    except Exception:\n        return None\n")
      bad_path = f.name
    try:
      out = io.StringIO()
      err = io.StringIO()
      script = AstScrubScript()
      code = script.execute(
          args=["--files", bad_path, "--strict", "--json"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      lines = err.getvalue().strip().splitlines()
      self.assertGreaterEqual(len(lines), 1)
      payload = json.loads(lines[0])
      self.assertEqual(payload["scar_id"], "SCAR-AST-01")
      self.assertEqual(payload["short_id"], "AST-01")
      self.assertEqual(payload["violation_code"], "TRIVIAL_FALLBACK_RETURN")
      self.assertEqual(payload["exit_code"], 2)
    finally:
      if os.path.exists(bad_path):
        os.unlink(bad_path)


class TestAstScrubSubprocessCli(unittest.TestCase):
  """Tests CLI execution via subprocess."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_cli_subprocess_exit_code_two(self):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("import unittest.mock\n")
      bad_path = f.name
    try:
      scrub_script = os.path.join(_SCRIPTS_DIR, "ast_scrub.py")
      proc = subprocess.run(
          [sys.executable, scrub_script, "--files", bad_path, "--strict"],
          capture_output=True,
          text=True,
          check=False,
          timeout=10,
      )
      self.assertEqual(proc.returncode, 2)
      self.assertIn("SCAR-ID: AST-01", proc.stderr)
      self.assertIn("PRODUCTION_MOCK_LEAKAGE", proc.stderr)
    finally:
      if os.path.exists(bad_path):
        os.unlink(bad_path)


class TestAstScrubPerformanceBenchmark(unittest.TestCase):
  """Benchmarks execution speed to assert sub-20ms execution on repo files."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_ast_scrub_execution_speed_under_twenty_milliseconds(self):
    targets = [
        os.path.join(_SCRIPTS_DIR, "safe_copy.py"),
        os.path.join(_SCRIPTS_DIR, "emit_reflex.py"),
        os.path.join(_SCRIPTS_DIR, "env_preflight.py"),
    ]
    existing = [t for t in targets if os.path.isfile(t)]
    self.assertGreaterEqual(len(existing), 2)

    start = time.perf_counter()
    for fpath in existing:
      violations = scrub_file(fpath)
      self.assertIsInstance(violations, list)
    elapsed = time.perf_counter() - start

    # Assert execution completed in under 20ms (< 0.020s)
    self.assertLess(
        elapsed,
        0.020,
        f"AST scrubbing took {elapsed * 1000:.2f} ms; must be under 20 ms.",
    )


if __name__ == "__main__":
  unittest.main()
