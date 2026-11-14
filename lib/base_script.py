"""Reusable BaseScript template implementing the Template Method pattern (SCAR-ARCH-05).

Provides a testable object-oriented execution lifecycle for CLI scripts and tools,
along with non-exiting argument parser classes (SilentArgumentParser and
ReflexArgumentError) so CLI entrypoints never call sys.exit() inside business logic.

Usage:
    class MyScript(BaseScript):
        def parse_args(self, args=None):
            ...
        def setup(self):
            ...
        def run(self) -> int:
            ...
        def cleanup(self, exit_code: int):
            ...

    if __name__ == "__main__":
        import sys
        sys.exit(MyScript().execute())
"""

import argparse
import sys
from typing import Any, List, NoReturn, Optional, TextIO


class HelpExit(Exception):
  """Raised when CLI argument parser exits with status 0 after formatting help text."""

  exit_code = 0


class ReflexArgumentError(Exception):
  """Raised on invalid CLI arguments without terminating the Python process."""

  exit_code = 1


class SilentArgumentParser(argparse.ArgumentParser):
  """ArgumentParser subclass that raises HelpExit or ReflexArgumentError instead of SystemExit."""

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    super().__init__(*args, **kwargs)
    self._printed_messages: List[str] = []

  def _print_message(
      self, message: str, file: Optional[TextIO] = None
  ) -> None:
    if message:
      self._printed_messages.append(message)

  def error(self, message: str) -> NoReturn:
    self._printed_messages.clear()
    raise ReflexArgumentError(message)

  def exit(self, status: int = 0, message: Optional[str] = None) -> NoReturn:
    buffered = "".join(self._printed_messages)
    self._printed_messages.clear()
    if status == 0:
      raise HelpExit(buffered + (message or ""))
    raise ReflexArgumentError(
        message or buffered or f"Argument parser exited with status {status}"
    )


class BaseScript:
  """Abstract base class implementing the Template Method pattern for testable CLI scripts."""

  def __init__(self) -> None:
    self.stdout: TextIO = sys.stdout
    self.stderr: TextIO = sys.stderr
    self.raw_args: List[str] = []

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parses command line arguments.

    Subclasses can override to use argparse.ArgumentParser or custom parsing logic.
    """
    self.raw_args = list(args) if args is not None else []

  def setup(self) -> None:
    """Pre-execution hook for environment verification, resources, or connections.

    Subclasses can override to perform pre-flight checks.
    """
    pass

  def run(self) -> Optional[int]:
    """Primary execution logic.

    Subclasses must implement their business logic here.
    Returns:
        Optional integer exit code. If None is returned, 0 is assumed.
    """
    return 0

  def cleanup(self, exit_code: int) -> None:
    """Guaranteed post-execution cleanup hook.

    Always executed in a finally block regardless of whether run() succeeded or raised
    an unhandled exception.
    """
    pass

  def execute(
      self,
      args: Optional[List[str]] = None,
      stdout: Optional[TextIO] = None,
      stderr: Optional[TextIO] = None,
  ) -> int:
    """Template method coordinating the full script execution lifecycle.

    Injects I/O streams, parses arguments, executes setup(), run(), and cleanup(),
    and catches unhandled exceptions without calling sys.exit().

    Returns:
        Integer exit code (0 for success, non-zero for error).
    """
    self.stdout = stdout if stdout is not None else sys.stdout
    self.stderr = stderr if stderr is not None else sys.stderr

    if args is None:
      args = sys.argv[1:]

    exit_code = 0
    try:
      self.parse_args(args)
      self.setup()
      run_result = self.run()
      exit_code = 0 if run_result is None else int(run_result)
    except HelpExit as exc:
      exit_code = 0
      help_text = str(exc)
      if help_text:
        try:
          self.stdout.write(
              help_text if help_text.endswith("\n") else f"{help_text}\n"
          )
        except Exception:  # pylint: disable=broad-except
          pass
    except Exception as exc:  # pylint: disable=broad-except
      raw_code = getattr(exc, "exit_code", 1)
      try:
        exit_code = int(raw_code)
      except (ValueError, TypeError):
        exit_code = 1
      try:
        self.stderr.write(f"Error: {exc}\n")
      except Exception:  # pylint: disable=broad-except
        pass
    finally:
      try:
        self.cleanup(exit_code)
      except Exception as cleanup_exc:  # pylint: disable=broad-except
        try:
          self.stderr.write(f"Error during cleanup: {cleanup_exc}\n")
        except Exception:  # pylint: disable=broad-except
          pass

    return exit_code
