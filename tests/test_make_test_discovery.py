#!/usr/bin/env python3
"""Tests for automatic unittest discovery across tests/test_*.py (GH-205).

Verifies:
1. Independent os.listdir filesystem witness parity against unittest.TestLoader.discover
   (N of N modules, zero loader errors, non-empty test count per file).
2. AST structural completeness across all tests/test_*.py files (zero top-level
   def test_* functions outside unittest.TestCase and >= 1 TestCase subclass).
3. Hermetic make -s -f Makefile test TESTS_DIR=<tmp> boundary checks for
   unregistered passing and failing test files without Makefile edits.
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)


def _list_test_files(tests_dir: str) -> list[str]:
  """Returns sorted test_*.py filenames from the given directory."""
  return sorted(
      fname
      for fname in os.listdir(tests_dir)
      if fname.startswith("test_")
      and fname.endswith(".py")
      and os.path.isfile(os.path.join(tests_dir, fname))
  )


def _collect_discovered_module_counts(suite: unittest.TestSuite) -> dict[str, int]:
  """Walks a unittest TestSuite tree and counts discovered tests per module."""
  counts: dict[str, int] = {}
  for item in suite:
    if isinstance(item, unittest.TestSuite):
      for mod_name, count in _collect_discovered_module_counts(item).items():
        counts[mod_name] = counts.get(mod_name, 0) + count
    else:
      mod_name = item.__class__.__module__
      counts[mod_name] = counts.get(mod_name, 0) + 1
  return counts


def _is_testcase_base(base: ast.expr, known_testcase_classes: set[str]) -> bool:
  """Returns True if an AST base class expression inherits from unittest.TestCase."""
  if isinstance(base, ast.Name):
    return base.id in known_testcase_classes
  if isinstance(base, ast.Attribute):
    return base.attr == "TestCase"
  return False


def _is_main_guard(node: ast.If) -> bool:
  """Returns True when an AST if-statement checks __name__ == '__main__'."""
  test = node.test
  if (
      not isinstance(test, ast.Compare)
      or len(test.ops) != 1
      or len(test.comparators) != 1
      or not isinstance(test.ops[0], ast.Eq)
  ):
    return False
  left, right = test.left, test.comparators[0]
  return (
      isinstance(left, ast.Name)
      and left.id == "__name__"
      and isinstance(right, ast.Constant)
      and right.value == "__main__"
  ) or (
      isinstance(right, ast.Name)
      and right.id == "__name__"
      and isinstance(left, ast.Constant)
      and left.value == "__main__"
  )


def _iter_active_nodes(node: ast.AST):
  """Yields AST nodes in active control flow, skipping nested defs and classes."""
  stack = [node]
  while stack:
    cur = stack.pop()
    yield cur
    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
      continue
    stack.extend(ast.iter_child_nodes(cur))


def _has_unittest_main_entrypoint(tree: ast.Module) -> bool:
  """Returns True when a top-level __main__ guard invokes unittest.main()."""
  for node in tree.body:
    if not isinstance(node, ast.If) or not _is_main_guard(node):
      continue
    for stmt in node.body:
      for sub in _iter_active_nodes(stmt):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "unittest"
            and sub.func.attr == "main"
        ):
          return True
  return False


class TestMakeTestDiscovery(unittest.TestCase):
  """Validates automatic unittest discovery parity, AST TestCase completeness, and Makefile wiring (GH-205)."""

  def test_unittest_discovery_matches_filesystem_witness(self):
    """Independent os.listdir witness matches unittest.TestLoader().discover N of N modules."""
    test_files = _list_test_files(TESTS_DIR)
    self.assertGreaterEqual(
        len(test_files),
        21,
        f"Expected at least 21 test_*.py files in {TESTS_DIR}, found {len(test_files)}",
    )
    expected_modules = [os.path.splitext(fname)[0] for fname in test_files]

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=TESTS_DIR, pattern="test_*.py")
    self.assertEqual(
        loader.errors,
        [],
        f"unittest.TestLoader().discover reported errors: {loader.errors}",
    )

    counts_by_module = _collect_discovered_module_counts(suite)
    self.assertEqual(
        sorted(counts_by_module.keys()),
        expected_modules,
        "Discovered modules do not match N of N test_*.py files on disk",
    )
    for mod_name in expected_modules:
      self.assertGreater(
          counts_by_module.get(mod_name, 0),
          0,
          f"Discovered module '{mod_name}' produced zero test cases",
      )

  def test_all_test_files_wrap_tests_in_unittest_testcase(self):
    """Every tests/test_*.py file wraps tests in unittest.TestCase with zero standalone def test_* functions."""
    test_files = _list_test_files(TESTS_DIR)
    self.assertGreaterEqual(len(test_files), 21)

    for fname in test_files:
      fpath = os.path.join(TESTS_DIR, fname)
      with open(fpath, "r", encoding="utf-8") as f:
        source = f.read()
      tree = ast.parse(source, filename=fpath)

      standalone_test_funcs = [
          node.name
          for node in tree.body
          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
          and node.name.startswith("test_")
      ]
      self.assertEqual(
          standalone_test_funcs,
          [],
          f"{fname} contains standalone test_* functions outside unittest.TestCase: {standalone_test_funcs}",
      )

      known_testcase_classes = {"TestCase"}
      testcase_classes: list[ast.ClassDef] = []
      non_testcase_test_methods: list[str] = []
      for node in tree.body:
        if not isinstance(node, ast.ClassDef):
          continue
        if any(_is_testcase_base(base, known_testcase_classes) for base in node.bases):
          known_testcase_classes.add(node.name)
          testcase_classes.append(node)
        else:
          for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
              non_testcase_test_methods.append(f"{node.name}.{item.name}")

      self.assertEqual(
          non_testcase_test_methods,
          [],
          f"{fname} defines test_* methods outside unittest.TestCase: {non_testcase_test_methods}",
      )
      self.assertGreaterEqual(
          len(testcase_classes),
          1,
          f"{fname} must define at least one unittest.TestCase subclass",
      )

      testcase_method_count = sum(
          1
          for cls in testcase_classes
          for item in cls.body
          if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
          and item.name.startswith("test_")
      )
      self.assertGreaterEqual(
          testcase_method_count,
          1,
          f"{fname} TestCase subclasses define zero test_* methods",
      )
      self.assertTrue(
          _has_unittest_main_entrypoint(tree),
          f"{fname} must invoke unittest.main() inside a top-level if __name__ == '__main__' guard",
      )

  def test_ast_main_guard_falsification_rejects_decoys_and_accepts_real_guards(self):
    """AST __main__ check rejects comment/string/unused-def decoys and accepts real unittest.main() calls."""
    comment_decoy = (
        "import unittest\n"
        'DOC = "unittest.main()"\n'
        "# if __name__ == '__main__': unittest.main()\n"
    )
    self.assertFalse(_has_unittest_main_entrypoint(ast.parse(comment_decoy)))

    custom_runner = (
        "import unittest\n"
        "def run(): pass\n"
        'if __name__ == "__main__":\n'
        '  msg = "unittest.main()"\n'
        "  run()\n"
    )
    self.assertFalse(_has_unittest_main_entrypoint(ast.parse(custom_runner)))

    nested_def_decoy = (
        "import unittest\n"
        'if __name__ == "__main__":\n'
        "  def unused():\n"
        "    unittest.main()\n"
    )
    self.assertFalse(_has_unittest_main_entrypoint(ast.parse(nested_def_decoy)))

    self_path = os.path.abspath(__file__)
    with open(self_path, "r", encoding="utf-8") as f:
      self_source = f.read()
    stripped_self = "\n".join(
        line
        for line in self_source.splitlines()
        if line not in ('if __name__ == "__main__":', "  unittest.main()")
    )
    self.assertFalse(_has_unittest_main_entrypoint(ast.parse(stripped_self)))

    reversed_guard = 'import unittest\nif "__main__" == __name__:\n  unittest.main()\n'
    self.assertTrue(_has_unittest_main_entrypoint(ast.parse(reversed_guard)))

    nested_ctrl = (
        "import sys, unittest\n"
        'if __name__ == "__main__":\n'
        '  if "-x" in sys.argv:\n'
        "    pass\n"
        "  unittest.main(verbosity=2)\n"
    )
    self.assertTrue(_has_unittest_main_entrypoint(ast.parse(nested_ctrl)))

  def test_makefile_discovers_unregistered_passing_test_without_makefile_edits(self):
    """Hermetic make test TESTS_DIR=<tmp> runs an unregistered passing suite after test-static and before budget gates."""
    makefile_path = os.path.join(REPO_ROOT, "Makefile")
    with tempfile.TemporaryDirectory() as tmp_tests:
      marker_path = os.path.join(tmp_tests, "pass_marker.txt")
      test_file = os.path.join(tmp_tests, "test_unregistered_pass.py")
      with open(test_file, "w", encoding="utf-8") as f:
        f.write(
            '"""Hermetic unregistered passing test fixture (GH-205)."""\n\n'
            "import unittest\n\n\n"
            "class TestUnregisteredPass(unittest.TestCase):\n"
            '  """Writes a marker file and passes."""\n\n'
            "  def test_unregistered_pass(self):\n"
            f"    with open({marker_path!r}, 'w', encoding='utf-8') as out:\n"
            "      out.write('passed\\n')\n"
            "    print('UNREGISTERED_DISCOVERY_PASS_MARKER', flush=True)\n"
            "    self.assertTrue(True)\n"
        )

      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              makefile_path,
              "test",
              f"TESTS_DIR={tmp_tests}",
              f"PYTHON={sys.executable}",
          ],
          cwd=REPO_ROOT,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(
          proc.returncode,
          0,
          f"Expected make test exit 0, got {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
      )
      self.assertTrue(
          os.path.isfile(marker_path),
          "Unregistered test_unregistered_pass.py was not executed by make test",
      )
      with open(marker_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read().strip(), "passed")

      expected_order = [
          "Running structural AST scrubber...",
          "Running pre-flight structural static analysis...",
          "UNREGISTERED_DISCOVERY_PASS_MARKER",
          "Testing file structure and skill context budget...",
          "All tests passed.",
      ]
      positions = []
      for token in expected_order:
        self.assertIn(
            token,
            proc.stdout,
            f"Missing expected output '{token}' in make test stdout:\n{proc.stdout}",
        )
        positions.append(proc.stdout.index(token))
      self.assertEqual(
          positions,
          sorted(positions),
          f"Unexpected stage order in make test stdout:\n{proc.stdout}",
      )

  def test_makefile_fails_with_scar_test_09_on_unregistered_failing_test(self):
    """Hermetic make test TESTS_DIR=<tmp> fails with Vector 4 Reflex exit 2 ([SCAR-ID: TEST-09]) on an unregistered failing suite."""
    makefile_path = os.path.join(REPO_ROOT, "Makefile")
    with tempfile.TemporaryDirectory() as tmp_tests:
      marker_path = os.path.join(tmp_tests, "fail_marker.txt")
      test_file = os.path.join(tmp_tests, "test_unregistered_fail.py")
      with open(test_file, "w", encoding="utf-8") as f:
        f.write(
            '"""Hermetic unregistered failing test fixture (GH-205)."""\n\n'
            "import unittest\n\n\n"
            "class TestUnregisteredFail(unittest.TestCase):\n"
            '  """Writes a marker file and fails deliberately."""\n\n'
            "  def test_unregistered_fail(self):\n"
            f"    with open({marker_path!r}, 'w', encoding='utf-8') as out:\n"
            "      out.write('ran_fail\\n')\n"
            "    self.fail('intentional discovery failure for GH-205')\n"
        )

      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              makefile_path,
              "test",
              f"TESTS_DIR={tmp_tests}",
              f"PYTHON={sys.executable}",
          ],
          cwd=REPO_ROOT,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(
          proc.returncode,
          2,
          f"Expected make test exit 2, got {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
      )
      self.assertTrue(
          os.path.isfile(marker_path),
          "Unregistered test_unregistered_fail.py was not executed by make test",
      )
      self.assertIn("[SCAR-ID: TEST-09]", proc.stderr)
      self.assertIn("TEST_DISCOVERY_SUITE_FAILED", proc.stderr)
      self.assertNotIn("Testing file structure and skill context budget...", proc.stdout)


if __name__ == "__main__":
  unittest.main()
