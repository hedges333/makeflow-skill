"""Deterministic Target 30 full subproject regression gate (GH-233, SCAR-TEST-12).

Computes working-tree Git Merkle hashes using an ephemeral GIT_INDEX_FILE seeded
via native Git plumbing, resolves and runs the active subproject's full
regression suite, rejects workspace mutation during test execution, and seals or
validates .workflow/state/30_regression_receipt.json against live ground truth.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import signal
import subprocess
import tempfile
from typing import Any, Dict, Optional, Tuple

from lib.config import resolve_workflow_config, sanitize_make_subprocess_env
from lib.git_index import ephemeral_git_index

DEFAULT_REGRESSION_RECEIPT_REL = os.path.join(
    ".workflow", "state", "30_regression_receipt.json"
)
DEFAULT_REGRESSION_TIMEOUT_SECONDS = 300.0
RECEIPT_SCHEMA_VERSION = 1

_MAKEFILE_TEST_TARGET_RE = re.compile(r"(?m)^test\s*:")


def _single_line(text: str) -> str:
  """Collapses whitespace and newlines so Vector 4 Reflex fields stay single-line.

  Args:
    text: Raw message text.

  Returns:
    Whitespace-collapsed single-line string.
  """
  return " ".join((text or "").split())


def _failure_payload(
    scar_id: str,
    code: str,
    trigger: str,
    rejected: str,
    reflex: str,
) -> Dict[str, str]:
  """Builds a structured Vector 4 Reflex failure payload.

  Args:
    scar_id: Short scar identifier without the SCAR- prefix.
    code: Machine-readable violation code.
    trigger: Observed failure trigger description.
    rejected: Prohibited action description.
    reflex: Concrete recovery directive.

  Returns:
    Dictionary containing scar_id, code, trigger, rejected, and reflex strings.
  """
  return {
      "scar_id": scar_id,
      "code": code,
      "trigger": _single_line(trigger),
      "rejected": _single_line(rejected),
      "reflex": _single_line(reflex),
  }


def _run_git(
    repo_root: str,
    *git_args: str,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess:
  """Executes a git command inside repo_root and returns the CompletedProcess.

  Args:
    repo_root: Git repository root directory.
    *git_args: Arguments passed to the git executable.
    env: Optional environment dictionary override.
    timeout: Command timeout in seconds.

  Returns:
    CompletedProcess with stdout, stderr, and returncode.
  """
  try:
    return subprocess.run(
        ["git", *git_args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=timeout,
    )
  except (subprocess.SubprocessError, OSError) as exc:
    return subprocess.CompletedProcess(
        args=["git", *git_args],
        returncode=128,
        stdout="",
        stderr=str(exc),
    )


def _git_head_sha(repo_root: str) -> Tuple[Optional[str], str]:
  """Resolves the current Git HEAD commit SHA.

  Args:
    repo_root: Git repository root directory.

  Returns:
    Tuple of (head_sha, error_message) where head_sha is None on error.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return None, f"Repository root does not exist: {repo_root}"
  proc = _run_git(repo_root, "rev-parse", "--verify", "HEAD")
  if proc.returncode != 0:
    err = (proc.stderr or proc.stdout or "git rev-parse --verify HEAD failed").strip()
    return None, err
  sha = (proc.stdout or "").strip()
  if not sha:
    return None, "git rev-parse --verify HEAD returned empty SHA"
  return sha, ""


def compute_working_tree_hash(repo_root: str) -> str:
  """Computes the exact Git Merkle hash of the full working tree without mutating .git/index.

  Seeds an isolated ephemeral ``GIT_INDEX_FILE`` via native Git plumbing
  (``ephemeral_git_index``), runs ``git -c core.splitIndex=false add -A`` and
  ``git -c core.splitIndex=false write-tree`` against that ephemeral index, and
  returns the resulting tree SHA.

  Args:
    repo_root: Git repository root directory.

  Returns:
    Hexadecimal Git tree SHA matching ``git add -A && git write-tree``.

  Raises:
    RuntimeError: If repo_root is not a valid Git repository or git commands fail.
  """
  abs_repo = os.path.abspath(repo_root)
  with ephemeral_git_index(abs_repo) as env:
    add_proc = _run_git(
        abs_repo, "-c", "core.splitIndex=false", "add", "-A", env=env
    )
    if add_proc.returncode != 0:
      err = (add_proc.stderr or add_proc.stdout or "git add -A failed").strip()
      raise RuntimeError(err)

    wt_proc = _run_git(
        abs_repo, "-c", "core.splitIndex=false", "write-tree", env=env
    )
    tree_hash = (wt_proc.stdout or "").strip()
    if wt_proc.returncode != 0 or not tree_hash:
      err = (wt_proc.stderr or wt_proc.stdout or "git write-tree failed").strip()
      raise RuntimeError(err)
    return tree_hash


def validate_subproject(repo_root: str, subproject: str) -> Tuple[str, str]:
  """Validates that subproject resolves to a directory inside repo_root.

  Args:
    repo_root: Git repository root directory.
    subproject: Relative or repo-internal subproject path (or '.' for root).

  Returns:
    Tuple of (abs_subproject_path, normalized_rel_subproject).

  Raises:
    ValueError: If subproject escapes repo_root, traverses symlinks outside
      repo_root, or does not exist as a directory.
  """
  abs_repo = os.path.abspath(repo_root)
  real_repo = os.path.realpath(abs_repo)
  cleaned = (subproject or ".").strip() or "."

  candidate = (
      os.path.abspath(cleaned)
      if os.path.isabs(cleaned)
      else os.path.abspath(os.path.join(abs_repo, cleaned))
  )

  try:
    if os.path.commonpath([abs_repo, candidate]) != abs_repo:
      raise ValueError(f"Subproject '{subproject}' escapes repository root.")
  except ValueError as exc:
    raise ValueError(f"Subproject '{subproject}' escapes repository root.") from exc

  if not os.path.exists(candidate):
    raise ValueError(f"Subproject directory does not exist: '{subproject}'.")

  real_candidate = os.path.realpath(candidate)
  try:
    if os.path.commonpath([real_repo, real_candidate]) != real_repo:
      raise ValueError(f"Subproject '{subproject}' resolves outside repository root.")
  except ValueError as exc:
    raise ValueError(
        f"Subproject '{subproject}' resolves outside repository root."
    ) from exc

  if not os.path.isdir(real_candidate):
    raise ValueError(f"Subproject path is not a directory: '{subproject}'.")

  rel_sub = os.path.relpath(candidate, abs_repo).replace(os.sep, "/")
  if not rel_sub:
    rel_sub = "."
  return candidate, rel_sub


_validate_subproject = validate_subproject


def _makefile_has_test_target(directory: str) -> bool:
  """Checks whether Makefile or GNUmakefile in directory defines a test: target.

  Args:
    directory: Absolute path to the subproject directory.

  Returns:
    True when a Makefile or GNUmakefile defines a ``test:`` target.
  """
  for fname in ("Makefile", "GNUmakefile"):
    mk_path = os.path.join(directory, fname)
    if not os.path.isfile(mk_path):
      continue
    try:
      with open(mk_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
      if _MAKEFILE_TEST_TARGET_RE.search(text):
        return True
    except OSError:
      continue
  return False


def resolve_regression_command(
    repo_root: str,
    subproject: str = ".",
    explicit_cmd: str = "",
) -> Tuple[str, str, str]:
  """Resolves the active subproject directory and full regression command.

  Resolution order:
  1. Explicit command passed via CLI flag or ``REGRESSION_CMD``.
  2. ``testing.regressionCommand`` from ``.workflow/config.json``.
  3. ``make test`` when ``<subproject>/Makefile`` or ``GNUmakefile`` defines
     a ``test:`` target.

  Args:
    repo_root: Repository root directory.
    subproject: Relative subproject directory (default ``'.'``).
    explicit_cmd: Optional explicit command override.

  Returns:
    Tuple of ``(abs_subproject, rel_subproject, resolved_command)``.
    ``resolved_command`` is an empty string when no command could be resolved.

  Raises:
    ValueError: If ``subproject`` is invalid or escapes ``repo_root``.
  """
  abs_repo = os.path.abspath(repo_root)
  abs_sub, rel_sub = _validate_subproject(abs_repo, subproject)

  cleaned_explicit = (explicit_cmd or "").strip()
  if cleaned_explicit:
    return abs_sub, rel_sub, cleaned_explicit

  cfg = resolve_workflow_config(workspace_dir=abs_repo)
  cfg_testing = cfg.get("testing") if isinstance(cfg, dict) else None
  if isinstance(cfg_testing, dict):
    cfg_cmd = cfg_testing.get("regressionCommand")
    if isinstance(cfg_cmd, str) and cfg_cmd.strip():
      return abs_sub, rel_sub, cfg_cmd.strip()

  if _makefile_has_test_target(abs_sub):
    return abs_sub, rel_sub, "make test"

  return abs_sub, rel_sub, ""


def compute_regression_seal(
    *,
    schema_version: int,
    subproject: str,
    regression_cmd: str,
    exit_code: int,
    verified: bool,
    tree_hash: str,
    head_sha: str,
    toolchain_hash: str,
    makefile_sha256: str,
) -> str:
  """Computes canonical SHA-256 seal over regression receipt fields.

  Args:
    schema_version: Receipt schema version integer.
    subproject: Normalized relative subproject path.
    regression_cmd: Exact regression command executed.
    exit_code: Command exit code (0 on pass).
    verified: Boolean verification status.
    tree_hash: Working-tree Git Merkle hash.
    head_sha: Git HEAD commit SHA at verification time.
    toolchain_hash: Core Makeflow governance toolchain SHA-256 digest.
    makefile_sha256: Canonical .workflow/state/Makefile SHA-256 digest.

  Returns:
    Hexadecimal SHA-256 digest string.
  """
  canonical = json.dumps(
      {
          "exit_code": exit_code,
          "head_sha": head_sha,
          "makefile_sha256": makefile_sha256,
          "regression_cmd": regression_cmd,
          "schema_version": schema_version,
          "subproject": subproject,
          "toolchain_hash": toolchain_hash,
          "tree_hash": tree_hash,
          "verified": verified,
      },
      sort_keys=True,
      separators=(",", ":"),
  )
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_state_makefile_integrity(repo_root: str) -> Tuple[bool, str, str]:
  """Computes canonical state Makefile SHA-256 and verifies live disk content.

  Args:
    repo_root: Repository root directory.

  Returns:
    Tuple of ``(is_valid, canonical_sha256, error_detail)``.
  """
  from lib.workflow_gates import generate_state_makefile

  canonical = generate_state_makefile(repo_root)
  canonical_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
  mk_path = os.path.join(repo_root, ".workflow", "state", "Makefile")
  if os.path.exists(mk_path):
    try:
      with open(mk_path, "r", encoding="utf-8") as f:
        live_content = f.read()
    except OSError as exc:
      return False, canonical_sha, f"Failed to read {mk_path}: {exc}"
    if live_content != canonical:
      return (
          False,
          canonical_sha,
          ".workflow/state/Makefile does not match canonical synthesis template.",
      )
  return True, canonical_sha, ""


def _terminate_process_tree(proc: subprocess.Popen) -> None:
  """Terminates a spawned shell process group on POSIX (or process on Windows).

  Args:
    proc: Running subprocess.Popen instance.
  """
  if os.name != "nt":
    try:
      os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
      pass
  try:
    proc.kill()
  except (ProcessLookupError, OSError):
    pass


def _run_regression_shell(
    cmd: str,
    cwd: str,
    timeout_seconds: float,
) -> Tuple[Optional[subprocess.CompletedProcess], str]:
  """Runs a regression shell command with process-group timeout cleanup.

  Args:
    cmd: Shell command string executed via ``/bin/sh -c``.
    cwd: Working directory for command execution.
    timeout_seconds: Positive finite timeout in seconds.

  Returns:
    Tuple of ``(completed_process, error_message)``. ``completed_process`` is
    None on timeout or launch error.
  """
  env = sanitize_make_subprocess_env(os.environ)
  env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
  proc: Optional[subprocess.Popen] = None
  prev_sigterm: Any = None
  sigterm_installed = False

  def _handle_sigterm(_signum: int, _frame: Any) -> None:
    if proc is not None and proc.poll() is None:
      _terminate_process_tree(proc)
    raise SystemExit(143)

  try:
    if os.name != "nt":
      try:
        prev_sigterm = signal.signal(signal.SIGTERM, _handle_sigterm)
        sigterm_installed = True
      except ValueError:
        sigterm_installed = False
    proc = subprocess.Popen(
        ["/bin/sh", "-c", cmd],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=(os.name != "nt"),
    )
    try:
      stdout_data, stderr_data = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
      _terminate_process_tree(proc)
      try:
        proc.communicate(timeout=5)
      except (subprocess.SubprocessError, OSError):
        pass
      return None, f"Regression command timed out after {timeout_seconds}s: {cmd}"
    return (
        subprocess.CompletedProcess(
            args=["/bin/sh", "-c", cmd],
            returncode=proc.returncode,
            stdout=stdout_data,
            stderr=stderr_data,
        ),
        "",
    )
  except (subprocess.SubprocessError, OSError) as exc:
    return None, f"Failed to execute regression command ({cmd}): {exc}"
  finally:
    if proc is not None:
      if proc.poll() is None:
        _terminate_process_tree(proc)
        try:
          proc.wait(timeout=5)
        except (subprocess.SubprocessError, OSError):
          pass
      for stream in (proc.stdout, proc.stderr):
        if stream is not None:
          try:
            stream.close()
          except OSError:
            pass
    if sigterm_installed:
      try:
        signal.signal(signal.SIGTERM, prev_sigterm)
      except ValueError:
        pass


def _unlink_receipt(receipt_path: str) -> None:
  """Removes a stale regression receipt file when present.

  Args:
    receipt_path: Absolute path to 30_regression_receipt.json.
  """
  if os.path.isfile(receipt_path):
    try:
      os.unlink(receipt_path)
    except OSError:
      pass


def _write_receipt_atomic(
    receipt_path: str, receipt: Dict[str, Any]
) -> Optional[str]:
  """Atomically writes receipt JSON using tempfile and os.replace.

  Args:
    receipt_path: Destination receipt file path.
    receipt: Receipt payload dictionary.

  Returns:
    None on success, or an error string on OSError.
  """
  tmp_path: Optional[str] = None
  try:
    receipt_dir = os.path.dirname(receipt_path)
    os.makedirs(receipt_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=receipt_dir,
        prefix=".tmp_regression_receipt_",
        suffix=".json",
        delete=False,
    ) as tmp_file:
      tmp_path = tmp_file.name
      json.dump(receipt, tmp_file, indent=2, sort_keys=True)
      tmp_file.write("\n")
      tmp_file.flush()
      os.fsync(tmp_file.fileno())
    os.replace(tmp_path, receipt_path)
    tmp_path = None
    return None
  except OSError as exc:
    if tmp_path is not None:
      try:
        os.unlink(tmp_path)
      except OSError:
        pass
    return str(exc)


def _resolve_timeout(
    repo_root: str, timeout_seconds: Optional[float]
) -> Tuple[Optional[float], str]:
  """Resolves and validates regression command timeout in seconds.

  Args:
    repo_root: Repository root directory.
    timeout_seconds: Optional explicit timeout override.

  Returns:
    Tuple of ``(timeout_float, error_message)``.
  """
  if timeout_seconds is not None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
      return None, f"Invalid timeout_seconds: {timeout_seconds}"
    return float(timeout_seconds), ""

  cfg = resolve_workflow_config(workspace_dir=repo_root)
  cfg_testing = cfg.get("testing") if isinstance(cfg, dict) else None
  if isinstance(cfg_testing, dict):
    val = cfg_testing.get("regressionTimeoutSeconds")
    if (
        not isinstance(val, bool)
        and isinstance(val, (int, float))
        and math.isfinite(float(val))
        and float(val) >= 1
    ):
      return float(val), ""
  return DEFAULT_REGRESSION_TIMEOUT_SECONDS, ""


def run_verify_regression(
    repo_root: str,
    *,
    explicit_cmd: str = "",
    subproject: str = ".",
    timeout_seconds: Optional[float] = None,
) -> Tuple[bool, Dict[str, Any]]:
  """Runs the full subproject regression suite and seals 30_regression_receipt.json.

  Verifies state Makefile integrity, resolves the regression command and timeout,
  executes the command in the validated subproject directory, rejects any
  working-tree mutation during execution, and atomically writes the sealed
  receipt. Any failure unlinks stale receipts and returns a Vector 4 Reflex
  failure payload.

  Args:
    repo_root: Repository root directory.
    explicit_cmd: Optional explicit regression command override.
    subproject: Relative subproject directory (default ``'.'``).
    timeout_seconds: Optional positive timeout in seconds.

  Returns:
    Tuple of ``(ok, payload)``. When ``ok`` is True, ``payload`` is the sealed
    receipt dictionary. When ``ok`` is False, ``payload`` contains ``scar_id``,
    ``code``, ``trigger``, ``rejected``, and ``reflex``.
  """
  from lib.tdd_guard import compute_toolchain_hash

  abs_repo = os.path.abspath(repo_root)
  receipt_path = os.path.join(abs_repo, DEFAULT_REGRESSION_RECEIPT_REL)

  mk_ok, makefile_sha256, mk_err = _check_state_makefile_integrity(abs_repo)
  if not mk_ok:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="ARCH-06",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger=mk_err,
        rejected="Running verify-regression with a tampered .workflow/state/Makefile.",
        reflex=(
            "Re-run 'python3 scripts/workflow_gates.py --repo-root . init-makefile'"
            " to restore the canonical Makefile, then re-run verify-regression."
        ),
    )

  try:
    abs_sub, rel_sub, resolved_cmd = resolve_regression_command(
        abs_repo, subproject=subproject, explicit_cmd=explicit_cmd
    )
  except ValueError as exc:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="INVALID_SUBPROJECT",
        trigger=str(exc),
        rejected="Running regression tests outside a valid subproject inside the repository.",
        reflex="Pass a valid relative subproject path inside the repository via SUBPROJECT or --subproject.",
    )

  if not resolved_cmd:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="MISSING_REGRESSION_COMMAND",
        trigger=(
            f"No regression command found for subproject '{rel_sub}' via"
            " REGRESSION_CMD, testing.regressionCommand, or Makefile test: target."
        ),
        rejected="Advancing past Target 30 without executing a full subproject regression suite.",
        reflex=(
            "Configure testing.regressionCommand in .workflow/config.json or pass"
            " REGRESSION_CMD='<full test command>' to verify-regression."
        ),
    )

  eff_timeout, to_err = _resolve_timeout(abs_repo, timeout_seconds)
  if eff_timeout is None:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=to_err,
        rejected="Running verify-regression with an invalid timeout value.",
        reflex="Provide a positive finite timeout via --timeout or testing.regressionTimeoutSeconds.",
    )

  head_sha, head_err = _git_head_sha(abs_repo)
  if not head_sha:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=f"Cannot resolve Git HEAD in {abs_repo}: {head_err}",
        rejected="Sealing a regression receipt outside a valid Git repository with at least one commit.",
        reflex="Run verify-regression inside a valid Git repository checkout with an initial commit.",
    )

  try:
    before_tree_hash = compute_working_tree_hash(abs_repo)
  except (RuntimeError, OSError) as exc:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=f"Failed to compute working-tree hash before regression run: {exc}",
        rejected="Sealing a regression receipt without a verified Git Merkle tree hash.",
        reflex="Ensure Git repository metadata is readable and re-run verify-regression.",
    )

  proc, exec_err = _run_regression_shell(resolved_cmd, abs_sub, eff_timeout)
  if proc is None:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=exec_err,
        rejected="Advancing to Target 40 when the full regression suite timed out or failed to launch.",
        reflex="Fix hangs or runner errors in the active subproject and re-run verify-regression.",
    )

  if proc.returncode != 0:
    _unlink_receipt(receipt_path)
    tail = _single_line((proc.stderr or proc.stdout or "")[-240:])
    detail = f" Output tail: {tail}" if tail else ""
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=(
            f"Full regression command '{resolved_cmd}' in '{rel_sub}'"
            f" exited {proc.returncode}.{detail}"
        ),
        rejected="Locking approved_tree.txt or entering Target 40 while subproject regression tests fail.",
        reflex="Fix all failing regression tests in the active subproject and re-run verify-regression.",
    )

  try:
    after_tree_hash = compute_working_tree_hash(abs_repo)
  except (RuntimeError, OSError) as exc:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=f"Failed to compute working-tree hash after regression run: {exc}",
        rejected="Sealing a regression receipt when post-test tree verification fails.",
        reflex="Ensure Git repository metadata remains intact during tests and re-run verify-regression.",
    )

  if after_tree_hash != before_tree_hash:
    _unlink_receipt(receipt_path)
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_WORKSPACE_MUTATED",
        trigger=(
            f"Regression command '{resolved_cmd}' mutated workspace files"
            f" (before={before_tree_hash}, after={after_tree_hash})."
        ),
        rejected="Sealing a regression receipt when tests create, modify, or delete workspace files.",
        reflex=(
            "Make tests hermetic by writing temporary files to tempfile.TemporaryDirectory(),"
            " clean leaked files, and re-run verify-regression."
        ),
    )

  toolchain_hash = compute_toolchain_hash(abs_repo)
  seal = compute_regression_seal(
      schema_version=RECEIPT_SCHEMA_VERSION,
      subproject=rel_sub,
      regression_cmd=resolved_cmd,
      exit_code=0,
      verified=True,
      tree_hash=before_tree_hash,
      head_sha=head_sha,
      toolchain_hash=toolchain_hash,
      makefile_sha256=makefile_sha256,
  )
  receipt: Dict[str, Any] = {
      "exit_code": 0,
      "head_sha": head_sha,
      "makefile_sha256": makefile_sha256,
      "receipt_seal": seal,
      "regression_cmd": resolved_cmd,
      "schema_version": RECEIPT_SCHEMA_VERSION,
      "subproject": rel_sub,
      "toolchain_hash": toolchain_hash,
      "tree_hash": before_tree_hash,
      "verified": True,
      "verified_at": datetime.now(timezone.utc).isoformat(),
  }
  write_err = _write_receipt_atomic(receipt_path, receipt)
  if write_err is not None:
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUITE_FAILED",
        trigger=f"Could not write regression receipt to {receipt_path}: {write_err}",
        rejected="Proceeding without a persisted regression verification receipt.",
        reflex="Check write permissions on .workflow/state/ and re-run verify-regression.",
    )
  return True, receipt


def verify_regression_receipt(
    repo_root: str,
    *,
    require_approved_tree_match: bool = True,
    expected_subproject: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
  """Independently validates .workflow/state/30_regression_receipt.json against live ground truth.

  Checks receipt presence, schema integrity, canonical Makefile integrity, live
  governance toolchain hash, cryptographic SHA-256 seal via ``hmac.compare_digest``,
  live Git HEAD parity, optional expected subproject match, live working-tree Git
  Merkle hash, and optional ``approved_tree.txt`` parity.

  Args:
    repo_root: Repository root directory.
    require_approved_tree_match: When True and ``approved_tree.txt`` exists,
      requires ``receipt['tree_hash']`` to match ``approved_tree.txt``.
    expected_subproject: Optional explicit subproject path that must validate
      inside ``repo_root`` and match ``receipt['subproject']``.

  Returns:
    Tuple of ``(ok, payload)``. When ``ok`` is True, ``payload`` is the verified
    receipt dictionary. When ``ok`` is False, ``payload`` contains ``scar_id``,
    ``code``, ``trigger``, ``rejected``, and ``reflex``.
  """
  from lib.tdd_guard import compute_toolchain_hash

  abs_repo = os.path.abspath(repo_root)
  norm_expected_sub: Optional[str] = None
  if expected_subproject is not None and str(expected_subproject).strip():
    try:
      _, norm_expected_sub = validate_subproject(abs_repo, str(expected_subproject))
    except ValueError as exc:
      return False, _failure_payload(
          scar_id="TEST-12",
          code="INVALID_SUBPROJECT",
          trigger=str(exc),
          rejected="Verifying a regression receipt against an invalid or escaping subproject path.",
          reflex="Pass a valid subproject path inside the repository via SUBPROJECT or --subproject.",
      )

  receipt_path = os.path.join(abs_repo, DEFAULT_REGRESSION_RECEIPT_REL)
  if not os.path.isfile(receipt_path):
    return False, _failure_payload(
        scar_id="TEST-12",
        code="MISSING_REGRESSION_RECEIPT",
        trigger=f"Missing regression receipt at {receipt_path}.",
        rejected="Entering Target 40 adversarial review before verifying the full subproject regression suite.",
        reflex="Run 'make -f .workflow/state/Makefile verify-regression' in Target 30 before running adversarial-review.",
    )

  try:
    with open(receipt_path, "r", encoding="utf-8") as f:
      receipt = json.load(f)
  except (OSError, ValueError) as exc:
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger=f"Malformed JSON in {receipt_path}: {exc}",
        rejected="Accepting a corrupted or manually edited regression receipt.",
        reflex="Re-run 'make -f .workflow/state/Makefile verify-regression' to generate a fresh sealed receipt.",
    )

  if not isinstance(receipt, dict):
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger=f"Receipt root in {receipt_path} is not a JSON object.",
        rejected="Accepting an invalid regression receipt schema.",
        reflex="Re-run 'make -f .workflow/state/Makefile verify-regression' to generate a valid sealed receipt.",
    )

  schema_version = receipt.get("schema_version")
  verified = receipt.get("verified")
  exit_code = receipt.get("exit_code")
  regression_cmd = receipt.get("regression_cmd")
  subproject = receipt.get("subproject")
  tree_hash = receipt.get("tree_hash")
  head_sha = receipt.get("head_sha")
  toolchain_hash = receipt.get("toolchain_hash")
  makefile_sha256 = receipt.get("makefile_sha256")
  actual_seal = receipt.get("receipt_seal")

  fields_valid = (
      not isinstance(schema_version, bool)
      and schema_version == RECEIPT_SCHEMA_VERSION
      and verified is True
      and not isinstance(exit_code, bool)
      and exit_code == 0
      and isinstance(regression_cmd, str)
      and bool(regression_cmd.strip())
      and isinstance(subproject, str)
      and bool(subproject.strip())
      and isinstance(tree_hash, str)
      and bool(tree_hash.strip())
      and isinstance(head_sha, str)
      and bool(head_sha.strip())
      and isinstance(toolchain_hash, str)
      and bool(toolchain_hash.strip())
      and isinstance(makefile_sha256, str)
      and bool(makefile_sha256.strip())
      and isinstance(actual_seal, str)
      and bool(actual_seal.strip())
  )
  if not fields_valid:
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger=f"Receipt {receipt_path} has missing, invalid, or unverified fields.",
        rejected="Accepting an unverified or altered regression receipt.",
        reflex="Re-run 'make -f .workflow/state/Makefile verify-regression' without manual receipt edits.",
    )

  mk_ok, canonical_mk_sha, mk_err = _check_state_makefile_integrity(abs_repo)
  if not mk_ok or not hmac.compare_digest(makefile_sha256, canonical_mk_sha):
    detail = mk_err or "Sealed makefile_sha256 does not match canonical .workflow/state/Makefile."
    return False, _failure_payload(
        scar_id="ARCH-06",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger=detail,
        rejected="Accepting a regression receipt when .workflow/state/Makefile was tampered or out of sync.",
        reflex=(
            "Restore .workflow/state/Makefile via 'python3 scripts/workflow_gates.py --repo-root . init-makefile'"
            " and re-run verify-regression."
        ),
    )

  live_toolchain = compute_toolchain_hash(abs_repo)
  if not hmac.compare_digest(toolchain_hash, live_toolchain):
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger="Sealed toolchain_hash does not match live core Makeflow governance scripts.",
        rejected="Accepting a regression receipt produced under a different or modified governance toolchain.",
        reflex="Re-run 'make -f .workflow/state/Makefile verify-regression' with the live governance toolchain.",
    )

  expected_seal = compute_regression_seal(
      schema_version=schema_version,
      subproject=subproject,
      regression_cmd=regression_cmd,
      exit_code=exit_code,
      verified=verified,
      tree_hash=tree_hash,
      head_sha=head_sha,
      toolchain_hash=toolchain_hash,
      makefile_sha256=makefile_sha256,
  )
  if not hmac.compare_digest(actual_seal, expected_seal):
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_RECEIPT_TAMPERED",
        trigger=f"Cryptographic seal mismatch in {receipt_path}.",
        rejected="Forging or modifying .workflow/state/30_regression_receipt.json directly.",
        reflex="Re-run 'make -f .workflow/state/Makefile verify-regression' to produce a valid sealed receipt.",
    )

  if norm_expected_sub is not None and not hmac.compare_digest(
      subproject, norm_expected_sub
  ):
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_SUBPROJECT_MISMATCH",
        trigger=(
            f"Sealed regression receipt covers subproject '{subproject}',"
            f" not requested subproject '{norm_expected_sub}'."
        ),
        rejected="Reusing a regression receipt from a different subproject.",
        reflex=(
            f"Run 'make -f .workflow/state/Makefile verify-regression"
            f" SUBPROJECT={norm_expected_sub}' before checking or advancing."
        ),
    )

  live_head, head_err = _git_head_sha(abs_repo)
  if not live_head or not hmac.compare_digest(head_sha, live_head):
    detail = (
        f"Git HEAD drifted since verify-regression (sealed={head_sha}, live={live_head})."
        if live_head
        else f"Cannot verify live Git HEAD in {abs_repo}: {head_err}"
    )
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_HEAD_DRIFT",
        trigger=detail,
        rejected="Entering Target 40 after moving Git HEAD without re-running full regression tests.",
        reflex="Re-run 'make -f .workflow/state/Makefile verify-regression' on the current HEAD before adversarial-review.",
    )

  try:
    live_tree_hash = compute_working_tree_hash(abs_repo)
  except (RuntimeError, OSError) as exc:
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_TREE_DRIFT",
        trigger=f"Failed to compute live working-tree hash: {exc}",
        rejected="Accepting a regression receipt without verifying live working-tree parity.",
        reflex="Run check-regression inside a valid Git repository checkout and re-run verify-regression.",
    )

  if not hmac.compare_digest(tree_hash, live_tree_hash):
    return False, _failure_payload(
        scar_id="TEST-12",
        code="REGRESSION_TREE_DRIFT",
        trigger=(
            f"Working tree drifted since verify-regression"
            f" (sealed={tree_hash}, live={live_tree_hash})."
        ),
        rejected="Entering Target 40 after modifying files without re-running full subproject regression tests.",
        reflex=(
            "Re-run 'make -f .workflow/state/Makefile verify-regression',"
            " re-lock approved_tree.txt, and then run adversarial-review."
        ),
    )

  if require_approved_tree_match:
    approved_path = os.path.join(
        abs_repo, ".workflow", "state", "approved_tree.txt"
    )
    if os.path.isfile(approved_path):
      try:
        with open(approved_path, "r", encoding="utf-8", errors="replace") as f:
          approved_tree = f.read().strip()
      except OSError as exc:
        return False, _failure_payload(
            scar_id="TEST-12",
            code="REGRESSION_TREE_DRIFT",
            trigger=f"Could not read {approved_path}: {exc}",
            rejected="Advancing to Target 40 when approved_tree.txt is unreadable.",
            reflex="Fix permissions on .workflow/state/approved_tree.txt and re-run verify-regression.",
        )
      if not approved_tree or not hmac.compare_digest(tree_hash, approved_tree):
        return False, _failure_payload(
            scar_id="TEST-12",
            code="REGRESSION_TREE_DRIFT",
            trigger=(
                f"Sealed regression tree_hash ({tree_hash}) does not match"
                f" approved_tree.txt ({approved_tree or 'empty'})."
            ),
            rejected="Advancing to Target 40 when regression verification and locked approved_tree.txt diverge.",
            reflex=(
                "Re-run 'make -f .workflow/state/Makefile verify-regression',"
                " update approved_tree.txt with git add -A && git write-tree > .workflow/state/approved_tree.txt,"
                " and re-run adversarial-review."
            ),
        )

  return True, receipt
