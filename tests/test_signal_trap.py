#!/usr/bin/env python3
"""Unit tests for signal_trap.py (SCAR-SYS-07)."""

import os
import signal
import subprocess
import sys
import tempfile
import threading
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


class TestSignalTrapConcurrency(unittest.TestCase):
  """Hermetic concurrency and shutdown-race tests for SignalTrap (GH-321)."""

  def setUp(self) -> None:
    self._orig_sigterm = signal.getsignal(signal.SIGTERM)
    self._orig_sigint = signal.getsignal(signal.SIGINT)

  def tearDown(self) -> None:
    try:
      signal.signal(signal.SIGTERM, self._orig_sigterm)
    except (ValueError, OSError):
      pass
    try:
      signal.signal(signal.SIGINT, self._orig_sigint)
    except (ValueError, OSError):
      pass

  def test_concurrent_register_and_unregister_subprocess_thread_safety(
      self,
  ) -> None:
    """Concurrent register/unregister across threads raises no dict mutation races."""
    trap = SignalTrap(exit_on_signal=False)
    self.assertTrue(
        hasattr(trap, "_subprocesses_lock"),
        "SignalTrap must guard _subprocesses with _subprocesses_lock",
    )
    errors = []
    barrier = threading.Barrier(8)

    def worker(worker_idx: int) -> None:
      try:
        barrier.wait(timeout=5.0)
        for step in range(50):
          proc = unittest.mock.MagicMock(name=f"proc-{worker_idx}-{step}")
          proc.poll.return_value = 0
          trap.register_subprocess(proc, timeout=0.5)
          trap.unregister_subprocess(proc)
      except Exception as exc:  # pylint: disable=broad-except
        errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(idx,)) for idx in range(8)
    ]
    for thread in threads:
      thread.start()
    for thread in threads:
      thread.join(timeout=5.0)
      self.assertFalse(thread.is_alive(), "Worker thread did not finish")

    self.assertEqual(errors, [])
    self.assertEqual(len(trap._subprocesses), 0)

  def test_execute_cleanups_terminates_subprocesses_outside_lock(self) -> None:
    """Terminating subprocesses during cleanup must not hold _subprocesses_lock."""
    trap = SignalTrap(exit_on_signal=False)
    other_proc = unittest.mock.MagicMock(name="other_proc")
    other_proc.poll.return_value = 0
    trap.register_subprocess(other_proc, timeout=1.0)

    worker_done = threading.Event()
    worker_errors = []

    def concurrent_mutation() -> None:
      try:
        trap.unregister_subprocess(other_proc)
        extra_proc = unittest.mock.MagicMock(name="extra_proc")
        extra_proc.poll.return_value = 0
        trap.register_subprocess(extra_proc, timeout=1.0)
        trap.unregister_subprocess(extra_proc)
      except Exception as exc:  # pylint: disable=broad-except
        worker_errors.append(exc)
      finally:
        worker_done.set()

    blocking_proc = unittest.mock.MagicMock(name="blocking_proc")
    blocking_proc.poll.return_value = None
    blocking_proc.wait.return_value = 0

    def terminate_while_other_thread_mutates() -> None:
      t = threading.Thread(target=concurrent_mutation)
      t.start()
      finished = worker_done.wait(timeout=2.0)
      t.join(timeout=2.0)
      if not finished:
        raise TimeoutError(
            "Deadlock detected: _subprocesses_lock was held during terminate()"
        )

    blocking_proc.terminate.side_effect = terminate_while_other_thread_mutates
    trap.register_subprocess(blocking_proc, timeout=1.0)

    errors = trap.execute_cleanups()
    self.assertEqual(errors, [])
    self.assertEqual(worker_errors, [])
    self.assertTrue(worker_done.is_set())
    blocking_proc.terminate.assert_called_once()

  def test_late_subprocess_registration_after_signal_terminates_immediately(
      self,
  ) -> None:
    """Subprocess registered after handle_signal shutdown starts is terminated immediately."""
    trap = SignalTrap(exit_on_signal=False)
    trap.handle_signal(signal.SIGINT, None)

    late_proc = unittest.mock.MagicMock(name="late_proc")
    late_proc.poll.return_value = None
    late_proc.wait.return_value = -signal.SIGTERM

    returned = trap.register_subprocess(late_proc, timeout=1.5)
    self.assertIs(returned, late_proc)
    late_proc.terminate.assert_called_once()
    late_proc.wait.assert_called_once_with(timeout=1.5)
    self.assertNotIn(late_proc, trap._subprocesses)

  def test_exit_drains_residual_subprocesses_when_cleanups_already_executed(
      self,
  ) -> None:
    """__exit__ must drain residual subprocesses even if _cleanups_executed is True."""
    residual_proc = unittest.mock.MagicMock(name="residual_proc")
    residual_proc.poll.return_value = None
    residual_proc.wait.return_value = 0

    with SignalTrap(exit_on_signal=False, cleanup_on_exit=True) as trap:
      trap.execute_cleanups()
      self.assertTrue(trap._cleanups_executed)
      trap.register_subprocess(residual_proc, timeout=1.0)

    residual_proc.terminate.assert_called_once()
    residual_proc.wait.assert_called_once_with(timeout=1.0)
    self.assertEqual(len(trap._subprocesses), 0)

  def test_off_main_thread_restore_preserves_original_handlers_for_main_thread(
      self,
  ) -> None:
    """Calling restore() off the main thread leaves _original_handlers intact for __exit__."""
    def custom_handler(_sig, _frame):
      pass

    signal.signal(signal.SIGTERM, custom_handler)
    with SignalTrap(exit_on_signal=False) as trap:
      self.assertEqual(signal.getsignal(signal.SIGTERM), trap.handle_signal)
      worker = threading.Thread(target=trap.restore)
      worker.start()
      worker.join(timeout=2.0)
      self.assertFalse(worker.is_alive())
      self.assertTrue(
          trap.is_installed,
          "Off-main-thread restore() must not clear installed state",
      )
      self.assertEqual(signal.getsignal(signal.SIGTERM), trap.handle_signal)

    self.assertFalse(trap.is_installed)
    self.assertEqual(signal.getsignal(signal.SIGTERM), custom_handler)

  def test_drain_subprocesses_two_phase_parallel_broadcast_and_shared_deadline(
      self,
  ) -> None:
    """Two-phase drain broadcasts SIGTERM to all live children before waiting under shared deadline."""
    clock = [100.0]
    events = []

    def fake_monotonic() -> float:
      return clock[0]

    proc_oserr = unittest.mock.MagicMock(name="proc_oserr")
    proc_oserr.poll.return_value = None

    def oserr_terminate() -> None:
      events.append(("terminate_err", "oserr"))
      raise OSError("No such process")

    proc_oserr.terminate.side_effect = oserr_terminate

    proc_already_done = unittest.mock.MagicMock(name="proc_already_done")
    proc_already_done.poll.side_effect = [None, 0]
    proc_already_done.terminate.side_effect = lambda: events.append(
        ("terminate", "already_done")
    )

    proc_clean = unittest.mock.MagicMock(name="proc_clean")
    proc_clean.poll.return_value = None
    proc_clean.terminate.side_effect = lambda: events.append(
        ("terminate", "clean")
    )

    def clean_wait(timeout=None) -> int:
      events.append(("wait", "clean", timeout))
      clock[0] += 1.5
      return 0

    proc_clean.wait.side_effect = clean_wait

    proc_timeout = unittest.mock.MagicMock(name="proc_timeout")
    proc_timeout.poll.return_value = None
    proc_timeout.terminate.side_effect = lambda: events.append(
        ("terminate", "timeout")
    )
    timeout_wait_calls = [0]

    def timeout_wait(timeout=None) -> int:
      events.append(("wait", "timeout", timeout))
      timeout_wait_calls[0] += 1
      if timeout_wait_calls[0] == 1:
        clock[0] += 0.5
        raise subprocess.TimeoutExpired(cmd="timeout", timeout=timeout)
      return -signal.SIGKILL

    proc_timeout.wait.side_effect = timeout_wait
    proc_timeout.kill.side_effect = lambda: events.append(("kill", "timeout"))

    proc_stubborn = unittest.mock.MagicMock(name="proc_stubborn")
    proc_stubborn.poll.return_value = None
    proc_stubborn.terminate.side_effect = lambda: events.append(
        ("terminate", "stubborn")
    )

    def stubborn_wait(timeout=None) -> int:
      events.append(("wait", "stubborn", timeout))
      raise subprocess.TimeoutExpired(cmd="stubborn", timeout=timeout)

    proc_stubborn.wait.side_effect = stubborn_wait
    proc_stubborn.kill.side_effect = lambda: events.append(("kill", "stubborn"))

    trap = SignalTrap(exit_on_signal=False)
    for proc in (
        proc_oserr,
        proc_already_done,
        proc_clean,
        proc_timeout,
        proc_stubborn,
    ):
      trap.register_subprocess(proc, timeout=2.0)

    with unittest.mock.patch(
        "lib.signal_trap.time.monotonic", side_effect=fake_monotonic
    ), unittest.mock.patch.object(
        trap, "reap_zombies", return_value=[]
    ) as mock_reap:
      errors = trap.execute_cleanups()

    self.assertEqual(errors, [])
    self.assertEqual(len(trap._subprocesses), 0)

    # Every live child receives terminate() BEFORE wait() is called on any child.
    first_wait_idx = next(
        idx for idx, ev in enumerate(events) if ev[0] == "wait"
    )
    terminate_indices = [
        idx
        for idx, ev in enumerate(events)
        if ev[0] in ("terminate", "terminate_err")
    ]
    self.assertEqual(len(terminate_indices), 5)
    self.assertTrue(
        all(idx < first_wait_idx for idx in terminate_indices),
        f"Expected all terminate() calls before first wait(), got events={events}",
    )

    # OSError child and already-exited child are neither waited nor killed.
    proc_oserr.wait.assert_not_called()
    proc_oserr.kill.assert_not_called()
    proc_already_done.wait.assert_not_called()
    proc_already_done.kill.assert_not_called()

    # Clean child gets full remaining budget and is never killed.
    proc_clean.wait.assert_called_once_with(timeout=2.0)
    proc_clean.kill.assert_not_called()

    # Shared deadline shrinks remaining wait budget for subsequent children.
    self.assertEqual(
        proc_timeout.wait.call_args_list,
        [unittest.mock.call(timeout=0.5), unittest.mock.call(timeout=2.0)],
    )
    proc_timeout.kill.assert_called_once()

    # Stubborn child gets 0.0 remaining wait, escalates to kill, and post-kill timeout is caught cleanly.
    self.assertEqual(
        proc_stubborn.wait.call_args_list,
        [unittest.mock.call(timeout=0.0), unittest.mock.call(timeout=2.0)],
    )
    proc_stubborn.kill.assert_called_once()

    # NBO-4: execute_cleanups() calls reap_zombies() exactly once.
    mock_reap.assert_called_once()

  def test_register_subprocess_main_thread_signal_reentrancy_no_deadlock(
      self,
  ) -> None:
    """Main-thread signal arriving inside register_subprocess does not deadlock and terminates late child."""
    trap = SignalTrap(exit_on_signal=False)

    class _ReEntrantProc:
      """Test double whose __hash__ triggers handle_signal on the main thread."""

      def __init__(self) -> None:
        self._triggered = False
        self.terminated = False
        self.killed = False
        self.waited_timeouts = []

      def __hash__(self) -> int:
        if not self._triggered:
          self._triggered = True
          trap.handle_signal(signal.SIGINT, None)
        return id(self)

      def poll(self):
        return None

      def terminate(self) -> None:
        self.terminated = True

      def kill(self) -> None:
        self.killed = True

      def wait(self, timeout=None) -> int:
        self.waited_timeouts.append(timeout)
        return -signal.SIGTERM

    proc = _ReEntrantProc()
    returned = trap.register_subprocess(proc, timeout=1.25)

    self.assertIs(returned, proc)
    self.assertEqual(trap.last_signal, signal.SIGINT)
    self.assertEqual(trap.signal_count, 1)
    self.assertTrue(proc.terminated)
    self.assertEqual(proc.waited_timeouts, [1.25])
    self.assertFalse(proc.killed)
    self.assertNotIn(proc, trap._subprocesses)


if __name__ == "__main__":
  unittest.main()
