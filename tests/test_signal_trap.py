#!/usr/bin/env python3
"""Unit tests for signal_trap.py (SCAR-SYS-07)."""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.signal_trap import SignalTrap


class TestSignalTrap(unittest.TestCase):
  """Comprehensive test suite for POSIX signal trapping and cleanup."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.test_root = self.temp_dir.name
    # Save original handlers for test signals to guarantee restoration
    self._orig_sigterm = signal.getsignal(signal.SIGTERM)
    self._orig_sigint = signal.getsignal(signal.SIGINT)

  def tearDown(self):
    # Ensure signal handlers are restored even if a test failed
    try:
      signal.signal(signal.SIGTERM, self._orig_sigterm)
    except (ValueError, OSError):
      pass
    try:
      signal.signal(signal.SIGINT, self._orig_sigint)
    except (ValueError, OSError):
      pass
    self.temp_dir.cleanup()

  def test_initialization_and_signal_registration(self):
    """TestCase 1: SignalTrap initialization and registration of SIGTERM / SIGINT."""
    trap = SignalTrap()
    self.assertIn(signal.SIGTERM, trap.signals)
    self.assertIn(signal.SIGINT, trap.signals)
    self.assertFalse(trap.is_installed)

    trap.install()
    self.assertTrue(trap.is_installed)
    self.assertEqual(signal.getsignal(signal.SIGTERM), trap.handle_signal)
    self.assertEqual(signal.getsignal(signal.SIGINT), trap.handle_signal)

    trap.restore()
    self.assertFalse(trap.is_installed)

  def test_restoration_of_original_signal_handlers(self):
    """TestCase 2: Restoration of original signal handlers upon exit or restore()."""
    def custom_handler(_sig, _frame):
      pass

    signal.signal(signal.SIGTERM, custom_handler)
    self.assertEqual(signal.getsignal(signal.SIGTERM), custom_handler)

    with SignalTrap() as trap:
      self.assertEqual(signal.getsignal(signal.SIGTERM), trap.handle_signal)

    # Upon context exit, custom handler must be restored
    self.assertEqual(signal.getsignal(signal.SIGTERM), custom_handler)

  def test_cleanup_callbacks_lifo_order(self):
    """TestCase 3: Cleanup callbacks execute in reverse registration order (LIFO)."""
    execution_order = []

    trap = SignalTrap(exit_on_signal=False)
    trap.register_cleanup(lambda: execution_order.append("first"))
    trap.register_cleanup(lambda: execution_order.append("second"))
    trap.register_cleanup(lambda: execution_order.append("third"))

    errors = trap.execute_cleanups()
    self.assertEqual(errors, [])
    self.assertEqual(execution_order, ["third", "second", "first"])

  def test_exception_isolation_in_cleanups(self):
    """TestCase 4: Exception in one callback does not abort remaining cleanups."""
    execution_order = []

    def failing_callback():
      execution_order.append("failing")
      raise RuntimeError("Cleanup failure simulation")

    trap = SignalTrap(exit_on_signal=False)
    trap.register_cleanup(lambda: execution_order.append("first"))
    trap.register_cleanup(failing_callback)
    trap.register_cleanup(lambda: execution_order.append("third"))

    errors = trap.execute_cleanups()
    # All callbacks must have executed in LIFO order despite the exception
    self.assertEqual(execution_order, ["third", "failing", "first"])
    self.assertEqual(len(errors), 1)
    self.assertEqual(errors[0][0], failing_callback)
    self.assertIsInstance(errors[0][1], RuntimeError)

  def test_lockfile_removal_on_context_exit(self):
    """TestCase 5: Lockfile registration and automatic removal upon context exit."""
    lockfile_path = os.path.join(self.test_root, "worker.lock")
    with open(lockfile_path, "w", encoding="utf-8") as f:
      f.write("12345")
    self.assertTrue(os.path.exists(lockfile_path))

    with SignalTrap() as trap:
      trap.register_lockfile(lockfile_path)
      self.assertTrue(os.path.exists(lockfile_path))

    # Lockfile must be deleted on context exit
    self.assertFalse(os.path.exists(lockfile_path))

  def test_lockfile_removal_on_signal(self):
    """TestCase 6: Lockfile removal on signal delivery."""
    lockfile_path = os.path.join(self.test_root, "worker_sig.lock")
    with open(lockfile_path, "w", encoding="utf-8") as f:
      f.write("54321")

    trap = SignalTrap(exit_on_signal=False)
    trap.install()
    trap.register_lockfile(lockfile_path)

    # Deliver simulated SIGTERM
    trap.handle_signal(signal.SIGTERM, None)
    self.assertFalse(os.path.exists(lockfile_path))
    self.assertEqual(trap.signal_count, 1)
    self.assertEqual(trap.last_signal, signal.SIGTERM)
    trap.restore()

  def test_signal_idempotency(self):
    """TestCase 7: Idempotency when multiple signals arrive."""
    calls = []
    trap = SignalTrap(exit_on_signal=False)
    trap.register_cleanup(lambda: calls.append("cleanup"))

    trap.handle_signal(signal.SIGTERM, None)
    trap.handle_signal(signal.SIGTERM, None)
    trap.handle_signal(signal.SIGINT, None)

    # Cleanups must run exactly once
    self.assertEqual(calls, ["cleanup"])
    self.assertEqual(trap.signal_count, 3)

  def test_restore_idempotency(self):
    """TestCase 8: restore() is safe to call multiple times."""
    trap = SignalTrap()
    trap.install()
    trap.restore()
    # Calling restore again must not raise any error
    trap.restore()
    self.assertFalse(trap.is_installed)

  def test_missing_lockfile_removal_graceful(self):
    """TestCase 9: Missing lockfile does not cause errors during cleanup."""
    lockfile_path = os.path.join(self.test_root, "nonexistent.lock")
    trap = SignalTrap(exit_on_signal=False)
    trap.register_lockfile(lockfile_path)
    errors = trap.remove_lockfiles()
    self.assertEqual(errors, [])

  def test_subprocess_end_to_end_sigterm(self):
    """TestCase 10: End-to-end subprocess signal trapping with SIGTERM."""
    lockfile_path = os.path.join(self.test_root, "subproc_term.lock")
    ready_file_path = os.path.join(self.test_root, "subproc_term.ready")
    script_path = os.path.join(_SCRIPTS_DIR, "signal_trap.py")

    cmd = [
        sys.executable,
        script_path,
        "--lockfile",
        lockfile_path,
        "--ready-file",
        ready_file_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
      # Wait for ready sentinel file
      for _ in range(50):
        if os.path.exists(ready_file_path) and os.path.exists(lockfile_path):
          break
        time.sleep(0.1)

      self.assertTrue(os.path.exists(ready_file_path), "Subprocess did not signal readiness")
      self.assertTrue(os.path.exists(lockfile_path), "Lockfile was not created")
      self.assertIsNone(proc.poll(), "Subprocess exited prematurely")

      # Send SIGTERM to child process
      proc.send_signal(signal.SIGTERM)

      # Wait for child process to terminate cleanly
      retcode = proc.wait(timeout=5)
      # POSIX convention: 128 + 15 = 143, or -SIGTERM (-15)
      self.assertIn(retcode, (143, -signal.SIGTERM, 0))

      # Verify lockfile was removed by SignalTrap handler
      self.assertFalse(
          os.path.exists(lockfile_path),
          "Lockfile must be unlinked upon SIGTERM",
      )
    finally:
      if proc.stdout:
        proc.stdout.close()
      if proc.stderr:
        proc.stderr.close()
      if proc.poll() is None:
        proc.kill()
        proc.wait()

  def test_subprocess_end_to_end_sigint(self):
    """TestCase 11: End-to-end subprocess signal trapping with SIGINT."""
    lockfile_path = os.path.join(self.test_root, "subproc_int.lock")
    ready_file_path = os.path.join(self.test_root, "subproc_int.ready")
    script_path = os.path.join(_SCRIPTS_DIR, "signal_trap.py")

    cmd = [
        sys.executable,
        script_path,
        "--lockfile",
        lockfile_path,
        "--ready-file",
        ready_file_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
      for _ in range(50):
        if os.path.exists(ready_file_path) and os.path.exists(lockfile_path):
          break
        time.sleep(0.1)

      self.assertTrue(os.path.exists(ready_file_path), "Subprocess did not signal readiness")
      self.assertTrue(os.path.exists(lockfile_path), "Lockfile was not created")
      self.assertIsNone(proc.poll(), "Subprocess exited prematurely")

      # Send SIGINT to child process
      proc.send_signal(signal.SIGINT)

      retcode = proc.wait(timeout=5)
      # POSIX convention: 128 + 2 = 130, or -SIGINT (-2)
      self.assertIn(retcode, (130, -signal.SIGINT, 0))

      self.assertFalse(
          os.path.exists(lockfile_path),
          "Lockfile must be unlinked upon SIGINT",
      )
    finally:
      if proc.stdout:
        proc.stdout.close()
      if proc.stderr:
        proc.stderr.close()
      if proc.poll() is None:
        proc.kill()
        proc.wait()

  def test_lock_dir_registration_and_removal_on_exit(self):
    """TestCase 12: Directory lock registration, collision check, and removal on exit."""
    lock_dir = os.path.join(self.test_root, "worker.lock.d")
    with SignalTrap() as trap:
      registered = trap.register_lock_dir(lock_dir)
      self.assertEqual(registered, lock_dir)
      os.mkdir(lock_dir)
      self.assertTrue(os.path.isdir(lock_dir))

      # Atomic collision check: another mkdir must fail with FileExistsError
      with self.assertRaises(FileExistsError):
        os.mkdir(lock_dir)

    # Lock directory must be removed on context exit
    self.assertFalse(os.path.exists(lock_dir))

  def test_lock_dir_removal_on_signal(self):
    """TestCase 13: Lock directory removal on signal delivery."""
    lock_dir = os.path.join(self.test_root, "sig_worker.lock.d")
    os.mkdir(lock_dir)
    self.assertTrue(os.path.isdir(lock_dir))

    trap = SignalTrap(exit_on_signal=False)
    trap.install()
    trap.register_lock_dir(lock_dir)

    # Deliver simulated SIGTERM
    trap.handle_signal(signal.SIGTERM, None)
    self.assertFalse(os.path.exists(lock_dir))
    self.assertEqual(trap.signal_count, 1)
    self.assertEqual(trap.last_signal, signal.SIGTERM)
    trap.restore()

  def test_concurrent_or_pre_removed_lock_graceful(self):
    """TestCase 14: Pre-removed or concurrently removed lock file or dir cleans up gracefully."""
    lock_dir = os.path.join(self.test_root, "pre_removed.lock.d")
    lock_file = os.path.join(self.test_root, "pre_removed.lock")
    trap = SignalTrap(exit_on_signal=False)
    trap.register_lock_dir(lock_dir)
    trap.register_lockfile(lock_file)

    # Neither exists on disk; remove_lockfiles() must catch FileNotFoundError silently
    with unittest.mock.patch("lib.signal_trap.logger.warning") as mock_warn:
      errors = trap.remove_lockfiles()
      self.assertEqual(errors, [])
      mock_warn.assert_not_called()

    # Also verify when unlink / rmdir explicitly raises FileNotFoundError during cleanup
    trap.register_lockfile(lock_file)
    trap.register_lock_dir(lock_dir)
    with unittest.mock.patch("os.unlink", side_effect=FileNotFoundError("No such file")), \
         unittest.mock.patch("os.rmdir", side_effect=FileNotFoundError("No such directory")), \
         unittest.mock.patch("lib.signal_trap.logger.warning") as mock_warn:
      errors = trap.remove_lockfiles()
      self.assertEqual(errors, [])
      mock_warn.assert_not_called()

  def test_subprocess_end_to_end_dir_lock(self):
    """TestCase 15: End-to-end subprocess signal trapping with --lock-dir and SIGTERM."""
    lock_dir_path = os.path.join(self.test_root, "subproc_dir.lock.d")
    ready_file_path = os.path.join(self.test_root, "subproc_dir.ready")
    script_path = os.path.join(_SCRIPTS_DIR, "signal_trap.py")

    cmd = [
        sys.executable,
        script_path,
        "--lock-dir",
        lock_dir_path,
        "--ready-file",
        ready_file_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
      # Wait for ready sentinel file
      for _ in range(50):
        if os.path.exists(ready_file_path) and os.path.isdir(lock_dir_path):
          break
        time.sleep(0.1)

      self.assertTrue(os.path.exists(ready_file_path), "Subprocess did not signal readiness")
      self.assertTrue(os.path.isdir(lock_dir_path), "Lock dir was not created")
      self.assertIsNone(proc.poll(), "Subprocess exited prematurely")

      # Send SIGTERM to child process
      proc.send_signal(signal.SIGTERM)

      # Wait for child process to terminate cleanly
      retcode = proc.wait(timeout=5)
      self.assertIn(retcode, (143, -signal.SIGTERM, 0))

      # Verify lock directory was removed by SignalTrap handler
      self.assertFalse(
          os.path.exists(lock_dir_path),
          "Lock directory must be rmdir'd upon SIGTERM",
      )
    finally:
      if proc.stdout:
        proc.stdout.close()
      if proc.stderr:
        proc.stderr.close()
      if proc.poll() is None:
        proc.kill()
        proc.wait()

  def test_cli_description_contains_both_scars(self):
    """TestCase 16: CLI help description references both SCAR-SYS-07 and SCAR-SYS-08."""
    script_path = os.path.join(_SCRIPTS_DIR, "signal_trap.py")
    result = subprocess.run(
        [sys.executable, script_path, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    self.assertIn("SCAR-SYS-07", result.stdout)
    self.assertIn("SCAR-SYS-08", result.stdout)

  def test_repeated_signal_force_exit(self):
    """TestCase 17: Second signal during cleanup triggers immediate force-exit."""
    import io
    mock_exit = unittest.mock.MagicMock()
    trap = SignalTrap(
        exit_on_signal=False,
        exit_func=mock_exit,
        force_exit_on_repeated=True,
    )
    trap.install()

    # Simulate cleanup already in progress
    trap._handling_signal = True
    trap.signal_count = 1

    fake_stderr = io.StringIO()
    with unittest.mock.patch("sys.stderr", fake_stderr):
      trap.handle_signal(signal.SIGINT, None)

    self.assertEqual(trap.signal_count, 2)
    self.assertEqual(trap.last_signal, signal.SIGINT)
    mock_exit.assert_called_once_with(128 + signal.SIGINT)
    self.assertFalse(trap.is_installed)
    self.assertIn("Caught repeated signal 2 during shutdown. Force exiting immediately.", fake_stderr.getvalue())

  def test_register_subprocess_graceful_termination(self):
    """TestCase 18: Registered subprocess is gracefully terminated and reaped."""
    mock_proc = unittest.mock.MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.wait.return_value = 0

    trap = SignalTrap(exit_on_signal=False)
    ret = trap.register_subprocess(mock_proc, timeout=1.0)
    self.assertIs(ret, mock_proc)

    errors = trap.execute_cleanups()
    self.assertEqual(errors, [])
    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=1.0)
    mock_proc.kill.assert_not_called()

  def test_register_subprocess_kill_escalation(self):
    """TestCase 19: Registered subprocess escalates to kill when terminate times out."""
    mock_proc = unittest.mock.MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="dummy", timeout=0.1),
        0,
    ]

    trap = SignalTrap(exit_on_signal=False)
    trap.register_subprocess(mock_proc, timeout=0.1)

    errors = trap.execute_cleanups()
    self.assertEqual(errors, [])
    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    self.assertEqual(mock_proc.wait.call_count, 2)

  def test_reap_zombies_static_method(self):
    """TestCase 20: reap_zombies drains dead child processes without blocking."""
    with unittest.mock.patch(
        "os.waitpid", side_effect=[(1001, 0), (1002, 0), (0, 0)]
    ) as mock_waitpid:
      reaped = SignalTrap.reap_zombies()
      self.assertEqual(reaped, [(1001, 0), (1002, 0)])
      self.assertEqual(mock_waitpid.call_count, 3)

    with unittest.mock.patch("os.waitpid", side_effect=ChildProcessError):
      reaped = SignalTrap.reap_zombies()
      self.assertEqual(reaped, [])



if __name__ == "__main__":
  unittest.main()
