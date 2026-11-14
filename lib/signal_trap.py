"""POSIX Signal Trapping for Long-Running Workers (SCAR-SYS-07, SCAR-SYS-08).

Provides SignalTrap context manager and cleanup registry to intercept SIGTERM
and SIGINT, execute cleanup callbacks in LIFO order, safely remove registered
lockfiles and atomic directory locks, and exit cleanly.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from types import FrameType
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from lib.base_script import BaseScript, SilentArgumentParser

logger = logging.getLogger(__name__)

DEFAULT_SIGNALS: Tuple[int, ...] = (signal.SIGTERM, signal.SIGINT)


class SignalTrap:
  """POSIX signal handler and cleanup manager for long-running workers."""

  _instance: Optional["SignalTrap"] = None

  @classmethod
  def get_instance(cls) -> "SignalTrap":
    """Return singleton SignalTrap instance, creating and installing if necessary."""
    if cls._instance is None:
      cls._instance = cls()
      cls._instance.install()
    return cls._instance

  @classmethod
  def reset_instance(cls) -> None:
    """Reset singleton instance (useful for hermetic tests)."""
    if cls._instance is not None:
      cls._instance.restore()
      cls._instance = None

  def __init__(
      self,
      signals: Sequence[int] = DEFAULT_SIGNALS,
      exit_on_signal: bool = True,
      cleanup_on_exit: bool = True,
      force_exit_on_repeated: bool = True,
      exit_func: Callable[[int], None] = os._exit,
  ) -> None:
    """Initialize SignalTrap.

    Args:
        signals: Sequence of POSIX signal integers to trap (default SIGTERM, SIGINT).
        exit_on_signal: If True, invoke sys.exit(128 + sig) after handling signal.
        cleanup_on_exit: If True, execute cleanups upon normal context exit.
        force_exit_on_repeated: If True, immediately force-exit on repeated signal during shutdown.
        exit_func: Callable invoked on force-exit (default os._exit).
    """
    self.signals: Tuple[int, ...] = tuple(signals)
    self.exit_on_signal: bool = exit_on_signal
    self.cleanup_on_exit: bool = cleanup_on_exit
    self.force_exit_on_repeated: bool = force_exit_on_repeated
    self._exit_func: Callable[[int], None] = exit_func

    self._original_handlers: Dict[int, Any] = {}
    self._cleanups: List[Tuple[Callable[..., Any], Tuple[Any, ...], Dict[str, Any]]] = []
    self._lockfiles: Set[str] = set()
    self._lock_dirs: Set[str] = set()
    self._subprocesses: Dict[subprocess.Popen, float] = {}

    self._installed: bool = False
    self._handling_signal: bool = False
    self._cleanups_executed: bool = False
    self.signal_count: int = 0
    self.last_signal: Optional[int] = None
    self.caught_exceptions: List[Tuple[Callable[..., Any], Exception]] = []

  @property
  def is_installed(self) -> bool:
    """Return whether signal handlers are actively installed."""
    return self._installed

  def install(self) -> "SignalTrap":
    """Install signal handlers for configured signals.

    Returns:
        self for chaining.
    """
    if self._installed:
      return self

    self._original_handlers.clear()
    for sig in self.signals:
      try:
        prev = signal.signal(sig, self.handle_signal)
        self._original_handlers[sig] = prev
      except (ValueError, OSError) as exc:
        logger.warning("Could not register signal %s: %s", sig, exc)

    self._installed = True
    return self

  def restore(self) -> None:
    """Restore original signal handlers (idempotent)."""
    if not self._installed and not self._original_handlers:
      return

    for sig, handler in list(self._original_handlers.items()):
      try:
        signal.signal(sig, handler)
      except (ValueError, OSError) as exc:
        logger.warning("Could not restore signal %s: %s", sig, exc)

    self._original_handlers.clear()
    self._installed = False

  def register_cleanup(
      self, callback: Callable[..., Any], *args: Any, **kwargs: Any
  ) -> Callable[..., Any]:
    """Register a cleanup callback to be executed in reverse order (LIFO).

    Args:
        callback: Callable to execute.
        *args: Positional arguments for callback.
        **kwargs: Keyword arguments for callback.

    Returns:
        The registered callback.
    """
    self._cleanups.append((callback, args, kwargs))
    return callback

  def register_lockfile(self, path: Union[str, os.PathLike]) -> str:
    """Register a lockfile path to be removed upon signal or exit.

    Args:
        path: Filesystem path to lockfile.

    Returns:
        Normalized absolute path string.
    """
    norm_path = os.path.abspath(os.fspath(path))
    self._lockfiles.add(norm_path)
    return norm_path

  def register_lock_dir(self, path: Union[str, os.PathLike]) -> str:
    """Register a directory lock path to be removed upon signal or exit.

    Args:
        path: Filesystem path to directory lock.

    Returns:
        Normalized absolute path string.
    """
    norm_path = os.path.abspath(os.fspath(path))
    self._lock_dirs.add(norm_path)
    return norm_path

  def register_subprocess(
      self, proc: subprocess.Popen, timeout: float = 2.0
  ) -> subprocess.Popen:
    """Register a child subprocess to be terminated and reaped upon cleanup.

    Args:
        proc: Subprocess instance to monitor and manage.
        timeout: Seconds to wait after terminate before escalating to kill.

    Returns:
        The registered subprocess instance.
    """
    self._subprocesses[proc] = timeout
    return proc

  def unregister_subprocess(self, proc: subprocess.Popen) -> None:
    """Unregister a child subprocess from termination management.

    Args:
        proc: Subprocess instance to unregister.
    """
    self._subprocesses.pop(proc, None)

  @staticmethod
  def reap_zombies() -> List[Tuple[int, int]]:
    """Reap dead child processes non-blockingly using waitpid(WNOHANG).

    Returns:
        List of (pid, status) tuples for all reaped zombie children.
    """
    reaped: List[Tuple[int, int]] = []
    while True:
      try:
        pid, status = os.waitpid(-1, os.WNOHANG)
        if pid == 0:
          break
        reaped.append((pid, status))
      except (ChildProcessError, OSError):
        break
    return reaped

  def remove_lockfiles(self) -> List[Tuple[str, Exception]]:
    """Remove all registered lockfiles and lock directories if they exist.

    Returns:
        List of (path, exception) tuples for any removal errors.
    """
    errors: List[Tuple[str, Exception]] = []
    lockfiles_to_remove = list(self._lockfiles)
    self._lockfiles.clear()
    lock_dirs_to_remove = list(self._lock_dirs)
    self._lock_dirs.clear()

    for path in lockfiles_to_remove:
      try:
        try:
          os.unlink(path)
        except IsADirectoryError:
          os.rmdir(path)
      except FileNotFoundError:
        pass
      except OSError as exc:
        errors.append((path, exc))
        logger.warning("Failed to remove lockfile %s: %s", path, exc)

    for path in lock_dirs_to_remove:
      try:
        if os.path.isdir(path):
          for fname in os.listdir(path):
            try:
              os.unlink(os.path.join(path, fname))
            except OSError:
              pass
        os.rmdir(path)
      except FileNotFoundError:
        pass
      except OSError as exc:
        errors.append((path, exc))
        logger.warning("Failed to remove lock directory %s: %s", path, exc)

    return errors

  def execute_cleanups(self) -> List[Tuple[Callable[..., Any], Exception]]:
    """Execute all registered cleanup callbacks in reverse order (LIFO).

    Isolates exceptions so that a failure in one callback does not abort
    subsequent cleanup execution.

    Returns:
        List of (callback, exception) tuples for any caught errors.
    """
    errors: List[Tuple[Callable[..., Any], Exception]] = []

    while self._cleanups:
      callback, args, kwargs = self._cleanups.pop()
      try:
        callback(*args, **kwargs)
      except Exception as exc:  # pylint: disable=broad-except
        errors.append((callback, exc))
        self.caught_exceptions.append((callback, exc))
        logger.warning("Error in cleanup callback %s: %s", callback, exc)

    # Terminate and reap registered child subprocesses
    subprocs = list(self._subprocesses.items())
    self._subprocesses.clear()
    for proc, timeout in subprocs:
      if proc.poll() is None:
        try:
          proc.terminate()
          try:
            proc.wait(timeout=timeout)
          except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
        except OSError as exc:
          logger.warning("Error terminating subprocess %s: %s", proc, exc)

    self.reap_zombies()
    self.remove_lockfiles()
    self._cleanups_executed = True
    return errors

  def handle_signal(self, sig: int, frame: Optional[FrameType]) -> None:
    """Handle caught POSIX signal, execute cleanups, and exit.

    Args:
        sig: Signal number received.
        frame: Current stack frame.
    """
    self.signal_count += 1
    self.last_signal = sig

    if self._handling_signal:
      if self.force_exit_on_repeated and self.signal_count >= 2:
        sys.stderr.write(
            f"\n[SignalTrap] Caught repeated signal {sig} during shutdown. Force exiting immediately.\n"
        )
        sys.stderr.flush()
        self.restore()
        self._exit_func(128 + sig)
      return

    self._handling_signal = True
    try:
      self.execute_cleanups()
    finally:
      self._handling_signal = False
      self.restore()
      if self.exit_on_signal:
        sys.exit(128 + sig)

  def __enter__(self) -> "SignalTrap":
    """Enter context manager, installing signal trap."""
    return self.install()

  def __exit__(
      self,
      exc_type: Optional[type],
      exc_val: Optional[BaseException],
      exc_tb: Optional[Any],
  ) -> None:
    """Exit context manager, running cleanups if configured and restoring handlers."""
    if self.cleanup_on_exit and not self._cleanups_executed:
      self.execute_cleanups()
    self.restore()


class SignalTrapScript(BaseScript):
  """CLI runner for POSIX Signal Trap Worker (SCAR-SYS-07, SCAR-SYS-08, SCAR-ARCH-05)."""

  def __init__(self) -> None:
    super().__init__()
    self.lockfile: Optional[str] = None
    self.lock_dir: Optional[str] = None
    self.ready_file: Optional[str] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="signal_trap.py",
        description="POSIX Signal Trap Worker (SCAR-SYS-07, SCAR-SYS-08)",
        epilog=(
            "Examples:\n"
            "  python3 scripts/signal_trap.py --lock-dir /tmp/work.lock.d --ready-file /tmp/work.ready\n"
            "  python3 scripts/signal_trap.py --lockfile /tmp/work.lock --ready-file /tmp/work.ready"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--lockfile",
        default=None,
        help="Path to lockfile to create and protect with SignalTrap.",
    )
    parser.add_argument(
        "--lock-dir",
        default=None,
        help="Path to lock directory to create and protect with SignalTrap.",
    )
    parser.add_argument(
        "--ready-file",
        default=None,
        help="Path to sentinel file to create when signal trap is active.",
    )
    parsed = parser.parse_args(self.raw_args)
    self.lockfile = parsed.lockfile
    self.lock_dir = parsed.lock_dir
    self.ready_file = parsed.ready_file

  def run(self) -> int:
    with SignalTrap() as trap:
      if self.lockfile:
        lock_path = os.path.abspath(self.lockfile)
        lock_dir = os.path.dirname(lock_path)
        if lock_dir:
          os.makedirs(lock_dir, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as f:
          f.write(str(os.getpid()))
        trap.register_lockfile(lock_path)

      if self.lock_dir:
        dir_path = os.path.abspath(self.lock_dir)
        parent_dir = os.path.dirname(dir_path)
        if parent_dir:
          os.makedirs(parent_dir, exist_ok=True)
        os.mkdir(dir_path)
        trap.register_lock_dir(dir_path)

      if self.ready_file:
        ready_path = os.path.abspath(self.ready_file)
        ready_dir = os.path.dirname(ready_path)
        if ready_dir:
          os.makedirs(ready_dir, exist_ok=True)
        with open(ready_path, "w", encoding="utf-8") as f:
          f.write("READY")

      # Keep worker alive until signal arrives
      while True:
        time.sleep(0.1)

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
  """Run signal_trap demo worker CLI for testing and process management."""
  return SignalTrapScript().execute(
      args=list(argv) if argv is not None else None
  )
