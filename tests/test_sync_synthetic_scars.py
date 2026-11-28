#!/usr/bin/env python3
"""Hermetic unit tests for scar sync, SQLite FTS5 query, and ontology (GH-118, GH-209).

Verifies SyncSyntheticScarsScript (lib/sync_synthetic_scars.py), slimmed
lib/scars_db.py helpers, query_scars() conditional circuit_breaker filtering,
lossless 4-field raw_hash backfill, in-place ss_id updates, deterministic
unmatched imports, Makeflow scar preservation, and ontology mapping.
Zero live network calls and zero mutations to bundled scars_registry.db
(SCAR-TEST-08, SCAR-TEST-45).
"""

import ast
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.base_script import BaseScript
from lib.scar_candidate import ScarCandidateValidator, validate_socratic_probe
from lib.scar_ontology import (
    DOMAIN_CATEGORY_ONTOLOGY,
    UPSTREAM_CATEGORY_MAP,
    compute_canonical_scar_id,
    compute_makeflow_targets,
    normalize_domain_category,
    parse_upstream_targets,
    resolve_excluded_category_tokens,
)
import lib.scars_db as scars_db_mod
from lib.scars_db import (
    RAW_HASH_VERSION,
    backfill_raw_hashes_if_needed,
    compute_raw_fields_hash,
    count_scar_pipeline_status,
    enrich_scars_database,
    extract_protected_spans,
    init_scars_db_schema,
    load_session_scars,
    partition_session_scars,
    query_scars,
    resolve_scars_db_path,
    resolve_scars_db_paths,
    save_session_scars,
    select_scars_needing_importer,
    select_scars_needing_probe_writer,
    verify_scar_transformation,
)
from lib.sync_synthetic_scars import SyncSyntheticScarsScript


class MockHTTPResponse:
  """Minimal context-manager mock for urllib.request.urlopen responses."""

  def __init__(
      self,
      body: str,
      status: int = 200,
      etag: str = '"etag-v1"',
  ) -> None:
    self._body = body.encode("utf-8")
    self.status = status
    self.headers = {"ETag": etag} if etag else {}

  def read(self) -> bytes:
    return self._body

  def getcode(self) -> int:
    return self.status

  def __enter__(self) -> "MockHTTPResponse":
    return self

  def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    return None


def _make_upstream_scar(
    ss_id: str,
    title: str = "Sample Scar",
    category: str = "ARCH",
    wound: str = "Raw wound text.",
    trap: str = "Raw trap text.",
    circuit_breaker: str = "",
    reflex: str = "Raw reflex text.",
    workflow_phases=None,
    applies_when: str = "Always",
) -> dict:
  """Build an upstream syntheticscars.org JSON scar record."""
  return {
      "id": ss_id,
      "title": title,
      "category": category,
      "categorySlug": category.lower(),
      "visibility": "public",
      "workflowPhases": workflow_phases or ["Target 20: plan", "Target 30: tdd"],
      "appliesWhen": applies_when,
      "graduationStatus": "graduated",
      "wound": wound,
      "trap": trap,
      "circuitBreaker": circuit_breaker,
      "reflex": reflex,
  }


def _insert_scar_row(
    conn: sqlite3.Connection,
    scar_id: str,
    ss_id: str = None,
    title: str = "Test Scar",
    category: str = "ARCH",
    raw_wound: str = "Raw wound.",
    raw_trap: str = "Raw trap.",
    raw_circuit_breaker: str = "",
    raw_reflex: str = "Raw reflex.",
    raw_hash: str = "legacy-md5-hash",
    wound: str = "Simple wound.",
    trap: str = "Simple trap.",
    circuit_breaker: str = "",
    reflex: str = "Simple reflex.",
    socratic_probe: str = "Did you verify the invariant?",
    source: str = "upstream",
    original_id: str = None,
    workflow_phases: str = '["Target 20: plan", "Target 30: tdd"]',
    ss_targets: str = '["Target 20: plan", "Target 30: tdd"]',
    mf_targets: str = "[20, 30]",
    applies_when: str = "Writing Python code",
) -> None:
  """Insert a scar row into an initialized temporary SQLite DB."""
  cur = conn.cursor()
  cur.execute(
      """
      INSERT INTO scars (
          id, ss_id, title, category, category_slug, visibility,
          workflow_phases, ss_targets, mf_targets, applies_when,
          graduation_status, raw_wound, raw_trap, raw_circuit_breaker,
          raw_reflex, raw_hash, wound, trap, circuit_breaker, reflex,
          socratic_probe, source, original_id
      ) VALUES (?, ?, ?, ?, ?, 'public', ?, ?, ?, ?, 'graduated', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
          scar_id,
          ss_id,
          title,
          category,
          category.lower(),
          workflow_phases,
          ss_targets,
          mf_targets,
          applies_when,
          raw_wound,
          raw_trap,
          raw_circuit_breaker,
          raw_reflex,
          raw_hash,
          wound,
          trap,
          circuit_breaker,
          reflex,
          socratic_probe,
          source,
          original_id,
      ),
  )
  conn.commit()


class TestScarsDbModuleSlimming(unittest.TestCase):
  """Verify GH-209 / GH-202 module split invariants on lib/scars_db.py."""

  def test_scars_db_drops_cli_and_obsolete_symbols(self) -> None:
    """Verify lib/scars_db.py removed CLI classes, argparse, BaseScript, and migrate_scars_ontology."""
    for removed_sym in (
        "SyncSyntheticScarsScript",
        "EnrichScarsIndexScript",
        "migrate_scars_ontology",
        "ScriptArgumentError",
        "ScriptArgumentParser",
    ):
      self.assertFalse(
          hasattr(scars_db_mod, removed_sym),
          f"lib/scars_db.py must not define removed symbol '{removed_sym}'",
      )

    scars_db_path = os.path.join(_REPO_ROOT, "lib", "scars_db.py")
    with open(scars_db_path, "r", encoding="utf-8") as fh:
      tree = ast.parse(fh.read(), filename=scars_db_path)
    imported_modules = set()
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        for alias in node.names:
          imported_modules.add(alias.name.split(".")[0])
      elif isinstance(node, ast.ImportFrom) and node.module:
        imported_modules.add(node.module)
    self.assertNotIn("argparse", imported_modules)
    self.assertNotIn("lib.base_script", imported_modules)
    self.assertNotIn("urllib.request", imported_modules)


class TestSyncSyntheticScarsCliContract(unittest.TestCase):
  """Verify SyncSyntheticScarsScript CLI flags, backfill, delta sync, and status output."""

  def test_inherits_base_script_and_rejects_obsolete_flags(self) -> None:
    """Verify SyncSyntheticScarsScript accepts only --db-path and rejects all obsolete flags with exit 2."""
    self.assertTrue(issubclass(SyncSyntheticScarsScript, BaseScript))

    obsolete_flag_cases = [
        ["--no-fetch"],
        ["--export-pending"],
        ["--apply-transformed", "file.json"],
        ["--import-simple-english", "file.json"],
        ["--migrate-ontology"],
        ["--enrich"],
        ["--status"],
        ["--verify-transformed"],
        ["--query", "BaseScript"],
        ["--batch-size", "5"],
        ["--force"],
        ["--url", "https://example.com/registry.json"],
        ["--model", "gemini-2.5-pro"],
        ["--max-retries", "2"],
        ["--agent", "gemini"],
        ["--limit", "3"],
    ]
    for argv in obsolete_flag_cases:
      with self.subTest(argv=argv):
        out = io.StringIO()
        err = io.StringIO()
        code = SyncSyntheticScarsScript().execute(argv, stdout=out, stderr=err)
        self.assertEqual(code, 2, f"Expected exit 2 for {argv}, got {code}")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Error:", err.getvalue())

  def test_lossless_one_time_raw_hash_backfill_preserves_transformed_scars(
      self,
  ) -> None:
    """Verify one-time 4-field raw_hash backfill preserves transformed rows on first sync."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "scars.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      _insert_scar_row(
          conn,
          scar_id="SCAR-ARCH-0001",
          ss_id="SCAR-ARCH-01",
          title="Old Title",
          raw_wound="Unchanged raw wound.",
          raw_trap="Unchanged raw trap.",
          raw_circuit_breaker="Unchanged raw breaker.",
          raw_reflex="Unchanged raw reflex.",
          raw_hash="legacy-md5-digest-from-old-sync",
          wound="Existing Simple English wound.",
          trap="Existing Simple English trap.",
          circuit_breaker="Existing Simple English breaker.",
          reflex="Existing Simple English reflex.",
          socratic_probe="Did you keep existing probe?",
      )
      conn.close()

      # Upstream keeps identical 4 raw prose fields even though title changed
      upstream_payload = json.dumps({
          "scars": [
              _make_upstream_scar(
                  ss_id="SCAR-ARCH-01",
                  title="Renamed Upstream Title",
                  wound="Unchanged raw wound.",
                  trap="Unchanged raw trap.",
                  circuit_breaker="Unchanged raw breaker.",
                  reflex="Unchanged raw reflex.",
              )
          ]
      })

      out = io.StringIO()
      err = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(upstream_payload),
      ):
        code = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out, stderr=err
        )

      self.assertEqual(code, 0, f"Unexpected stderr: {err.getvalue()}")
      conn = sqlite3.connect(db_path)
      cur = conn.cursor()
      cur.execute(
          "SELECT value FROM meta WHERE key = 'raw_hash_version'"
      )
      self.assertEqual(cur.fetchone()[0], RAW_HASH_VERSION)

      expected_hash = compute_raw_fields_hash(
          "Unchanged raw wound.",
          "Unchanged raw trap.",
          "Unchanged raw breaker.",
          "Unchanged raw reflex.",
      )
      cur.execute(
          """
          SELECT id, raw_hash, wound, trap, circuit_breaker, reflex, socratic_probe
          FROM scars WHERE ss_id = 'SCAR-ARCH-01'
          """
      )
      row = cur.fetchone()
      conn.close()

      self.assertEqual(row[0], "SCAR-ARCH-0001")
      self.assertEqual(row[1], expected_hash)
      self.assertEqual(row[2], "Existing Simple English wound.")
      self.assertEqual(row[3], "Existing Simple English trap.")
      self.assertEqual(row[4], "Existing Simple English breaker.")
      self.assertEqual(row[5], "Existing Simple English reflex.")
      self.assertEqual(row[6], "Did you keep existing probe?")

      # Second backfill call is a no-op (one-time)
      conn = sqlite3.connect(db_path)
      self.assertEqual(backfill_raw_hashes_if_needed(conn), 0)
      conn.close()

  def test_matched_ss_id_changed_raw_updates_in_place_and_nulls_transformed_fields(
      self,
  ) -> None:
    """Verify changed 4-field checksum updates matched ss_id in place without changing id and NULLs prose."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "scars.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      _insert_scar_row(
          conn,
          scar_id="SCAR-CUSTOM-ID-0042",
          ss_id="SCAR-ARCH-05",
          title="Old Title",
          raw_wound="Original wound.",
          raw_trap="Original trap.",
          raw_circuit_breaker="Original breaker.",
          raw_reflex="Original reflex.",
          wound="Old Simple wound.",
          trap="Old Simple trap.",
          circuit_breaker="Old Simple breaker.",
          reflex="Old Simple reflex.",
          socratic_probe="Old probe?",
      )
      backfill_raw_hashes_if_needed(conn)
      conn.close()

      upstream_payload = json.dumps({
          "scars": [
              _make_upstream_scar(
                  ss_id="SCAR-ARCH-05",
                  title="Updated Upstream Title",
                  wound="Revised upstream wound.",
                  trap="Original trap.",
                  circuit_breaker="Original breaker.",
                  reflex="Original reflex.",
              )
          ]
      })

      out = io.StringIO()
      err = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(upstream_payload),
      ):
        code = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out, stderr=err
        )

      self.assertEqual(code, 0, f"Unexpected stderr: {err.getvalue()}")
      conn = sqlite3.connect(db_path)
      cur = conn.cursor()
      cur.execute(
          """
          SELECT id, ss_id, title, raw_wound, raw_hash,
                 wound, trap, circuit_breaker, reflex, socratic_probe
          FROM scars
          """
      )
      rows = cur.fetchall()
      conn.close()

      self.assertEqual(len(rows), 1)
      row = rows[0]
      # Makeflow primary key id MUST remain unchanged in place
      self.assertEqual(row[0], "SCAR-CUSTOM-ID-0042")
      self.assertEqual(row[1], "SCAR-ARCH-05")
      self.assertEqual(row[2], "Updated Upstream Title")
      self.assertEqual(row[3], "Revised upstream wound.")
      self.assertEqual(
          row[4],
          compute_raw_fields_hash(
              "Revised upstream wound.",
              "Original trap.",
              "Original breaker.",
              "Original reflex.",
          ),
      )
      # All five transformed fields MUST be SQL NULL (None)
      self.assertIsNone(row[5])
      self.assertIsNone(row[6])
      self.assertIsNone(row[7])
      self.assertIsNone(row[8])
      self.assertIsNone(row[9])

  def test_unmatched_ss_id_sorted_ascending_deterministic_ids_makeflow_untouched_and_counts(
      self,
  ) -> None:
    """Verify unmatched ss_id sorting, deterministic IDs, makeflow preservation, FTS/meta, and exit counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "scars.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      # 1. Existing Makeflow-owned row (complete)
      _insert_scar_row(
          conn,
          scar_id="SCAR-ARCH-0009",
          ss_id=None,
          title="Makeflow Local Scar",
          category="ARCH",
          raw_wound="Makeflow wound.",
          raw_trap="Makeflow trap.",
          raw_circuit_breaker="",
          raw_reflex="Makeflow reflex.",
          wound="Makeflow simple wound.",
          trap="Makeflow simple trap.",
          circuit_breaker=None,
          reflex="Makeflow simple reflex.",
          socratic_probe="Did you keep Makeflow scar?",
          source="makeflow",
      )
      # 2. Existing upstream row needing only socratic-probe-writer
      _insert_scar_row(
          conn,
          scar_id="SCAR-TEST-0001",
          ss_id="SCAR-TEST-01",
          title="Existing Ready For Probe",
          category="TEST",
          raw_wound="Ready wound.",
          raw_trap="Ready trap.",
          raw_circuit_breaker="",
          raw_reflex="Ready reflex.",
          wound="Simple ready wound.",
          trap="Simple ready trap.",
          circuit_breaker=None,
          reflex="Simple ready reflex.",
          socratic_probe=None,
          source="upstream",
      )
      backfill_raw_hashes_if_needed(conn)
      conn.close()

      # Provide two unmatched upstream scars in REVERSE order to verify ascending sort
      upstream_payload = json.dumps({
          "scars": [
              _make_upstream_scar(
                  ss_id="SCAR-TEST-01",
                  title="Existing Ready For Probe",
                  category="TEST",
                  wound="Ready wound.",
                  trap="Ready trap.",
                  reflex="Ready reflex.",
              ),
              _make_upstream_scar(
                  ss_id="SCAR-NEW-02",
                  title="Second Unseen Scar",
                  category="API",
                  wound="Second raw wound.",
                  trap="Second raw trap.",
                  reflex="Second raw reflex.",
              ),
              _make_upstream_scar(
                  ss_id="SCAR-NEW-01",
                  title="First Unseen Scar",
                  category="API",
                  wound="First raw wound.",
                  trap="First raw trap.",
                  circuit_breaker="Stop if broken.",
                  reflex="First raw reflex.",
              ),
          ]
      })

      out = io.StringIO()
      err = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(upstream_payload),
      ):
        code = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out, stderr=err
        )

      self.assertEqual(code, 0, f"Unexpected stderr: {err.getvalue()}")
      conn = sqlite3.connect(db_path)
      cur = conn.cursor()

      # Makeflow row is untouched
      cur.execute(
          "SELECT title, source, socratic_probe FROM scars WHERE id = 'SCAR-ARCH-0009'"
      )
      mf_row = cur.fetchone()
      self.assertEqual(mf_row, ("Makeflow Local Scar", "makeflow", "Did you keep Makeflow scar?"))

      # Deterministic IDs derived in ss_id ascending order
      cur.execute(
          """
          SELECT ss_id, id, wound, trap, circuit_breaker, reflex, socratic_probe, keywords
          FROM scars
          WHERE ss_id IN ('SCAR-NEW-01', 'SCAR-NEW-02')
          ORDER BY ss_id ASC
          """
      )
      new_rows = cur.fetchall()
      self.assertEqual(len(new_rows), 2)
      self.assertEqual(new_rows[0][0], "SCAR-NEW-01")
      self.assertEqual(new_rows[0][1], "SCAR-API-0001")
      self.assertEqual(new_rows[1][0], "SCAR-NEW-02")
      self.assertEqual(new_rows[1][1], "SCAR-API-0002")
      for row in new_rows:
        for col_idx in (2, 3, 4, 5, 6):
          self.assertIsNone(row[col_idx])
        self.assertTrue(json.loads(row[7]))

      # Verify scars_fts rebuilt and meta.db_size_bytes populated
      cur.execute("SELECT COUNT(*) FROM scars_fts")
      self.assertEqual(cur.fetchone()[0], 4)
      cur.execute("SELECT value FROM meta WHERE key = 'db_size_bytes'")
      self.assertGreater(int(cur.fetchone()[0]), 0)
      conn.close()

      # Verify explicit counts printed at exit:
      # total=4, complete=1 (Makeflow), needs_importer=2 (NEW-01, NEW-02), needs_probe=1 (TEST-01)
      output = out.getvalue()
      self.assertIn("Total scars: 4", output)
      self.assertIn("Complete scars: 1", output)
      self.assertIn("Scars needing synthetic-scar-importer: 2", output)
      self.assertIn("Scars needing socratic-probe-writer: 1", output)

  def test_http_304_and_error_handling(self) -> None:
    """Verify HTTP 304 prints explicit counts and exits 0; HTTP 500 and bad JSON exit 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "scars.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      _insert_scar_row(
          conn,
          scar_id="SCAR-ARCH-0001",
          ss_id="SCAR-ARCH-01",
      )
      backfill_raw_hashes_if_needed(conn)
      conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('etag', '\"v1\"')")
      conn.commit()
      conn.close()

      out_304 = io.StringIO()
      err_304 = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          side_effect=urllib.error.HTTPError(
              "https://syntheticscars.org/registry.json", 304, "Not Modified", {}, None
          ),
      ):
        code_304 = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out_304, stderr=err_304
        )
      self.assertEqual(code_304, 0)
      self.assertIn("Total scars: 1", out_304.getvalue())
      self.assertIn("Complete scars: 1", out_304.getvalue())

      out_500 = io.StringIO()
      err_500 = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          side_effect=urllib.error.HTTPError(
              "https://syntheticscars.org/registry.json", 500, "Server Error", {}, None
          ),
      ):
        code_500 = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out_500, stderr=err_500
        )
      self.assertEqual(code_500, 1)
      self.assertIn("Error:", err_500.getvalue())

      out_bad = io.StringIO()
      err_bad = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse("{not-valid-json"),
      ):
        code_bad = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out_bad, stderr=err_bad
        )
      self.assertEqual(code_bad, 1)
      self.assertIn("Error:", err_bad.getvalue())

  def test_sync_reconciles_curation_flags_resets_on_raw_change_and_deduplicates_makeflow_ssid(
      self,
  ) -> None:
    """Verify makeflow ss_id deduplication, unchanged-hash preservation, changed-hash reset+notice, and curation counts (GH-343)."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "sync_curation.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)

      # 1. Makeflow-owned row claiming an upstream ss_id -> never duplicated or overwritten
      _insert_scar_row(
          conn,
          scar_id="SCAR-GIT-0099",
          ss_id="SCAR-GIT-99",
          title="Makeflow Owned Git Rule",
          category="GIT",
          raw_wound="MF wound.",
          raw_trap="MF trap.",
          raw_reflex="MF reflex.",
          wound="MF simple wound.",
          trap="MF simple trap.",
          reflex="MF simple reflex.",
          socratic_probe="Did you keep makeflow rule?",
          source="makeflow",
      )
      conn.execute(
          "UPDATE scars SET active = 1, vetted = 1, override = 1 WHERE id = 'SCAR-GIT-0099'"
      )

      # 2. Upstream row with override=1, vetted=1, unchanged raw_hash -> preserved
      _insert_scar_row(
          conn,
          scar_id="SCAR-GIT-0018",
          ss_id="SCAR-GIT-18",
          title="Overridden Unchanged Scar",
          category="GIT",
          raw_wound="Stable raw wound.",
          raw_trap="Stable raw trap.",
          raw_reflex="Stable raw reflex.",
          wound="Vetted custom wound.",
          trap="Vetted custom trap.",
          reflex="Vetted custom reflex.",
          socratic_probe="Did you keep vetted override?",
          source="upstream",
      )
      conn.execute(
          "UPDATE scars SET active = 1, vetted = 1, override = 1 WHERE id = 'SCAR-GIT-0018'"
      )

      # 3. Upstream row with active=0, vetted=1, changed raw_hash -> reset + stdout notice
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0010",
          ss_id="SCAR-API-10",
          title="Inactive Changed Scar",
          category="API",
          raw_wound="Old inactive wound.",
          raw_trap="Old inactive trap.",
          raw_reflex="Old inactive reflex.",
          wound="Old simple wound.",
          trap="Old simple trap.",
          reflex="Old simple reflex.",
          socratic_probe="Old probe?",
          source="upstream",
      )
      conn.execute(
          "UPDATE scars SET active = 0, vetted = 1, override = 0 WHERE id = 'SCAR-API-0010'"
      )

      # 4. Upstream row with override=1, vetted=1, changed raw_hash -> reset + stdout notice
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0011",
          ss_id="SCAR-API-11",
          title="Overridden Changed Scar",
          category="API",
          raw_wound="Old override wound.",
          raw_trap="Old override trap.",
          raw_reflex="Old override reflex.",
          wound="Custom override wound.",
          trap="Custom override trap.",
          reflex="Custom override reflex.",
          socratic_probe="Custom override probe?",
          source="upstream",
      )
      conn.execute(
          "UPDATE scars SET active = 1, vetted = 1, override = 1 WHERE id = 'SCAR-API-0011'"
      )

      backfill_raw_hashes_if_needed(conn)
      conn.close()

      upstream_payload = json.dumps({
          "scars": [
              _make_upstream_scar(
                  ss_id="SCAR-GIT-99",
                  title="Upstream Attempt To Duplicate Makeflow",
                  category="GIT",
                  wound="Upstream changed wound.",
                  trap="Upstream changed trap.",
                  reflex="Upstream changed reflex.",
              ),
              _make_upstream_scar(
                  ss_id="SCAR-GIT-18",
                  title="Overridden Unchanged Scar",
                  category="GIT",
                  wound="Stable raw wound.",
                  trap="Stable raw trap.",
                  reflex="Stable raw reflex.",
              ),
              _make_upstream_scar(
                  ss_id="SCAR-API-10",
                  title="Inactive Changed Scar Updated",
                  category="API",
                  wound="New upstream wound for inactive.",
                  trap="Old inactive trap.",
                  reflex="Old inactive reflex.",
              ),
              _make_upstream_scar(
                  ss_id="SCAR-API-11",
                  title="Overridden Changed Scar Updated",
                  category="API",
                  wound="New upstream wound for override.",
                  trap="Old override trap.",
                  reflex="Old override reflex.",
              ),
              _make_upstream_scar(
                  ss_id="SCAR-API-12",
                  title="Brand New Upstream Scar",
                  category="API",
                  wound="New raw wound.",
                  trap="New raw trap.",
                  reflex="New raw reflex.",
              ),
          ]
      })

      out = io.StringIO()
      err = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(upstream_payload),
      ):
        code = SyncSyntheticScarsScript().execute(
            ["--db-path", db_path], stdout=out, stderr=err
        )

      self.assertEqual(code, 0, f"Unexpected stderr: {err.getvalue()}")
      output = out.getvalue()

      conn = sqlite3.connect(db_path)
      cur = conn.cursor()

      # No duplicate inserted for makeflow ss_id='SCAR-GIT-99'
      cur.execute(
          "SELECT id, source, title, wound, active, vetted, override "
          "FROM scars WHERE ss_id = 'SCAR-GIT-99'"
      )
      mf_rows = cur.fetchall()
      self.assertEqual(
          mf_rows,
          [("SCAR-GIT-0099", "makeflow", "Makeflow Owned Git Rule", "MF simple wound.", 1, 1, 1)],
      )

      # Unchanged upstream override preserved verbatim
      cur.execute(
          "SELECT wound, trap, reflex, socratic_probe, active, vetted, override "
          "FROM scars WHERE id = 'SCAR-GIT-0018'"
      )
      self.assertEqual(
          cur.fetchone(),
          (
              "Vetted custom wound.",
              "Vetted custom trap.",
              "Vetted custom reflex.",
              "Did you keep vetted override?",
              1,
              1,
              1,
          ),
      )

      # Changed previously inactive row reset to active=1, vetted=0, override=0 + NULL prose
      cur.execute(
          "SELECT raw_wound, wound, trap, circuit_breaker, reflex, socratic_probe, "
          "active, vetted, override FROM scars WHERE id = 'SCAR-API-0010'"
      )
      self.assertEqual(
          cur.fetchone(),
          ("New upstream wound for inactive.", None, None, None, None, None, 1, 0, 0),
      )

      # Changed previously overridden row reset to active=1, vetted=0, override=0 + NULL prose
      cur.execute(
          "SELECT raw_wound, wound, trap, circuit_breaker, reflex, socratic_probe, "
          "active, vetted, override FROM scars WHERE id = 'SCAR-API-0011'"
      )
      self.assertEqual(
          cur.fetchone(),
          ("New upstream wound for override.", None, None, None, None, None, 1, 0, 0),
      )

      # New upstream row inserted with defaults active=1, vetted=0, override=0
      cur.execute(
          "SELECT active, vetted, override FROM scars WHERE ss_id = 'SCAR-API-12'"
      )
      self.assertEqual(cur.fetchone(), (1, 0, 0))
      conn.close()

      # Explicit stdout reset notices for both previously inactive and overridden rows
      self.assertIn("SCAR-API-0010", output)
      self.assertIn("SCAR-API-10", output)
      self.assertIn("SCAR-API-0011", output)
      self.assertIn("SCAR-API-11", output)

      # Existing 4 lines + appended curation count lines
      self.assertIn("Total scars: 5", output)
      self.assertIn("Complete scars: 2", output)
      self.assertIn("Scars needing synthetic-scar-importer: 3", output)
      self.assertIn("Scars needing socratic-probe-writer: 0", output)
      self.assertIn("Active scars: 5", output)
      self.assertIn("Inactive scars: 0", output)
      self.assertIn("Vetted scars: 2", output)
      self.assertIn("Unvetted scars: 3", output)
      self.assertIn("Overridden scars: 2", output)


class TestQueryScarsCircuitBreakerAndFilters(unittest.TestCase):
  """Verify query_scars() conditional circuit_breaker filter, FTS5/LIKE, stacking, and session ledger."""

  def test_query_scars_conditional_circuit_breaker_completeness(self) -> None:
    """Verify query_scars() excludes rows missing required circuit_breaker while allowing empty when raw is empty."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "cb_filter.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      # 1. Has raw_circuit_breaker, missing circuit_breaker (NULL) -> MUST BE EXCLUDED
      _insert_scar_row(
          conn,
          scar_id="SCAR-CB-0001",
          ss_id="SCAR-CB-01",
          title="Missing Required Breaker Null",
          raw_circuit_breaker="Stop immediately on failure.",
          circuit_breaker=None,
      )
      # 2. Has raw_circuit_breaker, empty circuit_breaker ('') -> MUST BE EXCLUDED
      _insert_scar_row(
          conn,
          scar_id="SCAR-CB-0002",
          ss_id="SCAR-CB-02",
          title="Missing Required Breaker Empty",
          raw_circuit_breaker="Stop immediately on failure.",
          circuit_breaker="   ",
      )
      # 3. Has raw_circuit_breaker AND non-empty circuit_breaker -> INCLUDED
      _insert_scar_row(
          conn,
          scar_id="SCAR-CB-0003",
          ss_id="SCAR-CB-03",
          title="Complete With Breaker",
          raw_circuit_breaker="Stop immediately on failure.",
          circuit_breaker="If failure occurs, stop immediately.",
      )
      # 4. Empty raw_circuit_breaker AND NULL circuit_breaker -> INCLUDED
      _insert_scar_row(
          conn,
          scar_id="SCAR-CB-0004",
          ss_id="SCAR-CB-04",
          title="Complete Without Breaker Null",
          raw_circuit_breaker="",
          circuit_breaker=None,
      )
      # 5. NULL raw_circuit_breaker AND empty circuit_breaker -> INCLUDED
      _insert_scar_row(
          conn,
          scar_id="SCAR-CB-0005",
          ss_id="SCAR-CB-05",
          title="Complete Without Breaker Empty",
          raw_circuit_breaker=None,
          circuit_breaker="",
      )
      conn.close()
      enrich_scars_database(db_path)

      # FTS5 query path
      fts_matches = query_scars(db_path, query="Breaker", limit=10)
      fts_ids = [m["id"] for m in fts_matches]
      self.assertNotIn("SCAR-CB-0001", fts_ids)
      self.assertNotIn("SCAR-CB-0002", fts_ids)
      self.assertIn("SCAR-CB-0003", fts_ids)
      self.assertIn("SCAR-CB-0004", fts_ids)
      self.assertIn("SCAR-CB-0005", fts_ids)

      # Filter-only LIKE/fallback path
      filter_matches = query_scars(db_path, category="ARCH", limit=10)
      filter_ids = [m["id"] for m in filter_matches]
      self.assertEqual(
          sorted(filter_ids),
          ["SCAR-CB-0003", "SCAR-CB-0004", "SCAR-CB-0005"],
      )

  def test_query_scars_legacy_db_without_raw_circuit_breaker_column(self) -> None:
    """Verify query_scars() remains compatible with legacy test DBs lacking raw_circuit_breaker."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "legacy.db")
      conn = sqlite3.connect(db_path)
      conn.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT
          )
          """
      )
      conn.execute(
          """
          INSERT INTO scars VALUES (
              'SCAR-LEG-01', 'Legacy Scar', 'ARCH', 'arch', 'public',
              '["Target 30: tdd"]', 'Python', 'graduated',
              'Legacy wound.', 'Legacy trap.', '', 'Legacy reflex.', 'Did you check legacy?'
          )
          """
      )
      conn.commit()
      conn.close()

      matches = query_scars(db_path, query="Legacy", limit=5)
      self.assertEqual(len(matches), 1)
      self.assertEqual(matches[0]["id"], "SCAR-LEG-01")

  def test_path_resolution_stacking_and_session_ledger(self) -> None:
    """Verify resolve_scars_db_path(s), stacked deduplication, exclude_categories, and session ledger."""
    with tempfile.TemporaryDirectory() as tmpdir:
      explicit = os.path.join(tmpdir, "explicit.db")
      self.assertEqual(resolve_scars_db_path(db_path=explicit), os.path.abspath(explicit))

      db_high = os.path.join(tmpdir, "high.db")
      db_low = os.path.join(tmpdir, "low.db")
      for path, title, category in (
          (db_high, "High Priority Scar", "ARCH"),
          (db_low, "Low Priority Duplicate", "ARCH"),
      ):
        conn = sqlite3.connect(path)
        init_scars_db_schema(conn)
        _insert_scar_row(
            conn,
            scar_id="SCAR-ARCH-0001",
            ss_id="SCAR-ARCH-01",
            title=title,
            category=category,
        )
        conn.close()
        enrich_scars_database(path)

      # Add a workflow scar to db_low to verify exclude_categories=["WORKFLOW"]
      conn = sqlite3.connect(db_low)
      _insert_scar_row(
          conn,
          scar_id="SCAR-PROC-0001",
          ss_id="SCAR-PROC-01",
          title="Workflow Process Scar",
          category="PROC",
      )
      conn.close()
      enrich_scars_database(db_low)

      paths = resolve_scars_db_paths(db_path=db_high)
      self.assertEqual(paths[0], os.path.abspath(db_high))

      stacked = query_scars(
          [db_high, db_low],
          category="ARCH",
          exclude_categories=["WORKFLOW"],
          limit=5,
      )
      self.assertEqual(len(stacked), 1)
      self.assertEqual(stacked[0]["title"], "High Priority Scar")

      # Session ledger load/save/partition
      state_dir = os.path.join(tmpdir, "state")
      os.makedirs(state_dir, exist_ok=True)
      save_session_scars(["SCAR-ARCH-0001"], state_dir=state_dir)
      loaded = load_session_scars(state_dir=state_dir)
      self.assertEqual(loaded, {"SCAR-ARCH-0001"})
      selected, reminders, updated = partition_session_scars(
          stacked, loaded, limit=3, refresh_loaded=False
      )
      self.assertEqual(len(reminders), 1)
      self.assertTrue(selected[0]["alreadyLoaded"])
      self.assertIn("SCAR-ARCH-0001", updated)

  def test_extract_protected_spans_and_verify_scar_transformation(self) -> None:
    """Verify span extraction and transformation verification helpers in lib/scars_db.py."""
    spans = extract_protected_spans(
        "Check `inline $x$` and $a + b$ and $$E = mc^2$$ and ```py\nx=1\n```."
    )
    self.assertEqual(spans["inline_math"], ["$a + b$"])
    self.assertEqual(spans["display_math"], ["$$E = mc^2$$"])
    self.assertEqual(spans["fenced_code"], ["```py\nx=1\n```"])

    raw_row = {
        "raw_wound": "Wound $x$.",
        "raw_trap": "Trap.",
        "raw_circuit_breaker": "",
        "raw_reflex": "Reflex.",
    }
    good = {
        "wound": "Simple wound $x$.",
        "trap": "Simple trap.",
        "circuit_breaker": "",
        "reflex": "Simple reflex.",
        "socratic_probe": "Did you check $x$?",
    }
    self.assertEqual(verify_scar_transformation(raw_row, good), [])
    bad = dict(good, wound="Missing formula.")
    self.assertTrue(verify_scar_transformation(raw_row, bad))


class TestScarCurationFlagsSchemaAndQueries(unittest.TestCase):
  """Verify active, vetted, and override curation columns, selectors, counts, and query filtering (GH-343)."""

  def test_init_scars_db_schema_migrates_curation_columns_idempotently(self) -> None:
    """Verify init_scars_db_schema adds active, vetted, and override with defaults without altering rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "legacy_schema.db")
      conn = sqlite3.connect(db_path)
      conn.execute(
          """
          CREATE TABLE scars (
              id TEXT NOT NULL PRIMARY KEY,
              ss_id TEXT,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              raw_wound TEXT,
              raw_trap TEXT,
              raw_circuit_breaker TEXT,
              raw_reflex TEXT,
              raw_hash TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT,
              source TEXT DEFAULT 'upstream',
              original_id TEXT
          )
          """
      )
      conn.execute(
          """
          INSERT INTO scars (
              id, ss_id, title, category, category_slug, visibility,
              workflow_phases, applies_when, graduation_status,
              raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
              wound, trap, circuit_breaker, reflex, socratic_probe, source
          ) VALUES (
              'SCAR-ARCH-0001', 'SCAR-ARCH-01', 'Existing Scar', 'ARCH', 'arch',
              'public', '["Target 30: tdd"]', 'Python', 'graduated',
              'Raw wound.', 'Raw trap.', '', 'Raw reflex.', 'hash-1',
              'Simple wound.', 'Simple trap.', '', 'Simple reflex.',
              'Did you check?', 'upstream'
          )
          """
      )
      conn.commit()

      init_scars_db_schema(conn)
      # Second call must be strictly idempotent.
      init_scars_db_schema(conn)

      cur = conn.cursor()
      cur.execute("PRAGMA table_info(scars)")
      col_info = {row[1]: row for row in cur.fetchall()}
      self.assertIn("active", col_info)
      self.assertIn("vetted", col_info)
      self.assertIn("override", col_info)
      self.assertEqual(col_info["active"][4], "1")
      self.assertEqual(col_info["vetted"][4], "0")
      self.assertEqual(col_info["override"][4], "0")

      cur.execute(
          "SELECT title, wound, active, vetted, override FROM scars WHERE id = 'SCAR-ARCH-0001'"
      )
      self.assertEqual(
          cur.fetchone(), ("Existing Scar", "Simple wound.", 1, 0, 0)
      )
      conn.close()

  def test_query_scars_filters_inactive_rows_and_auto_migrates_legacy_db(
      self,
  ) -> None:
    """Verify query_scars omits active=0 on FTS5/LIKE paths and auto-migrates legacy DBs lacking active."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "curation_query.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      _insert_scar_row(
          conn,
          scar_id="SCAR-ARCH-0001",
          ss_id="SCAR-ARCH-01",
          title="Active Nebula Guard",
          category="ARCH",
          wound="Active nebula failure.",
          trap="Active nebula trap.",
          reflex="Apply active nebula guard.",
          socratic_probe="Did you apply active nebula guard?",
      )
      _insert_scar_row(
          conn,
          scar_id="SCAR-ARCH-0002",
          ss_id="SCAR-ARCH-02",
          title="Ignored Nebula Guard",
          category="ARCH",
          wound="Ignored nebula failure.",
          trap="Ignored nebula trap.",
          reflex="Apply ignored nebula guard.",
          socratic_probe="Did you apply ignored nebula guard?",
      )
      conn.execute(
          "UPDATE scars SET active = 0, vetted = 1 WHERE id = 'SCAR-ARCH-0002'"
      )
      conn.commit()
      conn.close()
      enrich_scars_database(db_path)

      # FTS5 path excludes active = 0.
      fts_res = query_scars(db_path, query="Nebula", limit=10)
      self.assertEqual([r["id"] for r in fts_res], ["SCAR-ARCH-0001"])

      # LIKE / filter-only path excludes active = 0.
      filter_res = query_scars(db_path, category="ARCH", limit=10)
      self.assertEqual([r["id"] for r in filter_res], ["SCAR-ARCH-0001"])

      # Legacy DB without active/vetted/override columns auto-migrates on query.
      legacy_path = os.path.join(tmpdir, "legacy_missing_curation.db")
      lconn = sqlite3.connect(legacy_path)
      lconn.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT
          )
          """
      )
      lconn.execute(
          """
          INSERT INTO scars VALUES (
              'SCAR-LEG-99', 'Legacy Auto Migrate Scar', 'ARCH', 'arch', 'public',
              '["Target 30: tdd"]', 'Python', 'graduated',
              'Legacy wound.', 'Legacy trap.', '', 'Legacy reflex.', 'Did you migrate?'
          )
          """
      )
      lconn.commit()
      lconn.close()

      legacy_res = query_scars(legacy_path, query="Migrate", limit=5)
      self.assertEqual([r["id"] for r in legacy_res], ["SCAR-LEG-99"])

      verify_conn = sqlite3.connect(legacy_path)
      verify_cur = verify_conn.cursor()
      verify_cur.execute("PRAGMA table_info(scars)")
      migrated_cols = {row[1] for row in verify_cur.fetchall()}
      self.assertTrue({"active", "vetted", "override"}.issubset(migrated_cols))
      verify_cur.execute(
          "SELECT active, vetted, override FROM scars WHERE id = 'SCAR-LEG-99'"
      )
      self.assertEqual(verify_cur.fetchone(), (1, 0, 0))
      verify_conn.close()

  def test_query_scars_falls_back_without_active_filter_on_read_only_legacy_db(
      self,
  ) -> None:
    """Verify an unmigratable read-only legacy DB still returns rows and stays unmodified (SCAR-TEST-24)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
      self.skipTest("root bypasses read-only file permissions")
    with tempfile.TemporaryDirectory() as tmpdir:
      legacy_path = os.path.join(tmpdir, "legacy_read_only.db")
      lconn = sqlite3.connect(legacy_path)
      lconn.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT
          )
          """
      )
      lconn.execute(
          """
          INSERT INTO scars VALUES (
              'SCAR-LEG-98', 'Read Only Legacy Scar', 'ARCH', 'arch', 'public',
              '[\"Target 30: tdd\"]', 'Python', 'graduated',
              'Legacy wound.', 'Legacy trap.', '', 'Legacy reflex.', 'Did you read?'
          )
          """
      )
      lconn.commit()
      lconn.close()
      with open(legacy_path, "rb") as fh:
        before_bytes = fh.read()
      os.chmod(legacy_path, 0o444)

      try:
        res = query_scars(legacy_path, category="ARCH", limit=5)
      finally:
        os.chmod(legacy_path, 0o644)

      self.assertEqual([r["id"] for r in res], ["SCAR-LEG-98"])
      with open(legacy_path, "rb") as fh:
        self.assertEqual(fh.read(), before_bytes)
      verify_conn = sqlite3.connect(legacy_path)
      cols = {r[1] for r in verify_conn.execute("PRAGMA table_info(scars)")}
      verify_conn.close()
      self.assertFalse({"active", "vetted", "override"} & cols)

  def test_selectors_skip_overridden_and_inactive_and_counts_report_curation(
      self,
  ) -> None:
    """Verify importer/probe selectors skip override=1 or active=0 rows and status counts include curation keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
      db_path = os.path.join(tmpdir, "selectors.db")
      conn = sqlite3.connect(db_path)
      init_scars_db_schema(conn)
      # 1. Pending importer (active=1, vetted=0, override=0) -> INCLUDED by importer
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0001",
          ss_id="SCAR-API-01",
          wound=None,
          trap=None,
          reflex=None,
          socratic_probe=None,
      )
      # 2. Missing prose but override=1 (vetted=1) -> SKIPPED by importer
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0002",
          ss_id="SCAR-API-02",
          wound=None,
          trap="Trap.",
          reflex="Reflex.",
          socratic_probe=None,
      )
      conn.execute(
          "UPDATE scars SET override = 1, vetted = 1 WHERE id = 'SCAR-API-0002'"
      )
      # 3. Missing prose and active=0 (vetted=1) -> SKIPPED by importer
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0003",
          ss_id="SCAR-API-03",
          wound=None,
          trap=None,
          reflex=None,
          socratic_probe=None,
      )
      conn.execute(
          "UPDATE scars SET active = 0, vetted = 1 WHERE id = 'SCAR-API-0003'"
      )
      # 4. Pending probe writer (active=1, override=0) -> INCLUDED by probe writer
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0004",
          ss_id="SCAR-API-04",
          socratic_probe=None,
      )
      # 5. Missing probe but override=1 -> SKIPPED by probe writer
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0005",
          ss_id="SCAR-API-05",
          socratic_probe=None,
      )
      conn.execute(
          "UPDATE scars SET override = 1, vetted = 1 WHERE id = 'SCAR-API-0005'"
      )
      # 6. Missing probe and active=0 -> SKIPPED by probe writer
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0006",
          ss_id="SCAR-API-06",
          socratic_probe=None,
      )
      conn.execute(
          "UPDATE scars SET active = 0, vetted = 0 WHERE id = 'SCAR-API-0006'"
      )
      # 7. Complete active vetted row
      _insert_scar_row(
          conn,
          scar_id="SCAR-API-0007",
          ss_id="SCAR-API-07",
      )
      conn.execute(
          "UPDATE scars SET active = 1, vetted = 1, override = 0 WHERE id = 'SCAR-API-0007'"
      )
      conn.commit()

      cur = conn.cursor()
      importer_ids = [r["id"] for r in select_scars_needing_importer(cur)]
      probe_ids = [r["id"] for r in select_scars_needing_probe_writer(cur)]
      counts = count_scar_pipeline_status(cur)
      conn.close()

      self.assertEqual(importer_ids, ["SCAR-API-0001"])
      self.assertEqual(probe_ids, ["SCAR-API-0004"])
      self.assertEqual(
          counts,
          {
              "total": 7,
              "complete": 1,
              "needs_importer": 1,
              "needs_probe_writer": 1,
              "active": 5,
              "inactive": 2,
              "vetted": 4,
              "unvetted": 3,
              "overridden": 2,
          },
      )


class TestScarOntologyAndBundledDb(unittest.TestCase):
  """Verify ontology invariants and read-only bundled DB preservation."""

  def test_ontology_and_shift_left_targets(self) -> None:
    """Verify 68 domain codes, canonical IDs, and shift-left targets never shift right."""
    self.assertGreaterEqual(len(DOMAIN_CATEGORY_ONTOLOGY), 68)
    self.assertIn("FLUTTER", UPSTREAM_CATEGORY_MAP)
    self.assertEqual(normalize_domain_category("Architecture"), "ARCH")

    reserved = set()
    cid = compute_canonical_scar_id(
        raw_id="SCAR-ARCH-05",
        domain="ARCH",
        languages=["python"],
        reserved_ids=reserved,
    )
    self.assertEqual(cid, "SCAR-LANG-PYTHON-ARCH-0005")
    self.assertEqual(parse_upstream_targets(["Target 30: tdd"]), [30])
    mf_targets, _ = compute_makeflow_targets(["Target 30: tdd"], domain="ARCH")
    self.assertIn(15, mf_targets)
    self.assertIn(20, mf_targets)
    self.assertNotIn(40, mf_targets)
    excluded = resolve_excluded_category_tokens(["WORKFLOW"])
    self.assertIn("PROC", excluded)

  def test_bundled_db_copy_preserves_makeflow_scars_on_sync(self) -> None:
    """Verify syncing against a temporary copy of bundled scars_registry.db preserves makeflow scars."""
    bundled = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled))
    with tempfile.TemporaryDirectory() as tmpdir:
      temp_db = os.path.join(tmpdir, "bundled_copy.db")
      shutil.copyfile(bundled, temp_db)

      out = io.StringIO()
      err = io.StringIO()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(json.dumps({"scars": []})),
      ):
        code = SyncSyntheticScarsScript().execute(
            ["--db-path", temp_db], stdout=out, stderr=err
        )
      self.assertEqual(code, 0, f"Unexpected stderr: {err.getvalue()}")
      conn = sqlite3.connect(temp_db)
      cur = conn.cursor()
      cur.execute(
          "SELECT id FROM scars WHERE source = 'makeflow' ORDER BY id ASC"
      )
      mf_ids = [r[0] for r in cur.fetchall()]
      conn.close()
      self.assertGreaterEqual(len(mf_ids), 1)
      self.assertIn("Total scars:", out.getvalue())

  def test_bundled_scar_git_0018_vetted_override_and_vetting_notes(self) -> None:
    """Verify bundled SCAR-GIT-0018 override prose, flags, FTS5, metadata, and notes/sync_scars.md vetting prompt (GH-343)."""
    bundled = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled))
    conn = sqlite3.connect(f"file:{bundled}?mode=ro", uri=True)
    try:
      conn.row_factory = sqlite3.Row
      cur = conn.cursor()
      cur.execute("SELECT * FROM scars WHERE id = 'SCAR-GIT-0018'")
      row_obj = cur.fetchone()
      self.assertIsNotNone(row_obj)
      row = dict(row_obj)

      cur.execute("SELECT value FROM meta WHERE key = 'db_size_bytes'")
      size_row = cur.fetchone()
      self.assertIsNotNone(size_row)
      self.assertGreater(int(size_row[0]), 0)
    finally:
      conn.close()

    self.assertEqual(row["source"], "upstream")
    self.assertEqual(row["ss_id"], "SCAR-GIT-18")
    self.assertEqual(
        row["raw_hash"],
        "d3e088e236bc0c46a86e1fda6620caea288c5a135e9c181404144bb012525d68",
    )
    self.assertEqual(row["active"], 1)
    self.assertEqual(row["vetted"], 1)
    self.assertEqual(row["override"], 1)
    self.assertNotIn("zero technical risk", (row["trap"] or "").lower())
    self.assertIn("trial-merge", (row["reflex"] or "").lower())
    self.assertIn("ephemeral", (row["reflex"] or "").lower())
    self.assertIn("worktree", (row["socratic_probe"] or "").lower())
    self.assertEqual(ScarCandidateValidator().validate(row), [])
    self.assertEqual(
        validate_socratic_probe(row["socratic_probe"], "SCAR-GIT-0018"), []
    )

    # FTS5 query discovers updated SCAR-GIT-0018 by new combined-tree terms and ss_id
    by_trial = query_scars(bundled, query="trial-merge", limit=10)
    self.assertIn("SCAR-GIT-0018", [r["id"] for r in by_trial])
    by_ssid = query_scars(bundled, query="SCAR-GIT-18", limit=5)
    self.assertIn("SCAR-GIT-0018", [r["id"] for r in by_ssid])

    # notes/sync_scars.md documents vetting session instructions and copy-paste agy prompt
    notes_path = os.path.join(_REPO_ROOT, "notes", "sync_scars.md")
    with open(notes_path, "r", encoding="utf-8") as fh:
      notes_text = fh.read()
    self.assertIn("vetted = 1", notes_text)
    self.assertIn("override = 1", notes_text)
    self.assertIn("active = 0", notes_text)
    self.assertIn("agy", notes_text)


if __name__ == "__main__":
  unittest.main()
