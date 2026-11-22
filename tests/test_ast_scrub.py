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
import re
import subprocess
import sys
import tempfile
import time
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
_LIB_DIR = os.path.join(_REPO_ROOT, "lib")
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

# Note: In the Red phase, ast_scrub does not exist yet.
try:
  import lib.ast_scrub as ast_scrub
  from lib.ast_scrub import (
      AstScrubScript,
      AstScrubberVisitor,
      Violation,
      collect_python_files,
      get_git_modified_files,
      scrub_file,
      scrub_source,
  )
except ImportError:
  ast_scrub = None
  AstScrubScript = None
  AstScrubberVisitor = None
  Violation = None
  collect_python_files = None
  get_git_modified_files = None
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
    with tempfile.TemporaryDirectory() as tmpdir:
      clean_path = os.path.join(tmpdir, "clean.py")
      with open(clean_path, "w", encoding="utf-8") as f:
        f.write("def clean():\n    return 42\n")
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

  def test_violation_returns_exit_code_two_and_reflex(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      bad_path = os.path.join(tmpdir, "bad.py")
      with open(bad_path, "w", encoding="utf-8") as f:
        f.write("def bad():\n    try:\n        run()\n    except:\n        pass\n")
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

  def test_json_output_mode(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      bad_path = os.path.join(tmpdir, "bad.py")
      with open(bad_path, "w", encoding="utf-8") as f:
        f.write("def bad():\n    try:\n        run()\n    except Exception:\n        return None\n")
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

  def test_default_scan_dirs_when_src_dir_and_files_omitted(self):
    self.assertEqual(
        getattr(ast_scrub, "DEFAULT_SCAN_DIRS", None),
        ("lib", "scripts", "mcp", "tests"),
    )
    with tempfile.TemporaryDirectory() as tmpdir:
      for subdir in ("lib", "scripts", "mcp", "tests"):
        dpath = os.path.join(tmpdir, subdir)
        os.makedirs(dpath, exist_ok=True)
        with open(os.path.join(dpath, f"mod_{subdir}.py"), "w", encoding="utf-8") as f:
          f.write("def ok() -> int:\n    return 1\n")
      out = io.StringIO()
      err = io.StringIO()
      code = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, err.getvalue())
      self.assertIn("scanned 4 files", out.getvalue())

      # Inject a violation into lib/mod_lib.py and verify default scan catches it
      with open(os.path.join(tmpdir, "lib", "mod_lib.py"), "w", encoding="utf-8") as f:
        f.write("def bad():\n    try:\n        run()\n    except Exception:\n        pass\n")
      out2 = io.StringIO()
      err2 = io.StringIO()
      code2 = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out2,
          stderr=err2,
      )
      self.assertEqual(code2, 2)
      self.assertIn("SILENT_EXCEPTION_HANDLER", err2.getvalue())


class TestAstScrubConsumerLayouts(unittest.TestCase):
  """Tests consumer-adaptive scanning, ignored tooling dirs, and Reference 14 snippet (GH-245)."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

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
        ["git", "config", "user.name", "Makeflow Test"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )

  @staticmethod
  def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
      f.write(content)

  def test_consumer_layout_src_app_and_root_without_mcp_non_git(self):
    """Non-Git consumer repo with src/, app/, and root .py (no mcp/) scans all 3 files."""
    with tempfile.TemporaryDirectory() as tmpdir:
      self._write_file(
          os.path.join(tmpdir, "src", "service.py"),
          "def ok() -> int:\n    return 1\n",
      )
      self._write_file(
          os.path.join(tmpdir, "app", "routes.py"),
          "def route() -> str:\n    return 'ok'\n",
      )
      self._write_file(
          os.path.join(tmpdir, "manage.py"),
          "def main() -> int:\n    return 0\n",
      )

      out = io.StringIO()
      err = io.StringIO()
      code = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, err.getvalue())
      self.assertIn("scanned 3 files", out.getvalue())

      # Violation inside src/ fails with exit 2
      self._write_file(
          os.path.join(tmpdir, "src", "service.py"),
          "def bad():\n    try:\n        run()\n    except Exception:\n        pass\n",
      )
      out_src = io.StringIO()
      err_src = io.StringIO()
      code_src = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out_src,
          stderr=err_src,
      )
      self.assertEqual(code_src, 2)
      self.assertIn("SILENT_EXCEPTION_HANDLER", err_src.getvalue())

      # Restore src/service.py and place violation in root manage.py
      self._write_file(
          os.path.join(tmpdir, "src", "service.py"),
          "def ok() -> int:\n    return 1\n",
      )
      self._write_file(
          os.path.join(tmpdir, "manage.py"),
          "def bad_root():\n    try:\n        run()\n    except Exception:\n        return None\n",
      )
      out_root = io.StringIO()
      err_root = io.StringIO()
      code_root = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out_root,
          stderr=err_root,
      )
      self.assertEqual(code_root, 2)
      self.assertIn("TRIVIAL_FALLBACK_RETURN", err_root.getvalue())

  def test_consumer_git_repo_detects_branch_src_and_modified_root_without_mcp(self):
    """Git consumer repo without mcp/ detects branch-committed src/ and root .py changes."""
    with tempfile.TemporaryDirectory() as tmpdir:
      self._init_git_repo(tmpdir)
      self._write_file(
          os.path.join(tmpdir, "src", "api.py"),
          "def ok() -> int:\n    return 1\n",
      )
      self._write_file(
          os.path.join(tmpdir, "app", "models.py"),
          "class Item:\n    pass\n",
      )
      self._write_file(
          os.path.join(tmpdir, "cli.py"),
          "def main() -> int:\n    return 0\n",
      )
      subprocess.run(
          ["git", "add", "."],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: initial consumer app"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/consumer-change"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      self._write_file(
          os.path.join(tmpdir, "src", "api.py"),
          "def broken():\n    try:\n        call()\n    except:\n        pass\n",
      )
      subprocess.run(
          ["git", "add", "src/api.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "feat: break src/api.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      out = io.StringIO()
      err = io.StringIO()
      code = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn("SILENT_EXCEPTION_HANDLER", err.getvalue())

      # Fix src/api.py and modify root cli.py in working tree
      self._write_file(
          os.path.join(tmpdir, "src", "api.py"),
          "def ok() -> int:\n    return 2\n",
      )
      subprocess.run(
          ["git", "add", "src/api.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "fix: repair src/api.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      self._write_file(
          os.path.join(tmpdir, "cli.py"),
          "def bad_cli():\n    try:\n        run()\n    except Exception:\n        return {}\n",
      )

      out2 = io.StringIO()
      err2 = io.StringIO()
      code2 = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out2,
          stderr=err2,
      )
      self.assertEqual(code2, 2)
      self.assertIn("TRIVIAL_FALLBACK_RETURN", err2.getvalue())

  def test_collect_and_git_modified_skip_venv_workflow_and_cache_dirs(self):
    """Ignored tooling dirs (.venv, .workflow, caches) are skipped directly and in Git scans."""
    ignored_dirs = (
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".tmp",
        ".tox",
        ".nox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".workflow",
    )
    bad_py = "def bad():\n    try:\n        run()\n    except:\n        pass\n"

    with tempfile.TemporaryDirectory() as tmpdir:
      self._init_git_repo(tmpdir)
      clean_src = os.path.abspath(os.path.join(tmpdir, "src", "clean.py"))
      self._write_file(clean_src, "def clean() -> int:\n    return 1\n")
      subprocess.run(
          ["git", "add", "src/clean.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: seed clean src"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      for dname in ignored_dirs:
        if dname == ".git":
          self._write_file(os.path.join(tmpdir, ".git", "hooks_helper.py"), bad_py)
        else:
          self._write_file(os.path.join(tmpdir, dname, "nested", "bad.py"), bad_py)

      # Direct collect_python_files on root skips all ignored dirs
      self.assertEqual(collect_python_files(tmpdir), [clean_src])

      # Direct collect_python_files when ignored directory path itself is passed
      for direct_skip in (".venv", ".workflow", "node_modules", ".pytest_cache"):
        dpath = os.path.join(tmpdir, direct_skip)
        self.assertEqual(collect_python_files(dpath), [])
        self.assertEqual(collect_python_files(dpath + os.sep), [])

      # Force git status --porcelain to emit nested untracked paths inside .venv/.workflow
      self._write_file(os.path.join(tmpdir, ".venv", ".keep"), "")
      self._write_file(os.path.join(tmpdir, ".workflow", ".keep"), "")
      subprocess.run(
          ["git", "add", "-f", ".venv/.keep", ".workflow/.keep"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      self._write_file(os.path.join(tmpdir, "src", "clean.py"), "def clean() -> int:\n    return 2\n")

      git_targets = get_git_modified_files(tmpdir, tmpdir)
      self.assertEqual(git_targets, [clean_src])

      out = io.StringIO()
      err = io.StringIO()
      code = AstScrubScript().execute(
          args=["--repo-root", tmpdir, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, err.getvalue())
      self.assertIn("scanned 1 files", out.getvalue())

  def test_non_python_repo_returns_exit_zero(self):
    """Non-Python repositories (with or without .git) pass ast-lint cleanly with exit 0."""
    for with_git in (False, True):
      with self.subTest(with_git=with_git):
        with tempfile.TemporaryDirectory() as tmpdir:
          if with_git:
            self._init_git_repo(tmpdir)
          self._write_file(os.path.join(tmpdir, "README.md"), "# JS Project\n")
          self._write_file(os.path.join(tmpdir, "src", "index.js"), "console.log(1);\n")
          self._write_file(
              os.path.join(tmpdir, "node_modules", "pkg", "bad.py"),
              "try:\n    x()\nexcept:\n    pass\n",
          )
          if with_git:
            subprocess.run(
                ["git", "add", "README.md", "src/index.js"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", "chore: js seed"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
                text=True,
            )
            self._write_file(os.path.join(tmpdir, "src", "index.js"), "console.log(2);\n")

          out = io.StringIO()
          err = io.StringIO()
          code = AstScrubScript().execute(
              args=["--repo-root", tmpdir, "--strict"],
              stdout=out,
              stderr=err,
          )
          self.assertEqual(code, 0, err.getvalue())
          self.assertEqual(err.getvalue(), "")
          self.assertEqual(out.getvalue(), "")

  def test_reference_14_basescript_python_snippet_passes_ast_scrub(self):
    """Reference 14 Python BaseScript example passes scrub_source with zero violations."""
    ref14_path = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "references", "14_design_patterns_and_architecture.md"
    )
    with open(ref14_path, "r", encoding="utf-8") as f:
      ref14_text = f.read()
    match = re.search(
        r"#### 1\. Python \(`BaseScript`\)\s*```python\n(.*?)\n```",
        ref14_text,
        re.DOTALL,
    )
    self.assertIsNotNone(match, "Missing Python BaseScript snippet in Reference 14")
    snippet = match.group(1)
    self.assertIn("self.stderr.write", snippet)
    violations = scrub_source(snippet, filename="lib/base_script_example.py")
    self.assertEqual(violations, [])


class TestAstScrubSubprocessCli(unittest.TestCase):
  """Tests CLI execution via subprocess."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_cli_subprocess_exit_code_two(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      bad_path = os.path.join(tmpdir, "prod.py")
      with open(bad_path, "w", encoding="utf-8") as f:
        f.write("import unittest.mock\n")
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


class TestAstScrubberUnsafeTempFixtures(unittest.TestCase):
  """Tests detection of unsafe temp fixtures in test files (SCAR-TEST-45, GH-215)."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_mkdtemp_mkstemp_and_mktemp_rejected_in_tests(self):
    for snippet in (
        "import tempfile\nd = tempfile.mkdtemp(prefix='t_')\n",
        "from tempfile import mkdtemp\nd = mkdtemp()\n",
        "import tempfile\nfd, p = tempfile.mkstemp()\n",
        "from tempfile import mkstemp\nfd, p = mkstemp()\n",
        "import tempfile\np = tempfile.mktemp()\n",
        "from tempfile import mktemp\np = mktemp()\n",
    ):
      violations = scrub_source(snippet, filename="tests/test_sample.py")
      self.assertTrue(
          any(
              v.code == "UNSAFE_TEMP_FIXTURE" and v.scar_id == "SCAR-TEST-45"
              for v in violations
          ),
          f"Expected UNSAFE_TEMP_FIXTURE (SCAR-TEST-45) for {snippet!r}, got {violations}",
      )

  def test_chained_receiver_temp_method_calls_not_flagged(self):
    safe_chained = (
        "def test_chained(self, factory, custom):\n"
        "    self.helper.mkdtemp()\n"
        "    self.helper.mkstemp()\n"
        "    self.helper.mktemp()\n"
        "    factory().NamedTemporaryFile(delete=False)\n"
        "    custom.mkdtemp()\n"
        "    custom.NamedTemporaryFile(delete=False)\n"
    )
    self.assertEqual(
        scrub_source(safe_chained, filename="tests/test_sample.py"), []
    )

  def test_named_temporary_file_delete_false_rejected_in_tests(self):
    for snippet in (
        "import tempfile\nwith tempfile.NamedTemporaryFile('w', delete=False) as f:\n    pass\n",
        "from tempfile import NamedTemporaryFile\nf = NamedTemporaryFile(delete=False)\n",
    ):
      violations = scrub_source(snippet, filename="tests/test_sample.py")
      self.assertTrue(
          any(
              v.code == "UNSAFE_TEMP_FIXTURE" and v.scar_id == "SCAR-TEST-45"
              for v in violations
          ),
          f"Expected UNSAFE_TEMP_FIXTURE for {snippet!r}, got {violations}",
      )

  def test_fixed_tmp_filesystem_mutations_rejected_in_tests(self):
    for snippet in (
        'open("/tmp/fixed.txt", "w")\n',
        'open("/tmp/fixed.txt", "a")\n',
        'open("/tmp/fixed.txt", "x")\n',
        'open("/var/tmp/fixed.txt", mode="w")\n',
        'open("/tmp/fixed.txt", "r+")\n',
        'import os\nos.mkdir("/tmp/fixed_dir")\n',
        'import os\nos.makedirs("/var/tmp/fixed_dir", exist_ok=True)\n',
        'import os\nos.makedirs(os.path.join("/tmp", "sub"))\n',
        'import sqlite3\nsqlite3.connect("/tmp/test.db")\n',
        'import os, sqlite3\nsqlite3.connect(os.path.join("/var/tmp", "test.db"))\n',
        'from pathlib import Path\nPath("/tmp/x.txt").write_text("hi")\n',
        'from pathlib import Path\nPath("/tmp/x.bin").write_bytes(b"hi")\n',
        'from pathlib import Path\nPath("/tmp/sub").mkdir()\n',
        'from pathlib import Path\nPath("/var/tmp/flag").touch()\n',
        'from pathlib import Path\nPath("/tmp/x.txt").open("w")\n',
        'from pathlib import Path\n(Path("/tmp") / "sub.txt").write_text("hi")\n',
        'open(f"/tmp/{name}.txt", "w")\n',
        'import os\nos.makedirs(f"/var/tmp/run_{pid}")\n',
        'from pathlib import Path\nPath(f"/tmp/{slug}/x").write_text("hi")\n',
        'open("/tmp/" + name, "w")\n',
        'open("/tmp/" + a + ".txt", "w")\n',
        'import sqlite3\nsqlite3.connect(f"/tmp/{name}.db")\n',
        'import os\nos.mkdir("/var/tmp/" + run_id + "/dir")\n',
    ):
      violations = scrub_source(snippet, filename="tests/test_sample.py")
      self.assertTrue(
          any(
              v.code == "UNSAFE_TEMP_FIXTURE" and v.scar_id == "SCAR-TEST-45"
              for v in violations
          ),
          f"Expected UNSAFE_TEMP_FIXTURE for {snippet!r}, got {violations}",
      )

  def test_non_slash_tmp_prefix_interpolations_not_flagged(self):
    safe_interpolated = (
        "import os\n"
        "from pathlib import Path\n"
        "def test_non_tmp_dir(slug, suffix):\n"
        '    open(f"/tmp_{slug}", "w")\n'
        '    open("/tmp_" + slug, "w")\n'
        '    os.makedirs("/tmp" + suffix)\n'
        '    Path(f"/var/tmp_{slug}").write_text("ok")\n'
    )
    self.assertEqual(
        scrub_source(safe_interpolated, filename="tests/test_sample.py"), []
    )

  def test_safe_temp_helpers_and_pure_string_fixtures_allowed(self):
    safe_code = (
        "import os\n"
        "import tempfile\n"
        "from pathlib import Path\n"
        "def test_safe():\n"
        '    cmd = "git -C /tmp reset --hard"\n'
        '    resolved = resolve_scars_db_path("/tmp/x.db")\n'
        '    env = {"TMUX": "/tmp/tmux-1000/default,1234,0"}\n'
        '    p = Path("/tmp/not_written.txt")\n'
        '    with open("/tmp/readonly.txt", "r") as rf:\n'
        "        _ = rf.read()\n"
        "    with tempfile.TemporaryDirectory() as tmpdir:\n"
        '        with open(os.path.join(tmpdir, "out.txt"), "w") as wf:\n'
        '            wf.write("ok")\n'
        "    with tempfile.NamedTemporaryFile(delete=True) as nf:\n"
        "        pass\n"
        "    with tempfile.NamedTemporaryFile() as nf2:\n"
        "        pass\n"
        "    return cmd, resolved, env, p\n"
    )
    violations = scrub_source(safe_code, filename="tests/test_sample.py")
    self.assertEqual(violations, [])

  def test_non_test_files_and_ast_scrub_ignore_exempt(self):
    prod_code = (
        "import tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "f = tempfile.NamedTemporaryFile(delete=False)\n"
        'open("/tmp/runtime.log", "w")\n'
    )
    self.assertEqual(scrub_source(prod_code, filename="lib/foo.py"), [])

    exempt_code = (
        "import tempfile\n"
        "d = tempfile.mkdtemp()  # ast-scrub: ignore\n"
    )
    self.assertEqual(scrub_source(exempt_code, filename="tests/test_sample.py"), [])

  def test_ast_scrub_script_emits_scar_test_45_reflex_and_json(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      test_file = os.path.join(tmpdir, "test_unsafe_temp.py")
      with open(test_file, "w", encoding="utf-8") as f:
        f.write("import tempfile\nd = tempfile.mkdtemp()\n")

      out = io.StringIO()
      err = io.StringIO()
      code = AstScrubScript().execute(
          args=["--files", test_file, "--strict"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn("SCAR-ID: TEST-45", err.getvalue())
      self.assertIn("UNSAFE_TEMP_FIXTURE", err.getvalue())

      out_json = io.StringIO()
      err_json = io.StringIO()
      code_json = AstScrubScript().execute(
          args=["--files", test_file, "--strict", "--json"],
          stdout=out_json,
          stderr=err_json,
      )
      self.assertEqual(code_json, 2)
      payload = json.loads(err_json.getvalue().strip().splitlines()[0])
      self.assertEqual(payload["scar_id"], "SCAR-TEST-45")
      self.assertEqual(payload["short_id"], "TEST-45")
      self.assertEqual(payload["violation_code"], "UNSAFE_TEMP_FIXTURE")

  def test_full_repo_tests_directory_passes_ast_scrub_strict(self):
    out = io.StringIO()
    err = io.StringIO()
    code = AstScrubScript().execute(
        args=["--src-dir", os.path.join(_REPO_ROOT, "tests"), "--all", "--strict"],
        stdout=out,
        stderr=err,
    )
    self.assertEqual(code, 0, f"Unexpected AST scrub violations in tests/:\n{err.getvalue()}")
    self.assertEqual(err.getvalue(), "")


class TestAstScrubPerformanceBenchmark(unittest.TestCase):
  """Benchmarks execution speed to assert sub-20ms execution on repo files."""

  def setUp(self):
    if ast_scrub is None:
      self.skipTest("ast_scrub not implemented yet")

  def test_ast_scrub_execution_speed_under_twenty_milliseconds(self):
    targets = [
        os.path.join(_LIB_DIR, "safe_copy.py"),
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
