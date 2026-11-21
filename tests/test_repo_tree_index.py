#!/usr/bin/env python3
"""Unit, Git boundary, and CLI/Makefile tests for repository Git file tree indexing (GH-246)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.repo_tree import (  # noqa: E402
    DEFAULT_REPO_TREE_THRESHOLD,
    RepoTreeCollector,
    RepoTreeError,
    RepoTreeIndex,
)
from lib.workflow_gates import (  # noqa: E402
    WorkflowGatesScript,
    generate_state_makefile,
)


def _init_git_repo(repo_dir: str) -> None:
  """Initializes a minimal Git repository with local committer metadata."""
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
      ["git", "config", "user.name", "Test User"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )


class TestRepoTreeIndexUnit(unittest.TestCase):
  """Pure in-memory unit tests for RepoTreeIndex and RepoTreeCollector (GH-246)."""

  def test_empty_greenfield_repo_index(self) -> None:
    """Empty greenfield repos report explicit 0 of 0 files without errors."""

    def fake_runner(cmd, cwd):
      self.assertEqual(
          cmd,
          ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
      )
      self.assertEqual(cwd, "/virtual/greenfield")
      return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    index = RepoTreeCollector("/virtual/greenfield", runner=fake_runner).collect()
    self.assertIsInstance(index, RepoTreeIndex)
    self.assertEqual(index.repo_root, "/virtual/greenfield")
    self.assertEqual(index.files, ())
    self.assertEqual(index.total_count, 0)
    self.assertEqual(index.filtered_count, 0)
    self.assertEqual(index.prefix, "")
    self.assertEqual(index.top_level_counts, {})
    self.assertFalse(index.exceeds_threshold())

    formatted = index.format_index()
    self.assertIn("0 of 0 files", formatted)

    payload = index.to_dict()
    self.assertEqual(payload["totalCount"], 0)
    self.assertEqual(payload["filteredCount"], 0)
    self.assertEqual(payload["returnedCount"], 0)
    self.assertEqual(payload["denominator"], "0 of 0 files")
    self.assertIs(payload["triageRequired"], False)
    self.assertEqual(payload["files"], [])

  def test_sorted_deduplicated_nul_paths_with_spaces(self) -> None:
    """Collector sorts, deduplicates, and preserves spaces from NUL-delimited output."""

    def fake_runner(cmd, cwd):
      return subprocess.CompletedProcess(
          cmd,
          0,
          stdout="dir with space/z file.py\x00README.md\x00dir with space/z file.py\x00lib/a.py\x00",
          stderr="",
      )

    index = RepoTreeCollector("/virtual/repo", runner=fake_runner).collect()
    self.assertEqual(
        index.files,
        ("README.md", "dir with space/z file.py", "lib/a.py"),
    )
    self.assertEqual(index.total_count, 3)
    self.assertEqual(index.filtered_count, 3)
    self.assertEqual(
        index.top_level_counts,
        {".": 1, "dir with space": 1, "lib": 1},
    )

    formatted = index.format_index()
    self.assertIn("3 of 3 files", formatted)
    self.assertIn("dir with space/z file.py", formatted)
    self.assertIn("README.md", formatted)
    self.assertIn("lib/a.py", formatted)

  def test_prefix_filtering_preserves_total_denominator(self) -> None:
    """Prefix filtering restricts files to segment matches while keeping total_count."""

    def fake_runner(cmd, cwd):
      return subprocess.CompletedProcess(
          cmd,
          0,
          stdout="lib/a.py\x00lib/b.py\x00lib_extra/c.py\x00tests/test_a.py\x00",
          stderr="",
      )

    index = RepoTreeCollector("/virtual/repo", runner=fake_runner).collect(
        prefix="./lib/"
    )
    self.assertEqual(index.prefix, "lib")
    self.assertEqual(index.files, ("lib/a.py", "lib/b.py"))
    self.assertEqual(index.total_count, 4)
    self.assertEqual(index.filtered_count, 2)
    self.assertEqual(
        index.top_level_counts,
        {"lib": 2, "lib_extra": 1, "tests": 1},
    )
    formatted = index.format_index()
    self.assertIn("2 of 4 files", formatted)
    self.assertNotIn("lib_extra/c.py", formatted)

  def test_threshold_triage_and_explicit_options(self) -> None:
    """Threshold checks block silent truncation and support all four SCAR-TOOL-16 modes."""
    self.assertEqual(DEFAULT_REPO_TREE_THRESHOLD, 500)

    def fake_runner(cmd, cwd):
      return subprocess.CompletedProcess(
          cmd,
          0,
          stdout="lib/a.py\x00lib/b.py\x00tests/t1.py\x00README.md\x00",
          stderr="",
      )

    index = RepoTreeCollector("/virtual/large", runner=fake_runner).collect()
    self.assertFalse(index.exceeds_threshold(threshold=4))
    self.assertTrue(index.exceeds_threshold(threshold=3))

    triage_text = index.format_triage_summary(threshold=3)
    self.assertIn("4 of 4", triage_text)
    self.assertIn("lib", triage_text)
    self.assertIn("--prefix", triage_text)
    self.assertIn("--approved-limit", triage_text)
    self.assertIn("--count-only", triage_text)
    self.assertIn("--no-limit", triage_text)

    triage_dict = index.to_dict(threshold=3)
    self.assertIs(triage_dict["triageRequired"], True)
    self.assertEqual(triage_dict["files"], [])
    self.assertEqual(triage_dict["totalCount"], 4)
    self.assertEqual(triage_dict["filteredCount"], 4)
    self.assertEqual(triage_dict["returnedCount"], 0)

    limited_dict = index.to_dict(approved_limit=2, threshold=3)
    self.assertIs(limited_dict["triageRequired"], False)
    self.assertIs(limited_dict["truncated"], True)
    self.assertEqual(limited_dict["files"], ["README.md", "lib/a.py"])
    self.assertEqual(limited_dict["denominator"], "2 of 4 files")
    self.assertIn("2 of 4 files", limited_dict["formattedIndex"])
    self.assertIn("approved_limit=2", limited_dict["formattedIndex"])

    count_dict = index.to_dict(threshold=3, count_only=True)
    self.assertIs(count_dict["triageRequired"], False)
    self.assertIs(count_dict["countOnly"], True)
    self.assertEqual(count_dict["files"], [])
    self.assertEqual(count_dict["topLevelCounts"], {".": 1, "lib": 2, "tests": 1})

    full_dict = index.to_dict(threshold=3, no_limit=True)
    self.assertIs(full_dict["triageRequired"], False)
    self.assertIs(full_dict["noLimit"], True)
    self.assertIs(full_dict["truncated"], False)
    self.assertEqual(len(full_dict["files"]), 4)

  def test_invalid_inputs_and_runner_failures_raise(self) -> None:
    """Git failures and invalid prefix or threshold values raise explicit errors."""

    def failing_runner(cmd, cwd):
      return subprocess.CompletedProcess(
          cmd, 128, stdout="", stderr="fatal: not a git repository"
      )

    collector = RepoTreeCollector("/virtual/non-git", runner=failing_runner)
    with self.assertRaises(RepoTreeError):
      collector.collect()

    def ok_runner(cmd, cwd):
      return subprocess.CompletedProcess(cmd, 0, stdout="a.py\x00", stderr="")

    ok_collector = RepoTreeCollector("/virtual/repo", runner=ok_runner)
    for bad_prefix in ("../outside", "/etc/passwd", "a/../b"):
      with self.subTest(bad_prefix=bad_prefix):
        with self.assertRaises(RepoTreeError):
          ok_collector.collect(prefix=bad_prefix)

    idx = ok_collector.collect()
    for bad_int in (0, -1, True):
      with self.subTest(bad_int=bad_int):
        with self.assertRaises(ValueError):
          idx.exceeds_threshold(threshold=bad_int)
        with self.assertRaises(ValueError):
          idx.format_index(approved_limit=bad_int)


class TestRepoTreeGitBoundary(unittest.TestCase):
  """Hermetic Git repository boundary tests using TemporaryDirectory (GH-246)."""

  def test_real_git_ls_files_empty_greenfield_repo(self) -> None:
    """Fresh git init repository produces 0 of 0 files without error."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      _init_git_repo(tmp_dir)
      index = RepoTreeCollector(tmp_dir).collect()
      self.assertEqual(index.total_count, 0)
      self.assertEqual(index.filtered_count, 0)
      self.assertEqual(index.files, ())
      self.assertIn("0 of 0 files", index.format_index())

  def test_real_git_ls_files_tracked_untracked_spaces_and_gitignore(self) -> None:
    """Collects tracked and untracked files with spaces while excluding gitignored files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      _init_git_repo(tmp_dir)
      with open(os.path.join(tmp_dir, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("*.log\nignored_dir/\n")
      with open(os.path.join(tmp_dir, "tracked.py"), "w", encoding="utf-8") as f:
        f.write("print('tracked')\n")
      subprocess.run(
          ["git", "add", ".gitignore", "tracked.py"],
          cwd=tmp_dir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-m", "init", "-q"],
          cwd=tmp_dir,
          check=True,
          capture_output=True,
          text=True,
      )

      spaced_dir = os.path.join(tmp_dir, "sub dir")
      os.makedirs(spaced_dir)
      with open(os.path.join(spaced_dir, "new file.py"), "w", encoding="utf-8") as f:
        f.write("# untracked\n")
      with open(os.path.join(tmp_dir, "debug.log"), "w", encoding="utf-8") as f:
        f.write("ignored\n")
      ignored_dir = os.path.join(tmp_dir, "ignored_dir")
      os.makedirs(ignored_dir)
      with open(os.path.join(ignored_dir, "secret.txt"), "w", encoding="utf-8") as f:
        f.write("ignored\n")

      index = RepoTreeCollector(tmp_dir).collect()
      self.assertEqual(
          index.files,
          (".gitignore", "sub dir/new file.py", "tracked.py"),
      )
      self.assertEqual(index.total_count, 3)
      self.assertNotIn("debug.log", index.files)
      self.assertNotIn("ignored_dir/secret.txt", index.files)

  def test_real_non_git_directory_raises_repo_tree_error(self) -> None:
    """Non-Git workspace directory raises RepoTreeError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      with self.assertRaises(RepoTreeError):
        RepoTreeCollector(tmp_dir).collect()


class TestWorkflowGatesRepoTreeCliAndMakefile(unittest.TestCase):
  """Tests WorkflowGatesScript repo-tree subcommand and Makefile target generation (GH-246)."""

  def test_generate_state_makefile_declares_repo_tree(self) -> None:
    """generate_state_makefile declares repo-tree target, PHONY, and env vars."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      mk_text = generate_state_makefile(tmp_dir)
      for expected in (
          "repo-tree",
          "PATH_PREFIX ?=",
          "TREE_LIMIT ?=",
          "NO_LIMIT ?=",
          "COUNT_ONLY ?=",
          "repo-tree:",
      ):
        with self.subTest(token=expected):
          self.assertIn(expected, mk_text)

  def test_cli_repo_tree_empty_and_small_repos(self) -> None:
    """repo-tree exits 0 and prints explicit denominators on empty and small repos."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      _init_git_repo(tmp_dir)
      out = io.StringIO()
      err = io.StringIO()
      rc = WorkflowGatesScript().execute(
          args=["--repo-root", tmp_dir, "repo-tree"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, err.getvalue())
      self.assertIn("0 of 0 files", out.getvalue())

      with open(os.path.join(tmp_dir, "main.py"), "w", encoding="utf-8") as f:
        f.write("print('ok')\n")
      out2 = io.StringIO()
      err2 = io.StringIO()
      rc2 = WorkflowGatesScript().execute(
          args=["--repo-root", tmp_dir, "repo-tree"],
          stdout=out2,
          stderr=err2,
      )
      self.assertEqual(rc2, 0, err2.getvalue())
      self.assertIn("1 of 1 files", out2.getvalue())
      self.assertIn("main.py", out2.getvalue())

  def test_cli_repo_tree_threshold_triage_and_four_paths(self) -> None:
    """Exceeding threshold triggers SCAR-TOOL-16 reflex exit 2; all 4 triage paths exit 0."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      _init_git_repo(tmp_dir)
      os.makedirs(os.path.join(tmp_dir, "lib"))
      os.makedirs(os.path.join(tmp_dir, "tests"))
      for rel in ("README.md", "lib/a.py", "lib/b.py", "tests/test_a.py"):
        with open(os.path.join(tmp_dir, rel), "w", encoding="utf-8") as f:
          f.write("# file\n")

      # Unbounded default over threshold=2 triggers SCAR-TOOL-16 exit 2
      out_triage = io.StringIO()
      err_triage = io.StringIO()
      rc_triage = WorkflowGatesScript().execute(
          args=["--repo-root", tmp_dir, "repo-tree", "--threshold", "2"],
          stdout=out_triage,
          stderr=err_triage,
      )
      self.assertEqual(rc_triage, 2)
      self.assertIn("SCAR-ID: TOOL-16", err_triage.getvalue())
      self.assertIn("REPO_TREE_VOLUME_TRIAGE_REQUIRED", err_triage.getvalue())
      self.assertIn("4 of 4", out_triage.getvalue())
      self.assertNotIn("lib/a.py", out_triage.getvalue())

      # JSON reflex mode keeps stdout and stderr valid JSON
      out_json_triage = io.StringIO()
      err_json_triage = io.StringIO()
      rc_json_triage = WorkflowGatesScript().execute(
          args=["--repo-root", tmp_dir, "repo-tree", "--threshold", "2", "--json"],
          stdout=out_json_triage,
          stderr=err_json_triage,
      )
      self.assertEqual(rc_json_triage, 2)
      stdout_payload = json.loads(out_json_triage.getvalue())
      stderr_payload = json.loads(err_json_triage.getvalue())
      self.assertIs(stdout_payload["triageRequired"], True)
      self.assertEqual(stdout_payload["files"], [])
      self.assertEqual(stderr_payload["violation_code"], "REPO_TREE_VOLUME_TRIAGE_REQUIRED")

      # Path 1: --prefix lib (2 <= 2)
      out_prefix = io.StringIO()
      err_prefix = io.StringIO()
      rc_prefix = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmp_dir,
              "repo-tree",
              "--threshold",
              "2",
              "--prefix",
              "lib",
          ],
          stdout=out_prefix,
          stderr=err_prefix,
      )
      self.assertEqual(rc_prefix, 0, err_prefix.getvalue())
      self.assertIn("2 of 4 files", out_prefix.getvalue())
      self.assertIn("lib/a.py", out_prefix.getvalue())
      self.assertNotIn("tests/test_a.py", out_prefix.getvalue())

      # Path 2: --approved-limit 2
      out_limit = io.StringIO()
      err_limit = io.StringIO()
      rc_limit = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmp_dir,
              "repo-tree",
              "--threshold",
              "2",
              "--approved-limit",
              "2",
          ],
          stdout=out_limit,
          stderr=err_limit,
      )
      self.assertEqual(rc_limit, 0, err_limit.getvalue())
      self.assertIn("2 of 4 files", out_limit.getvalue())
      self.assertIn("approved_limit=2", out_limit.getvalue())

      # Path 3: --count-only
      out_count = io.StringIO()
      err_count = io.StringIO()
      rc_count = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmp_dir,
              "repo-tree",
              "--threshold",
              "2",
              "--count-only",
          ],
          stdout=out_count,
          stderr=err_count,
      )
      self.assertEqual(rc_count, 0, err_count.getvalue())
      self.assertIn("4 of 4", out_count.getvalue())
      self.assertNotIn("lib/a.py", out_count.getvalue())

      # Path 4: --no-limit
      out_nolimit = io.StringIO()
      err_nolimit = io.StringIO()
      rc_nolimit = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              tmp_dir,
              "repo-tree",
              "--threshold",
              "2",
              "--no-limit",
          ],
          stdout=out_nolimit,
          stderr=err_nolimit,
      )
      self.assertEqual(rc_nolimit, 0, err_nolimit.getvalue())
      self.assertIn("4 of 4 files", out_nolimit.getvalue())
      self.assertIn("tests/test_a.py", out_nolimit.getvalue())

      # --make-env hydration for PATH_PREFIX, TREE_LIMIT, NO_LIMIT, COUNT_ONLY
      with mock.patch.dict(
          os.environ,
          {"PATH_PREFIX": "lib", "TREE_LIMIT": "1"},
          clear=False,
      ):
        out_env = io.StringIO()
        err_env = io.StringIO()
        rc_env = WorkflowGatesScript().execute(
            args=["--repo-root", tmp_dir, "repo-tree", "--make-env"],
            stdout=out_env,
            stderr=err_env,
        )
        self.assertEqual(rc_env, 0, err_env.getvalue())
        self.assertIn("1 of 4 files", out_env.getvalue())
        self.assertIn("lib/a.py", out_env.getvalue())
        self.assertNotIn("lib/b.py", out_env.getvalue())

      with mock.patch.dict(os.environ, {"TREE_LIMIT": "0"}, clear=False):
        out_bad_env = io.StringIO()
        err_bad_env = io.StringIO()
        rc_bad_env = WorkflowGatesScript().execute(
            args=["--repo-root", tmp_dir, "repo-tree", "--make-env"],
            stdout=out_bad_env,
            stderr=err_bad_env,
        )
        self.assertNotEqual(rc_bad_env, 0)

  def test_cli_repo_tree_rejects_invalid_args_and_non_git(self) -> None:
    """Invalid integer flags, contradictory flags, and non-Git dirs return non-zero."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      for bad_args in (
          ["--repo-root", tmp_dir, "repo-tree", "--threshold", "0"],
          ["--repo-root", tmp_dir, "repo-tree", "--approved-limit", "0"],
          ["--repo-root", tmp_dir, "repo-tree", "--approved-limit", "2", "--no-limit"],
          ["--repo-root", tmp_dir, "repo-tree", "--count-only", "--no-limit"],
          ["--repo-root", tmp_dir, "repo-tree"],
      ):
        with self.subTest(bad_args=bad_args):
          out = io.StringIO()
          err = io.StringIO()
          rc = WorkflowGatesScript().execute(
              args=bad_args,
              stdout=out,
              stderr=err,
          )
          self.assertNotEqual(rc, 0)


if __name__ == "__main__":
  unittest.main()
