"""Native Runtime Environment Path Pre-Flight Utility (SCAR-SYS-06).

Validates that directory paths in environment variables (such as TMPDIR,
TEMP, TMP, CACHE_DIR, XDG_CACHE_HOME) exist and are writable before launching
native compiled runtimes, preventing silent segmentation faults (SIGSEGV).
"""

import argparse
import os
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

from lib.base_script import BaseScript, SilentArgumentParser

STANDARD_ENV_VARS = (
    "TMPDIR",
    "TEMP",
    "TMP",
    "CACHE_DIR",
    "XDG_CACHE_HOME",
)


def validate_runtime_path(
    path: str,
    create_if_missing: bool = False,
    test_writable: bool = True,
) -> str:
  """Validate that a directory path exists and is writable.

  Args:
      path: Path to validate.
      create_if_missing: If True, create the directory if it does not exist.
      test_writable: If True, perform an ephemeral write probe.

  Returns:
      The normalized absolute path.

  Raises:
      FileNotFoundError: If path does not exist and create_if_missing is False.
      NotADirectoryError: If path exists but is not a directory.
      PermissionError: If path is not writable or cannot be created.
  """
  if not path:
    raise FileNotFoundError("Runtime path cannot be empty")

  norm_path = os.path.abspath(os.path.expanduser(path))

  if not os.path.exists(norm_path):
    if create_if_missing:
      try:
        os.makedirs(norm_path, exist_ok=True)
      except PermissionError as exc:
        raise PermissionError(
            f"Permission denied creating runtime directory: {norm_path}"
        ) from exc
      except OSError as exc:
        raise OSError(
            f"Failed to create runtime directory: {norm_path}: {exc}"
        ) from exc
    else:
      raise FileNotFoundError(
          f"Runtime directory does not exist: {norm_path}"
      )

  if not os.path.isdir(norm_path):
    raise NotADirectoryError(
        f"Runtime path exists but is not a directory: {norm_path}"
    )

  if test_writable:
    # Check standard POSIX access bits first
    if not os.access(norm_path, os.W_OK):
      raise PermissionError(
          f"Runtime directory lacks write permission: {norm_path}"
      )

    # Ephemeral writability probe to catch filesystem-level restrictions
    probe_path = None
    try:
      with tempfile.NamedTemporaryFile(
          dir=norm_path, prefix=".probe_preflight_", delete=False
      ) as probe:
        probe_path = probe.name
        probe.write(b"probe")
    except (OSError, PermissionError) as exc:
      raise PermissionError(
          f"Runtime directory writability probe failed: {norm_path}"
      ) from exc
    finally:
      if probe_path and os.path.exists(probe_path):
        try:
          os.unlink(probe_path)
        except OSError:
          pass

  return norm_path


def preflight_env_vars(
    env: Optional[Dict[str, str]] = None,
    var_names: Optional[Sequence[str]] = None,
    fallback_dir: Optional[str] = None,
    auto_create: bool = False,
) -> Dict[str, str]:
  """Inspect and validate runtime environment paths against fallbacks.

  Args:
      env: Environment mapping to inspect (defaults to os.environ).
      var_names: List of environment variable names to inspect.
      fallback_dir: Directory to use if an environment path is invalid.
      auto_create: If True, attempt to create missing directories.

  Returns:
      A dictionary mapping each inspected variable name to a validated path.
  """
  source_env = os.environ if env is None else env
  vars_to_check = STANDARD_ENV_VARS if var_names is None else tuple(var_names)

  # Resolve verified fallback directory
  if fallback_dir is None:
    fallback_candidate = tempfile.gettempdir()
  else:
    fallback_candidate = fallback_dir

  safe_fallback = validate_runtime_path(
      fallback_candidate, create_if_missing=True, test_writable=True
  )

  resolved: Dict[str, str] = {}
  for var in vars_to_check:
    if var in source_env:
      candidate = source_env[var]
      try:
        validated = validate_runtime_path(
            candidate, create_if_missing=auto_create, test_writable=True
        )
        resolved[var] = validated
      except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        resolved[var] = safe_fallback

  return resolved


def sanitize_runtime_env(
    env: Optional[Dict[str, str]] = None,
    var_names: Optional[Sequence[str]] = None,
    fallback_dir: Optional[str] = None,
    auto_create: bool = False,
) -> Dict[str, str]:
  """Return a sanitized copy of the environment with validated runtime paths.

  Args:
      env: Base environment mapping (defaults to os.environ).
      var_names: Variables to sanitize (defaults to standard variables).
      fallback_dir: Safe fallback directory for invalid paths.
      auto_create: Whether to auto-create missing directories.

  Returns:
      A new environment dictionary with invalid paths replaced by safe fallbacks.
  """
  base_env = dict(os.environ if env is None else env)
  resolved = preflight_env_vars(
      env=base_env,
      var_names=var_names,
      fallback_dir=fallback_dir,
      auto_create=auto_create,
  )
  base_env.update(resolved)
  return base_env


class EnvPreflightScript(BaseScript):
  """CLI runner for native runtime environment path pre-flight validation (SCAR-SYS-06, SCAR-ARCH-05)."""

  def __init__(self) -> None:
    super().__init__()
    self.paths: List[str] = []

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="env_preflight.py",
        description="Validate runtime environment directory paths exist and are writable (SCAR-SYS-06).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/env_preflight.py\n"
            "  python3 scripts/env_preflight.py /tmp /var/tmp"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional directory paths to validate (defaults to TMPDIR, TEMP, TMP, CACHE_DIR, XDG_CACHE_HOME).",
    )
    parsed = parser.parse_args(self.raw_args)
    self.paths = list(parsed.paths or [])

  def run(self) -> int:
    errors = 0

    if self.paths:
      for p in self.paths:
        try:
          validate_runtime_path(p, test_writable=True)
          self.stdout.write(f"✔ Path valid: {p}\n")
        except Exception as exc:  # pylint: disable=broad-except
          self.stderr.write(f"Pre-flight error for '{p}': {exc}\n")
          errors += 1
    else:
      for var in STANDARD_ENV_VARS:
        if var in os.environ:
          val = os.environ[var]
          try:
            validate_runtime_path(val, test_writable=True)
            self.stdout.write(f"✔ {var}={val} is valid.\n")
          except Exception as exc:  # pylint: disable=broad-except
            self.stderr.write(
                f"Pre-flight error: {var}='{val}' is invalid: {exc}\n"
            )
            errors += 1

    if errors > 0:
      return 1
    self.stdout.write("✔ All runtime environment paths valid.\n")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
  """CLI entrypoint for environment pre-flight path validation."""
  return EnvPreflightScript().execute(
      args=list(argv) if argv is not None else None
  )
