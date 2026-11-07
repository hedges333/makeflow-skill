#!/usr/bin/env python3
"""Hermetic unit and Make boundary tests for cryptographically anchored TDD receipts (GH-53, SCAR-TEST-12)."""

import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from base_script import BaseScript
from tdd_guard import TddGuardScript, compute_test_tree_hash
from workflow_gates import WorkflowGatesScript


def _init_fixture_repo(repo_dir: str) -> str:
  """Initializes an isolated Git repo with src/calc.py (buggy) and tests/test_calc.py."""
  subprocess.run(
      ["git", "init", "-q"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  subprocess.run(
      ["git", "config", "user.email", "test@example.com"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  subprocess.run(
      ["git", "config", "user.name", "Makeflow TDD Guard Test"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  exclude_path = os.path.join(repo_dir, ".git", "info", "exclude")
  os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
  with open(exclude_path, "a", encoding="utf-8") as f:
    f.write("\n.workflow/state/\n.workflow/archive/\n")

  src_dir = os.path.join(repo_dir, "src")
  tests_dir = os.path.join(repo_dir, "tests")
  os.makedirs(src_dir, exist_ok=True)
  os.makedirs(tests_dir, exist_ok=True)

  with open(os.path.join(src_dir, "__init__.py"), "w", encoding="utf-8") as f:
    f.write("")
  with open(os.path.join(src_dir, "calc.py"), "w", encoding="utf-8") as f:
    f.write("def add(a: int, b: int) -> int:\n  return a - b\n")

  with open(os.path.join(tests_dir, "__init__.py"), "w", encoding="utf-8") as f:
    f.write("")
  with open(
      os.path.join(tests_dir, "test_calc.py"), "w", encoding="utf-8"
  ) as f:
    f.write(
        "import unittest\n"
        "from src.calc import add\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "  def test_add(self):\n"
        "    self.assertEqual(add(2, 3), 5)\n\n"
        "if __name__ == '__main__':\n"
        "  unittest.main()\n"
    )

  subprocess.run(
      ["git", "add", "-A"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  subprocess.run(
      ["git", "commit", "-q", "-m", "chore: seed buggy calc and failing test"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  head_proc = subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=repo_dir,
      check=True,
      capture_output=True,
      text=True,
  )
  return head_proc.stdout.strip()


def _unit_test_cmd() -> str:
  """Returns a shell command using sys.executable so tests inherit the active interpreter."""
  return f"{shlex.quote(sys.executable)} -m unittest discover -s tests -p 'test_*.py'"


def _fix_calc_bug(repo_dir: str) -> None:
  """Updates src/calc.py so the failing unit test passes without touching tests/."""
  with open(os.path.join(repo_dir, "src", "calc.py"), "w", encoding="utf-8") as f:
    f.write(
        "def add(a: int, b: int) -> int:\n"
        "  result = a + b\n"
        "  return result\n"
    )


def _receipt_path(repo_dir: str) -> str:
  return os.path.join(repo_dir, ".workflow", "state", "30_tdd_receipt.json")


class TestTddGuardScript(unittest.TestCase):
  """Hermetic unit tests for TddGuardScript (SCAR-ARCH-05, SCAR-TEST-12)."""

  def test_inherits_base_script_and_isolates_streams(self):
    self.assertTrue(issubclass(TddGuardScript, BaseScript))
    invalid_arg_sets = [
        [],
        ["--unknown-flag"],
        ["witness-red", "--timeout", "0"],
        ["witness-red", "--timeout", "-5"],
        ["witness-red", "--timeout", "-1"],
        ["witness-red", "--timeout", "nan"],
        ["witness-red", "--timeout", "inf"],
        ["witness-red", "--timeout", "-inf"],
    ]
    for bad_args in invalid_arg_sets:
      out = io.StringIO()
      err = io.StringIO()
      code = TddGuardScript().execute(args=bad_args, stdout=out, stderr=err)
      self.assertEqual(code, 1, f"Expected exit code 1 for args={bad_args}")
      self.assertEqual(out.getvalue(), "")
      self.assertIn("Error:", err.getvalue())

  def test_witness_red_happy_path_seals_receipt(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      expected_head = _init_fixture_repo(tmpdir)
      expected_hash, expected_manifest = compute_test_tree_hash(tmpdir, "tests")
      cmd = _unit_test_cmd()

      out = io.StringIO()
      err = io.StringIO()
      code = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "witness-red",
              "--test-dir",
              "tests",
              "--cmd",
              cmd,
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, err.getvalue())
      self.assertEqual(err.getvalue(), "")

      rpath = _receipt_path(tmpdir)
      self.assertTrue(os.path.isfile(rpath))
      with open(rpath, "r", encoding="utf-8") as f:
        receipt = json.load(f)

      self.assertEqual(receipt["witnessed_head_sha"], expected_head)
      self.assertEqual(receipt["test_dir"], "tests")
      self.assertEqual(receipt["test_tree_hash"], expected_hash)
      self.assertEqual(receipt["test_manifest"], expected_manifest)
      self.assertIn("tests/test_calc.py", receipt["test_manifest"])
      self.assertEqual(receipt["test_cmd"], cmd)
      self.assertNotEqual(receipt["red_exit_code"], 0)
      self.assertTrue(receipt["receipt_seal"])
      self.assertEqual(receipt["forensic_log"], [])
      self.assertFalse(receipt["verified_green"])

  def test_witness_red_rejects_passing_command(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      _fix_calc_bug(tmpdir)

      err = io.StringIO()
      code = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "witness-red",
              "--test-dir",
              "tests",
              "--cmd",
              _unit_test_cmd(),
          ],
          stdout=io.StringIO(),
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] RED_PHASE_PASSED_UNEXPECTEDLY", err.getvalue()
      )
      self.assertFalse(os.path.exists(_receipt_path(tmpdir)))

  def test_witness_red_rejects_invalid_test_dir_and_empty_cmd(self):
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside_dir:
      _init_fixture_repo(tmpdir)
      escape_link = os.path.join(tmpdir, "escape_tests")
      os.symlink(outside_dir, escape_link)

      bad_dir_cases = [
          "",
          "missing_dir",
          "../outside",
          outside_dir,
          "src/calc.py",
          ".",
          "escape_tests",
      ]
      for bad_dir in bad_dir_cases:
        err = io.StringIO()
        code = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "witness-red",
                "--test-dir",
                bad_dir,
                "--cmd",
                _unit_test_cmd(),
            ],
            stdout=io.StringIO(),
            stderr=err,
        )
        self.assertEqual(code, 2, f"Expected exit 2 for --test-dir={bad_dir!r}")
        self.assertIn("[SCAR-ID: TEST-12]", err.getvalue())

      for empty_cmd in ("", "   "):
        err_cmd = io.StringIO()
        code_cmd = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "witness-red",
                "--test-dir",
                "tests",
                "--cmd",
                empty_cmd,
            ],
            stdout=io.StringIO(),
            stderr=err_cmd,
        )
        self.assertEqual(code_cmd, 2)
        self.assertIn("[SCAR-ID: TEST-12]", err_cmd.getvalue())

  def test_verify_green_happy_path_and_transient_cache_immunity(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      cmd = _unit_test_cmd()

      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  cmd,
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Simulate transient caches created inside tests/ during test runs
      pycache_dir = os.path.join(tmpdir, "tests", "__pycache__")
      pytest_cache_dir = os.path.join(tmpdir, "tests", ".pytest_cache", "v")
      os.makedirs(pycache_dir, exist_ok=True)
      os.makedirs(pytest_cache_dir, exist_ok=True)
      with open(
          os.path.join(pycache_dir, "test_calc.cpython-312.pyc"), "wb"
      ) as f:
        f.write(b"\x00\x01\x02\x03")
      with open(os.path.join(pytest_cache_dir, "nodeids"), "w", encoding="utf-8") as f:
        f.write("[]\n")
      with open(os.path.join(tmpdir, "tests", "stale.pyc"), "wb") as f:
        f.write(b"\x04\x05")

      # Fix production defect in src/ without modifying tests/ or committing
      _fix_calc_bug(tmpdir)

      out = io.StringIO()
      err = io.StringIO()
      code = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, err.getvalue())
      self.assertEqual(err.getvalue(), "")

      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertTrue(receipt["verified_green"])
      self.assertEqual(receipt["green_exit_code"], 0)
      self.assertIsInstance(receipt["green_verified_at"], str)
      self.assertTrue(receipt["green_verified_at"])
      self.assertFalse(receipt["head_move_allowed"])
      self.assertEqual(receipt["forensic_log"], [])

  def test_exploit_vector_1_command_and_test_dir_swap(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      os.makedirs(os.path.join(tmpdir, "other_tests"), exist_ok=True)

      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # 1a. Attempt command swap with trivial 'true'
      err_swap = io.StringIO()
      code_swap = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green", "--cmd", "true"],
          stdout=io.StringIO(),
          stderr=err_swap,
      )
      self.assertEqual(code_swap, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] COMMAND_MISMATCH", err_swap.getvalue()
      )

      # 1b. Attempt --test-dir swap
      err_dir = io.StringIO()
      code_dir = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-green",
              "--test-dir",
              "other_tests",
          ],
          stdout=io.StringIO(),
          stderr=err_dir,
      )
      self.assertEqual(code_dir, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_dir.getvalue()
      )

      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        receipt = json.load(f)
      codes = [entry["violation_code"] for entry in receipt["forensic_log"]]
      self.assertEqual(codes, ["COMMAND_MISMATCH", "EACCES_TEST_TAMPERING"])

  def test_exploit_vector_2_untracked_conftest_and_test_edits(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # 2a. Drop untracked tests/conftest.py (never git add'ed)
      conftest_path = os.path.join(tmpdir, "tests", "conftest.py")
      with open(conftest_path, "w", encoding="utf-8") as f:
        f.write("import src.calc\nsrc.calc.add = lambda a, b: 5\n")

      err_conftest = io.StringIO()
      code_conftest = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_conftest,
      )
      self.assertEqual(code_conftest, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_conftest.getvalue()
      )
      self.assertIn("conftest.py", err_conftest.getvalue())
      os.remove(conftest_path)

      # 2b. Modify tracked test file assertions
      test_calc_path = os.path.join(tmpdir, "tests", "test_calc.py")
      with open(test_calc_path, "r", encoding="utf-8") as f:
        orig_test_content = f.read()
      with open(test_calc_path, "w", encoding="utf-8") as f:
        f.write(orig_test_content.replace("5", "-1"))

      err_mod = io.StringIO()
      code_mod = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_mod,
      )
      self.assertEqual(code_mod, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_mod.getvalue()
      )
      self.assertIn("test_calc.py", err_mod.getvalue())

      # 2c. Delete tracked test file
      os.remove(test_calc_path)
      err_del = io.StringIO()
      code_del = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_del,
      )
      self.assertEqual(code_del, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_del.getvalue()
      )

      # Restore tracked test file, then test adding a symlink inside tests/
      with open(test_calc_path, "w", encoding="utf-8") as f:
        f.write(orig_test_content)
      sym_path = os.path.join(tmpdir, "tests", "injected_link.py")
      os.symlink(os.path.join(tmpdir, "src", "calc.py"), sym_path)
      err_sym = io.StringIO()
      code_sym = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_sym,
      )
      self.assertEqual(code_sym, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_sym.getvalue()
      )
      os.unlink(sym_path)

      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertEqual(len(receipt["forensic_log"]), 4)
      self.assertIn("conftest.py", receipt["forensic_log"][0]["details"])

  def test_exploit_vector_3_mid_phase_commit_and_allow_head_move(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      _fix_calc_bug(tmpdir)
      subprocess.run(
          ["git", "add", "src/calc.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "wip: mid-phase commit"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      err_head = io.StringIO()
      code_head = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_head,
      )
      self.assertEqual(code_head, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] HEAD_MOVED_DURING_RED_PHASE", err_head.getvalue()
      )

      # Explicit --allow-head-move succeeds while preserving forensic collision history
      err_allow = io.StringIO()
      code_allow = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green", "--allow-head-move"],
          stdout=io.StringIO(),
          stderr=err_allow,
      )
      self.assertEqual(code_allow, 0, err_allow.getvalue())

      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertTrue(receipt["verified_green"])
      self.assertTrue(receipt["head_move_allowed"])
      self.assertEqual(len(receipt["forensic_log"]), 1)
      self.assertEqual(
          receipt["forensic_log"][0]["violation_code"],
          "HEAD_MOVED_DURING_RED_PHASE",
      )

  def test_forensic_collision_persistence_across_recovery_and_rewitness(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Trigger command swap + untracked conftest collisions
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green", "--cmd", "true"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          2,
      )
      conftest_path = os.path.join(tmpdir, "tests", "conftest.py")
      with open(conftest_path, "w", encoding="utf-8") as f:
        f.write("# injected\n")
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          2,
      )

      # Revert tampering and re-run witness-red: prior forensic_log must remain intact
      os.remove(conftest_path)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        after_rewitness = json.load(f)
      self.assertEqual(len(after_rewitness["forensic_log"]), 2)

      # Fix src/calc.py and run verify-green: succeeds while keeping forensic_log
      _fix_calc_bug(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        final_receipt = json.load(f)
      self.assertTrue(final_receipt["verified_green"])
      self.assertEqual(len(final_receipt["forensic_log"]), 2)

  def test_direct_json_tampering_missing_receipt_and_failing_green(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)

      # 1. Missing receipt
      err_missing = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_missing,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] MISSING_RED_RECEIPT", err_missing.getvalue()
      )

      # 2. Malformed JSON receipt
      rpath = _receipt_path(tmpdir)
      os.makedirs(os.path.dirname(rpath), exist_ok=True)
      with open(rpath, "w", encoding="utf-8") as f:
        f.write("{not valid json")
      err_malformed = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_malformed,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] CORRUPT_RED_RECEIPT", err_malformed.getvalue()
      )
      os.remove(rpath)

      # Seal valid receipt
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # 3. Failing green test (bug in src/calc.py not fixed yet)
      err_still_red = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_still_red,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] GREEN_VERIFICATION_FAILED",
          err_still_red.getvalue(),
      )

      # 4. Direct JSON tampering of test_cmd without updating receipt_seal
      with open(rpath, "r", encoding="utf-8") as f:
        tampered = json.load(f)
      orig_cmd = tampered["test_cmd"]
      tampered["test_cmd"] = "true"
      with open(rpath, "w", encoding="utf-8") as f:
        json.dump(tampered, f)

      err_seal_cmd = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_seal_cmd,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] CORRUPT_RED_RECEIPT", err_seal_cmd.getvalue()
      )

      # 5. Direct JSON tampering of test_tree_hash without updating receipt_seal
      tampered["test_cmd"] = orig_cmd
      tampered["test_tree_hash"] = "0" * 64
      with open(rpath, "w", encoding="utf-8") as f:
        json.dump(tampered, f)

      err_seal_hash = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_seal_hash,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] CORRUPT_RED_RECEIPT", err_seal_hash.getvalue()
      )

  def test_json_mode_and_subprocess_error_resilience(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)

      # --json mode on missing receipt
      err_json = io.StringIO()
      code_json = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "--json", "verify-green"],
          stdout=io.StringIO(),
          stderr=err_json,
      )
      self.assertEqual(code_json, 2)
      payload = json.loads(err_json.getvalue())
      self.assertEqual(payload["scar_id"], "SCAR-TEST-12")
      self.assertEqual(payload["violation_code"], "MISSING_RED_RECEIPT")
      self.assertEqual(payload["exit_code"], 2)

      # Shell Popen OSError resilience
      real_popen = subprocess.Popen

      def _fail_sh_popen(cmd_args, *args, **kwargs):
        if isinstance(cmd_args, list) and cmd_args and cmd_args[0] == "/bin/sh":
          raise OSError("/bin/sh launch failed")
        return real_popen(cmd_args, *args, **kwargs)

      with mock.patch("tdd_guard.subprocess.Popen", side_effect=_fail_sh_popen):
        err_sh_os = io.StringIO()
        code_sh_os = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "witness-red",
                "--test-dir",
                "tests",
                "--cmd",
                _unit_test_cmd(),
            ],
            stdout=io.StringIO(),
            stderr=err_sh_os,
        )
        self.assertEqual(code_sh_os, 2)
        self.assertIn(
            "[SCAR-ID: TEST-12] TEST_COMMAND_EXECUTION_ERROR",
            err_sh_os.getvalue(),
        )

      # Git OSError resilience
      with mock.patch(
          "tdd_guard.subprocess.run", side_effect=OSError("git binary missing")
      ):
        err_os = io.StringIO()
        code_os = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "witness-red",
                "--test-dir",
                "tests",
                "--cmd",
                _unit_test_cmd(),
            ],
            stdout=io.StringIO(),
            stderr=err_os,
        )
        self.assertEqual(code_os, 2)
        self.assertIn("[SCAR-ID: TEST-12]", err_os.getvalue())

  def test_synthesized_state_makefile_end_to_end_integration(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)

      init_code = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(init_code, 0)

      cmd = _unit_test_cmd()
      p_red = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "witness-red",
              "TEST_DIR=tests",
              f"TEST_CMD={cmd}",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_red.returncode, 0, p_red.stderr)
      self.assertTrue(os.path.isfile(_receipt_path(tmpdir)))

      # Attempt TEST_CMD="true" swap via Make
      p_swap = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "verify-green",
              "TEST_CMD=true",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_swap.returncode, 2)
      self.assertIn("[SCAR-ID: TEST-12] COMMAND_MISMATCH", p_swap.stderr)

      # Fix src/calc.py and run verify-green via Make
      _fix_calc_bug(tmpdir)
      p_green = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "verify-green",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_green.returncode, 0, p_green.stderr)

  def test_cli_wrapper_and_custom_receipt_path(self):
    tdd_guard_path = os.path.join(_SCRIPTS_DIR, "tdd_guard.py")
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      custom_rel = os.path.join(".workflow", "state", "custom_receipt.json")
      proc_missing = subprocess.run(
          [
              sys.executable,
              tdd_guard_path,
              "--repo-root",
              tmpdir,
              "--receipt",
              custom_rel,
              "verify-green",
          ],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc_missing.returncode, 2)
      self.assertEqual(proc_missing.stdout, "")
      self.assertIn(
          "[SCAR-ID: TEST-12] MISSING_RED_RECEIPT", proc_missing.stderr
      )

  def _assert_pid_terminated(self, pid: int) -> None:
    """Verifies that pid is terminated and reaped (os.kill(pid, 0) raises ProcessLookupError)."""
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
      try:
        os.kill(pid, 0)
      except ProcessLookupError:
        return
      time.sleep(0.01)
    with self.assertRaises(ProcessLookupError):
      os.kill(pid, 0)

  def test_real_subprocess_process_group_timeout_containment(self):
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as pid_dir:
      _init_fixture_repo(tmpdir)

      # 1. Real compound command timeout in witness-red
      red_pid_file = os.path.join(pid_dir, "red_child.pid")
      red_py_snippet = (
          "import os, time; "
          f"open({red_pid_file!r}, 'w').write(str(os.getpid())); "
          "time.sleep(30)"
      )
      red_compound_cmd = (
          f"{shlex.quote(sys.executable)} -c {shlex.quote(red_py_snippet)} ; echo after"
      )
      red_child_pid = None
      try:
        t0 = time.monotonic()
        err_red = io.StringIO()
        code_red = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "witness-red",
                "--test-dir",
                "tests",
                "--cmd",
                red_compound_cmd,
                "--timeout",
                "0.3",
            ],
            stdout=io.StringIO(),
            stderr=err_red,
        )
        elapsed_red = time.monotonic() - t0
        self.assertEqual(code_red, 2)
        self.assertLess(elapsed_red, 3.0)
        self.assertIn(
            "[SCAR-ID: TEST-12] TEST_COMMAND_EXECUTION_ERROR",
            err_red.getvalue(),
        )
        self.assertTrue(os.path.isfile(red_pid_file))
        with open(red_pid_file, "r", encoding="utf-8") as pf:
          red_child_pid = int(pf.read().strip())
        self._assert_pid_terminated(red_child_pid)
      finally:
        if red_child_pid is not None:
          try:
            os.kill(red_child_pid, signal.SIGKILL)
          except OSError:
            pass

      # 2. Real compound command timeout in verify-green
      green_pid_file = os.path.join(pid_dir, "green_child.pid")
      # Seal a failing Red command that delegates behavior to src/calc.py
      with open(
          os.path.join(tmpdir, "tests", "test_calc.py"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "import unittest\n"
            "from src.calc import add\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "  def test_add(self):\n"
            "    self.assertEqual(add(2, 3), 5)\n"
        )
      subprocess.run(
          ["git", "add", "-A"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: baseline test"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      compound_unit_cmd = f"{_unit_test_cmd()} ; exit $?"
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  compound_unit_cmd,
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Make src/calc.py spawn a child Python sleeper that records its PID and hangs 30s
      with open(
          os.path.join(tmpdir, "src", "calc.py"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "import subprocess, sys\n"
            "def add(a: int, b: int) -> int:\n"
            "  subprocess.run([\n"
            "      sys.executable,\n"
            "      '-c',\n"
            f"      \"import os, time; open({green_pid_file!r}, 'w').write(str(os.getpid())); time.sleep(30)\",\n"
            "  ], check=False)\n"
            "  return a + b\n"
        )

      green_child_pid = None
      try:
        t1 = time.monotonic()
        err_green = io.StringIO()
        code_green = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "verify-green",
                "--timeout",
                "0.6",
            ],
            stdout=io.StringIO(),
            stderr=err_green,
        )
        elapsed_green = time.monotonic() - t1
        self.assertEqual(code_green, 2)
        self.assertLess(elapsed_green, 3.0)
        self.assertIn(
            "[SCAR-ID: TEST-12] TEST_COMMAND_EXECUTION_ERROR",
            err_green.getvalue(),
        )
        self.assertTrue(os.path.isfile(green_pid_file))
        with open(green_pid_file, "r", encoding="utf-8") as pf:
          green_child_pid = int(pf.read().strip())
        self._assert_pid_terminated(green_child_pid)
      finally:
        if green_child_pid is not None:
          try:
            os.kill(green_child_pid, signal.SIGKILL)
          except OSError:
            pass

  def test_verified_green_rollback_bijection_across_failures(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      initial_head = _init_fixture_repo(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      def _read_receipt():
        with open(_receipt_path(tmpdir), "r", encoding="utf-8") as rf:
          return json.load(rf)

      def _pass_green(extra_args=None):
        _fix_calc_bug(tmpdir)
        cmd_args = ["--repo-root", tmpdir, "verify-green"] + (extra_args or [])
        err_buf = io.StringIO()
        rc = TddGuardScript().execute(
            args=cmd_args, stdout=io.StringIO(), stderr=err_buf
        )
        self.assertEqual(rc, 0, err_buf.getvalue())
        rec = _read_receipt()
        self.assertTrue(rec["verified_green"])
        self.assertEqual(rec["green_exit_code"], 0)
        self.assertIsNotNone(rec["green_verified_at"])
        self.assertIsNotNone(rec["verified_head_sha"])
        return rec

      # Baseline passing verify-green
      rec0 = _pass_green()
      self.assertEqual(rec0["verified_head_sha"], initial_head)
      self.assertFalse(rec0["head_move_allowed"])

      # (a) Break src/ -> GREEN_VERIFICATION_FAILED rolls back verified_green
      with open(
          os.path.join(tmpdir, "src", "calc.py"), "w", encoding="utf-8"
      ) as f:
        f.write("def add(a: int, b: int) -> int:\n  return a * b - 99\n")
      err_reg = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_reg,
          ),
          2,
      )
      self.assertIn("GREEN_VERIFICATION_FAILED", err_reg.getvalue())
      rec_a = _read_receipt()
      self.assertFalse(rec_a["verified_green"])
      self.assertIsNotNone(rec_a["green_exit_code"])
      self.assertNotEqual(rec_a["green_exit_code"], 0)
      self.assertIsNone(rec_a["green_verified_at"])
      self.assertIsNone(rec_a["verified_head_sha"])
      self.assertFalse(rec_a["head_move_allowed"])

      # (b1) Re-pass green, then trigger COMMAND_MISMATCH -> rolls back verified_green
      _pass_green()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green", "--cmd", "true"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          2,
      )
      rec_b1 = _read_receipt()
      self.assertFalse(rec_b1["verified_green"])
      self.assertIsNone(rec_b1["green_exit_code"])
      self.assertIsNone(rec_b1["green_verified_at"])
      self.assertIsNone(rec_b1["verified_head_sha"])
      self.assertFalse(rec_b1["head_move_allowed"])
      self.assertEqual(len(rec_b1["forensic_log"]), 1)

      # (b2) Re-pass green, then trigger EACCES_TEST_TAMPERING -> rolls back verified_green
      _pass_green()
      conftest_path = os.path.join(tmpdir, "tests", "conftest.py")
      with open(conftest_path, "w", encoding="utf-8") as f:
        f.write("# tamper\n")
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          2,
      )
      os.remove(conftest_path)
      rec_b2 = _read_receipt()
      self.assertFalse(rec_b2["verified_green"])
      self.assertIsNone(rec_b2["green_exit_code"])
      self.assertIsNone(rec_b2["green_verified_at"])
      self.assertIsNone(rec_b2["verified_head_sha"])
      self.assertFalse(rec_b2["head_move_allowed"])
      self.assertEqual(len(rec_b2["forensic_log"]), 2)

      # (b3) Re-pass green, then commit mid-phase -> HEAD_MOVED_DURING_RED_PHASE rolls back verified_green
      _pass_green()
      with open(
          os.path.join(tmpdir, "README_NOTE.txt"), "w", encoding="utf-8"
      ) as f:
        f.write("note\n")
      subprocess.run(
          ["git", "add", "README_NOTE.txt"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: extra commit"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          2,
      )
      rec_b3 = _read_receipt()
      self.assertFalse(rec_b3["verified_green"])
      self.assertIsNone(rec_b3["green_exit_code"])
      self.assertIsNone(rec_b3["green_verified_at"])
      self.assertIsNone(rec_b3["verified_head_sha"])
      self.assertFalse(rec_b3["head_move_allowed"])
      self.assertEqual(len(rec_b3["forensic_log"]), 3)

      # (c) Re-pass green (with --allow-head-move), then trigger verify-green timeout -> rolls back verified_green
      rec_moved = _pass_green(["--allow-head-move"])
      self.assertTrue(rec_moved["head_move_allowed"])
      with open(
          os.path.join(tmpdir, "src", "calc.py"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "import time\n"
            "def add(a: int, b: int) -> int:\n"
            "  time.sleep(5)\n"
            "  return a + b\n"
        )
      err_to = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "verify-green",
                  "--allow-head-move",
                  "--timeout",
                  "0.25",
              ],
              stdout=io.StringIO(),
              stderr=err_to,
          ),
          2,
      )
      self.assertIn("TEST_COMMAND_EXECUTION_ERROR", err_to.getvalue())
      rec_c = _read_receipt()
      self.assertFalse(rec_c["verified_green"])
      self.assertIsNone(rec_c["green_exit_code"])
      self.assertIsNone(rec_c["green_verified_at"])
      self.assertIsNone(rec_c["verified_head_sha"])
      self.assertFalse(rec_c["head_move_allowed"])

      # (d) Restore normal execution and pass verify-green -> preserves forensic_log
      rec_d = _pass_green(["--allow-head-move"])
      self.assertTrue(rec_d["verified_green"])
      self.assertEqual(rec_d["green_exit_code"], 0)
      self.assertTrue(rec_d["head_move_allowed"])
      self.assertEqual(len(rec_d["forensic_log"]), 3)

  def test_surrogateescape_filenames_and_atomic_receipt_write(self):
    # NBO 1: Non-UTF-8 surrogateescaped filename in compute_test_tree_hash
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      raw_bad_path = os.path.join(
          os.fsencode(os.path.join(tmpdir, "tests")), b"bad_\xff\xfe_test.py"
      )
      created_non_utf8 = False
      try:
        with open(raw_bad_path, "wb") as bf:
          bf.write(b"X = 1\n")
        created_non_utf8 = True
      except OSError:
        created_non_utf8 = False

      if created_non_utf8:
        digest1, manifest1 = compute_test_tree_hash(tmpdir, "tests")
        digest2, manifest2 = compute_test_tree_hash(tmpdir, "tests")
        self.assertEqual(digest1, digest2)
        self.assertEqual(manifest1, manifest2)
        self.assertEqual(len(manifest1), 3)

    # NBO 2: Atomic _write_receipt via sibling temp file + os.replace
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      state_dir = os.path.dirname(_receipt_path(tmpdir))
      self.assertEqual(
          [f for f in os.listdir(state_dir) if f.startswith(".tmp_")], []
      )

      with open(_receipt_path(tmpdir), "rb") as f:
        original_bytes = f.read()

      _fix_calc_bug(tmpdir)
      with mock.patch(
          "tdd_guard.os.replace",
          side_effect=OSError("simulated atomic replace failure"),
      ):
        err_rep = io.StringIO()
        code_rep = TddGuardScript().execute(
            args=["--repo-root", tmpdir, "verify-green"],
            stdout=io.StringIO(),
            stderr=err_rep,
        )
        self.assertEqual(code_rep, 2)
        self.assertIn("RECEIPT_WRITE_FAILED", err_rep.getvalue())

      # Existing receipt must remain byte-for-byte intact and zero .tmp_ files may leak
      with open(_receipt_path(tmpdir), "rb") as f:
        self.assertEqual(f.read(), original_bytes)
      self.assertEqual(
          [f for f in os.listdir(state_dir) if f.startswith(".tmp_")], []
      )

  def test_rollback_secondary_write_failure_surfaced_in_trigger(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      _fix_calc_bug(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # 1. Collision rollback path (_record_collision) with secondary I/O write error
      with mock.patch(
          "tdd_guard.os.replace", side_effect=OSError("read-only disk")
      ):
        err_col = io.StringIO()
        code_col = TddGuardScript().execute(
            args=["--repo-root", tmpdir, "verify-green", "--cmd", "true"],
            stdout=io.StringIO(),
            stderr=err_col,
        )
        self.assertEqual(code_col, 2)
        self.assertIn(
            "[SCAR-ID: TEST-12] COMMAND_MISMATCH", err_col.getvalue()
        )
        self.assertIn(
            "(receipt rollback write failed: read-only disk)",
            err_col.getvalue(),
        )

      # 2. Non-collision rollback path (GREEN_VERIFICATION_FAILED) with secondary I/O write error
      with open(
          os.path.join(tmpdir, "src", "calc.py"), "w", encoding="utf-8"
      ) as f:
        f.write("def add(a: int, b: int) -> int:\n  return a - b - 10\n")
      with mock.patch(
          "tdd_guard.os.replace", side_effect=OSError("read-only disk")
      ):
        err_reg = io.StringIO()
        code_reg = TddGuardScript().execute(
            args=["--repo-root", tmpdir, "verify-green"],
            stdout=io.StringIO(),
            stderr=err_reg,
        )
        self.assertEqual(code_reg, 2)
        self.assertIn(
            "[SCAR-ID: TEST-12] GREEN_VERIFICATION_FAILED", err_reg.getvalue()
        )
        self.assertIn(
            "(receipt rollback write failed: read-only disk)",
            err_reg.getvalue(),
        )

  def test_ancestor_autoload_hook_sealing_outside_test_dir(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      sub_src = os.path.join(tmpdir, "packages", "sub", "src")
      sub_tests = os.path.join(tmpdir, "packages", "sub", "tests")
      os.makedirs(sub_src, exist_ok=True)
      os.makedirs(sub_tests, exist_ok=True)

      with open(
          os.path.join(sub_src, "__init__.py"), "w", encoding="utf-8"
      ) as f:
        f.write("")
      with open(
          os.path.join(sub_src, "subcalc.py"), "w", encoding="utf-8"
      ) as f:
        f.write("def mul(a: int, b: int) -> int:\n  return a + b\n")
      with open(
          os.path.join(sub_tests, "__init__.py"), "w", encoding="utf-8"
      ) as f:
        f.write("")
      with open(
          os.path.join(sub_tests, "test_subcalc.py"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "import unittest\n"
            "from packages.sub.src.subcalc import mul\n\n"
            "class TestSubCalc(unittest.TestCase):\n"
            "  def test_mul(self):\n"
            "    self.assertEqual(mul(3, 4), 12)\n"
        )
      for d in (
          os.path.join(tmpdir, "packages"),
          os.path.join(tmpdir, "packages", "sub"),
      ):
        with open(os.path.join(d, "__init__.py"), "w", encoding="utf-8") as f:
          f.write("")

      subprocess.run(
          ["git", "add", "-A"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: seed nested subproject"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      sub_cmd = (
          f"{shlex.quote(sys.executable)} -m unittest discover "
          "-s packages/sub/tests -t . -p 'test_*.py'"
      )
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "packages/sub/tests",
                  "--cmd",
                  sub_cmd,
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # (a) Dropping untracked autoload hooks in any ancestor dir fails verify-green
      injected_rel_paths = [
          "conftest.py",
          "sitecustomize.py",
          "usercustomize.py",
          "inject.pth",
          "packages/conftest.py",
      ]
      expected_log_len = 0
      for rel_hook in injected_rel_paths:
        abs_hook = os.path.join(tmpdir, *rel_hook.split("/"))
        with open(abs_hook, "w", encoding="utf-8") as f:
          f.write("# malicious autoload hook\n")
        err_hook = io.StringIO()
        rc_hook = TddGuardScript().execute(
            args=["--repo-root", tmpdir, "verify-green"],
            stdout=io.StringIO(),
            stderr=err_hook,
        )
        os.remove(abs_hook)
        expected_log_len += 1
        self.assertEqual(
            rc_hook, 2, f"Expected exit 2 when injecting {rel_hook}"
        )
        self.assertIn(
            "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_hook.getvalue()
        )
        self.assertIn(rel_hook, err_hook.getvalue())
        with open(_receipt_path(tmpdir), "r", encoding="utf-8") as rf:
          rec_hook = json.load(rf)
        self.assertEqual(len(rec_hook["forensic_log"]), expected_log_len)

      # Symlink autoload hook in ancestor directory also caught
      sym_rel = "packages/sub/evil_hook.pth"
      sym_abs = os.path.join(tmpdir, "packages", "sub", "evil_hook.pth")
      os.symlink(os.path.join(tmpdir, "src", "calc.py"), sym_abs)
      err_sym = io.StringIO()
      rc_sym = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_sym,
      )
      os.unlink(sym_abs)
      expected_log_len += 1
      self.assertEqual(rc_sym, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_sym.getvalue()
      )
      self.assertIn(sym_rel, err_sym.getvalue())

      # (b) Existing root conftest.py present at witness-red is sealed against edit/delete
      root_conftest = os.path.join(tmpdir, "conftest.py")
      orig_conftest = "# legitimate root conftest\n"
      with open(root_conftest, "w", encoding="utf-8") as f:
        f.write(orig_conftest)
      subprocess.run(
          ["git", "add", "conftest.py"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add root conftest"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "packages/sub/tests",
                  "--cmd",
                  sub_cmd,
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Modify existing root conftest.py -> EACCES_TEST_TAMPERING
      with open(root_conftest, "w", encoding="utf-8") as f:
        f.write("# tampered root conftest\n")
      err_mod_cf = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_mod_cf,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_mod_cf.getvalue()
      )
      self.assertIn("conftest.py", err_mod_cf.getvalue())

      # Delete existing root conftest.py -> EACCES_TEST_TAMPERING
      os.remove(root_conftest)
      err_del_cf = io.StringIO()
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=err_del_cf,
          ),
          2,
      )
      self.assertIn(
          "[SCAR-ID: TEST-12] EACCES_TEST_TAMPERING", err_del_cf.getvalue()
      )
      self.assertIn("conftest.py", err_del_cf.getvalue())

      # Restore root conftest.py
      with open(root_conftest, "w", encoding="utf-8") as f:
        f.write(orig_conftest)

      # (c) Fixing production files and editing normal root/ancestor files passes verify-green
      with open(
          os.path.join(sub_src, "subcalc.py"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "def mul(a: int, b: int) -> int:\n"
            "  product = a * b\n"
            "  return product\n"
        )
      with open(
          os.path.join(tmpdir, "helper.py"), "w", encoding="utf-8"
      ) as f:
        f.write("HELPER = True\n")
      with open(
          os.path.join(tmpdir, "packages", "README.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Subproject\n")

      err_pass = io.StringIO()
      rc_pass = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_pass,
      )
      self.assertEqual(rc_pass, 0, err_pass.getvalue())

  def test_git_head_uses_verify_flag(self):
    with mock.patch("subprocess.run", wraps=subprocess.run) as mock_run:
      guard = TddGuardScript()
      guard.repo_root = _REPO_ROOT
      sha, err = guard._git_head()
      self.assertIsNotNone(sha)
      self.assertEqual(err, "")
      mock_run.assert_called_with(
          ["git", "rev-parse", "--verify", "HEAD"],
          cwd=_REPO_ROOT,
          capture_output=True,
          text=True,
          errors="replace",
          check=False,
          timeout=15,
      )

  def test_citation_verification_blob_streaming_and_working_tree_immunity(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      specs_dir = os.path.join(tmpdir, "specs")
      os.makedirs(specs_dir, exist_ok=True)
      spec1 = os.path.join(specs_dir, "requirements.txt")
      with open(spec1, "w", encoding="utf-8") as f:
        f.write("# Requirements\nFEATURE_ENABLED = true\n# More comments\n")
      spec2 = os.path.join(specs_dir, "special 🚀 spec.txt")
      with open(spec2, "w", encoding="utf-8") as f:
        f.write("# Unicode and spaces\nSETTING = 42\n")

      subprocess.run(
          ["git", "add", "specs"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add specification witnesses"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      # witness-red
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Working-tree tampering: modify spec1 to be comments, and delete spec2 from disk!
      with open(spec1, "w", encoding="utf-8") as f:
        f.write("# Tampered working-tree file\n# All comments\n")
      os.remove(spec2)

      # verify-citation must stream from Git blob at witnessed_head_sha and succeed (exit 0)
      out = io.StringIO()
      err = io.StringIO()
      rc1 = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--cite",
              "specs/requirements.txt:2",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc1, 0, err.getvalue())

      # Repeatable --cite with spaces and unicode path streaming
      err_rep = io.StringIO()
      rc_rep = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--cite",
              "specs/requirements.txt:2",
              "--cite",
              "specs/special 🚀 spec.txt:2",
          ],
          stdout=io.StringIO(),
          stderr=err_rep,
      )
      self.assertEqual(rc_rep, 0, err_rep.getvalue())

      # CITE environment variable via --make-env (newline, comma, semicolon delimited)
      for cite_env in [
          "specs/requirements.txt:2\nspecs/special 🚀 spec.txt:2",
          "specs/requirements.txt:2,specs/special 🚀 spec.txt:2",
          "specs/requirements.txt:2; specs/special 🚀 spec.txt:2",
      ]:
        with mock.patch.dict(os.environ, {"CITE": cite_env}):
          err_env = io.StringIO()
          rc_env = TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "verify-citation",
                  "--make-env",
              ],
              stdout=io.StringIO(),
              stderr=err_env,
          )
          self.assertEqual(rc_env, 0, err_env.getvalue())

      # verify-green with --cite
      _fix_calc_bug(tmpdir)
      err_green = io.StringIO()
      rc_green = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-green",
              "--cite",
              "specs/requirements.txt:2",
              "--cite",
              "specs/special 🚀 spec.txt:2",
          ],
          stdout=io.StringIO(),
          stderr=err_green,
      )
      self.assertEqual(rc_green, 0, err_green.getvalue())
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        rec = json.load(f)
      self.assertTrue(rec["verified_green"])

  def test_uncommitted_witness_file_rejection_and_rollback(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      # witness-red
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      # Fix calc bug and pass verify-green so verified_green is True
      _fix_calc_bug(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        self.assertTrue(json.load(f)["verified_green"])

      # (a) Untracked scratch file on disk
      scratch = os.path.join(tmpdir, "scratch.txt")
      with open(scratch, "w", encoding="utf-8") as f:
        f.write("ACTIVE = 1\n")
      err_scratch = io.StringIO()
      rc_scratch = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--cite",
              "scratch.txt:1",
          ],
          stdout=io.StringIO(),
          stderr=err_scratch,
      )
      self.assertEqual(rc_scratch, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE",
          err_scratch.getvalue(),
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        rec1 = json.load(f)
      self.assertFalse(rec1["verified_green"])
      self.assertTrue(
          any(
              e.get("violation_code") == "UNCOMMITTED_WITNESS_FILE"
              for e in rec1.get("forensic_log", [])
          )
      )

      # (b) Non-existent file
      err_missing = io.StringIO()
      rc_missing = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--cite",
              "specs/missing.txt:1",
          ],
          stdout=io.StringIO(),
          stderr=err_missing,
      )
      self.assertEqual(rc_missing, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE",
          err_missing.getvalue(),
      )

      # (c) File committed in a later mid-phase commit when --allow-head-move is passed
      late_file = os.path.join(tmpdir, "late.txt")
      with open(late_file, "w", encoding="utf-8") as f:
        f.write("LATE_STMT = True\n")
      subprocess.run(
          ["git", "add", "late.txt"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: late commit"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      err_late = io.StringIO()
      rc_late = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--allow-head-move",
              "--cite",
              "late.txt:1",
          ],
          stdout=io.StringIO(),
          stderr=err_late,
      )
      self.assertEqual(rc_late, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE",
          err_late.getvalue(),
      )

      # (d) Directory / tree path (e.g. src:1)
      err_tree = io.StringIO()
      rc_tree = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--allow-head-move",
              "--cite",
              "src:1",
          ],
          stdout=io.StringIO(),
          stderr=err_tree,
      )
      self.assertEqual(rc_tree, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE",
          err_tree.getvalue(),
      )

      # (e) Traversal path or absolute path
      for bad_path in ("../outside.py:1", "/etc/passwd:1"):
        err_trav = io.StringIO()
        rc_trav = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "verify-citation",
                "--allow-head-move",
                "--cite",
                bad_path,
            ],
            stdout=io.StringIO(),
            stderr=err_trav,
        )
        self.assertEqual(rc_trav, 2)
        self.assertIn(
            "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE",
            err_trav.getvalue(),
        )

      # (f) verify-green with uncommitted citation also fails with UNCOMMITTED_WITNESS_FILE
      err_vg = io.StringIO()
      rc_vg = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-green",
              "--allow-head-move",
              "--cite",
              "scratch.txt:1",
          ],
          stdout=io.StringIO(),
          stderr=err_vg,
      )
      self.assertEqual(rc_vg, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE",
          err_vg.getvalue(),
      )

  def test_trivial_witness_line_rejection_and_rollback(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      spec_lines = [
          "# Python comment",          # line 1
          "// C++ style comment",       # line 2
          "/* Block comment start",     # line 3
          " * Block comment middle",    # line 4
          " */",                        # line 5
          "-- SQL comment",             # line 6
          "<!-- HTML comment -->",      # line 7
          "    ",                       # line 8 (whitespace)
          "",                           # line 9 (blank)
          "ACTIVE_STATEMENT = True",    # line 10
      ]
      spec_file = os.path.join(tmpdir, "spec.txt")
      with open(spec_file, "w", encoding="utf-8") as f:
        f.write("\n".join(spec_lines) + "\n")
      subprocess.run(
          ["git", "add", "spec.txt"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add spec with comments"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      # witness-red
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Fix calc bug and pass verify-green
      _fix_calc_bug(tmpdir)
      self.assertEqual(
          TddGuardScript().execute(
              args=["--repo-root", tmpdir, "verify-green"],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        self.assertTrue(json.load(f)["verified_green"])

      # (a) Missing/empty citation in verify-citation
      err_missing = io.StringIO()
      rc_missing = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-citation"],
          stdout=io.StringIO(),
          stderr=err_missing,
      )
      self.assertEqual(rc_missing, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] TRIVIAL_WITNESS_LINE", err_missing.getvalue()
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        self.assertFalse(json.load(f)["verified_green"])

      # (b) Malformed citation strings
      malformed_citations = [
          "spec.txt",         # missing colon
          "spec.txt:",        # empty line part
          "spec.txt:abc",     # non-integer line
          "spec.txt:0",       # line <= 0
          "spec.txt:-1",      # negative line
          "spec.txt:999",     # line > total_lines
      ]
      for mal_cite in malformed_citations:
        err_mal = io.StringIO()
        rc_mal = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "verify-citation",
                "--cite",
                mal_cite,
            ],
            stdout=io.StringIO(),
            stderr=err_mal,
        )
        self.assertEqual(rc_mal, 2, f"Expected 2 for cite={mal_cite}")
        self.assertIn(
            "[SCAR-ID: TEST-12] TRIVIAL_WITNESS_LINE",
            err_mal.getvalue(),
        )

      # (c) Lines 1 through 9 are blank or comment lines
      for line_no in range(1, 10):
        err_triv = io.StringIO()
        rc_triv = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "verify-citation",
                "--cite",
                f"spec.txt:{line_no}",
            ],
            stdout=io.StringIO(),
            stderr=err_triv,
        )
        self.assertEqual(
            rc_triv, 2, f"Expected line {line_no} to be rejected as trivial"
        )
        self.assertIn(
            "[SCAR-ID: TEST-12] TRIVIAL_WITNESS_LINE",
            err_triv.getvalue(),
        )

      # (d) Active code statement at line 10 succeeds
      err_ok = io.StringIO()
      rc_ok = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--cite",
              "spec.txt:10",
          ],
          stdout=io.StringIO(),
          stderr=err_ok,
      )
      self.assertEqual(rc_ok, 0, err_ok.getvalue())

  def test_head_moved_during_red_phase_in_verify_citation(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      spec_file = os.path.join(tmpdir, "spec.txt")
      with open(spec_file, "w", encoding="utf-8") as f:
        f.write("ACTIVE = 1\n")
      subprocess.run(
          ["git", "add", "spec.txt"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add spec"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      # witness-red
      self.assertEqual(
          TddGuardScript().execute(
              args=[
                  "--repo-root",
                  tmpdir,
                  "witness-red",
                  "--test-dir",
                  "tests",
                  "--cmd",
                  _unit_test_cmd(),
              ],
              stdout=io.StringIO(),
              stderr=io.StringIO(),
          ),
          0,
      )

      # Move HEAD by committing a new file
      with open(os.path.join(tmpdir, "other.txt"), "w", encoding="utf-8") as f:
        f.write("other\n")
      subprocess.run(
          ["git", "add", "other.txt"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: move head"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      # verify-citation without --allow-head-move fails with HEAD_MOVED_DURING_RED_PHASE
      err_moved = io.StringIO()
      rc_moved = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--cite",
              "spec.txt:1",
          ],
          stdout=io.StringIO(),
          stderr=err_moved,
      )
      self.assertEqual(rc_moved, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] HEAD_MOVED_DURING_RED_PHASE",
          err_moved.getvalue(),
      )
      with open(_receipt_path(tmpdir), "r", encoding="utf-8") as f:
        rec = json.load(f)
      self.assertFalse(rec["verified_green"])
      self.assertTrue(
          any(
              e.get("violation_code") == "HEAD_MOVED_DURING_RED_PHASE"
              for e in rec.get("forensic_log", [])
          )
      )

      # verify-citation with --allow-head-move passes
      err_allowed = io.StringIO()
      rc_allowed = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "verify-citation",
              "--allow-head-move",
              "--cite",
              "spec.txt:1",
          ],
          stdout=io.StringIO(),
          stderr=err_allowed,
      )
      self.assertEqual(rc_allowed, 0, err_allowed.getvalue())

      # verify-citation with ALLOW_HEAD_MOVE=1 and --make-env passes
      with mock.patch.dict(
          os.environ, {"ALLOW_HEAD_MOVE": "1", "CITE": "spec.txt:1"}
      ):
        err_env = io.StringIO()
        rc_env = TddGuardScript().execute(
            args=[
                "--repo-root",
                tmpdir,
                "verify-citation",
                "--make-env",
            ],
            stdout=io.StringIO(),
            stderr=err_env,
        )
        self.assertEqual(rc_env, 0, err_env.getvalue())

  def test_synthesized_state_makefile_citation_targets(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      spec_file = os.path.join(tmpdir, "spec.txt")
      with open(spec_file, "w", encoding="utf-8") as f:
        f.write("ACTIVE = 1\n")
      subprocess.run(
          ["git", "add", "spec.txt"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )
      subprocess.run(
          ["git", "commit", "-q", "-m", "chore: add spec"],
          cwd=tmpdir,
          check=True,
          capture_output=True,
          text=True,
      )

      init_code = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "init-makefile"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(init_code, 0)

      # Verify Makefile content contains CITE export and verify-citation target
      mk_path = os.path.join(tmpdir, ".workflow", "state", "Makefile")
      with open(mk_path, "r", encoding="utf-8") as f:
        mk_content = f.read()
      self.assertIn("CITE =", mk_content)
      self.assertIn("export TEST_DIR TEST_CMD ALLOW_HEAD_MOVE CITE", mk_content)
      self.assertIn("verify-citation:", mk_content)

      # witness-red via Make
      cmd = _unit_test_cmd()
      p_red = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "witness-red",
              "TEST_DIR=tests",
              f"TEST_CMD={cmd}",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_red.returncode, 0, p_red.stderr)

      # verify-citation via Make with valid citation
      p_cite = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "verify-citation",
              "CITE=spec.txt:1",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_cite.returncode, 0, p_cite.stderr)

      # verify-citation via Make with invalid citation (uncommitted scratch)
      with open(os.path.join(tmpdir, "scratch.txt"), "w", encoding="utf-8") as f:
        f.write("KEY = 1\n")
      p_cite_bad = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "verify-citation",
              "CITE=scratch.txt:1",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_cite_bad.returncode, 2)
      self.assertIn(
          "[SCAR-ID: TEST-12] UNCOMMITTED_WITNESS_FILE", p_cite_bad.stderr
      )

      # Fix calc bug and run verify-green via Make with CITE
      _fix_calc_bug(tmpdir)
      p_green = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              ".workflow/state/Makefile",
              "verify-green",
              "CITE=spec.txt:1",
              f"PYTHON={sys.executable}",
          ],
          cwd=tmpdir,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(p_green.returncode, 0, p_green.stderr)

  def test_witness_red_records_and_seals_toolchain_hash(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      expected_head = _init_fixture_repo(tmpdir)
      cmd = _unit_test_cmd()
      out = io.StringIO()
      err = io.StringIO()
      code = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "witness-red",
              "--test-dir",
              "tests",
              "--cmd",
              cmd,
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, err.getvalue())

      rpath = _receipt_path(tmpdir)
      with open(rpath, "r", encoding="utf-8") as f:
        receipt = json.load(f)

      self.assertIn("toolchain_hash", receipt)
      self.assertIsInstance(receipt["toolchain_hash"], str)
      self.assertEqual(len(receipt["toolchain_hash"]), 64)

      # Tampering toolchain_hash in receipt invalidates receipt_seal
      receipt["toolchain_hash"] = "0" * 64
      with open(rpath, "w", encoding="utf-8") as f:
        json.dump(receipt, f)

      err_seal = io.StringIO()
      code_tampered = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_seal,
      )
      self.assertEqual(code_tampered, 2)
      self.assertIn("[SCAR-ID: TEST-12] CORRUPT_RED_RECEIPT", err_seal.getvalue())

  def test_verify_green_and_citation_detect_governance_toolchain_tampering(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      expected_head = _init_fixture_repo(tmpdir)
      scripts_dir = os.path.join(tmpdir, "scripts")
      os.makedirs(scripts_dir, exist_ok=True)
      for fname in ("tdd_guard.py", "workflow_gates.py", "base_script.py", "emit_reflex.py"):
        src_path = os.path.join(_SCRIPTS_DIR, fname)
        dst_path = os.path.join(scripts_dir, fname)
        shutil.copyfile(src_path, dst_path)

      with open(os.path.join(tmpdir, "spec.txt"), "w", encoding="utf-8") as f:
        f.write("def add(a: int, b: int) -> int:\n  return a + b\n")

      subprocess.run(["git", "add", "scripts", "spec.txt"], cwd=tmpdir, check=True, capture_output=True)
      subprocess.run(["git", "commit", "-q", "-m", "chore: add governance scripts and spec"], cwd=tmpdir, check=True, capture_output=True)

      cmd = _unit_test_cmd()
      code_red = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "witness-red",
              "--test-dir",
              "tests",
              "--cmd",
              cmd,
          ],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(code_red, 0)

      # Tamper with scripts/tdd_guard.py in working tree
      with open(os.path.join(scripts_dir, "tdd_guard.py"), "a", encoding="utf-8") as f:
        f.write("\n# MALICIOUS_TAMPER = True\n")

      _fix_calc_bug(tmpdir)

      # verify-green detects tampering
      err_green = io.StringIO()
      code_green = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_green,
      )
      self.assertEqual(code_green, 2)
      self.assertIn("[SCAR-ID: TEST-12] GOVERNANCE_TOOLCHAIN_TAMPERED", err_green.getvalue())

      # Check collision logged in forensic_log and rollback verified_green
      rpath = _receipt_path(tmpdir)
      with open(rpath, "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertFalse(receipt["verified_green"])
      collisions = [c for c in receipt.get("forensic_log", []) if c.get("violation_code") == "GOVERNANCE_TOOLCHAIN_TAMPERED"]
      self.assertTrue(collisions, "Expected GOVERNANCE_TOOLCHAIN_TAMPERED collision in forensic_log")

      # verify-citation also detects tampering
      err_cite = io.StringIO()
      code_cite = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-citation", "--cite", "spec.txt:1"],
          stdout=io.StringIO(),
          stderr=err_cite,
      )
      self.assertEqual(code_cite, 2)
      self.assertIn("[SCAR-ID: TEST-12] GOVERNANCE_TOOLCHAIN_TAMPERED", err_cite.getvalue())

  def test_non_governance_scripts_modification_allowed_during_green(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      expected_head = _init_fixture_repo(tmpdir)
      scripts_dir = os.path.join(tmpdir, "scripts")
      os.makedirs(scripts_dir, exist_ok=True)
      for fname in ("tdd_guard.py", "workflow_gates.py", "base_script.py", "emit_reflex.py"):
        src_path = os.path.join(_SCRIPTS_DIR, fname)
        dst_path = os.path.join(scripts_dir, fname)
        shutil.copyfile(src_path, dst_path)

      # Add a non-governance script in scripts/ (e.g. scripts/safe_copy.py)
      safe_copy_src = os.path.join(_SCRIPTS_DIR, "safe_copy.py")
      safe_copy_dst = os.path.join(scripts_dir, "safe_copy.py")
      if os.path.isfile(safe_copy_src):
        shutil.copyfile(safe_copy_src, safe_copy_dst)
      else:
        with open(safe_copy_dst, "w", encoding="utf-8") as f:
          f.write("#!/usr/bin/env python3\ndef safe_copy(): pass\n")

      with open(os.path.join(tmpdir, "spec.txt"), "w", encoding="utf-8") as f:
        f.write("def add(a: int, b: int) -> int:\n  return a + b\n")

      subprocess.run(["git", "add", "scripts", "spec.txt"], cwd=tmpdir, check=True, capture_output=True)
      subprocess.run(["git", "commit", "-q", "-m", "chore: add governance scripts, non-governance script, and spec"], cwd=tmpdir, check=True, capture_output=True)

      cmd = _unit_test_cmd()
      code_red = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "witness-red",
              "--test-dir",
              "tests",
              "--cmd",
              cmd,
          ],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(code_red, 0)

      # Modify non-governance file in scripts/
      with open(safe_copy_dst, "a", encoding="utf-8") as f:
        f.write("\n# NON_GOVERNANCE_EDIT = True\n")

      _fix_calc_bug(tmpdir)

      # verify-green should succeed and NOT trigger GOVERNANCE_TOOLCHAIN_TAMPERED
      out_green = io.StringIO()
      err_green = io.StringIO()
      code_green = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=out_green,
          stderr=err_green,
      )
      self.assertNotIn("GOVERNANCE_TOOLCHAIN_TAMPERED", err_green.getvalue())
      self.assertEqual(code_green, 0, f"verify-green failed: {err_green.getvalue()}")

      rpath = _receipt_path(tmpdir)
      with open(rpath, "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertTrue(receipt["verified_green"])

      # verify-citation should also succeed and NOT trigger GOVERNANCE_TOOLCHAIN_TAMPERED
      out_cite = io.StringIO()
      err_cite = io.StringIO()
      code_cite = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-citation", "--cite", "spec.txt:1"],
          stdout=out_cite,
          stderr=err_cite,
      )
      self.assertNotIn("GOVERNANCE_TOOLCHAIN_TAMPERED", err_cite.getvalue())
      self.assertEqual(code_cite, 0, f"verify-citation failed: {err_cite.getvalue()}")

  def test_verify_green_detects_state_makefile_tampering(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      _init_fixture_repo(tmpdir)
      out_init = io.StringIO()
      code_init = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "init-makefile"],
          stdout=out_init,
          stderr=io.StringIO(),
      )
      self.assertEqual(code_init, 0)
      mk_path = os.path.join(tmpdir, ".workflow", "state", "Makefile")
      self.assertTrue(os.path.isfile(mk_path))

      cmd = _unit_test_cmd()
      code_red = TddGuardScript().execute(
          args=[
              "--repo-root",
              tmpdir,
              "witness-red",
              "--test-dir",
              "tests",
              "--cmd",
              cmd,
          ],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
      self.assertEqual(code_red, 0)

      # Tamper with .workflow/state/Makefile
      with open(mk_path, "a", encoding="utf-8") as f:
        f.write("\n# BACKDOOR_TARGET: malicious\n")

      _fix_calc_bug(tmpdir)

      err_green = io.StringIO()
      code_green = TddGuardScript().execute(
          args=["--repo-root", tmpdir, "verify-green"],
          stdout=io.StringIO(),
          stderr=err_green,
      )
      self.assertEqual(code_green, 2)
      self.assertIn("[SCAR-ID: ARCH-06] STATE_MAKEFILE_TAMPERED", err_green.getvalue())

      rpath = _receipt_path(tmpdir)
      with open(rpath, "r", encoding="utf-8") as f:
        receipt = json.load(f)
      self.assertFalse(receipt["verified_green"])
      collisions = [c for c in receipt.get("forensic_log", []) if c.get("violation_code") == "STATE_MAKEFILE_TAMPERED"]
      self.assertTrue(collisions, "Expected STATE_MAKEFILE_TAMPERED collision in forensic_log")
      self.assertEqual(collisions[0].get("scar_id"), "SCAR-ARCH-06")


if __name__ == "__main__":
  unittest.main()
