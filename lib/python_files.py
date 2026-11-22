"""Shared Python file discovery and ignored directory rules (GH-275).

Provides canonical directory exclusion rules and recursive Python file
collection shared by ``lib/static_check.py`` and ``lib/ast_scrub.py``.
"""

import os
from typing import FrozenSet, Iterable, List, Optional, Set, Union

SKIP_SCAN_DIRS: FrozenSet[str] = frozenset({
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


def _is_within_dir(abs_base: str, abs_target: str) -> bool:
  """Returns True when abs_target is equal to or nested inside abs_base.

  Args:
    abs_base: Normalized absolute base directory path.
    abs_target: Normalized absolute candidate path.

  Returns:
    True if ``abs_target`` shares ``abs_base`` as its common path prefix.
  """
  try:
    return os.path.commonpath([abs_base, abs_target]) == abs_base
  except ValueError:
    return False


def normalize_targets(
    targets: Union[str, os.PathLike, Iterable[Union[str, os.PathLike]]],
) -> List[str]:
  """Materializes a single path or iterable of paths into a concrete string list.

  Args:
    targets: Single path or iterable of file or directory paths.

  Returns:
    Concrete list of filesystem path strings safe for multi-pass consumption.
  """
  if isinstance(targets, (str, os.PathLike)):
    return [os.fspath(targets)]
  return [os.fspath(target) for target in targets]


def has_ignored_dir_segment(
    base_dir: Union[str, os.PathLike],
    target_path: Union[str, os.PathLike],
    *,
    ignore_dirs: FrozenSet[str] = SKIP_SCAN_DIRS,
) -> bool:
  """Returns True when target_path contains any ignored directory under base_dir.

  Args:
    base_dir: Base repository directory used to compute relative segments.
    target_path: Candidate file or directory path to inspect.
    ignore_dirs: Directory basenames treated as excluded tooling or cache folders.

  Returns:
    True if any relative path component under ``base_dir`` belongs to ``ignore_dirs``.
  """
  abs_base = os.path.abspath(os.fspath(base_dir))
  abs_target = os.path.abspath(os.fspath(target_path))
  try:
    rel = os.path.relpath(abs_target, abs_base)
  except ValueError:
    return os.path.basename(abs_target) in ignore_dirs
  parts = [
      part
      for part in rel.replace(os.sep, "/").split("/")
      if part not in ("", ".", "..")
  ]
  return any(part in ignore_dirs for part in parts)


def is_ignored_dir_target(
    target_path: Union[str, os.PathLike],
    *,
    base_dir: Optional[Union[str, os.PathLike]] = None,
    ignore_dirs: FrozenSet[str] = SKIP_SCAN_DIRS,
) -> bool:
  """Returns True when a directory target is ignored or nested in an ignored dir.

  Args:
    target_path: Candidate directory path (absolute or relative to ``base_dir``).
    base_dir: Optional base directory used to resolve relative paths and detect
      ignored segments.
    ignore_dirs: Directory basenames treated as excluded tooling or cache folders.

  Returns:
    True if ``target_path`` or a relative ancestor under ``base_dir`` belongs
    to ``ignore_dirs``.
  """
  abs_base = (
      os.path.abspath(os.fspath(base_dir)) if base_dir is not None else None
  )
  raw_path = os.fspath(target_path)
  if os.path.isabs(raw_path) or abs_base is None:
    abs_target = os.path.abspath(raw_path)
  else:
    abs_target = os.path.abspath(os.path.join(abs_base, raw_path))

  if os.path.basename(abs_target) in ignore_dirs:
    return True
  return (
      abs_base is not None
      and _is_within_dir(abs_base, abs_target)
      and has_ignored_dir_segment(abs_base, abs_target, ignore_dirs=ignore_dirs)
  )


def collect_python_files(
    targets: Union[str, os.PathLike, Iterable[Union[str, os.PathLike]]],
    *,
    base_dir: Optional[Union[str, os.PathLike]] = None,
    ignore_dirs: FrozenSet[str] = SKIP_SCAN_DIRS,
) -> List[str]:
  """Collects sorted, deduplicated absolute Python file paths from target paths.

  Accepts either a single file or directory path (``str`` or ``os.PathLike``) or
  an iterable of paths. Skips ignored tooling, cache, virtual environment, and
  workflow directories both during recursive traversal and when passed directly.
  Uses ``followlinks=False`` to avoid infinite recursion on symlinked cycles.

  Args:
    targets: Single path or iterable of file or directory paths to scan.
    base_dir: Optional base directory used to resolve relative targets and detect
      nested ignored directory segments.
    ignore_dirs: Directory basenames excluded from traversal.

  Returns:
    Sorted list of unique absolute ``.py`` file paths.
  """
  abs_base = (
      os.path.abspath(os.fspath(base_dir)) if base_dir is not None else None
  )
  target_list = normalize_targets(targets)

  collected: Set[str] = set()
  for raw_path in target_list:
    if os.path.isabs(raw_path) or abs_base is None:
      abs_path = os.path.abspath(raw_path)
    else:
      abs_path = os.path.abspath(os.path.join(abs_base, raw_path))

    if os.path.isfile(abs_path):
      if abs_path.endswith(".py"):
        collected.add(abs_path)
      continue

    if not os.path.isdir(abs_path):
      continue

    if is_ignored_dir_target(
        abs_path, base_dir=abs_base, ignore_dirs=ignore_dirs
    ):
      continue

    for root, dirs, filenames in os.walk(abs_path, followlinks=False):
      dirs[:] = sorted(d for d in dirs if d not in ignore_dirs)
      for name in filenames:
        if name.endswith(".py"):
          collected.add(os.path.abspath(os.path.join(root, name)))

  return sorted(collected)
