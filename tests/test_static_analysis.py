#!/usr/bin/env python3
"""Test suite for pre-flight structural static analysis runner (SCAR-TEST-10)."""

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)
STATIC_CHECK = os.path.join(REPO_ROOT, "scripts", "static_check.py")


def run_static_check(target_paths, env=None):
  """Executes scripts/static_check.py on target_paths and returns exit code and output."""
  cmd = [sys.executable, STATIC_CHECK] + list(target_paths)
  proc = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      cwd=REPO_ROOT,
      env=env,
  )
  return proc.returncode, proc.stdout, proc.stderr


class TestStaticAnalysis(unittest.TestCase):
  """Unittest suite for scripts/static_check.py pre-flight checks (SCAR-TEST-10)."""

  def setUp(self):
    self.assertTrue(os.path.exists(STATIC_CHECK), f"Missing {STATIC_CHECK}")

  def test_static_check_on_valid_code(self):
    """Valid Python scripts in the repository must pass with returncode 0."""
    code, out, err = run_static_check(["scripts/session_start.py"])
    self.assertEqual(
        code, 0, f"Expected 0 on valid file, got {code}. Output: {out}\n{err}"
    )
    self.assertTrue(
        "PASSED" in out or "All checks passed" in out or "valid" in out.lower()
    )

  def test_static_check_detects_syntax_error(self):
    """Static check must exit non-zero and report syntax errors."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("def broken_syntax(\n")  # Unclosed parenthesis and missing body
      bad_file = f.name

    try:
      code, out, err = run_static_check([bad_file])
      self.assertNotEqual(code, 0, f"Expected non-zero exit on syntax error, got {code}")
      output = out + err
      self.assertTrue(
          "SyntaxError" in output
          or "syntax" in output.lower()
          or "error" in output.lower()
      )
    finally:
      if os.path.exists(bad_file):
        os.unlink(bad_file)

  def test_static_check_tool_absence_fallback(self):
    """Static check falls back to stdlib AST when external linters are absent."""
    clean_env = dict(os.environ)
    clean_env["PATH"] = "/usr/bin:/bin"
    clean_env["STATIC_CHECK_FORCE_NO_LINTER"] = "1"

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("def valid_func():\n    return 42\n")
      valid_file = f.name

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
      f.write("def invalid_syntax(:\n")
      invalid_file = f.name

    try:
      code_valid, _, _ = run_static_check([valid_file], env=clean_env)
      self.assertEqual(
          code_valid, 0, f"Expected 0 on fallback AST valid file, got {code_valid}"
      )

      code_bad, _, _ = run_static_check([invalid_file], env=clean_env)
      self.assertNotEqual(
          code_bad, 0, f"Expected non-zero on fallback AST syntax error, got {code_bad}"
      )
    finally:
      if os.path.exists(valid_file):
        os.unlink(valid_file)
      if os.path.exists(invalid_file):
        os.unlink(invalid_file)


if __name__ == "__main__":
  unittest.main()
