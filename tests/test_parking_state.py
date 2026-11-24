#!/usr/bin/env python3
"""Hermetic unit tests for structured parking state, renderers, and gates (GH-47)."""

import dataclasses
import io
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

from lib.parking_state import (  # noqa: E402
    PARKING_STATE_FILENAME,
    ParkingState,
    ParkingStateStore,
    format_parking_status_label,
    render_parked_triage_card,
    render_parking_receipt,
)
from lib.state_machine import (  # noqa: E402
    advance_workflow_target,
    read_active_target,
    write_active_target,
)
from lib.turn_boundary import read_gate_entry  # noqa: E402
from lib.workflow_gates import (  # noqa: E402
    WorkflowGatesScript,
    generate_state_makefile,
)


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
  """Runs a local git command inside cwd."""
  return subprocess.run(
      ["git", *args],
      cwd=cwd,
      capture_output=True,
      text=True,
      check=True,
  )


def _init_git_repo(repo_dir: str, branch: str = "feat/47-parking-test") -> None:
  """Initializes a hermetic local Git repository on a feature branch."""
  _git(repo_dir, "init", "-q")
  _git(repo_dir, "config", "user.email", "test@example.com")
  _git(repo_dir, "config", "user.name", "Makeflow Test")
  _git(repo_dir, "branch", "-M", "main")
  with open(os.path.join(repo_dir, "README.md"), "w", encoding="utf-8") as f:
    f.write("# Parking Test Repo\n")
  _git(repo_dir, "add", "README.md")
  _git(repo_dir, "commit", "-q", "-m", "chore: initial commit")
  if branch != "main":
    _git(repo_dir, "checkout", "-q", "-b", branch)


def _seed_t70_workspace(repo_dir: str, branch: str = "feat/47-parking-test") -> str:
  """Creates a hermetic Git workspace positioned at Target 70."""
  _init_git_repo(repo_dir, branch=branch)
  state_dir = os.path.join(repo_dir, ".workflow", "state")
  os.makedirs(state_dir, exist_ok=True)
  with open(
      os.path.join(state_dir, "10_ticket.json"), "w", encoding="utf-8"
  ) as f:
    json.dump(
        {
            "ticket_id": "GH-47",
            "title": "Multi-contributor parking",
            "url": "https://github.com/org/repo/issues/47",
            "branch": branch,
        },
        f,
    )
  write_active_target(
      state_dir,
      {
          "previousTarget": "T60",
          "target": "T70",
          "targetName": "bot-quiescence",
      },
  )
  return state_dir


class TestParkingStateAndStore(unittest.TestCase):
  """Unit tests for ParkingState value object and ParkingStateStore repository (GH-47)."""

  def test_lib_parking_state_has_no_shebang_header(self) -> None:
    """Verifies lib/parking_state.py complies with SCAR-ARCH-05 zero-shebang rule."""
    mod_path = os.path.join(REPO_ROOT, "lib", "parking_state.py")
    with open(mod_path, "r", encoding="utf-8") as f:
      first_line = f.readline()
    self.assertFalse(first_line.startswith("#!"))

  def test_parking_state_is_frozen_and_round_trips_store(self) -> None:
    """Verifies ParkingState immutability and JSON round-trip persistence."""
    state = ParkingState(
        parked=True,
        pr_number=47,
        pr_url="https://github.com/org/repo/pull/47",
        head_branch="feat/47-session-parking",
        reason="Awaiting peer review approvals: 1/2",
        ci_status="SUCCESS",
        review_decision="REVIEW_REQUIRED",
        approvals_count=1,
        required_approvals=2,
        require_code_owner_reviews=True,
        pending_reviewers=("alice", "org/core-team"),
        changes_requested_by=(),
        unresolved_threads_count=1,
        parked_at="2026-11-23T10:00:00Z",
        last_checked_at="2026-11-23T10:30:00Z",
        cron_expression="*/30 * * * *",
    )
    with self.assertRaises(dataclasses.FrozenInstanceError):
      state.parked = False  # type: ignore[misc]

    with tempfile.TemporaryDirectory() as tmp:
      store = ParkingStateStore.from_repo_root(tmp)
      self.assertFalse(store.exists())
      self.assertFalse(store.is_parked())

      written_path = store.save(state)
      self.assertEqual(
          os.path.basename(written_path), PARKING_STATE_FILENAME
      )
      self.assertTrue(store.exists())
      self.assertTrue(store.is_parked())

      loaded = store.load()
      self.assertEqual(loaded, state)

      self.assertTrue(store.clear())
      self.assertFalse(store.exists())
      self.assertFalse(store.is_parked())
      self.assertFalse(store.clear())

  def test_from_dict_validation_and_from_poll_result_factory(self) -> None:
    """Validates from_dict type checking and factory creation from poll_ci_status output."""
    with self.assertRaises(TypeError):
      ParkingState.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]

    poll_result = {
        "ciStatus": "SUCCESS",
        "prUrl": "https://github.com/org/repo/pull/88",
        "headBranch": "feat/88-team-pr",
        "reviewDecision": "REVIEW_REQUIRED",
        "approvalsCount": 1,
        "requiredApprovals": 2,
        "requireCodeOwnerReviews": True,
        "pendingReviewers": ["org/security"],
        "changesRequestedBy": [],
        "unresolvedThreadsCount": 2,
        "parkingReason": "Awaiting peer review approvals: 1/2",
    }
    created = ParkingState.from_poll_result(
        poll_result,
        pr_number=88,
        cron_expression="*/30 * * * *",
        parked_at="2026-11-23T11:00:00Z",
        last_checked_at="2026-11-23T11:00:00Z",
    )
    self.assertTrue(created.parked)
    self.assertEqual(created.pr_number, 88)
    self.assertEqual(created.head_branch, "feat/88-team-pr")
    self.assertEqual(created.approvals_count, 1)
    self.assertEqual(created.required_approvals, 2)
    self.assertEqual(created.pending_reviewers, ("org/security",))
    self.assertEqual(created.unresolved_threads_count, 2)
    self.assertEqual(created.cron_expression, "*/30 * * * *")

  def test_store_load_handles_corrupt_non_utf8_and_non_dict_json_safely(
      self,
  ) -> None:
    """Failure-injection test ensuring corrupt or malformed JSON returns None (SCAR-TEST-24)."""
    with tempfile.TemporaryDirectory() as tmp:
      store = ParkingStateStore(tmp)
      for payload in (b"{broken json", b"[]", b"\xff\xfe\xfd"):
        with self.subTest(payload=payload):
          with open(store.path, "wb") as f:
            f.write(payload)
          self.assertIsNone(store.load())
          self.assertFalse(store.is_parked())


class TestParkingRenderers(unittest.TestCase):
  """Unit tests for pure parking label, triage card, and receipt renderers (GH-47)."""

  def test_format_parking_status_label_covers_all_wait_reasons(self) -> None:
    """Formats concise PARKED status labels without ever emitting uppercase COMPLETE."""
    cases = [
        (
            ParkingState(
                parked=True,
                ci_status="SUCCESS",
                approvals_count=1,
                required_approvals=2,
            ),
            "PARKED (Awaiting peer review approvals: 1/2)",
        ),
        (
            ParkingState(
                parked=True,
                ci_status="SUCCESS",
                require_code_owner_reviews=True,
                pending_reviewers=("org/core", "alice"),
            ),
            "PARKED (Awaiting CODEOWNERS / peer review: org/core, alice)",
        ),
        (
            ParkingState(
                parked=True,
                ci_status="SUCCESS",
                unresolved_threads_count=3,
            ),
            "PARKED (Awaiting resolution of 3 review thread(s))",
        ),
        (
            ParkingState(
                parked=True,
                ci_status="PENDING",
                reason="Awaiting long-running CI pipeline",
            ),
            "PARKED (Awaiting long-running CI pipeline)",
        ),
        (
            ParkingState(
                parked=True,
                ci_status="SUCCESS",
                reason="Waiting for COMPLETE check",
            ),
            "PARKED (Waiting for Complete check)",
        ),
    ]
    for state, expected in cases:
      with self.subTest(expected=expected):
        label = format_parking_status_label(state)
        self.assertEqual(label, expected)
        self.assertNotIn("COMPLETE", label)

  def test_render_parked_triage_card_and_receipt_content(self) -> None:
    """Renders 70_bot_triage.md and chat Parking Receipt with PR details and no COMPLETE."""
    state = ParkingState(
        parked=True,
        pr_number=47,
        pr_url="https://github.com/org/repo/pull/47",
        head_branch="feat/47-multi-contributor-session-parking",
        reason="Awaiting peer review approvals: 1/2",
        ci_status="SUCCESS",
        review_decision="REVIEW_REQUIRED",
        approvals_count=1,
        required_approvals=2,
        require_code_owner_reviews=True,
        pending_reviewers=("org/maintainers",),
        unresolved_threads_count=1,
        parked_at="2026-11-23T12:00:00Z",
        last_checked_at="2026-11-23T12:05:00Z",
        cron_expression="*/30 * * * *",
    )
    card = render_parked_triage_card(state)
    self.assertIn("PARKED (Awaiting peer review approvals: 1/2)", card)
    self.assertIn("https://github.com/org/repo/pull/47", card)
    self.assertIn("feat/47-multi-contributor-session-parking", card)
    self.assertIn("1/2", card)
    self.assertIn("org/maintainers", card)
    self.assertIn("resume-parked", card)
    self.assertNotIn("COMPLETE", card)

    receipt = render_parking_receipt(state)
    self.assertIn("PARKED (Awaiting peer review approvals: 1/2)", receipt)
    self.assertIn("https://github.com/org/repo/pull/47", receipt)
    self.assertIn("*/30 * * * *", receipt)
    self.assertIn("resume-parked", receipt)
    self.assertNotIn("COMPLETE", receipt)


class TestParkingGatesAndLifecycle(unittest.TestCase):
  """End-to-end hermetic tests for park-session, resume-parked, and T90 cleanup (GH-47)."""

  def test_generate_state_makefile_declares_park_and_resume_targets_unexported(
      self,
  ) -> None:
    """Declares park-session and resume-parked without adding exported Make env vars."""
    with tempfile.TemporaryDirectory() as tmp:
      mk_text = generate_state_makefile(tmp)
      self.assertIn("park-session", mk_text)
      self.assertIn("resume-parked", mk_text)
      self.assertIn("PR ?=", mk_text)
      self.assertIn("BRANCH ?=", mk_text)
      self.assertIn("CRON ?=", mk_text)
      for line in mk_text.splitlines():
        if line.strip().startswith("export "):
          exported = set(line.strip()[len("export ") :].split())
          self.assertTrue(
              exported.isdisjoint({"PR", "BRANCH", "REASON", "CRON"})
          )

  def test_park_session_rejects_non_t70_target_with_reflex(self) -> None:
    """Blocks park-session outside Target 70 with exit code 2."""
    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      state_dir = os.path.join(repo, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      write_active_target(
          state_dir,
          {
              "previousTarget": "T20",
              "target": "T30",
              "targetName": "implementation-diff",
          },
      )
      out, err = io.StringIO(), io.StringIO()
      rc = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              repo,
              "park-session",
              "--pr",
              "47",
              "--reason",
              "Awaiting review",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 2)
      self.assertIn("PARK_REQUIRES_T70", err.getvalue())

  def test_park_session_and_resume_parked_ready_advances_to_t75_pause_gate(
      self,
  ) -> None:
    """Parks at T70 and resumes directly to T75 pause gate without bypassing landing approval."""
    with tempfile.TemporaryDirectory() as repo:
      state_dir = _seed_t70_workspace(repo)
      out, err = io.StringIO(), io.StringIO()
      rc = WorkflowGatesScript().execute(
          args=[
              "--repo-root",
              repo,
              "park-session",
              "--pr",
              "47",
              "--pr-url",
              "https://github.com/org/repo/pull/47",
              "--branch",
              "feat/47-parking-test",
              "--ci-status",
              "SUCCESS",
              "--review-decision",
              "REVIEW_REQUIRED",
              "--approvals",
              "1",
              "--required-approvals",
              "2",
              "--pending-reviewers",
              "alice,org/core",
              "--reason",
              "Awaiting peer review approvals: 1/2",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, err.getvalue())
      self.assertIn("PARKED (Awaiting peer review approvals: 1/2)", out.getvalue())

      store = ParkingStateStore(state_dir)
      self.assertTrue(store.is_parked())
      active_meta = read_active_target(state_dir)
      self.assertEqual(active_meta["target"], "T70")
      self.assertTrue(active_meta.get("parked"))
      self.assertIn("1/2", active_meta.get("parkingReason", ""))

      with open(
          os.path.join(state_dir, "99_next_action.md"), "r", encoding="utf-8"
      ) as f:
        na_text = f.read()
      self.assertIn("Session Status: PARKED", na_text)
      self.assertNotIn("COMPLETE", na_text)

      # Now simulate PR becoming green and approved on resume-parked
      ready_poll = {
          "mode": "github",
          "ciStatus": "SUCCESS",
          "quiescenceSatisfied": True,
          "readyToLand": True,
          "sessionStatus": "READY",
          "parkingRecommended": False,
          "prUrl": "https://github.com/org/repo/pull/47",
          "headBranch": "feat/47-parking-test",
          "reviewDecision": "APPROVED",
          "approvalsCount": 2,
          "requiredApprovals": 2,
          "requireCodeOwnerReviews": False,
          "pendingReviewers": [],
          "changesRequestedBy": [],
          "unresolvedThreadsCount": 0,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "mergeable": "MERGEABLE",
          "mergeStateStatus": "CLEAN",
          "message": "CI passed and peer reviews approved.",
      }
      out2, err2 = io.StringIO(), io.StringIO()
      with mock.patch(
          "lib.ci_polling.poll_ci_status", return_value=ready_poll
      ) as mock_poll:
        rc2 = WorkflowGatesScript().execute(
            args=["--repo-root", repo, "resume-parked", "--json"],
            stdout=out2,
            stderr=err2,
        )
      self.assertEqual(rc2, 0, err2.getvalue())
      mock_poll.assert_called_once()
      self.assertEqual(mock_poll.call_args.kwargs["max_wait_sec"], 1)
      self.assertEqual(
          mock_poll.call_args.kwargs["expected_branch"],
          "feat/47-parking-test",
      )

      payload = json.loads(out2.getvalue())
      self.assertEqual(payload["action"], "ADVANCED_T75")
      self.assertFalse(store.exists())
      self.assertEqual(read_active_target(state_dir)["target"], "T75")
      self.assertFalse(read_active_target(state_dir).get("parked", False))
      # Mandatory human landing gate recorded (SCAR-PROC-102)
      gate_entry = read_gate_entry(state_dir)
      self.assertIsNotNone(gate_entry)
      self.assertEqual(gate_entry["gate"], "T75")

  def test_resume_parked_remediation_clears_parking_and_guides_t30(self) -> None:
    """Clears parking and emits T70 -> T30 guidance when CHANGES_REQUESTED or CI fails."""
    with tempfile.TemporaryDirectory() as repo:
      state_dir = _seed_t70_workspace(repo)
      ParkingStateStore(state_dir).save(
          ParkingState(
              parked=True,
              pr_number=47,
              head_branch="feat/47-parking-test",
              reason="Awaiting peer review approvals: 0/1",
              ci_status="SUCCESS",
              required_approvals=1,
              parked_at="2026-11-23T10:00:00Z",
              last_checked_at="2026-11-23T10:00:00Z",
          )
      )
      remediation_poll = {
          "mode": "github",
          "ciStatus": "SUCCESS",
          "quiescenceSatisfied": True,
          "readyToLand": False,
          "sessionStatus": "REMEDIATION_REQUIRED",
          "parkingRecommended": False,
          "prUrl": "https://github.com/org/repo/pull/47",
          "headBranch": "feat/47-parking-test",
          "reviewDecision": "CHANGES_REQUESTED",
          "approvalsCount": 0,
          "requiredApprovals": 1,
          "changesRequestedBy": ["reviewer1"],
          "pendingReviewers": [],
          "unresolvedThreadsCount": 1,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": "Changes requested by reviewer1.",
      }
      out, err = io.StringIO(), io.StringIO()
      with mock.patch(
          "lib.ci_polling.poll_ci_status", return_value=remediation_poll
      ):
        rc = WorkflowGatesScript().execute(
            args=["--repo-root", repo, "resume-parked"],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(rc, 0, err.getvalue())
      self.assertIn("REMEDIATION_REQUIRED", out.getvalue())
      self.assertIn("implementation-diff FROM=T70", out.getvalue())
      self.assertFalse(ParkingStateStore(state_dir).exists())
      self.assertEqual(read_active_target(state_dir)["target"], "T70")
      self.assertFalse(read_active_target(state_dir).get("parked", False))

  def test_resume_parked_still_pending_refreshes_state_and_stays_parked(
      self,
  ) -> None:
    """Preserves parked_at, updates counts/last_checked_at, and stays parked when still pending."""
    with tempfile.TemporaryDirectory() as repo:
      state_dir = _seed_t70_workspace(repo)
      ParkingStateStore(state_dir).save(
          ParkingState(
              parked=True,
              pr_number=47,
              head_branch="feat/47-parking-test",
              reason="Awaiting peer review approvals: 0/2",
              ci_status="SUCCESS",
              approvals_count=0,
              required_approvals=2,
              parked_at="2026-11-23T09:00:00Z",
              last_checked_at="2026-11-23T09:00:00Z",
              cron_expression="*/30 * * * *",
          )
      )
      still_pending_poll = {
          "mode": "github",
          "ciStatus": "SUCCESS",
          "quiescenceSatisfied": True,
          "readyToLand": False,
          "sessionStatus": "PARKED",
          "parkingRecommended": True,
          "parkingReason": "Awaiting peer review approvals: 1/2",
          "prUrl": "https://github.com/org/repo/pull/47",
          "headBranch": "feat/47-parking-test",
          "reviewDecision": "REVIEW_REQUIRED",
          "approvalsCount": 1,
          "requiredApprovals": 2,
          "requireCodeOwnerReviews": False,
          "pendingReviewers": ["bob"],
          "changesRequestedBy": [],
          "unresolvedThreadsCount": 0,
          "unresolvedBotCommentsCount": 0,
          "botComments": [],
          "message": "Awaiting peer review approvals: 1/2.",
      }
      out, err = io.StringIO(), io.StringIO()
      with mock.patch(
          "lib.ci_polling.poll_ci_status", return_value=still_pending_poll
      ):
        rc = WorkflowGatesScript().execute(
            args=["--repo-root", repo, "resume-parked", "--json"],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(rc, 0, err.getvalue())
      payload = json.loads(out.getvalue())
      self.assertEqual(payload["action"], "REMAIN_PARKED")

      refreshed = ParkingStateStore(state_dir).load()
      self.assertIsNotNone(refreshed)
      self.assertEqual(refreshed.parked_at, "2026-11-23T09:00:00Z")
      self.assertNotEqual(refreshed.last_checked_at, "2026-11-23T09:00:00Z")
      self.assertEqual(refreshed.approvals_count, 1)
      self.assertEqual(refreshed.pending_reviewers, ("bob",))
      self.assertEqual(refreshed.cron_expression, "*/30 * * * *")

  def test_resume_parked_when_not_parked_exits_with_reflex(self) -> None:
    """Emits NOT_PARKED Vector 4 Reflex when resume-parked runs without parked state."""
    with tempfile.TemporaryDirectory() as repo:
      _seed_t70_workspace(repo)
      out, err = io.StringIO(), io.StringIO()
      rc = WorkflowGatesScript().execute(
          args=["--repo-root", repo, "resume-parked"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 2)
      self.assertIn("NOT_PARKED", err.getvalue())

  def test_resume_parked_rejects_non_t70_target_without_polling_or_mutating(
      self,
  ) -> None:
    """Rejects resume-parked outside T70 with exit 2 without polling or altering state (BLOCKER-2)."""
    with tempfile.TemporaryDirectory() as repo:
      _init_git_repo(repo)
      state_dir = os.path.join(repo, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      write_active_target(
          state_dir,
          {
              "previousTarget": "T20",
              "target": "T30",
              "targetName": "implementation-diff",
          },
      )
      stale = ParkingState(
          parked=True,
          pr_number=47,
          pr_url="https://github.com/org/repo/pull/47",
          head_branch="feat/47-parking-test",
          reason="Stale parking file",
          ci_status="SUCCESS",
          approvals_count=1,
          required_approvals=2,
      )
      store = ParkingStateStore(state_dir)
      store.save(stale)

      out, err = io.StringIO(), io.StringIO()
      with mock.patch("lib.ci_polling.poll_ci_status") as mock_poll:
        rc = WorkflowGatesScript().execute(
            args=["--repo-root", repo, "resume-parked"],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(rc, 2)
      self.assertIn("RESUME_REQUIRES_T70", err.getvalue())
      mock_poll.assert_not_called()
      self.assertEqual(read_active_target(state_dir)["target"], "T30")
      self.assertEqual(store.load(), stale)

  def test_resume_parked_preserves_state_on_transient_github_error(
      self,
  ) -> None:
    """Exits 2 without modifying 70_parking_state.json, 70_bot_triage.md, or 99_next_action.md on transient poll errors (BLOCKER-2)."""
    with tempfile.TemporaryDirectory() as repo:
      state_dir = _seed_t70_workspace(repo)
      original = ParkingState(
          parked=True,
          pr_number=47,
          pr_url="https://github.com/org/repo/pull/47",
          head_branch="feat/47-parking-test",
          reason="Awaiting peer review approvals: 1/2",
          ci_status="SUCCESS",
          review_decision="REVIEW_REQUIRED",
          approvals_count=1,
          required_approvals=2,
          pending_reviewers=("alice",),
          parked_at="2026-11-23T10:00:00Z",
          last_checked_at="2026-11-23T10:00:00Z",
      )
      store = ParkingStateStore(state_dir)
      store.save(original)
      triage_path = os.path.join(state_dir, "70_bot_triage.md")
      na_path = os.path.join(state_dir, "99_next_action.md")
      orig_triage = render_parked_triage_card(original)
      with open(triage_path, "w", encoding="utf-8") as f:
        f.write(orig_triage)
      orig_na = "# Original readerboard\n- Session Status: PARKED\n"
      with open(na_path, "w", encoding="utf-8") as f:
        f.write(orig_na)
      orig_active = read_active_target(state_dir)

      transient_err = {
          "mode": "github_error",
          "ciStatus": "ERROR",
          "quiescenceSatisfied": False,
          "readyToLand": False,
          "sessionStatus": "ERROR",
          "parkingRecommended": False,
          "message": "gh api graphql failed: network timeout",
      }
      out, err = io.StringIO(), io.StringIO()
      with mock.patch(
          "lib.ci_polling.poll_ci_status", return_value=transient_err
      ):
        rc = WorkflowGatesScript().execute(
            args=["--repo-root", repo, "resume-parked"],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(rc, 2)
      self.assertIn("PARKED_POLL_ERROR", err.getvalue())
      self.assertEqual(store.load(), original)
      with open(triage_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), orig_triage)
      with open(na_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), orig_na)
      self.assertEqual(read_active_target(state_dir), orig_active)

  def test_resume_parked_handles_merged_and_closed_pr_without_advancing_t75(
      self,
  ) -> None:
    """Clears parking without advancing to T75 when PR is already MERGED or CLOSED (NBO-2)."""
    for terminal_state in ("MERGED", "CLOSED"):
      with self.subTest(prState=terminal_state), tempfile.TemporaryDirectory() as repo:
        state_dir = _seed_t70_workspace(repo)
        ParkingStateStore(state_dir).save(
            ParkingState(
                parked=True,
                pr_number=47,
                head_branch="feat/47-parking-test",
                reason="Awaiting peer review",
                ci_status="SUCCESS",
            )
        )
        poll_res = {
            "mode": "github",
            "prState": terminal_state,
            "ciStatus": "SUCCESS",
            "quiescenceSatisfied": True,
            "readyToLand": False,
            "sessionStatus": f"PR_{terminal_state}",
            "parkingRecommended": False,
            "prUrl": "https://github.com/org/repo/pull/47",
            "headBranch": "feat/47-parking-test",
            "message": f"Pull request #47 is already {terminal_state}.",
        }
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("lib.ci_polling.poll_ci_status", return_value=poll_res):
          rc = WorkflowGatesScript().execute(
              args=["--repo-root", repo, "resume-parked", "--json"],
              stdout=out,
              stderr=err,
          )
        self.assertEqual(rc, 0, err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["action"], f"PR_{terminal_state}")
        self.assertFalse(ParkingStateStore(state_dir).exists())
        self.assertEqual(read_active_target(state_dir)["target"], "T70")

  def test_makefile_park_session_hydrates_metadata_and_escapes_shell_metachars(
      self,
  ) -> None:
    """Real GNU make park-session PR=<n> hydrates PR metadata and resists shell/Make interpolation (BLOCKER-3)."""
    with tempfile.TemporaryDirectory() as tmp:
      repo = os.path.join(tmp, "repo")
      fake_bin = os.path.join(tmp, "bin")
      os.makedirs(repo)
      os.makedirs(fake_bin)
      state_dir = _seed_t70_workspace(repo, branch="feat/47-parking-test")
      # Enable team mode + parking in workspace config
      with open(
          os.path.join(repo, ".workflow", "config.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {
                "ci": {
                    "quiescenceSeconds": 0,
                    "enablePeerReviews": True,
                    "minApprovals": 2,
                    "enableSessionParking": True,
                }
            },
            f,
        )
      # Add a fake origin remote pointing to GitHub so detect_hosting_mode succeeds
      _git(repo, "remote", "add", "origin", "https://github.com/org/repo.git")

      # Write a hermetic fake `gh` CLI in fake_bin returning GraphQL PR JSON
      graphql_payload = {
          "data": {
              "repository": {
                  "pullRequest": {
                      "number": 47,
                      "url": "https://github.com/org/repo/pull/47",
                      "state": "OPEN",
                      "isDraft": False,
                      "headRefName": "feat/47-parking-test",
                      "headRefOid": "sha_abc",
                      "baseRefName": "main",
                      "mergeable": "MERGEABLE",
                      "mergeStateStatus": "BLOCKED",
                      "reviewDecision": "REVIEW_REQUIRED",
                      "baseRef": {
                          "branchProtectionRule": {
                              "requiredApprovingReviewCount": 2,
                              "dismissesStaleReviews": False,
                              "requiresCodeOwnerReviews": True,
                          }
                      },
                      "commits": {
                          "nodes": [{
                              "commit": {
                                  "oid": "sha_abc",
                                  "statusCheckRollup": {
                                      "contexts": {
                                          "nodes": [{
                                              "__typename": "CheckRun",
                                              "name": "unit",
                                              "status": "COMPLETED",
                                              "conclusion": "SUCCESS",
                                              "completedAt": "2026-11-01T10:00:00Z",
                                          }]
                                      }
                                  },
                              }
                          }]
                      },
                      "reviewRequests": {
                          "nodes": [{
                              "requestedReviewer": {
                                  "__typename": "Team",
                                  "combinedSlug": "org/core",
                              }
                          }]
                      },
                      "latestOpinionatedReviews": {
                          "nodes": [{
                              "author": {"login": "alice"},
                              "state": "APPROVED",
                              "body": "+1",
                              "submittedAt": "2026-11-01T10:05:00Z",
                              "commit": {"oid": "sha_abc"},
                          }]
                      },
                      "reviews": {"nodes": []},
                      "reviewThreads": {
                          "nodes": [{
                              "isResolved": False,
                              "isOutdated": False,
                              "path": "lib/a.py",
                              "line": 10,
                              "comments": {
                                  "nodes": [{
                                      "author": {"login": "bob"},
                                      "body": "Nit",
                                  }]
                              },
                          }]
                      },
                      "comments": {"nodes": []},
                  }
              }
          }
      }
      fake_gh = os.path.join(fake_bin, "gh")
      with open(fake_gh, "w", encoding="utf-8") as f:
        f.write(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "if len(sys.argv) >= 3 and sys.argv[1] == 'api' and sys.argv[2] == 'graphql':\n"
            f"  print(json.dumps({graphql_payload!r}))\n"
            "  sys.exit(0)\n"
            "print('ok')\n"
            "sys.exit(0)\n"
        )
      os.chmod(fake_gh, 0o755)

      # Synthesize real .workflow/state/Makefile
      out_init, err_init = io.StringIO(), io.StringIO()
      init_rc = WorkflowGatesScript().execute(
          args=["--repo-root", repo, "init-makefile"],
          stdout=out_init,
          stderr=err_init,
      )
      self.assertEqual(init_rc, 0, err_init.getvalue())

      tricky_reason = (
          'Awaiting $review "1/2" `echo injected` $(echo bad) it\'s pending'
      )
      env = dict(os.environ)
      env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
      mk_path = os.path.join(state_dir, "Makefile")
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              mk_path,
              f"PYTHON={sys.executable}",
              "park-session",
              "PR=47",
              f"REASON={tricky_reason}",
          ],
          cwd=repo,
          env=env,
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(
          proc.returncode,
          0,
          f"make park-session failed:\nstderr={proc.stderr}\nstdout={proc.stdout}",
      )
      self.assertIn("https://github.com/org/repo/pull/47", proc.stdout)
      self.assertNotIn("injected", proc.stdout)

      loaded = ParkingStateStore(state_dir).load()
      self.assertIsNotNone(loaded)
      self.assertEqual(loaded.pr_number, 47)
      self.assertEqual(loaded.pr_url, "https://github.com/org/repo/pull/47")
      self.assertEqual(loaded.head_branch, "feat/47-parking-test")
      self.assertEqual(loaded.ci_status, "SUCCESS")
      self.assertEqual(loaded.review_decision, "REVIEW_REQUIRED")
      self.assertEqual(loaded.approvals_count, 1)
      self.assertEqual(loaded.required_approvals, 2)
      self.assertTrue(loaded.require_code_owner_reviews)
      self.assertEqual(loaded.pending_reviewers, ("org/core",))
      self.assertEqual(loaded.unresolved_threads_count, 1)
      self.assertEqual(loaded.reason, tricky_reason)

      with open(
          os.path.join(state_dir, "70_bot_triage.md"), "r", encoding="utf-8"
      ) as f:
        triage_text = f.read()
      self.assertIn("https://github.com/org/repo/pull/47", triage_text)
      self.assertIn("Approvals: 1/2", triage_text)
      self.assertIn("CI Status: `SUCCESS`", triage_text)

  def test_target_90_finish_cleans_up_parking_state_json(self) -> None:
    """Verifies Target 90 finish automatically unlinks or archives 70_parking_state.json."""
    for action in ("unlink", "archive"):
      with self.subTest(completedCardAction=action), tempfile.TemporaryDirectory() as repo:
        state_dir = _seed_t70_workspace(repo)
        with open(
            os.path.join(repo, ".workflow", "config.json"),
            "w",
            encoding="utf-8",
        ) as f:
          json.dump({"state": {"completedCardAction": action}}, f)
        ParkingStateStore(state_dir).save(
            ParkingState(
                parked=True,
                pr_number=47,
                reason="Stale parked state",
            )
        )
        write_active_target(
            state_dir,
            {
                "previousTarget": "T80",
                "target": "T90",
                "targetName": "codified-scars-consolidated",
            },
        )
        ok, msg = advance_workflow_target(
            "T90",
            "T90",
            evidence={"complete": True},
            workspace_dir=repo,
            state_dir=state_dir,
        )
        self.assertTrue(ok, msg)
        self.assertFalse(ParkingStateStore(state_dir).exists())
        archived = os.path.join(
            repo, ".workflow", "archive", "GH-47", PARKING_STATE_FILENAME
        )
        if action == "archive":
          self.assertTrue(os.path.isfile(archived))
        else:
          self.assertFalse(os.path.exists(archived))


if __name__ == "__main__":
  unittest.main()
