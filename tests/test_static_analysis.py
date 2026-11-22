#!/usr/bin/env python3
"""Test suite for pre-flight structural static analysis runner (SCAR-TEST-10)."""

import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)
STATIC_CHECK = os.path.join(REPO_ROOT, "scripts", "static_check.py")

from lib import ast_scrub  # noqa: E402
from lib import static_check  # noqa: E402
from scripts import ast_scrub as ast_scrub_script  # noqa: E402
from scripts import static_check as static_check_script  # noqa: E402

EXPECTED_SKIP_SCAN_DIRS = frozenset({
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
})


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
    with tempfile.TemporaryDirectory() as tmpdir:
      bad_file = os.path.join(tmpdir, "broken.py")
      with open(bad_file, "w", encoding="utf-8") as f:
        f.write("def broken_syntax(\n")  # Unclosed parenthesis and missing body

      code, out, err = run_static_check([bad_file])
      self.assertNotEqual(code, 0, f"Expected non-zero exit on syntax error, got {code}")
      output = out + err
      self.assertTrue(
          "SyntaxError" in output
          or "syntax" in output.lower()
          or "error" in output.lower()
      )

  def test_static_check_tool_absence_fallback(self):
    """Static check falls back to stdlib AST when external linters are absent."""
    clean_env = dict(os.environ)
    clean_env["PATH"] = "/usr/bin:/bin"
    clean_env["STATIC_CHECK_FORCE_NO_LINTER"] = "1"

    with tempfile.TemporaryDirectory() as tmpdir:
      valid_file = os.path.join(tmpdir, "valid.py")
      with open(valid_file, "w", encoding="utf-8") as f:
        f.write("def valid_func():\n    return 42\n")

      invalid_file = os.path.join(tmpdir, "invalid.py")
      with open(invalid_file, "w", encoding="utf-8") as f:
        f.write("def invalid_syntax(:\n")

      code_valid, _, _ = run_static_check([valid_file], env=clean_env)
      self.assertEqual(
          code_valid, 0, f"Expected 0 on fallback AST valid file, got {code_valid}"
      )

      code_bad, _, _ = run_static_check([invalid_file], env=clean_env)
      self.assertNotEqual(
          code_bad, 0, f"Expected non-zero on fallback AST syntax error, got {code_bad}"
      )


def _write_text(path: str, content: str) -> str:
  """Creates parent directories and writes UTF-8 text to path."""
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as handle:
    handle.write(content)
  return os.path.abspath(path)


class TestPythonFileDiscovery(unittest.TestCase):
  """Unit tests for shared Python file discovery helper (GH-275)."""

  def test_shared_collect_python_files_identity_and_skip_dirs_parity(self):
    """GH-275: lib/static_check, lib/ast_scrub, and scripts re-export one shared helper."""
    from lib import python_files

    self.assertIs(static_check.collect_python_files, python_files.collect_python_files)
    self.assertIs(ast_scrub.collect_python_files, python_files.collect_python_files)
    self.assertIs(
        static_check_script.collect_python_files,
        python_files.collect_python_files,
    )
    self.assertIs(
        ast_scrub_script.collect_python_files,
        python_files.collect_python_files,
    )
    self.assertIs(static_check.SKIP_SCAN_DIRS, python_files.SKIP_SCAN_DIRS)
    self.assertIs(ast_scrub.SKIP_SCAN_DIRS, python_files.SKIP_SCAN_DIRS)
    self.assertEqual(python_files.SKIP_SCAN_DIRS, EXPECTED_SKIP_SCAN_DIRS)
    self.assertIs(
        ast_scrub.has_ignored_dir_segment,
        python_files.has_ignored_dir_segment,
    )
    self.assertEqual(ast_scrub.DEFAULT_SCAN_DIRS, ("lib", "scripts", "mcp", "tests"))

  def test_collect_python_files_accepts_single_path_and_sequences_deduplicated(self):
    """GH-275: collect_python_files supports single paths, sequences, base_dir, and deduplication."""
    from lib.python_files import collect_python_files

    with tempfile.TemporaryDirectory() as tmpdir:
      manage_py = _write_text(os.path.join(tmpdir, "manage.py"), "x = 1\n")
      service_py = _write_text(os.path.join(tmpdir, "src", "service.py"), "y = 2\n")
      routes_py = _write_text(os.path.join(tmpdir, "app", "routes.py"), "z = 3\n")
      _write_text(os.path.join(tmpdir, "README.md"), "# docs\n")

      expected_all = sorted([manage_py, service_py, routes_py])
      self.assertEqual(collect_python_files(tmpdir), expected_all)
      self.assertEqual(collect_python_files(Path(tmpdir)), expected_all)
      self.assertEqual(
          collect_python_files([
              tmpdir,
              os.path.join(tmpdir, "src"),
              service_py,
              Path(manage_py),
          ]),
          expected_all,
      )
      self.assertEqual(
          collect_python_files(
              ["src", "manage.py", "src/service.py"],
              base_dir=tmpdir,
          ),
          sorted([manage_py, service_py]),
      )

  def test_collect_python_files_skips_all_ignored_dirs_direct_and_nested(self):
    """GH-275: collect_python_files skips all 12 ignored dirs nested and passed directly."""
    from lib.python_files import collect_python_files, has_ignored_dir_segment

    with tempfile.TemporaryDirectory() as tmpdir:
      ok_py = _write_text(os.path.join(tmpdir, "src", "ok.py"), "def ok():\n    return 1\n")
      for ignored in sorted(EXPECTED_SKIP_SCAN_DIRS):
        _write_text(os.path.join(tmpdir, ignored, "broken.py"), "def broken(:\n")
        _write_text(
            os.path.join(tmpdir, ignored, "nested", "bad.py"),
            "def nested_broken(:\n",
        )
      nested_nm_bad = _write_text(
          os.path.join(tmpdir, "frontend", "node_modules", "pkg", "bad.py"),
          "def nm_broken(:\n",
      )

      self.assertEqual(collect_python_files(tmpdir), [ok_py])
      self.assertTrue(has_ignored_dir_segment(tmpdir, nested_nm_bad))
      self.assertFalse(has_ignored_dir_segment(tmpdir, ok_py))

      for ignored in sorted(EXPECTED_SKIP_SCAN_DIRS):
        dpath = os.path.join(tmpdir, ignored)
        self.assertEqual(collect_python_files(dpath), [])
        self.assertEqual(collect_python_files(dpath + os.sep), [])
        self.assertEqual(collect_python_files([ignored], base_dir=tmpdir), [])

      self.assertEqual(
          collect_python_files(os.path.join(tmpdir, "frontend", "node_modules")),
          [],
      )
      self.assertEqual(
          collect_python_files(
              ["frontend/node_modules/pkg"],
              base_dir=tmpdir,
          ),
          [],
      )

  def test_collect_python_files_handles_symlink_directory_cycle(self):
    """GH-275: collect_python_files avoids infinite loops on symlinked directory cycles."""
    from lib.python_files import collect_python_files

    with tempfile.TemporaryDirectory() as tmpdir:
      ok_py = _write_text(os.path.join(tmpdir, "src", "ok.py"), "VALUE = 1\n")
      loop_link = os.path.join(tmpdir, "src", "loop")
      os.symlink(tmpdir, loop_link)

      self.assertEqual(collect_python_files(tmpdir), [ok_py])

  def test_collect_python_files_handles_ignored_ancestor_empty_missing_and_parent_traversal(self):
    """GH-275: Shared discovery handles ignored ancestors above base_dir, empty/missing, and '..'."""
    from lib.python_files import (
        collect_python_files,
        is_ignored_dir_target,
        normalize_targets,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
      consumer_repo = os.path.join(tmpdir, ".tmp", "consumer_repo")
      ok_py = _write_text(os.path.join(consumer_repo, "src", "ok.py"), "VALUE = 1\n")
      _write_text(os.path.join(consumer_repo, ".venv", "broken.py"), "def broken(:\n")

      # Ignored ancestor (.tmp) above base_dir must not exclude consumer_repo or src
      self.assertFalse(is_ignored_dir_target(consumer_repo, base_dir=consumer_repo))
      self.assertFalse(is_ignored_dir_target("src", base_dir=consumer_repo))
      self.assertEqual(
          collect_python_files(consumer_repo, base_dir=consumer_repo),
          [ok_py],
      )

      # Single-pass generator and PathLike normalization
      self.assertEqual(normalize_targets(Path("src")), ["src"])
      self.assertEqual(
          normalize_targets(p for p in [Path("src"), "manage.py"]),
          ["src", "manage.py"],
      )
      self.assertEqual(
          collect_python_files((p for p in ["src"]), base_dir=consumer_repo),
          [ok_py],
      )

      # Empty targets, empty iterators, and non-existent targets
      self.assertEqual(collect_python_files([], base_dir=consumer_repo), [])
      self.assertEqual(
          collect_python_files((p for p in []), base_dir=consumer_repo),
          [],
      )
      self.assertEqual(
          collect_python_files(
              ["does_not_exist.py", "no_such_dir"],
              base_dir=consumer_repo,
          ),
          [],
      )

      # Parent traversal ('..') resolving into ignored directories
      self.assertTrue(is_ignored_dir_target("src/../.venv", base_dir=consumer_repo))
      self.assertEqual(
          collect_python_files(["src/../.venv"], base_dir=consumer_repo),
          [],
      )


class TestAdaptiveStaticCheck(unittest.TestCase):
  """Hermetic unit and boundary tests for generalized StaticCheckScript (GH-275)."""

  def test_static_check_scans_consumer_layout_in_memory_and_subprocess(self):
    """GH-275: StaticCheckScript discovers src/, app/, and root .py without hardcoded dirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
      service_py = _write_text(
          os.path.join(tmpdir, "src", "service.py"),
          "def run():\n    return 1\n",
      )
      _write_text(
          os.path.join(tmpdir, "app", "routes.py"),
          "def index():\n    return 'ok'\n",
      )
      manage_py = _write_text(
          os.path.join(tmpdir, "manage.py"),
          "def main():\n    return 0\n",
      )

      with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
        out = io.StringIO()
        err = io.StringIO()
        code = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir], stdout=out, stderr=err
        )
        self.assertEqual(code, 0, f"Unexpected failure: {out.getvalue()}\n{err.getvalue()}")
        self.assertIn("scanned 3 files", out.getvalue())
        self.assertEqual(err.getvalue(), "")

      sub_env = dict(os.environ)
      sub_env["STATIC_CHECK_FORCE_NO_LINTER"] = "1"
      proc_ok = subprocess.run(
          [sys.executable, STATIC_CHECK],
          capture_output=True,
          text=True,
          cwd=tmpdir,
          env=sub_env,
      )
      self.assertEqual(
          proc_ok.returncode,
          0,
          f"Expected subprocess pass: {proc_ok.stdout}\n{proc_ok.stderr}",
      )
      self.assertIn("scanned 3 files", proc_ok.stdout)

      # Introduce syntax error in src/service.py
      _write_text(service_py, "def broken_service(:\n")
      with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
        out_bad = io.StringIO()
        err_bad = io.StringIO()
        code_bad = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir], stdout=out_bad, stderr=err_bad
        )
        self.assertNotEqual(code_bad, 0)
        self.assertIn("src/service.py", err_bad.getvalue())
        self.assertIn("SyntaxError", err_bad.getvalue())

      proc_bad_src = subprocess.run(
          [sys.executable, STATIC_CHECK],
          capture_output=True,
          text=True,
          cwd=tmpdir,
          env=sub_env,
      )
      self.assertNotEqual(proc_bad_src.returncode, 0)

      # Fix src/service.py and introduce syntax error in root manage.py
      _write_text(service_py, "def run():\n    return 1\n")
      _write_text(manage_py, "def broken_root(:\n")
      with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
        out_root = io.StringIO()
        err_root = io.StringIO()
        code_root = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir], stdout=out_root, stderr=err_root
        )
        self.assertNotEqual(code_root, 0)
        self.assertIn("manage.py", err_root.getvalue())
        self.assertIn("SyntaxError", err_root.getvalue())

      proc_bad_root = subprocess.run(
          [sys.executable, STATIC_CHECK],
          capture_output=True,
          text=True,
          cwd=tmpdir,
          env=sub_env,
      )
      self.assertNotEqual(proc_bad_root.returncode, 0)

  def test_static_check_skips_all_twelve_ignored_dirs_and_direct_targets(self):
    """GH-275: Broken .py files in any of the 12 ignored dirs do not fail StaticCheckScript."""
    with tempfile.TemporaryDirectory() as tmpdir:
      _write_text(
          os.path.join(tmpdir, "src", "ok.py"),
          "def ok():\n    return True\n",
      )
      for ignored in sorted(EXPECTED_SKIP_SCAN_DIRS):
        _write_text(
            os.path.join(tmpdir, ignored, "broken.py"),
            "def broken(:\n",
        )
      _write_text(
          os.path.join(tmpdir, "frontend", "node_modules", "bad.py"),
          "def broken_nested(:\n",
      )

      with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
        out = io.StringIO()
        err = io.StringIO()
        code = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir], stdout=out, stderr=err
        )
        self.assertEqual(code, 0, f"Unexpected failure: {out.getvalue()}\n{err.getvalue()}")
        self.assertIn("scanned 1 files", out.getvalue())
        self.assertEqual(err.getvalue(), "")

        out_direct = io.StringIO()
        err_direct = io.StringIO()
        code_direct = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir, ".venv", "node_modules" + os.sep, ".workflow"],
            stdout=out_direct,
            stderr=err_direct,
        )
        self.assertEqual(code_direct, 0)
        self.assertIn("No Python files found to inspect.", out_direct.getvalue())
        self.assertEqual(err_direct.getvalue(), "")

      sub_env = dict(os.environ)
      sub_env["STATIC_CHECK_FORCE_NO_LINTER"] = "1"
      proc = subprocess.run(
          [sys.executable, STATIC_CHECK],
          capture_output=True,
          text=True,
          cwd=tmpdir,
          env=sub_env,
      )
      self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
      self.assertIn("scanned 1 files", proc.stdout)

  def test_static_check_non_python_repo_exits_zero_for_ast_and_external_linter(self):
    """GH-275: Non-Python consumer repos exit 0 cleanly under both AST and external linters."""
    with tempfile.TemporaryDirectory() as tmpdir:
      _write_text(os.path.join(tmpdir, "README.md"), "# JS Project\n")
      _write_text(os.path.join(tmpdir, "src", "index.js"), "export const x = 1;\n")
      _write_text(
          os.path.join(tmpdir, "node_modules", "pkg", "broken.py"),
          "def broken(:\n",
      )

      with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
        out_ast = io.StringIO()
        err_ast = io.StringIO()
        code_ast = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir], stdout=out_ast, stderr=err_ast
        )
        self.assertEqual(code_ast, 0)
        self.assertIn("No Python files found to inspect.", out_ast.getvalue())
        self.assertEqual(err_ast.getvalue(), "")

      with (
          mock.patch.object(
              static_check, "find_linter", return_value=("ruff", "/fake/ruff")
          ),
          mock.patch.object(static_check.subprocess, "run") as mock_run,
      ):
        out_ext = io.StringIO()
        err_ext = io.StringIO()
        code_ext = static_check.StaticCheckScript().execute(
            ["--repo-root", tmpdir], stdout=out_ext, stderr=err_ext
        )
        self.assertEqual(code_ext, 0)
        self.assertIn("No Python files found to inspect.", out_ext.getvalue())
        self.assertEqual(err_ext.getvalue(), "")
        mock_run.assert_not_called()

  def test_external_linter_ruff_and_flake8_extend_exclude_cwd_and_direct_ignored_filter(self):
    """GH-275: ruff and flake8 receive --extend-exclude, cwd=repo_root, and filtered targets."""
    expected_exclude = "--extend-exclude=" + ",".join(sorted(EXPECTED_SKIP_SCAN_DIRS))

    with tempfile.TemporaryDirectory() as tmpdir:
      abs_root = os.path.abspath(tmpdir)
      _write_text(os.path.join(tmpdir, "src", "ok.py"), "x = 1\n")
      _write_text(os.path.join(tmpdir, ".venv", "bad.py"), "def bad(:\n")
      _write_text(os.path.join(tmpdir, "node_modules", "bad.py"), "def bad(:\n")

      for tool_name, tool_path in (
          ("ruff", "/fake/ruff"),
          ("flake8", "/fake/flake8"),
      ):
        fake_proc = subprocess.CompletedProcess(
            args=[tool_path], returncode=0, stdout="", stderr=""
        )
        with (
            mock.patch.object(
                static_check, "find_linter", return_value=(tool_name, tool_path)
            ),
            mock.patch.object(
                static_check.subprocess, "run", return_value=fake_proc
            ) as mock_run,
        ):
          # Default targets -> [repo_root]
          out = io.StringIO()
          err = io.StringIO()
          code = static_check.StaticCheckScript().execute(
              ["--repo-root", tmpdir], stdout=out, stderr=err
          )
          self.assertEqual(code, 0)
          self.assertIn(f"PASSED (using {tool_name})", out.getvalue())
          mock_run.assert_called_once()
          cmd = mock_run.call_args[0][0]
          kwargs = mock_run.call_args[1]
          self.assertEqual(kwargs.get("cwd"), abs_root)
          self.assertIn("--select=E9,F63,F7,F82", cmd)
          self.assertIn(expected_exclude, cmd)
          self.assertIn(abs_root, cmd)
          if tool_name == "ruff":
            self.assertEqual(cmd[:2], [tool_path, "check"])
          else:
            self.assertEqual(cmd[0], tool_path)
            self.assertNotIn("check", cmd)

          # Mixed explicit targets: ignored directories must be stripped
          mock_run.reset_mock()
          out_mix = io.StringIO()
          err_mix = io.StringIO()
          code_mix = static_check.StaticCheckScript().execute(
              ["--repo-root", tmpdir, "src", ".venv", "node_modules" + os.sep],
              stdout=out_mix,
              stderr=err_mix,
          )
          self.assertEqual(code_mix, 0)
          mock_run.assert_called_once()
          mix_cmd = mock_run.call_args[0][0]
          mix_kwargs = mock_run.call_args[1]
          self.assertEqual(mix_kwargs.get("cwd"), abs_root)
          self.assertIn("src", mix_cmd)
          self.assertNotIn(".venv", mix_cmd)
          self.assertNotIn("node_modules" + os.sep, mix_cmd)
          self.assertIn(expected_exclude, mix_cmd)

  def test_find_linter_checks_consumer_venv_and_honors_force_no_linter(self):
    """GH-275: find_linter probes consumer repo_root .venv/bin and honors force-no-linter."""
    with tempfile.TemporaryDirectory() as tmpdir:
      abs_root = os.path.abspath(tmpdir)
      venv_bin = os.path.join(abs_root, ".venv", "bin")
      ruff_bin = _write_text(os.path.join(venv_bin, "ruff"), "#!/bin/sh\nexit 0\n")
      os.chmod(ruff_bin, os.stat(ruff_bin).st_mode | stat.S_IEXEC)

      clean_env = dict(os.environ)
      clean_env.pop("STATIC_CHECK_FORCE_NO_LINTER", None)
      with (
          mock.patch.dict(os.environ, clean_env, clear=True),
          mock.patch.object(static_check.shutil, "which", return_value=None),
      ):
        self.assertEqual(
            static_check.find_linter(repo_root=tmpdir),
            ("ruff", ruff_bin),
        )

      with (
          mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}),
          mock.patch.object(static_check.shutil, "which", return_value="/usr/bin/ruff"),
      ):
        self.assertEqual(
            static_check.find_linter(repo_root=tmpdir),
            (None, None),
        )

      os.remove(ruff_bin)
      flake8_bin = _write_text(os.path.join(venv_bin, "flake8"), "#!/bin/sh\nexit 0\n")
      os.chmod(flake8_bin, os.stat(flake8_bin).st_mode | stat.S_IEXEC)

      with (
          mock.patch.dict(os.environ, clean_env, clear=True),
          mock.patch.object(static_check.shutil, "which", return_value=None),
      ):
        self.assertEqual(
            static_check.find_linter(repo_root=tmpdir),
            ("flake8", flake8_bin),
        )

  def test_run_external_linter_handles_single_pass_generator_targets(self):
    """GH-275: run_external_linter materializes single-pass generators before filtering."""
    expected_exclude = "--extend-exclude=" + ",".join(sorted(EXPECTED_SKIP_SCAN_DIRS))

    with tempfile.TemporaryDirectory() as tmpdir:
      abs_root = os.path.abspath(tmpdir)
      _write_text(os.path.join(tmpdir, "src", "ok.py"), "x = 1\n")
      _write_text(os.path.join(tmpdir, "other", "bad.py"), "def bad(:\n")

      for tool_name, tool_path in (
          ("ruff", "/fake/ruff"),
          ("flake8", "/fake/flake8"),
      ):
        fake_proc = subprocess.CompletedProcess(
            args=[tool_path], returncode=0, stdout="", stderr=""
        )
        with mock.patch.object(
            static_check.subprocess, "run", return_value=fake_proc
        ) as mock_run:
          out = io.StringIO()
          err = io.StringIO()
          code = static_check.run_external_linter(
              tool_name,
              tool_path,
              (p for p in ["src"]),
              stdout=out,
              stderr=err,
              repo_root=tmpdir,
          )
          self.assertEqual(code, 0)
          mock_run.assert_called_once()
          cmd = mock_run.call_args[0][0]
          expected_cmd = (
              [tool_path, "check", "src", "--select=E9,F63,F7,F82", expected_exclude]
              if tool_name == "ruff"
              else [tool_path, "src", "--select=E9,F63,F7,F82", expected_exclude]
          )
          self.assertEqual(cmd, expected_cmd)
          self.assertEqual(mock_run.call_args[1].get("cwd"), abs_root)

          mock_run.reset_mock()
          code_iter = static_check.run_external_linter(
              tool_name,
              tool_path,
              iter([Path("src")]),
              stdout=io.StringIO(),
              stderr=io.StringIO(),
              repo_root=tmpdir,
          )
          self.assertEqual(code_iter, 0)
          mock_run.assert_called_once()
          self.assertEqual(mock_run.call_args[0][0], expected_cmd)

  def test_run_external_linter_deduplicates_canonical_targets_and_skips_non_python_files(self):
    """GH-275: External linter deduplicates canonical paths and skips non-.py or missing targets."""
    expected_exclude = "--extend-exclude=" + ",".join(sorted(EXPECTED_SKIP_SCAN_DIRS))

    with tempfile.TemporaryDirectory() as tmpdir:
      abs_root = os.path.abspath(tmpdir)
      abs_src = os.path.join(abs_root, "src")
      _write_text(os.path.join(abs_src, "ok.py"), "x = 1\n")
      _write_text(os.path.join(abs_root, "README.md"), "# docs\n")
      _write_text(os.path.join(abs_root, ".venv", "bad.py"), "def bad(:\n")

      for tool_name, tool_path in (
          ("ruff", "/fake/ruff"),
          ("flake8", "/fake/flake8"),
      ):
        fake_proc = subprocess.CompletedProcess(
            args=[tool_path], returncode=0, stdout="", stderr=""
        )
        with mock.patch.object(
            static_check.subprocess, "run", return_value=fake_proc
        ) as mock_run:
          # Canonical deduplication: ["src", "./src", abs_src] -> single "src" target
          code_dedup = static_check.run_external_linter(
              tool_name,
              tool_path,
              ["src", "./src", abs_src],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
              repo_root=tmpdir,
          )
          self.assertEqual(code_dedup, 0)
          mock_run.assert_called_once()
          dedup_cmd = mock_run.call_args[0][0]
          self.assertEqual(dedup_cmd.count("src"), 1)
          self.assertNotIn("./src", dedup_cmd)
          self.assertNotIn(abs_src, dedup_cmd)
          self.assertIn(expected_exclude, dedup_cmd)

          # Skip explicit non-.py file, missing file, and '..' into ignored dir
          mock_run.reset_mock()
          code_filter = static_check.run_external_linter(
              tool_name,
              tool_path,
              ["src/ok.py", "README.md", "does_not_exist.py", "src/../.venv"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
              repo_root=tmpdir,
          )
          self.assertEqual(code_filter, 0)
          mock_run.assert_called_once()
          filter_cmd = mock_run.call_args[0][0]
          self.assertIn("src/ok.py", filter_cmd)
          self.assertNotIn("README.md", filter_cmd)
          self.assertNotIn("does_not_exist.py", filter_cmd)
          self.assertNotIn("src/../.venv", filter_cmd)

          # Empty and non-existent target lists exit 0 without running linter
          mock_run.reset_mock()
          out_empty = io.StringIO()
          code_empty = static_check.run_external_linter(
              tool_name,
              tool_path,
              [],
              stdout=out_empty,
              stderr=io.StringIO(),
              repo_root=tmpdir,
          )
          self.assertEqual(code_empty, 0)
          self.assertIn("No Python files found to inspect.", out_empty.getvalue())
          mock_run.assert_not_called()

          out_missing = io.StringIO()
          code_missing = static_check.run_external_linter(
              tool_name,
              tool_path,
              ["does_not_exist.py"],
              stdout=out_missing,
              stderr=io.StringIO(),
              repo_root=tmpdir,
          )
          self.assertEqual(code_missing, 0)
          self.assertIn("No Python files found to inspect.", out_missing.getvalue())
          mock_run.assert_not_called()

  def test_run_ast_fallback_handles_cross_drive_relpath_value_error_and_empty_targets(self):
    """GH-275: run_ast_fallback falls back to full path on cross-drive ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
      valid_py = _write_text(os.path.join(tmpdir, "valid.py"), "VALUE = 1\n")
      broken_py = _write_text(os.path.join(tmpdir, "broken.py"), "def broken(:\n")

      with mock.patch.object(
          static_check.os.path,
          "relpath",
          side_effect=ValueError("path is on mount 'D:', start on mount 'C:'"),
      ):
        out_ok = io.StringIO()
        err_ok = io.StringIO()
        code_ok = static_check.run_ast_fallback(
            [valid_py], stdout=out_ok, stderr=err_ok, repo_root=tmpdir
        )
        self.assertEqual(code_ok, 0)
        self.assertIn("PASSED", out_ok.getvalue())
        self.assertEqual(err_ok.getvalue(), "")

        out_bad = io.StringIO()
        err_bad = io.StringIO()
        code_bad = static_check.run_ast_fallback(
            [broken_py], stdout=out_bad, stderr=err_bad, repo_root=tmpdir
        )
        self.assertEqual(code_bad, 1)
        self.assertIn(broken_py, err_bad.getvalue())
        self.assertIn("SyntaxError", err_bad.getvalue())

      out_empty = io.StringIO()
      err_empty = io.StringIO()
      code_empty = static_check.run_ast_fallback(
          [], stdout=out_empty, stderr=err_empty, repo_root=tmpdir
      )
      self.assertEqual(code_empty, 0)
      self.assertIn("No Python files found to inspect.", out_empty.getvalue())
      self.assertEqual(err_empty.getvalue(), "")

  def test_static_check_scans_repo_under_ignored_ancestor_and_prefers_venv_ruff_over_path(self):
    """GH-275: StaticCheckScript scans repos under ignored ancestors and prefers .venv/bin/ruff."""
    with tempfile.TemporaryDirectory() as tmpdir:
      consumer_repo = os.path.abspath(os.path.join(tmpdir, ".tmp", "consumer_repo"))
      _write_text(os.path.join(consumer_repo, "src", "ok.py"), "x = 1\n")
      _write_text(os.path.join(consumer_repo, ".venv", "broken.py"), "def bad(:\n")

      with mock.patch.dict(os.environ, {"STATIC_CHECK_FORCE_NO_LINTER": "1"}):
        out = io.StringIO()
        err = io.StringIO()
        code = static_check.StaticCheckScript().execute(
            ["--repo-root", consumer_repo], stdout=out, stderr=err
        )
        self.assertEqual(code, 0, f"{out.getvalue()}\n{err.getvalue()}")
        self.assertIn("scanned 1 files", out.getvalue())
        self.assertEqual(err.getvalue(), "")

      fake_proc = subprocess.CompletedProcess(
          args=["/fake/ruff"], returncode=0, stdout="", stderr=""
      )
      with mock.patch.object(
          static_check.subprocess, "run", return_value=fake_proc
      ) as mock_run:
        code_ext = static_check.run_external_linter(
            "ruff",
            "/fake/ruff",
            [consumer_repo],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            repo_root=consumer_repo,
        )
        self.assertEqual(code_ext, 0)
        mock_run.assert_called_once()
        self.assertIn(consumer_repo, mock_run.call_args[0][0])
        self.assertEqual(mock_run.call_args[1].get("cwd"), consumer_repo)

      # .venv/bin/ruff takes precedence even when PATH also provides ruff
      ruff_bin = _write_text(
          os.path.join(consumer_repo, ".venv", "bin", "ruff"),
          "#!/bin/sh\nexit 0\n",
      )
      os.chmod(ruff_bin, os.stat(ruff_bin).st_mode | stat.S_IEXEC)
      clean_env = dict(os.environ)
      clean_env.pop("STATIC_CHECK_FORCE_NO_LINTER", None)
      with (
          mock.patch.dict(os.environ, clean_env, clear=True),
          mock.patch.object(
              static_check.shutil, "which", return_value="/usr/local/bin/ruff"
          ),
      ):
        self.assertEqual(
            static_check.find_linter(repo_root=consumer_repo),
            ("ruff", ruff_bin),
        )


if __name__ == "__main__":
  unittest.main()
