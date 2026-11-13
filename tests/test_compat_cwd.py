#!/usr/bin/env python3
"""Hermetic unit tests for arbitrary CWD execution validation with timeout (GH-62)."""

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
for _p in (_REPO_ROOT, _TESTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

from test_cross_agent_compatibility import validate_arbitrary_cwd_execution


class TestArbitraryCwdExecution(unittest.TestCase):
  """Tests timeout parameterization and exception trapping in validate_arbitrary_cwd_execution."""

  def setUp(self):
    self.env_patcher = patch.dict(os.environ)
    self.env_patcher.start()
    if "_COMPAT_CHILD" in os.environ:
      del os.environ["_COMPAT_CHILD"]

  def tearDown(self):
    self.env_patcher.stop()

  @patch("test_cross_agent_compatibility.subprocess.run")
  def test_default_timeout_passed_to_subprocess_run(self, mock_run):
    """Verifies validate_arbitrary_cwd_execution by default passes timeout=30.0."""
    mock_run.return_value = subprocess.CompletedProcess(
        args=["python3", "dummy.py"],
        returncode=0,
        stdout="All cross-agent configuration files validated successfully!\n",
        stderr="",
    )
    errors = validate_arbitrary_cwd_execution()
    self.assertEqual(errors, [])
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    self.assertIn("timeout", kwargs)
    self.assertEqual(kwargs["timeout"], 30.0)

  @patch("test_cross_agent_compatibility.subprocess.run")
  def test_custom_timeout_passed_to_subprocess_run(self, mock_run):
    """Verifies validate_arbitrary_cwd_execution accepts custom timeout and passes it to subprocess.run."""
    mock_run.return_value = subprocess.CompletedProcess(
        args=["python3", "dummy.py"],
        returncode=0,
        stdout="All cross-agent configuration files validated successfully!\n",
        stderr="",
    )
    errors = validate_arbitrary_cwd_execution(timeout=10.0)
    self.assertEqual(errors, [])
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    self.assertIn("timeout", kwargs)
    self.assertEqual(kwargs["timeout"], 10.0)

  @patch("test_cross_agent_compatibility.subprocess.run")
  def test_timeout_expired_caught_and_returns_error(self, mock_run):
    """Verifies validate_arbitrary_cwd_execution catches TimeoutExpired and returns error list."""
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["python3", "dummy.py"], timeout=30.0
    )
    errors = validate_arbitrary_cwd_execution()
    self.assertTrue(len(errors) > 0)
    self.assertTrue(
        any("timed out" in err.lower() or "timeout" in err.lower() for err in errors)
    )

  @patch("test_cross_agent_compatibility.subprocess.run")
  def test_subprocess_error_and_oserror_caught_and_returns_error(self, mock_run):
    """Verifies validate_arbitrary_cwd_execution catches (SubprocessError, OSError) and returns error list."""
    mock_run.side_effect = OSError("Simulated OS execution failure")
    errors = validate_arbitrary_cwd_execution()
    self.assertTrue(len(errors) > 0)
    self.assertTrue(
        any("simulated os execution failure" in err.lower() for err in errors)
    )

    mock_run.side_effect = subprocess.SubprocessError("Simulated subprocess failure")
    errors2 = validate_arbitrary_cwd_execution()
    self.assertTrue(len(errors2) > 0)
    self.assertTrue(
        any("simulated subprocess failure" in err.lower() for err in errors2)
    )

  def test_real_execution_succeeds(self):
    """Verifies unmocked real execution of validate_arbitrary_cwd_execution succeeds."""
    errors = validate_arbitrary_cwd_execution()
    self.assertEqual(errors, [])


if __name__ == "__main__":
  unittest.main()
