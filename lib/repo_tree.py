"""Repository Git file tree indexer with SCAR-TOOL-16 volume triage (GH-246).

Collects tracked and untracked non-ignored files via ``git ls-files -z`` and
formats explicit ``(K of N files)`` indexes and volume triage summaries for
``archaeology-worker`` dispatch, CLI gates, and the MCP server.
"""

from collections import Counter
from dataclasses import dataclass, field
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_REPO_TREE_THRESHOLD = 500
_GIT_LS_FILES_CMD = [
    "git",
    "ls-files",
    "-z",
    "--cached",
    "--others",
    "--exclude-standard",
]

RunnerFn = Callable[[List[str], str], subprocess.CompletedProcess]


class RepoTreeError(RuntimeError):
  """Raised when repository file tree collection or prefix validation fails."""


def _validate_positive_int(value: Any, name: str) -> int:
  """Validates that ``value`` is a non-boolean integer greater than zero.

  Args:
    value: Candidate numeric value.
    name: Parameter name used in error messages.

  Returns:
    Validated positive integer.

  Raises:
    ValueError: If ``value`` is a boolean, not an integer, or ``<= 0``.
  """
  if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
    raise ValueError(f"{name} must be a positive integer.")
  return value


def _normalize_prefix(prefix: str) -> str:
  """Normalizes and validates a repository-relative path prefix.

  Args:
    prefix: Raw path prefix string.

  Returns:
    Normalized POSIX relative prefix without leading ``./`` or trailing ``/``.

  Raises:
    RepoTreeError: If ``prefix`` is absolute, contains NUL bytes, or escapes
      the repository root via ``..`` segments.
  """
  if "\x00" in prefix:
    raise RepoTreeError("Path prefix must not contain NUL bytes.")
  cleaned = prefix.strip().replace("\\", "/")
  if cleaned.startswith("/"):
    raise RepoTreeError(
        f"Path prefix must be repository-relative, got '{prefix}'."
    )
  while cleaned.startswith("./"):
    cleaned = cleaned[2:]
  cleaned = cleaned.rstrip("/")
  if cleaned in ("", "."):
    return ""
  segments = [seg for seg in cleaned.split("/") if seg]
  if any(seg in (".", "..") for seg in segments):
    raise RepoTreeError(
        f"Path prefix must not contain '.' or '..' traversal segments: '{prefix}'."
    )
  return "/".join(segments)


def _matches_prefix(path: str, norm_prefix: str) -> bool:
  """Returns True when ``path`` matches exact file or directory ``norm_prefix``."""
  if not norm_prefix:
    return True
  return path == norm_prefix or path.startswith(f"{norm_prefix}/")


def _compute_top_level_counts(paths: Sequence[str]) -> Dict[str, int]:
  """Computes sorted file counts grouped by top-level directory ('.' for root files)."""
  counts: Counter[str] = Counter()
  for path in paths:
    top_dir = path.split("/", 1)[0] if "/" in path else "."
    counts[top_dir] += 1
  return dict(sorted(counts.items(), key=lambda item: item[0]))


def _decode_process_text(raw: Any) -> str:
  """Decodes CompletedProcess stdout or stderr from str or bytes cleanly."""
  if raw is None:
    return ""
  if isinstance(raw, bytes):
    return raw.decode("utf-8", errors="replace")
  return str(raw)


def _default_git_runner(cmd: List[str], cwd: str) -> subprocess.CompletedProcess:
  """Executes a read-only Git command inside ``cwd`` using stdlib subprocess."""
  return subprocess.run(
      cmd,
      cwd=cwd,
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      check=False,
      timeout=15,
  )


@dataclass(frozen=True)
class RepoTreeIndex:
  """Immutable snapshot of a repository Git file tree index.

  Attributes:
    repo_root: Normalized repository root path.
    files: Sorted tuple of filtered relative file paths.
    total_count: Total non-ignored files in the full repository.
    prefix: Normalized path prefix applied to ``files`` (empty if unfiltered).
    top_level_counts: File counts grouped by top-level directory across all files.
  """

  repo_root: str
  files: Tuple[str, ...]
  total_count: int
  prefix: str = ""
  top_level_counts: Dict[str, int] = field(default_factory=dict)

  def __post_init__(self) -> None:
    object.__setattr__(self, "files", tuple(self.files))
    object.__setattr__(self, "top_level_counts", dict(self.top_level_counts))

  @property
  def filtered_count(self) -> int:
    """Returns the number of files matching the active prefix filter."""
    return len(self.files)

  def exceeds_threshold(
      self, threshold: int = DEFAULT_REPO_TREE_THRESHOLD
  ) -> bool:
    """Checks whether ``filtered_count`` exceeds ``threshold``.

    Args:
      threshold: Positive integer file count threshold.

    Returns:
      True when ``filtered_count > threshold``.

    Raises:
      ValueError: If ``threshold`` is not a positive integer.
    """
    valid_threshold = _validate_positive_int(threshold, "threshold")
    return self.filtered_count > valid_threshold

  def format_index(self, approved_limit: Optional[int] = None) -> str:
    """Formats the file tree index with explicit ``(K of N files)`` denominators.

    Args:
      approved_limit: Optional positive user-approved ceiling on displayed files.

    Returns:
      Deterministic multiline string containing denominator header and paths.

    Raises:
      ValueError: If ``approved_limit`` is provided and not a positive integer.
    """
    limit_val: Optional[int] = None
    if approved_limit is not None:
      limit_val = _validate_positive_int(approved_limit, "approved_limit")

    displayed = self.files[:limit_val] if limit_val is not None else self.files
    shown_count = len(displayed)
    meta_parts = [f"{shown_count} of {self.total_count} files"]
    if self.prefix:
      meta_parts.append(f"prefix={self.prefix}")
    if limit_val is not None:
      meta_parts.append(f"approved_limit={limit_val}")

    lines = [f"Repository Git file tree index ({', '.join(meta_parts)}):"]
    if displayed:
      lines.extend(displayed)
      if limit_val is not None and shown_count < self.filtered_count:
        omitted = self.filtered_count - shown_count
        lines.append(
            f"... ({omitted} more matching file(s) omitted under user-approved limit)"
        )
    elif self.total_count == 0:
      lines.append("(empty repository: 0 tracked or untracked non-ignored files)")
    elif self.prefix:
      lines.append(f"(no files matched prefix '{self.prefix}')")
    return "\n".join(lines)

  def _format_top_level_lines(self) -> List[str]:
    """Formats sorted top-level directory counts for summary blocks."""
    if not self.top_level_counts:
      return ["  - (none): 0"]
    sorted_items = sorted(
        self.top_level_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    lines: List[str] = []
    for name, count in sorted_items:
      label = ". (root)" if name == "." else f"{name}/"
      lines.append(f"  - {label}: {count}")
    return lines

  def format_count_summary(self) -> str:
    """Formats counts and top-level directory breakdown without listing file paths."""
    suffix = f", prefix={self.prefix}" if self.prefix else ""
    lines = [
        f"Repository Git file tree count ({self.filtered_count} of {self.total_count} files{suffix}):",
        "Top-level directory file counts:",
    ]
    lines.extend(self._format_top_level_lines())
    return "\n".join(lines)

  def format_triage_summary(
      self, threshold: int = DEFAULT_REPO_TREE_THRESHOLD
  ) -> str:
    """Formats a SCAR-TOOL-16 volume triage summary with counts and options.

    Args:
      threshold: Positive integer file count threshold.

    Returns:
      Summary showing matching counts, top-level breakdown, and 4 triage paths.

    Raises:
      ValueError: If ``threshold`` is not a positive integer.
    """
    valid_threshold = _validate_positive_int(threshold, "threshold")
    lines = [
        (
            f"Repository Git file tree volume triage required: "
            f"{self.filtered_count} of {self.total_count} files match "
            f"(threshold: {valid_threshold})."
        )
    ]
    if self.prefix:
      lines.append(f"Active prefix filter: {self.prefix}")
    lines.append("Top-level directory file counts:")
    lines.extend(self._format_top_level_lines())
    lines.extend([
        "Select one SCAR-TOOL-16 triage path before listing files:",
        "  1. Revise filter criteria (--prefix <dir> / pathPrefix)",
        "  2. Impose explicit user-approved limit (--approved-limit <N> / approvedLimit)",
        "  3. Count total files only (--count-only / countOnly)",
        "  4. Proceed without limits (--no-limit / noLimit)",
    ])
    return "\n".join(lines)

  def to_dict(
      self,
      approved_limit: Optional[int] = None,
      threshold: int = DEFAULT_REPO_TREE_THRESHOLD,
      *,
      count_only: bool = False,
      no_limit: bool = False,
  ) -> Dict[str, Any]:
    """Serializes the index into a JSON-ready dictionary respecting SCAR-TOOL-16.

    Args:
      approved_limit: Optional user-approved path count limit.
      threshold: Positive integer volume triage threshold.
      count_only: True to omit file paths and return counts only.
      no_limit: True when the user explicitly approved unbounded output.

    Returns:
      Dictionary containing file counts, ``triageRequired`` status, top-level
      directory breakdown, and either paths or triage guidance.

    Raises:
      ValueError: If ``threshold`` or ``approved_limit`` is not a positive int.
    """
    valid_threshold = _validate_positive_int(threshold, "threshold")
    limit_val: Optional[int] = None
    if approved_limit is not None:
      limit_val = _validate_positive_int(approved_limit, "approved_limit")

    triage_required = (
        not count_only
        and not no_limit
        and limit_val is None
        and self.exceeds_threshold(valid_threshold)
    )
    payload: Dict[str, Any] = {
        "repoRoot": self.repo_root,
        "prefix": self.prefix,
        "totalCount": self.total_count,
        "filteredCount": self.filtered_count,
        "returnedCount": 0,
        "denominator": f"0 of {self.total_count} files",
        "threshold": valid_threshold,
        "triageRequired": triage_required,
        "countOnly": bool(count_only),
        "noLimit": False,
        "approvedLimit": None,
        "truncated": False,
        "topLevelCounts": dict(self.top_level_counts),
        "files": [],
        "formattedIndex": "",
        "triageSummary": "",
    }
    if triage_required:
      payload["countOnly"] = False
      payload["triageSummary"] = self.format_triage_summary(
          threshold=valid_threshold
      )
      payload["triagePaths"] = [
          "Revise the directive or filter criteria (pathPrefix / --prefix).",
          "Impose explicit user-approved limits (approvedLimit / --approved-limit).",
          "Count total rows first before reading detailed content (countOnly / --count-only).",
          "Proceed without limits (noLimit / --no-limit).",
      ]
      return payload

    if count_only:
      payload["denominator"] = f"{self.filtered_count} of {self.total_count} files"
      return payload

    selected = (
        list(self.files[:limit_val]) if limit_val is not None else list(self.files)
    )
    returned_count = len(selected)
    payload.update({
        "returnedCount": returned_count,
        "denominator": f"{returned_count} of {self.total_count} files",
        "noLimit": bool(no_limit),
        "approvedLimit": limit_val,
        "truncated": returned_count < self.filtered_count,
        "files": selected,
        "formattedIndex": self.format_index(approved_limit=limit_val),
    })
    return payload


class RepoTreeCollector:
  """Collects a sorted RepoTreeIndex from a Git workspace."""

  def __init__(
      self,
      repo_root: str,
      runner: Optional[RunnerFn] = None,
  ) -> None:
    """Initializes the collector with a repository path and optional runner.

    Args:
      repo_root: Path to the target Git workspace.
      runner: Optional callable ``(cmd, cwd) -> CompletedProcess`` for tests.
    """
    self.repo_root = repo_root if runner is not None else os.path.abspath(repo_root)
    self._custom_runner = runner
    self._runner: RunnerFn = runner if runner is not None else _default_git_runner

  def collect(self, prefix: str = "") -> RepoTreeIndex:
    """Runs ``git ls-files -z`` and returns an immutable ``RepoTreeIndex``.

    Args:
      prefix: Optional repository-relative path prefix filter.

    Returns:
      Populated ``RepoTreeIndex`` sorted deterministically.

    Raises:
      RepoTreeError: If ``repo_root`` is missing, ``prefix`` is invalid, or
        ``git ls-files`` fails (for example outside a Git repository).
    """
    norm_prefix = _normalize_prefix(prefix)
    if self._custom_runner is None and (
        not self.repo_root or not os.path.isdir(self.repo_root)
    ):
      raise RepoTreeError(
          f"Repository root directory does not exist: {self.repo_root}"
      )

    try:
      proc = self._runner(list(_GIT_LS_FILES_CMD), self.repo_root)
    except (OSError, subprocess.SubprocessError) as exc:
      raise RepoTreeError(
          f"Failed to run git ls-files in '{self.repo_root}': {exc}"
      ) from exc

    if proc.returncode != 0:
      err_msg = _decode_process_text(proc.stderr).strip() or (
          f"exit code {proc.returncode}"
      )
      raise RepoTreeError(
          f"git ls-files failed in '{self.repo_root}': {err_msg}"
      )

    raw_stdout = _decode_process_text(proc.stdout)
    all_files = sorted({entry for entry in raw_stdout.split("\x00") if entry})
    top_level_counts = _compute_top_level_counts(all_files)
    filtered = tuple(
        path for path in all_files if _matches_prefix(path, norm_prefix)
    )
    return RepoTreeIndex(
        repo_root=self.repo_root,
        files=filtered,
        total_count=len(all_files),
        prefix=norm_prefix,
        top_level_counts=top_level_counts,
    )
