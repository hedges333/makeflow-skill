#!/usr/bin/env python3
"""Unit tests for safe_copy.py (SCAR-SYS-05)."""

import errno
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.safe_copy import safe_copy_file, safe_copy_tree


class TestSafeCopy(unittest.TestCase):
  """Test suite for safe cross-user file promotion helpers."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.src_dir = os.path.join(self.temp_dir.name, "src")
    self.dst_dir = os.path.join(self.temp_dir.name, "dst")
    os.makedirs(self.src_dir, exist_ok=True)

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_safe_copy_file_contents(self):
    """Verify safe_copy_file accurately copies file content."""
    src_file = os.path.join(self.src_dir, "test.txt")
    dst_file = os.path.join(self.dst_dir, "test.txt")
    content = "Hello, SCAR-SYS-05!\n"

    with open(src_file, "w") as f:
      f.write(content)

    result = safe_copy_file(src_file, dst_file)
    self.assertEqual(result, dst_file)
    self.assertTrue(os.path.isfile(dst_file))

    with open(dst_file) as f:
      self.assertEqual(f.read(), content)

  def test_safe_copy_file_into_directory(self):
    """Verify safe_copy_file accepts destination directory and preserves filename."""
    src_file = os.path.join(self.src_dir, "sample.dat")
    os.makedirs(self.dst_dir, exist_ok=True)
    with open(src_file, "w") as f:
      f.write("data-1234")

    result = safe_copy_file(src_file, self.dst_dir)
    expected_dst = os.path.join(self.dst_dir, "sample.dat")
    self.assertEqual(result, expected_dst)
    self.assertTrue(os.path.isfile(expected_dst))

  def test_safe_copy_file_creates_nested_parents(self):
    """Verify safe_copy_file creates non-existent intermediate directories."""
    src_file = os.path.join(self.src_dir, "nested.txt")
    dst_file = os.path.join(self.dst_dir, "a", "b", "c", "nested.txt")
    with open(src_file, "w") as f:
      f.write("deep file")

    result = safe_copy_file(src_file, dst_file)
    self.assertEqual(result, dst_file)
    self.assertTrue(os.path.isfile(dst_file))

  def test_safe_copy_file_missing_source(self):
    """Verify safe_copy_file raises FileNotFoundError for missing source."""
    missing_src = os.path.join(self.src_dir, "nonexistent.txt")
    dst_file = os.path.join(self.dst_dir, "out.txt")
    with self.assertRaises(FileNotFoundError):
      safe_copy_file(missing_src, dst_file)

  def test_safe_copy_file_eperm_resilience(self):
    """Verify safe_copy_file succeeds when os.chmod/os.utime raise EPERM, where copy2 fails."""
    src_file = os.path.join(self.src_dir, "eperm_test.txt")
    dst_copy2 = os.path.join(self.dst_dir, "dst_copy2.txt")
    dst_safe = os.path.join(self.dst_dir, "dst_safe.txt")
    os.makedirs(self.dst_dir, exist_ok=True)

    with open(src_file, "w") as f:
      f.write("content requiring ownership to copystat")

    def mock_eperm(*args, **kwargs):
      raise PermissionError(errno.EPERM, "Operation not permitted")

    # Simulate cross-user EPERM on metadata alteration (copystat)
    with patch("os.chmod", side_effect=mock_eperm), patch("os.utime", side_effect=mock_eperm):
      # Standard shutil.copy2 MUST fail under EPERM
      with self.assertRaises((PermissionError, OSError)):
        shutil.copy2(src_file, dst_copy2)

      # safe_copy_file MUST succeed under EPERM because it bypasses copystat
      result = safe_copy_file(src_file, dst_safe)
      self.assertEqual(result, dst_safe)
      self.assertTrue(os.path.isfile(dst_safe))
      with open(dst_safe) as f:
        self.assertEqual(f.read(), "content requiring ownership to copystat")

  def test_safe_copy_tree_structure(self):
    """Verify safe_copy_tree copies entire recursive hierarchy."""
    sub1 = os.path.join(self.src_dir, "sub1")
    sub2 = os.path.join(sub1, "sub2")
    os.makedirs(sub2, exist_ok=True)

    with open(os.path.join(self.src_dir, "f1.txt"), "w") as f:
      f.write("root file")
    with open(os.path.join(sub1, "f2.txt"), "w") as f:
      f.write("level 1 file")
    with open(os.path.join(sub2, "f3.txt"), "w") as f:
      f.write("level 2 file")

    safe_copy_tree(self.src_dir, self.dst_dir)

    self.assertTrue(os.path.isfile(os.path.join(self.dst_dir, "f1.txt")))
    self.assertTrue(os.path.isfile(os.path.join(self.dst_dir, "sub1", "f2.txt")))
    self.assertTrue(os.path.isfile(os.path.join(self.dst_dir, "sub1", "sub2", "f3.txt")))

    with open(os.path.join(self.dst_dir, "sub1", "sub2", "f3.txt")) as f:
      self.assertEqual(f.read(), "level 2 file")

  def test_safe_copy_tree_eperm_resilience(self):
    """Verify safe_copy_tree succeeds under simulated metadata EPERM."""
    sub1 = os.path.join(self.src_dir, "sub")
    os.makedirs(sub1, exist_ok=True)
    with open(os.path.join(sub1, "file.txt"), "w") as f:
      f.write("tree content")

    def mock_eperm(*args, **kwargs):
      raise PermissionError(errno.EPERM, "Operation not permitted")

    with patch("os.chmod", side_effect=mock_eperm), patch("os.utime", side_effect=mock_eperm):
      safe_copy_tree(self.src_dir, self.dst_dir)
      dst_file = os.path.join(self.dst_dir, "sub", "file.txt")
      self.assertTrue(os.path.isfile(dst_file))
      with open(dst_file) as f:
        self.assertEqual(f.read(), "tree content")

  def test_safe_copy_tree_invalid_src(self):
    """Verify safe_copy_tree raises NotADirectoryError when source is invalid."""
    not_a_dir = os.path.join(self.src_dir, "fake_dir")
    with self.assertRaises(NotADirectoryError):
      safe_copy_tree(not_a_dir, self.dst_dir)


if __name__ == "__main__":
  unittest.main()
