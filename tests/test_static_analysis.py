#!/usr/bin/env python3
"""Test suite for pre-flight structural static analysis runner (SCAR-TEST-10)."""

import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_CHECK = os.path.join(REPO_ROOT, "scripts", "static_check.py")


def run_static_check(target_paths, env=None):
  cmd = [sys.executable, STATIC_CHECK] + list(target_paths)
  proc = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      cwd=REPO_ROOT,
      env=env,
  )
  return proc.returncode, proc.stdout, proc.stderr


def test_static_check_on_valid_code():
  # Valid python scripts in the repo must pass with returncode 0
  code, out, err = run_static_check(["scripts/session_start.py"])
  assert code == 0, f"Expected 0 on valid file, got {code}. Output: {out}\n{err}"
  assert "PASSED" in out or "All checks passed" in out or "valid" in out.lower()
  print("✔ Pre-flight static check passes on valid Python code.")


def test_static_check_detects_syntax_error():
  with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
    f.write("def broken_syntax(\n")  # Unclosed parenthesis and missing body
    bad_file = f.name

  try:
    code, out, err = run_static_check([bad_file])
    assert code != 0, f"Expected non-zero exit on syntax error, got {code}"
    output = out + err
    assert "SyntaxError" in output or "syntax" in output.lower() or "error" in output.lower()
    print("✔ Pre-flight static check successfully catches syntax defects.")
  finally:
    if os.path.exists(bad_file):
      os.unlink(bad_file)


def test_static_check_tool_absence_fallback():
  # Run in environment with PATH cleared and without pointing to .venv
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
    code_valid, out_valid, err_valid = run_static_check([valid_file], env=clean_env)
    assert code_valid == 0, f"Expected 0 on fallback AST valid file, got {code_valid}"

    code_bad, out_bad, err_bad = run_static_check([invalid_file], env=clean_env)
    assert code_bad != 0, f"Expected non-zero on fallback AST syntax error, got {code_bad}"
    print("✔ Pre-flight static check falls back gracefully when external tools are absent.")
  finally:
    if os.path.exists(valid_file):
      os.unlink(valid_file)
    if os.path.exists(invalid_file):
      os.unlink(invalid_file)


def main():
  if not os.path.exists(STATIC_CHECK):
    print(f"❌ Missing {STATIC_CHECK}")
    sys.exit(1)

  test_static_check_on_valid_code()
  test_static_check_detects_syntax_error()
  test_static_check_tool_absence_fallback()
  print("\n✔ All static analysis tests passed successfully!")


if __name__ == "__main__":
  main()
