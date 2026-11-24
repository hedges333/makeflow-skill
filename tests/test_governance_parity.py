#!/usr/bin/env python3
"""Hermetic governance file parity and AST import-closure tests (GH-296).

Verifies bidirectional parity between `CORE_GOVERNANCE_FILES` in
`lib/tdd_guard.py` and `skills/makeflow/references/03_tdd_and_code_quality.md`,
cross-list subset invariants across `lib/pre_tool_guard.py` and
`lib/harness_boundary.py`, `hooks.json` entrypoint registration, and
zero-allowlist top-level Python AST import closure.
"""

import ast
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
from typing import Iterable, Iterator, List, Sequence, Set, Union

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.harness_boundary import (
    HARNESS_INTERNAL_RELPATHS,
    RUNTIME_LOCK_STATE_RELPATHS,
)
from lib.pre_tool_guard import (
    GOVERNANCE_FILE_RELPATHS,
    STATE_GOVERNANCE_RELPATHS,
)
from lib.tdd_guard import CORE_GOVERNANCE_FILES

_TDD_DOC_RELPATH = os.path.join(
    "skills", "makeflow", "references", "03_tdd_and_code_quality.md"
)
_ANCHORED_HEADING_RE = re.compile(
    r"^### Anchored TDD Receipts \(`SCAR-TEST-12`\)\s*$", re.MULTILINE
)
_NEXT_HEADING_RE = re.compile(r"^#{2,6}\s+", re.MULTILINE)
_CORE_LEAD_RE = re.compile(r"core governance files:[ \t]*", re.IGNORECASE)
_INLINE_BACKTICK_LIST_RE = re.compile(
    r"((?:`[^`]+`(?:,\s*(?:and\s+)?|\s+and\s+))*`[^`]+`)\."
)
_BULLET_BACKTICK_RE = re.compile(r"^\s*[-*]\s+`([^`]+)`\s*$")
_BULLET_LINE_RE = re.compile(r"^\s*[-*]\s+")
_PROTECTED_EXAMPLES_RE = re.compile(
    r"Protected targets include\s+"
    r"((?:`[^`]+`(?:,\s*(?:and\s+)?|\s+and\s+))*`[^`]+`)\."
)
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_LOCAL_GOVERNANCE_ROOTS = ("lib", "scripts")


def _extract_anchored_receipts_section(doc_text: str) -> str:
  """Extracts the `### Anchored TDD Receipts (SCAR-TEST-12)` section body.

  Args:
    doc_text: Full Markdown contents of `03_tdd_and_code_quality.md`.

  Returns:
    The Markdown body following the heading up to the next section heading.

  Raises:
    ValueError: If the canonical heading is absent from `doc_text`.
  """
  heading_match = _ANCHORED_HEADING_RE.search(doc_text)
  if heading_match is None:
    raise ValueError(
        "Missing heading '### Anchored TDD Receipts (`SCAR-TEST-12`)'."
    )
  section_start = heading_match.end()
  next_heading = _NEXT_HEADING_RE.search(doc_text, section_start)
  if next_heading is None:
    return doc_text[section_start:]
  return doc_text[section_start : next_heading.start()]


def _parse_documented_core_governance_files(doc_text: str) -> List[str]:
  """Parses documented `CORE_GOVERNANCE_FILES` paths from Markdown.

  Supports both the legacy inline comma-separated sentence and the canonical
  bullet-list format under `### Anchored TDD Receipts (SCAR-TEST-12)`.

  Args:
    doc_text: Full Markdown contents of `03_tdd_and_code_quality.md`.

  Returns:
    Ordered list of backtick-quoted relative file paths (preserving duplicates
    for caller validation).

  Raises:
    ValueError: If the lead-in phrase, backticked file paths, or bullet line
      formatting is missing or malformed.
  """
  section = _extract_anchored_receipts_section(doc_text)
  lead_match = _CORE_LEAD_RE.search(section)
  if lead_match is None:
    raise ValueError(
        "Missing 'core governance files:' lead-in in Anchored TDD Receipts."
    )

  tail = section[lead_match.end() :]
  first_newline = tail.find("\n")
  rest_of_line = tail if first_newline == -1 else tail[:first_newline]

  if rest_of_line.strip():
    inline_match = _INLINE_BACKTICK_LIST_RE.match(rest_of_line.strip())
    if inline_match is None:
      raise ValueError(
          "Could not parse inline core governance file list after lead-in."
      )
    return _BACKTICK_TOKEN_RE.findall(inline_match.group(1))

  remaining_lines = [] if first_newline == -1 else tail[first_newline + 1 :].splitlines()
  paths: List[str] = []
  started_bullets = False
  for line in remaining_lines:
    if not line.strip():
      if started_bullets:
        break
      continue
    bullet_match = _BULLET_BACKTICK_RE.match(line)
    if bullet_match is None:
      if _BULLET_LINE_RE.match(line):
        raise ValueError(
            f"Malformed core governance file bullet line: {line!r}"
        )
      break
    started_bullets = True
    paths.append(bullet_match.group(1))

  if not paths:
    raise ValueError(
        "Found 'core governance files:' lead-in but no bulleted paths."
    )
  return paths


def _parse_protected_target_examples(doc_text: str) -> List[str]:
  """Parses example paths from the `Protected targets include ...` sentence.

  Args:
    doc_text: Full Markdown contents of `03_tdd_and_code_quality.md`.

  Returns:
    Ordered list of backtick-quoted paths cited as protected targets.

  Raises:
    ValueError: If the `Protected targets include ...` sentence is missing.
  """
  section = _extract_anchored_receipts_section(doc_text)
  match = _PROTECTED_EXAMPLES_RE.search(section)
  if match is None:
    raise ValueError(
        "Missing 'Protected targets include ...' sentence in Anchored TDD Receipts."
    )
  return _BACKTICK_TOKEN_RE.findall(match.group(1))


def _walk_hook_commands(node: object) -> Iterator[str]:
  """Yields shell command strings from command hook objects in `hooks.json`.

  Args:
    node: Deserialized JSON value (`dict`, `list`, or primitive).

  Yields:
    Command strings for every hook entry with `type == "command"`.
  """
  if isinstance(node, dict):
    if node.get("type") == "command" and isinstance(node.get("command"), str):
      yield node["command"]
    for value in node.values():
      yield from _walk_hook_commands(value)
  elif isinstance(node, list):
    for item in node:
      yield from _walk_hook_commands(item)


def _discover_hooks_json_scripts(repo_root: str) -> Set[str]:
  """Discovers all Python script relative paths registered in `hooks.json`.

  Args:
    repo_root: Repository root directory containing `hooks.json`.

  Returns:
    Set of POSIX-normalized relative `.py` script paths invoked by hooks.
  """
  hooks_path = os.path.join(repo_root, "hooks.json")
  with open(hooks_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)

  scripts: Set[str] = set()
  for command in _walk_hook_commands(payload):
    for token in shlex.split(command):
      normalized = token.replace("\\", "/")
      while normalized.startswith("./"):
        normalized = normalized[2:]
      if normalized.endswith(".py"):
        scripts.add(normalized)
  return scripts


def _iter_module_level_import_nodes(
    statements: Sequence[ast.stmt],
) -> Iterator[Union[ast.Import, ast.ImportFrom]]:
  """Yields top-level `Import` and `ImportFrom` nodes executed at import time.

  Descends into module-body control-flow blocks (`if`, `try`, `with`, `for`,
  `while`, `match`, `try*`), while intentionally skipping function, async
  function, and class bodies so deferred function-local imports stay excluded.

  Args:
    statements: Sequence of AST statements from a module body or block.

  Yields:
    Every module-level `ast.Import` and `ast.ImportFrom` node.
  """
  try_star_type = getattr(ast, "TryStar", ())
  match_type = getattr(ast, "Match", ())

  for stmt in statements:
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
      yield stmt
    elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
      continue
    elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
      yield from _iter_module_level_import_nodes(stmt.body)
      yield from _iter_module_level_import_nodes(stmt.orelse)
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
      yield from _iter_module_level_import_nodes(stmt.body)
    elif isinstance(stmt, ast.Try) or (
        try_star_type and isinstance(stmt, try_star_type)
    ):
      yield from _iter_module_level_import_nodes(stmt.body)
      for handler in stmt.handlers:
        yield from _iter_module_level_import_nodes(handler.body)
      yield from _iter_module_level_import_nodes(stmt.orelse)
      yield from _iter_module_level_import_nodes(stmt.finalbody)
    elif match_type and isinstance(stmt, match_type):
      for case in stmt.cases:
        yield from _iter_module_level_import_nodes(case.body)


def _resolve_candidate_parts(
    repo_root: str, parts: Sequence[str]
) -> Set[str]:
  """Resolves dotted module parts to existing local `lib/` or `scripts/` files.

  When `parts` resolves to a matching local module or subpackage, also includes
  every existing intermediate subpackage `__init__.py` (`parts[:depth]` for
  `2 <= depth < len(parts)`) that Python executes upon import, while keeping
  root `lib/__init__.py` and `scripts/__init__.py` excluded.

  Args:
    repo_root: Repository root directory on disk.
    parts: Non-empty sequence of Python module path segments.

  Returns:
    Set containing the resolved POSIX relative path and any existing
    intermediate subpackage `__init__.py` paths if a matching local Python
    module or subpackage exists on disk, or an empty set otherwise.
  """
  if len(parts) < 2 or parts[0] not in _LOCAL_GOVERNANCE_ROOTS:
    return set()

  target_rel = None
  if os.path.isfile(os.path.join(repo_root, *parts) + ".py"):
    target_rel = "/".join(parts) + ".py"
  elif os.path.isfile(os.path.join(repo_root, *parts, "__init__.py")):
    target_rel = "/".join(parts) + "/__init__.py"

  if target_rel is None:
    return set()

  resolved: Set[str] = {target_rel}
  for depth in range(2, len(parts)):
    prefix = parts[:depth]
    if os.path.isfile(os.path.join(repo_root, *prefix, "__init__.py")):
      resolved.add("/".join(prefix) + "/__init__.py")
  return resolved


def _resolve_local_module_paths(
    repo_root: str,
    source_relpath: str,
    node: Union[ast.Import, ast.ImportFrom],
) -> Set[str]:
  """Resolves an AST import statement to local `lib/*.py` / `scripts/*.py` files.

  Args:
    repo_root: Repository root directory on disk.
    source_relpath: POSIX relative path of the importing module.
    node: Module-level `ast.Import` or `ast.ImportFrom` node.

  Returns:
    Set of existing local relative Python file paths imported by `node`.
  """
  discovered: Set[str] = set()

  if isinstance(node, ast.Import):
    for alias in node.names:
      parts = alias.name.split(".")
      discovered.update(_resolve_candidate_parts(repo_root, parts))
    return discovered

  if node.level > 0:
    dir_rel = os.path.dirname(source_relpath.replace("\\", "/"))
    pkg_parts = [seg for seg in dir_rel.split("/") if seg]
    climb = node.level - 1
    if climb > len(pkg_parts):
      return discovered
    anchor = pkg_parts[: len(pkg_parts) - climb] if climb else list(pkg_parts)
    base_parts = anchor + (node.module.split(".") if node.module else [])
  else:
    base_parts = node.module.split(".") if node.module else []

  if base_parts:
    discovered.update(_resolve_candidate_parts(repo_root, base_parts))
    for alias in node.names:
      if alias.name != "*":
        discovered.update(
            _resolve_candidate_parts(repo_root, base_parts + [alias.name])
        )

  return discovered


def _collect_top_level_import_closure(
    repo_root: str, entry_relpaths: Iterable[str]
) -> Set[str]:
  """Computes the transitive top-level local import closure for Python entrypoints.

  Args:
    repo_root: Repository root directory on disk.
    entry_relpaths: Seed relative paths (non-Python entries are ignored).

  Returns:
    Set of POSIX relative paths for all visited Python seeds and every local
    `lib/*.py` or `scripts/*.py` module transitively imported at module scope.
  """
  queue: List[str] = sorted(
      {
          rel.replace("\\", "/")
          for rel in entry_relpaths
          if rel.endswith(".py")
      }
  )
  visited: Set[str] = set()

  while queue:
    current = queue.pop(0)
    if current in visited:
      continue
    abs_path = os.path.join(repo_root, current)
    if not os.path.isfile(abs_path):
      continue
    visited.add(current)

    with open(abs_path, "r", encoding="utf-8") as handle:
      tree = ast.parse(handle.read(), filename=abs_path)

    for node in _iter_module_level_import_nodes(tree.body):
      for dep in _resolve_local_module_paths(repo_root, current, node):
        if dep not in visited:
          queue.append(dep)

  return visited


class TestGovernanceParity(unittest.TestCase):
  """Verifies governance file registration, documentation, and AST import closure (GH-296)."""

  def _read_tdd_manual(self) -> str:
    """Reads `skills/makeflow/references/03_tdd_and_code_quality.md` from disk.

    Returns:
      UTF-8 contents of the TDD protocol reference manual.
    """
    doc_path = os.path.join(REPO_ROOT, _TDD_DOC_RELPATH)
    with open(doc_path, "r", encoding="utf-8") as handle:
      return handle.read()

  def test_core_governance_documentation_matches_code_bijectively(self) -> None:
    """Verifies `03_tdd_and_code_quality.md` matches `CORE_GOVERNANCE_FILES` exactly (GH-296)."""
    doc_paths = _parse_documented_core_governance_files(self._read_tdd_manual())

    self.assertEqual(
        len(doc_paths),
        len(set(doc_paths)),
        f"Duplicate paths in {_TDD_DOC_RELPATH}: {doc_paths}",
    )
    self.assertEqual(
        len(CORE_GOVERNANCE_FILES),
        len(set(CORE_GOVERNANCE_FILES)),
        f"Duplicate paths in CORE_GOVERNANCE_FILES: {CORE_GOVERNANCE_FILES}",
    )

    doc_set = set(doc_paths)
    code_set = set(CORE_GOVERNANCE_FILES)
    missing_from_doc = sorted(code_set - doc_set)
    extra_in_doc = sorted(doc_set - code_set)
    self.assertEqual(
        doc_set,
        code_set,
        (
            f"Documentation drift in {_TDD_DOC_RELPATH}: "
            f"missing_from_doc={missing_from_doc}, extra_in_doc={extra_in_doc}"
        ),
    )

  def test_protected_target_examples_belong_to_governance_relpaths(self) -> None:
    """Verifies example protected targets in `03_tdd_and_code_quality.md` are registered (GH-296)."""
    examples = _parse_protected_target_examples(self._read_tdd_manual())
    self.assertTrue(examples, "Expected non-empty protected target examples.")
    self.assertEqual(
        len(examples),
        len(set(examples)),
        f"Duplicate protected target examples: {examples}",
    )
    unregistered = sorted(set(examples) - set(GOVERNANCE_FILE_RELPATHS))
    self.assertEqual(
        unregistered,
        [],
        f"Documented protected targets missing from GOVERNANCE_FILE_RELPATHS: {unregistered}",
    )

  def test_governance_constants_maintain_subset_hierarchy(self) -> None:
    """Verifies strict subset relationships across governance and harness constants (GH-296)."""
    core_missing_from_guard = sorted(
        set(CORE_GOVERNANCE_FILES) - set(GOVERNANCE_FILE_RELPATHS)
    )
    self.assertEqual(
        core_missing_from_guard,
        [],
        (
            "CORE_GOVERNANCE_FILES entries missing from "
            f"GOVERNANCE_FILE_RELPATHS: {core_missing_from_guard}"
        ),
    )

    repo_guard_files = set(GOVERNANCE_FILE_RELPATHS) - set(
        STATE_GOVERNANCE_RELPATHS
    )
    guard_missing_from_harness = sorted(
        repo_guard_files - set(HARNESS_INTERNAL_RELPATHS)
    )
    self.assertEqual(
        guard_missing_from_harness,
        [],
        (
            "Repo GOVERNANCE_FILE_RELPATHS entries missing from "
            f"HARNESS_INTERNAL_RELPATHS: {guard_missing_from_harness}"
        ),
    )

    locks_missing_from_state = sorted(
        set(RUNTIME_LOCK_STATE_RELPATHS) - set(STATE_GOVERNANCE_RELPATHS)
    )
    self.assertEqual(
        locks_missing_from_state,
        [],
        (
            "RUNTIME_LOCK_STATE_RELPATHS entries missing from "
            f"STATE_GOVERNANCE_RELPATHS: {locks_missing_from_state}"
        ),
    )

  def test_governance_repo_files_exist_on_disk_without_lib_shebangs(self) -> None:
    """Verifies every registered governance repo file exists and `lib/*.py` has no shebang (GH-296)."""
    self.assertEqual(
        len(CORE_GOVERNANCE_FILES),
        len(set(CORE_GOVERNANCE_FILES)),
        "CORE_GOVERNANCE_FILES contains duplicate entries.",
    )

    repo_files = (
        set(CORE_GOVERNANCE_FILES)
        | (set(GOVERNANCE_FILE_RELPATHS) - set(STATE_GOVERNANCE_RELPATHS))
        | set(HARNESS_INTERNAL_RELPATHS)
    )

    missing_on_disk = sorted(
        rel
        for rel in repo_files
        if not os.path.isfile(os.path.join(REPO_ROOT, rel))
    )
    self.assertEqual(
        missing_on_disk,
        [],
        f"Registered governance files missing on disk: {missing_on_disk}",
    )

    shebang_violations: List[str] = []
    for rel in sorted(repo_files):
      if rel.startswith("lib/") and rel.endswith(".py"):
        abs_path = os.path.join(REPO_ROOT, rel)
        with open(abs_path, "r", encoding="utf-8") as handle:
          first_line = handle.readline()
        if first_line.startswith("#!"):
          shebang_violations.append(rel)

    self.assertEqual(
        shebang_violations,
        [],
        f"Library governance modules must not contain shebangs (SCAR-ARCH-08): {shebang_violations}",
    )

  def test_core_governance_files_are_closed_under_top_level_imports(self) -> None:
    """Verifies zero-allowlist top-level AST import closure for `CORE_GOVERNANCE_FILES` (GH-296)."""
    closure = _collect_top_level_import_closure(REPO_ROOT, CORE_GOVERNANCE_FILES)
    missing = sorted(closure - set(CORE_GOVERNANCE_FILES))
    self.assertEqual(
        missing,
        [],
        (
            "Top-level transitive imports missing from CORE_GOVERNANCE_FILES: "
            f"{missing}"
        ),
    )

  def test_hooks_json_scripts_and_governance_imports_are_closed(self) -> None:
    """Verifies `hooks.json` scripts and top-level imports are closed in `GOVERNANCE_FILE_RELPATHS` (GH-296)."""
    hook_scripts = _discover_hooks_json_scripts(REPO_ROOT)
    self.assertTrue(
        hook_scripts, "Expected hooks.json to register at least one Python script."
    )
    self.assertIn("hooks.json", GOVERNANCE_FILE_RELPATHS)

    missing_hooks = sorted(hook_scripts - set(GOVERNANCE_FILE_RELPATHS))
    self.assertEqual(
        missing_hooks,
        [],
        f"hooks.json scripts missing from GOVERNANCE_FILE_RELPATHS: {missing_hooks}",
    )

    closure = _collect_top_level_import_closure(
        REPO_ROOT, set(GOVERNANCE_FILE_RELPATHS) | hook_scripts
    )
    missing_from_guard = sorted(closure - set(GOVERNANCE_FILE_RELPATHS))
    self.assertEqual(
        missing_from_guard,
        [],
        (
            "Top-level transitive imports missing from GOVERNANCE_FILE_RELPATHS: "
            f"{missing_from_guard}"
        ),
    )

    missing_from_harness = sorted(closure - set(HARNESS_INTERNAL_RELPATHS))
    self.assertEqual(
        missing_from_harness,
        [],
        (
            "Top-level transitive imports missing from HARNESS_INTERNAL_RELPATHS: "
            f"{missing_from_harness}"
        ),
    )

  def test_ast_closure_detects_unregistered_future_imports_and_ignores_function_locals(
      self,
  ) -> None:
    """Verifies synthetic future imports are caught while function-local imports stay excluded (GH-296)."""
    with tempfile.TemporaryDirectory() as tmpdir:
      os.makedirs(os.path.join(tmpdir, "scripts"))
      os.makedirs(os.path.join(tmpdir, "lib"))

      files = {
          "scripts/entry.py": "from lib.core_a import run\n",
          "lib/core_a.py": (
              "import lib.future_guard\n"
              "if True:\n"
              "  from lib import future_helper\n"
              "try:\n"
              "  from . import future_rel\n"
              "except ImportError:\n"
              "  pass\n"
              "\n"
              "def deferred_helper():\n"
              "  from lib import non_core_local\n"
              "  return non_core_local\n"
          ),
          "lib/future_guard.py": "VALUE = 1\n",
          "lib/future_helper.py": "VALUE = 2\n",
          "lib/future_rel.py": "VALUE = 3\n",
          "lib/non_core_local.py": "VALUE = 4\n",
      }
      for rel, content in files.items():
        with open(os.path.join(tmpdir, rel), "w", encoding="utf-8") as handle:
          handle.write(content)

      registered = {"scripts/entry.py", "lib/core_a.py"}
      closure = _collect_top_level_import_closure(tmpdir, registered)

      self.assertEqual(
          closure - registered,
          {
              "lib/future_guard.py",
              "lib/future_helper.py",
              "lib/future_rel.py",
          },
      )
      self.assertNotIn("lib/non_core_local.py", closure)

  def test_hooks_json_discovery_detects_unregistered_future_hook_script(
      self,
  ) -> None:
    """Verifies adding a new script in synthetic `hooks.json` is detected as unregistered (GH-296)."""
    with tempfile.TemporaryDirectory() as tmpdir:
      hooks_payload = {
          "makeflow-guard": {
              "SessionStart": [
                  {
                      "type": "command",
                      "command": "python3 ./scripts/session_start.py",
                  }
              ],
              "PreToolUse": [
                  {
                      "matcher": "run_command",
                      "hooks": [
                          {
                              "type": "command",
                              "command": "python3 ./scripts/pre_tool_guard.py",
                          },
                          {
                              "type": "command",
                              "command": "python3 ./scripts/future_hook.py --strict",
                          },
                      ],
                  }
              ],
          }
      }
      with open(
          os.path.join(tmpdir, "hooks.json"), "w", encoding="utf-8"
      ) as handle:
        json.dump(hooks_payload, handle)

      discovered = _discover_hooks_json_scripts(tmpdir)
      registered = {
          "scripts/session_start.py",
          "scripts/pre_tool_guard.py",
      }
      self.assertEqual(discovered - registered, {"scripts/future_hook.py"})

  def test_documentation_parser_detects_omitted_and_stale_entries_across_formats(
      self,
  ) -> None:
    """Verifies doc parser flags missing, stale, and duplicate paths in inline and bullet docs (GH-296)."""
    expected = {
        "scripts/tdd_guard.py",
        "lib/tdd_guard.py",
        "lib/git_index.py",
    }

    inline_doc = (
        "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
        "The seal also hashes core governance files: `scripts/tdd_guard.py`, "
        "`lib/tdd_guard.py`, and `lib/stale_guard.py`. "
        "The tree hash ignores `conftest.py`.\n\n"
        "### Phase 3: Refactor\n"
    )
    inline_parsed = _parse_documented_core_governance_files(inline_doc)
    self.assertEqual(expected - set(inline_parsed), {"lib/git_index.py"})
    self.assertEqual(set(inline_parsed) - expected, {"lib/stale_guard.py"})

    bullet_doc = (
        "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
        "The seal also hashes these core governance files:\n\n"
        "- `scripts/tdd_guard.py`\n"
        "- `lib/tdd_guard.py`\n"
        "- `lib/tdd_guard.py`\n"
        "- `lib/stale_guard.py`\n\n"
        "The tree hash ignores `conftest.py`.\n"
    )
    bullet_parsed = _parse_documented_core_governance_files(bullet_doc)
    self.assertEqual(len(bullet_parsed), 4)
    self.assertEqual(len(set(bullet_parsed)), 3)
    self.assertEqual(expected - set(bullet_parsed), {"lib/git_index.py"})
    self.assertEqual(set(bullet_parsed) - expected, {"lib/stale_guard.py"})

  def test_documentation_parser_rejects_missing_heading_and_lead_in_sections(
      self,
  ) -> None:
    """Verifies documentation parsers raise ValueError on missing headings, lead-ins, or lists (GH-296)."""
    with self.assertRaisesRegex(ValueError, "Missing heading"):
      _extract_anchored_receipts_section("# Unrelated Heading\n\nBody text.\n")

    missing_lead_doc = (
        "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
        "This section omits the expected lead-in sentence.\n"
    )
    with self.assertRaisesRegex(ValueError, "lead-in"):
      _parse_documented_core_governance_files(missing_lead_doc)

    malformed_inline_doc = (
        "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
        "The seal also hashes core governance files: lib/tdd_guard.py without backticks.\n"
    )
    with self.assertRaisesRegex(ValueError, "inline core governance file list"):
      _parse_documented_core_governance_files(malformed_inline_doc)

    zero_bullets_doc = (
        "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
        "The seal also hashes these core governance files:\n\n"
        "The tree hash ignores `conftest.py`.\n"
    )
    with self.assertRaisesRegex(ValueError, "no bulleted paths"):
      _parse_documented_core_governance_files(zero_bullets_doc)

    missing_protected_doc = (
        "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
        "The hook blocks direct writes to governance files.\n"
    )
    with self.assertRaisesRegex(ValueError, "Protected targets include"):
      _parse_protected_target_examples(missing_protected_doc)

  def test_documentation_parser_rejects_malformed_core_governance_bullets(
      self,
  ) -> None:
    """Verifies malformed bullet lines raise ValueError instead of terminating silently (GH-296)."""
    cases = {
        "trailing_unquoted": (
            "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
            "The seal also hashes these core governance files:\n\n"
            "- `scripts/tdd_guard.py`\n"
            "- lib/unquoted.py\n"
        ),
        "trailing_extra_prose": (
            "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
            "The seal also hashes these core governance files:\n\n"
            "- `scripts/tdd_guard.py`\n"
            "- `lib/a.py` extra prose\n"
        ),
        "leading_unquoted": (
            "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
            "The seal also hashes these core governance files:\n\n"
            "- lib/unquoted.py\n"
        ),
        "asterisk_unquoted": (
            "### Anchored TDD Receipts (`SCAR-TEST-12`)\n\n"
            "The seal also hashes these core governance files:\n\n"
            "- `scripts/tdd_guard.py`\n"
            "* lib/unquoted.py\n"
        ),
    }
    for label, doc_text in cases.items():
      with self.subTest(case=label):
        with self.assertRaisesRegex(ValueError, "Malformed"):
          _parse_documented_core_governance_files(doc_text)

  def test_ast_closure_includes_nested_subpackage_inits_and_excludes_root_init(
      self,
  ) -> None:
    """Verifies nested subpackage `__init__.py` prefixes and transitive imports are closed (GH-296)."""
    with tempfile.TemporaryDirectory() as tmpdir:
      os.makedirs(os.path.join(tmpdir, "scripts"))
      os.makedirs(os.path.join(tmpdir, "lib", "guards", "sub"))

      files = {
          "lib/__init__.py": "ROOT_INIT = True\n",
          "lib/guards/__init__.py": "from . import base\n",
          "lib/guards/base.py": "BASE = True\n",
          "lib/guards/sub/__init__.py": "SUB = True\n",
          "lib/guards/sub/leaf.py": "class Guard:\n  pass\n",
          "scripts/from_leaf.py": "from lib.guards.sub.leaf import Guard\n",
          "scripts/import_leaf.py": "import lib.guards.sub.leaf\n",
          "scripts/from_sub.py": "from lib.guards.sub import leaf\n",
      }
      for rel, content in files.items():
        with open(os.path.join(tmpdir, rel), "w", encoding="utf-8") as handle:
          handle.write(content)

      for entrypoint in (
          "scripts/from_leaf.py",
          "scripts/import_leaf.py",
          "scripts/from_sub.py",
      ):
        with self.subTest(entrypoint=entrypoint):
          closure = _collect_top_level_import_closure(tmpdir, {entrypoint})
          self.assertEqual(
              closure,
              {
                  entrypoint,
                  "lib/guards/__init__.py",
                  "lib/guards/base.py",
                  "lib/guards/sub/__init__.py",
                  "lib/guards/sub/leaf.py",
              },
          )
          self.assertNotIn("lib/__init__.py", closure)


if __name__ == "__main__":
  unittest.main()
