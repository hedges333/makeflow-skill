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
from typing import Iterable, List, Optional, Sequence, TextIO, Tuple, Union

from lib.base_script import BaseScript, SilentArgumentParser
from lib.python_files import (
    SKIP_SCAN_DIRS,
    collect_python_files,
    is_ignored_dir_target,
    normalize_targets,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_linter(
    repo_root: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
  """Finds an available structural static analysis linter.

  Probes ``<repo_root>/.venv/bin`` and ``PATH`` for ``ruff`` first, then
  ``flake8``. Honors ``STATIC_CHECK_FORCE_NO_LINTER=1`` to force stdlib AST
  fallback.

  Args:
    repo_root: Optional target repository root directory for ``.venv/bin``
      discovery. Defaults to ``REPO_ROOT``.

  Returns:
    Tuple of ``(tool_name, tool_path)`` or ``(None, None)`` when unavailable.
  """
  if os.environ.get("STATIC_CHECK_FORCE_NO_LINTER") == "1":
    return None, None

  active_root = os.path.abspath(repo_root) if repo_root is not None else REPO_ROOT

  # 1. Search for ruff in local venv or PATH
  local_ruff = os.path.join(active_root, ".venv", "bin", "ruff")
  if os.path.isfile(local_ruff) and os.access(local_ruff, os.X_OK):
    return "ruff", local_ruff

  path_ruff = shutil.which("ruff")
  if path_ruff:
    return "ruff", path_ruff

  # 2. Search for flake8 in local venv or PATH
  local_flake8 = os.path.join(active_root, ".venv", "bin", "flake8")
  if os.path.isfile(local_flake8) and os.access(local_flake8, os.X_OK):
    return "flake8", local_flake8

  path_flake8 = shutil.which("flake8")
  if path_flake8:
    return "flake8", path_flake8

  return None, None


def _filter_external_linter_targets(
    targets: Union[str, os.PathLike, Iterable[Union[str, os.PathLike]]],
    active_root: str,
) -> List[str]:
  """Filters and deduplicates CLI targets before invoking an external linter.

  Drops ignored directories, non-Python files, and missing paths. Deduplicates
  remaining targets by canonical absolute path.

  Args:
    targets: Single target path or iterable of target paths passed to the linter.
    active_root: Normalized absolute repository root directory.

  Returns:
    Canonically deduplicated list of CLI target path strings.
  """
  filtered: List[str] = []
  seen = set()
  for target_str in normalize_targets(targets):
    abs_target = os.path.abspath(
        target_str
        if os.path.isabs(target_str)
        else os.path.join(active_root, target_str)
    )
    if os.path.isfile(abs_target):
      if not abs_target.endswith(".py"):
        continue
    elif os.path.isdir(abs_target):
      if is_ignored_dir_target(abs_target, base_dir=active_root):
        continue
    else:
      continue

    if abs_target not in seen:
      seen.add(abs_target)
      filtered.append(target_str)
  return filtered


def run_ast_fallback(
    targets: Union[str, os.PathLike, Iterable[Union[str, os.PathLike]]],
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    repo_root: Optional[str] = None,
) -> int:
  """Runs fallback static check with standard library ast and py_compile.

  Args:
    targets: Single path or iterable of Python files or directories to inspect.
    stdout: Optional output stream for status messages.
    stderr: Optional error stream for syntax and compile diagnostics.
    repo_root: Optional base repository directory used to resolve relative
      targets and format reported file paths. Defaults to ``REPO_ROOT``.

  Returns:
    Exit code ``0`` when all files pass or none are found, else ``1``.
  """
  out_stream = stdout if stdout is not None else sys.stdout
  err_stream = stderr if stderr is not None else sys.stderr
  active_root = os.path.abspath(repo_root) if repo_root is not None else REPO_ROOT
  files = collect_python_files(targets, base_dir=active_root)
  if not files:
    out_stream.write("Pre-flight static check: No Python files found to inspect.\n")
    return 0

  errors = []
  for fpath in files:
    try:
      rel_path = os.path.relpath(fpath, active_root)
    except ValueError:
      rel_path = fpath
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
    tool_name: str,
    tool_path: str,
    targets: Union[str, os.PathLike, Iterable[Union[str, os.PathLike]]],
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    repo_root: Optional[str] = None,
) -> int:
  """Runs structural static check with ruff or flake8.

  Materializes target iterables once before calling ``collect_python_files()``
  so empty or non-Python repositories exit ``0`` cleanly. Strips ignored
  directories and non-Python files, then passes ``--extend-exclude`` with
  ``cwd=repo_root``.

  Args:
    tool_name: External linter name (``"ruff"`` or ``"flake8"``).
    tool_path: Executable path for the selected linter.
    targets: Single path or iterable of Python files or directories to inspect.
    stdout: Optional output stream for status messages.
    stderr: Optional error stream for linter diagnostics.
    repo_root: Optional repository root directory used for target resolution
      and subprocess working directory. Defaults to ``REPO_ROOT``.

  Returns:
    Subprocess return code (``0`` on success or when no Python files exist).
  """
  out_stream = stdout if stdout is not None else sys.stdout
  err_stream = stderr if stderr is not None else sys.stderr
  active_root = os.path.abspath(repo_root) if repo_root is not None else REPO_ROOT
  target_list = normalize_targets(targets)

  if tool_name not in ("ruff", "flake8"):
    return run_ast_fallback(
        target_list, stdout=out_stream, stderr=err_stream, repo_root=active_root
    )

  files = collect_python_files(target_list, base_dir=active_root)
  if not files:
    out_stream.write("Pre-flight static check: No Python files found to inspect.\n")
    return 0

  filtered_targets = _filter_external_linter_targets(target_list, active_root)
  extend_exclude = ",".join(sorted(SKIP_SCAN_DIRS))
  if tool_name == "ruff":
    cmd = [
        tool_path,
        "check",
        *filtered_targets,
        "--select=E9,F63,F7,F82",
        f"--extend-exclude={extend_exclude}",
    ]
  else:
    cmd = [
        tool_path,
        *filtered_targets,
        "--select=E9,F63,F7,F82",
        f"--extend-exclude={extend_exclude}",
    ]

  proc = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      cwd=active_root,
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
    """Initializes script state with default repository root and target list."""
    super().__init__()
    self.targets: List[str] = []
    self.repo_root: str = os.getcwd()

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parses CLI options and positional targets.

    Args:
      args: Optional CLI argument list. Defaults to ``sys.argv[1:]``.
    """
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="static_check.py",
        description="Pre-flight structural static analysis runner (SCAR-TEST-10).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/static_check.py\n"
            "  python3 scripts/static_check.py --repo-root /path/to/repo\n"
            "  python3 scripts/static_check.py lib/base_script.py tests/test_base_script.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=os.getcwd(),
        help="Repository root directory to scan when targets are omitted.",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Python files or directories to check (defaults to --repo-root).",
    )
    parsed = parser.parse_args(self.raw_args)
    self.repo_root = os.path.abspath(parsed.repo_root)
    self.targets = list(parsed.targets or [])

  def run(self) -> int:
    """Executes static analysis against configured targets or repository root.

    Returns:
      Zero on success, or non-zero when syntax or linter errors are detected.
    """
    targets = list(self.targets) if self.targets else [self.repo_root]

    tool_name, tool_path = find_linter(repo_root=self.repo_root)
    if tool_name and tool_path:
      return run_external_linter(
          tool_name,
          tool_path,
          targets,
          stdout=self.stdout,
          stderr=self.stderr,
          repo_root=self.repo_root,
      )
    return run_ast_fallback(
        targets,
        stdout=self.stdout,
        stderr=self.stderr,
        repo_root=self.repo_root,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
  """Executes ``StaticCheckScript`` with optional CLI arguments.

  Args:
    argv: Optional sequence of CLI arguments.

  Returns:
    Process exit code returned by ``StaticCheckScript.execute()``.
  """
  return StaticCheckScript().execute(
      args=list(argv) if argv is not None else None
  )
