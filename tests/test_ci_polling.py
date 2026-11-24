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


def _make_graphql_pr_payload(
    *,
    pr_number: int = 47,
    head_branch: str = "feat/47-multi-contributor",
    head_oid: str = "sha_head_001",
    base_branch: str = "main",
    url: str = "https://github.com/org/repo/pull/47",
    is_draft: bool = False,
    mergeable: str = "MERGEABLE",
    merge_state: str = "CLEAN",
    review_decision: str = "APPROVED",
    branch_protection=None,
    checks=None,
    review_requests=None,
    latest_opinionated_reviews=None,
    reviews=None,
    review_threads=None,
    comments=None,
):
  """Builds a realistic GraphQL pullRequest response payload for unit tests."""
  if checks is None:
    checks = [{
        "__typename": "CheckRun",
        "name": "ci",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
        "completedAt": "2026-11-01T10:00:00Z",
    }]
  return {
      "data": {
          "repository": {
              "pullRequest": {
                  "number": pr_number,
                  "url": url,
                  "state": "OPEN",
                  "isDraft": is_draft,
                  "headRefName": head_branch,
                  "headRefOid": head_oid,
                  "baseRefName": base_branch,
                  "mergeable": mergeable,
                  "mergeStateStatus": merge_state,
                  "reviewDecision": review_decision,
                  "baseRef": {
                      "branchProtectionRule": branch_protection,
                  },
                  "commits": {
                      "nodes": [{
                          "commit": {
                              "oid": head_oid,
                              "statusCheckRollup": {
                                  "contexts": {"nodes": checks},
                              },
                          }
                      }]
                  },
                  "reviewRequests": {"nodes": review_requests or []},
                  "latestOpinionatedReviews": {
                      "nodes": latest_opinionated_reviews or []
                  },
                  "reviews": {"nodes": reviews or []},
                  "reviewThreads": {"nodes": review_threads or []},
                  "comments": {"nodes": comments or []},
              }
          }
      }
  }


def _write_ci_config(ws_dir: str, ci_dict: dict) -> None:
  """Writes .workflow/config.json with the given ci section inside ws_dir."""
  wf_dir = os.path.join(ws_dir, ".workflow")
  os.makedirs(wf_dir, exist_ok=True)
  with open(os.path.join(wf_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"ci": ci_dict}, f)


class TestMultiContributorPeerReviewsAndParking(unittest.TestCase):
  """Hermetic unit tests for sole-dev defaults, opt-in peer reviews, CODEOWNERS, and parking (GH-47)."""

  def test_ci_config_defaults_and_validation_and_env_overrides(self):
    """Verifies opt-in ci.* defaults (false/0/''), invalid-type fallbacks, and env overrides."""
    from lib.config import get_workflow_config, resolve_workflow_config

    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True):
        cfg = resolve_workflow_config(ws_dir)
        self.assertIs(cfg["ci"]["enablePeerReviews"], False)
        self.assertEqual(cfg["ci"]["minApprovals"], 0)
        self.assertIs(cfg["ci"]["requireCodeOwnerReviews"], False)
        self.assertIs(cfg["ci"]["enableSessionParking"], False)
        self.assertEqual(cfg["ci"]["parkingCronExpression"], "")

        # Invalid types fall back cleanly to sole-dev defaults
        _write_ci_config(
            ws_dir,
            {
                "enablePeerReviews": "yes",
                "minApprovals": -2,
                "requireCodeOwnerReviews": 1,
                "enableSessionParking": "true",
                "parkingCronExpression": 123,
            },
        )
        bad_cfg = resolve_workflow_config(ws_dir)
        self.assertIs(bad_cfg["ci"]["enablePeerReviews"], False)
        self.assertEqual(bad_cfg["ci"]["minApprovals"], 0)
        self.assertIs(bad_cfg["ci"]["requireCodeOwnerReviews"], False)
        self.assertIs(bad_cfg["ci"]["enableSessionParking"], False)
        self.assertEqual(bad_cfg["ci"]["parkingCronExpression"], "")

        # Boolean minApprovals rejected too
        _write_ci_config(ws_dir, {"minApprovals": True})
        self.assertEqual(resolve_workflow_config(ws_dir)["ci"]["minApprovals"], 0)

      # Env overrides apply consistently across resolve_workflow_config and get_workflow_config
      env = _clean_env(
          fake_home,
          MAKEFILE_WORKFLOW_CI_ENABLE_PEER_REVIEWS="true",
          MAKEFILE_WORKFLOW_CI_MIN_APPROVALS="2",
          MAKEFILE_WORKFLOW_CI_REQUIRE_CODEOWNER_REVIEWS="1",
          MAKEFILE_WORKFLOW_CI_ENABLE_SESSION_PARKING="true",
          MAKEFILE_WORKFLOW_CI_PARKING_CRON="*/30 * * * *",
      )
      with mock.patch.dict(os.environ, env, clear=True):
        env_cfg = resolve_workflow_config(ws_dir)
        hook_cfg, _ = get_workflow_config([ws_dir])
        for target_cfg in (env_cfg["ci"], hook_cfg["ci"]):
          self.assertIs(target_cfg["enablePeerReviews"], True)
          self.assertEqual(target_cfg["minApprovals"], 2)
          self.assertIs(target_cfg["requireCodeOwnerReviews"], True)
          self.assertIs(target_cfg["enableSessionParking"], True)
          self.assertEqual(target_cfg["parkingCronExpression"], "*/30 * * * *")

  def test_sole_dev_default_never_blocks_or_parks_on_missing_peer_reviews(self):
    """Sole-developer defaults (enablePeerReviews=False, enableSessionParking=False) stay zero-wait."""
    payload = _make_graphql_pr_payload(
        review_decision="REVIEW_REQUIRED",
        merge_state="BLOCKED",
        branch_protection={
            "requiredApprovingReviewCount": 2,
            "dismissesStaleReviews": True,
            "requiresCodeOwnerReviews": True,
        },
        review_requests=[
            {"requestedReviewer": {"__typename": "Team", "combinedSlug": "org/core"}}
        ],
        review_threads=[{
            "isResolved": False,
            "isOutdated": False,
            "path": "lib/app.py",
            "line": 10,
            "comments": {
                "nodes": [{
                    "author": {"login": "alice"},
                    "body": "Consider renaming",
                    "createdAt": "2026-11-01T10:05:00Z",
                }]
            },
        }],
    )
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(payload), stderr="", returncode=0
          ),
      ) as mock_run:
        res = poll_ci_status(
            pr_number=47,
            ws=ws_dir,
            quiescence_sec=0,
            max_wait_sec=1,
            hosting_mode="github",
        )
      # Single PR-scoped GraphQL call, no REST branch protection call in sole-dev mode
      self.assertEqual(mock_run.call_count, 1)
      cmd = mock_run.call_args.args[0]
      self.assertEqual(cmd[:3], ["gh", "api", "graphql"])
      self.assertIn("number=47", " ".join(cmd))

      self.assertEqual(res["ciStatus"], "SUCCESS")
      self.assertIs(res["quiescenceSatisfied"], True)
      self.assertIs(res["readyToLand"], True)
      self.assertIs(res["parkingRecommended"], False)
      # Passive review metadata is still surfaced
      self.assertEqual(res["reviewDecision"], "REVIEW_REQUIRED")
      self.assertEqual(res["approvalsCount"], 0)
      self.assertEqual(res["requiredApprovals"], 2)
      self.assertEqual(res["pendingReviewers"], ["org/core"])
      self.assertEqual(res["unresolvedThreadsCount"], 1)

  def test_opt_in_session_parking_alone_parks_on_long_running_ci(self):
    """Enabling enableSessionParking=True without peer reviews parks on pending/timed-out CI."""
    pending_payload = _make_graphql_pr_payload(
        checks=[{
            "__typename": "CheckRun",
            "name": "e2e-suite",
            "status": "IN_PROGRESS",
            "conclusion": None,
        }],
    )
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 0,
              "enablePeerReviews": False,
              "enableSessionParking": True,
          },
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(pending_payload), stderr="", returncode=0
          ),
      ):
        res = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res["ciStatus"], "PENDING")
      self.assertIs(res["readyToLand"], False)
      self.assertIs(res["parkingRecommended"], True)
      self.assertEqual(res["sessionStatus"], "PARKED")

  def test_team_mode_approvals_min_floor_codeowners_and_stale_reviews(self):
    """Team mode enforces M/N approvals, minApprovals floor, CODEOWNERS, and stale review dismissal."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 0,
              "enablePeerReviews": True,
              "minApprovals": 2,
              "requireCodeOwnerReviews": True,
              "enableSessionParking": True,
          },
      )
      # 1. Stale approval (old commit OID) excluded when dismissesStaleReviews=True
      stale_payload = _make_graphql_pr_payload(
          head_oid="sha_new",
          review_decision="REVIEW_REQUIRED",
          branch_protection={
              "requiredApprovingReviewCount": 1,
              "dismissesStaleReviews": True,
              "requiresCodeOwnerReviews": True,
          },
          latest_opinionated_reviews=[
              {
                  "author": {"login": "alice"},
                  "state": "APPROVED",
                  "body": "LGTM",
                  "submittedAt": "2026-11-01T09:00:00Z",
                  "commit": {"oid": "sha_old"},
              },
              {
                  "author": {"login": "bob"},
                  "state": "APPROVED",
                  "body": "+1",
                  "submittedAt": "2026-11-01T09:30:00Z",
                  "commit": {"oid": "sha_new"},
              },
          ],
          review_requests=[
              {"requestedReviewer": {"__typename": "Team", "combinedSlug": "org/sec"}}
          ],
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(stale_payload), stderr="", returncode=0
          ),
      ):
        res_stale = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      # Only bob's fresh approval counts -> 1/2 (minApprovals floor = max(1, 2) = 2)
      self.assertEqual(res_stale["approvalsCount"], 1)
      self.assertEqual(res_stale["requiredApprovals"], 2)
      self.assertIs(res_stale["readyToLand"], False)
      self.assertIs(res_stale["parkingRecommended"], True)
      self.assertEqual(res_stale["sessionStatus"], "PARKED")
      self.assertEqual(res_stale["pendingReviewers"], ["org/sec"])

      # 2. Both fresh approvals + no pending CODEOWNERS -> readyToLand=True
      approved_payload = _make_graphql_pr_payload(
          head_oid="sha_new",
          review_decision="APPROVED",
          branch_protection={
              "requiredApprovingReviewCount": 1,
              "dismissesStaleReviews": True,
              "requiresCodeOwnerReviews": True,
          },
          latest_opinionated_reviews=[
              {
                  "author": {"login": "alice"},
                  "state": "APPROVED",
                  "body": "LGTM",
                  "submittedAt": "2026-11-01T10:00:00Z",
                  "commit": {"oid": "sha_new"},
              },
              {
                  "author": {"login": "bob"},
                  "state": "APPROVED",
                  "body": "+1",
                  "submittedAt": "2026-11-01T10:01:00Z",
                  "commit": {"oid": "sha_new"},
              },
          ],
          review_requests=[],
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(approved_payload), stderr="", returncode=0
          ),
      ):
        res_ok = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_ok["approvalsCount"], 2)
      self.assertEqual(res_ok["requiredApprovals"], 2)
      self.assertIs(res_ok["readyToLand"], True)
      self.assertIs(res_ok["parkingRecommended"], False)
      self.assertEqual(res_ok["sessionStatus"], "READY")

  def test_team_mode_changes_requested_conflicts_and_threads(self):
    """Classifies CHANGES_REQUESTED/CONFLICTING as REMEDIATION_REQUIRED and blocks unresolved threads."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 0,
              "enablePeerReviews": True,
              "enableSessionParking": True,
          },
      )
      # CHANGES_REQUESTED -> REMEDIATION_REQUIRED (never parked)
      changes_payload = _make_graphql_pr_payload(
          review_decision="CHANGES_REQUESTED",
          latest_opinionated_reviews=[{
              "author": {"login": "carol"},
              "state": "CHANGES_REQUESTED",
              "body": "Please add bounds check",
              "submittedAt": "2026-11-01T10:00:00Z",
              "commit": {"oid": "sha_head_001"},
          }],
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(changes_payload), stderr="", returncode=0
          ),
      ):
        res_cr = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertIs(res_cr["readyToLand"], False)
      self.assertIs(res_cr["parkingRecommended"], False)
      self.assertEqual(res_cr["sessionStatus"], "REMEDIATION_REQUIRED")
      self.assertEqual(res_cr["changesRequestedBy"], ["carol"])

      # Unresolved vs resolved reviewThreads
      unresolved_payload = _make_graphql_pr_payload(
          review_decision="APPROVED",
          review_threads=[
              {
                  "isResolved": False,
                  "isOutdated": False,
                  "path": "lib/a.py",
                  "line": 12,
                  "comments": {"nodes": [{"author": {"login": "dan"}, "body": "Fix"}]},
              },
              {
                  "isResolved": True,
                  "isOutdated": False,
                  "path": "lib/b.py",
                  "line": 5,
                  "comments": {"nodes": [{"author": {"login": "dan"}, "body": "Done"}]},
              },
          ],
      )
      resolved_only_payload = _make_graphql_pr_payload(
          review_decision="APPROVED",
          review_threads=[{
              "isResolved": True,
              "isOutdated": False,
              "path": "lib/b.py",
              "line": 5,
              "comments": {"nodes": [{"author": {"login": "dan"}, "body": "Done"}]},
          }],
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True):
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(unresolved_payload), stderr="", returncode=0
            ),
        ):
          res_unres = poll_ci_status(
              pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
          )
        self.assertEqual(res_unres["unresolvedThreadsCount"], 1)
        self.assertIs(res_unres["readyToLand"], False)
        self.assertIs(res_unres["parkingRecommended"], True)

        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(resolved_only_payload), stderr="", returncode=0
            ),
        ):
          res_res = poll_ci_status(
              pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
          )
        self.assertEqual(res_res["unresolvedThreadsCount"], 0)
        self.assertIs(res_res["readyToLand"], True)

  def test_non_admin_rest_branch_protection_fallback_and_403_404_degradation(
      self,
  ):
    """Falls back to REST branch protection and degrades cleanly on HTTP 403/404 (SCAR-TEST-24)."""
    no_bp_payload = _make_graphql_pr_payload(
        branch_protection=None,
        review_decision="REVIEW_REQUIRED",
        merge_state="BLOCKED",
        latest_opinionated_reviews=[{
            "author": {"login": "alice"},
            "state": "APPROVED",
            "body": "+1",
            "submittedAt": "2026-11-01T10:00:00Z",
            "commit": {"oid": "sha_head_001"},
        }],
    )
    rest_ok = {
        "required_pull_request_reviews": {
            "required_approving_review_count": 2,
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
        }
    }
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 0,
              "enablePeerReviews": True,
              "minApprovals": 2,
              "enableSessionParking": True,
          },
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True):
        # 1. REST 200 populates policy from REST
        def rest_success(cmd, **kwargs):
          if cmd[:3] == ["gh", "api", "graphql"]:
            return mock.MagicMock(
                stdout=json.dumps(no_bp_payload), stderr="", returncode=0
            )
          return mock.MagicMock(
              stdout=json.dumps(rest_ok), stderr="", returncode=0
          )

        with mock.patch("subprocess.run", side_effect=rest_success):
          res_rest = poll_ci_status(
              pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
          )
        self.assertEqual(res_rest["policySource"], "rest")
        self.assertEqual(res_rest["requiredApprovals"], 2)
        self.assertIs(res_rest["requireCodeOwnerReviews"], True)
        self.assertIs(res_rest["parkingRecommended"], True)

        # 2. REST HTTP 403/404 degrades gracefully without crashing
        for status_msg in ("gh: HTTP 403 Forbidden", "gh: HTTP 404 Not Found"):
          with self.subTest(status_msg=status_msg):
            def rest_denied(cmd, **kwargs):
              if cmd[:3] == ["gh", "api", "graphql"]:
                return mock.MagicMock(
                    stdout=json.dumps(no_bp_payload), stderr="", returncode=0
                )
              return mock.MagicMock(stdout="", stderr=status_msg, returncode=1)

            with mock.patch("subprocess.run", side_effect=rest_denied):
              res_fb = poll_ci_status(
                  pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
              )
            self.assertEqual(res_fb["ciStatus"], "SUCCESS")
            self.assertEqual(res_fb["policySource"], "fallback")
            self.assertEqual(res_fb["requiredApprovals"], 2)
            self.assertIs(res_fb["readyToLand"], False)
            self.assertIs(res_fb["parkingRecommended"], True)

  def test_legacy_status_context_and_branch_firewall_and_quiescence_accuracy(
      self,
  ):
    """Handles legacy StatusContext, always-on headRefName firewalling, and single-probe quiescence."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True):
        # 1. Legacy StatusContext PENDING and FAILURE
        sc_pending = _make_graphql_pr_payload(
            checks=[{
                "__typename": "StatusContext",
                "context": "ci/circleci",
                "state": "PENDING",
                "createdAt": "2026-11-01T10:00:00Z",
            }]
        )
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(sc_pending), stderr="", returncode=0
            ),
        ):
          res_p = poll_ci_status(
              pr_number=47,
              ws=ws_dir,
              quiescence_sec=0,
              max_wait_sec=1,
              hosting_mode="github",
          )
        self.assertEqual(res_p["ciStatus"], "PENDING")
        self.assertIs(res_p["readyToLand"], False)

        sc_fail = _make_graphql_pr_payload(
            checks=[{
                "__typename": "StatusContext",
                "context": "ci/jenkins",
                "state": "ERROR",
                "createdAt": "2026-11-01T10:00:00Z",
            }]
        )
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(sc_fail), stderr="", returncode=0
            ),
        ):
          res_f = poll_ci_status(
              pr_number=47,
              ws=ws_dir,
              quiescence_sec=0,
              max_wait_sec=1,
              hosting_mode="github",
          )
        self.assertEqual(res_f["ciStatus"], "FAILURE")
        self.assertEqual(res_f["failedChecks"], ["ci/jenkins"])

        # 2. Always-on PR-branch firewalling (explicit expected_branch)
        wrong_branch_payload = _make_graphql_pr_payload(
            head_branch="feat/99-someone-elses-pr"
        )
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(wrong_branch_payload), stderr="", returncode=0
            ),
        ):
          res_mm = poll_ci_status(
              pr_number=47,
              ws=ws_dir,
              quiescence_sec=0,
              max_wait_sec=1,
              hosting_mode="github",
              expected_branch="feat/47-my-branch",
          )
        self.assertEqual(res_mm["ciStatus"], "ERROR")
        self.assertIs(res_mm["branchMismatch"], True)
        self.assertIs(res_mm["readyToLand"], False)

        # 3. Auto-detected local .git/HEAD feature branch firewalling
        git_dir = os.path.join(ws_dir, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
          f.write("ref: refs/heads/feat/47-local-branch\n")
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(wrong_branch_payload), stderr="", returncode=0
            ),
        ):
          res_auto_mm = poll_ci_status(
              pr_number=47,
              ws=ws_dir,
              quiescence_sec=0,
              max_wait_sec=1,
              hosting_mode="github",
          )
        self.assertEqual(res_auto_mm["ciStatus"], "ERROR")
        self.assertIs(res_auto_mm["branchMismatch"], True)

        # 4. Accurate single-probe quiescenceSatisfied when elapsed < quiescence_sec
        recent_ts = datetime.datetime(
            2026, 11, 23, 12, 0, 0, tzinfo=datetime.timezone.utc
        )
        recent_payload = _make_graphql_pr_payload(
            head_branch="feat/47-local-branch",
            checks=[{
                "__typename": "CheckRun",
                "name": "ci",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "completedAt": "2026-11-23T12:00:00Z",
            }],
        )
        with mock.patch(
            "subprocess.run",
            return_value=mock.MagicMock(
                stdout=json.dumps(recent_payload), stderr="", returncode=0
            ),
        ), mock.patch("time.time", return_value=recent_ts.timestamp() + 10):
          res_q = poll_ci_status(
              pr_number=47,
              ws=ws_dir,
              quiescence_sec=180,
              max_wait_sec=1,
              hosting_mode="github",
          )
        self.assertEqual(res_q["ciStatus"], "SUCCESS")
        self.assertIs(res_q["quiescenceSatisfied"], False)
        self.assertIs(res_q["readyToLand"], False)

  def test_non_admin_graphql_forbidden_branch_protection_succeeds_sole_and_team(
      self,
  ):
    """Non-admin tokens succeed in sole-dev mode (no BP field) and degrade cleanly on GraphQL FORBIDDEN in team mode (BLOCKER-1)."""
    # 1. Sole-developer default query omits admin-gated branchProtectionRule
    sole_payload = _make_graphql_pr_payload(branch_protection=None)
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(sole_payload), stderr="", returncode=0
          ),
      ) as mock_run:
        res_sole = poll_ci_status(
            pr_number=47,
            ws=ws_dir,
            quiescence_sec=0,
            max_wait_sec=1,
            hosting_mode="github",
        )
      self.assertEqual(res_sole["ciStatus"], "SUCCESS")
      self.assertIs(res_sole["readyToLand"], True)
      query_arg = " ".join(mock_run.call_args.args[0])
      self.assertNotIn("branchProtectionRule", query_arg)

      # 2. Team mode accepts partial GraphQL data when gh exits 1 with FORBIDDEN on branchProtectionRule
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 0,
              "enablePeerReviews": True,
              "minApprovals": 2,
              "enableSessionParking": True,
          },
      )
      partial_forbidden = _make_graphql_pr_payload(
          branch_protection=None,
          review_decision="REVIEW_REQUIRED",
          merge_state="BLOCKED",
          latest_opinionated_reviews=[{
              "author": {"login": "alice"},
              "state": "APPROVED",
              "body": "+1",
              "submittedAt": "2026-11-01T10:00:00Z",
              "commit": {"oid": "sha_head_001"},
          }],
      )
      partial_forbidden["errors"] = [{
          "type": "FORBIDDEN",
          "path": ["repository", "pullRequest", "baseRef", "branchProtectionRule"],
          "message": "Resource not accessible by personal access token",
      }]

      def graphql_partial_then_rest_403(cmd, **kwargs):
        if cmd[:3] == ["gh", "api", "graphql"]:
          return mock.MagicMock(
              stdout=json.dumps(partial_forbidden),
              stderr="gh: Resource not accessible by personal access token",
              returncode=1,
          )
        return mock.MagicMock(
            stdout="", stderr="gh: HTTP 403 Forbidden", returncode=1
        )

      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run", side_effect=graphql_partial_then_rest_403
      ):
        res_team = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_team["mode"], "github")
      self.assertEqual(res_team["ciStatus"], "SUCCESS")
      self.assertEqual(res_team["policySource"], "fallback")
      self.assertEqual(res_team["approvalsCount"], 1)
      self.assertEqual(res_team["requiredApprovals"], 2)
      self.assertIs(res_team["readyToLand"], False)
      self.assertIs(res_team["parkingRecommended"], True)

      # 3. Genuine non-permission GraphQL failure (null PR) still returns github_error
      null_pr_err = {
          "data": {"repository": {"pullRequest": None}},
          "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a PullRequest"}],
      }
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(null_pr_err),
              stderr="gh: Could not resolve to a PullRequest",
              returncode=1,
          ),
      ):
        res_err = poll_ci_status(
            pr_number=999, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_err["mode"], "github_error")
      self.assertEqual(res_err["ciStatus"], "ERROR")

  def test_status_context_expected_and_single_probe_remediation_precedence(
      self,
  ):
    """Treats StatusContext EXPECTED as incomplete and enforces REMEDIATION_REQUIRED ahead of PARKED on single-probe (BLOCKER-4)."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 0,
              "enablePeerReviews": True,
              "enableSessionParking": True,
          },
      )
      # 1. StatusContext state=EXPECTED is incomplete (PENDING), never falsely green
      expected_payload = _make_graphql_pr_payload(
          checks=[{
              "__typename": "StatusContext",
              "context": "ci/required-gate",
              "state": "EXPECTED",
              "createdAt": "2026-11-01T10:00:00Z",
          }]
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(expected_payload), stderr="", returncode=0
          ),
      ):
        res_exp = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_exp["ciStatus"], "PENDING")
      self.assertIs(res_exp["readyToLand"], False)
      self.assertIs(res_exp["parkingRecommended"], True)
      self.assertEqual(res_exp["sessionStatus"], "PARKED")

      # 2. Single-probe with PENDING CI + CHANGES_REQUESTED -> REMEDIATION_REQUIRED beats PARKED
      pending_changes_payload = _make_graphql_pr_payload(
          checks=[{
              "__typename": "CheckRun",
              "name": "e2e",
              "status": "IN_PROGRESS",
              "conclusion": None,
          }],
          review_decision="CHANGES_REQUESTED",
          latest_opinionated_reviews=[{
              "author": {"login": "bob"},
              "state": "CHANGES_REQUESTED",
              "body": "Fix null deref",
              "submittedAt": "2026-11-01T10:00:00Z",
              "commit": {"oid": "sha_head_001"},
          }],
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(pending_changes_payload), stderr="", returncode=0
          ),
      ):
        res_cr = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_cr["ciStatus"], "PENDING")
      self.assertEqual(res_cr["sessionStatus"], "REMEDIATION_REQUIRED")
      self.assertIs(res_cr["parkingRecommended"], False)
      self.assertIs(res_cr["readyToLand"], False)

      # 3. Single-probe with PENDING CI + CONFLICTING -> REMEDIATION_REQUIRED beats PARKED
      pending_conflict_payload = _make_graphql_pr_payload(
          checks=[{
              "__typename": "CheckRun",
              "name": "e2e",
              "status": "IN_PROGRESS",
              "conclusion": None,
          }],
          mergeable="CONFLICTING",
          merge_state="DIRTY",
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(pending_conflict_payload), stderr="", returncode=0
          ),
      ):
        res_conf = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_conf["sessionStatus"], "REMEDIATION_REQUIRED")
      self.assertIs(res_conf["parkingRecommended"], False)

  def test_zero_check_pr_merged_closed_slash_base_null_commit_and_timeout_meta(
      self,
  ):
    """Covers NBOs 1-5: zero-check single-probe, MERGED/CLOSED prState, URL-encoded base_ref, null commit stale dismissal, and TIMEOUT metadata."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      _write_ci_config(
          ws_dir,
          {
              "quiescenceSeconds": 180,
              "enablePeerReviews": True,
              "enableSessionParking": True,
          },
      )
      # NBO-1: Zero-check docs PR approved and quiescent on single-probe -> readyToLand=True
      zero_check_payload = _make_graphql_pr_payload(
          checks=[],
          review_decision="APPROVED",
          latest_opinionated_reviews=[{
              "author": {"login": "alice"},
              "state": "APPROVED",
              "body": "LGTM",
              "submittedAt": "2026-11-01T10:00:00Z",
              "commit": {"oid": "sha_head_001"},
          }],
      )
      zero_check_payload["data"]["repository"]["pullRequest"]["updatedAt"] = (
          "2026-11-01T10:00:00Z"
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(zero_check_payload), stderr="", returncode=0
          ),
      ):
        res_zero = poll_ci_status(
            pr_number=47, ws=ws_dir, max_wait_sec=1, hosting_mode="github"
        )
      self.assertEqual(res_zero["ciStatus"], "SUCCESS")
      self.assertIs(res_zero["quiescenceSatisfied"], True)
      self.assertIs(res_zero["readyToLand"], True)
      self.assertEqual(res_zero["prState"], "OPEN")

      # NBO-2: MERGED / CLOSED PR surfaces prState and never returns readyToLand=True
      for terminal_state in ("MERGED", "CLOSED"):
        with self.subTest(terminal_state=terminal_state):
          term_payload = _make_graphql_pr_payload(review_decision="APPROVED")
          term_payload["data"]["repository"]["pullRequest"]["state"] = terminal_state
          with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
              "subprocess.run",
              return_value=mock.MagicMock(
                  stdout=json.dumps(term_payload), stderr="", returncode=0
              ),
          ):
            res_term = poll_ci_status(
                pr_number=47,
                ws=ws_dir,
                quiescence_sec=0,
                max_wait_sec=1,
                hosting_mode="github",
            )
          self.assertEqual(res_term["prState"], terminal_state)
          self.assertIs(res_term["readyToLand"], False)
          self.assertIs(res_term["parkingRecommended"], False)
          self.assertEqual(res_term["sessionStatus"], f"PR_{terminal_state}")

      # NBO-3: Slash-containing base branch URL-encoded in REST protection call
      slash_payload = _make_graphql_pr_payload(
          base_branch="release/2.0",
          branch_protection=None,
      )
      called_cmds = []

      def capture_rest(cmd, **kwargs):
        called_cmds.append(cmd)
        if cmd[:3] == ["gh", "api", "graphql"]:
          return mock.MagicMock(
              stdout=json.dumps(slash_payload), stderr="", returncode=0
          )
        return mock.MagicMock(stdout="", stderr="HTTP 404", returncode=1)

      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run", side_effect=capture_rest
      ):
        poll_ci_status(
            pr_number=47,
            ws=ws_dir,
            quiescence_sec=0,
            max_wait_sec=1,
            hosting_mode="github",
        )
      rest_cmds = [c for c in called_cmds if c[:2] == ["gh", "api"] and "graphql" not in c]
      self.assertEqual(len(rest_cmds), 1)
      self.assertIn("branches/release%2F2.0/protection", rest_cmds[0][2])

      # NBO-4: Null commit on APPROVED review treated as stale when dismissesStaleReviews=True
      orphan_payload = _make_graphql_pr_payload(
          head_oid="sha_head_002",
          review_decision="REVIEW_REQUIRED",
          branch_protection={
              "requiredApprovingReviewCount": 1,
              "dismissesStaleReviews": True,
              "requiresCodeOwnerReviews": False,
          },
          latest_opinionated_reviews=[{
              "author": {"login": "alice"},
              "state": "APPROVED",
              "body": "Old approval",
              "submittedAt": "2026-11-01T10:00:00Z",
              "commit": None,
          }],
      )
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(orphan_payload), stderr="", returncode=0
          ),
      ):
        res_orphan = poll_ci_status(
            pr_number=47,
            ws=ws_dir,
            quiescence_sec=0,
            max_wait_sec=1,
            hosting_mode="github",
        )
      self.assertEqual(res_orphan["approvalsCount"], 0)
      self.assertIs(res_orphan["readyToLand"], False)

      # NBO-5: TIMEOUT preserves last-observed PR metadata (prUrl, headBranch, review counts)
      pending_meta_payload = _make_graphql_pr_payload(
          url="https://github.com/org/repo/pull/47",
          head_branch="feat/47-timeout-meta",
          checks=[{
              "__typename": "CheckRun",
              "name": "slow-e2e",
              "status": "IN_PROGRESS",
              "conclusion": None,
          }],
          branch_protection={
              "requiredApprovingReviewCount": 2,
              "dismissesStaleReviews": False,
              "requiresCodeOwnerReviews": False,
          },
          latest_opinionated_reviews=[{
              "author": {"login": "alice"},
              "state": "APPROVED",
              "body": "+1",
              "submittedAt": "2026-11-01T10:00:00Z",
              "commit": {"oid": "sha_head_001"},
          }],
      )
      clock = [1000.0, 1000.0, 1700.0]
      with mock.patch.dict(os.environ, _clean_env(fake_home), clear=True), mock.patch(
          "subprocess.run",
          return_value=mock.MagicMock(
              stdout=json.dumps(pending_meta_payload), stderr="", returncode=0
          ),
      ), mock.patch("time.time", side_effect=clock), mock.patch("time.sleep"):
        res_to = poll_ci_status(
            pr_number=47,
            ws=ws_dir,
            quiescence_sec=0,
            max_wait_sec=600,
            hosting_mode="github",
        )
      self.assertEqual(res_to["ciStatus"], "TIMEOUT")
      self.assertEqual(res_to["prUrl"], "https://github.com/org/repo/pull/47")
      self.assertEqual(res_to["headBranch"], "feat/47-timeout-meta")
      self.assertEqual(res_to["approvalsCount"], 1)
      self.assertEqual(res_to["requiredApprovals"], 2)


if __name__ == "__main__":
  unittest.main()
