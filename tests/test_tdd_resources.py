#!/usr/bin/env python3
"""Hermetic contract and negative-falsification tests for TDD resources (GH-273)."""

import os
import re
import sqlite3
import sys
import unittest
from typing import Dict, List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scar_candidate import (
    ScarCandidateValidator,
    count_words_in_sentence,
    split_into_sentences,
    validate_socratic_probe,
)
from lib.scars_db import compute_raw_fields_hash, query_scars
from tests.test_cross_agent_compatibility import check_scar_catalog_routing

TDD_RESOURCES_DIR = os.path.join(
    _REPO_ROOT, "skills", "makeflow", "references", "resources", "tdd"
)
TESTING_SCARS_PATH = os.path.join(
    _REPO_ROOT, "skills", "makeflow", "references", "scars", "testing.md"
)
SCARS_CATALOG_PATH = os.path.join(
    _REPO_ROOT, "skills", "makeflow", "references", "09_synthetic_scars_catalog.md"
)
SCARS_DB_PATH = os.path.join(
    _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
)

EXPECTED_TDD_SCARS = (
    ("SCAR-TEST-46", "SCAR-TEST-0046"),
    ("SCAR-TEST-47", "SCAR-TEST-0047"),
    ("SCAR-TEST-48", "SCAR-TEST-0048"),
    ("SCAR-TEST-49", "SCAR-TEST-0049"),
)

EXPECTED_RESOURCE_FILES = (
    "00_index_and_tdd_synthesis.md",
    "01_red_green_iron_law_and_rationalizations.md",
    "02_seams_behavior_and_mocking_boundaries.md",
    "03_vertical_slicing_and_iterative_cycles.md",
)

VERIFIED_UPSTREAM_SOURCES = (
    (
        "obra/superpowers",
        "https://github.com/obra/superpowers",
        "MIT",
        "Jesse Vincent",
    ),
    (
        "mattpocock/skills",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd",
        "MIT",
        "Matt Pocock",
    ),
    (
        "tweag/agentic-coding-handbook",
        "https://github.com/tweag/agentic-coding-handbook",
        "MIT",
        "Modus Labs",
    ),
    (
        "AsiaOstrich/universal-dev-standards",
        "https://github.com/AsiaOstrich/universal-dev-standards",
        "CC BY 4.0",
        "Universal Development Standards Contributors",
    ),
    (
        "fheikens/stdd",
        "https://github.com/fheikens/stdd",
        "CC BY 4.0",
        "Frank Heikens",
    ),
    (
        "emilybache/DiamondKata",
        "https://github.com/emilybache/DiamondKata",
        "MIT",
        "Emily Bache",
    ),
    (
        "emilybache/Racing-Car-Katas",
        "https://github.com/emilybache/Racing-Car-Katas",
        "MIT",
        "Emily Bache",
    ),
)

_NUMBERED_STEP_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_BULLET_LINE_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def extract_markdown_prose_blocks(
    markdown_text: str,
) -> Tuple[List[str], List[str]]:
  """Extracts descriptive prose lines and numbered procedural lines outside fences.

  Tracks open triple-backtick and tilde fences before matching headings,
  separators, or tables (SCAR-LOCAL-DATA-0001).

  Args:
    markdown_text: Raw Markdown document content.

  Returns:
    Tuple of (descriptive_blocks, procedural_blocks).
  """
  descriptive: List[str] = []
  procedural: List[str] = []
  in_fence = False
  fence_char = ""
  fence_len = 0

  for raw_line in markdown_text.splitlines():
    fence_match = _FENCE_RE.match(raw_line)
    if fence_match:
      marker = fence_match.group(1)
      if not in_fence:
        in_fence = True
        fence_char = marker[0]
        fence_len = len(marker)
      elif marker[0] == fence_char and len(marker) >= fence_len:
        in_fence = False
        fence_char = ""
        fence_len = 0
      continue

    if in_fence:
      continue

    stripped = raw_line.strip()
    if (
        not stripped
        or stripped.startswith("#")
        or stripped.startswith("|")
        or stripped in ("---", "***", "___")
    ):
      continue

    step_match = _NUMBERED_STEP_RE.match(raw_line)
    if step_match:
      procedural.append(step_match.group(1).strip())
      continue

    bullet_match = _BULLET_LINE_RE.match(raw_line)
    if bullet_match:
      body = bullet_match.group(1).strip()
      if body.startswith("**Source Standard") or body.startswith(
          "**License and Attribution**"
      ):
        continue
      descriptive.append(body)
      continue

    descriptive.append(stripped)

  return descriptive, procedural


def validate_markdown_simple_english(
    markdown_text: str, doc_id: str
) -> List[str]:
  """Validates Markdown prose against Simple English rules using ScarCandidateValidator.

  Args:
    markdown_text: Raw Markdown content to validate.
    doc_id: Document label for error messages.

  Returns:
    List of diagnostic error strings (empty when compliant).
  """
  validator = ScarCandidateValidator()
  descriptive, procedural = extract_markdown_prose_blocks(markdown_text)
  desc_text = "\n".join(descriptive) if descriptive else "Placeholder."
  proc_text = "\n".join(procedural) if procedural else "Follow this rule."
  candidate = {
      "id": doc_id,
      "wound": desc_text,
      "trap": "Placeholder.",
      "circuit_breaker": "",
      "reflex": proc_text,
      "raw_wound": desc_text,
      "raw_trap": "Placeholder.",
      "raw_circuit_breaker": "",
      "raw_reflex": proc_text,
  }
  return validator.validate(candidate)


def validate_prose_line_simple_english(
    line_text: str, doc_id: str, procedural: bool = True
) -> List[str]:
  """Validates a single Markdown line against Simple English rules.

  Args:
    line_text: Raw Markdown line (numbered step, bullet, blockquote, or prose).
    doc_id: Diagnostic label for error messages.
    procedural: When True, enforces <= 20 words per sentence; otherwise <= 25.

  Returns:
    List of diagnostic error strings (empty when compliant).
  """
  cleaned = re.sub(r"^\s*(?:\d+\.\s+|[-*]\s+|>\s*)", "", line_text).strip()
  if cleaned.startswith('*"') and cleaned.endswith('"*'):
    cleaned = cleaned[2:-2].strip()
  validator = ScarCandidateValidator()
  desc_text = "Placeholder." if procedural else cleaned
  proc_text = cleaned if procedural else "Follow this rule."
  candidate = {
      "id": doc_id,
      "wound": desc_text,
      "trap": "Placeholder.",
      "circuit_breaker": "",
      "reflex": proc_text,
      "raw_wound": desc_text,
      "raw_trap": "Placeholder.",
      "raw_circuit_breaker": "",
      "raw_reflex": proc_text,
  }
  return validator.validate(candidate)


class TestTddResourceLibrary(unittest.TestCase):
  """Contract and negative-falsification tests for skills/makeflow/references/resources/tdd/."""

  def _read_resource(self, filename: str) -> str:
    path = os.path.join(TDD_RESOURCES_DIR, filename)
    self.assertTrue(
        os.path.isfile(path), f"Missing TDD resource file: {path}"
    )
    with open(path, "r", encoding="utf-8") as f:
      return f.read()

  def test_all_four_tdd_resource_files_exist_and_have_attribution_headers(self):
    for filename in EXPECTED_RESOURCE_FILES:
      content = self._read_resource(filename)
      self.assertTrue(
          content.strip(), f"TDD resource file is empty: {filename}"
      )
      self.assertTrue(
          content.lstrip().startswith("# "),
          f"TDD resource {filename} must start with a top-level heading",
      )

    for spoke in EXPECTED_RESOURCE_FILES[1:]:
      content = self._read_resource(spoke)
      self.assertIn(
          "- **Source Standards**:",
          content,
          f"{spoke} missing '- **Source Standards**:' attribution header",
      )
      self.assertIn(
          "- **License and Attribution**:",
          content,
          f"{spoke} missing '- **License and Attribution**:' attribution header",
      )

  def test_index_attributes_all_seven_verified_sources_and_documents_exclusion(
      self,
  ):
    index_text = self._read_resource("00_index_and_tdd_synthesis.md")
    for repo_name, url, license_name, author in VERIFIED_UPSTREAM_SOURCES:
      self.assertIn(
          repo_name,
          index_text,
          f"Index missing upstream repo name: {repo_name}",
      )
      self.assertIn(
          url, index_text, f"Index missing upstream repo URL: {url}"
      )
      self.assertIn(
          license_name,
          index_text,
          f"Index missing license '{license_name}' for {repo_name}",
      )
      self.assertIn(
          author,
          index_text,
          f"Index missing author '{author}' for {repo_name}",
      )

    # Transparent license exclusion note for mfranzon/tdd
    self.assertIn("mfranzon/tdd", index_text)
    self.assertRegex(
        index_text,
        r"(?i)mfranzon/tdd.*?(?:no\s+`?LICENSE`?|no\s+license|omitted|excluded)",
    )

  def test_complexity_scaled_slice_budget_and_anti_padding_documented(self):
    for filename in (
        "00_index_and_tdd_synthesis.md",
        "03_vertical_slicing_and_iterative_cycles.md",
    ):
      text = self._read_resource(filename)
      for required_term in (
          "Trivial",
          "Moderate",
          "Complex",
          "1 slice",
          "2 to 5",
          "5+",
          "1 dispatch",
          "padding",
          "completed_cycles",
          "RETRACTED_PREMISE",
      ):
        self.assertIn(
            required_term,
            text,
            f"{filename} missing required complexity-scaling term '{required_term}'",
        )

  def test_all_tdd_resources_comply_with_simple_english_validator(self):
    for filename in EXPECTED_RESOURCE_FILES:
      content = self._read_resource(filename)
      errors = validate_markdown_simple_english(content, doc_id=filename)
      self.assertEqual(
          errors,
          [],
          f"Simple English violations in {filename}:\n" + "\n".join(errors),
      )

  def test_simple_english_validator_negative_falsification_and_fence_tracking(
      self,
  ):
    # Contraction must fail
    err_contraction = validate_markdown_simple_english(
        "# Test\n\nAgents don't skip red tests.\n", doc_id="neg-contraction"
    )
    self.assertTrue(
        any("contraction" in e for e in err_contraction), err_contraction
    )

    # Semicolon must fail
    err_semi = validate_markdown_simple_english(
        "# Test\n\nWrite one test; run it first.\n", doc_id="neg-semicolon"
    )
    self.assertTrue(any("semicolon" in e for e in err_semi), err_semi)

    # Em-dash must fail
    err_emdash = validate_markdown_simple_english(
        "# Test\n\nWrite one test \u2014 then verify failure.\n",
        doc_id="neg-emdash",
    )
    self.assertTrue(any("em-dash" in e for e in err_emdash), err_emdash)

    # Over-length descriptive sentence (>25 words) must fail
    long_desc = (
        "# Test\n\n"
        "This single descriptive sentence deliberately contains far too many "
        "ordinary English words in one uninterrupted clause so that the "
        "validator rejects it for exceeding twenty five words.\n"
    )
    self.assertGreater(
        count_words_in_sentence(split_into_sentences(long_desc)[0]), 25
    )
    err_long_desc = validate_markdown_simple_english(
        long_desc, doc_id="neg-long-desc"
    )
    self.assertTrue(
        any("exceeds word limit" in e for e in err_long_desc), err_long_desc
    )

    # Over-length procedural numbered step (>20 words) must fail
    long_proc = (
        "# Test\n\n"
        "1. Write one focused unit test that exercises the entire subsystem "
        "boundary with every single optional argument populated before you "
        "begin editing any file.\n"
    )
    err_long_proc = validate_markdown_simple_english(
        long_proc, doc_id="neg-long-proc"
    )
    self.assertTrue(
        any("exceeds word limit" in e for e in err_long_proc), err_long_proc
    )

    # Fenced code blocks with contractions, semicolons, em-dashes, and '---' must be ignored (SCAR-LOCAL-DATA-0001)
    fenced_ok = (
        "# Test\n\n"
        "Keep tests isolated.\n\n"
        "```python\n"
        "# don't flag this; em-dash \u2014 inside code\n"
        "## not a heading\n"
        "---\n"
        "x = 1\n"
        "```\n\n"
        "1. Run the test suite.\n"
    )
    self.assertEqual(
        validate_markdown_simple_english(fenced_ok, doc_id="fence-ok"), []
    )


def parse_spoke_scar_sections(markdown_text: str) -> Dict[str, Dict[str, str]]:
  """Parses ### SCAR-... sections from a Tier 1 spoke Markdown file.

  Tracks open code fences before matching headings (SCAR-LOCAL-DATA-0001).

  Args:
    markdown_text: Raw spoke file Markdown text.

  Returns:
    Mapping from scar ID (for example ``SCAR-TEST-46``) to parsed anatomy fields.
  """
  scars: Dict[str, Dict[str, str]] = {}
  current_id = ""
  in_fence = False
  fence_char = ""
  fence_len = 0

  heading_re = re.compile(r"^###\s+(SCAR-[A-Z]+-\d+):\s*(.+?)\s*$")
  field_re = re.compile(
      r"^-\s+\*\*(Wound|Trap|Circuit Breaker|Permanent Reflex|Socratic Probe)\*\*:\s*(.+?)\s*$"
  )
  field_map = {
      "Wound": "wound",
      "Trap": "trap",
      "Circuit Breaker": "circuit_breaker",
      "Permanent Reflex": "reflex",
      "Socratic Probe": "socratic_probe",
  }

  for raw_line in markdown_text.splitlines():
    fence_match = _FENCE_RE.match(raw_line)
    if fence_match:
      marker = fence_match.group(1)
      if not in_fence:
        in_fence = True
        fence_char = marker[0]
        fence_len = len(marker)
      elif marker[0] == fence_char and len(marker) >= fence_len:
        in_fence = False
        fence_char = ""
        fence_len = 0
      continue
    if in_fence:
      continue

    h_match = heading_re.match(raw_line.strip())
    if h_match:
      current_id = h_match.group(1)
      scars[current_id] = {
          "id": current_id,
          "title": h_match.group(2),
          "wound": "",
          "trap": "",
          "circuit_breaker": "",
          "reflex": "",
          "socratic_probe": "",
      }
      continue

    if current_id:
      f_match = field_re.match(raw_line.strip())
      if f_match:
        scars[current_id][field_map[f_match.group(1)]] = f_match.group(2)

  return scars


class TestTddTierOneScars(unittest.TestCase):
  """Contract tests for Tier 1 TDD scars SCAR-TEST-46 through SCAR-TEST-49 (GH-273)."""

  def test_scars_present_in_testing_spoke_and_pass_anatomy_validators(self):
    with open(TESTING_SCARS_PATH, "r", encoding="utf-8") as f:
      testing_text = f.read()

    parsed = parse_spoke_scar_sections(testing_text)
    validator = ScarCandidateValidator()

    for short_id, _ in EXPECTED_TDD_SCARS:
      # Summary table row in testing.md
      row_pattern = rf"(?m)^\|\s*`{re.escape(short_id)}`\s*\|"
      self.assertRegex(
          testing_text,
          row_pattern,
          f"{short_id} missing summary table row in scars/testing.md",
      )
      self.assertIn(
          short_id, parsed, f"{short_id} missing heading section in scars/testing.md"
      )
      entry = parsed[short_id]
      for required_key in ("wound", "trap", "reflex", "socratic_probe"):
        self.assertTrue(
            entry[required_key],
            f"{short_id} missing non-empty '{required_key}' field",
        )
      candidate = {
          "id": short_id,
          "wound": entry["wound"],
          "trap": entry["trap"],
          "circuit_breaker": entry["circuit_breaker"],
          "reflex": entry["reflex"],
          "socratic_probe": entry["socratic_probe"],
          "raw_wound": entry["wound"],
          "raw_trap": entry["trap"],
          "raw_circuit_breaker": entry["circuit_breaker"],
          "raw_reflex": entry["reflex"],
      }
      self.assertEqual(
          validator.validate(candidate),
          [],
          f"{short_id} failed ScarCandidateValidator",
      )
      self.assertEqual(
          validate_socratic_probe(entry["socratic_probe"], scar_id=short_id),
          [],
          f"{short_id} failed validate_socratic_probe",
      )

    # SCAR-TEST-46 must guard against both horizontal dumping and trivial padding
    scar_46_combined = " ".join(parsed["SCAR-TEST-46"].values()).lower()
    self.assertIn("horizontal", scar_46_combined)
    self.assertIn("trivial", scar_46_combined)
    self.assertIn("padding", scar_46_combined)

  def test_scars_routed_in_master_catalog(self):
    with open(SCARS_CATALOG_PATH, "r", encoding="utf-8") as f:
      catalog_text = f.read()

    for short_id, _ in EXPECTED_TDD_SCARS:
      self.assertTrue(
          check_scar_catalog_routing(catalog_text, short_id, "testing.md"),
          f"{short_id} missing valid Master Routing Table row in 09_synthetic_scars_catalog.md",
      )

    # Negative falsification: wrong spoke must fail routing check
    self.assertFalse(
        check_scar_catalog_routing(catalog_text, "SCAR-TEST-46", "process.md")
    )

  def test_scars_mirrored_and_queryable_in_bundled_sqlite_db(self):
    conn = sqlite3.connect(f"file:{SCARS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
      for short_id, canonical_id in EXPECTED_TDD_SCARS:
        row = conn.execute(
            "SELECT * FROM scars WHERE id = ?", (canonical_id,)
        ).fetchone()
        self.assertIsNotNone(
            row, f"{canonical_id} missing from {SCARS_DB_PATH}"
        )
        self.assertEqual(row["source"], "makeflow")
        self.assertIsNone(row["ss_id"])
        self.assertEqual(row["original_id"], short_id)
        expected_hash = compute_raw_fields_hash(
            row["raw_wound"],
            row["raw_trap"],
            row["raw_circuit_breaker"],
            row["raw_reflex"],
        )
        self.assertEqual(
            row["raw_hash"],
            expected_hash,
            f"{canonical_id} has mismatched raw_hash",
        )
    finally:
      conn.close()

    # Queryable via query_scars at Target 20, Target 30, and Target 40
    for short_id, canonical_id in EXPECTED_TDD_SCARS:
      for phase_label, target_int in (
          ("Target 20: plan", 20),
          ("Target 30: tdd", 30),
          ("Target 40: review", 40),
      ):
        matches = query_scars(
            db_path=SCARS_DB_PATH,
            query=short_id,
            workflow_phase=phase_label,
            mf_target=target_int,
            limit=5,
        )
        found_ids = [m["id"] for m in matches]
        self.assertIn(
            canonical_id,
            found_ids,
            f"{canonical_id} not returned by query_scars for {phase_label} (mf_target={target_int})",
        )

  def test_malformed_scar_and_probe_fail_validators(self):
    validator = ScarCandidateValidator()
    bad_candidate = {
        "id": "SCAR-TEST-99",
        "wound": "Broken tests fail silently.",
        "trap": "Don't verify failures.",
        "circuit_breaker": "",
        "reflex": "Write one test.",
        "socratic_probe": "Do not write bad tests.",
        "raw_wound": "Broken tests fail silently.",
        "raw_trap": "Don't verify failures.",
        "raw_circuit_breaker": "",
        "raw_reflex": "Write one test.",
    }
    errors = validator.validate(bad_candidate)
    self.assertTrue(any("contraction" in e for e in errors), errors)
    probe_errors = validate_socratic_probe(
        "Do not mock internal helpers.", scar_id="SCAR-TEST-99"
    )
    self.assertTrue(any("must end with '?'" in e for e in probe_errors), probe_errors)


class TestTddWorkflowWiring(unittest.TestCase):
  """Contract tests for supervisor planning, tdd-worker, rules, SKILL.md, README, and adapters (GH-273)."""

  TDD_INDEX_ANCHOR = (
      "skills/makeflow/references/resources/tdd/00_index_and_tdd_synthesis.md"
  )

  def _read_repo_file(self, *parts: str) -> str:
    path = os.path.join(_REPO_ROOT, *parts)
    self.assertTrue(os.path.isfile(path), f"Missing repository file: {path}")
    with open(path, "r", encoding="utf-8") as f:
      return f.read()

  def test_planning_manual_requires_complexity_budget_seams_and_tdd_index(self):
    text = self._read_repo_file(
        "skills", "makeflow", "references", "02_archaeology_and_planning.md"
    )
    self.assertIn("resources/tdd/00_index_and_tdd_synthesis.md", text)
    for token in ("complexity", "slice", "round", "public seam", "trivial"):
      self.assertIn(
          token,
          text.lower(),
          f"02_archaeology_and_planning.md missing '{token}'",
      )

  def test_tdd_manual_wires_resources_vertical_slices_and_preserves_scar_test_12(
      self,
  ):
    text = self._read_repo_file(
        "skills", "makeflow", "references", "03_tdd_and_code_quality.md"
    )
    self.assertIn("resources/tdd/00_index_and_tdd_synthesis.md", text)
    for token in (
        "completed_cycles",
        "vertical",
        "verify-green",
        "SCAR-TEST-46",
        "SCAR-TEST-49",
        "### Anchored TDD Receipts (`SCAR-TEST-12`)",
        "Protected targets include",
    ):
      self.assertIn(
          token,
          text,
          f"03_tdd_and_code_quality.md missing '{token}'",
      )

  def test_subagent_orchestration_worker_skill_and_rules_anchor_tdd_index(self):
    anchored_files = (
        ("agents", "tdd-worker.md"),
        ("skills", "makeflow", "references", "08_subagent_orchestration.md"),
        ("skills", "makeflow", "SKILL.md"),
        ("rules", "AGENTS.md"),
    )
    for parts in anchored_files:
      text = self._read_repo_file(*parts)
      self.assertIn(
          self.TDD_INDEX_ANCHOR,
          text,
          f"{'/'.join(parts)} missing explicit anchor '{self.TDD_INDEX_ANCHOR}' (SCAR-PROC-105)",
      )

    worker_text = self._read_repo_file("agents", "tdd-worker.md")
    for token in ("slice", "padding", "verify-green", "RETRACTED_PREMISE"):
      self.assertIn(
          token,
          worker_text,
          f"agents/tdd-worker.md missing '{token}'",
      )

    orch_text = self._read_repo_file(
        "skills", "makeflow", "references", "08_subagent_orchestration.md"
    )
    self.assertIn("slice", orch_text.lower())
    self.assertIn("round", orch_text.lower())

  def test_readme_links_tdd_resources_and_attributes_seven_verified_sources(self):
    readme = self._read_repo_file("README.md")
    lines = readme.splitlines()
    lead_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("- TDD Resources:")),
        None,
    )
    self.assertIsNotNone(
        lead_idx, "README.md missing '- TDD Resources:' Acknowledgments bullet"
    )
    lead_line = lines[lead_idx]
    self.assertIn("skills/makeflow/references/resources/tdd/", lead_line)
    self.assertEqual(
        validate_markdown_simple_english(lead_line, doc_id="README-tdd-lead"),
        [],
        f"README.md TDD Resources lead-in violates Simple English: {lead_line}",
    )

    nested_bullets: List[str] = []
    for line in lines[lead_idx + 1 :]:
      if re.match(r"^ {2,}[-*]\s+", line):
        nested_bullets.append(line)
      else:
        break

    self.assertEqual(
        len(nested_bullets),
        len(VERIFIED_UPSTREAM_SOURCES),
        f"Expected {len(VERIFIED_UPSTREAM_SOURCES)} nested attribution bullets under "
        f"'- TDD Resources:' in README.md, found {len(nested_bullets)}",
    )

    for repo_name, url, license_name, author in VERIFIED_UPSTREAM_SOURCES:
      matching = [b for b in nested_bullets if repo_name in b]
      self.assertEqual(
          len(matching),
          1,
          f"README.md must have exactly one nested bullet for {repo_name}",
      )
      bullet = matching[0]
      self.assertIn(
          url, bullet, f"README.md nested bullet for {repo_name} missing URL {url}"
      )
      self.assertIn(
          license_name,
          bullet,
          f"README.md nested bullet for {repo_name} missing license '{license_name}'",
      )
      self.assertIn(
          author,
          bullet,
          f"README.md nested bullet for {repo_name} missing author '{author}'",
      )
      self.assertEqual(
          validate_markdown_simple_english(
              bullet, doc_id=f"README-tdd-{repo_name}"
          ),
          [],
          f"README.md nested bullet for {repo_name} violates Simple English: {bullet}",
      )

  def test_all_five_cross_agent_adapters_wire_tdd_resources_and_complexity_scaling(
      self,
  ):
    adapters = (
        ("CLAUDE.md",),
        (".cursorrules",),
        (".cursor", "rules", "makeflow.mdc"),
        (".github", "copilot-instructions.md"),
        ("adapters", "chatgpt", "instructions.md"),
    )
    for parts in adapters:
      text = self._read_repo_file(*parts)
      rel = "/".join(parts)
      self.assertIn(
          self.TDD_INDEX_ANCHOR,
          text,
          f"{rel} missing '{self.TDD_INDEX_ANCHOR}'",
      )
      self.assertIn(
          "trivial",
          text.lower(),
          f"{rel} missing complexity-scaled trivial-task TDD rule",
      )

  def test_skill_md_stays_within_byte_budget_and_platform_neutral(self):
    skill_path = os.path.join(_REPO_ROOT, "skills", "makeflow", "SKILL.md")
    size_bytes = os.path.getsize(skill_path)
    self.assertLessEqual(
        size_bytes,
        24576,
        f"skills/makeflow/SKILL.md exceeds 24,576 byte ceiling ({size_bytes} bytes)",
    )
    with open(skill_path, "r", encoding="utf-8") as f:
      text = f.read()
    self.assertNotIn("<brain>", text)

  def test_modified_tdd_anchoring_prose_complies_with_simple_english(self):
    # Every newly added or modified TDD-anchoring line across prompts, manuals,
    # rules, SKILL.md, and all five adapters must obey Simple English word limits
    # (<= 20 words procedural, <= 25 words descriptive, zero contractions/semicolons/em-dashes).
    checks: List[Tuple[Tuple[str, ...], str, bool]] = [
        # agents/tdd-worker.md
        (("agents", "tdd-worker.md"), "1. Pre-flight reading:", True),
        (("agents", "tdd-worker.md"), "2. Complexity-scaled vertical slicing", True),
        (("agents", "tdd-worker.md"), "3. Red phase", True),
        (("agents", "tdd-worker.md"), "4. Green phase:", True),
        (("agents", "tdd-worker.md"), "7. Refactor phase", True),
        # skills/makeflow/SKILL.md
        (("skills", "makeflow", "SKILL.md"), "Read `references/02`,", True),
        (
            ("skills", "makeflow", "SKILL.md"),
            "1. Evaluate Socratic deliberation questions",
            True,
        ),
        (
            ("skills", "makeflow", "SKILL.md"),
            "3. Write `.workflow/state/20_plan.md`",
            True,
        ),
        (("skills", "makeflow", "SKILL.md"), "Read `references/03`,", True),
        (
            ("skills", "makeflow", "SKILL.md"),
            "1. Anchor explicit workspace manual paths",
            True,
        ),
        (("skills", "makeflow", "SKILL.md"), "3. Red phase:", True),
        (("skills", "makeflow", "SKILL.md"), "5. Refactor phase:", True),
        # skills/makeflow/references/02_archaeology_and_planning.md
        (
            ("skills", "makeflow", "references", "02_archaeology_and_planning.md"),
            "10. **Red-Green-Refactor TDD Methodology**:",
            True,
        ),
        (
            ("skills", "makeflow", "references", "02_archaeology_and_planning.md"),
            "10. Red-Green-Refactor TDD Plan:",
            True,
        ),
        # skills/makeflow/references/03_tdd_and_code_quality.md
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "6. Read `.workflow/state/20_plan.md`,",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "In multi-agent runtimes, fork `tdd-worker`",
            False,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "1. Write unit tests for the active slice",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "3. Run the targeted subproject test command",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "4. Never write feature code before watching",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "1. Wait until `verify-green`",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "2. Remove duplicate logic in production code",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "4. Re-run the targeted test command",
            True,
        ),
        (
            ("skills", "makeflow", "references", "03_tdd_and_code_quality.md"),
            "5. After all planned slices finish,",
            True,
        ),
        # skills/makeflow/references/08_subagent_orchestration.md
        (
            ("skills", "makeflow", "references", "08_subagent_orchestration.md"),
            "Anchor explicit paths (`SCAR-PROC-105`): `.workflow/state/20_plan.md`",
            True,
        ),
        (
            ("skills", "makeflow", "references", "08_subagent_orchestration.md"),
            '> *"Did you honor `.workflow/state/20_plan.md`',
            True,
        ),
        # rules/AGENTS.md
        (
            ("rules", "AGENTS.md"),
            "15. Surface OO Patterns, Cohesion, Complexity-Scaled TDD",
            True,
        ),
        (
            ("rules", "AGENTS.md"),
            "4. Anchor explicit workspace paths in Target 15",
            True,
        ),
        # All five cross-agent adapters
        (("CLAUDE.md",), self.TDD_INDEX_ANCHOR, True),
        ((".cursorrules",), self.TDD_INDEX_ANCHOR, True),
        ((".cursor", "rules", "makeflow.mdc"), self.TDD_INDEX_ANCHOR, True),
        ((".github", "copilot-instructions.md"), self.TDD_INDEX_ANCHOR, True),
        (("adapters", "chatgpt", "instructions.md"), self.TDD_INDEX_ANCHOR, True),
    ]

    all_errors: List[str] = []
    for parts, marker, is_procedural in checks:
      rel = "/".join(parts)
      text = self._read_repo_file(*parts)
      matched_lines = [
          line for line in text.splitlines() if marker in line
      ]
      self.assertTrue(
          matched_lines,
          f"{rel} missing expected TDD-anchoring line matching '{marker}'",
      )
      for idx, line in enumerate(matched_lines, start=1):
        doc_id = f"{rel}:{marker[:32]}#{idx}"
        errs = validate_prose_line_simple_english(
            line, doc_id=doc_id, procedural=is_procedural
        )
        all_errors.extend(errs)

    self.assertEqual(
        all_errors,
        [],
        "Simple English violations in modified TDD-anchoring lines:\n"
        + "\n".join(all_errors),
    )


if __name__ == "__main__":
  unittest.main()
