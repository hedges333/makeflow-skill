"""Cryptographically anchored TDD Red-to-Green guard (GH-53, SCAR-TEST-12).

Seals witnessed Git HEAD SHA, deterministic test directory tree hash (including
untracked files, symlinks, and ancestor autoload hooks), and exact test command
during witness-red, then verifies them before replaying the command during
verify-green. Emits Vector 4 Reflex directives with exit code 2 and records
forensic collisions on tampering.
"""

import argparse
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
from typing import Any, Dict, List, Optional, Tuple

from lib.base_script import BaseScript, SilentArgumentParser
from lib.reflex import emit_reflex
from lib.workflow_gates import generate_state_makefile

_DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_DEFAULT_REPO_ROOT, "scripts")
DEFAULT_RECEIPT_REL = os.path.join(".workflow", "state", "30_tdd_receipt.json")
DEFAULT_TIMEOUT_SECONDS = 120.0

IGNORED_CACHE_DIRS = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".hypothesis",
    ".git",
    ".workflow",
})
IGNORED_SUFFIXES = (".pyc", ".pyo")
AUTOLOAD_HOOK_FILENAMES = frozenset({
    "conftest.py",
    "sitecustomize.py",
    "usercustomize.py",
})


def _single_line(text: str) -> str:
  """Collapses whitespace and newlines so Vector 4 Reflex fields stay single-line."""
  return " ".join((text or "").split())


def _resolve_test_dir(
    repo_root: str, raw_test_dir: str
) -> Optional[Tuple[str, str]]:
  """Validates that raw_test_dir is an existing subdirectory strictly inside repo_root."""
  cleaned = (raw_test_dir or "").strip()
  if not cleaned:
    return None

  abs_repo = os.path.abspath(repo_root)
  real_repo = os.path.realpath(abs_repo)
  candidate = (
      os.path.abspath(cleaned)
      if os.path.isabs(cleaned)
      else os.path.abspath(os.path.join(abs_repo, cleaned))
  )

  try:
    if os.path.commonpath([abs_repo, candidate]) != abs_repo:
      return None
  except ValueError:
    return None

  if candidate == abs_repo or os.path.islink(candidate):
    return None
  if not os.path.exists(candidate):
    return None

  real_candidate = os.path.realpath(candidate)
  try:
    if os.path.commonpath([real_repo, real_candidate]) != real_repo:
      return None
  except ValueError:
    return None

  if real_candidate == real_repo or not os.path.isdir(real_candidate):
    return None

  rel_dir = os.path.relpath(candidate, abs_repo).replace(os.sep, "/")
  return candidate, rel_dir


def _hash_file_sha256(path: str) -> str:
  """Streams a regular file in 64 KiB chunks and returns its hex SHA-256."""
  file_hasher = hashlib.sha256()
  with open(path, "rb") as f:
    while True:
      chunk = f.read(65536)
      if not chunk:
        break
      file_hasher.update(chunk)
  return file_hasher.hexdigest()


def _hash_symlink_target(path: str) -> str:
  """Hashes the raw symlink target string using surrogateescape encoding."""
  target = os.readlink(path)
  return hashlib.sha256(
      target.encode("utf-8", errors="surrogateescape")
  ).hexdigest()


def compute_test_tree_hash(
    repo_root: str, test_dir: str
) -> Tuple[str, Dict[str, str]]:
  """Computes a deterministic SHA-256 digest and manifest over test_dir and ancestor autoload hooks."""
  abs_repo = os.path.abspath(repo_root)
  abs_test_dir = (
      os.path.abspath(test_dir)
      if os.path.isabs(test_dir)
      else os.path.abspath(os.path.join(abs_repo, test_dir))
  )

  manifest: Dict[str, str] = {}

  ancestor_dirs: List[str] = []
  try:
    is_strict_sub = (
        os.path.commonpath([abs_repo, abs_test_dir]) == abs_repo
        and abs_test_dir != abs_repo
    )
  except ValueError:
    is_strict_sub = False

  if is_strict_sub:
    cur_anc = os.path.dirname(abs_test_dir)
    while True:
      ancestor_dirs.append(cur_anc)
      if cur_anc == abs_repo:
        break
      nxt_anc = os.path.dirname(cur_anc)
      if nxt_anc == cur_anc:
        break
      cur_anc = nxt_anc
    ancestor_dirs.reverse()

  for anc_dir in ancestor_dirs:
    with os.scandir(anc_dir) as entries:
      for entry in sorted(entries, key=lambda e: e.name):
        if (
            entry.name not in AUTOLOAD_HOOK_FILENAMES
            and not entry.name.endswith(".pth")
        ):
          continue
        rel_anc = os.path.relpath(entry.path, abs_repo).replace(os.sep, "/")
        if entry.is_symlink():
          manifest[rel_anc] = f"symlink:{_hash_symlink_target(entry.path)}"
        elif entry.is_file(follow_symlinks=False):
          manifest[rel_anc] = f"sha256:{_hash_file_sha256(entry.path)}"

  for root, dirnames, filenames in os.walk(
      abs_test_dir, topdown=True, followlinks=False
  ):
    kept_dirs: List[str] = []
    for dname in sorted(dirnames):
      full_dir = os.path.join(root, dname)
      if os.path.islink(full_dir):
        rel_dir = os.path.relpath(full_dir, abs_repo).replace(os.sep, "/")
        manifest[rel_dir] = f"symlink:{_hash_symlink_target(full_dir)}"
      elif dname not in IGNORED_CACHE_DIRS:
        kept_dirs.append(dname)
    dirnames[:] = kept_dirs

    for fname in sorted(filenames):
      full_file = os.path.join(root, fname)
      rel_file = os.path.relpath(full_file, abs_repo).replace(os.sep, "/")
      if os.path.islink(full_file):
        manifest[rel_file] = f"symlink:{_hash_symlink_target(full_file)}"
        continue
      if fname.endswith(IGNORED_SUFFIXES):
        continue
      manifest[rel_file] = f"sha256:{_hash_file_sha256(full_file)}"

  sorted_manifest = dict(sorted(manifest.items()))
  rel_root = os.path.relpath(abs_test_dir, abs_repo).replace(os.sep, "/")
  tree_hasher = hashlib.sha256()
  tree_hasher.update(
      f"root:{rel_root}\n".encode("utf-8", errors="surrogateescape")
  )
  for rel_path, entry_digest in sorted_manifest.items():
    tree_hasher.update(
        f"{rel_path}\0{entry_digest}\n".encode(
            "utf-8", errors="surrogateescape"
        )
    )

  return tree_hasher.hexdigest(), sorted_manifest


CORE_GOVERNANCE_FILES = (
    "scripts/tdd_guard.py",
    "scripts/workflow_gates.py",
    "scripts/emit_reflex.py",
    "lib/base_script.py",
    "lib/tdd_guard.py",
    "lib/workflow_gates.py",
    "lib/reflex.py",
)


def compute_toolchain_hash(repo_root: str) -> str:
  """Computes a deterministic SHA-256 digest over core makeflow governance scripts."""
  hasher = hashlib.sha256()
  abs_repo = os.path.abspath(repo_root)
  for rel_path in sorted(CORE_GOVERNANCE_FILES):
    target = os.path.join(abs_repo, rel_path)
    if not os.path.isfile(target):
      alt = os.path.join(_DEFAULT_REPO_ROOT, rel_path)
      if os.path.isfile(alt):
        target = alt
    if os.path.isfile(target):
      hasher.update(f"{rel_path}\0{_hash_file_sha256(target)}\n".encode("utf-8"))
    else:
      hasher.update(f"{rel_path}\0missing\n".encode("utf-8"))
  return hasher.hexdigest()


def _compute_receipt_seal(
    witnessed_head_sha: str,
    test_dir: str,
    test_tree_hash: str,
    test_manifest: Dict[str, str],
    test_cmd: str,
    red_exit_code: int,
    toolchain_hash: str,
) -> str:
  """Computes canonical SHA-256 seal over core Red-phase receipt fields."""
  canonical = json.dumps(
      {
          "red_exit_code": red_exit_code,
          "test_cmd": test_cmd,
          "test_dir": test_dir,
          "test_manifest": test_manifest,
          "test_tree_hash": test_tree_hash,
          "toolchain_hash": toolchain_hash,
          "witnessed_head_sha": witnessed_head_sha,
      },
      sort_keys=True,
      separators=(",", ":"),
  )
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _diff_manifests(
    sealed: Dict[str, str], current: Dict[str, str]
) -> str:
  """Summarizes added, modified, and removed test files for forensic reporting."""
  sealed_keys = set(sealed.keys())
  current_keys = set(current.keys())
  added = sorted(current_keys - sealed_keys)
  removed = sorted(sealed_keys - current_keys)
  modified = sorted(
      k for k in (sealed_keys & current_keys) if sealed[k] != current[k]
  )
  parts: List[str] = []
  if added:
    parts.append(f"added={added}")
  if modified:
    parts.append(f"modified={modified}")
  if removed:
    parts.append(f"removed={removed}")
  return ", ".join(parts) if parts else "tree digest mismatch"


class TddGuardScript(BaseScript):
  """BaseScript runner for witness-red and verify-green TDD verification gates."""

  def __init__(self) -> None:
    super().__init__()
    self.repo_root: str = _DEFAULT_REPO_ROOT
    self.receipt_path: str = ""
    self.as_json: bool = False
    self.timeout: float = DEFAULT_TIMEOUT_SECONDS
    self.subcommand: str = ""
    self.test_dir: str = ""
    self.cmd: str = ""
    self.allow_head_move: bool = False
    self.citations: List[str] = []
    self.make_env: bool = False

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="tdd_guard.py",
        description="Cryptographically anchored TDD Red-to-Green guard (SCAR-TEST-12).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/tdd_guard.py witness-red --test-dir tests --cmd 'python3 -m unittest -v -f tests/test_x.py'\n"
            "  python3 scripts/tdd_guard.py verify-green\n"
            "  python3 scripts/tdd_guard.py verify-citation --cite lib/foo.py:10-25"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=_DEFAULT_REPO_ROOT,
        help="Repository root directory.",
    )
    parser.add_argument(
        "--receipt",
        default=None,
        help="Path to TDD receipt JSON file (defaults to .workflow/state/30_tdd_receipt.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit Vector 4 Reflex errors as JSON.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Timeout in seconds for test command execution (default 120).",
    )
    parser.add_argument(
        "--cite",
        action="append",
        default=[],
        dest="root_cites",
        help="File and line-range citation (<file>:<start>-<end>) for verify-citation.",
    )
    parser.add_argument(
        "--allow-head-move",
        action="store_true",
        dest="root_allow_head_move",
        help="Allow Git HEAD SHA to differ from the witnessed Red-phase HEAD.",
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        parser_class=SilentArgumentParser,
        help="TDD guard subcommand to execute.",
    )

    sub_descriptions = {
        "witness-red": (
            "Witness a failing test run (exit != 0) and seal .workflow/state/30_tdd_receipt.json.",
            "Example:\n  python3 scripts/tdd_guard.py witness-red --test-dir tests --cmd 'python3 -m unittest -v -f tests/test_x.py'",
        ),
        "verify-green": (
            "Verify test tree integrity and replay the sealed test command expecting exit 0.",
            "Example:\n  python3 scripts/tdd_guard.py verify-green",
        ),
        "verify-citation": (
            "Verify that cited file and line ranges exist and match non-empty lines.",
            "Example:\n  python3 scripts/tdd_guard.py verify-citation --cite lib/base_script.py:1-30",
        ),
    }

    for sub_name in ("witness-red", "verify-green", "verify-citation"):
      sub_desc, sub_epilog = sub_descriptions[sub_name]
      sub = subparsers.add_parser(
          sub_name,
          help=sub_desc,
          description=sub_desc,
          epilog=sub_epilog,
          formatter_class=argparse.RawDescriptionHelpFormatter,
      )
      sub.add_argument(
          "--repo-root",
          default=argparse.SUPPRESS,
          help="Repository root directory.",
      )
      sub.add_argument(
          "--receipt",
          default=argparse.SUPPRESS,
          help="Path to TDD receipt JSON file.",
      )
      sub.add_argument(
          "--json",
          action="store_true",
          default=argparse.SUPPRESS,
          dest="sub_json",
          help="Emit Vector 4 Reflex errors as JSON.",
      )
      sub.add_argument(
          "--timeout",
          type=float,
          default=argparse.SUPPRESS,
          help="Timeout in seconds for test command execution.",
      )
      sub.add_argument(
          "--test-dir",
          default=None,
          help="Dedicated test subdirectory inside the repository root.",
      )
      sub.add_argument(
          "--cmd",
          default=None,
          help="Targeted test command to run.",
      )
      sub.add_argument(
          "--make-env",
          action="store_true",
          dest="make_env",
          help="Read TEST_DIR, TEST_CMD, ALLOW_HEAD_MOVE, and CITE from environment variables.",
      )
      if sub_name in ("verify-green", "verify-citation"):
        sub.add_argument(
            "--allow-head-move",
            action="store_true",
            help="Allow Git HEAD SHA to differ from the witnessed Red-phase HEAD.",
        )
        sub.add_argument(
            "--cite",
            action="append",
            default=[],
            dest="cite_args",
            help="File and line-range citation (<file>:<start>-<end>).",
        )

    ns = parser.parse_args(self.raw_args)
    if not ns.subcommand:
      parser.error(
          "Specify a subcommand: witness-red, verify-green, or verify-citation."
      )
    if not math.isfinite(ns.timeout) or ns.timeout <= 0:
      parser.error("--timeout must be a finite positive number.")

    self.repo_root = os.path.abspath(ns.repo_root)
    raw_receipt = ns.receipt
    if raw_receipt:
      self.receipt_path = (
          os.path.abspath(raw_receipt)
          if os.path.isabs(raw_receipt)
          else os.path.abspath(os.path.join(self.repo_root, raw_receipt))
      )
    else:
      self.receipt_path = os.path.join(self.repo_root, DEFAULT_RECEIPT_REL)

    self.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
    self.timeout = ns.timeout
    self.subcommand = ns.subcommand

    use_make_env = bool(getattr(ns, "make_env", False))
    self.make_env = use_make_env
    raw_test_dir = ns.test_dir
    if raw_test_dir is None and use_make_env:
      raw_test_dir = os.environ.get("TEST_DIR", "")
    self.test_dir = raw_test_dir or ""

    raw_cmd = ns.cmd
    if raw_cmd is None and use_make_env:
      raw_cmd = os.environ.get("TEST_CMD", "")
    self.cmd = raw_cmd or ""

    raw_cites = list(getattr(ns, "cite_args", []) or []) + list(
        getattr(ns, "root_cites", []) or []
    )
    if not raw_cites and use_make_env:
      env_cite = os.environ.get("CITE", "")
      if env_cite.strip():
        raw_cites = [
            e.strip() for e in re.split(r"[\n,;]+", env_cite) if e.strip()
        ]
    self.citations = [c.strip() for c in raw_cites]

    env_allow_head = use_make_env and os.environ.get(
        "ALLOW_HEAD_MOVE", ""
    ).strip().lower() in ("1", "true", "yes")
    self.allow_head_move = bool(
        getattr(ns, "allow_head_move", False)
        or getattr(ns, "root_allow_head_move", False)
        or env_allow_head
    )

  def _fail(
      self,
      code: str,
      trigger: str,
      rejected: str,
      reflex: str,
      scar_id: str = "TEST-12",
  ) -> int:
    return emit_reflex(
        stderr=self.stderr,
        scar_id=scar_id,
        violation_code=code,
        trigger=_single_line(trigger),
        rejected=_single_line(rejected),
        reflex=_single_line(reflex),
        as_json=self.as_json,
    )

  def _git_head(self) -> Tuple[Optional[str], str]:
    if not os.path.isdir(self.repo_root):
      return None, f"Repository root does not exist: {self.repo_root}"
    try:
      proc = subprocess.run(
          ["git", "rev-parse", "--verify", "HEAD"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          errors="replace",
          check=False,
          timeout=15,
      )
    except (subprocess.SubprocessError, OSError) as exc:
      return None, str(exc)
    if proc.returncode != 0:
      return None, (
          proc.stderr or proc.stdout or "git rev-parse --verify HEAD failed"
      ).strip()
    sha = (proc.stdout or "").strip()
    return (
        (sha, "")
        if sha
        else (None, "git rev-parse --verify HEAD returned empty SHA")
    )

  def _terminate_process_tree(self, proc: subprocess.Popen) -> None:
    """Terminates the spawned shell process group on POSIX (or process on Windows)."""
    if os.name != "nt":
      try:
        os.killpg(proc.pid, signal.SIGKILL)
      except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
      proc.kill()
    except (ProcessLookupError, OSError):
      pass

  def _run_shell_cmd(
      self, cmd: str
  ) -> Tuple[Optional[subprocess.CompletedProcess], str]:
    proc: Optional[subprocess.Popen] = None
    try:
      proc = subprocess.Popen(
          ["/bin/sh", "-c", cmd],
          cwd=self.repo_root,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          text=True,
          errors="replace",
          start_new_session=(os.name != "nt"),
      )
      try:
        stdout_data, stderr_data = proc.communicate(timeout=self.timeout)
      except subprocess.TimeoutExpired:
        self._terminate_process_tree(proc)
        try:
          proc.communicate(timeout=5)
        except (subprocess.SubprocessError, OSError):
          pass
        return None, f"Test command timed out after {self.timeout}s: {cmd}"
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
      return None, f"Failed to execute test command ({cmd}): {exc}"
    finally:
      if proc is not None:
        if proc.poll() is None:
          self._terminate_process_tree(proc)
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

  def _load_prior_forensic_log(self) -> List[Dict[str, Any]]:
    if not os.path.isfile(self.receipt_path):
      return []
    try:
      with open(self.receipt_path, "r", encoding="utf-8") as f:
        data = json.load(f)
      if isinstance(data, dict) and isinstance(data.get("forensic_log"), list):
        return [
            item for item in data["forensic_log"] if isinstance(item, dict)
        ]
    except (OSError, ValueError, TypeError):
      pass
    return []

  def _write_receipt(self, receipt: Dict[str, Any]) -> Optional[str]:
    tmp_path: Optional[str] = None
    try:
      receipt_dir = os.path.dirname(self.receipt_path)
      os.makedirs(receipt_dir, exist_ok=True)
      with tempfile.NamedTemporaryFile(
          mode="w",
          encoding="utf-8",
          dir=receipt_dir,
          prefix=".tmp_tdd_receipt_",
          suffix=".json",
          delete=False,
      ) as tmp_file:
        tmp_path = tmp_file.name
        json.dump(receipt, tmp_file, indent=2, sort_keys=True)
        tmp_file.write("\n")
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
      os.replace(tmp_path, self.receipt_path)
      tmp_path = None
      return None
    except OSError as exc:
      if tmp_path is not None:
        try:
          os.unlink(tmp_path)
        except OSError:
          pass
      return str(exc)

  def _invalidate_green_state(
      self, receipt: Dict[str, Any], green_exit_code: Optional[int] = None
  ) -> None:
    receipt["verified_green"] = False
    receipt["green_exit_code"] = green_exit_code
    receipt["green_verified_at"] = None
    receipt["verified_head_sha"] = None
    receipt["head_move_allowed"] = False
    receipt.pop("verified_at", None)

  def _persist_rollback(
      self, receipt: Dict[str, Any], green_exit_code: Optional[int] = None
  ) -> str:
    self._invalidate_green_state(receipt, green_exit_code=green_exit_code)
    err = self._write_receipt(receipt)
    return f" (receipt rollback write failed: {err})" if err else ""

  def _record_collision(
      self,
      receipt: Dict[str, Any],
      code: str,
      details: str,
      scar_id: str = "SCAR-TEST-12",
  ) -> str:
    log_entries = receipt.setdefault("forensic_log", [])
    if isinstance(log_entries, list):
      log_entries.append({
          "details": _single_line(details),
          "scar_id": scar_id,
          "timestamp": datetime.now(timezone.utc).isoformat(),
          "violation_code": code,
      })
    return self._persist_rollback(receipt, green_exit_code=None)

  def _witness_red(self) -> int:
    head_sha, git_err = self._git_head()
    if not head_sha:
      return self._fail(
          code="GIT_HEAD_UNAVAILABLE",
          trigger=f"Cannot resolve Git HEAD in {self.repo_root}: {git_err}",
          rejected="Sealing a TDD Red receipt outside a valid Git repository with at least one commit.",
          reflex="Run witness-red inside a valid Git repository checkout with an initial commit.",
      )

    resolved = _resolve_test_dir(self.repo_root, self.test_dir)
    if resolved is None:
      return self._fail(
          code="INVALID_TEST_DIRECTORY",
          trigger=f"Invalid or missing --test-dir '{self.test_dir}' relative to {self.repo_root}.",
          rejected="Sealing TDD Red state without a valid test subdirectory inside the repository root.",
          reflex="Pass an existing test subdirectory inside the repository via --test-dir or TEST_DIR.",
      )
    _, rel_test_dir = resolved

    cleaned_cmd = self.cmd.strip()
    if not cleaned_cmd:
      return self._fail(
          code="EMPTY_TEST_COMMAND",
          trigger="Missing or empty --cmd / TEST_CMD for witness-red.",
          rejected="Sealing a TDD Red receipt without an executable test command.",
          reflex="Provide the targeted failing test command via --cmd or TEST_CMD.",
      )

    try:
      before_hash, before_manifest = compute_test_tree_hash(
          self.repo_root, rel_test_dir
      )
    except OSError as exc:
      return self._fail(
          code="TEST_TREE_HASH_ERROR",
          trigger=f"Failed to hash test directory '{rel_test_dir}': {exc}",
          rejected="Sealing an incomplete or unreadable test directory.",
          reflex="Check file permissions in the test directory and re-run witness-red.",
      )

    proc, exec_err = self._run_shell_cmd(cleaned_cmd)
    if proc is None:
      return self._fail(
          code="TEST_COMMAND_EXECUTION_ERROR",
          trigger=exec_err,
          rejected="Sealing a TDD Red receipt when the test runner timed out or failed to launch.",
          reflex="Fix the test runner invocation or increase --timeout, then re-run witness-red.",
      )

    if proc.returncode == 0:
      return self._fail(
          code="RED_PHASE_PASSED_UNEXPECTEDLY",
          trigger=f"Red-phase test command exited 0 without failing: {cleaned_cmd}",
          rejected="Advancing to Green implementation when the new test already passes before production edits.",
          reflex="Write a test assertion that reproduces the bug or missing feature and fails (exit != 0), then re-run witness-red.",
      )

    try:
      after_hash, after_manifest = compute_test_tree_hash(
          self.repo_root, rel_test_dir
      )
    except OSError as exc:
      return self._fail(
          code="TEST_TREE_HASH_ERROR",
          trigger=f"Failed to verify test directory '{rel_test_dir}' after Red run: {exc}",
          rejected="Sealing an unstable test directory.",
          reflex="Ensure tests do not delete or corrupt test files during execution, then re-run witness-red.",
      )

    if after_hash != before_hash or after_manifest != before_manifest:
      diff_desc = _diff_manifests(before_manifest, after_manifest)
      return self._fail(
          code="EACCES_TEST_TAMPERING",
          trigger=f"Test command mutated files inside '{rel_test_dir}' during Red run ({diff_desc}).",
          rejected="Writing non-cache artifacts inside the sealed test directory during test execution.",
          reflex="Write temporary test outputs inside tempfile.TemporaryDirectory(), clean the test directory, and re-run witness-red.",
      )

    prior_log = self._load_prior_forensic_log()
    toolchain_hash = compute_toolchain_hash(self.repo_root)
    seal = _compute_receipt_seal(
        witnessed_head_sha=head_sha,
        test_dir=rel_test_dir,
        test_tree_hash=before_hash,
        test_manifest=before_manifest,
        test_cmd=cleaned_cmd,
        red_exit_code=proc.returncode,
        toolchain_hash=toolchain_hash,
    )
    receipt: Dict[str, Any] = {
        "forensic_log": prior_log,
        "green_exit_code": None,
        "green_verified_at": None,
        "head_move_allowed": False,
        "receipt_seal": seal,
        "red_exit_code": proc.returncode,
        "test_cmd": cleaned_cmd,
        "test_dir": rel_test_dir,
        "test_manifest": before_manifest,
        "test_tree_hash": before_hash,
        "toolchain_hash": toolchain_hash,
        "verified_green": False,
        "verified_head_sha": None,
        "witnessed_at": datetime.now(timezone.utc).isoformat(),
        "witnessed_head_sha": head_sha,
    }
    write_err = self._write_receipt(receipt)
    if write_err is not None:
      return self._fail(
          code="RECEIPT_WRITE_FAILED",
          trigger=f"Could not write TDD Red receipt to {self.receipt_path}: {write_err}",
          rejected="Proceeding without a persisted TDD Red state receipt.",
          reflex="Check write permissions on .workflow/state/ and re-run witness-red.",
      )
    return 0

  def _load_and_verify_receipt(
      self,
  ) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    if not os.path.isfile(self.receipt_path):
      return None, self._fail(
          code="MISSING_RED_RECEIPT",
          trigger=f"Missing TDD Red receipt at {self.receipt_path}.",
          rejected="Running verify-green before witnessing and sealing a failing Red test.",
          reflex="Write a failing test first and run witness-red to seal 30_tdd_receipt.json before editing production code.",
      )

    try:
      with open(self.receipt_path, "r", encoding="utf-8") as f:
        receipt = json.load(f)
    except (OSError, ValueError) as exc:
      return None, self._fail(
          code="CORRUPT_RED_RECEIPT",
          trigger=f"Malformed JSON in {self.receipt_path}: {exc}",
          rejected="Verifying Green state against an unreadable or corrupted TDD receipt.",
          reflex="Revert manual edits to 30_tdd_receipt.json or re-run witness-red against the failing state before fixing production code.",
      )

    if not isinstance(receipt, dict):
      return None, self._fail(
          code="CORRUPT_RED_RECEIPT",
          trigger=f"Receipt root in {self.receipt_path} is not a JSON object.",
          rejected="Verifying Green state against an invalid receipt schema.",
          reflex="Re-run witness-red to generate a valid 30_tdd_receipt.json.",
      )

    head_sha = receipt.get("witnessed_head_sha")
    test_dir = receipt.get("test_dir")
    tree_hash = receipt.get("test_tree_hash")
    manifest = receipt.get("test_manifest")
    test_cmd = receipt.get("test_cmd")
    red_code = receipt.get("red_exit_code")
    toolchain_hash = receipt.get("toolchain_hash")
    actual_seal = receipt.get("receipt_seal")
    forensic_log = receipt.get("forensic_log")

    valid_manifest = isinstance(manifest, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()
    )
    valid_red_code = (
        not isinstance(red_code, bool)
        and isinstance(red_code, int)
        and red_code != 0
    )
    if not (
        isinstance(head_sha, str)
        and head_sha.strip()
        and isinstance(test_dir, str)
        and test_dir.strip()
        and isinstance(tree_hash, str)
        and tree_hash.strip()
        and valid_manifest
        and isinstance(test_cmd, str)
        and test_cmd.strip()
        and valid_red_code
        and isinstance(toolchain_hash, str)
        and toolchain_hash.strip()
        and isinstance(actual_seal, str)
        and actual_seal.strip()
        and isinstance(forensic_log, list)
    ):
      return None, self._fail(
          code="CORRUPT_RED_RECEIPT",
          trigger=f"Receipt {self.receipt_path} is missing required anchored fields or types.",
          rejected="Verifying Green state against an incomplete or altered receipt.",
          reflex="Restore the original 30_tdd_receipt.json or re-run witness-red before applying the production fix.",
      )

    expected_seal = _compute_receipt_seal(
        witnessed_head_sha=head_sha,
        test_dir=test_dir,
        test_tree_hash=tree_hash,
        test_manifest=manifest,
        test_cmd=test_cmd,
        red_exit_code=red_code,
        toolchain_hash=toolchain_hash,
    )
    if not hmac.compare_digest(actual_seal, expected_seal):
      return None, self._fail(
          code="CORRUPT_RED_RECEIPT",
          trigger=f"Cryptographic seal mismatch in {self.receipt_path} (receipt fields were altered after witness-red).",
          rejected="Modifying 30_tdd_receipt.json directly to bypass command, tree-hash, or HEAD verification.",
          reflex="Revert edits to 30_tdd_receipt.json, keep tests untouched, and fix the defect in production code.",
      )

    return receipt, None

  def _verify_green(self) -> int:
    receipt, err_code = self._load_and_verify_receipt()
    if err_code is not None or receipt is None:
      return err_code if err_code is not None else 2

    sealed_head: str = receipt["witnessed_head_sha"]
    sealed_dir: str = receipt["test_dir"]
    sealed_hash: str = receipt["test_tree_hash"]
    sealed_manifest: Dict[str, str] = receipt["test_manifest"]
    sealed_cmd: str = receipt["test_cmd"]

    # Exploit Vector 1a: Command Swap
    override_cmd = self.cmd.strip()
    if override_cmd and override_cmd != sealed_cmd:
      details = (
          f"Attempted command override '{override_cmd}' does not match sealed "
          f"test_cmd '{sealed_cmd}'."
      )
      rb_note = self._record_collision(receipt, "COMMAND_MISMATCH", details)
      return self._fail(
          code="COMMAND_MISMATCH",
          trigger=f"{details}{rb_note}",
          rejected="Swapping or narrowing TEST_CMD / --cmd during verify-green to manufacture a trivial pass.",
          reflex="Run verify-green without overriding TEST_CMD / --cmd and fix the production code so the sealed test command passes.",
      )

    # Exploit Vector 1b: Test Directory Swap
    override_dir = self.test_dir.strip()
    if override_dir:
      resolved_override = _resolve_test_dir(self.repo_root, override_dir)
      norm_override = resolved_override[1] if resolved_override else override_dir
      if norm_override != sealed_dir:
        details = (
            f"Attempted test_dir override '{override_dir}' does not match "
            f"sealed test_dir '{sealed_dir}'."
        )
        rb_note = self._record_collision(
            receipt, "EACCES_TEST_TAMPERING", details
        )
        return self._fail(
            code="EACCES_TEST_TAMPERING",
            trigger=f"{details}{rb_note}",
            rejected="Pointing verify-green at a different test directory than the one sealed during witness-red.",
            reflex=f"Run verify-green against the sealed '{sealed_dir}' directory without overriding TEST_DIR / --test-dir.",
        )

    # Exploit Vector 3: Mid-Phase Commit / Working-Tree Forgery
    current_head, git_err = self._git_head()
    if not current_head:
      rb_note = self._persist_rollback(receipt, green_exit_code=None)
      return self._fail(
          code="GIT_HEAD_UNAVAILABLE",
          trigger=f"Cannot resolve Git HEAD in {self.repo_root}: {git_err}{rb_note}",
          rejected="Running verify-green when Git HEAD cannot be verified.",
          reflex="Ensure the repository .git metadata is intact and re-run verify-green.",
      )
    if current_head != sealed_head and not self.allow_head_move:
      details = (
          f"Git HEAD moved from witnessed '{sealed_head}' to '{current_head}' "
          "between witness-red and verify-green."
      )
      rb_note = self._record_collision(
          receipt, "HEAD_MOVED_DURING_RED_PHASE", details
      )
      return self._fail(
          code="HEAD_MOVED_DURING_RED_PHASE",
          trigger=f"{details}{rb_note}",
          rejected="Committing mid-phase between witness-red and verify-green to re-baseline working-tree history.",
          reflex="Do not commit during the Red-Green TDD loop; keep HEAD at witnessed_head_sha until Target 50 (or pass --allow-head-move only if explicitly authorized by the human operator).",
      )

    toolchain_rc = self._verify_governance_toolchain(receipt, sealed_head)
    if toolchain_rc is not None:
      return toolchain_rc

    mk_rc = self._verify_state_makefile(receipt)
    if mk_rc is not None:
      return mk_rc

    if self.citations:
      cite_rc = self._verify_citations(receipt, sealed_head)
      if cite_rc != 0:
        return cite_rc

    # Exploit Vector 2: Untracked Fixture Injection / Test File Modification
    resolved_sealed = _resolve_test_dir(self.repo_root, sealed_dir)
    if resolved_sealed is None:
      details = f"Sealed test directory '{sealed_dir}' was deleted, moved, or replaced with a non-directory."
      rb_note = self._record_collision(
          receipt, "EACCES_TEST_TAMPERING", details
      )
      return self._fail(
          code="EACCES_TEST_TAMPERING",
          trigger=f"{details}{rb_note}",
          rejected="Deleting or replacing the sealed test directory during the Green phase.",
          reflex=f"Restore '{sealed_dir}' to its exact Red-phase state and fix the defect in production code.",
      )

    try:
      current_hash, current_manifest = compute_test_tree_hash(
          self.repo_root, sealed_dir
      )
    except OSError as exc:
      rb_note = self._persist_rollback(receipt, green_exit_code=None)
      return self._fail(
          code="TEST_TREE_HASH_ERROR",
          trigger=f"Failed to hash sealed test directory '{sealed_dir}': {exc}{rb_note}",
          rejected="Verifying Green state when test files cannot be read.",
          reflex=f"Check permissions inside '{sealed_dir}' and re-run verify-green.",
      )

    if current_hash != sealed_hash or current_manifest != sealed_manifest:
      diff_desc = _diff_manifests(sealed_manifest, current_manifest)
      details = (
          f"Test directory '{sealed_dir}' drifted from sealed Red tree hash "
          f"({diff_desc})."
      )
      rb_note = self._record_collision(
          receipt, "EACCES_TEST_TAMPERING", details
      )
      return self._fail(
          code="EACCES_TEST_TAMPERING",
          trigger=f"{details}{rb_note}",
          rejected="Modifying tracked tests or injecting untracked fixtures (such as conftest.py) during the Green phase.",
          reflex=f"Revert all changes and untracked files in '{sealed_dir}' ({diff_desc}) and fix the bug in production code.",
      )

    # Replay sealed test command
    proc, exec_err = self._run_shell_cmd(sealed_cmd)
    if proc is None:
      rb_note = self._persist_rollback(receipt, green_exit_code=None)
      return self._fail(
          code="TEST_COMMAND_EXECUTION_ERROR",
          trigger=f"{exec_err}{rb_note}",
          rejected="Marking Green verification passed when the sealed test command timed out or failed to execute.",
          reflex="Fix infinite loops or runtime errors in production code and re-run verify-green.",
      )

    # Verify the test run itself did not mutate sealed test files
    try:
      post_hash, post_manifest = compute_test_tree_hash(
          self.repo_root, sealed_dir
      )
    except OSError as exc:
      rb_note = self._persist_rollback(receipt, green_exit_code=None)
      return self._fail(
          code="TEST_TREE_HASH_ERROR",
          trigger=f"Failed to re-verify '{sealed_dir}' after test execution: {exc}{rb_note}",
          rejected="Accepting Green state when the test directory became unreadable during test execution.",
          reflex=f"Ensure tests keep '{sealed_dir}' intact and re-run verify-green.",
      )
    if post_hash != sealed_hash or post_manifest != sealed_manifest:
      diff_desc = _diff_manifests(sealed_manifest, post_manifest)
      details = (
          f"Test execution mutated '{sealed_dir}' during verify-green ({diff_desc})."
      )
      rb_note = self._record_collision(
          receipt, "EACCES_TEST_TAMPERING", details
      )
      return self._fail(
          code="EACCES_TEST_TAMPERING",
          trigger=f"{details}{rb_note}",
          rejected="Mutating files inside the sealed test directory during Green test execution.",
          reflex=f"Restore '{sealed_dir}' ({diff_desc}), write test artifacts only to temporary directories, and re-run verify-green.",
      )

    if proc.returncode != 0:
      rb_note = self._persist_rollback(receipt, green_exit_code=proc.returncode)
      tail = _single_line((proc.stderr or proc.stdout or "")[-240:])
      detail_suffix = f" Output tail: {tail}" if tail else ""
      return self._fail(
          code="GREEN_VERIFICATION_FAILED",
          trigger=f"Sealed test command '{sealed_cmd}' exited {proc.returncode}.{detail_suffix}{rb_note}",
          rejected="Advancing past Target 30 while the sealed Red test still fails.",
          reflex=f"Keep '{sealed_dir}' untouched, fix the implementation in production source files, and re-run verify-green.",
      )

    receipt["verified_green"] = True
    receipt["green_exit_code"] = 0
    receipt["green_verified_at"] = datetime.now(timezone.utc).isoformat()
    receipt["verified_head_sha"] = current_head
    receipt["head_move_allowed"] = bool(
        self.allow_head_move and current_head != sealed_head
    )
    receipt.pop("verified_at", None)
    write_err = self._write_receipt(receipt)
    if write_err is not None:
      return self._fail(
          code="RECEIPT_WRITE_FAILED",
          trigger=f"Could not update TDD receipt at {self.receipt_path}: {write_err}",
          rejected="Completing verify-green without persisting verified_green state.",
          reflex="Check write permissions on .workflow/state/ and re-run verify-green.",
      )
    return 0

  def _verify_governance_toolchain(
      self, receipt: Dict[str, Any], sealed_head: str
  ) -> Optional[int]:
    sealed_hash = receipt.get("toolchain_hash")
    current_hash = compute_toolchain_hash(self.repo_root)
    hasher_mismatch = bool(sealed_hash and current_hash != sealed_hash)

    diff_tampered = False
    diff_desc = ""
    try:
      diff_proc = subprocess.run(
          ["git", "diff", "--name-only", sealed_head, "--", "scripts/", "lib/"],
          cwd=self.repo_root,
          capture_output=True,
          text=True,
          errors="replace",
          check=False,
          timeout=15,
      )
      if diff_proc.returncode == 0 and diff_proc.stdout.strip():
        raw_lines = [
            f.strip().replace(os.sep, "/")
            for f in diff_proc.stdout.splitlines()
            if f.strip()
        ]
        modified = [
            f
            for f in raw_lines
            if f in CORE_GOVERNANCE_FILES
            or any(
                f.endswith("/" + os.path.basename(p))
                for p in CORE_GOVERNANCE_FILES
            )
        ]
        if modified:
          diff_tampered = True
          diff_desc = ", ".join(modified)
    except (subprocess.SubprocessError, OSError):
      pass

    if hasher_mismatch or diff_tampered:
      details = (
          "Core governance toolchain in 'scripts/' or 'lib/' was tampered relative to witnessed state"
          + (f" ({diff_desc})" if diff_desc else " (toolchain digest mismatch).")
      )
      rb_note = self._record_collision(
          receipt, "GOVERNANCE_TOOLCHAIN_TAMPERED", details, scar_id="SCAR-TEST-12"
      )
      return self._fail(
          code="GOVERNANCE_TOOLCHAIN_TAMPERED",
          trigger=f"{details}{rb_note}",
          rejected="Modifying or tampering with core makeflow governance scripts during the Green phase.",
          reflex="Revert all changes to scripts/ and lib/ relative to witnessed_head_sha and fix the defect strictly in production code.",
          scar_id="TEST-12",
      )
    return None

  def _verify_state_makefile(
      self, receipt: Dict[str, Any]
  ) -> Optional[int]:
    mk_path = os.path.join(self.repo_root, ".workflow", "state", "Makefile")
    if os.path.exists(mk_path):
      try:
        with open(mk_path, "r", encoding="utf-8") as f:
          mk_content = f.read()
      except OSError as exc:
        rb_note = self._persist_rollback(receipt, green_exit_code=None)
        return self._fail(
            code="STATE_MAKEFILE_TAMPERED",
            trigger=f"Failed to read {mk_path}: {exc}{rb_note}",
            rejected="Verifying Green state when .workflow/state/Makefile is unreadable.",
            reflex="Check filesystem permissions on .workflow/state/Makefile.",
            scar_id="ARCH-06",
        )
      canonical = generate_state_makefile(self.repo_root)
      if mk_content != canonical:
        details = (
            ".workflow/state/Makefile does not match canonical synthesis template."
        )
        rb_note = self._record_collision(
            receipt, "STATE_MAKEFILE_TAMPERED", details, scar_id="SCAR-ARCH-06"
        )
        return self._fail(
            code="STATE_MAKEFILE_TAMPERED",
            trigger=f"{details}{rb_note}",
            rejected="Tampering with .workflow/state/Makefile to alter test execution, flags, or workflow gates.",
            reflex="Re-run 'python3 scripts/workflow_gates.py --repo-root . init-makefile' to restore canonical Makefile.",
            scar_id="ARCH-06",
        )
    return None

  def _verify_citations(
      self, receipt: Dict[str, Any], sealed_head: str
  ) -> int:
    comment_prefixes = ("#", "//", "/*", "*", "*/", "--", "<!--")
    for raw_cite in self.citations:
      cleaned_cite = raw_cite.strip()
      if not cleaned_cite or ":" not in cleaned_cite:
        details = (
            f"Citation '{cleaned_cite}' is malformed (missing colon separator)."
        )
        rb_note = self._record_collision(
            receipt, "TRIVIAL_WITNESS_LINE", details
        )
        return self._fail(
            code="TRIVIAL_WITNESS_LINE",
            trigger=f"{details}{rb_note}",
            rejected="Citing invalid or malformed specification line references.",
            reflex="Pass citations in the format <rel_path>:<line>.",
        )

      rel_path, _, line_str = cleaned_cite.rpartition(":")
      rel_path = rel_path.strip().replace(os.sep, "/")
      line_str = line_str.strip()

      norm_parts = [p for p in rel_path.split("/") if p and p != "."]
      if (
          not rel_path
          or rel_path.startswith("/")
          or ".." in norm_parts
          or os.path.isabs(rel_path)
      ):
        details = f"Citation path '{rel_path}' is an absolute path or path traversal."
        rb_note = self._record_collision(
            receipt, "UNCOMMITTED_WITNESS_FILE", details
        )
        return self._fail(
            code="UNCOMMITTED_WITNESS_FILE",
            trigger=f"{details}{rb_note}",
            rejected="Citing absolute or traversal paths outside the repository Git tree.",
            reflex="Pass a relative path to a regular file blob committed in witnessed_head_sha.",
        )

      try:
        line_no = int(line_str)
      except ValueError:
        details = f"Citation line '{line_str}' in '{cleaned_cite}' is not a valid integer."
        rb_note = self._record_collision(
            receipt, "TRIVIAL_WITNESS_LINE", details
        )
        return self._fail(
            code="TRIVIAL_WITNESS_LINE",
            trigger=f"{details}{rb_note}",
            rejected="Citing non-integer line numbers.",
            reflex="Pass a positive 1-based line number in <rel_path>:<line>.",
        )

      if line_no <= 0:
        details = f"Citation line {line_no} in '{cleaned_cite}' is not positive."
        rb_note = self._record_collision(
            receipt, "TRIVIAL_WITNESS_LINE", details
        )
        return self._fail(
            code="TRIVIAL_WITNESS_LINE",
            trigger=f"{details}{rb_note}",
            rejected="Citing non-positive line numbers.",
            reflex="Pass a positive 1-based line number in <rel_path>:<line>.",
        )

      try:
        type_proc = subprocess.run(
            ["git", "cat-file", "-t", f"{sealed_head}:{rel_path}"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=15,
        )
      except (subprocess.SubprocessError, OSError) as exc:
        rb_note = self._persist_rollback(receipt, green_exit_code=None)
        return self._fail(
            code="GIT_HEAD_UNAVAILABLE",
            trigger=f"Failed to inspect Git blob for '{rel_path}': {exc}{rb_note}",
            rejected="Verifying citations when Git metadata is unreadable.",
            reflex="Ensure Git repository metadata is intact and retry.",
        )

      if type_proc.returncode != 0 or type_proc.stdout.strip() != "blob":
        obj_type = type_proc.stdout.strip() or "nonexistent"
        details = (
            f"Cited path '{rel_path}' is not a committed regular file blob in "
            f"witnessed HEAD '{sealed_head}' (git cat-file -t returned '{obj_type}')."
        )
        rb_note = self._record_collision(
            receipt, "UNCOMMITTED_WITNESS_FILE", details
        )
        return self._fail(
            code="UNCOMMITTED_WITNESS_FILE",
            trigger=f"{details}{rb_note}",
            rejected="Citing uncommitted working-tree files, trees, or non-blob objects as specification witnesses.",
            reflex="Commit the specification file before running witness-red so it exists in witnessed_head_sha.",
        )

      try:
        show_proc = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "show",
                f"{sealed_head}:{rel_path}",
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=15,
        )
      except (subprocess.SubprocessError, OSError) as exc:
        rb_note = self._persist_rollback(receipt, green_exit_code=None)
        return self._fail(
            code="GIT_HEAD_UNAVAILABLE",
            trigger=f"Failed to stream Git blob for '{rel_path}': {exc}{rb_note}",
            rejected="Verifying citations when Git blob streaming fails.",
            reflex="Ensure Git repository metadata is intact and retry.",
        )

      if show_proc.returncode != 0:
        details = (
            f"Failed to stream blob '{sealed_head}:{rel_path}': "
            f"{(show_proc.stderr or show_proc.stdout).strip()}"
        )
        rb_note = self._record_collision(
            receipt, "UNCOMMITTED_WITNESS_FILE", details
        )
        return self._fail(
            code="UNCOMMITTED_WITNESS_FILE",
            trigger=f"{details}{rb_note}",
            rejected="Citing unreadable Git blobs.",
            reflex="Verify the blob exists in witnessed_head_sha and retry.",
        )

      blob_lines = show_proc.stdout.splitlines()
      total_lines = len(blob_lines)
      if line_no > total_lines:
        details = (
            f"Cited line {line_no} exceeds total line count ({total_lines}) "
            f"in '{rel_path}' at witnessed HEAD '{sealed_head}'."
        )
        rb_note = self._record_collision(
            receipt, "TRIVIAL_WITNESS_LINE", details
        )
        return self._fail(
            code="TRIVIAL_WITNESS_LINE",
            trigger=f"{details}{rb_note}",
            rejected="Citing out-of-range line numbers.",
            reflex=f"Cite a valid line number between 1 and {total_lines}.",
        )

      target_line = blob_lines[line_no - 1]
      stripped_line = target_line.strip()
      if not stripped_line:
        details = (
            f"Cited line {line_no} in '{rel_path}' is blank or whitespace only."
        )
        rb_note = self._record_collision(
            receipt, "TRIVIAL_WITNESS_LINE", details
        )
        return self._fail(
            code="TRIVIAL_WITNESS_LINE",
            trigger=f"{details}{rb_note}",
            rejected="Citing blank lines as specification witnesses.",
            reflex="Cite an active code statement line in the specification file.",
        )

      if stripped_line.startswith(comment_prefixes):
        details = (
            f"Cited line {line_no} in '{rel_path}' is a pure comment: '{stripped_line[:40]}'."
        )
        rb_note = self._record_collision(
            receipt, "TRIVIAL_WITNESS_LINE", details
        )
        return self._fail(
            code="TRIVIAL_WITNESS_LINE",
            trigger=f"{details}{rb_note}",
            rejected="Citing comment lines as specification witnesses.",
            reflex="Cite an active code statement line in the specification file.",
        )

    return 0

  def _verify_citation(self) -> int:
    receipt, err_code = self._load_and_verify_receipt()
    if err_code is not None or receipt is None:
      return err_code if err_code is not None else 2

    sealed_head: str = receipt["witnessed_head_sha"]

    current_head, git_err = self._git_head()
    if not current_head:
      rb_note = self._persist_rollback(receipt, green_exit_code=None)
      return self._fail(
          code="GIT_HEAD_UNAVAILABLE",
          trigger=f"Cannot resolve Git HEAD in {self.repo_root}: {git_err}{rb_note}",
          rejected="Running verify-citation when Git HEAD cannot be verified.",
          reflex="Ensure the repository .git metadata is intact and re-run verify-citation.",
      )

    if current_head != sealed_head and not self.allow_head_move:
      details = (
          f"Git HEAD moved from witnessed '{sealed_head}' to '{current_head}' "
          "between witness-red and verify-citation."
      )
      rb_note = self._record_collision(
          receipt, "HEAD_MOVED_DURING_RED_PHASE", details
      )
      return self._fail(
          code="HEAD_MOVED_DURING_RED_PHASE",
          trigger=f"{details}{rb_note}",
          rejected="Committing mid-phase between witness-red and verify-citation to re-baseline working-tree history.",
          reflex="Do not commit during the Red-Green TDD loop; keep HEAD at witnessed_head_sha until Target 50 (or pass --allow-head-move only if explicitly authorized by the human operator).",
      )

    toolchain_rc = self._verify_governance_toolchain(receipt, sealed_head)
    if toolchain_rc is not None:
      return toolchain_rc

    mk_rc = self._verify_state_makefile(receipt)
    if mk_rc is not None:
      return mk_rc

    if not self.citations:
      details = "Missing citation specification for verify-citation."
      rb_note = self._record_collision(
          receipt, "TRIVIAL_WITNESS_LINE", details
      )
      return self._fail(
          code="TRIVIAL_WITNESS_LINE",
          trigger=f"{details}{rb_note}",
          rejected="Running verify-citation without providing at least one specification citation via --cite or CITE.",
          reflex="Provide an active code statement line citation via --cite <rel_path>:<line> or CITE environment variable.",
      )

    return self._verify_citations(receipt, sealed_head)

  def run(self) -> int:
    if self.subcommand == "witness-red":
      return self._witness_red()
    if self.subcommand == "verify-green":
      return self._verify_green()
    if self.subcommand == "verify-citation":
      return self._verify_citation()
    raise ValueError(f"Unsupported subcommand: {self.subcommand}")
