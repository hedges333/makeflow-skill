"""Pre-flight structural static analysis runner (SCAR-TEST-10).

Discovers structural syntax errors and undefined symbols before test execution.
Uses ruff or flake8 if available. Falls back to Python standard library AST.
"""

import argparse
import ast
import os
import py_compile
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence, TextIO

from lib.base_script import BaseScript, SilentArgumentParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_linter():
  """Find available structural static analysis linter."""
  if os.environ.get("STATIC_CHECK_FORCE_NO_LINTER") == "1":
    return None, None

  # 1. Search for ruff in local venv or PATH
  local_ruff = os.path.join(REPO_ROOT, ".venv", "bin", "ruff")
  if os.path.isfile(local_ruff) and os.access(local_ruff, os.X_OK):
    return "ruff", local_ruff

  path_ruff = shutil.which("ruff")
  if path_ruff:
    return "ruff", path_ruff

  # 2. Search for flake8 in local venv or PATH
  local_flake8 = os.path.join(REPO_ROOT, ".venv", "bin", "flake8")
  if os.path.isfile(local_flake8) and os.access(local_flake8, os.X_OK):
    return "flake8", local_flake8

  path_flake8 = shutil.which("flake8")
  if path_flake8:
    return "flake8", path_flake8

  return None, None


def collect_python_files(targets):
  """Collect Python file paths from list of targets."""
  files = []
  for target in targets:
    abs_path = os.path.abspath(target)
    if os.path.isfile(abs_path):
      if abs_path.endswith(".py"):
        files.append(abs_path)
    elif os.path.isdir(abs_path):
      for root, dirs, filenames in os.walk(abs_path):
        # Skip virtual environment and cache folders
        dirs[:] = [
            d
            for d in dirs
            if d not in {".venv", "venv", "__pycache__", ".git", ".tmp"}
        ]
        for name in filenames:
          if name.endswith(".py"):
            files.append(os.path.join(root, name))
  return sorted(files)


def run_ast_fallback(
    targets,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
):
  """Run fallback static check with standard library ast and py_compile."""
  out_stream = stdout if stdout is not None else sys.stdout
  err_stream = stderr if stderr is not None else sys.stderr
  files = collect_python_files(targets)
  if not files:
    out_stream.write("Pre-flight static check: No Python files found to inspect.\n")
    return 0

  errors = []
  for fpath in files:
    rel_path = os.path.relpath(fpath, REPO_ROOT)
    try:
      with open(fpath, "rb") as f:
        content = f.read()
      ast.parse(content, filename=rel_path)
      py_compile.compile(fpath, doraise=True)
    except SyntaxError as err:
      errors.append(f"{rel_path}:{err.lineno}:{err.offset}: SyntaxError: {err.msg}")
    except py_compile.PyCompileError as err:
      errors.append(f"{rel_path}: PyCompileError: {err.msg}")
    except Exception as err:  # pylint: disable=broad-except
      errors.append(f"{rel_path}: Error: {err}")

  if errors:
    err_stream.write(
        f"❌ Pre-flight static check found {len(errors)} error(s):\n"
    )
    for err in errors:
      err_stream.write(f"  {err}\n")
    return 1

  out_stream.write(
      f"✔ Pre-flight static check PASSED (using standard library AST, scanned {len(files)} files).\n"
  )
  return 0


def run_external_linter(
    tool_name,
    tool_path,
    targets,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
):
  """Run structural static check with ruff or flake8."""
  out_stream = stdout if stdout is not None else sys.stdout
  err_stream = stderr if stderr is not None else sys.stderr
  if tool_name == "ruff":
    cmd = [tool_path, "check", *targets, "--select=E9,F63,F7,F82"]
  elif tool_name == "flake8":
    cmd = [tool_path, *targets, "--select=E9,F63,F7,F82"]
  else:
    return run_ast_fallback(targets, stdout=out_stream, stderr=err_stream)

  proc = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      cwd=REPO_ROOT,
  )

  if proc.returncode != 0:
    err_stream.write(
        f"❌ Pre-flight static check failed ({tool_name}):\n"
    )
    if proc.stdout:
      err_stream.write(f"{proc.stdout.strip()}\n")
    if proc.stderr:
      err_stream.write(f"{proc.stderr.strip()}\n")
    return proc.returncode

  out_stream.write(
      f"✔ Pre-flight static check PASSED (using {tool_name}).\n"
  )
  return 0


class StaticCheckScript(BaseScript):
  """CLI runner for pre-flight structural static analysis (SCAR-TEST-10, SCAR-ARCH-05)."""

  def __init__(self) -> None:
    super().__init__()
    self.targets: List[str] = []

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="static_check.py",
        description="Pre-flight structural static analysis runner (SCAR-TEST-10).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/static_check.py\n"
            "  python3 scripts/static_check.py lib/base_script.py tests/test_base_script.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Python files or directories to check (defaults to lib, scripts, mcp, tests).",
    )
    parsed = parser.parse_args(self.raw_args)
    self.targets = list(parsed.targets or [])

  def run(self) -> int:
    targets = list(self.targets)
    if not targets:
      default_dirs = ["lib", "scripts", "mcp", "tests"]
      targets = [
          d for d in default_dirs if os.path.exists(os.path.join(REPO_ROOT, d))
      ]

    tool_name, tool_path = find_linter()
    if tool_name and tool_path:
      return run_external_linter(
          tool_name, tool_path, targets, stdout=self.stdout, stderr=self.stderr
      )
    return run_ast_fallback(targets, stdout=self.stdout, stderr=self.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
  """Main entry point for static checker."""
  return StaticCheckScript().execute(
      args=list(argv) if argv is not None else None
  )
