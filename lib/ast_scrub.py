"""Structural AST scrubber gate (SCAR-AST-01, GH-56).

Prevents autonomous coding agents from bypassing failing tests by silencing errors
in production code. Enforces deterministic AST checks that reject:
1. Silent exception handlers (bare except:, broad except Exception: with pass/ellipsis/continue).
2. Trivial fallback returns in broad handlers (return None, {}, [], "", 0, False without logging/re-raising).
3. Production mock leakage (importing unittest.mock, MagicMock, mock, pytest in non-test code).

Emits Vector 4 Reflex recovery directives on stderr with exit code 2.
"""

import argparse
import ast
from dataclasses import dataclass
import os
import re
import subprocess
from typing import List, Optional, Set

from lib.base_script import BaseScript
from lib.reflex import REFLEX_EXIT_CODE, emit_reflex

SCAR_ID = "SCAR-AST-01"
DEFAULT_SCAN_DIRS = ("lib", "scripts", "mcp", "tests")

_COMMENT_EXEMPTION_RE = re.compile(
    r"#.*(?:pylint:\s*disable=(?:broad-except|bare-except)|noqa|ast-scrub:\s*(?:ignore|disable)|nosec)",
    re.IGNORECASE,
)

_LOGGING_NAMES = {
    "log",
    "logger",
    "logging",
    "print",
    "warn",
    "warning",
    "error",
    "exception",
    "critical",
    "write",
    "append",
}

_FORBIDDEN_MODULE_PREFIXES = (
    "unittest.mock",
    "mock",
    "pytest",
    "_pytest",
)

_FORBIDDEN_SYMBOLS = {
    "MagicMock",
    "Mock",
    "patch",
    "PropertyMock",
    "AsyncMock",
    "monkeypatch",
}


@dataclass
class Violation:
  """Represents a single structural AST violation."""

  code: str
  file_path: str
  line_number: int
  trigger: str
  rejected: str
  reflex: str


def is_test_file(filepath: str) -> bool:
  """Determines if a given file path is a test file or in a test directory."""
  normalized = os.path.normpath(filepath).replace(os.sep, "/")
  basename = os.path.basename(normalized)

  if basename.startswith("test_") or basename.endswith("_test.py"):
    return True

  parts = normalized.split("/")
  for part in parts[:-1]:
    if part in {"test", "tests", "testing"} or part.startswith("test_") or part.endswith("_test"):
      return True

  return False


class AstScrubberVisitor(ast.NodeVisitor):
  """AST Visitor that detects anti-shortcut violations."""

  def __init__(
      self,
      filename: str,
      source_lines: List[str],
      is_test: bool,
  ) -> None:
    self.filename = filename
    self.source_lines = source_lines
    self.is_test = is_test
    self.violations: List[Violation] = []

  def _is_line_exempt(self, lineno: int) -> bool:
    if 1 <= lineno <= len(self.source_lines):
      line = self.source_lines[lineno - 1]
      if _COMMENT_EXEMPTION_RE.search(line):
        return True
    return False

  def _is_broad_handler(self, node: ast.ExceptHandler) -> bool:
    if node.type is None:
      return True
    if isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
      return True
    if isinstance(node.type, ast.Tuple):
      for elt in node.type.elts:
        if isinstance(elt, ast.Name) and elt.id in {"Exception", "BaseException"}:
          return True
    return False

  def _is_silent_body(self, body: List[ast.stmt]) -> bool:
    # Filter out docstrings
    meaningful: List[ast.stmt] = []
    for s in body:
      if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
        continue
      meaningful.append(s)

    if not meaningful:
      return True

    for s in meaningful:
      if isinstance(s, (ast.Pass, ast.Continue)):
        continue
      if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...:
        continue
      return False
    return True

  def _has_logging_or_raise(self, node: ast.AST) -> bool:
    for child in ast.walk(node):
      if isinstance(child, ast.Raise):
        return True
      if isinstance(child, ast.Call):
        func_name = ""
        if isinstance(child.func, ast.Name):
          func_name = child.func.id
        elif isinstance(child.func, ast.Attribute):
          func_name = child.func.attr
          if isinstance(child.func.value, ast.Name):
            func_name = f"{child.func.value.id}.{func_name}"
        func_lower = func_name.lower()
        for token in _LOGGING_NAMES:
          if token in func_lower:
            return True
    return False

  def _get_trivial_return(self, body: List[ast.stmt]) -> Optional[ast.Return]:
    for s in body:
      if isinstance(s, ast.Return):
        # return None or empty return
        if s.value is None:
          return s
        if isinstance(s.value, ast.Constant):
          val = s.value.value
          if val is None or val == "" or val is False:
            return s
          if val == 0 and type(val) is int:
            return s
        if isinstance(s.value, ast.Dict) and len(s.value.keys) == 0:
          return s
        if isinstance(s.value, ast.List) and len(s.value.elts) == 0:
          return s
        if isinstance(s.value, ast.Tuple) and len(s.value.elts) == 0:
          return s
    return None

  def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
    if self._is_line_exempt(node.lineno):
      self.generic_visit(node)
      return

    if self._is_broad_handler(node):
      if self._is_silent_body(node.body):
        self.violations.append(
            Violation(
                code="SILENT_EXCEPTION_HANDLER",
                file_path=self.filename,
                line_number=node.lineno,
                trigger=(
                    f"{self.filename}:{node.lineno}: Broad or bare exception handler"
                    " silently swallows exceptions with pass/ellipsis/continue."
                ),
                rejected=(
                    "Silently swallowing exceptions in production code without"
                    " recovery or logging."
                ),
                reflex=(
                    "Catch specific exceptions (e.g. KeyError, ValueError) or"
                    " log/re-raise the error."
                ),
            )
        )
      else:
        # Check for trivial fallback returns without logging or re-raise
        if not self._has_logging_or_raise(node):
          trivial_ret = self._get_trivial_return(node.body)
          if trivial_ret is not None:
            self.violations.append(
                Violation(
                    code="TRIVIAL_FALLBACK_RETURN",
                    file_path=self.filename,
                    line_number=trivial_ret.lineno,
                    trigger=(
                        f"{self.filename}:{trivial_ret.lineno}: Broad exception"
                        " handler returns trivial fallback default without logging or"
                        " re-raising."
                    ),
                    rejected=(
                        "Masking broken logic with trivial default returns in"
                        " broad exception handlers."
                    ),
                    reflex=(
                        "Catch specific exceptions (e.g. KeyError, ValueError) or"
                        " log the error before returning."
                    ),
                )
            )

    self.generic_visit(node)

  def visit_Import(self, node: ast.Import) -> None:
    if self.is_test or self._is_line_exempt(node.lineno):
      self.generic_visit(node)
      return

    for alias in node.names:
      mod = alias.name
      for forbidden in _FORBIDDEN_MODULE_PREFIXES:
        if mod == forbidden or mod.startswith(f"{forbidden}."):
          self.violations.append(
              Violation(
                  code="PRODUCTION_MOCK_LEAKAGE",
                  file_path=self.filename,
                  line_number=node.lineno,
                  trigger=(
                      f"{self.filename}:{node.lineno}: Production file imports test"
                      f" doubles or testing framework ({mod})."
                  ),
                  rejected=(
                      "Importing mock test doubles or test libraries into"
                      " non-test production modules."
                  ),
                  reflex=(
                      "Remove test mocks from production code; isolate mocks"
                      " inside test files."
                  ),
              )
          )
    self.generic_visit(node)

  def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
    if self.is_test or self._is_line_exempt(node.lineno):
      self.generic_visit(node)
      return

    mod = node.module or ""
    # Check forbidden modules
    is_forbidden_mod = False
    for forbidden in _FORBIDDEN_MODULE_PREFIXES:
      if mod == forbidden or mod.startswith(f"{forbidden}."):
        is_forbidden_mod = True
        break

    if is_forbidden_mod:
      self.violations.append(
          Violation(
              code="PRODUCTION_MOCK_LEAKAGE",
              file_path=self.filename,
              line_number=node.lineno,
              trigger=(
                  f"{self.filename}:{node.lineno}: Production file imports test"
                  f" doubles or testing framework ({mod})."
              ),
              rejected=(
                  "Importing mock test doubles or test libraries into"
                  " non-test production modules."
              ),
              reflex=(
                  "Remove test mocks from production code; isolate mocks"
                  " inside test files."
              ),
          )
      )
      self.generic_visit(node)
      return

    # Check unittest importing mock
    if mod == "unittest":
      for alias in node.names:
        if alias.name in ("mock", "MagicMock", "Mock", "patch"):
          self.violations.append(
              Violation(
                  code="PRODUCTION_MOCK_LEAKAGE",
                  file_path=self.filename,
                  line_number=node.lineno,
                  trigger=(
                      f"{self.filename}:{node.lineno}: Production file imports"
                      f" test doubles from unittest ({alias.name})."
                  ),
                  rejected=(
                      "Importing mock test doubles or test libraries into"
                      " non-test production modules."
                  ),
                  reflex=(
                      "Remove test mocks from production code; isolate mocks"
                      " inside test files."
                  ),
              )
          )
      self.generic_visit(node)
      return

    # Check forbidden symbol imports across any module
    for alias in node.names:
      if alias.name in _FORBIDDEN_SYMBOLS:
        self.violations.append(
            Violation(
                code="PRODUCTION_MOCK_LEAKAGE",
                file_path=self.filename,
                line_number=node.lineno,
                trigger=(
                    f"{self.filename}:{node.lineno}: Production file imports test"
                    f" double symbol ({alias.name})."
                ),
                rejected=(
                    "Importing mock test doubles or test libraries into"
                    " non-test production modules."
                ),
                reflex=(
                    "Remove test mocks from production code; isolate mocks"
                    " inside test files."
                ),
            )
        )

    self.generic_visit(node)


def scrub_source(
    source_code: str,
    filename: str = "snippet.py",
    is_test: Optional[bool] = None,
) -> List[Violation]:
  """Scrubs a Python source code string for structural violations."""
  if is_test is None:
    is_test = is_test_file(filename)

  try:
    tree = ast.parse(source_code, filename=filename)
  except SyntaxError:
    # Syntax errors are handled by static_check.py (SCAR-TEST-10)
    return []

  lines = source_code.splitlines()
  visitor = AstScrubberVisitor(filename=filename, source_lines=lines, is_test=is_test)
  visitor.visit(tree)
  return visitor.violations


def scrub_file(filepath: str) -> List[Violation]:
  """Scrubs a single Python file for structural violations."""
  try:
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
      content = f.read()
  except OSError:
    return []
  return scrub_source(content, filename=filepath)


def get_git_modified_files(repo_root: str, src_dir: str) -> Optional[List[str]]:
  """Discovers git modified or untracked Python files inside src_dir."""
  abs_repo = os.path.abspath(repo_root)
  abs_src = os.path.abspath(src_dir if os.path.isabs(src_dir) else os.path.join(abs_repo, src_dir))

  # Check if repo_root is a git repository
  git_dir = os.path.join(abs_repo, ".git")
  if not os.path.exists(git_dir):
    return None

  modified_files: Set[str] = set()

  # 1. Uncommitted and untracked files
  try:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", abs_src],
        cwd=abs_repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if proc.returncode == 0:
      for line in proc.stdout.splitlines():
        if not line.strip():
          continue
        # Status code is first 2 chars
        path_part = line[3:].strip()
        if " -> " in path_part:
          path_part = path_part.split(" -> ")[1].strip()
        full_path = (
            path_part
            if os.path.isabs(path_part)
            else os.path.join(abs_repo, path_part)
        )
        if full_path.endswith(".py") and os.path.isfile(full_path):
          modified_files.add(os.path.abspath(full_path))
        elif os.path.isdir(full_path):
          for py_file in collect_python_files(full_path):
            modified_files.add(py_file)
  except (subprocess.SubprocessError, OSError):
    pass

  # 2. Branch committed changes relative to merge-base with main/origin
  try:
    base_proc = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=abs_repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    base_commit = base_proc.stdout.strip() if base_proc.returncode == 0 else ""
    if not base_commit:
      base_proc2 = subprocess.run(
          ["git", "merge-base", "HEAD", "main"],
          cwd=abs_repo,
          capture_output=True,
          text=True,
          check=False,
          timeout=5,
      )
      base_commit = base_proc2.stdout.strip() if base_proc2.returncode == 0 else ""

    if base_commit:
      diff_proc = subprocess.run(
          ["git", "diff", "--name-only", base_commit, "HEAD", "--", abs_src],
          cwd=abs_repo,
          capture_output=True,
          text=True,
          check=False,
          timeout=10,
      )
      if diff_proc.returncode == 0:
        for rel_file in diff_proc.stdout.splitlines():
          rel_file = rel_file.strip()
          if rel_file.endswith(".py"):
            full_path = os.path.abspath(os.path.join(abs_repo, rel_file))
            if os.path.isfile(full_path):
              modified_files.add(full_path)
  except (subprocess.SubprocessError, OSError):
    pass

  return sorted(list(modified_files))


def collect_python_files(src_dir: str) -> List[str]:
  """Recursively collects Python files in src_dir skipping caches."""
  collected = []
  for root, dirs, files in os.walk(src_dir):
    dirs[:] = [d for d in dirs if d not in {".venv", "venv", "__pycache__", ".git", ".tmp"}]
    for fname in files:
      if fname.endswith(".py"):
        collected.append(os.path.abspath(os.path.join(root, fname)))
  return sorted(collected)


class AstScrubScript(BaseScript):
  """BaseScript runner for the structural AST scrubber gate."""

  def __init__(self) -> None:
    super().__init__()
    self.src_dir: Optional[str] = None
    self.files: List[str] = []
    self.strict: bool = False
    self.as_json: bool = False
    self.all_files: bool = False
    self.repo_root: str = os.getcwd()

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = argparse.ArgumentParser(
        prog="ast_scrub.py",
        description="Structural AST scrubber gate (SCAR-AST-01, GH-56).",
    )
    parser.add_argument("--src-dir", default=None, help="Source directory to scan.")
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Specific files to scan.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 2 on any violation.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit violations in JSON reflex format.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_files",
        help="Scan all files in src-dir unconditionally (ignoring git status).",
    )
    parser.add_argument(
        "--repo-root",
        default=os.getcwd(),
        help="Repository root directory.",
    )

    parsed = parser.parse_args(self.raw_args)
    self.src_dir = parsed.src_dir
    self.files = list(parsed.files)
    self.strict = parsed.strict
    self.as_json = parsed.as_json
    self.all_files = parsed.all_files
    self.repo_root = os.path.abspath(parsed.repo_root)

  def run(self) -> int:
    targets: List[str] = []

    if self.files:
      for f in self.files:
        p = os.path.abspath(f if os.path.isabs(f) else os.path.join(self.repo_root, f))
        if os.path.isfile(p):
          targets.append(p)
        elif os.path.isdir(p):
          targets.extend(collect_python_files(p))
    elif self.src_dir:
      src_abs = os.path.abspath(
          self.src_dir if os.path.isabs(self.src_dir) else os.path.join(self.repo_root, self.src_dir)
      )
      if self.all_files:
        targets = collect_python_files(src_abs)
      else:
        git_files = get_git_modified_files(self.repo_root, src_abs)
        if git_files is not None:
          targets = git_files
        else:
          targets = collect_python_files(src_abs)
    else:
      for subdir in DEFAULT_SCAN_DIRS:
        src_abs = os.path.abspath(os.path.join(self.repo_root, subdir))
        if not os.path.isdir(src_abs):
          continue
        if self.all_files:
          targets.extend(collect_python_files(src_abs))
        else:
          git_files = get_git_modified_files(self.repo_root, src_abs)
          if git_files is not None:
            targets.extend(git_files)
          else:
            targets.extend(collect_python_files(src_abs))

    all_violations: List[Violation] = []
    for target_path in targets:
      violations = scrub_file(target_path)
      all_violations.extend(violations)

    if all_violations:
      for v in all_violations:
        emit_reflex(
            stderr=self.stderr,
            scar_id=SCAR_ID,
            violation_code=v.code,
            trigger=v.trigger,
            rejected=v.rejected,
            reflex=v.reflex,
            as_json=self.as_json,
        )
      return REFLEX_EXIT_CODE

    if not self.as_json and targets:
      self.stdout.write(
          f"✔ Structural AST scrub passed (scanned {len(targets)} files).\n"
      )
    return 0
