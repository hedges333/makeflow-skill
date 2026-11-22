#!/usr/bin/env python3
"""Unit tests for hosting discovery and CI quiescence polling in lib/ci_polling.py (GH-213)."""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

import lib.ci_polling as ci_polling  # noqa: E402
from lib.ci_polling import detect_hosting_mode, poll_ci_status  # noqa: E402
import mcp.server as mcp_server  # noqa: E402


def _clean_env(fake_home, **extra):
  env = {
      k: v
      for k, v in os.environ.items()
      if not k.startswith("MAKEFILE_WORKFLOW_")
  }
  env["HOME"] = fake_home
  env.update(extra)
  return env


class TestDetectHostingMode(unittest.TestCase):
  """Hermetic unit tests for detect_hosting_mode in lib/ci_polling.py (GH-213)."""

  def test_no_origin_or_empty_remote_returns_pure_git(self):
    """Returns pure_git when origin remote is missing or empty."""
    with mock.patch("subprocess.run") as mock_run:
      mock_run.return_value = mock.MagicMock(
          returncode=128, stdout="", stderr="fatal: No such remote 'origin'"
      )
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    with mock.patch("subprocess.run") as mock_run:
      mock_run.return_value = mock.MagicMock(returncode=0, stdout="   \n", stderr="")
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

  def test_non_github_remote_returns_pure_git(self):
    """Returns pure_git for GitLab, Bitbucket, and local file remotes."""
    for url in (
        "git@gitlab.com:org/repo.git",
        "https://bitbucket.org/org/repo.git",
        "file:///local/repo.git",
    ):
      with self.subTest(url=url):
        with mock.patch("subprocess.run") as mock_run:
          mock_run.return_value = mock.MagicMock(
              returncode=0, stdout=url + "\n", stderr=""
          )
          self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

  def test_missing_gh_cli_or_failed_auth_or_repo_view_returns_pure_git(self):
    """Returns pure_git when gh is absent, unauthenticated, or repo view fails."""
    with mock.patch("subprocess.run") as mock_run, mock.patch(
        "shutil.which", return_value=None
    ):
      mock_run.return_value = mock.MagicMock(
          returncode=0, stdout="git@github.com:org/repo.git\n", stderr=""
      )
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    def auth_fail(cmd, **kwargs):
      if cmd[:3] == ["git", "remote", "get-url"]:
        return mock.MagicMock(
            returncode=0, stdout="https://github.com/org/repo.git\n", stderr=""
        )
      return mock.MagicMock(returncode=1, stdout="", stderr="not logged in")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=auth_fail
    ):
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

    def repo_fail(cmd, **kwargs):
      if cmd[:3] == ["git", "remote", "get-url"]:
        return mock.MagicMock(
            returncode=0, stdout="https://github.com/org/repo.git\n", stderr=""
        )
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="ok\n", stderr="")
      return mock.MagicMock(returncode=1, stdout="", stderr="no repo")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=repo_fail
    ):
      self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

  def test_subprocess_or_os_error_returns_pure_git(self):
    """Returns pure_git cleanly on SubprocessError or OSError."""
    for exc in (
        subprocess.TimeoutExpired(cmd=["git"], timeout=10),
        OSError("git not executable"),
    ):
      with self.subTest(exc=type(exc).__name__):
        with mock.patch("subprocess.run", side_effect=exc):
          self.assertEqual(detect_hosting_mode("/fake/repo"), "pure_git")

  def test_valid_github_remote_auth_and_repo_returns_github(self):
    """Returns github when origin is GitHub and gh auth/repo checks succeed."""
    def all_pass(cmd, **kwargs):
      if cmd[:3] == ["git", "remote", "get-url"]:
        return mock.MagicMock(
            returncode=0, stdout="git@github.com:org/repo.git\n", stderr=""
        )
      return mock.MagicMock(returncode=0, stdout="ok\n", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=all_pass
    ):
      self.assertEqual(detect_hosting_mode("/fake/repo"), "github")


class TestPollCiStatus(unittest.TestCase):
  """Hermetic unit tests for poll_ci_status in lib/ci_polling.py (GH-213)."""

  def test_pure_git_mode_and_non_positive_pr_number_skip_polling(self):
    """Returns SKIPPED and readyToLand=True for pure_git mode or pr_number <= 0."""
    with tempfile.TemporaryDirectory() as ws_dir:
      with mock.patch(
          "lib.ci_polling.detect_hosting_mode", return_value="pure_git"
      ):
        res = poll_ci_status(pr_number=42, ws=ws_dir)
      self.assertEqual(res["mode"], "pure_git")
      self.assertEqual(res["ciStatus"], "SKIPPED")
      self.assertIs(res["quiescenceSatisfied"], True)
      self.assertIs(res["readyToLand"], True)
      self.assertEqual(res["unresolvedBotCommentsCount"], 0)

      res_zero = poll_ci_status(
          pr_number=0, ws=ws_dir, hosting_mode="github"
      )
      self.assertEqual(res_zero["mode"], "pure_git")
      self.assertEqual(res_zero["ciStatus"], "SKIPPED")

  def test_timeout_called_process_error_and_json_parse_error(self):
    """Returns TIMEOUT on deadline breach and github_error on gh CLI or JSON failure."""
    with tempfile.TemporaryDirectory() as ws_dir:
      clock = [1000.0, 1700.0]
      with mock.patch("time.time", side_effect=clock):
        res_timeout = poll_ci_status(
            pr_number=10, ws=ws_dir, max_wait_sec=600, hosting_mode="github"
        )
      self.assertEqual(res_timeout["ciStatus"], "TIMEOUT")
      self.assertIs(res_timeout["readyToLand"], False)

      with mock.patch(
          "subprocess.run",
          side_effect=subprocess.CalledProcessError(
              returncode=1, cmd=["gh"], stderr="auth required"
          ),
      ):
        res_err = poll_ci_status(
            pr_number=10, ws=ws_dir, max_wait_sec=60, hosting_mode="github"
        )
      self.assertEqual(res_err["mode"], "github_error")
      self.assertEqual(res_err["ciStatus"], "ERROR")
      self.assertIn("auth required", res_err["message"])

      with mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(stdout="{bad json", stderr="", returncode=0),
      ):
        res_json = poll_ci_status(
            pr_number=10, ws=ws_dir, max_wait_sec=60, hosting_mode="github"
        )
      self.assertEqual(res_json["mode"], "github_error")
      self.assertEqual(res_json["ciStatus"], "ERROR")
      self.assertIn("Error parsing gh output", res_json["message"])

  def test_failed_checks_return_failure_with_check_names(self):
    """Returns FAILURE and lists failing check names across all failure conclusions."""
    payload = {
        "statusCheckRollup": [
            {"name": "unit", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"context": "lint", "status": "COMPLETED", "conclusion": "TIMED_OUT"},
        ],
        "comments": [],
        "reviews": [],
    }
    with tempfile.TemporaryDirectory() as ws_dir, mock.patch(
        "subprocess.run",
        return_value=mock.MagicMock(
            stdout=json.dumps(payload), stderr="", returncode=0
        ),
    ):
      res = poll_ci_status(
          pr_number=12, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
      )
    self.assertEqual(res["ciStatus"], "FAILURE")
    self.assertIs(res["readyToLand"], False)
    self.assertEqual(res["failedChecks"], ["unit", "lint"])

  def test_bot_comments_reviews_and_human_filtering(self):
    """Collects bot comments and actionable bot reviews while ignoring humans and approvals."""
    payload = {
        "statusCheckRollup": [{
            "name": "ci",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "completedAt": "not-a-timestamp",
        }],
        "comments": [
            {
                "author": {"login": "alice"},
                "body": "Looks good",
                "createdAt": "2026-11-15T10:00:00Z",
            },
            {
                "author": {"login": "coderabbitai[bot]"},
                "body": "Fix edge case",
                "createdAt": "2026-11-15T10:01:00Z",
            },
        ],
        "reviews": [
            {
                "author": {"login": "gemini-code-assist"},
                "body": "Changes needed",
                "state": "COMMENTED",
                "submittedAt": "2026-11-15T10:02:00Z",
            },
            {
                "author": {"login": "copilot"},
                "body": "LGTM",
                "state": "APPROVED",
                "submittedAt": "2026-11-15T10:03:00Z",
            },
            {
                "author": {"login": "sonarcloud"},
                "body": "Old review",
                "state": "DISMISSED",
                "submittedAt": "2026-11-15T10:03:00Z",
            },
        ],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    with tempfile.TemporaryDirectory() as ws_dir, mock.patch(
        "subprocess.run",
        return_value=mock.MagicMock(
            stdout=json.dumps(payload), stderr="", returncode=0
        ),
    ):
      res = poll_ci_status(
          pr_number=15,
          ws=ws_dir,
          quiescence_sec=0,
          max_wait_sec=1,
          hosting_mode="github",
      )
    self.assertEqual(res["ciStatus"], "SUCCESS")
    self.assertIs(res["quiescenceSatisfied"], True)
    self.assertIs(res["readyToLand"], False)
    self.assertEqual(res["unresolvedBotCommentsCount"], 2)
    authors = [c["author"] for c in res["botComments"]]
    self.assertEqual(authors, ["coderabbitai[bot]", "gemini-code-assist"])

  def test_pending_and_quiescence_zero_and_invalid_poll_interval(self):
    """Handles pending max_wait<=1, quiescence=0 no-sleep, and invalid poll interval fallback."""
    pending_payload = {
        "statusCheckRollup": [{"name": "ci", "status": "IN_PROGRESS"}],
        "comments": [],
        "reviews": [],
    }
    completed_payload = {
        "statusCheckRollup": [{
            "name": "ci",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "completedAt": "2026-11-15T12:00:00Z",
        }],
        "comments": [],
        "reviews": [],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    base_ts = datetime.datetime(
        2026, 11, 15, 12, 0, 0, tzinfo=datetime.timezone.utc
    ).timestamp()

    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      wf_dir = os.path.join(ws_dir, ".workflow")
      os.makedirs(wf_dir)
      with open(
          os.path.join(wf_dir, "config.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {"ci": {"quiescenceSeconds": 0, "pollIntervalSeconds": 7}}, f
        )

      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True):
        # Pending with max_wait_sec=1 returns PENDING immediately without sleeping
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(pending_payload), stderr="", returncode=0
            ),
        ), mock.patch("time.sleep") as mock_sleep:
          res_pending = poll_ci_status(
              pr_number=20, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
          )
        self.assertEqual(res_pending["ciStatus"], "PENDING")
        self.assertIs(res_pending["readyToLand"], False)
        mock_sleep.assert_not_called()

        # Quiescence=0 returns SUCCESS on poll 1 without sleeping
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(completed_payload), stderr="", returncode=0
            ),
        ), mock.patch("time.time", return_value=base_ts), mock.patch(
            "time.sleep"
        ) as mock_sleep:
          res_zero_q = poll_ci_status(
              pr_number=20, ws=ws_dir, max_wait_sec=600, hosting_mode="github"
          )
        self.assertEqual(res_zero_q["ciStatus"], "SUCCESS")
        self.assertIs(res_zero_q["readyToLand"], True)
        mock_sleep.assert_not_called()

        # Invalid poll_interval_sec (0) falls back to configured default (7s)
        now = [base_ts]
        sleep_calls = []

        def fake_sleep(sec):
          sleep_calls.append(sec)
          now[0] += sec

        with mock.patch(
            "subprocess.run",
            side_effect=[
                mock.MagicMock(
                    stdout=json.dumps(pending_payload), stderr="", returncode=0
                ),
                mock.MagicMock(
                    stdout=json.dumps(completed_payload), stderr="", returncode=0
                ),
            ],
        ), mock.patch("time.time", side_effect=lambda: now[0]), mock.patch(
            "time.sleep", side_effect=fake_sleep
        ):
          res_poll = poll_ci_status(
              pr_number=20,
              ws=ws_dir,
              poll_interval_sec=0,
              max_wait_sec=60,
              hosting_mode="github",
          )
        self.assertEqual(res_poll["ciStatus"], "SUCCESS")
        self.assertEqual(sleep_calls, [7])


class TestCiPollingReExportsAndArchitecture(unittest.TestCase):
  """Verifies SCAR-GENUI-08 re-export identity and SCAR-ARCH-05 zero-shebang invariant (GH-213)."""

  def test_mcp_server_reexports_ci_polling_by_identity(self):
    """Verifies mcp.server re-exports detect_hosting_mode and poll_ci_status identically."""
    self.assertIs(
        mcp_server.detect_hosting_mode, ci_polling.detect_hosting_mode
    )
    self.assertIs(mcp_server.poll_ci_status, ci_polling.poll_ci_status)

  def test_lib_ci_polling_has_no_shebang_header(self):
    """Verifies lib/ci_polling.py complies with SCAR-ARCH-05 zero-shebang library rule."""
    mod_path = os.path.join(REPO_ROOT, "lib", "ci_polling.py")
    with open(mod_path, "r", encoding="utf-8") as f:
      first_line = f.readline()
    self.assertFalse(first_line.startswith("#!"))


if __name__ == "__main__":
  unittest.main()
