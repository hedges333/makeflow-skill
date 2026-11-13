#!/usr/bin/env python3
"""Unit tests for env_preflight.py (SCAR-SYS-06)."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.env_preflight import (
    STANDARD_ENV_VARS,
    main,
    preflight_env_vars,
    sanitize_runtime_env,
    validate_runtime_path,
)


class TestEnvPreflight(unittest.TestCase):
  """Comprehensive test suite for runtime environment path pre-flight."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.test_root = self.temp_dir.name

  def tearDown(self):
    # Ensure any read-only subdirectories have permissions restored for cleanup
    for root, dirs, _files in os.walk(self.test_root):
      for d in dirs:
        dir_path = os.path.join(root, d)
        try:
          os.chmod(dir_path, stat.S_IRWXU)
        except OSError:
          pass
    self.temp_dir.cleanup()

  def test_valid_directory_verification(self):
    """TestCase 1: Real Valid Directory Verification."""
    valid_dir = os.path.join(self.test_root, "valid_temp")
    os.makedirs(valid_dir, exist_ok=True)

    result = validate_runtime_path(valid_dir)
    self.assertEqual(result, os.path.abspath(valid_dir))
    # Assert writability probe passes cleanly without leaving lingering files
    self.assertEqual(os.listdir(valid_dir), [])

  def test_missing_path_handling(self):
    """TestCase 2: Missing Path Handling."""
    missing_dir = os.path.join(self.test_root, "missing_subdir")

    # With create_if_missing=False, must raise FileNotFoundError
    with self.assertRaises(FileNotFoundError):
      validate_runtime_path(missing_dir, create_if_missing=False)

    # With create_if_missing=True, creates directory with caller permissions
    created = validate_runtime_path(missing_dir, create_if_missing=True)
    self.assertEqual(created, os.path.abspath(missing_dir))
    self.assertTrue(os.path.isdir(missing_dir))

  def test_non_directory_path_collision(self):
    """TestCase 3: Non-Directory Path Collision."""
    file_path = os.path.join(self.test_root, "regular_file.txt")
    with open(file_path, "w") as f:
      f.write("I am a file, not a directory.")

    with self.assertRaises(NotADirectoryError):
      validate_runtime_path(file_path)

  def test_read_only_directory_permission_boundary(self):
    """TestCase 4: Real Permission Boundary (Read-Only Directory)."""
    ro_dir = os.path.join(self.test_root, "readonly_dir")
    os.makedirs(ro_dir, exist_ok=True)
    # Set permissions to 0555 (read + execute, no write)
    os.chmod(
        ro_dir,
        stat.S_IRUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH,
    )

    try:
      with self.assertRaises(PermissionError):
        validate_runtime_path(ro_dir, test_writable=True)
    finally:
      # Restore write permissions so cleanup succeeds
      os.chmod(ro_dir, stat.S_IRWXU)

  def test_environment_sanitization_and_fallback(self):
    """TestCase 5: Environment Sanitization and Fallback Resolution."""
    safe_fallback = os.path.join(self.test_root, "safe_fallback")
    os.makedirs(safe_fallback, exist_ok=True)

    valid_sub = os.path.join(self.test_root, "valid_sub")
    os.makedirs(valid_sub, exist_ok=True)

    isolated_env = {
        "TMPDIR": "/nonexistent/fake/directory/12345",
        "CACHE_DIR": os.path.join(self.test_root, "also_missing"),
        "TEMP": valid_sub,
        "OTHER_VAR": "preserve_me",
    }

    # Verify preflight_env_vars resolution
    resolved = preflight_env_vars(env=isolated_env, fallback_dir=safe_fallback)
    self.assertEqual(resolved["TMPDIR"], os.path.abspath(safe_fallback))
    self.assertEqual(resolved["CACHE_DIR"], os.path.abspath(safe_fallback))
    self.assertEqual(resolved["TEMP"], os.path.abspath(valid_sub))

    # Verify sanitize_runtime_env
    sanitized = sanitize_runtime_env(
        env=isolated_env, fallback_dir=safe_fallback
    )
    self.assertEqual(sanitized["TMPDIR"], os.path.abspath(safe_fallback))
    self.assertEqual(sanitized["CACHE_DIR"], os.path.abspath(safe_fallback))
    self.assertEqual(sanitized["TEMP"], os.path.abspath(valid_sub))
    self.assertEqual(sanitized["OTHER_VAR"], "preserve_me")

  def test_native_process_interception(self):
    """TestCase 6: End-to-End Native Process Interception."""
    # Child script simulating a native routine requiring a writable tempdir
    sim_script = (
        "import os, tempfile\n"
        "t = os.environ.get('TMPDIR')\n"
        "with tempfile.NamedTemporaryFile(dir=t, delete=True) as f:\n"
        "  f.write(b'data')\n"
        "print('NATIVE_INIT_SUCCESS')\n"
    )

    bad_env = dict(os.environ)
    bad_env["TMPDIR"] = "/nonexistent/path/for/native/worker"

    # Without pre-flight / sanitation, child process fails
    proc_bad = subprocess.run(
        [sys.executable, "-c", sim_script],
        env=bad_env,
        capture_output=True,
        text=True,
    )
    self.assertNotEqual(proc_bad.returncode, 0)

    # With pre-flight sanitation, child process succeeds
    safe_env = sanitize_runtime_env(env=bad_env)
    proc_good = subprocess.run(
        [sys.executable, "-c", sim_script],
        env=safe_env,
        capture_output=True,
        text=True,
    )
    self.assertEqual(proc_good.returncode, 0)
    self.assertIn("NATIVE_INIT_SUCCESS", proc_good.stdout)

  def test_cli_interface_and_exit_codes(self):
    """TestCase 7: CLI Interface & Exit Codes."""
    script_path = os.path.join(_SCRIPTS_DIR, "env_preflight.py")

    # Test clean environment
    clean_env = dict(os.environ)
    valid_tmp = os.path.join(self.test_root, "cli_valid")
    os.makedirs(valid_tmp, exist_ok=True)
    clean_env["TMPDIR"] = valid_tmp
    clean_env["CACHE_DIR"] = valid_tmp

    proc_ok = subprocess.run(
        [sys.executable, script_path],
        env=clean_env,
        capture_output=True,
        text=True,
    )
    self.assertEqual(proc_ok.returncode, 0)
    self.assertIn("valid", proc_ok.stdout)

    # Test broken environment
    bad_env = dict(os.environ)
    bad_env["TMPDIR"] = "/path/guaranteed/to/fail/preflight"
    proc_err = subprocess.run(
        [sys.executable, script_path],
        env=bad_env,
        capture_output=True,
        text=True,
    )
    self.assertNotEqual(proc_err.returncode, 0)
    self.assertIn("TMPDIR", proc_err.stderr)


if __name__ == "__main__":
  unittest.main()
