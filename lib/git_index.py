"""Native Git plumbing helper for ephemeral GIT_INDEX_FILE seeding (GH-249).

Provides a hermetic context manager that seeds a temporary Git index via
``git ls-files -z --stage`` piped into
``git -c core.splitIndex=false update-index -z --index-info``. Because entries
seeded through ``update-index --index-info`` carry zeroed stat-cache fields,
subsequent ``git add`` operations always hash true working-tree bytes without
racy-git stat-cache drift or mutations to ``.git/index`` or
``.git/sharedindex.*``.
"""

import contextlib
import os
import subprocess
import tempfile
from typing import Dict, Iterator, Optional


def _run_git_bytes(
    repo_root: str,
    *git_args: str,
    input_bytes: bytes = b"",
    env: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess:
  """Executes a git plumbing command in binary bytes mode inside repo_root.

  Args:
    repo_root: Absolute path to the Git repository root directory.
    *git_args: Command arguments passed to the ``git`` executable.
    input_bytes: Raw bytes written to the subprocess stdin stream.
    env: Optional environment mapping override.
    timeout: Subprocess timeout in seconds.

  Returns:
    CompletedProcess with binary ``stdout`` and ``stderr`` byte buffers.

  Raises:
    RuntimeError: If invoking ``git`` fails or times out.
  """
  try:
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_root,
        input=input_bytes,
        env=env,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
  except (subprocess.SubprocessError, OSError) as exc:
    cmd_label = git_args[0] if git_args else "git"
    raise RuntimeError(f"git {cmd_label} failed: {exc}") from exc


def _decode_git_error(proc: subprocess.CompletedProcess, fallback: str) -> str:
  """Decodes a failed binary git process's stderr or stdout into an error string.

  Args:
    proc: CompletedProcess returned from ``_run_git_bytes``.
    fallback: Default message returned when both streams are empty.

  Returns:
    Stripped diagnostic error string.
  """
  raw = proc.stderr or proc.stdout or b""
  if isinstance(raw, bytes):
    text = raw.decode("utf-8", errors="replace").strip()
  else:
    text = str(raw).strip()
  return text or fallback


@contextlib.contextmanager
def ephemeral_git_index(
    repo_root: str,
    *,
    timeout: int = 15,
) -> Iterator[Dict[str, str]]:
  """Seeds a temporary GIT_INDEX_FILE from the live index via native Git plumbing.

  Reads staged entries from the repository's real index with
  ``git ls-files -z --stage`` and seeds an isolated temporary index file inside
  ``tempfile.TemporaryDirectory()`` via
  ``git -c core.splitIndex=false update-index -z --index-info``. Yields an
  environment dictionary with ``GIT_INDEX_FILE`` pointing to the ephemeral index
  so callers can run ``git -c core.splitIndex=false add`` and ``write-tree``
  without mutating ``.git/index`` or ``.git/sharedindex.*``.

  Args:
    repo_root: Path to the Git repository root directory.
    timeout: Timeout in seconds for each Git plumbing subprocess.

  Yields:
    Environment dictionary copy with ``GIT_INDEX_FILE`` bound to the ephemeral
    temporary index path.

  Raises:
    RuntimeError: If ``repo_root`` does not exist, is not a valid Git
      repository, or native index plumbing commands fail.
  """
  abs_repo = os.path.abspath(repo_root)
  if not os.path.isdir(abs_repo):
    raise RuntimeError(f"Repository root does not exist: {abs_repo}")

  ls_proc = _run_git_bytes(
      abs_repo, "ls-files", "-z", "--stage", timeout=timeout
  )
  if ls_proc.returncode != 0:
    raise RuntimeError(
        _decode_git_error(ls_proc, "git ls-files -z --stage failed")
    )

  with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_index = os.path.join(tmp_dir, "index")
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = tmp_index

    if ls_proc.stdout:
      upd_proc = _run_git_bytes(
          abs_repo,
          "-c",
          "core.splitIndex=false",
          "update-index",
          "-z",
          "--index-info",
          input_bytes=ls_proc.stdout,
          env=env,
          timeout=timeout,
      )
      if upd_proc.returncode != 0:
        raise RuntimeError(
            _decode_git_error(
                upd_proc, "git update-index -z --index-info failed"
            )
        )

    yield env
