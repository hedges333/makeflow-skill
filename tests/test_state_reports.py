#!/usr/bin/env python3
"""Hermetic unit and boundary tests for canonical JSON state and reports (GH-281).

Verifies:
1. Leaf `lib/active_state.py` (`ActiveTargetState`, atomic `ActiveStateStore`,
   status predicates, and `list_state_card_files`).
2. Pure `lib/report_renderers.py` Markdown renderers and `SCAR-LOCAL-DATA-0001`
   fenced code block preservation/safety.
3. Extensible `lib/state_reports.py` (`ReportSpec`, `ReportRegistry`,
   `StateReportStore`, strict schema/contradiction validation, zero-partial-write
   guarantees, and GH-270 periphery registration).
4. JSON-first consumers (`lib/config.py`, `lib/target90_guard.py`,
   `lib/pre_tool_guard.py`, `lib/workspace_mutex.py`, `lib/commit_msg.py`,
   `lib/review_promotion.py`, `lib/state_machine.py`) and `mcp/server.py`
   `workflow_submit_report` tool with path containment and child subagent
   airlock pass-through.
"""

from dataclasses import FrozenInstanceError
import errno
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)


def _valid_archaeology_payload(hosting_mode: str = "GITHUB") -> dict:
  """Returns a valid canonical Target 15 archaeology report payload."""
  return {
      "ticket_id": "GH-281",
      "hosting_mode": hosting_mode,
      "subprojects": ["."],
      "belnap_facts": [
          {
              "claim": "lib/ticket_state.py stores 10_ticket.json.",
              "truth_state": "ESTABLISHED",
              "evidence": "lib/ticket_state.py:13",
          }
      ],
      "testing_strategy": {
          "failure_mechanism": "Lossy Markdown prose scraping.",
          "burden_balance": "Hermetic unittest suites run in under 100ms.",
          "scenarios": [
              "Valid JSON report writes pretty .json and rendered .md atomically."
          ],
      },
      "consulted_references": [
          "skills/makeflow/references/02_archaeology_and_planning.md",
          "skills/makeflow/references/14_design_patterns_and_architecture.md",
      ],
      "applicable_scars": [
          {
              "id": "SCAR-ARCH-10",
              "probe": "Does any module scrape Markdown prose for structured JSON fields?",
          }
      ],
      "notes": "Repository uses Python 3 stdlib only.",
  }


def _valid_tdd_report_payload() -> dict:
  """Returns a valid canonical Target 30 TDD report payload."""
  return {
      "ticket_id": "GH-281",
      "changed_files": ["lib/active_state.py", "lib/state_reports.py"],
      "test_commands_run": [
          {
              "command": "python3 -m unittest -v -f tests/test_state_reports.py",
              "exit_code": 1,
              "phase": "red",
          },
          {
              "command": "python3 -m unittest -v -f tests/test_state_reports.py",
              "exit_code": 0,
              "phase": "green",
          },
      ],
      "applied_scars": ["SCAR-ARCH-10", "SCAR-TOOL-13"],
      "summary": "Implemented canonical JSON state stores and atomic report submission.",
  }


def _valid_critic_review_payload(
    *,
    round_num: int = 1,
    verdict: str = "APPROVED",
    blockers: list = None,
    pillar_extra: str = "",
) -> dict:
  """Returns a valid canonical Target 40 critic review payload."""
  if blockers is None:
    if verdict == "REJECTED":
      blockers = [
          {
              "id": "BLOCKER-1",
              "pillar": "Architecture",
              "location": "lib/state_reports.py:42",
              "wound": "Missing schema validation.",
              "trap": "Accepted unvalidated dict.",
              "diagnostic_invariant": "Reject malformed payloads before disk I/O.",
          }
      ]
    else:
      blockers = []
  return {
      "round": round_num,
      "verdict": verdict,
      "pillars": {
          "intent": "The diff resolves GH-281 cleanly within planned boundaries.",
          "verification": (
              "Red tests failed first and green hermetic suites pass cleanly."
              + (f"\n\n{pillar_extra}" if pillar_extra else "")
          ),
          "architecture": "Cohesive leaf modules enforce acyclic one-way imports.",
          "blast_radius": "Existing caller signatures and GH-47 parking stay intact.",
          "reviewer_defense": "Docs satisfy Simple English and byte ceilings.",
          "immune_defenses": "Atomic writes and specific exception handlers hold.",
      },
      "pillar_summaries": {
          "intent": "Scoped cleanly to GH-281.",
          "verification": "Hermetic tests cover positive and negative paths.",
          "architecture": "Leaf modules keep dependencies acyclic.",
          "blast_radius": "Zero regression across existing suites.",
          "reviewer_defense": "Docs and adapters match contracts.",
          "immune_defenses": "Atomic temp-file replacement avoids partial writes.",
      },
      "blockers": blockers,
      "non_blocking_observations": [
          {
              "id": "NBO-1",
              "pillar": "Architecture",
              "location": "lib/report_renderers.py:12",
              "relationship": "Somewhat related",
              "disposition": "Skip",
              "summary": "Renderer layout is already concise.",
          }
      ],
  }


def _valid_bot_triage_payload(
    *,
    hosting_mode: str = "GITHUB",
    pr_number: int = 281,
    ci_status: str = "PASSING",
    quiescence_satisfied: bool = True,
    findings: list = None,
    ready_to_land: bool = True,
) -> dict:
  """Returns a valid canonical Target 70 bot triage payload."""
  return {
      "hosting_mode": hosting_mode,
      "pr_number": pr_number,
      "ci_status": ci_status,
      "quiescence_satisfied": quiescence_satisfied,
      "checks": [
          {
              "name": "unit-tests",
              "status": "PASS",
              "details": "All 40 suites passed.",
          }
      ],
      "findings": [] if findings is None else findings,
      "ready_to_land": ready_to_land,
  }


class TestNewModulesStructure(unittest.TestCase):
  """Verifies SCAR-ARCH-05 zero-shebang and module structure invariants."""

  def test_new_lib_modules_exist_without_shebangs(self) -> None:
    """New library modules under lib/ must exist and omit #! shebang headers."""
    for rel in (
        "lib/active_state.py",
        "lib/report_renderers.py",
        "lib/state_reports.py",
    ):
      with self.subTest(module=rel):
        path = os.path.join(REPO_ROOT, rel)
        self.assertTrue(os.path.isfile(path), f"Missing expected module {rel}")
        with open(path, "r", encoding="utf-8") as f:
          first_line = f.readline()
        self.assertFalse(
            first_line.startswith("#!"),
            f"Library module {rel} must not contain a shebang header.",
        )


class TestActiveStateModule(unittest.TestCase):
  """Unit tests for lib/active_state.py value object, store, and card listing."""

  def test_active_target_state_is_frozen_and_preserves_camel_case_keys(
      self,
  ) -> None:
    """ActiveTargetState is immutable and serializes exact GH-47 camelCase keys."""
    from lib.active_state import ActiveTargetState

    state = ActiveTargetState(
        target="T70",
        target_name="bot-quiescence",
        previous_target="T60",
        status="PARKED",
        complete=False,
        ticket_id="GH-281",
        branch="feat/281-json-state-subagent-reports",
        skipped_targets=("T60",),
        parked=True,
        parking_reason="PARKED (Awaiting peer review approvals: 1/2)",
    )
    with self.assertRaises(FrozenInstanceError):
      state.target = "T75"  # type: ignore[misc]

    serialized = state.to_dict()
    self.assertEqual(serialized["target"], "T70")
    self.assertEqual(serialized["targetName"], "bot-quiescence")
    self.assertEqual(serialized["previousTarget"], "T60")
    self.assertEqual(serialized["status"], "PARKED")
    self.assertIs(serialized["complete"], False)
    self.assertEqual(serialized["ticketId"], "GH-281")
    self.assertEqual(serialized["branch"], "feat/281-json-state-subagent-reports")
    self.assertEqual(serialized["skippedTargets"], ["T60"])
    self.assertIs(serialized["parked"], True)
    self.assertEqual(
        serialized["parkingReason"],
        "PARKED (Awaiting peer review approvals: 1/2)",
    )

    round_tripped = ActiveTargetState.from_dict(serialized)
    self.assertEqual(round_tripped, state)

    with self.assertRaises(TypeError):
      ActiveTargetState.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]

  def test_status_predicates_across_lifecycle_states(self) -> None:
    """Verifies is_active, is_complete, is_target_90, and is_post_landing."""
    from lib.active_state import ActiveTargetState

    active_t30 = ActiveTargetState.from_dict({
        "target": "T30",
        "targetName": "implementation-diff",
        "previousTarget": "T20",
        "status": "ACTIVE",
        "complete": False,
    })
    self.assertTrue(active_t30.is_active())
    self.assertFalse(active_t30.is_complete())
    self.assertFalse(active_t30.is_target_90())
    self.assertFalse(active_t30.is_post_landing())
    self.assertTrue(active_t30.has_explicit_status())

    active_t80 = ActiveTargetState.from_dict({
        "target": "T80",
        "status": "ACTIVE",
        "complete": False,
    })
    self.assertTrue(active_t80.is_active())
    self.assertFalse(active_t80.is_target_90())
    self.assertTrue(active_t80.is_post_landing())

    active_t90 = ActiveTargetState.from_dict({
        "target": "T90",
        "status": "ACTIVE",
        "complete": False,
    })
    self.assertTrue(active_t90.is_active())
    self.assertFalse(active_t90.is_complete())
    self.assertTrue(active_t90.is_target_90())
    self.assertTrue(active_t90.is_post_landing())

    finished = ActiveTargetState.from_dict({
        "target": "T90",
        "status": "COMPLETE",
        "complete": True,
    })
    self.assertFalse(finished.is_active())
    self.assertTrue(finished.is_complete())
    self.assertFalse(finished.is_target_90())
    self.assertTrue(finished.is_post_landing())

    legacy = ActiveTargetState.from_dict({
        "target": "T90",
        "previousTarget": "T80",
    })
    self.assertFalse(legacy.has_explicit_status())
    self.assertTrue(legacy.is_target_90())
    self.assertFalse(legacy.is_complete())

  def test_active_state_store_atomic_pretty_write_and_corrupt_load(self) -> None:
    """ActiveStateStore writes pretty indent=2 JSON atomically and ignores corrupt files."""
    from lib.active_state import ActiveStateStore, ActiveTargetState

    with tempfile.TemporaryDirectory() as tmp:
      store = ActiveStateStore.from_repo_root(tmp)
      self.assertFalse(store.exists())
      self.assertIsNone(store.load())

      state = ActiveTargetState(
          target="T40",
          target_name="adversarial-review",
          previous_target="T30",
          status="ACTIVE",
          complete=False,
          ticket_id="GH-281",
          branch="feat/281-json-state",
      )
      written = store.save(state)
      self.assertEqual(written, store.path)
      self.assertTrue(store.exists())

      with open(written, "r", encoding="utf-8") as f:
        raw_text = f.read()
      self.assertTrue(raw_text.endswith("\n"))
      self.assertIn('\n  "target": "T40"', raw_text)
      self.assertEqual(store.load(), state)
      # Zero leftover temp files in state_dir
      leftovers = [
          n for n in os.listdir(store.state_dir) if n.startswith(".tmp_")
      ]
      self.assertEqual(leftovers, [])

      for bad_bytes in (b"{broken", b"[]", b"\xff\xfe"):
        with self.subTest(payload=bad_bytes):
          with open(store.path, "wb") as f:
            f.write(bad_bytes)
          self.assertIsNone(store.load())

  def test_list_state_card_files_includes_report_jsons_and_excludes_internals(
      self,
  ) -> None:
    """list_state_card_files counts canonical JSON and Markdown cards while skipping locks."""
    from lib.active_state import list_state_card_files

    with tempfile.TemporaryDirectory() as state_dir:
      for name in (
          "10_ticket.json",
          "15_archaeology_receipt.json",
          "15_archaeology_receipt.md",
          "20_plan.md",
          "30_tdd_report.json",
          "40_critic_review_round1.json",
          "40_critic_review.json",
          "70_bot_triage.json",
          "99_next_action.md",
          # Excluded runtime locks, receipts, and parking state:
          "active_target.json",
          "gate_entry.json",
          "active_subagent.json",
          "loaded_scars.json",
          "30_tdd_receipt.json",
          "30_regression_receipt.json",
          "70_parking_state.json",
          "50_commit_msg.txt",
          "approved_tree.txt",
          "Makefile",
      ):
        with open(os.path.join(state_dir, name), "w", encoding="utf-8") as f:
          f.write("{}\n" if name.endswith(".json") else "ok\n")

      cards = list_state_card_files(state_dir)
      self.assertEqual(
          cards,
          [
              "10_ticket.json",
              "15_archaeology_receipt.json",
              "15_archaeology_receipt.md",
              "20_plan.md",
              "30_tdd_report.json",
              "40_critic_review.json",
              "40_critic_review_round1.json",
              "70_bot_triage.json",
              "99_next_action.md",
          ],
      )

  def test_active_target_state_to_dict_normalizes_lifecycle_fields_consistently(
      self,
  ) -> None:
    """NBO-1: Direct ActiveTargetState construction normalizes status/complete/parked identically to from_dict."""
    from lib.active_state import ActiveTargetState

    parked_manual = ActiveTargetState(
        target="T70", status="PARKED", parked=False
    )
    parked_dict = parked_manual.to_dict()
    self.assertEqual(parked_dict["status"], "PARKED")
    self.assertIs(parked_dict["complete"], False)
    self.assertIs(parked_dict["parked"], True)
    self.assertNotIn("parkingReason", parked_dict)
    self.assertEqual(
        ActiveTargetState.from_dict(parked_dict).to_dict(), parked_dict
    )

    parked_with_reason = ActiveTargetState(
        target="T70",
        status="PARKED",
        parked=False,
        parking_reason="Awaiting review",
    ).to_dict()
    self.assertEqual(parked_with_reason["status"], "PARKED")
    self.assertIs(parked_with_reason["parked"], True)
    self.assertEqual(parked_with_reason["parkingReason"], "Awaiting review")

    complete_stale = ActiveTargetState(
        target="T90",
        status="COMPLETE",
        parked=True,
        parking_reason="stale",
    ).to_dict()
    self.assertEqual(complete_stale["status"], "COMPLETE")
    self.assertIs(complete_stale["complete"], True)
    self.assertNotIn("parked", complete_stale)
    self.assertNotIn("parkingReason", complete_stale)

    complete_flag_only = ActiveTargetState(
        target="T90",
        complete=True,
        parked=True,
        parking_reason="stale",
    ).to_dict()
    self.assertEqual(complete_flag_only["status"], "COMPLETE")
    self.assertIs(complete_flag_only["complete"], True)
    self.assertNotIn("parked", complete_flag_only)
    self.assertNotIn("parkingReason", complete_flag_only)

    parked_flag_only = ActiveTargetState(
        target="T70", parked=True, parking_reason="CI pending"
    ).to_dict()
    self.assertEqual(parked_flag_only["status"], "PARKED")
    self.assertIs(parked_flag_only["complete"], False)
    self.assertIs(parked_flag_only["parked"], True)
    self.assertEqual(parked_flag_only["parkingReason"], "CI pending")

    active_overrides_parked = ActiveTargetState(
        target="T70",
        status="ACTIVE",
        parked=True,
        parking_reason="stale",
    ).to_dict()
    self.assertEqual(active_overrides_parked["status"], "ACTIVE")
    self.assertIs(active_overrides_parked["complete"], False)
    self.assertNotIn("parked", active_overrides_parked)
    self.assertNotIn("parkingReason", active_overrides_parked)


class TestReportRenderersAndStateReports(unittest.TestCase):
  """Unit tests for lib/report_renderers.py and lib/state_reports.py."""

  def test_rendered_critic_markdown_passes_review_validator_with_fenced_blocks(
      self,
  ) -> None:
    """Rendered critic review Markdown passes validate_critic_review_text even with embedded fences (SCAR-LOCAL-DATA-0001)."""
    from lib.report_renderers import render_critic_review_markdown
    from lib.review_promotion import validate_critic_review_text

    embedded_fence = (
        "Verified round-trip snippet:\n"
        "```markdown\n"
        "---\n"
        "## Embedded Heading\n"
        "### 1. Intent\n"
        "VERDICT: REJECTED (9 BLOCKERS)\n"
        "    indented_code()\n"
        "\n"
        "    preserved_blank_line()\n"
        "```"
    )
    approved_md = render_critic_review_markdown(
        _valid_critic_review_payload(
            round_num=1, verdict="APPROVED", pillar_extra=embedded_fence
        )
    )
    val_ok = validate_critic_review_text(approved_md)
    self.assertTrue(val_ok.valid, val_ok.errors)
    self.assertEqual(val_ok.verdict, "APPROVED")
    self.assertEqual(val_ok.blocker_count, 0)
    self.assertIn("    indented_code()\n\n    preserved_blank_line()", approved_md)

    # Unclosed fence inside pillar prose must be closed cleanly so subsequent
    # headings and VERDICT are never swallowed (SCAR-LOCAL-DATA-0001).
    unclosed_fence = "Quoted output:\n```text\n---\n## Fake heading"
    recovered_md = render_critic_review_markdown(
        _valid_critic_review_payload(
            round_num=2, verdict="REJECTED", pillar_extra=unclosed_fence
        )
    )
    val_rej = validate_critic_review_text(recovered_md)
    self.assertTrue(val_rej.valid, val_rej.errors)
    self.assertEqual(val_rej.verdict, "REJECTED")
    self.assertEqual(val_rej.blocker_count, 1)

  def test_submit_all_four_report_types_writes_pretty_json_and_rendered_md(
      self,
  ) -> None:
    """StateReportStore validates, writes indent=2 JSON with trailing newline, and renders .md."""
    from lib.commit_msg import parse_receipt_hosting_mode
    from lib.state_reports import (
        StateReportStore,
        load_archaeology_hosting_mode,
        load_critic_review,
    )

    with tempfile.TemporaryDirectory() as tmp:
      store = StateReportStore.from_repo_root(tmp)

      cases = (
          (
              "archaeology",
              _valid_archaeology_payload("PURE_GIT"),
              "15_archaeology_receipt.json",
              "15_archaeology_receipt.md",
          ),
          (
              "tdd_report",
              _valid_tdd_report_payload(),
              "30_tdd_report.json",
              "30_tdd_report.md",
          ),
          (
              "critic_review",
              _valid_critic_review_payload(round_num=2, verdict="APPROVED"),
              "40_critic_review_round2.json",
              "40_critic_review_round2.md",
          ),
          (
              "bot_triage",
              _valid_bot_triage_payload(),
              "70_bot_triage.json",
              "70_bot_triage.md",
          ),
      )

      for rtype, payload, expected_json, expected_md in cases:
        with self.subTest(report_type=rtype):
          res = store.submit(rtype, payload)
          self.assertTrue(res.ok, res.errors)
          self.assertEqual(os.path.basename(res.json_path), expected_json)
          self.assertEqual(os.path.basename(res.md_path), expected_md)
          self.assertTrue(os.path.isfile(res.json_path))
          self.assertTrue(os.path.isfile(res.md_path))

          with open(res.json_path, "r", encoding="utf-8") as jf:
            raw_json = jf.read()
          self.assertTrue(raw_json.endswith("\n"))
          self.assertIn("\n  ", raw_json)
          parsed = json.loads(raw_json)
          self.assertIsInstance(parsed, dict)

          with open(res.md_path, "r", encoding="utf-8") as mf:
            md_text = mf.read()
          self.assertTrue(md_text.strip())
          self.assertTrue(md_text.endswith("\n"))

      self.assertEqual(
          load_archaeology_hosting_mode(store.state_dir), "PURE_GIT"
      )
      with open(
          os.path.join(store.state_dir, "15_archaeology_receipt.md"),
          "r",
          encoding="utf-8",
      ) as f:
        self.assertEqual(parse_receipt_hosting_mode(f.read()), "PURE_GIT")

      loaded_critic = load_critic_review(store.state_dir, round_number=2)
      self.assertIsNotNone(loaded_critic)
      self.assertEqual(loaded_critic["verdict"], "APPROVED")
      self.assertEqual(loaded_critic["round"], 2)

      leftovers = [
          n for n in os.listdir(store.state_dir) if n.startswith(".tmp_")
      ]
      self.assertEqual(leftovers, [])

  def test_rejects_invalid_and_contradictory_payloads_without_touching_disk(
      self,
  ) -> None:
    """Schema violations and contradictions fail cleanly with zero partial writes."""
    from lib.state_reports import StateReportStore

    bad_cases = (
        ("unknown_type", "nonexistent", {"a": 1}),
        (
            "archaeology_bad_mode",
            "archaeology",
            {**_valid_archaeology_payload(), "hosting_mode": "BITBUCKET"},
        ),
        (
            "archaeology_bad_belnap",
            "archaeology",
            {
                **_valid_archaeology_payload(),
                "belnap_facts": [
                    {
                        "claim": "x",
                        "truth_state": "MAYBE",
                        "evidence": "lib/a.py:1",
                    }
                ],
            },
        ),
        (
            "tdd_bad_phase",
            "tdd_report",
            {
                **_valid_tdd_report_payload(),
                "test_commands_run": [
                    {"command": "pytest", "exit_code": 0, "phase": "deploy"}
                ],
            },
        ),
        (
            "tdd_bool_exit_code",
            "tdd_report",
            {
                **_valid_tdd_report_payload(),
                "test_commands_run": [
                    {"command": "pytest", "exit_code": True, "phase": "green"}
                ],
            },
        ),
        (
            "critic_approved_with_blocker",
            "critic_review",
            _valid_critic_review_payload(
                verdict="APPROVED",
                blockers=[
                    {
                        "id": "BLOCKER-1",
                        "pillar": "Intent",
                        "location": "lib/a.py:1",
                        "wound": "w",
                        "trap": "t",
                        "diagnostic_invariant": "d",
                    }
                ],
            ),
        ),
        (
            "critic_rejected_zero_blockers",
            "critic_review",
            _valid_critic_review_payload(verdict="REJECTED", blockers=[]),
        ),
        (
            "critic_soft_verdict",
            "critic_review",
            {**_valid_critic_review_payload(), "verdict": "REQUEST_CHANGES"},
        ),
        (
            "critic_missing_pillar",
            "critic_review",
            {
                **_valid_critic_review_payload(),
                "pillars": {"intent": "Only one pillar provided."},
            },
        ),
        (
            "critic_fence_only_pillar",
            "critic_review",
            {
                **_valid_critic_review_payload(),
                "pillars": {
                    **_valid_critic_review_payload()["pillars"],
                    "architecture": "```python\nx = 1\n```",
                },
            },
        ),
        (
            "critic_zero_round",
            "critic_review",
            {**_valid_critic_review_payload(), "round": 0},
        ),
        (
            "triage_ready_while_failing",
            "bot_triage",
            _valid_bot_triage_payload(
                ci_status="FAILING", quiescence_satisfied=True, ready_to_land=True
            ),
        ),
        (
            "triage_ready_without_quiescence",
            "bot_triage",
            _valid_bot_triage_payload(
                ci_status="PASSING",
                quiescence_satisfied=False,
                ready_to_land=True,
            ),
        ),
        (
            "triage_ready_with_unresolved_accept",
            "bot_triage",
            _valid_bot_triage_payload(
                ci_status="PASSING",
                quiescence_satisfied=True,
                ready_to_land=True,
                findings=[
                    {
                        "source": "bot",
                        "location": "lib/x.py:10",
                        "summary": "Null deref",
                        "action": "Accept",
                        "rationale": "Real bug",
                        "resolved": False,
                    }
                ],
            ),
        ),
    )

    for label, rtype, payload in bad_cases:
      with self.subTest(case=label):
        with tempfile.TemporaryDirectory() as tmp:
          state_dir = os.path.join(tmp, ".workflow", "state")
          store = StateReportStore(state_dir)
          res = store.submit(rtype, payload)
          self.assertFalse(res.ok, f"Expected failure for {label}")
          self.assertTrue(res.errors)
          self.assertFalse(
              os.path.exists(state_dir),
              f"Invalid payload {label} must not create or mutate disk files.",
          )

  def test_report_registry_supports_gh270_plan_extension_at_periphery(
      self,
  ) -> None:
    """SCAR-API-0006: GH-270 can register a 'plan' ReportSpec without editing StateReportStore."""
    from lib.state_reports import (
        ReportSpec,
        ReportValidationResult,
        StateReportStore,
        create_default_registry,
    )

    def _validate_plan(payload: dict) -> ReportValidationResult:
      if not isinstance(payload.get("ticket_id"), str) or not payload["ticket_id"].strip():
        return ReportValidationResult(valid=False, errors=("Missing ticket_id.",))
      return ReportValidationResult(valid=True, normalized_payload=dict(payload))

    def _render_plan(payload: dict) -> str:
      return f"# Plan for {payload['ticket_id']}\n"

    registry = create_default_registry()
    registry.register(
        ReportSpec(
            report_type="plan",
            json_filename_template="20_plan.json",
            md_filename_template="20_plan.md",
            validator=_validate_plan,
            renderer=_render_plan,
        )
    )
    self.assertIn("plan", registry.supported_types())

    with tempfile.TemporaryDirectory() as tmp:
      store = StateReportStore.from_repo_root(tmp, registry=registry)
      res = store.submit("plan", {"ticket_id": "GH-270"})
      self.assertTrue(res.ok, res.errors)
      self.assertEqual(os.path.basename(res.json_path), "20_plan.json")
      self.assertEqual(os.path.basename(res.md_path), "20_plan.md")


class TestJsonFirstConsumersAndMcpBoundary(unittest.TestCase):
  """Boundary tests for MCP workflow_submit_report and JSON-first state consumers."""

  def test_mcp_workflow_submit_report_and_path_containment(self) -> None:
    """MCP lists workflow_submit_report, writes valid reports, and blocks path traversal."""
    from mcp.server import handle_tools_call, handle_tools_list

    tools = handle_tools_list(1)["result"]["tools"]
    tool_map = {t["name"]: t for t in tools}
    self.assertIn("workflow_submit_report", tool_map)
    self.assertEqual(len(tool_map), 9)
    enum_vals = tool_map["workflow_submit_report"]["inputSchema"]["properties"][
        "reportType"
    ]["enum"]
    self.assertEqual(
        set(enum_vals),
        {"archaeology", "tdd_report", "critic_review", "bot_triage"},
    )

    with tempfile.TemporaryDirectory() as tmp:
      ws = os.path.join(tmp, "repo")
      outside = os.path.join(tmp, "outside")
      os.makedirs(ws)
      os.makedirs(outside)

      ok_resp = handle_tools_call(
          2,
          "workflow_submit_report",
          {
              "workspaceDir": ws,
              "reportType": "archaeology",
              "payload": _valid_archaeology_payload("GITHUB"),
          },
      )
      self.assertFalse(ok_resp["result"].get("isError", False), ok_resp)
      body = json.loads(ok_resp["result"]["content"][0]["text"])
      self.assertTrue(body["ok"])
      self.assertTrue(os.path.isfile(body["jsonPath"]))
      self.assertTrue(os.path.isfile(body["mdPath"]))

      bad_resp = handle_tools_call(
          3,
          "workflow_submit_report",
          {
              "workspaceDir": ws,
              "reportType": "critic_review",
              "payload": _valid_critic_review_payload(
                  verdict="REJECTED", blockers=[]
              ),
          },
      )
      self.assertTrue(bad_resp["result"].get("isError", False))
      self.assertFalse(
          os.path.exists(
              os.path.join(ws, ".workflow", "state", "40_critic_review_round1.json")
          )
      )

      trav_resp = handle_tools_call(
          4,
          "workflow_submit_report",
          {
              "workspaceDir": ws,
              "stateDir": "../outside",
              "reportType": "tdd_report",
              "payload": _valid_tdd_report_payload(),
          },
      )
      self.assertTrue(trav_resp["result"].get("isError", False))
      self.assertEqual(os.listdir(outside), [])

      link_dir = os.path.join(ws, ".workflow", "escape_link")
      os.makedirs(os.path.dirname(link_dir), exist_ok=True)
      os.symlink(outside, link_dir)
      sym_resp = handle_tools_call(
          5,
          "workflow_submit_report",
          {
              "workspaceDir": ws,
              "stateDir": ".workflow/escape_link",
              "reportType": "tdd_report",
              "payload": _valid_tdd_report_payload(),
          },
      )
      self.assertTrue(sym_resp["result"].get("isError", False))
      self.assertEqual(os.listdir(outside), [])

  def test_child_subagent_mcp_submit_report_passes_airlock(self) -> None:
    """Child subagents calling workflow_submit_report pass the PreToolUse airlock."""
    from lib.pre_tool_guard import evaluate_tool_call

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      with open(
          os.path.join(state_dir, "active_subagent.json"),
          "w",
          encoding="utf-8",
      ) as f:
        json.dump(
            {
                "active": True,
                "agent": "adversarial-critic",
                "supervisorConversationId": "sup-conv-1",
            },
            f,
        )

      child_res = evaluate_tool_call({
          "conversationId": "child-conv-2",
          "workspacePaths": [ws],
          "toolCall": {
              "name": "call_mcp_tool",
              "args": {
                  "ServerName": "makeflow_makeflow",
                  "ToolName": "workflow_submit_report",
                  "Arguments": {
                      "workspaceDir": ws,
                      "reportType": "critic_review",
                      "payload": _valid_critic_review_payload(),
                  },
              },
          },
      })
      self.assertEqual(child_res["decision"], "allow", child_res)

  def test_poisoned_next_action_md_cannot_override_authoritative_active_target_json(
      self,
  ) -> None:
    """Authoritative active_target.json defeats poisoned 99_next_action.md across all consumers."""
    from lib.active_state import ActiveStateStore, ActiveTargetState
    from lib.config import is_ticket_active
    from lib.target90_guard import is_target_90_state_dir
    from lib.ticket_state import TicketStateStore
    from lib.workspace_mutex import WorkspaceMutex

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      TicketStateStore(state_dir).save({"ticket_id": "GH-281", "title": "JSON"})
      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T30",
              target_name="implementation-diff",
              previous_target="T20",
              status="ACTIVE",
              complete=False,
              ticket_id="GH-281",
          )
      )
      # Poison 99_next_action.md with COMPLETE and Target 90 lines
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "# Target 90 Finished: COMPLETE\n"
            "- Status: COMPLETE (make finish satisfied)\n"
            "- Active Target: Target 90\n"
        )

      self.assertTrue(is_ticket_active(state_dir))
      self.assertFalse(is_target_90_state_dir(state_dir))
      self.assertTrue(
          WorkspaceMutex(ws).is_session_active(
              {"pid": None, "ticket_id": "GH-281", "workspace_path": ws}
          )
      )

      # Now write COMPLETE active_target.json while 99_next_action.md claims active T30
      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T90",
              target_name="finish",
              previous_target="T80",
              status="COMPLETE",
              complete=True,
              ticket_id="GH-281",
          )
      )
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Active\n- Active Target: Target 30\n")

      self.assertFalse(is_ticket_active(state_dir))
      self.assertFalse(is_target_90_state_dir(state_dir))
      self.assertFalse(
          WorkspaceMutex(ws).is_session_active(
              {"pid": None, "ticket_id": "GH-281", "workspace_path": ws}
          )
      )

  def test_promote_review_prefers_canonical_json_and_renders_markdown(
      self,
  ) -> None:
    """promote_review_round validates 40_critic_review_round<N>.json first and copies via safe_copy_file."""
    from lib.review_promotion import promote_review_round, validate_critic_review_text
    from lib.state_reports import StateReportStore

    with tempfile.TemporaryDirectory() as ws:
      store = StateReportStore.from_repo_root(ws)
      sub = store.submit(
          "critic_review",
          _valid_critic_review_payload(round_num=1, verdict="APPROVED"),
      )
      self.assertTrue(sub.ok, sub.errors)

      eperm = PermissionError(errno.EPERM, "Operation not permitted")
      with (
          mock.patch("os.chmod", side_effect=eperm),
          mock.patch("os.utime", side_effect=eperm),
      ):
        outcome = promote_review_round(ws, "1")

      self.assertTrue(outcome.promoted)
      self.assertEqual(outcome.validation.verdict, "APPROVED")
      self.assertEqual(outcome.validation.blocker_count, 0)
      canon_json = os.path.join(ws, ".workflow", "state", "40_critic_review.json")
      canon_md = os.path.join(ws, ".workflow", "state", "40_critic_review.md")
      self.assertTrue(os.path.isfile(canon_json))
      self.assertTrue(os.path.isfile(canon_md))
      with open(canon_md, "r", encoding="utf-8") as f:
        self.assertTrue(validate_critic_review_text(f.read()).valid)

      # Corrupt round 2 JSON must fail validation without clobbering round 1 canonical files
      bad_r2 = os.path.join(
          ws, ".workflow", "state", "40_critic_review_round2.json"
      )
      with open(bad_r2, "w", encoding="utf-8") as f:
        json.dump(
            _valid_critic_review_payload(
                round_num=2, verdict="REJECTED", blockers=[]
            ),
            f,
        )
      bad_outcome = promote_review_round(ws, "2")
      self.assertFalse(bad_outcome.promoted)
      self.assertFalse(bad_outcome.validation.valid)
      with open(canon_json, "r", encoding="utf-8") as f:
        self.assertEqual(json.load(f)["round"], 1)

  def test_state_machine_finish_persists_complete_and_t40_enforces_json_blocker_parity(
      self,
  ) -> None:
    """finish persists COMPLETE active_target.json and T40->T42/T45 enforces 40_critic_review.json parity."""
    from lib.state_machine import (
        advance_workflow_target,
        read_active_target,
    )
    from lib.state_reports import StateReportStore
    from lib.review_promotion import promote_review_round

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      store = StateReportStore(state_dir)

      # Submit REJECTED (1 blocker) critic review and promote
      res = store.submit(
          "critic_review",
          _valid_critic_review_payload(round_num=1, verdict="REJECTED"),
      )
      self.assertTrue(res.ok, res.errors)
      self.assertTrue(promote_review_round(ws, "1").promoted)

      # Claiming 0 blockers on T40 -> T45 must be blocked by canonical 40_critic_review.json
      ok_spoof, msg_spoof = advance_workflow_target(
          "T40",
          "T45",
          evidence={"blockersCount": 0, "skipMainFetch": True},
          workspace_dir=ws,
          state_dir=state_dir,
      )
      self.assertFalse(ok_spoof, msg_spoof)
      self.assertIn("40_critic_review.json", msg_spoof)

      # Routing to T42 with matching blockersCount=1 succeeds
      ok_t42, msg_t42 = advance_workflow_target(
          "T40",
          "T42",
          evidence={"blockersCount": 1, "skipMainFetch": True},
          workspace_dir=ws,
          state_dir=state_dir,
      )
      self.assertTrue(ok_t42, msg_t42)

      # Finish persists active_target.json with status=COMPLETE, complete=True, target=T90
      ok_fin, msg_fin = advance_workflow_target(
          "T80",
          "T90",
          evidence={"complete": True, "skipMainFetch": True},
          workspace_dir=ws,
          state_dir=state_dir,
      )
      self.assertTrue(ok_fin, msg_fin)
      active = read_active_target(state_dir)
      self.assertIsNotNone(active)
      self.assertEqual(active["target"], "T90")
      self.assertEqual(active["status"], "COMPLETE")
      self.assertIs(active["complete"], True)

  def test_mixed_mode_md_promotion_clears_stale_canonical_json(self) -> None:
    """BLOCKER-1: MD-only round promotion unlinks stale 40_critic_review.json across both transitions."""
    from lib.report_renderers import render_critic_review_markdown
    from lib.review_promotion import promote_review_round
    from lib.state_machine import advance_workflow_target
    from lib.state_reports import StateReportStore

    # Case A: JSON Round 1 REJECTED -> MD-only Round 2 APPROVED
    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      store = StateReportStore(state_dir)
      self.assertTrue(
          store.submit(
              "critic_review",
              _valid_critic_review_payload(round_num=1, verdict="REJECTED"),
          ).ok
      )
      self.assertTrue(promote_review_round(ws, "1").promoted)
      canon_json = os.path.join(state_dir, "40_critic_review.json")
      self.assertTrue(os.path.isfile(canon_json))

      r2_md = os.path.join(state_dir, "40_critic_review_round2.md")
      with open(r2_md, "w", encoding="utf-8") as f:
        f.write(
            render_critic_review_markdown(
                _valid_critic_review_payload(round_num=2, verdict="APPROVED")
            )
        )
      outcome = promote_review_round(ws, "2")
      self.assertTrue(outcome.promoted)
      self.assertFalse(os.path.exists(canon_json))
      ok_t45, msg_t45 = advance_workflow_target(
          "T40",
          "T45",
          evidence={"blockersCount": 0, "skipMainFetch": True},
          workspace_dir=ws,
          state_dir=state_dir,
      )
      self.assertTrue(ok_t45, msg_t45)

    # Case B: JSON Round 1 APPROVED -> MD-only Round 2 REJECTED
    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      store = StateReportStore(state_dir)
      self.assertTrue(
          store.submit(
              "critic_review",
              _valid_critic_review_payload(round_num=1, verdict="APPROVED"),
          ).ok
      )
      self.assertTrue(promote_review_round(ws, "1").promoted)
      canon_json = os.path.join(state_dir, "40_critic_review.json")
      self.assertTrue(os.path.isfile(canon_json))

      r2_md = os.path.join(state_dir, "40_critic_review_round2.md")
      with open(r2_md, "w", encoding="utf-8") as f:
        f.write(
            render_critic_review_markdown(
                _valid_critic_review_payload(round_num=2, verdict="REJECTED")
            )
        )
      outcome = promote_review_round(ws, "2")
      self.assertTrue(outcome.promoted)
      self.assertFalse(os.path.exists(canon_json))
      ok_t42, msg_t42 = advance_workflow_target(
          "T40",
          "T42",
          evidence={"blockersCount": 1, "skipMainFetch": True},
          workspace_dir=ws,
          state_dir=state_dir,
      )
      self.assertTrue(ok_t42, msg_t42)

  def test_active_state_strict_booleans_and_atomic_cleanup_on_errors(
      self,
  ) -> None:
    """BLOCKER-3: Strict boolean parsing, COMPLETE precedence, and temp cleanup on OSError/UnicodeEncodeError."""
    from lib.active_state import ActiveTargetState, atomic_write_text

    # String "false" must never coerce to True
    parsed = ActiveTargetState.from_dict({
        "target": "T30",
        "status": "ACTIVE",
        "complete": "false",
        "parked": "false",
    })
    self.assertIs(parsed.complete, False)
    self.assertIs(parsed.parked, False)
    self.assertEqual(parsed.status, "ACTIVE")
    self.assertTrue(parsed.is_active())

    # COMPLETE always forces complete=True, parked=False, parking_reason=""
    comp = ActiveTargetState.from_dict({
        "target": "T90",
        "status": "COMPLETE",
        "complete": False,
        "parked": True,
        "parkingReason": "stale reason",
    })
    self.assertEqual(comp.status, "COMPLETE")
    self.assertIs(comp.complete, True)
    self.assertIs(comp.parked, False)
    self.assertEqual(comp.parking_reason, "")

    with tempfile.TemporaryDirectory() as tmp:
      target = os.path.join(tmp, "out.txt")

      # UnicodeEncodeError (lone surrogate) must clean up temp file
      with self.assertRaises(UnicodeEncodeError):
        atomic_write_text(target, "bad surrogate: \ud800")
      self.assertFalse(os.path.exists(target))
      self.assertEqual(os.listdir(tmp), [])

      # OSError during fsync must clean up temp file
      with mock.patch("os.fsync", side_effect=OSError(errno.ENOSPC, "No space")):
        with self.assertRaises(OSError):
          atomic_write_text(target, "valid utf8\n")
      self.assertFalse(os.path.exists(target))
      self.assertEqual(os.listdir(tmp), [])

  def test_state_report_store_stages_both_files_atomically_on_md_failure(
      self,
  ) -> None:
    """BLOCKER-3: Failing during .md staging leaves zero partial .json or .tmp_* files on disk."""
    from lib.state_reports import StateReportStore

    with tempfile.TemporaryDirectory() as tmp:
      state_dir = os.path.join(tmp, ".workflow", "state")
      store = StateReportStore(state_dir)

      # Seed existing valid report so we verify it is NOT overwritten on partial failure
      first = store.submit("archaeology", _valid_archaeology_payload("GITHUB"))
      self.assertTrue(first.ok, first.errors)
      with open(first.json_path, "r", encoding="utf-8") as f:
        original_json = f.read()

      # Fail on the second fsync (the .md temp file)
      real_fsync = os.fsync
      call_count = {"n": 0}

      def _fail_second_fsync(fd: int) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
          raise OSError(errno.ENOSPC, "Disk full during markdown staging")
        real_fsync(fd)

      with mock.patch("os.fsync", side_effect=_fail_second_fsync):
        failed = store.submit(
            "archaeology", _valid_archaeology_payload("PURE_GIT")
        )
      self.assertFalse(failed.ok)
      self.assertTrue(failed.errors)
      with open(first.json_path, "r", encoding="utf-8") as f:
        self.assertEqual(f.read(), original_json)
      leftovers = [n for n in os.listdir(state_dir) if n.startswith(".tmp_")]
      self.assertEqual(leftovers, [])

  def test_pre_tool_guard_post_landing_target_ignores_poisoned_next_action(
      self,
  ) -> None:
    """BLOCKER-3: _is_post_landing_target trusts active_target.json over poisoned 99_next_action.md."""
    from lib.active_state import ActiveStateStore, ActiveTargetState
    from lib.pre_tool_guard import _is_post_landing_target

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T50",
              target_name="commit",
              previous_target="T45",
              status="ACTIVE",
              complete=False,
          )
      )
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Poisoned\n- Active Target: Target 80 (land)\n")

      self.assertFalse(_is_post_landing_target(state_dir))

      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T80",
              target_name="land",
              previous_target="T75",
              status="ACTIVE",
              complete=False,
          )
      )
      with open(
          os.path.join(state_dir, "99_next_action.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Poisoned\n- Active Target: Target 50 (commit)\n")

      self.assertTrue(_is_post_landing_target(state_dir))

  def test_resolve_skipped_targets_ignores_completed_active_target(self) -> None:
    """NBO-1: Completed Pure Git ticket skippedTargets never leaks into a new ticket."""
    from lib.active_state import ActiveStateStore, ActiveTargetState
    from lib.state_machine import _resolve_skipped_targets

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T90",
              target_name="finish",
              previous_target="T80",
              status="COMPLETE",
              complete=True,
              skipped_targets=("T60",),
          )
      )
      self.assertEqual(_resolve_skipped_targets(state_dir, "T75", "T70"), ())

  def test_commonmark_closing_fence_requires_at_most_three_leading_spaces(
      self,
  ) -> None:
    """NBO-3: 4-space indented backticks inside a fenced block do not close the fence (SCAR-LOCAL-DATA-0001)."""
    from lib.report_renderers import ensure_balanced_fences
    from lib.state_reports import _has_unfenced_alnum_prose

    # Four-space indented closing backticks stay inside the code block
    indented_block = "```markdown\n    ```\nprose inside fence"
    balanced = ensure_balanced_fences(indented_block)
    self.assertTrue(
        balanced.endswith("\n```"),
        f"Expected unclosed fence to be closed after 4-space indented backticks: {balanced!r}",
    )

    # Pillar containing ONLY a code block with a 4-space indented ``` inside has NO unfenced prose
    fence_only_with_indented = "```markdown\n    ```\nstill inside fence\n```"
    self.assertFalse(_has_unfenced_alnum_prose(fence_only_with_indented))

  def test_registry_register_and_critic_validation_negative_boundaries(
      self,
  ) -> None:
    """NBO-4: ReportRegistry rejects empty/duplicate names; load_critic_review_validation rejects mismatch/malformed."""
    from lib.state_reports import (
        ReportSpec,
        ReportValidationResult,
        create_default_registry,
        load_critic_review_validation,
    )

    reg = create_default_registry()
    dummy = ReportSpec(
        report_type="",
        json_filename_template="x.json",
        md_filename_template="x.md",
        validator=lambda p: ReportValidationResult(valid=True, normalized_payload=p),
        renderer=lambda p: "# x\n",
    )
    with self.assertRaises(ValueError):
      reg.register(dummy)

    dup = ReportSpec(
        report_type="archaeology",
        json_filename_template="x.json",
        md_filename_template="x.md",
        validator=lambda p: ReportValidationResult(valid=True, normalized_payload=p),
        renderer=lambda p: "# x\n",
    )
    with self.assertRaises(ValueError):
      reg.register(dup)

    with tempfile.TemporaryDirectory() as state_dir:
      # Round mismatch: round1 file claims round=2
      r1_path = os.path.join(state_dir, "40_critic_review_round1.json")
      with open(r1_path, "w", encoding="utf-8") as f:
        json.dump(_valid_critic_review_payload(round_num=2, verdict="APPROVED"), f)
      mismatch = load_critic_review_validation(state_dir, round_number=1)
      self.assertIsNotNone(mismatch)
      self.assertFalse(mismatch.valid)
      self.assertTrue(any("mismatch" in e.lower() for e in mismatch.errors))

      # Malformed JSON file
      r2_path = os.path.join(state_dir, "40_critic_review_round2.json")
      with open(r2_path, "w", encoding="utf-8") as f:
        f.write("{broken json")
      malformed = load_critic_review_validation(state_dir, round_number=2)
      self.assertIsNotNone(malformed)
      self.assertFalse(malformed.valid)
      self.assertTrue(any("malformed" in e.lower() for e in malformed.errors))

  def test_detect_latest_target_ignores_completed_state_and_enforces_pause_gates(
      self,
  ) -> None:
    """BLOCKER-2 (GH-281): Completed active_target.json never triggers T80/T90 SCAR-PROC-102 exemptions."""
    from lib.active_state import ActiveStateStore, ActiveTargetState
    from lib.state_machine import advance_workflow_target
    from lib.turn_boundary import (
        detect_latest_target,
        evaluate_command_turn_boundary,
    )

    with tempfile.TemporaryDirectory() as ws:
      state_dir = os.path.join(ws, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)

      # Finish a prior ticket via state_machine so active_target.json persists COMPLETE T90
      ok_fin, msg_fin = advance_workflow_target(
          "T80",
          "T90",
          evidence={"complete": True, "skipMainFetch": True},
          workspace_dir=ws,
          state_dir=state_dir,
      )
      self.assertTrue(ok_fin, msg_fin)
      self.assertEqual(detect_latest_target(steps=[], state_dir=state_dir), "")

      # Same-turn T50 (writing 50_commit_msg.txt and committing) must remain blocked
      t50_transcript = os.path.join(ws, "t50_same_turn.jsonl")
      t50_steps = [
          {
              "step_index": 1,
              "type": "USER_INPUT",
              "content": "Draft the commit message.",
          },
          {
              "step_index": 2,
              "type": "PLANNER_RESPONSE",
              "content": "Writing commit message card.",
              "tool_calls": [{
                  "name": "write_file",
                  "args": {"Path": os.path.join(state_dir, "50_commit_msg.txt")},
              }],
          },
      ]
      with open(t50_transcript, "w", encoding="utf-8") as f:
        for step in t50_steps:
          f.write(json.dumps(step) + "\n")

      ok_t50, reason_t50 = evaluate_command_turn_boundary(
          "T50", t50_transcript, state_dir=state_dir
      )
      self.assertFalse(
          ok_t50,
          "Same-turn T50 commit must be blocked after prior ticket finish.",
      )
      self.assertIn("SCAR-PROC-102", reason_t50)

      # T60 push with no human approval turn must also remain blocked after finish
      t60_transcript = os.path.join(ws, "t60_no_user.jsonl")
      with open(t60_transcript, "w", encoding="utf-8") as f:
        f.write(
            json.dumps({
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "content": "Attempting push without human approval.",
                "tool_calls": [],
            })
            + "\n"
        )
      ok_t60, reason_t60 = evaluate_command_turn_boundary(
          "T60", t60_transcript, state_dir=state_dir
      )
      self.assertFalse(
          ok_t60,
          "Unapproved T60 push must be blocked after prior ticket finish.",
      )
      self.assertIn("SCAR-PROC-102", reason_t60)

      # Raw partial COMPLETE variants (status-only or complete-only) also return ""
      active_path = os.path.join(state_dir, "active_target.json")
      for raw_complete in (
          {"target": "T90", "status": "COMPLETE"},
          {"target": "T90", "complete": True},
      ):
        with open(active_path, "w", encoding="utf-8") as f:
          json.dump(raw_complete, f)
        self.assertEqual(
            detect_latest_target(steps=[], state_dir=state_dir), ""
        )

      # Active (non-complete) T90 still returns "T90" and preserves post-landing exemption
      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T90",
              target_name="codified-scars-consolidated",
              previous_target="T80",
              status="ACTIVE",
              complete=False,
          )
      )
      self.assertEqual(
          detect_latest_target(steps=[], state_dir=state_dir), "T90"
      )
      ok_active_t50, reason_active_t50 = evaluate_command_turn_boundary(
          "T50", t50_transcript, state_dir=state_dir
      )
      self.assertTrue(ok_active_t50, reason_active_t50)
      ok_active_t60, reason_active_t60 = evaluate_command_turn_boundary(
          "T60", t60_transcript, state_dir=state_dir
      )
      self.assertTrue(ok_active_t60, reason_active_t60)

      # Legacy pre-GH-281 active T90 without status/complete still returns "T90"
      with open(active_path, "w", encoding="utf-8") as f:
        json.dump(
            {"previousTarget": "T80", "target": "T90", "targetName": "finish"},
            f,
        )
      self.assertEqual(
          detect_latest_target(steps=[], state_dir=state_dir), "T90"
      )

  def test_t50_to_t70_block_message_names_actual_github_mode_receipt_file(
      self,
  ) -> None:
    """NBO-2: SCAR-GIT-30 T50 -> T70 block message cites the actual JSON or legacy .md receipt file."""
    from lib.state_machine import advance_workflow_target
    from lib.state_reports import StateReportStore

    # 1. Canonical JSON receipt declaring GITHUB names 15_archaeology_receipt.json
    with tempfile.TemporaryDirectory() as ws_json:
      st_json = os.path.join(ws_json, ".workflow", "state")
      res = StateReportStore(st_json).submit(
          "archaeology", _valid_archaeology_payload("GITHUB")
      )
      self.assertTrue(res.ok, res.errors)
      ok_json, msg_json = advance_workflow_target(
          "T50",
          "T70",
          evidence={"humanCommitApproved": True, "skipMainFetch": True},
          workspace_dir=ws_json,
          state_dir=st_json,
      )
      self.assertFalse(ok_json)
      self.assertIn("BLOCKED: [SCAR-GIT-30]", msg_json)
      self.assertIn("15_archaeology_receipt.json", msg_json)
      self.assertNotIn("15_archaeology_receipt.md", msg_json)

    # 2. Legacy Markdown-only receipt declaring GITHUB names 15_archaeology_receipt.md
    with tempfile.TemporaryDirectory() as ws_md:
      st_md = os.path.join(ws_md, ".workflow", "state")
      os.makedirs(st_md, exist_ok=True)
      with open(
          os.path.join(st_md, "15_archaeology_receipt.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Archaeology Receipt\n- HOSTING_MODE: GITHUB\n")
      ok_md, msg_md = advance_workflow_target(
          "T50",
          "T70",
          evidence={"humanCommitApproved": True, "skipMainFetch": True},
          workspace_dir=ws_md,
          state_dir=st_md,
      )
      self.assertFalse(ok_md)
      self.assertIn("BLOCKED: [SCAR-GIT-30]", msg_md)
      self.assertIn("15_archaeology_receipt.md", msg_md)
      self.assertNotIn("15_archaeology_receipt.json", msg_md)

    # 3. Canonical JSON PURE_GIT overrides stale legacy Markdown GITHUB
    with tempfile.TemporaryDirectory() as ws_override:
      st_ov = os.path.join(ws_override, ".workflow", "state")
      res_ov = StateReportStore(st_ov).submit(
          "archaeology", _valid_archaeology_payload("PURE_GIT")
      )
      self.assertTrue(res_ov.ok, res_ov.errors)
      with open(
          os.path.join(st_ov, "15_archaeology_receipt.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Stale Legacy Receipt\n- HOSTING_MODE: GITHUB\n")
      ok_ov, msg_ov = advance_workflow_target(
          "T50",
          "T70",
          evidence={"humanCommitApproved": True, "skipMainFetch": True},
          workspace_dir=ws_override,
          state_dir=st_ov,
      )
      self.assertTrue(ok_ov, msg_ov)


if __name__ == "__main__":
  unittest.main()
