#!/usr/bin/env python3
"""Hermetic unit and integration tests for synthetic scars SQLite sync and query (GH-118).

Tests:
1. BaseScript lifecycle (Template Method pattern, SCAR-ARCH-05).
2. Cross-agent database path resolution (Gemini, Claude, Cursor, Copilot, ChatGPT, XDG).
3. HTTP initial sync (200 OK) with SQLite schema and FTS5 indexing.
4. HTTP ETag caching (304 Not Modified) preventing duplicate rewrites.
5. CLI status reporting (--status) for uninitialized and initialized databases.
6. JIT query_scars full-text search, category and language filtering, and limits.
7. Fallback search when FTS5 virtual tables are unavailable.
8. Error handling for network and JSON serialization failures.
"""

import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

# Ensure scripts directory and repo root are on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

from lib.base_script import BaseScript
import lib.scars_db as sync_synthetic_scars
from lib.scars_db import (
    SyncSyntheticScarsScript,
    query_scars,
    resolve_scars_db_path,
)

SAMPLE_SCARS = [
    {
        "id": "SCAR-ARCH-05",
        "title": "BaseScript Lifecycle Template Method Pattern",
        "category": "Architecture",
        "categorySlug": "architecture",
        "visibility": "public",
        "workflowPhases": ["T30", "T50"],
        "appliesWhen": "Implementing CLI scripts or command runners in Python",
        "graduationStatus": "graduated",
        "wound": "Direct sys.exit calls in business logic prevent test execution",
        "trap": "Running CLI scripts that terminate test suites",
        "circuitBreaker": "Wrap all CLI scripts in BaseScript execute template",
        "reflex": "Inherit from BaseScript and override run and cleanup",
    },
    {
        "id": "SCAR-TEST-08",
        "title": "Hermetic In-Memory Test Isolation",
        "category": "Testing",
        "categorySlug": "testing",
        "visibility": "public",
        "workflowPhases": ["T30"],
        "appliesWhen": "Writing unit and integration tests without network sockets",
        "graduationStatus": "graduated",
        "wound": "Tests connecting to live databases or remote APIs fail intermittently",
        "trap": "Using unmocked socket connections or live databases in unit tests",
        "circuitBreaker": "Use tempfile.TemporaryDirectory and mock urllib.request",
        "reflex": "Point tests to temporary directories or in-memory SQLite fixtures",
    },
    {
        "id": "SCAR-LANG-01",
        "title": "Python AST Scrubber Strict Formatting",
        "category": "Languages",
        "categorySlug": "languages",
        "visibility": "public",
        "workflowPhases": ["T30", "T45"],
        "appliesWhen": "Checking Python source files for AST syntax validity",
        "graduationStatus": "experimental",
        "wound": "Syntax errors and invalid imports break automated pipelines",
        "trap": "Deploying Python code without pre-flight AST parsing",
        "circuitBreaker": "Run ast_scrub.py before running tests",
        "reflex": "Execute python3 scripts/ast_scrub.py --strict",
    },
]


class MockHTTPResponse:
  """Mock response object for urllib.request.urlopen."""

  def __init__(self, body_bytes: bytes, etag: str = '"etag-abc-123"', status: int = 200):
    self._body = body_bytes
    self._etag = etag
    self.status = status
    self.headers = {"ETag": etag, "Content-Type": "application/json"}

  def read(self) -> bytes:
    return self._body

  def getcode(self) -> int:
    return self.status

  def info(self):
    return self.headers

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    pass


class TestBaseScriptLifecycle(unittest.TestCase):
  """Tests verifying BaseScript compliance and stream isolation."""

  def test_inherits_base_script(self):
    script = SyncSyntheticScarsScript()
    self.assertIsInstance(script, BaseScript)

  def test_stream_isolation_on_help_or_execution(self):
    script = SyncSyntheticScarsScript()
    out = io.StringIO()
    err = io.StringIO()
    code = script.execute(args=["--help"], stdout=out, stderr=err)
    self.assertEqual(code, 0)
    self.assertIn("usage", out.getvalue().lower())


class TestCrossAgentPathResolution(unittest.TestCase):
  """Tests resolving database paths across agent environments."""

  def test_explicit_db_path_precedence(self):
    custom_path = "/tmp/custom_scars.db"
    resolved = resolve_scars_db_path(db_path=custom_path)
    self.assertEqual(resolved, os.path.abspath(custom_path))

  def test_env_var_precedence(self):
    with patch.dict(os.environ, {"MAKEFLOW_SCARS_DB": "/tmp/env_scars.db"}):
      resolved = resolve_scars_db_path()
      self.assertEqual(resolved, os.path.abspath("/tmp/env_scars.db"))

  def test_workspace_local_precedence_when_present(self):
    with tempfile.TemporaryDirectory() as tmp_ws:
      ws_db_dir = os.path.join(tmp_ws, ".workflow", "cache")
      os.makedirs(ws_db_dir, exist_ok=True)
      ws_db_file = os.path.join(ws_db_dir, "scars_registry.db")
      with open(ws_db_file, "w") as f:
        f.write("")

      with patch.dict(os.environ, {}, clear=True):
        resolved = resolve_scars_db_path(workspace_dir=tmp_ws, package_root=tmp_ws)
        self.assertEqual(resolved, ws_db_file)

  def test_agent_path_resolution(self):
    home = os.path.expanduser("~")
    expected_agents = {
        "gemini": os.path.join(home, ".gemini", "antigravity-cli", "plugin_data", "makeflow", "scars_registry.db"),
        "claude": os.path.join(home, ".claude", "plugin_data", "makeflow", "scars_registry.db"),
        "cursor": os.path.join(home, ".cursor", "plugin_data", "makeflow", "scars_registry.db"),
        "copilot": os.path.join(home, ".copilot", "plugin_data", "makeflow", "scars_registry.db"),
        "chatgpt": os.path.join(home, ".chatgpt", "plugin_data", "makeflow", "scars_registry.db"),
    }
    with tempfile.TemporaryDirectory() as tmp_pkg:
      for agent_name, expected_path in expected_agents.items():
        resolved = resolve_scars_db_path(agent=agent_name, package_root=tmp_pkg)
        self.assertEqual(resolved, expected_path)

  def test_default_xdg_cache_fallback(self):
    home = os.path.expanduser("~")
    expected_default = os.path.join(home, ".cache", "makeflow", "scars_registry.db")
    with tempfile.TemporaryDirectory() as tmp_pkg:
      with patch.dict(os.environ, {}, clear=True), patch("os.path.exists") as mock_exists:
        mock_exists.side_effect = lambda p: p == expected_default
        resolved = resolve_scars_db_path(package_root=tmp_pkg)
        self.assertEqual(resolved, expected_default)

  def test_bundled_database_precedence_when_present(self):
    with tempfile.TemporaryDirectory() as tmp_pkg, tempfile.TemporaryDirectory() as tmp_ws:
      bundled_dir = os.path.join(tmp_pkg, "skills", "makeflow", "data")
      os.makedirs(bundled_dir, exist_ok=True)
      bundled_db = os.path.join(bundled_dir, "scars_registry.db")
      with open(bundled_db, "w") as f:
        f.write("")

      ws_db_dir = os.path.join(tmp_ws, ".workflow", "cache")
      os.makedirs(ws_db_dir, exist_ok=True)
      ws_db = os.path.join(ws_db_dir, "scars_registry.db")
      with open(ws_db, "w") as f:
        f.write("")

      with patch.dict(os.environ, {}, clear=True):
        resolved = resolve_scars_db_path(
            agent="gemini",
            workspace_dir=tmp_ws,
            package_root=tmp_pkg,
        )
        self.assertEqual(resolved, os.path.abspath(bundled_db))

  def test_fallback_when_bundled_database_missing(self):
    with tempfile.TemporaryDirectory() as tmp_pkg, tempfile.TemporaryDirectory() as tmp_ws:
      ws_db_dir = os.path.join(tmp_ws, ".workflow", "cache")
      os.makedirs(ws_db_dir, exist_ok=True)
      ws_db = os.path.join(ws_db_dir, "scars_registry.db")
      with open(ws_db, "w") as f:
        f.write("")

      with patch.dict(os.environ, {}, clear=True):
        resolved_ws = resolve_scars_db_path(
            workspace_dir=tmp_ws,
            package_root=tmp_pkg,
        )
        self.assertEqual(resolved_ws, ws_db)

      home = os.path.expanduser("~")
      expected_agent = os.path.join(
          home, ".gemini", "antigravity-cli", "plugin_data", "makeflow", "scars_registry.db"
      )
      with patch.dict(os.environ, {}, clear=True):
        resolved_agent = resolve_scars_db_path(
            agent="gemini",
            package_root=tmp_pkg,
        )
        self.assertEqual(resolved_agent, expected_agent)

      expected_canonical = os.path.join(
          tmp_pkg, "skills", "makeflow", "data", "scars_registry.db"
      )
      with patch.dict(os.environ, {}, clear=True):
        resolved_default = resolve_scars_db_path(package_root=tmp_pkg)
        self.assertEqual(resolved_default, os.path.abspath(expected_canonical))


class TestSyncLifecycle(unittest.TestCase):
  """Tests HTTP synchronization, ETag caching, and schema population."""

  def setUp(self):
    self.tmp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.tmp_dir.name, "test_scars.db")

  def tearDown(self):
    self.tmp_dir.cleanup()

  def test_initial_sync_200_populates_sqlite_schema_and_fts(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    mock_resp = MockHTTPResponse(payload, etag='"etag-v1"')

    script = SyncSyntheticScarsScript()
    out = io.StringIO()
    err = io.StringIO()

    with patch("urllib.request.urlopen", return_value=mock_resp):
      code = script.execute(args=["--db-path", self.db_path], stdout=out, stderr=err)

    self.assertEqual(code, 0)
    self.assertTrue(os.path.isfile(self.db_path))

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()

    # Verify meta table
    cur.execute("SELECT key, value FROM meta")
    meta = dict(cur.fetchall())
    self.assertEqual(meta.get("etag"), '"etag-v1"')
    self.assertEqual(meta.get("total_scars"), "3")
    self.assertIn("last_synced_at", meta)

    # Verify scars table
    cur.execute("SELECT COUNT(*) FROM scars")
    count = cur.fetchone()[0]
    self.assertEqual(count, 3)

    # Verify scars_fts table
    cur.execute("SELECT COUNT(*) FROM scars_fts")
    fts_count = cur.fetchone()[0]
    self.assertEqual(fts_count, 3)
    conn.close()

  def test_etag_caching_304_skips_table_rewrite(self):
    # Initial sync
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    mock_resp_1 = MockHTTPResponse(payload, etag='"etag-v1"')
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=mock_resp_1):
      code = script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())
    self.assertEqual(code, 0)

    # Record row modification timestamp or insert a marker
    conn = sqlite3.connect(self.db_path)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('canary', 'present')")
    conn.commit()
    conn.close()

    # Second sync: server returns 304 Not Modified
    script2 = SyncSyntheticScarsScript()
    out2 = io.StringIO()
    err2 = io.StringIO()

    http_304 = urllib.error.HTTPError(
        url="https://syntheticscars.org/registry.json",
        code=304,
        msg="Not Modified",
        hdrs={},
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_304) as mock_get:
      code2 = script2.execute(args=["--db-path", self.db_path], stdout=out2, stderr=err2)
      # Assert If-None-Match was passed in request headers
      call_args, _ = mock_get.call_args
      sent_req = call_args[0]
      self.assertEqual(sent_req.get_header("If-none-match"), '"etag-v1"')

    self.assertEqual(code2, 0)
    self.assertIn("304", out2.getvalue())

    # Ensure canary is still present and tables were not wiped
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = 'canary'")
    row = cur.fetchone()
    self.assertIsNotNone(row)
    self.assertEqual(row[0], "present")
    cur.execute("SELECT COUNT(*) FROM scars")
    self.assertEqual(cur.fetchone()[0], 3)
    conn.close()

  def test_force_flag_bypasses_cached_etag(self):
    payload1 = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload1, etag='"etag-v1"')):
      script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())

    # Second sync with --force: request should NOT send If-None-Match
    updated_scars = [SAMPLE_SCARS[0]]
    payload2 = json.dumps(updated_scars).encode("utf-8")
    mock_resp_2 = MockHTTPResponse(payload2, etag='"etag-v2"')

    script2 = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=mock_resp_2) as mock_get:
      code = script2.execute(
          args=["--db-path", self.db_path, "--force"],
          stdout=io.StringIO(),
      )
      call_args, _ = mock_get.call_args
      sent_req = call_args[0]
      self.assertIsNone(sent_req.get_header("If-none-match"))

    self.assertEqual(code, 0)
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM scars")
    self.assertEqual(cur.fetchone()[0], 1)
    conn.close()

  def test_status_flag_reporting(self):
    script = SyncSyntheticScarsScript()
    out = io.StringIO()
    # When DB doesn't exist
    code = script.execute(args=["--db-path", self.db_path, "--status"], stdout=out)
    self.assertEqual(code, 0)
    self.assertIn("not found", out.getvalue().lower())

    # When DB exists
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-v1"')):
      script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())

    out2 = io.StringIO()
    code2 = script.execute(args=["--db-path", self.db_path, "--status"], stdout=out2)
    self.assertEqual(code2, 0)
    self.assertIn("3 scars", out2.getvalue().lower())
    self.assertIn("etag-v1", out2.getvalue())


class TestQueryScars(unittest.TestCase):
  """Tests querying scars via FTS5 and fallback searches."""

  def setUp(self):
    self.tmp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.tmp_dir.name, "query_scars.db")
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-v1"')):
      script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        """
        UPDATE scars
        SET wound = raw_wound,
            trap = raw_trap,
            circuit_breaker = raw_circuit_breaker,
            reflex = raw_reflex,
            socratic_probe = 'Did you verify ' || id || '?'
        """
    )
    conn.commit()
    conn.close()
    script.execute(args=["--db-path", self.db_path, "--enrich"], stdout=io.StringIO())

  def tearDown(self):
    self.tmp_dir.cleanup()

  def test_fts5_keyword_search(self):
    results = query_scars(self.db_path, query="BaseScript")
    self.assertGreaterEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-LANG-PYTHON-ARCH-0005")
    self.assertEqual(results[0]["ssId"], "SCAR-ARCH-05")
    self.assertIn("Template Method", results[0]["title"])

  def test_category_filter(self):
    results = query_scars(self.db_path, query="isolation", category="Testing")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-TEST-0008")
    self.assertEqual(results[0]["ssId"], "SCAR-TEST-08")

    # Mismatched category should return empty
    empty_results = query_scars(self.db_path, query="isolation", category="Architecture")
    self.assertEqual(len(empty_results), 0)

  def test_language_filter(self):
    results = query_scars(self.db_path, query="strict", language="python")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-LANG-PYTHON-AST-0001")
    self.assertEqual(results[0]["ssId"], "SCAR-LANG-01")

  def test_limit_enforcement(self):
    results = query_scars(self.db_path, query="test", limit=1)
    self.assertEqual(len(results), 1)

  def test_uninitialized_and_empty_db_handling(self):
    missing_path = os.path.join(self.tmp_dir.name, "missing.db")
    self.assertEqual(query_scars(missing_path, query="anything"), [])

    empty_path = os.path.join(self.tmp_dir.name, "empty.db")
    sqlite3.connect(empty_path).close()
    self.assertEqual(query_scars(empty_path, query="anything"), [])

  def test_fallback_search_without_fts5(self):
    # Simulate DB without FTS5 table
    fallback_db = os.path.join(self.tmp_dir.name, "fallback.db")
    conn = sqlite3.connect(fallback_db)
    conn.execute("""
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
    """)
    for s in SAMPLE_SCARS:
      conn.execute(
          """
          INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
          (
              s["id"],
              s["title"],
              s["category"],
              s["categorySlug"],
              s["visibility"],
              json.dumps(s["workflowPhases"]),
              s["appliesWhen"],
              s["graduationStatus"],
              s["wound"],
              s["trap"],
              s["circuitBreaker"],
              s["reflex"],
              f"Did you verify {s['id']}?",
          ),
      )
    conn.commit()
    conn.close()

    results = query_scars(fallback_db, query="BaseScript")
    self.assertGreaterEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-ARCH-05")


class TestErrorHandling(unittest.TestCase):
  """Tests network failure and malformed payload handling."""

  def setUp(self):
    self.tmp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.tmp_dir.name, "err_scars.db")

  def tearDown(self):
    self.tmp_dir.cleanup()

  def test_malformed_json_returns_nonzero_exit_code(self):
    mock_resp = MockHTTPResponse(b"NOT JSON", etag='"etag-v1"')
    script = SyncSyntheticScarsScript()
    err = io.StringIO()
    with patch("urllib.request.urlopen", return_value=mock_resp):
      code = script.execute(args=["--db-path", self.db_path], stderr=err)
    self.assertNotEqual(code, 0)
    self.assertIn("Error", err.getvalue())

  def test_http_500_server_error_returns_nonzero(self):
    http_500 = urllib.error.HTTPError(
        url="https://syntheticscars.org/registry.json",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=None,
    )
    script = SyncSyntheticScarsScript()
    err = io.StringIO()
    with patch("urllib.request.urlopen", side_effect=http_500):
      code = script.execute(args=["--db-path", self.db_path], stderr=err)
    self.assertNotEqual(code, 0)
    self.assertIn("Error", err.getvalue())


class TestMultiSourceDeltaSync(unittest.TestCase):
  """Tests verifying multi-source schema, delta sync, and collision reconciliation (GH-136)."""

  def setUp(self):
    self.tmp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.tmp_dir.name, "multi_source.db")

  def tearDown(self):
    self.tmp_dir.cleanup()

  def test_multi_source_schema_columns(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-s1"')):
      code = script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())
    self.assertEqual(code, 0)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(scars)")
    col_names = [r[1] for r in cur.fetchall()]
    self.assertIn("source", col_names)
    self.assertIn("original_id", col_names)
    self.assertIn("raw_hash", col_names)
    self.assertIn("ss_id", col_names)
    self.assertIn("ss_targets", col_names)
    self.assertIn("mf_targets", col_names)

    cur.execute("PRAGMA table_info(scars_fts)")
    fts_cols = [r[1] for r in cur.fetchall()]
    self.assertIn("source", fts_cols)
    self.assertIn("original_id", fts_cols)
    self.assertIn("ss_id", fts_cols)
    conn.close()

  def test_delta_sync_skips_unchanged_rows(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-s1"')):
      code = script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())
    self.assertEqual(code, 0)

    # Verify raw_hash was computed
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, ss_id, raw_hash FROM scars")
    rows = cur.fetchall()
    for scar_id, ss_id, raw_hash in rows:
      self.assertIsNotNone(raw_hash)
      self.assertEqual(len(raw_hash), 32)
      self.assertIsNotNone(ss_id)

    # Inject canary into SCAR-ARCH-05 while leaving raw_hash unchanged
    cur.execute("UPDATE scars SET wound = 'Canary wound' WHERE ss_id = 'SCAR-ARCH-05'")
    conn.commit()
    conn.close()

    # Second sync with --force: SCAR-ARCH-05 payload matches raw_hash, so rewrite is skipped
    script2 = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-s2"')):
      code2 = script2.execute(args=["--db-path", self.db_path, "--force"], stdout=io.StringIO())
    self.assertEqual(code2, 0)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT wound FROM scars WHERE ss_id = 'SCAR-ARCH-05'")
    wound = cur.fetchone()[0]
    conn.close()
    self.assertEqual(wound, "Canary wound")

  def test_collision_reconciliation_renames_makeflow_scar(self):
    # Initial sync to set up tables
    payload = json.dumps(SAMPLE_SCARS[:1]).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-init"')):
      code = script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())
    self.assertEqual(code, 0)

    # Insert a Makeflow scar with ID SCAR-LANG-PERL-001
    conn = sqlite3.connect(self.db_path)
    conn.execute("""
        INSERT INTO scars (
            id, title, category, category_slug, visibility,
            workflow_phases, applies_when, graduation_status,
            wound, trap, circuit_breaker, reflex, socratic_probe, source, original_id, raw_hash
        ) VALUES (
            'SCAR-LANG-PERL-001', 'Makeflow Perl Guidelines', 'Languages', 'languages', 'public',
            '["T30"]', 'Writing Perl scripts', 'graduated',
            'Makeflow Perl wound', 'Makeflow Perl trap', 'Makeflow breaker', 'Makeflow reflex',
            'Did you follow Makeflow Perl guidelines?',
            'makeflow', NULL, 'mf_hash'
        )
    """)
    conn.commit()
    conn.close()

    # Upstream provides SCAR-LANG-PERL-001 with different content
    upstream_payload = [
        SAMPLE_SCARS[0],
        {
            "id": "SCAR-LANG-PERL-001",
            "title": "Upstream Perl Scars",
            "category": "Languages",
            "categorySlug": "languages",
            "visibility": "public",
            "workflowPhases": ["T30"],
            "appliesWhen": "Writing CPAN modules",
            "graduationStatus": "graduated",
            "wound": "Upstream Perl wound",
            "trap": "Upstream Perl trap",
            "circuitBreaker": "Upstream breaker",
            "reflex": "Upstream reflex",
        },
    ]
    payload_bytes = json.dumps(upstream_payload).encode("utf-8")
    script2 = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload_bytes, etag='"etag-coll"')):
      code2 = script2.execute(args=["--db-path", self.db_path, "--force"], stdout=io.StringIO())
    self.assertEqual(code2, 0)

    conn = sqlite3.connect(self.db_path)
    conn.execute(
        """
        UPDATE scars
        SET wound = COALESCE(wound, raw_wound),
            trap = COALESCE(trap, raw_trap),
            circuit_breaker = COALESCE(circuit_breaker, raw_circuit_breaker),
            reflex = COALESCE(reflex, raw_reflex),
            socratic_probe = COALESCE(socratic_probe, 'Did you verify ' || id || '?')
        """
    )
    conn.commit()
    conn.close()
    script2.execute(args=["--db-path", self.db_path, "--enrich"], stdout=io.StringIO())

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()

    # Verify upstream scar receives canonical 4-digit id and preserves ss_id
    cur.execute(
        "SELECT id, ss_id, source, original_id, title"
        " FROM scars WHERE ss_id = 'SCAR-LANG-PERL-001'"
    )
    up_row = cur.fetchone()
    self.assertIsNotNone(up_row)
    self.assertEqual(up_row[0], "SCAR-LANG-PERL-ARCH-0001")
    self.assertEqual(up_row[1], "SCAR-LANG-PERL-001")
    self.assertEqual(up_row[2], "upstream")
    self.assertIsNone(up_row[3])
    self.assertEqual(up_row[4], "Upstream Perl Scars")

    # Verify Makeflow scar keeps its exact id (no SCAR-MF-* mutation) and has ss_id=NULL
    cur.execute(
        "SELECT id, ss_id, source, original_id, title"
        " FROM scars WHERE id = 'SCAR-LANG-PERL-001'"
    )
    mf_row = cur.fetchone()
    self.assertIsNotNone(mf_row)
    self.assertIsNone(mf_row[1])
    self.assertEqual(mf_row[2], "makeflow")
    self.assertIsNone(mf_row[3])
    self.assertEqual(mf_row[4], "Makeflow Perl Guidelines")

    # Verify both are searchable in FTS5
    up_search = query_scars(self.db_path, query="Upstream")
    self.assertTrue(any(r["id"] == "SCAR-LANG-PERL-ARCH-0001" for r in up_search))
    self.assertTrue(any(r["ssId"] == "SCAR-LANG-PERL-001" for r in up_search))

    mf_search = query_scars(self.db_path, query="Guidelines")
    self.assertTrue(any(r["id"] == "SCAR-LANG-PERL-001" for r in mf_search))
    conn.close()

  def test_query_scars_source_filter_and_fields(self):
    # Setup DB with an upstream scar and a makeflow scar
    self.test_collision_reconciliation_renames_makeflow_scar()

    # Query all perl scars
    results = query_scars(self.db_path, query="Perl", limit=10)
    self.assertGreaterEqual(len(results), 2)
    for r in results:
      self.assertIn("source", r)
      self.assertIn("originalId", r)
      self.assertIn("ssId", r)

    # Filter by source='makeflow'
    mf_only = query_scars(self.db_path, query="Perl", source="makeflow")
    self.assertTrue(len(mf_only) >= 1)
    self.assertTrue(all(r["source"] == "makeflow" for r in mf_only))
    self.assertEqual(mf_only[0]["id"], "SCAR-LANG-PERL-001")
    self.assertIsNone(mf_only[0]["ssId"])

    # Filter by source='upstream'
    up_only = query_scars(self.db_path, query="Perl", source="upstream")
    self.assertTrue(len(up_only) >= 1)
    self.assertTrue(all(r["source"] == "upstream" for r in up_only))
    self.assertEqual(up_only[0]["id"], "SCAR-LANG-PERL-ARCH-0001")
    self.assertEqual(up_only[0]["ssId"], "SCAR-LANG-PERL-001")

  def test_sync_never_deletes_makeflow_scars(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-init"')):
      script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())

    # Insert custom Makeflow scar
    conn = sqlite3.connect(self.db_path)
    conn.execute("""
        INSERT INTO scars (
            id, title, category, category_slug, visibility,
            workflow_phases, applies_when, graduation_status,
            wound, trap, circuit_breaker, reflex, source, original_id, raw_hash
        ) VALUES (
            'SCAR-MF-CUSTOM-99', 'Makeflow Custom Scar', 'Testing', 'testing', 'public',
            '["T30"]', 'Testing custom scars', 'graduated',
            'wound', 'trap', 'breaker', 'reflex',
            'makeflow', NULL, 'custom_hash'
        )
    """)
    conn.commit()
    conn.close()

    # Sync with upstream payload that has only SCAR-ARCH-05
    reduced_payload = json.dumps([SAMPLE_SCARS[0]]).encode("utf-8")
    script2 = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(reduced_payload, etag='"etag-red"')):
      code = script2.execute(args=["--db-path", self.db_path, "--force"], stdout=io.StringIO())
    self.assertEqual(code, 0)

    # Makeflow scar MUST NOT be deleted
    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, source FROM scars WHERE id = 'SCAR-MF-CUSTOM-99'")
    row = cur.fetchone()
    self.assertIsNotNone(row)
    self.assertEqual(row[1], "makeflow")
    conn.close()

  def test_collision_reconciliation_handles_existing_mf_prefix(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-1"')):
      script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())

    conn = sqlite3.connect(self.db_path)
    # Insert Makeflow rows owning legacy and canonical Perl IDs without ss_id
    conn.execute("""
        INSERT INTO scars (
            id, title, category, category_slug, visibility,
            workflow_phases, applies_when, graduation_status,
            wound, trap, circuit_breaker, reflex, source, original_id, raw_hash
        ) VALUES (
            'SCAR-LANG-PERL-ARCH-0001', 'First Makeflow Perl Rule', 'ARCH', 'arch', 'public',
            '["T30"]', 'Perl', 'graduated', 'First wound', 'First trap', 'NOOO', 'First reflex',
            'makeflow', NULL, 'hash_mf_1'
        )
    """)
    conn.execute("""
        INSERT INTO scars (
            id, title, category, category_slug, visibility,
            workflow_phases, applies_when, graduation_status,
            wound, trap, circuit_breaker, reflex, source, original_id, raw_hash
        ) VALUES (
            'SCAR-LANG-PERL-001', 'Second Makeflow Perl Rule', 'Polyglot', 'polyglot', 'public',
            '["T30"]', 'Perl', 'graduated', 'Second wound', 'Second trap', 'NOOO', 'Second reflex',
            'makeflow', NULL, 'hash_mf_2'
        )
    """)
    conn.commit()
    conn.close()

    upstream_perl = {
        "id": "SCAR-LANG-PERL-001",
        "title": "Upstream Perl Rule",
        "category": "Polyglot",
        "categorySlug": "polyglot",
        "visibility": "public",
        "workflowPhases": ["T30"],
        "appliesWhen": "Perl",
        "graduationStatus": "graduated",
        "wound": "Upstream wound",
        "trap": "Upstream trap",
        "circuitBreaker": "NOOO",
        "reflex": "Upstream reflex",
    }
    new_payload = json.dumps(SAMPLE_SCARS + [upstream_perl]).encode("utf-8")
    script2 = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(new_payload, etag='"etag-2"')):
      code = script2.execute(args=["--db-path", self.db_path, "--force"], stdout=io.StringIO())
    self.assertEqual(code, 0)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, ss_id, title, source FROM scars WHERE id LIKE '%PERL%' ORDER BY id")
    rows = cur.fetchall()
    by_id = {r[0]: (r[1], r[2], r[3]) for r in rows}
    self.assertEqual(
        by_id.get("SCAR-LANG-PERL-ARCH-0001"),
        (None, "First Makeflow Perl Rule", "makeflow"),
    )
    self.assertEqual(
        by_id.get("SCAR-LANG-PERL-001"),
        (None, "Second Makeflow Perl Rule", "makeflow"),
    )
    self.assertEqual(
        by_id.get("SCAR-LANG-PERL-ARCH-0002"),
        ("SCAR-LANG-PERL-001", "Upstream Perl Rule", "upstream"),
    )
    conn.close()

  def test_schema_upgrade_rebuilds_fts5_before_304(self):
    # Create legacy database where scars_fts lacks source and original_id
    legacy_db = os.path.join(self.tmp_dir.name, "legacy_upgrade.db")
    conn = sqlite3.connect(legacy_db)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('etag', '\"etag-v1\"')")
    conn.execute("""
        CREATE TABLE scars (
            id TEXT PRIMARY KEY, title TEXT, category TEXT, category_slug TEXT,
            visibility TEXT, workflow_phases TEXT, applies_when TEXT,
            graduation_status TEXT, wound TEXT, trap TEXT, circuit_breaker TEXT,
            reflex TEXT, socratic_probe TEXT
        )
    """)
    s = SAMPLE_SCARS[0]
    conn.execute(
        "INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            s["id"], s["title"], s["category"], s["categorySlug"], s["visibility"],
            json.dumps(s["workflowPhases"]), s["appliesWhen"], s["graduationStatus"],
            s["wound"], s["trap"], s["circuitBreaker"], s["reflex"],
            "Did you inherit from BaseScript?",
        ),
    )
    conn.execute("""
        CREATE VIRTUAL TABLE scars_fts USING fts5(
            id UNINDEXED, title, category, applies_when, wound, trap, reflex
        )
    """)
    conn.commit()
    conn.close()

    # Simulate HTTP 304 Not Modified immediately after schema upgrade
    http_304 = urllib.error.HTTPError(
        url="https://syntheticscars.org/registry.json",
        code=304,
        msg="Not Modified",
        hdrs={},
        fp=None,
    )
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", side_effect=http_304):
      code = script.execute(args=["--db-path", legacy_db], stdout=io.StringIO())
    self.assertEqual(code, 0)

    # Verify scars_fts was rebuilt and populated during _init_db_schema before 304 exit
    results = query_scars(legacy_db, query="BaseScript")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-ARCH-05")

  def test_database_size_tracking_and_compaction(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-sz"')):
      code = script.execute(args=["--db-path", self.db_path], stdout=io.StringIO())
    self.assertEqual(code, 0)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = 'db_size_bytes'")
    row = cur.fetchone()
    self.assertIsNotNone(row)
    self.assertGreater(int(row[0]), 0)
    conn.close()

    out = io.StringIO()
    script_status = SyncSyntheticScarsScript()
    code_status = script_status.execute(args=["--db-path", self.db_path, "--status"], stdout=out)
    self.assertEqual(code_status, 0)
    self.assertIn("Size:", out.getvalue())

    bundled_db = resolve_scars_db_path()
    if os.path.isfile(bundled_db):
      self.assertLess(os.path.getsize(bundled_db), 25 * 1024 * 1024)


class TestSimpleEnglishTransformationWorkflow(unittest.TestCase):
  """Tests 10-field Simple English & Socratic Probe SQLite pipeline (GH-142)."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.temp_dir.name, "scars_se.db")

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_ten_field_schema_and_absence_of_flag_columns(self):
    payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-10f"'),
    ):
      code = script.execute(
          args=["--db-path", self.db_path], stdout=io.StringIO()
      )
    self.assertEqual(code, 0)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(scars)")
    col_names = {row[1] for row in cur.fetchall()}

    for expected_col in (
        "raw_wound",
        "raw_trap",
        "raw_circuit_breaker",
        "raw_reflex",
        "raw_hash",
        "wound",
        "trap",
        "circuit_breaker",
        "reflex",
        "socratic_probe",
    ):
      self.assertIn(expected_col, col_names)

    for forbidden_col in (
        "simple_english_transformed",
        "socratic_probe_generated",
        "transformed_hash",
    ):
      self.assertNotIn(forbidden_col, col_names)

    cur.execute("PRAGMA table_info(scars_fts)")
    fts_cols = {row[1] for row in cur.fetchall()}
    self.assertIn("socratic_probe", fts_cols)
    conn.close()

  def test_raw_hash_populates_raw_and_nulls_in_use_then_preserves_and_resets(self):
    scars_v1 = [
        {
            "id": "SCAR-MATH-01",
            "title": "Math & Code Invariant",
            "category": "Architecture",
            "category_slug": "architecture",
            "visibility": "public",
            "workflow_phases": ["T30"],
            "applies_when": "Transforming scars with formulas",
            "graduation_status": "graduated",
            "wound": "Lost inline $x_i + y_i = z_i$ formula.",
            "trap": "Stripping LaTeX $$\\sum_{i=1}^n x_i$$ during rewrite.",
            "circuit_breaker": "Verify spans before commit.",
            "reflex": "Preserve `$VAR` and ```python\nprint(1)\n``` blocks.",
        },
        {
            "id": "SCAR-NOCB-01",
            "title": "Empty Circuit Breaker Scar",
            "category": "Testing",
            "category_slug": "testing",
            "visibility": "public",
            "workflow_phases": ["T30"],
            "applies_when": "Testing empty optional circuit_breaker",
            "graduation_status": "graduated",
            "wound": "Raw wound text.",
            "trap": "Raw trap text.",
            "circuit_breaker": "",
            "reflex": "Raw reflex text.",
        },
    ]
    payload_v1 = json.dumps(scars_v1).encode("utf-8")

    out_sync = io.StringIO()
    script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload_v1, etag='"etag-v1"'),
    ):
      code = script.execute(
          args=["--db-path", self.db_path], stdout=out_sync
      )
    self.assertEqual(code, 0)
    sync_stdout = out_sync.getvalue()
    self.assertIn("Pending Simple English transformations: 2 scar(s).", sync_stdout)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, ss_id, raw_wound, raw_trap, raw_circuit_breaker,"
        " raw_reflex, raw_hash, wound, trap, circuit_breaker, reflex,"
        " socratic_probe FROM scars WHERE ss_id = 'SCAR-MATH-01'"
    )
    row_math = cur.fetchone()
    self.assertEqual(row_math[0], "SCAR-LANG-PYTHON-ARCH-0001")
    self.assertEqual(row_math[1], "SCAR-MATH-01")
    self.assertEqual(row_math[2], "Lost inline $x_i + y_i = z_i$ formula.")
    self.assertEqual(
        row_math[3], "Stripping LaTeX $$\\sum_{i=1}^n x_i$$ during rewrite."
    )
    self.assertEqual(row_math[4], "Verify spans before commit.")
    self.assertEqual(
        row_math[5], "Preserve `$VAR` and ```python\nprint(1)\n``` blocks."
    )
    self.assertTrue(row_math[6])
    self.assertIsNone(row_math[7])
    self.assertIsNone(row_math[8])
    self.assertIsNone(row_math[9])
    self.assertIsNone(row_math[10])
    self.assertIsNone(row_math[11])

    cur.execute(
        "SELECT id, raw_circuit_breaker, circuit_breaker, wound, trap, reflex,"
        " socratic_probe FROM scars WHERE ss_id = 'SCAR-NOCB-01'"
    )
    row_nocb = cur.fetchone()
    self.assertEqual(row_nocb[0], "SCAR-TEST-0001")
    self.assertEqual(row_nocb[1], "")
    self.assertEqual(row_nocb[2], "")
    self.assertIsNone(row_nocb[3])
    self.assertIsNone(row_nocb[4])
    self.assertIsNone(row_nocb[5])
    self.assertIsNone(row_nocb[6])

    # Status command also reports pending count
    out_status = io.StringIO()
    script_status = SyncSyntheticScarsScript()
    code_st = script_status.execute(
        args=["--db-path", self.db_path, "--status"], stdout=out_status
    )
    self.assertEqual(code_st, 0)
    self.assertIn(
        "Pending Simple English transformations: 2 scar(s).",
        out_status.getvalue(),
    )

    # Populate Simple English fields for SCAR-MATH-01 (via ss_id)
    cur.execute(
        "UPDATE scars SET wound = ?, trap = ?, circuit_breaker = ?, reflex = ?,"
        " socratic_probe = ? WHERE ss_id = 'SCAR-MATH-01'",
        (
            "Simple wound with $x_i + y_i = z_i$ preserved.",
            "Simple trap with $$\\sum_{i=1}^n x_i$$ preserved.",
            "Simple circuit breaker.",
            "Simple reflex with `$VAR` and ```python\nprint(1)\n``` preserved.",
            "Did you preserve every formula and code block?",
        ),
    )
    conn.commit()

    # Re-download unchanged upstream payload -> transformed values preserved
    out_same = io.StringIO()
    script_same = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload_v1, etag='"etag-v1b"'),
    ):
      code_same = script_same.execute(
          args=["--db-path", self.db_path, "--force"], stdout=out_same
      )
    self.assertEqual(code_same, 0)
    self.assertIn(
        "Pending Simple English transformations: 1 scar(s).",
        out_same.getvalue(),
    )

    cur.execute(
        "SELECT wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-MATH-01'"
    )
    preserved = cur.fetchone()
    self.assertEqual(
        preserved[0], "Simple wound with $x_i + y_i = z_i$ preserved."
    )
    self.assertEqual(
        preserved[4], "Did you preserve every formula and code block?"
    )

    # Modify upstream SCAR-MATH-01 -> raw_hash changes -> in-use fields reset to NULL
    scars_v2 = json.loads(payload_v1.decode("utf-8"))
    scars_v2[0]["reflex"] = (
        "Updated upstream reflex with `$VAR` and ```python\nprint(2)\n```."
    )
    payload_v2 = json.dumps(scars_v2).encode("utf-8")

    script_changed = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload_v2, etag='"etag-v2"'),
    ):
      code_changed = script_changed.execute(
          args=["--db-path", self.db_path, "--force"], stdout=io.StringIO()
      )
    self.assertEqual(code_changed, 0)

    cur.execute(
        "SELECT raw_reflex, wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-MATH-01'"
    )
    reset_row = cur.fetchone()
    self.assertEqual(
        reset_row[0],
        "Updated upstream reflex with `$VAR` and ```python\nprint(2)\n```.",
    )
    self.assertIsNone(reset_row[1])
    self.assertIsNone(reset_row[2])
    self.assertIsNone(reset_row[3])
    self.assertIsNone(reset_row[4])
    self.assertIsNone(reset_row[5])

    # Populate only the 4 Simple English fields first (socratic_probe still NULL -> NBO-2 still pending)
    cur.execute(
        "UPDATE scars SET wound = 'Simple wound.', trap = 'Simple trap.',"
        " circuit_breaker = 'Simple breaker.', reflex = 'Simple reflex.'"
    )
    conn.commit()
    out_missing_probe = io.StringIO()
    code_missing_probe = SyncSyntheticScarsScript().execute(
        args=["--db-path", self.db_path, "--status"], stdout=out_missing_probe
    )
    self.assertEqual(code_missing_probe, 0)
    self.assertIn(
        "Pending Simple English transformations: 2 scar(s).",
        out_missing_probe.getvalue(),
    )

    # Now populate socratic_probe too -> 0 pending -> notice omitted
    cur.execute("UPDATE scars SET socratic_probe = 'Did you verify this?'")
    conn.commit()
    conn.close()
    out_zero = io.StringIO()
    script_zero = SyncSyntheticScarsScript()
    code_zero = script_zero.execute(
        args=["--db-path", self.db_path, "--status"], stdout=out_zero
    )
    self.assertEqual(code_zero, 0)
    self.assertNotIn(
        "Pending Simple English transformations", out_zero.getvalue()
    )
    self.assertFalse(
        hasattr(sync_synthetic_scars, "_resolve_scar_importer_model")
    )

  def test_extract_protected_spans_and_verify_scar_transformation(self):
    from lib.scars_db import (
        extract_protected_spans,
        verify_scar_transformation,
    )

    sample_text = (
        "Check `$HOME` and `$PATH` in shell, then compute $a + b = c$ and\n"
        "$$E = mc^2$$\n"
        "before running:\n"
        "```bash\necho $USER\n```"
    )
    spans = extract_protected_spans(sample_text)
    self.assertEqual(spans["fenced_code"], ["```bash\necho $USER\n```"])
    self.assertEqual(spans["display_math"], ["$$E = mc^2$$"])
    self.assertEqual(spans["inline_math"], ["$a + b = c$"])

    raw_row = {
        "raw_wound": "Wound with $a + b = c$ and `$ENV_VAR`.",
        "raw_trap": "Trap with $$E = mc^2$$ equation.",
        "raw_circuit_breaker": "Stop if `x` fails.",
        "raw_reflex": "Run ```python\nx = 1; y = 2\n``` in test.",
    }
    valid_transformed = {
        "wound": "Simple wound with $a + b = c$ and `$ENV_VAR`.",
        "trap": "Simple trap with $$E = mc^2$$ equation.",
        "circuit_breaker": "If `x` fails, stop the build.",
        "reflex": "Run ```python\nx = 1; y = 2\n``` in the test.",
        "socratic_probe": "Did you verify $a + b = c$ before running the test?",
    }
    errors = verify_scar_transformation(raw_row, valid_transformed)
    self.assertEqual(errors, [])

    # Drop inline math -> rejected
    bad_math = dict(valid_transformed, wound="Simple wound without formula.")
    self.assertTrue(verify_scar_transformation(raw_row, bad_math))

    # Alter fenced code block -> rejected
    bad_code = dict(
        valid_transformed, reflex="Run ```python\nx = 99\n``` in the test."
    )
    self.assertTrue(verify_scar_transformation(raw_row, bad_code))

    # Semicolon in prose (outside code/math) -> rejected
    bad_semi = dict(
        valid_transformed, wound="Simple wound with $a + b = c$; bad semicolon."
    )
    self.assertTrue(verify_scar_transformation(raw_row, bad_semi))

    # Em-dash in prose -> rejected
    bad_emdash = dict(
        valid_transformed, trap="Simple trap — with $$E = mc^2$$ equation."
    )
    self.assertTrue(verify_scar_transformation(raw_row, bad_emdash))

    # Socratic probe missing question mark -> rejected
    bad_probe = dict(
        valid_transformed, socratic_probe="Verify the formula before running."
    )
    self.assertTrue(verify_scar_transformation(raw_row, bad_probe))

    # Empty required field -> rejected
    bad_empty = dict(valid_transformed, reflex="")
    self.assertTrue(verify_scar_transformation(raw_row, bad_empty))

    # Null or omitted socratic_probe -> allowed (populated separately by socratic-probe-writer)
    no_probe = dict(valid_transformed, socratic_probe=None)
    self.assertEqual(verify_scar_transformation(raw_row, no_probe), [])

  def test_export_pending_apply_transformed_verify_and_query_scars(self):
    scars_input = [
        {
            "id": "SCAR-PIPE-01",
            "title": "Pipeline Transformation Test",
            "category": "Architecture",
            "category_slug": "architecture",
            "visibility": "public",
            "workflow_phases": ["T20", "T30"],
            "applies_when": "Testing export and apply CLI flags",
            "graduation_status": "graduated",
            "wound": "Raw wound with $k = 1$.",
            "trap": "Raw trap text.",
            "circuit_breaker": "Raw circuit breaker text.",
            "reflex": "Raw reflex with ```sh\ntest -f a\n```.",
        }
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-pipe"'),
    ):
      self.assertEqual(
          script.execute(args=["--db-path", self.db_path], stdout=io.StringIO()),
          0,
      )

    # Pre-populate wound directly so we can verify --apply-transformed only updates NULL fields
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        "UPDATE scars SET wound = ? WHERE ss_id = 'SCAR-PIPE-01'",
        ("Existing Simple English wound with $k = 1$.",),
    )
    conn.commit()
    conn.close()

    # 1. --export-pending exports 1 scar by default and lists null_fields
    out_exp = io.StringIO()
    script_exp = SyncSyntheticScarsScript()
    self.assertEqual(
        script_exp.execute(
            args=["--db-path", self.db_path, "--export-pending"],
            stdout=out_exp,
        ),
        0,
    )
    exported = json.loads(out_exp.getvalue())
    self.assertEqual(len(exported), 1)
    self.assertEqual(exported[0]["id"], "SCAR-LANG-BASH-ARCH-0001")
    self.assertEqual(exported[0]["ss_id"], "SCAR-PIPE-01")
    self.assertNotIn("wound", exported[0]["null_fields"])
    for nf in ("trap", "circuit_breaker", "reflex", "socratic_probe"):
      self.assertIn(nf, exported[0]["null_fields"])

    # 2. --apply-transformed merges only into NULL fields and keeps existing non-null wound (resolves ss_id)
    apply_path = os.path.join(self.temp_dir.name, "transformed.json")
    with open(apply_path, "w", encoding="utf-8") as f:
      json.dump(
          [
              {
                  "id": "SCAR-PIPE-01",
                  "wound": "Attempted overwrite of non-null wound with $k = 1$.",
                  "trap": "Simple trap text.",
                  "circuit_breaker": "If the check fails, stop.",
                  "reflex": "Run ```sh\ntest -f a\n``` to verify.",
                  "socratic_probe": "Did you run the check before proceeding?",
              }
          ],
          f,
      )

    script_apply = SyncSyntheticScarsScript()
    self.assertEqual(
        script_apply.execute(
            args=[
                "--db-path",
                self.db_path,
                "--apply-transformed",
                apply_path,
            ],
            stdout=io.StringIO(),
        ),
        0,
    )

    # Verify query_scars returns Simple English fields and socraticProbe, keeping existing wound
    results = query_scars(self.db_path, query="Simple trap")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-LANG-BASH-ARCH-0001")
    self.assertEqual(results[0]["ssId"], "SCAR-PIPE-01")
    self.assertEqual(
        results[0]["wound"], "Existing Simple English wound with $k = 1$."
    )
    self.assertEqual(results[0]["trap"], "Simple trap text.")
    self.assertEqual(results[0]["circuitBreaker"], "If the check fails, stop.")
    self.assertEqual(
        results[0]["reflex"], "Run ```sh\ntest -f a\n``` to verify."
    )
    self.assertEqual(
        results[0]["socraticProbe"],
        "Did you run the check before proceeding?",
    )

    # 3. --verify-transformed exits 0 when valid
    script_ver = SyncSyntheticScarsScript()
    self.assertEqual(
        script_ver.execute(
            args=["--db-path", self.db_path, "--verify-transformed"],
            stdout=io.StringIO(),
        ),
        0,
    )

    # Corrupt the transformed wound in SQLite (drop $k = 1$) -> --verify-transformed exits 1
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        "UPDATE scars SET wound = 'Corrupted wound missing formula.' WHERE ss_id = 'SCAR-PIPE-01'"
    )
    conn.commit()
    conn.close()

    script_ver_fail = SyncSyntheticScarsScript()
    self.assertEqual(
        script_ver_fail.execute(
            args=["--db-path", self.db_path, "--verify-transformed"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ),
        1,
    )

  def test_import_simple_english_updates_four_fields_and_preserves_socratic_probe(self):
    scars_input = [
        {
            "id": "SCAR-MATH-01",
            "title": "Math & Code Invariant",
            "category": "Architecture",
            "category_slug": "architecture",
            "visibility": "public",
            "workflow_phases": ["T30"],
            "applies_when": "Transforming scars with formulas",
            "graduation_status": "graduated",
            "wound": "Lost inline $x_i + y_i = z_i$ formula.",
            "trap": "Stripping LaTeX $$\\sum_{i=1}^n x_i$$ during rewrite.",
            "circuit_breaker": "Verify spans before commit.",
            "reflex": "Preserve `$VAR` and ```python\nprint(1)\n``` blocks.",
        },
        {
            "id": "SCAR-NOCB-01",
            "title": "Empty Circuit Breaker Scar",
            "category": "Testing",
            "category_slug": "testing",
            "visibility": "public",
            "workflow_phases": ["T30"],
            "applies_when": "Testing empty optional circuit_breaker",
            "graduation_status": "graduated",
            "wound": "Raw wound text.",
            "trap": "Raw trap text.",
            "circuit_breaker": "",
            "reflex": "Raw reflex text.",
        },
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-imp1"'),
    ):
      self.assertEqual(
          script.execute(args=["--db-path", self.db_path], stdout=io.StringIO()),
          0,
      )

    # Set an existing socratic_probe on SCAR-MATH-01 in SQLite
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        "UPDATE scars SET socratic_probe = ? WHERE ss_id = 'SCAR-MATH-01'",
        ("Existing Socratic question?",),
    )
    conn.commit()
    conn.close()

    # 1. Single-object JSON with --import-simple-english (including an ignored socratic_probe key)
    single_json_path = os.path.join(self.temp_dir.name, "import_single.json")
    with open(single_json_path, "w", encoding="utf-8") as f:
      json.dump(
          {
              "id": "SCAR-MATH-01",
              "wound": "Simple wound with $x_i + y_i = z_i$ formula.",
              "trap": "Simple trap with $$\\sum_{i=1}^n x_i$$ formula.",
              "circuit_breaker": "If spans change, stop the commit.",
              "reflex": "Keep `$VAR` and ```python\nprint(1)\n``` blocks.",
              "socratic_probe": "Should be ignored?",
          },
          f,
      )

    out1 = io.StringIO()
    err1 = io.StringIO()
    script_imp1 = SyncSyntheticScarsScript()
    code1 = script_imp1.execute(
        args=[
            "--db-path",
            self.db_path,
            "--import-simple-english",
            single_json_path,
        ],
        stdout=out1,
        stderr=err1,
    )
    self.assertEqual(code1, 0, f"Expected 0, got {code1}: {err1.getvalue()}")
    self.assertIn("Imported 1 Simple English scar(s).", out1.getvalue())

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-MATH-01'"
    )
    row = cur.fetchone()
    conn.close()
    self.assertEqual(row[0], "Simple wound with $x_i + y_i = z_i$ formula.")
    self.assertEqual(row[1], "Simple trap with $$\\sum_{i=1}^n x_i$$ formula.")
    self.assertEqual(row[2], "If spans change, stop the commit.")
    self.assertEqual(row[3], "Keep `$VAR` and ```python\nprint(1)\n``` blocks.")
    self.assertEqual(row[4], "Existing Socratic question?")

    # 2. Array JSON with --import alias: overwrites SCAR-MATH-01 4 fields and imports SCAR-NOCB-01 without circuit_breaker key
    batch_json_path = os.path.join(self.temp_dir.name, "import_batch.json")
    with open(batch_json_path, "w", encoding="utf-8") as f:
      json.dump(
          [
              {
                  "id": "SCAR-MATH-01",
                  "wound": "Revised wound with $x_i + y_i = z_i$ intact.",
                  "trap": "Revised trap with $$\\sum_{i=1}^n x_i$$ intact.",
                  "circuit_breaker": "Check spans before every commit.",
                  "reflex": "Verify `$VAR` and ```python\nprint(1)\n``` blocks.",
              },
              {
                  "id": "SCAR-NOCB-01",
                  "wound": "Simple wound without breaker.",
                  "trap": "Simple trap without breaker.",
                  "reflex": "Run the test suite.",
              },
          ],
          f,
      )

    out2 = io.StringIO()
    err2 = io.StringIO()
    script_imp2 = SyncSyntheticScarsScript()
    code2 = script_imp2.execute(
        args=["--db-path", self.db_path, "--import", batch_json_path],
        stdout=out2,
        stderr=err2,
    )
    self.assertEqual(code2, 0, f"Expected 0, got {code2}: {err2.getvalue()}")
    self.assertIn("Imported 2 Simple English scar(s).", out2.getvalue())

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-MATH-01'"
    )
    row_math = cur.fetchone()
    self.assertEqual(row_math[0], "Revised wound with $x_i + y_i = z_i$ intact.")
    self.assertEqual(row_math[4], "Existing Socratic question?")

    cur.execute(
        "SELECT wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-NOCB-01'"
    )
    row_nocb = cur.fetchone()
    conn.close()
    self.assertEqual(row_nocb[0], "Simple wound without breaker.")
    self.assertEqual(row_nocb[1], "Simple trap without breaker.")
    self.assertEqual(row_nocb[2], "")
    self.assertEqual(row_nocb[3], "Run the test suite.")
    self.assertIsNone(row_nocb[4])

    # Searchable via FTS5 query_scars
    results = query_scars(self.db_path, query="Revised wound")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-LANG-PYTHON-ARCH-0001")
    self.assertEqual(results[0]["ssId"], "SCAR-MATH-01")
    self.assertEqual(results[0]["socraticProbe"], "Existing Socratic question?")

  def test_import_simple_english_rejects_invalid_and_rolls_back(self):
    scars_input = [
        {
            "id": "SCAR-MATH-01",
            "title": "Math & Code Invariant",
            "category": "Architecture",
            "category_slug": "architecture",
            "visibility": "public",
            "workflow_phases": ["T30"],
            "applies_when": "Transforming scars with formulas",
            "graduation_status": "graduated",
            "wound": "Lost inline $x_i + y_i = z_i$ formula.",
            "trap": "Stripping LaTeX $$\\sum_{i=1}^n x_i$$ during rewrite.",
            "circuit_breaker": "Verify spans before commit.",
            "reflex": "Preserve `$VAR` and ```python\nprint(1)\n``` blocks.",
        }
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-imp-err"'),
    ):
      self.assertEqual(
          script.execute(args=["--db-path", self.db_path], stdout=io.StringIO()),
          0,
      )

    # 1. Missing file -> exit 1
    err_missing = io.StringIO()
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=[
                "--db-path",
                self.db_path,
                "--import-simple-english",
                os.path.join(self.temp_dir.name, "nonexistent.json"),
            ],
            stdout=io.StringIO(),
            stderr=err_missing,
        ),
        1,
    )

    # 2. Malformed JSON -> exit 1
    bad_json_path = os.path.join(self.temp_dir.name, "bad.json")
    with open(bad_json_path, "w", encoding="utf-8") as f:
      f.write("{not valid json")
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=[
                "--db-path",
                self.db_path,
                "--import-simple-english",
                bad_json_path,
            ],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ),
        1,
    )

    # 3. Missing id -> exit 1
    no_id_path = os.path.join(self.temp_dir.name, "no_id.json")
    with open(no_id_path, "w", encoding="utf-8") as f:
      json.dump({"wound": "W.", "trap": "T.", "reflex": "R."}, f)
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=[
                "--db-path",
                self.db_path,
                "--import-simple-english",
                no_id_path,
            ],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ),
        1,
    )

    # 4. Unknown scar id -> exit 1
    unknown_id_path = os.path.join(self.temp_dir.name, "unknown_id.json")
    with open(unknown_id_path, "w", encoding="utf-8") as f:
      json.dump(
          {
              "id": "SCAR-UNKNOWN-99",
              "wound": "W.",
              "trap": "T.",
              "circuit_breaker": "C.",
              "reflex": "R.",
          },
          f,
      )
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=[
                "--db-path",
                self.db_path,
                "--import-simple-english",
                unknown_id_path,
            ],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ),
        1,
    )

    # 5. Invalid transformation (dropped math span) -> exit 1 and rollback
    bad_span_path = os.path.join(self.temp_dir.name, "bad_span.json")
    with open(bad_span_path, "w", encoding="utf-8") as f:
      json.dump(
          {
              "id": "SCAR-MATH-01",
              "wound": "Dropped formula in wound.",
              "trap": "Simple trap with $$\\sum_{i=1}^n x_i$$ formula.",
              "circuit_breaker": "Verify spans before commit.",
              "reflex": "Preserve `$VAR` and ```python\nprint(1)\n``` blocks.",
          },
          f,
      )
    err_span = io.StringIO()
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=[
                "--db-path",
                self.db_path,
                "--import-simple-english",
                bad_span_path,
            ],
            stdout=io.StringIO(),
            stderr=err_span,
        ),
        1,
    )
    self.assertIn("SCAR-MATH-01", err_span.getvalue())

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT wound FROM scars WHERE ss_id = 'SCAR-MATH-01'")
    self.assertIsNone(cur.fetchone()[0])
    conn.close()

    # 6. Invalid transformation (forbidden semicolon in prose) -> exit 1 and rollback
    bad_semi_path = os.path.join(self.temp_dir.name, "bad_semi.json")
    with open(bad_semi_path, "w", encoding="utf-8") as f:
      json.dump(
          {
              "id": "SCAR-MATH-01",
              "wound": "Simple wound with $x_i + y_i = z_i$; forbidden semicolon.",
              "trap": "Simple trap with $$\\sum_{i=1}^n x_i$$ formula.",
              "circuit_breaker": "Verify spans before commit.",
              "reflex": "Preserve `$VAR` and ```python\nprint(1)\n``` blocks.",
          },
          f,
      )
    err_semi = io.StringIO()
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=[
                "--db-path",
                self.db_path,
                "--import-simple-english",
                bad_semi_path,
            ],
            stdout=io.StringIO(),
            stderr=err_semi,
        ),
        1,
    )
    self.assertIn("semicolon", err_semi.getvalue())


class TestMultiDimensionalIndexingAndEnrichment(unittest.TestCase):
  """Tests multi-dimensional indexing, keyword enrichment, and FTS5 ID search (GH-145)."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.temp_dir.name, "scars_multidim.db")

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_schema_migration_adds_metadata_columns_and_indexes_id_in_fts5(self):
    legacy_db = os.path.join(self.temp_dir.name, "legacy_20col.db")
    conn = sqlite3.connect(legacy_db)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
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
        CREATE VIRTUAL TABLE scars_fts USING fts5(
            id UNINDEXED,
            title,
            category,
            applies_when,
            wound,
            trap,
            reflex,
            socratic_probe,
            source UNINDEXED,
            original_id UNINDEXED
        )
        """
    )
    s = SAMPLE_SCARS[0]
    conn.execute(
        """
        INSERT INTO scars (
            id, title, category, category_slug, visibility,
            workflow_phases, applies_when, graduation_status,
            raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
            wound, trap, circuit_breaker, reflex, socratic_probe,
            source, original_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upstream', 'ORIG-ARCH-05')
        """,
        (
            s["id"],
            s["title"],
            s["category"],
            s["categorySlug"],
            s["visibility"],
            json.dumps(s["workflowPhases"]),
            s["appliesWhen"],
            s["graduationStatus"],
            s["wound"],
            s["trap"],
            s["circuitBreaker"],
            s["reflex"],
            "hash_arch_05",
            s["wound"],
            s["trap"],
            s["circuitBreaker"],
            s["reflex"],
            "Did you inherit from BaseScript?",
        ),
    )
    conn.commit()
    conn.close()

    script = SyncSyntheticScarsScript()
    script.db_path = legacy_db
    script._init_db_schema()
    script.cleanup(0)

    conn = sqlite3.connect(legacy_db)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(scars)")
    scars_cols = {r[1] for r in cur.fetchall()}
    for new_col in ("keywords", "languages", "frameworks", "diagnostic_codes"):
      self.assertIn(new_col, scars_cols)

    cur.execute("PRAGMA table_info(scars_fts)")
    fts_cols = {r[1] for r in cur.fetchall()}
    for fts_col in (
        "id",
        "original_id",
        "keywords",
        "languages",
        "frameworks",
        "diagnostic_codes",
        "workflow_phases",
    ):
      self.assertIn(fts_col, fts_cols)

    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    fts_ddl = cur.fetchone()[0]
    self.assertNotIn("id unindexed", fts_ddl.lower())
    self.assertNotIn("original_id unindexed", fts_ddl.lower())

    cur.execute(
        "SELECT id FROM scars_fts WHERE scars_fts MATCH '\"SCAR-ARCH-05\"*'"
    )
    self.assertEqual(cur.fetchall(), [("SCAR-ARCH-05",)])

    cur.execute(
        "SELECT id FROM scars_fts WHERE scars_fts MATCH '\"SCAR-ARCH\"*'"
    )
    self.assertEqual(cur.fetchall(), [("SCAR-ARCH-05",)])

    cur.execute(
        "SELECT id FROM scars_fts WHERE scars_fts MATCH '\"SCAR-ARCH-05\"* \"BaseScript\"*'"
    )
    self.assertEqual(cur.fetchall(), [("SCAR-ARCH-05",)])
    conn.close()

    self.assertEqual(
        query_scars(legacy_db, query="SCAR-ARCH-05")[0]["id"], "SCAR-ARCH-05"
    )
    self.assertEqual(
        query_scars(legacy_db, query="SCAR-ARCH")[0]["id"], "SCAR-ARCH-05"
    )
    self.assertEqual(
        query_scars(legacy_db, query="SCAR-ARCH-05 BaseScript")[0]["id"],
        "SCAR-ARCH-05",
    )

  def test_extract_scar_metadata_dimensions(self):
    from lib.scars_db import extract_scar_metadata
    import scripts.sync_synthetic_scars as cli_mod

    self.assertTrue(hasattr(cli_mod, "extract_scar_metadata"))

    row_a = {
        "id": "SCAR-RIV-99",
        "title": "AsyncNotifier Reentrancy & Completer StateError in Riverpod",
        "category": "RIV",
        "category_slug": "riv",
        "applies_when": (
            "Using Flutter Riverpod or Bloc with `Completer` and `WidgetTester`"
        ),
        "workflow_phases": (
            '["Step 4 (Plan Approval) & Step 6 (TDD Implementation) & Step 9'
            ' (Adversarial Review)"]'
        ),
        "raw_wound": (
            "Concurrent mutation causes race condition, deadlock, and memory"
            " leak with `StateError` and `UNDEFINED_GETTER`."
        ),
        "raw_trap": (
            "Calling `completer.complete()` twice without checking"
            " `!isCompleted`."
        ),
        "raw_circuit_breaker": "If `isCompleted` is true, stop.",
        "raw_reflex": (
            "Guard with ```dart\nif (!completer.isCompleted)"
            " completer.complete();\n```."
        ),
        "wound": "Simple wound text.",
        "trap": "Simple trap text.",
        "circuit_breaker": "If completed, stop.",
        "reflex": "Check before completing.",
        "socratic_probe": "Did you guard the completer?",
    }
    meta_a = extract_scar_metadata(row_a)
    for key in (
        "keywords",
        "languages",
        "frameworks",
        "diagnostic_codes",
        "workflow_phases",
    ):
      self.assertIn(key, meta_a)
      self.assertIsInstance(meta_a[key], list)

    kw_lower_a = {k.lower() for k in meta_a["keywords"]}
    for expected_kw in (
        "reentrancy",
        "race condition",
        "deadlock",
        "memory leak",
        "completer",
    ):
      self.assertIn(expected_kw, kw_lower_a)
    self.assertIn("dart", meta_a["languages"])
    for expected_fw in ("flutter", "riverpod", "bloc"):
      self.assertIn(expected_fw, meta_a["frameworks"])
    for expected_diag in ("StateError", "UNDEFINED_GETTER"):
      self.assertIn(expected_diag, meta_a["diagnostic_codes"])
    for expected_phase in (
        "Target 20: plan",
        "Target 30: tdd",
        "Target 40: review",
    ):
      self.assertIn(expected_phase, meta_a["workflow_phases"])

    row_b = {
        "id": "SCAR-SYS-99",
        "title": "FastAPI SQLite Worker Shutdown and SemVer Breakage",
        "category": "SYS",
        "category_slug": "sys",
        "applies_when": (
            "Running Python FastAPI and pytest inside Docker containers with"
            " SQLite"
        ),
        "workflow_phases": '["T30", "T50", "T70"]',
        "raw_wound": (
            "Worker ignores `SIGTERM`, fails with `EADDRINUSE` and"
            " `FileExistsError`, or introduces semver breakage."
        ),
        "raw_trap": "Skipping signal trap in Python.",
        "raw_circuit_breaker": "",
        "raw_reflex": "Trap `SIGTERM` and test with `pytest`.",
    }
    meta_b = extract_scar_metadata(row_b)
    kw_lower_b = {k.lower() for k in meta_b["keywords"]}
    self.assertIn("semver breakage", kw_lower_b)
    self.assertIn("python", meta_b["languages"])
    for expected_fw in ("fastapi", "pytest", "sqlite", "docker"):
      self.assertIn(expected_fw, meta_b["frameworks"])
    for expected_diag in ("SIGTERM", "EADDRINUSE", "FileExistsError"):
      self.assertIn(expected_diag, meta_b["diagnostic_codes"])
    for expected_phase in (
        "Target 30: tdd",
        "Target 50: commit",
        "Target 70: ci-triage",
    ):
      self.assertIn(expected_phase, meta_b["workflow_phases"])

    row_c = {
        "id": "SCAR-POLY-99",
        "title": (
            "Polyglot React TypeScript, Rust Cargo, Go Goroutine, C malloc,"
            " and Bash Trap"
        ),
        "category": "ARCH",
        "category_slug": "arch",
        "applies_when": (
            "Building TypeScript React UI, Rust crates, Go services, C"
            " modules, Java JVM, or bash scripts"
        ),
        "workflow_phases": "[]",
        "raw_wound": (
            "C `malloc` leak, Go goroutine leak, Rust borrow error, React"
            " hydration mismatch, or `NullPointerException`."
        ),
        "raw_trap": "Ignoring polyglot boundaries in bash scripts.",
        "raw_circuit_breaker": "",
        "raw_reflex": "Validate across languages.",
    }
    meta_c = extract_scar_metadata(row_c)
    for expected_lang in ("typescript", "rust", "go", "c", "bash"):
      self.assertIn(expected_lang, meta_c["languages"])
    self.assertIn("react", meta_c["frameworks"])
    self.assertIn("NullPointerException", meta_c["diagnostic_codes"])
    self.assertGreaterEqual(len(meta_c["workflow_phases"]), 1)

    row_d = {
        "id": "SCAR-PROC-999",
        "title": "Human Approval Gate Enforcement",
        "category": "Process",
        "category_slug": "process",
        "applies_when": "Transitioning between workflow targets",
        "workflow_phases": '["Target 20: approved-plan"]',
        "raw_wound": "Self-approving pause gates.",
        "raw_trap": "Bypassing human review.",
        "raw_circuit_breaker": "",
        "raw_reflex": "Wait for explicit operator confirmation.",
    }
    meta_d = extract_scar_metadata(row_d)
    self.assertIn("universal", meta_d["languages"])
    self.assertIn("Target 20: plan", meta_d["workflow_phases"])

  def test_enrich_scars_database_and_cli_scripts_preserve_prose_and_raw_hash(self):
    from lib.scars_db import EnrichScarsIndexScript, enrich_scars_database
    import scripts.sync_synthetic_scars as cli_mod

    self.assertTrue(hasattr(cli_mod, "EnrichScarsIndexScript"))
    self.assertTrue(hasattr(cli_mod, "enrich_scars_database"))
    enrich_script_path = os.path.join(_SCRIPTS_DIR, "enrich_scars_index.py")
    self.assertTrue(os.path.isfile(enrich_script_path))

    scars_input = [
        {
            "id": "SCAR-RIV-01",
            "title": "Riverpod AsyncNotifier Reentrancy Guard",
            "category": "RIV",
            "categorySlug": "riv",
            "visibility": "public",
            "workflowPhases": ["Step 4 (Plan) & Step 6 (TDD)"],
            "appliesWhen": "Using Flutter Riverpod AsyncNotifier with Completer",
            "graduationStatus": "graduated",
            "wound": (
                "Reentrancy race condition throws `StateError` and"
                " `UNDEFINED_GETTER`."
            ),
            "trap": "Unchecked concurrent `Completer` completion.",
            "circuitBreaker": "If `isCompleted` is true, stop.",
            "reflex": "Check `!completer.isCompleted` in Dart.",
        }
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    sync_script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-enr"'),
    ):
      self.assertEqual(
          sync_script.execute(
              args=["--db-path", self.db_path], stdout=io.StringIO()
          ),
          0,
      )

    conn = sqlite3.connect(self.db_path)
    conn.execute(
        """
        UPDATE scars
        SET wound = 'Simple wound.',
            trap = 'Simple trap.',
            circuit_breaker = 'If completed, stop.',
            reflex = 'Check completer in Dart.',
            socratic_probe = 'Did you check isCompleted?',
            keywords = NULL,
            languages = NULL,
            frameworks = NULL,
            diagnostic_codes = NULL
        WHERE ss_id = 'SCAR-RIV-01'
        """
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, raw_hash, wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-RIV-01'"
    )
    before_row = cur.fetchone()
    self.assertEqual(before_row[0], "SCAR-LANG-DART-RIV-0001")
    before_row = before_row[1:]
    conn.close()

    out_enr = io.StringIO()
    err_enr = io.StringIO()
    enr_script = EnrichScarsIndexScript()
    self.assertIsInstance(enr_script, BaseScript)
    code_enr = enr_script.execute(
        args=["--db-path", self.db_path], stdout=out_enr, stderr=err_enr
    )
    self.assertEqual(code_enr, 0, err_enr.getvalue())
    self.assertIn("Enriched", out_enr.getvalue())

    # NBO-2: --status on an empty uninitialized SQLite file on disk exits 0 cleanly
    uninit_db = os.path.join(self.temp_dir.name, "empty_uninitialized.db")
    open(uninit_db, "wb").close()
    out_uninit = io.StringIO()
    err_uninit = io.StringIO()
    code_uninit = EnrichScarsIndexScript().execute(
        args=["--db-path", uninit_db, "--status"],
        stdout=out_uninit,
        stderr=err_uninit,
    )
    self.assertEqual(code_uninit, 0, err_uninit.getvalue())
    self.assertIn("uninitialized", out_uninit.getvalue().lower())

    out_flag = io.StringIO()
    code_flag = SyncSyntheticScarsScript().execute(
        args=["--db-path", self.db_path, "--enrich"], stdout=out_flag
    )
    self.assertEqual(code_flag, 0)

    summary = enrich_scars_database(self.db_path)
    self.assertEqual(summary["total_scars"], 1)
    self.assertEqual(summary["enriched_scars"], 1)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT raw_hash, wound, trap, circuit_breaker, reflex,"
        " socratic_probe, keywords, languages, frameworks, diagnostic_codes,"
        " workflow_phases FROM scars WHERE ss_id = 'SCAR-RIV-01'"
    )
    after_row = cur.fetchone()
    conn.close()

    self.assertEqual(after_row[:6], before_row)
    self.assertIn(
        "reentrancy", [k.lower() for k in json.loads(after_row[6])]
    )
    self.assertIn("dart", json.loads(after_row[7]))
    self.assertIn("riverpod", json.loads(after_row[8]))
    self.assertIn("flutter", json.loads(after_row[8]))
    self.assertIn("StateError", json.loads(after_row[9]))
    self.assertIn("UNDEFINED_GETTER", json.loads(after_row[9]))
    self.assertIn("Target 20: plan", json.loads(after_row[10]))
    self.assertIn("Target 30: tdd", json.loads(after_row[10]))

  def test_multi_dimensional_filtering_in_query_scars_and_cli(self):
    scars_input = [
        {
            "id": "SCAR-FLUTTER-10",
            "title": "Flutter Riverpod Completer Lifecycle",
            "category": "FLUTTER",
            "categorySlug": "flutter",
            "visibility": "public",
            "workflowPhases": ["Target 20: plan", "Target 30: tdd"],
            "appliesWhen": "Building Flutter widgets with Riverpod and Bloc",
            "graduationStatus": "graduated",
            "wound": (
                "Reentrancy deadlock causes `StateError` and"
                " `UNDEFINED_GETTER`."
            ),
            "trap": "Unchecked `Completer` completion in Dart.",
            "circuitBreaker": "If `isCompleted` is true, stop.",
            "reflex": "Guard `Completer` in Dart.",
        },
        {
            "id": "SCAR-PY-20",
            "title": "Python FastAPI Pytest Docker Signal Handling",
            "category": "SYS",
            "categorySlug": "sys",
            "visibility": "public",
            "workflowPhases": ["Target 30: tdd", "Target 40: review"],
            "appliesWhen": "Running Python FastAPI and pytest in Docker with SQLite",
            "graduationStatus": "graduated",
            "wound": (
                "Uncaught `SIGTERM` causes `EADDRINUSE`, `FileExistsError`, and"
                " semver breakage."
            ),
            "trap": "Ignoring POSIX signals in Python workers.",
            "circuitBreaker": "",
            "reflex": "Trap `SIGTERM` and clean up SQLite locks.",
        },
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    script = SyncSyntheticScarsScript()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-filter"'),
    ):
      self.assertEqual(
          script.execute(
              args=["--db-path", self.db_path], stdout=io.StringIO()
          ),
          0,
      )

    conn = sqlite3.connect(self.db_path)
    conn.execute(
        "UPDATE scars SET wound = 'Simple widget wound.' WHERE ss_id = 'SCAR-FLUTTER-10'"
    )
    conn.execute(
        """
        UPDATE scars
        SET wound = COALESCE(wound, raw_wound),
            trap = COALESCE(trap, raw_trap),
            circuit_breaker = COALESCE(circuit_breaker, raw_circuit_breaker),
            reflex = COALESCE(reflex, raw_reflex),
            socratic_probe = COALESCE(socratic_probe, 'Did you verify ' || id || '?')
        """
    )
    conn.commit()
    conn.close()
    SyncSyntheticScarsScript().execute(
        args=["--db-path", self.db_path, "--enrich"], stdout=io.StringIO()
    )

    # 1. Search by enriched keyword ('reentrancy') that was simplified out of in-use wound
    reent_res = query_scars(self.db_path, query="reentrancy")
    self.assertEqual(len(reent_res), 1)
    self.assertEqual(reent_res[0]["id"], "SCAR-LANG-DART-FLUTTER-0010")
    self.assertEqual(reent_res[0]["ssId"], "SCAR-FLUTTER-10")
    for expected_field in (
        "keywords",
        "languages",
        "frameworks",
        "diagnosticCodes",
        "diagnostic_codes",
        "workflowPhases",
        "workflow_phases",
        "ssId",
        "ssTargets",
        "mfTargets",
    ):
      self.assertIn(expected_field, reent_res[0])

    # 2. Filter by framework
    fw_res = query_scars(self.db_path, query="", framework="riverpod")
    self.assertEqual(len(fw_res), 1)
    self.assertEqual(fw_res[0]["id"], "SCAR-LANG-DART-FLUTTER-0010")

    fw_py = query_scars(self.db_path, query="Worker", framework="fastapi")
    self.assertEqual(len(fw_py), 1)
    self.assertEqual(fw_py[0]["id"], "SCAR-LANG-PYTHON-SYS-0020")

    # 3. Filter by diagnostic_code
    diag_res = query_scars(self.db_path, query="", diagnostic_code="SIGTERM")
    self.assertEqual(len(diag_res), 1)
    self.assertEqual(diag_res[0]["id"], "SCAR-LANG-PYTHON-SYS-0020")

    diag_dart = query_scars(
        self.db_path, query="Completer", diagnostic_code="UNDEFINED_GETTER"
    )
    self.assertEqual(len(diag_dart), 1)
    self.assertEqual(diag_dart[0]["id"], "SCAR-LANG-DART-FLUTTER-0010")

    # 4. Filter by workflow_phase
    wp_plan = query_scars(
        self.db_path, query="", workflow_phase="Target 20: plan"
    )
    self.assertEqual(len(wp_plan), 1)
    self.assertEqual(wp_plan[0]["id"], "SCAR-LANG-DART-FLUTTER-0010")

    wp_rev = query_scars(
        self.db_path, query="", workflow_phase="Target 40: review"
    )
    self.assertEqual(len(wp_rev), 1)
    self.assertEqual(wp_rev[0]["id"], "SCAR-LANG-PYTHON-SYS-0020")

    wp_short = query_scars(self.db_path, query="", workflow_phase="T40")
    self.assertEqual(len(wp_short), 1)
    self.assertEqual(wp_short[0]["id"], "SCAR-LANG-PYTHON-SYS-0020")

    # 4b. Negative boundary assertions (BLOCKER-1)
    self.assertEqual(
        query_scars(
            self.db_path,
            query="python",
            workflow_phase="Target 99: nonexistent",
        ),
        [],
    )
    self.assertEqual(
        query_scars(
            self.db_path,
            query="",
            workflow_phase="Target 99: nonexistent",
        ),
        [],
    )
    self.assertEqual(
        query_scars(self.db_path, query="", framework="nonexistent-fw"),
        [],
    )
    self.assertEqual(
        query_scars(
            self.db_path, query="Completer", framework="nonexistent-fw"
        ),
        [],
    )
    self.assertEqual(
        query_scars(
            self.db_path, query="", diagnostic_code="NONEXISTENT_CODE"
        ),
        [],
    )

    # 5. CLI flags --framework, --diagnostic-code, --workflow-phase, --mf-target
    out_cli = io.StringIO()
    code_cli = SyncSyntheticScarsScript().execute(
        args=[
            "--db-path",
            self.db_path,
            "--query",
            "SIGTERM",
            "--framework",
            "pytest",
            "--diagnostic-code",
            "EADDRINUSE",
            "--workflow-phase",
            "Target 30: tdd",
            "--mf-target",
            "30",
        ],
        stdout=out_cli,
    )
    self.assertEqual(code_cli, 0)
    cli_data = json.loads(out_cli.getvalue())
    self.assertEqual(len(cli_data), 1)
    self.assertEqual(cli_data[0]["id"], "SCAR-LANG-PYTHON-SYS-0020")

  def test_bundled_scars_registry_db_is_enriched_and_indexed(self):
    from lib.scar_ontology import DOMAIN_CATEGORY_ONTOLOGY
    import re

    bundled_db = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled_db))

    conn = sqlite3.connect(bundled_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM scars")
    total_scars = cur.fetchone()[0]
    self.assertEqual(total_scars, 599)

    cur.execute("SELECT COUNT(*) FROM scars_fts")
    fts_scars = cur.fetchone()[0]
    self.assertEqual(fts_scars, total_scars)

    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    fts_sql = cur.fetchone()[0]
    self.assertNotIn("id unindexed", fts_sql.lower())
    self.assertIn("ss_id", fts_sql.lower())

    self.assertEqual(len(DOMAIN_CATEGORY_ONTOLOGY), 68)
    canonical_id_re = re.compile(r"^SCAR-(?:LANG-[A-Z0-9]+-)?[A-Z0-9]+-\d{4}$")
    cur.execute(
        "SELECT id, ss_id, source, category, keywords, languages, frameworks,"
        " diagnostic_codes, workflow_phases, ss_targets, mf_targets, original_id FROM scars"
    )
    distinct_categories = set()
    by_ss_id = {}
    by_id = {}
    for row in cur.fetchall():
      scar_id = row[0]
      ss_id = row[1]
      source = row[2]
      category = row[3]
      distinct_categories.add(category)
      if ss_id:
        by_ss_id[ss_id] = (scar_id, category, row[11])
      by_id[scar_id] = (ss_id, category, row[11], source)
      self.assertRegex(scar_id, canonical_id_re)
      self.assertIn(category, DOMAIN_CATEGORY_ONTOLOGY)
      if source == "makeflow":
        self.assertIsNone(ss_id)
      else:
        self.assertTrue(bool(ss_id))

      keywords = json.loads(row[4] or "null")
      languages = json.loads(row[5] or "null")
      frameworks = json.loads(row[6] or "null")
      diag_codes = json.loads(row[7] or "null")
      phases = json.loads(row[8] or "null")
      ss_targets = json.loads(row[9] or "null")
      mf_targets = json.loads(row[10] or "null")

      self.assertIsInstance(keywords, list, f"{scar_id} keywords not a list")
      self.assertGreaterEqual(
          len(keywords), 1, f"{scar_id} keywords must be non-empty"
      )
      self.assertIsInstance(languages, list, f"{scar_id} languages not a list")
      self.assertGreaterEqual(
          len(languages), 1, f"{scar_id} languages must be non-empty"
      )
      self.assertIsInstance(
          frameworks, list, f"{scar_id} frameworks not a list"
      )
      self.assertIsInstance(
          diag_codes, list, f"{scar_id} diagnostic_codes not a list"
      )
      self.assertIsInstance(
          phases, list, f"{scar_id} workflow_phases not a list"
      )
      self.assertGreaterEqual(
          len(phases), 1, f"{scar_id} workflow_phases must be non-empty"
      )
      self.assertIsInstance(
          ss_targets, list, f"{scar_id} ss_targets must be a JSON list"
      )
      self.assertIsInstance(
          mf_targets, list, f"{scar_id} mf_targets must be a JSON list"
      )
      self.assertGreaterEqual(len(mf_targets), 1)
    conn.close()

    # 1-to-1 upstream domain preservation checks across the 59+ categories
    self.assertGreaterEqual(len(distinct_categories), 59)
    self.assertEqual(by_ss_id["SCAR-RIV-01"][1], "RIV")
    self.assertEqual(by_ss_id["SCAR-FLUTTER-01"][1], "FLUTTER")
    self.assertEqual(by_ss_id["SCAR-BLOC-01"][1], "BLOC")
    self.assertEqual(by_ss_id["SCAR-A2UI-01"][1], "A2UI")
    self.assertEqual(by_ss_id["SCAR-OTEL-01"][1], "OTEL")
    self.assertEqual(by_ss_id["SCAR-PUB-08"][:2], ("SCAR-PUB-0008", "PUB"))
    self.assertIn("SCAR-LANG-DART-ASYNC-0001", by_id)
    self.assertEqual(by_id["SCAR-LANG-DART-ASYNC-0001"][2], "SCAR-LANG-DART-01")

    # Unprocessed scars in bundled_db (e.g., SCAR-PROC-11 with socratic_probe=NULL) must never be returned by query_scars
    proc11_results = query_scars(bundled_db, query="SCAR-PROC-11", limit=20)
    self.assertTrue(
        all(
            r.get("ssId") != "SCAR-PROC-11" and r["id"] != "SCAR-PROC-11"
            for r in proc11_results
        )
    )
    all_queried = query_scars(bundled_db, query="SCAR", limit=1000)
    self.assertGreater(len(all_queried), 200)
    for q_row in all_queried:
      for req_key in ("wound", "trap", "reflex", "socraticProbe"):
        self.assertTrue(
            bool(q_row.get(req_key) and str(q_row.get(req_key)).strip()),
            f"{q_row['id']} returned by query_scars missing processed {req_key}",
        )

    id_matches = query_scars(bundled_db, query="SCAR-ARCH-05", limit=5)
    self.assertTrue(
        any(
            m.get("ssId") == "SCAR-ARCH-05"
            and m["id"] == "SCAR-LANG-DART-ARCH-0005"
            for m in id_matches
        )
    )

    dart_timer_matches = query_scars(
        bundled_db, query="fakeAsync", language="dart", limit=5
    )
    self.assertTrue(
        any(
            m["id"] == "SCAR-LANG-DART-ASYNC-0001"
            and m.get("ssId") is None
            and m.get("originalId") == "SCAR-LANG-DART-01"
            for m in dart_timer_matches
        )
    )
    orig_id_matches = query_scars(
        bundled_db, query="SCAR-LANG-DART-01", limit=5
    )
    self.assertTrue(
        any(
            m["id"] == "SCAR-LANG-DART-ASYNC-0001"
            for m in orig_id_matches
        )
    )

    flutter_dispose_matches = query_scars(
        bundled_db,
        query="FocusNode TextEditingController",
        language="dart",
        limit=5,
    )
    self.assertTrue(
        any(
            m.get("ssId") == "SCAR-ARCH-21"
            and m["id"] == "SCAR-LANG-DART-ARCH-0021"
            for m in flutter_dispose_matches
        )
    )

    from lib.scar_candidate import validate_socratic_probe

    for query_term in (
        "SCAR-ARCH-09",
        "SCAR-ARCH-0009",
        "upstream root cause",
    ):
      arch09_matches = query_scars(bundled_db, query=query_term, limit=10)
      arch09_row = next(
          (m for m in arch09_matches if m["id"] == "SCAR-ARCH-0009"), None
      )
      self.assertIsNotNone(
          arch09_row,
          f"Expected SCAR-ARCH-0009 in query_scars({query_term!r}), got {[m['id'] for m in arch09_matches]}",
      )
      self.assertEqual(arch09_row["source"], "makeflow")
      self.assertIsNone(arch09_row.get("ssId"))
      self.assertEqual(arch09_row.get("originalId"), "SCAR-ARCH-09")
      self.assertEqual(arch09_row["category"], "ARCH")
      self.assertEqual(arch09_row["languages"], ["universal"])
      self.assertEqual(arch09_row["mfTargets"], [15, 20, 30, 40])
      self.assertEqual(
          arch09_row["ssTargets"],
          ["Target 20: plan", "Target 30: tdd", "Target 40: review"],
      )
      self.assertGreaterEqual(len(arch09_row["keywords"]), 1)
      self.assertEqual(
          validate_socratic_probe(
              arch09_row["socraticProbe"], scar_id="SCAR-ARCH-0009"
          ),
          [],
      )

    for query_term in (
        "SCAR-ARCH-10",
        "SCAR-ARCH-0010",
        "lossy prose",
    ):
      arch10_matches = query_scars(bundled_db, query=query_term, limit=10)
      arch10_row = next(
          (m for m in arch10_matches if m["id"] == "SCAR-ARCH-0010"), None
      )
      self.assertIsNotNone(
          arch10_row,
          f"Expected SCAR-ARCH-0010 in query_scars({query_term!r}), got {[m['id'] for m in arch10_matches]}",
      )
      self.assertEqual(arch10_row["source"], "makeflow")
      self.assertIsNone(arch10_row.get("ssId"))
      self.assertEqual(arch10_row.get("originalId"), "SCAR-ARCH-10")
      self.assertEqual(arch10_row["category"], "ARCH")
      self.assertEqual(arch10_row["languages"], ["universal"])
      self.assertEqual(arch10_row["mfTargets"], [15, 20, 30, 40])
      self.assertEqual(
          arch10_row["ssTargets"],
          ["Target 20: plan", "Target 30: tdd", "Target 40: review"],
      )
      self.assertGreaterEqual(len(arch10_row["keywords"]), 1)
      self.assertEqual(
          validate_socratic_probe(
              arch10_row["socraticProbe"], scar_id="SCAR-ARCH-0010"
          ),
          [],
      )

    for query_term in (
        "SCAR-TEST-45",
        "SCAR-TEST-0045",
        "ephemeral temporary directory",
    ):
      test45_matches = query_scars(bundled_db, query=query_term, limit=10)
      test45_row = next(
          (m for m in test45_matches if m["id"] == "SCAR-TEST-0045"), None
      )
      self.assertIsNotNone(
          test45_row,
          f"Expected SCAR-TEST-0045 in query_scars({query_term!r}), got {[m['id'] for m in test45_matches]}",
      )
      self.assertEqual(test45_row["source"], "makeflow")
      self.assertIsNone(test45_row.get("ssId"))
      self.assertEqual(test45_row.get("originalId"), "SCAR-TEST-45")
      self.assertEqual(test45_row["category"], "TEST")
      self.assertEqual(test45_row["languages"], ["universal"])
      self.assertEqual(test45_row["mfTargets"], [20, 30, 40])
      self.assertEqual(
          test45_row["ssTargets"],
          ["Target 20: plan", "Target 30: tdd", "Target 40: review"],
      )
      self.assertGreaterEqual(len(test45_row["keywords"]), 1)
      self.assertEqual(
          validate_socratic_probe(
              test45_row["socraticProbe"], scar_id="SCAR-TEST-0045"
          ),
          [],
      )

  def test_sync_preserves_bundled_scar_arch_0009_when_omitted_upstream(self):
    """Verifies SyncSyntheticScarsScript preserves Makeflow-sourced SCAR-ARCH-0009 across upstream syncs (GH-232)."""
    bundled_db = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled_db))

    with tempfile.TemporaryDirectory() as tmpdir:
      tmp_db = os.path.join(tmpdir, "scars_registry.db")
      shutil.copyfile(bundled_db, tmp_db)

      # Upstream registry omits SCAR-ARCH-0009 and SCAR-ARCH-09
      upstream_payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
      script = SyncSyntheticScarsScript()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(upstream_payload, etag='"etag-preserve-arch09"'),
      ):
        code = script.execute(
            args=["--db-path", tmp_db, "--force"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
      self.assertEqual(code, 0)

      for query_term in ("SCAR-ARCH-09", "SCAR-ARCH-0009"):
        matches = query_scars(tmp_db, query=query_term, limit=5)
        matched = next(
            (m for m in matches if m["id"] == "SCAR-ARCH-0009"), None
        )
        self.assertIsNotNone(
            matched,
            f"SCAR-ARCH-0009 lost after sync on query {query_term!r}",
        )
        self.assertEqual(matched["source"], "makeflow")
        self.assertIsNone(matched.get("ssId"))
        self.assertEqual(matched.get("originalId"), "SCAR-ARCH-09")

  def test_sync_preserves_bundled_scar_arch_0010_when_omitted_upstream(self):
    """Verifies SyncSyntheticScarsScript preserves Makeflow-sourced SCAR-ARCH-0010 across upstream syncs (GH-240)."""
    bundled_db = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled_db))

    with tempfile.TemporaryDirectory() as tmpdir:
      tmp_db = os.path.join(tmpdir, "scars_registry.db")
      shutil.copyfile(bundled_db, tmp_db)

      upstream_payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
      script = SyncSyntheticScarsScript()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(
              upstream_payload, etag='"etag-preserve-arch10"'
          ),
      ):
        code = script.execute(
            args=["--db-path", tmp_db, "--force"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
      self.assertEqual(code, 0)

      for query_term in ("SCAR-ARCH-10", "SCAR-ARCH-0010"):
        matches = query_scars(tmp_db, query=query_term, limit=5)
        matched = next(
            (m for m in matches if m["id"] == "SCAR-ARCH-0010"), None
        )
        self.assertIsNotNone(
            matched,
            f"SCAR-ARCH-0010 lost after sync on query {query_term!r}",
        )
        self.assertEqual(matched["source"], "makeflow")
        self.assertIsNone(matched.get("ssId"))
        self.assertEqual(matched.get("originalId"), "SCAR-ARCH-10")

  def test_sync_preserves_bundled_scar_test_0045_when_omitted_upstream(self):
    """Verifies SyncSyntheticScarsScript preserves Makeflow-sourced SCAR-TEST-0045 across upstream syncs (GH-215)."""
    bundled_db = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled_db))

    with tempfile.TemporaryDirectory() as tmpdir:
      tmp_db = os.path.join(tmpdir, "scars_registry.db")
      shutil.copyfile(bundled_db, tmp_db)

      # Upstream registry omits SCAR-TEST-0045 and SCAR-TEST-45
      upstream_payload = json.dumps(SAMPLE_SCARS).encode("utf-8")
      script = SyncSyntheticScarsScript()
      with patch(
          "urllib.request.urlopen",
          return_value=MockHTTPResponse(upstream_payload, etag='"etag-preserve-test45"'),
      ):
        code = script.execute(
            args=["--db-path", tmp_db, "--force"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
      self.assertEqual(code, 0)

      for query_term in ("SCAR-TEST-45", "SCAR-TEST-0045"):
        matches = query_scars(tmp_db, query=query_term, limit=5)
        matched = next(
            (m for m in matches if m["id"] == "SCAR-TEST-0045"), None
        )
        self.assertIsNotNone(
            matched,
            f"SCAR-TEST-0045 lost after sync on query {query_term!r}",
        )
        self.assertEqual(matched["source"], "makeflow")
        self.assertIsNone(matched.get("ssId"))
        self.assertEqual(matched.get("originalId"), "SCAR-TEST-45")


class TestMultiPathStackingAndSessionLedger(unittest.TestCase):
  """Tests 5-tier path stacking, processed-only filtering, and session scar ledger (GH-192)."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()

  def tearDown(self):
    self.temp_dir.cleanup()

  def _create_processed_db(self, db_path, rows):
    script = SyncSyntheticScarsScript()
    script.db_path = db_path
    script._init_db_schema()
    conn = script.conn
    for r in rows:
      conn.execute(
          """
          INSERT OR REPLACE INTO scars (
              id, title, category, category_slug, visibility,
              workflow_phases, applies_when, graduation_status,
              raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
              wound, trap, circuit_breaker, reflex, socratic_probe,
              source, original_id
          ) VALUES (?, ?, ?, ?, 'public', ?, ?, 'graduated', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          """,
          (
              r["id"],
              r.get("title", r["id"]),
              r.get("category", "Architecture"),
              r.get("category_slug", "architecture"),
              json.dumps(r.get("workflow_phases", ["Target 30: tdd"])),
              r.get("applies_when", "Testing stacked databases"),
              r.get("raw_wound", "Raw wound."),
              r.get("raw_trap", "Raw trap."),
              r.get("raw_circuit_breaker", ""),
              r.get("raw_reflex", "Raw reflex."),
              r.get("raw_hash", "hash"),
              r.get("wound"),
              r.get("trap"),
              r.get("circuit_breaker", ""),
              r.get("reflex"),
              r.get("socratic_probe"),
              r.get("source", "upstream"),
              r.get("original_id"),
          ),
      )
    conn.commit()
    script.cleanup(0)
    SyncSyntheticScarsScript().execute(
        args=["--db-path", db_path, "--enrich"], stdout=io.StringIO()
    )

  def test_resolve_scars_db_paths_five_tier_precedence(self):
    from lib.scars_db import resolve_scars_db_paths

    explicit_db = os.path.join(self.temp_dir.name, "tier1_explicit.db")
    env_db = os.path.join(self.temp_dir.name, "tier2_env.db")
    ws_dir = os.path.join(self.temp_dir.name, "workspace")
    ws_cache_db = os.path.join(ws_dir, ".workflow", "cache", "scars_registry.db")
    ws_root_db = os.path.join(ws_dir, ".workflow", "scars_registry.db")
    xdg_dir = os.path.join(self.temp_dir.name, "xdg_cache")
    user_db = os.path.join(xdg_dir, "makeflow", "scars_registry.db")
    pkg_dir = os.path.join(self.temp_dir.name, "pkg")
    bundled_db = os.path.join(
        pkg_dir, "skills", "makeflow", "data", "scars_registry.db"
    )

    for p in (explicit_db, env_db, ws_cache_db, ws_root_db, user_db, bundled_db):
      os.makedirs(os.path.dirname(p), exist_ok=True)
      with open(p, "w", encoding="utf-8") as f:
        f.write("")

    with patch.dict(
        os.environ,
        {"MAKEFLOW_SCARS_DB": env_db, "XDG_CACHE_HOME": xdg_dir},
        clear=False,
    ):
      paths = resolve_scars_db_paths(
          db_path=explicit_db,
          workspace_dir=ws_dir,
          package_root=pkg_dir,
      )

    expected_order = [
        os.path.abspath(explicit_db),
        os.path.abspath(env_db),
        os.path.abspath(ws_cache_db),
        os.path.abspath(ws_root_db),
        os.path.abspath(user_db),
        os.path.abspath(bundled_db),
    ]
    subseq = [p for p in paths if p in expected_order]
    self.assertEqual(subseq, expected_order)
    self.assertEqual(len(paths), len(set(paths)))

  def test_query_scars_excludes_unprocessed_scars_in_fts_and_fallback(self):
    db_path = os.path.join(self.temp_dir.name, "processed_filter.db")
    self._create_processed_db(
        db_path,
        [
            {
                "id": "SCAR-PROC-OK",
                "title": "Processed Target Scar",
                "wound": "Processed wound text.",
                "trap": "Processed trap text.",
                "reflex": "Processed reflex text.",
                "socratic_probe": "Is this scar processed?",
            },
            {
                "id": "SCAR-UNPROC-NULL-WOUND",
                "title": "Unprocessed Null Wound Scar",
                "raw_wound": "Raw untransformed wound Target.",
                "wound": None,
                "trap": "Processed trap.",
                "reflex": "Processed reflex.",
                "socratic_probe": "Has probe?",
            },
            {
                "id": "SCAR-UNPROC-EMPTY-TRAP",
                "title": "Unprocessed Empty Trap Scar",
                "wound": "Processed wound.",
                "trap": "   ",
                "reflex": "Processed reflex.",
                "socratic_probe": "Has probe?",
            },
            {
                "id": "SCAR-UNPROC-NULL-PROBE",
                "title": "Unprocessed Null Probe Scar",
                "wound": "Processed wound.",
                "trap": "Processed trap.",
                "reflex": "Processed reflex.",
                "socratic_probe": None,
            },
        ],
    )

    fts_results = query_scars(db_path, query="Scar", limit=10)
    self.assertEqual([r["id"] for r in fts_results], ["SCAR-PROC-OK"])

    # Drop scars_fts to exercise the LIKE fallback path as well
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE scars_fts")
    conn.commit()
    conn.close()

    fallback_results = query_scars(db_path, query="Scar", limit=10)
    self.assertEqual([r["id"] for r in fallback_results], ["SCAR-PROC-OK"])

  def test_query_scars_stacks_multiple_databases_and_deduplicates_by_id_and_original_id(self):
    ws_db = os.path.join(self.temp_dir.name, "ws_scars.db")
    bundled_db = os.path.join(self.temp_dir.name, "bundled_scars.db")

    self._create_processed_db(
        ws_db,
        [
            {
                "id": "SCAR-ARCH-05",
                "title": "Workspace Override BaseScript",
                "wound": "Workspace wound.",
                "trap": "Workspace trap.",
                "reflex": "Workspace reflex.",
                "socratic_probe": "Did you use the workspace BaseScript?",
            },
            {
                "id": "SCAR-LOCAL-01",
                "title": "Workspace Local BaseScript Rule",
                "wound": "Local wound.",
                "trap": "Local trap.",
                "reflex": "Local reflex.",
                "socratic_probe": "Did you follow the local rule?",
            },
        ],
    )

    self._create_processed_db(
        bundled_db,
        [
            {
                "id": "SCAR-ARCH-05",
                "title": "Bundled BaseScript",
                "wound": "Bundled wound.",
                "trap": "Bundled trap.",
                "reflex": "Bundled reflex.",
                "socratic_probe": "Did you use the bundled BaseScript?",
            },
            {
                "id": "SCAR-MF-LOCAL-01",
                "original_id": "SCAR-LOCAL-01",
                "source": "makeflow",
                "title": "Bundled Renamed Local BaseScript Rule",
                "wound": "Bundled local wound.",
                "trap": "Bundled local trap.",
                "reflex": "Bundled local reflex.",
                "socratic_probe": "Did you follow bundled local rule?",
            },
            {
                "id": "SCAR-BUNDLED-99",
                "title": "Bundled Extra BaseScript Rule",
                "wound": "Extra wound.",
                "trap": "Extra trap.",
                "reflex": "Extra reflex.",
                "socratic_probe": "Did you follow the extra rule?",
            },
        ],
    )

    stacked = query_scars([ws_db, bundled_db], query="BaseScript", limit=10)
    ids = [r["id"] for r in stacked]
    self.assertEqual(
        ids, ["SCAR-ARCH-05", "SCAR-LOCAL-01", "SCAR-BUNDLED-99"]
    )
    self.assertEqual(stacked[0]["title"], "Workspace Override BaseScript")

  def test_session_scar_ledger_load_save_partition_and_ticket_reset(self):
    from lib.scars_db import (
        detect_active_ticket_id,
        format_loaded_scar_reminder,
        load_session_scars,
        partition_session_scars,
        save_session_scars,
    )

    state_dir = os.path.join(self.temp_dir.name, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    ticket_card = os.path.join(state_dir, "10_ticket.json")
    with open(ticket_card, "w", encoding="utf-8") as f:
      json.dump({"ticket_id": "GH-192", "title": "Stack scar queries"}, f)
    # Quoted ticket ID in 10_ticket.md prose must not hijack detect_active_ticket_id
    with open(
        os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
    ) as f:
      f.write("See GH-999 before GH-192.\n")

    self.assertEqual(detect_active_ticket_id(state_dir), "GH-192")
    self.assertEqual(load_session_scars(state_dir=state_dir), set())

    sample_matches = [
        {
            "id": "SCAR-A",
            "title": "First Scar",
            "category": "ARCH",
            "categorySlug": "arch",
            "source": "upstream",
            "originalId": None,
            "original_id": None,
            "wound": "Full wound A.",
            "trap": "Full trap A.",
            "circuitBreaker": "",
            "reflex": "Full reflex A.",
            "socraticProbe": "Probe A?",
            "socratic_probe": "Probe A?",
        },
        {
            "id": "SCAR-B",
            "title": "Second Scar",
            "category": "TEST",
            "categorySlug": "test",
            "source": "upstream",
            "originalId": None,
            "original_id": None,
            "wound": "Full wound B.",
            "trap": "Full trap B.",
            "circuitBreaker": "",
            "reflex": "Full reflex B.",
            "socraticProbe": "Probe B?",
            "socratic_probe": "Probe B?",
        },
        {
            "id": "SCAR-C",
            "title": "Third Scar",
            "category": "SYS",
            "categorySlug": "sys",
            "source": "upstream",
            "originalId": None,
            "original_id": None,
            "wound": "Full wound C.",
            "trap": "Full trap C.",
            "circuitBreaker": "",
            "reflex": "Full reflex C.",
            "socraticProbe": "Probe C?",
            "socratic_probe": "Probe C?",
        },
    ]

    # 1. First query loads SCAR-A and SCAR-B
    scars_1, already_1, updated_1 = partition_session_scars(
        sample_matches, set(), limit=2
    )
    self.assertEqual([s["id"] for s in scars_1], ["SCAR-A", "SCAR-B"])
    self.assertEqual(already_1, [])
    self.assertEqual(updated_1, {"SCAR-A", "SCAR-B"})
    save_session_scars(updated_1, state_dir=state_dir)
    self.assertEqual(
        load_session_scars(state_dir=state_dir), {"SCAR-A", "SCAR-B"}
    )

    # 2. Second query skips SCAR-A & SCAR-B in primary scars, returns SCAR-C + compact reminders
    scars_2, already_2, updated_2 = partition_session_scars(
        sample_matches, load_session_scars(state_dir=state_dir), limit=2
    )
    self.assertEqual([s["id"] for s in scars_2], ["SCAR-C"])
    self.assertIn("wound", scars_2[0])
    self.assertEqual([r["id"] for r in already_2], ["SCAR-A", "SCAR-B"])
    for rem in already_2:
      self.assertTrue(rem.get("alreadyLoaded"))
      self.assertIn("reminder", rem)
      self.assertIn("socraticProbe", rem)
      self.assertNotIn("wound", rem)
      self.assertNotIn("trap", rem)
      self.assertNotIn("reflex", rem)
    save_session_scars(updated_2, state_dir=state_dir)

    # 3. Third query when all matches are already loaded returns compact reminders in scars_out
    scars_3, already_3, _ = partition_session_scars(
        sample_matches[:2], load_session_scars(state_dir=state_dir), limit=2
    )
    self.assertEqual([s["id"] for s in scars_3], ["SCAR-A", "SCAR-B"])
    self.assertTrue(all(s.get("alreadyLoaded") for s in scars_3))
    self.assertEqual(len(already_3), 2)

    # 4. refresh_loaded=True returns full scars again
    scars_ref, already_ref, _ = partition_session_scars(
        sample_matches[:2],
        load_session_scars(state_dir=state_dir),
        limit=2,
        refresh_loaded=True,
    )
    self.assertEqual([s["id"] for s in scars_ref], ["SCAR-A", "SCAR-B"])
    self.assertTrue(all("wound" in s for s in scars_ref))
    self.assertEqual(already_ref, [])

    # 5. Changing active ticket in 10_ticket.json resets loaded scars automatically
    with open(ticket_card, "w", encoding="utf-8") as f:
      json.dump({"ticket_id": "GH-193", "title": "Next ticket"}, f)
    self.assertEqual(load_session_scars(state_dir=state_dir), set())


class TestScarOntologyAndTargets(unittest.TestCase):
  """Tests canonical ontology mapping, language detection, 4-digit IDs, and shift-left targets (GH-193)."""

  def test_ontology_covers_all_60_upstream_categories(self):
    from lib.scar_ontology import (
        CATEGORY_SYNONYM_MAP,
        DOMAIN_CATEGORY_ONTOLOGY,
        UPSTREAM_CATEGORY_MAP,
        normalize_domain_category,
        resolve_category_filter_tokens,
    )

    self.assertIn("WEB", DOMAIN_CATEGORY_ONTOLOGY)
    self.assertEqual(len(DOMAIN_CATEGORY_ONTOLOGY), 68)
    for code, desc in DOMAIN_CATEGORY_ONTOLOGY.items():
      self.assertTrue(code.isupper() and code.isalnum())
      self.assertTrue(bool(desc.strip()))

    upstream_db_categories = (
        "A2UI", "API", "ARCH", "AST", "ASYNC", "AUTH", "BLOC", "BUILD", "CI",
        "COLLAB", "CONC", "CRAFT", "DATA", "DEP", "DIAG", "DIFF", "DIR", "DOC",
        "ERR", "FAIL", "FLUTTER", "GENUI", "GEOM", "GIT", "GOV", "HCI", "HOOK",
        "INTEROP", "INV", "LANG", "META", "METRIC", "MIXIN", "NAV", "OBS",
        "OTEL", "PEDAGOGY", "PERF", "PROC", "PUB", "REFACTOR", "REGEX", "REVIEW",
        "RIV", "SCRIPT", "SEC", "SKILL", "SSR", "STATE", "STORAGE", "SYNTAX",
        "TEST", "TIME", "TMPL", "TOOL", "TYPE", "UI", "VERIF", "WEB", "WIRE",
    )
    self.assertEqual(len(upstream_db_categories), 60)
    for raw_cat in upstream_db_categories:
      if raw_cat != "LANG":
        self.assertIn(raw_cat, DOMAIN_CATEGORY_ONTOLOGY)
        self.assertEqual(UPSTREAM_CATEGORY_MAP[raw_cat], raw_cat)
        self.assertEqual(normalize_domain_category(raw_cat), raw_cat)
      else:
        self.assertIn(raw_cat, UPSTREAM_CATEGORY_MAP)
        self.assertIn(UPSTREAM_CATEGORY_MAP[raw_cat], DOMAIN_CATEGORY_ONTOLOGY)
      norm = normalize_domain_category(raw_cat)
      self.assertIn(norm, DOMAIN_CATEGORY_ONTOLOGY)

    # All 59 non-LANG upstream codes preserve 1-to-1; Makeflow's 9 core codes preserve 1-to-1
    for preserved in (
        "FLUTTER", "BLOC", "RIV", "A2UI", "OTEL", "PUB", "REGEX", "SSR",
        "HOOK", "SCRIPT", "INV", "CRAFT", "DIAG", "FAIL", "GEOM", "METRIC",
        "NAV", "SKILL", "SYNTAX", "TIME", "TMPL", "VERIF",
        "A11Y", "DB", "ENV", "I18N", "MATH", "NET", "SCHEMA", "SH", "SYS",
    ):
      self.assertEqual(normalize_domain_category(preserved), preserved)

    # Bare language tags and unknown categories infer canonical domain (or SCAR prefix)
    self.assertEqual(
        normalize_domain_category("LANG", title="Python AST Scrubber Strict Formatting"),
        "AST",
    )
    self.assertEqual(
        normalize_domain_category("LANG", title="Async Timers fakeAsync"),
        "ASYNC",
    )
    self.assertEqual(
        normalize_domain_category("DART", title="Generic variance in TypeScript"),
        "TYPE",
    )
    self.assertEqual(
        normalize_domain_category("POLYGLOT", title="Hermetic fixture isolation in tests"),
        "TEST",
    )
    self.assertEqual(
        normalize_domain_category("RUST", title="Cargo.lock dependency pinning"),
        "DEP",
    )
    self.assertEqual(
        normalize_domain_category("PERL", title="Perl scripting conventions"),
        "ARCH",
    )
    self.assertEqual(
        normalize_domain_category("", scar_id="SCAR-FLUTTER-02", title="Anything"),
        "FLUTTER",
    )
    self.assertEqual(
        normalize_domain_category("UNKNOWN_CUSTOM", title="Async cancellation"),
        "ASYNC",
    )

    # Human prose aliases normalize to canonical codes
    for prose, expected_code in (
        ("Architecture", "ARCH"),
        ("Testing", "TEST"),
        ("Process", "PROC"),
        ("Security", "SEC"),
        ("Database", "DB"),
        ("System", "SYS"),
        ("Environment", "ENV"),
        ("Documentation", "DOC"),
        ("Tooling", "TOOL"),
    ):
      self.assertEqual(normalize_domain_category(prose), expected_code)

    # Category synonym tokens match prose aliases cleanly with zero cross-domain bleeding
    self.assertNotIn("STATE", CATEGORY_SYNONYM_MAP.get("RIV", ()))
    self.assertNotIn("RIV", CATEGORY_SYNONYM_MAP.get("STATE", ()))
    self.assertNotIn("UI", resolve_category_filter_tokens("FLUTTER"))
    self.assertNotIn("STATE", resolve_category_filter_tokens("RIV"))
    self.assertIn("TEST", resolve_category_filter_tokens("TESTING"))
    self.assertIn("TESTING", resolve_category_filter_tokens("TEST"))

  def test_language_rules_and_canonical_four_digit_ids(self):
    from lib.scar_ontology import compute_canonical_scar_id, select_primary_language
    from lib.scars_db import extract_scar_metadata

    # SCAR-PUB-08 social scar stays universal (not mislabeled 'dart')
    pub_meta = extract_scar_metadata(
        {
            "id": "SCAR-PUB-08",
            "title": "Public launch communication trap",
            "category": "PUB",
            "applies_when": "Announcing libraries on public forums",
            "wound": "Premature hype triggers backlash.",
            "trap": "Posting unfinished demos.",
            "circuit_breaker": "Verify docs first.",
            "reflex": "Wait until tests pass.",
        }
    )
    self.assertEqual(pub_meta["languages"], ["universal"])

    # SQLite in Python app should add framework 'sqlite' without false 'sql' language
    sqlite_meta = extract_scar_metadata(
        {
            "id": "SCAR-STORAGE-02",
            "title": "FastAPI SQLite checkpointing",
            "category": "STORAGE",
            "applies_when": "Using SQLite files inside Python FastAPI services",
            "wound": "SQLite WAL left unclosed.",
            "trap": "Dropping handle.",
            "circuit_breaker": "Close connection cleanly.",
            "reflex": "Close Python sqlite3 handle cleanly.",
        }
    )
    self.assertEqual(sqlite_meta["languages"], ["python"])
    self.assertIn("sqlite", sqlite_meta["frameworks"])

    # Framework detection for A2UI, GenUI, OpenTelemetry
    fw_meta = extract_scar_metadata(
        {
            "id": "SCAR-A2UI-01",
            "title": "A2UI GenUI schema with OpenTelemetry",
            "category": "A2UI",
            "applies_when": "Tracing GenUI widgets via OpenTelemetry spans",
            "wound": "Missing OTEL trace.",
            "trap": "Broken A2UI tree.",
            "circuit_breaker": "",
            "reflex": "Validate GenUI catalog.",
        }
    )
    for fw in ("a2ui", "genui", "opentelemetry"):
      self.assertIn(fw, fw_meta["frameworks"])

    # Language precedence + incidental helper stripping + 4-digit deterministic index
    self.assertEqual(select_primary_language(["dart", "bash"], "ARCH"), "dart")
    self.assertEqual(select_primary_language(["python", "sql"], "DB"), "python")
    self.assertIsNone(select_primary_language(["dart", "c"], "WEB"))
    self.assertIsNone(select_primary_language(["universal"], "TEST"))

    reserved = set()
    self.assertEqual(
        compute_canonical_scar_id("SCAR-ARCH-05", "ARCH", ["dart"], reserved),
        "SCAR-LANG-DART-ARCH-0005",
    )
    self.assertEqual(
        compute_canonical_scar_id("SCAR-ARCH-05", "ARCH", ["dart"], reserved),
        "SCAR-LANG-DART-ARCH-0001",
    )
    self.assertEqual(
        compute_canonical_scar_id("SCAR-PUB-08", "PUB", ["universal"], reserved),
        "SCAR-PUB-0008",
    )
    self.assertEqual(
        compute_canonical_scar_id("SCAR-CI-02", "CI", ["dart", "bash"], reserved),
        "SCAR-LANG-DART-CI-0002",
    )
    self.assertEqual(
        compute_canonical_scar_id("SCAR-WEB-03", "WEB", ["dart", "c"], reserved),
        "SCAR-WEB-0003",
    )

  def test_parse_upstream_targets_and_shift_left_never_right(self):
    from lib.scar_ontology import (
        compute_makeflow_targets,
        parse_upstream_targets,
        shift_left_targets,
    )

    self.assertEqual(
        parse_upstream_targets(["Target 0", "Target 4a", "4b", "4c", "7a", "7b", "Step 15"]),
        [10, 40, 42, 45, 70, 75, 90],
    )
    self.assertEqual(
        parse_upstream_targets(["Step 4 (Plan)", "Step 6 (TDD)", "Step 14"]),
        [20, 30, 75],
    )

    # BLOCKER-2: Explicit unrecognized Target/Step numbers do NOT fall back to substrings
    self.assertEqual(parse_upstream_targets(["Target 99: review"]), [])
    self.assertEqual(parse_upstream_targets(["Target 99: plan"]), [])
    self.assertEqual(parse_upstream_targets(["Target 99: nonexistent"]), [])
    self.assertEqual(parse_upstream_targets(["Step 9: Review commit diff"]), [40])
    # Word-boundary fallback rejects partial substrings and false positives
    self.assertEqual(
        parse_upstream_targets(["explanation", "uncommitted", "emergency", "unfinished", "nonexistent phase"]),
        [],
    )
    self.assertEqual(parse_upstream_targets(["plan", "review"]), [20, 40])

    # ARCH/DATA/SEC/DOC/PUB/INV/SKILL at 30 or 40 promote left to 15, 20, 30
    arch_targets, arch_labels = compute_makeflow_targets(["Target 4"], "ARCH")
    self.assertEqual(arch_targets, [15, 20, 30, 40])
    self.assertIn("Target 15: archaeology", arch_labels)
    self.assertIn("Target 20: plan", arch_labels)
    self.assertEqual(shift_left_targets([40], "PUB"), [15, 20, 30, 40])
    self.assertEqual(shift_left_targets([40], "INV"), [15, 20, 30, 40])
    self.assertEqual(shift_left_targets([30], "PUB"), [15, 20, 30])
    self.assertEqual(shift_left_targets([40], "SKILL"), [15, 20, 30, 40])

    # UI/FLUTTER/TEST/STATE/RIV/BLOC/BUILD only at 40 promote left to 20, 30, 40
    self.assertEqual(shift_left_targets([40], "UI"), [20, 30, 40])
    self.assertEqual(shift_left_targets([40], "FLUTTER"), [20, 30, 40])
    self.assertEqual(shift_left_targets([40], "RIV"), [20, 30, 40])
    self.assertEqual(shift_left_targets([40], "BLOC"), [20, 30, 40])
    self.assertEqual(shift_left_targets([40], "VERIF"), [20, 30, 40])
    self.assertEqual(shift_left_targets([40], "TEST"), [20, 30, 40])
    # ERR/SYS/OTEL/HOOK only at 40 promote left to 30, 40
    self.assertEqual(shift_left_targets([40], "ERR"), [30, 40])
    self.assertEqual(shift_left_targets([40], "OTEL"), [30, 40])
    self.assertEqual(shift_left_targets([40], "HOOK"), [30, 40])
    # NEVER shift right to add 40 when absent
    self.assertEqual(shift_left_targets([30], "ARCH"), [15, 20, 30])
    self.assertEqual(shift_left_targets([20, 30], "UI"), [20, 30])
    self.assertNotIn(40, shift_left_targets([20, 30], "STATE"))
    self.assertNotIn(40, shift_left_targets([20, 30], "RIV"))


class TestSchemaRebuildSsIdAndMigration(unittest.TestCase):
  """Tests id NOT NULL table rebuild, ss_id indexing, stacked dedup, and lossless migration (GH-193)."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.temp_dir.name, "scars_rebuild.db")

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_legacy_nullable_id_rebuilt_not_null_and_ss_id_partial_unique_index(self):
    conn = sqlite3.connect(self.db_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
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
            id, title, category, category_slug, visibility, workflow_phases,
            applies_when, graduation_status, raw_wound, raw_trap,
            raw_circuit_breaker, raw_reflex, raw_hash, wound, trap,
            circuit_breaker, reflex, socratic_probe, source, original_id
        ) VALUES (
            'SCAR-ARCH-05', 'Architecture Boundary', 'ARCH', 'arch', 'public',
            '["Target 4"]', 'Writing Python layers', 'graduated',
            'Raw wound.', 'Raw trap.', 'If bad, stop.', 'Keep Python layers clean.',
            'h1', 'Simple wound.', 'Simple trap.', 'If bad, stop.',
            'Keep Python layers clean.', 'Did you separate layers cleanly?',
            'upstream', NULL
        )
        """
    )
    conn.commit()
    conn.close()

    script = SyncSyntheticScarsScript()
    script.db_path = self.db_path
    script._init_db_schema()
    conn = script.conn

    info = {r[1]: r for r in conn.execute("PRAGMA table_info(scars)").fetchall()}
    self.assertEqual(info["id"][3], 1, "id column must be NOT NULL after rebuild")
    for col in ("ss_id", "ss_targets", "mf_targets"):
      self.assertIn(col, info)

    row = conn.execute(
        "SELECT id, wound, socratic_probe FROM scars WHERE id = 'SCAR-ARCH-05'"
    ).fetchone()
    self.assertEqual(row, ("SCAR-ARCH-05", "Simple wound.", "Did you separate layers cleanly?"))

    # Partial unique index permits multiple NULL ss_id values but blocks duplicate non-null ss_id
    conn.execute(
        "INSERT INTO scars (id, ss_id, title, wound, trap, reflex, socratic_probe) VALUES ('M1', NULL, 'M1', 'W.', 'T.', 'R.', 'Q?')"
    )
    conn.execute(
        "INSERT INTO scars (id, ss_id, title, wound, trap, reflex, socratic_probe) VALUES ('M2', NULL, 'M2', 'W.', 'T.', 'R.', 'Q?')"
    )
    conn.execute(
        "INSERT INTO scars (id, ss_id, title, wound, trap, reflex, socratic_probe) VALUES ('U1', 'UP-1', 'U1', 'W.', 'T.', 'R.', 'Q?')"
    )
    with self.assertRaises(sqlite3.IntegrityError):
      conn.execute(
          "INSERT INTO scars (id, ss_id, title, wound, trap, reflex, socratic_probe) VALUES ('U2', 'UP-1', 'U2', 'W.', 'T.', 'R.', 'Q?')"
      )
    conn.commit()
    script.cleanup(0)

  def test_ss_id_fts_and_like_fallback_plus_stacked_deduplication(self):
    ws_db = os.path.join(self.temp_dir.name, "ws_stack.db")
    bundled_db = os.path.join(self.temp_dir.name, "bundled_stack.db")

    for path, rows in (
        (
            ws_db,
            [
                (
                    "SCAR-LANG-PYTHON-ARCH-0005",
                    "SCAR-ARCH-05",
                    "Workspace Override Architecture",
                    "ARCH",
                    "arch",
                    '["Target 15: archaeology", "Target 30: tdd"]',
                    '["Target 3"]',
                    "[15, 20, 30]",
                    "Workspace wound text.",
                    "Workspace trap text.",
                    "",
                    "Workspace reflex text.",
                    "Did workspace rule run?",
                    "upstream",
                    None,
                )
            ],
        ),
        (
            bundled_db,
            [
                (
                    "SCAR-ARCH-05",
                    "SCAR-ARCH-05",
                    "Bundled Architecture",
                    "ARCH",
                    "arch",
                    '["Target 40: review"]',
                    '["Target 4"]',
                    "[40]",
                    "Bundled wound text.",
                    "Bundled trap text.",
                    "",
                    "Bundled reflex text.",
                    "Did bundled rule run?",
                    "upstream",
                    None,
                ),
                (
                    "SCAR-LANG-DART-RIV-0002",
                    "SCAR-RIV-02",
                    "Riverpod State Check",
                    "RIV",
                    "riv",
                    '["Target 20: plan", "Target 30: tdd"]',
                    '["Target 2", "Target 3"]',
                    "[20, 30]",
                    "State wound text.",
                    "State trap text.",
                    "",
                    "State reflex text.",
                    "Did you isolate state?",
                    "upstream",
                    None,
                ),
            ],
        ),
    ):
      s = SyncSyntheticScarsScript()
      s.db_path = path
      s._init_db_schema()
      for r in rows:
        s.conn.execute(
            """
            INSERT INTO scars (
                id, ss_id, title, category, category_slug, visibility,
                workflow_phases, ss_targets, mf_targets, applies_when,
                graduation_status, wound, trap, circuit_breaker, reflex,
                socratic_probe, source, original_id
            ) VALUES (?, ?, ?, ?, ?, 'public', ?, ?, ?, 'Testing', 'graduated', ?, ?, ?, ?, ?, ?, ?)
            """,
            r,
        )
      s.conn.commit()
      s.cleanup(0)
      SyncSyntheticScarsScript().execute(
          args=["--db-path", path, "--enrich"], stdout=io.StringIO()
      )

    # ss_targets must stay preserved verbatim across re-enrichment
    conn_ws = sqlite3.connect(ws_db)
    self.assertEqual(
        json.loads(
            conn_ws.execute(
                "SELECT ss_targets FROM scars WHERE id = 'SCAR-LANG-PYTHON-ARCH-0005'"
            ).fetchone()[0]
        ),
        ["Target 3"],
    )
    conn_ws.close()

    # Stacked dedup across id / ss_id prefers workspace DB copy
    stacked = query_scars([ws_db, bundled_db], query="SCAR-ARCH-05", limit=10)
    self.assertEqual(len(stacked), 1)
    self.assertEqual(stacked[0]["id"], "SCAR-LANG-PYTHON-ARCH-0005")
    self.assertEqual(stacked[0]["ssId"], "SCAR-ARCH-05")

    # 1-to-1 domain filtering + mf_target integer filtering (no cross-domain bleed)
    by_alias = query_scars(bundled_db, query="", category="RIV", mf_target=20)
    self.assertEqual([r["id"] for r in by_alias], ["SCAR-LANG-DART-RIV-0002"])
    self.assertEqual(query_scars(bundled_db, query="", category="STATE"), [])

    # Drop scars_fts to verify ss_id LIKE fallback search
    conn_b = sqlite3.connect(bundled_db)
    conn_b.execute("DROP TABLE scars_fts")
    conn_b.commit()
    conn_b.close()
    fallback_res = query_scars(bundled_db, query="SCAR-RIV-02", limit=5)
    self.assertEqual([r["id"] for r in fallback_res], ["SCAR-LANG-DART-RIV-0002"])

  def test_stacked_dedup_disjoint_makeflow_and_upstream_namespaces(self):
    ws_db = os.path.join(self.temp_dir.name, "ws_disjoint.db")
    bundled_db = os.path.join(self.temp_dir.name, "bundled_disjoint.db")

    for path, rows in (
        (
            ws_db,
            [
                (
                    "SCAR-ARCH-05",
                    None,
                    "Makeflow Architecture Rule",
                    "ARCH",
                    "arch",
                    '["Target 20: plan"]',
                    '["Target 20: plan"]',
                    "[20]",
                    "Makeflow architectural boundary.",
                    "Tight coupling trap.",
                    "",
                    "Keep layers separate.",
                    "Did you isolate the boundary?",
                    "makeflow",
                    None,
                )
            ],
        ),
        (
            bundled_db,
            [
                (
                    "SCAR-LANG-DART-ARCH-0005",
                    "SCAR-ARCH-05",
                    "Upstream Dart Architecture Rule",
                    "ARCH",
                    "arch",
                    '["Target 30: tdd"]',
                    '["Target 3"]',
                    "[15, 20, 30]",
                    "Upstream architectural boundary.",
                    "Circular import trap.",
                    "",
                    "Use one-way imports in Dart.",
                    "Did you check imports?",
                    "upstream",
                    None,
                )
            ],
        ),
    ):
      s = SyncSyntheticScarsScript()
      s.db_path = path
      s._init_db_schema()
      for r in rows:
        s.conn.execute(
            """
            INSERT INTO scars (
                id, ss_id, title, category, category_slug, visibility,
                workflow_phases, ss_targets, mf_targets, applies_when,
                graduation_status, wound, trap, circuit_breaker, reflex,
                socratic_probe, source, original_id
            ) VALUES (?, ?, ?, ?, ?, 'public', ?, ?, ?, 'Testing', 'graduated', ?, ?, ?, ?, ?, ?, ?)
            """,
            r,
        )
      s.conn.commit()
      s.cleanup(0)
      SyncSyntheticScarsScript().execute(
          args=["--db-path", path, "--enrich"], stdout=io.StringIO()
      )

    # Disjoint namespaces: Makeflow id SCAR-ARCH-05 NEVER suppresses upstream ss_id SCAR-ARCH-05
    coexist = query_scars([ws_db, bundled_db], query="SCAR-ARCH-05", limit=10)
    self.assertEqual(len(coexist), 2)
    self.assertEqual(
        {r["id"] for r in coexist},
        {"SCAR-ARCH-05", "SCAR-LANG-DART-ARCH-0005"},
    )

  def test_migrate_scars_ontology_lossless_cli_and_verify(self):
    from lib.scars_db import migrate_scars_ontology

    conn = sqlite3.connect(self.db_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
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
    rows = [
        (
            "SCAR-RIV-01",
            "Riverpod AsyncNotifier Guard",
            "RIV",
            "riv",
            "public",
            '["Target 4"]',
            "Using Flutter Riverpod AsyncNotifier",
            "graduated",
            "Raw wound with $x = 1$.",
            "Raw trap.",
            "Stop now.",
            "Check `!isDisposed` in Dart.",
            "h1",
            "Simple wound with $x = 1$.",
            "Simple trap.",
            "Stop now.",
            "Check disposal in Dart.",
            "Did you check disposal?",
            "upstream",
            None,
        ),
        (
            "SCAR-PUB-08",
            "Public launch restraint",
            "PUB",
            "pub",
            "public",
            '["Target 3"]',
            "Announcing projects publicly",
            "graduated",
            "Raw communication wound.",
            "Raw trap.",
            "",
            "Wait until verified.",
            "h2",
            None,
            None,
            "",
            None,
            None,
            "upstream",
            None,
        ),
        (
            "SCAR-LANG-DART-01",
            "Async Timers fakeAsync",
            "LANG",
            "lang",
            "public",
            '["Target 3"]',
            "Testing Dart async timers",
            "graduated",
            "Raw timer wound.",
            "Raw trap.",
            "",
            "Use `fakeAsync` in Dart.",
            "h3",
            "Simple timer wound.",
            "Simple timer trap.",
            "",
            "Use `fakeAsync` in Dart.",
            "Did you pump the microtask queue?",
            "makeflow",
            None,
        ),
    ]
    for row in rows:
      conn.execute("INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    conn.commit()
    conn.close()

    out = io.StringIO()
    err = io.StringIO()
    code = SyncSyntheticScarsScript().execute(
        args=["--db-path", self.db_path, "--migrate-ontology"],
        stdout=out,
        stderr=err,
    )
    self.assertEqual(code, 0, err.getvalue())

    conn = sqlite3.connect(self.db_path)
    migrated = {
        r[0]: r[1:]
        for r in conn.execute(
            "SELECT id, ss_id, category, ss_targets, mf_targets, wound, socratic_probe, source, original_id FROM scars"
        ).fetchall()
    }
    conn.close()

    self.assertIn("SCAR-LANG-DART-RIV-0001", migrated)
    riv = migrated["SCAR-LANG-DART-RIV-0001"]
    self.assertEqual(riv[0], "SCAR-RIV-01")
    self.assertEqual(riv[1], "RIV")
    self.assertEqual(json.loads(riv[2]), ["Target 4"])
    self.assertEqual(json.loads(riv[3]), [20, 30, 40])
    self.assertEqual(riv[4], "Simple wound with $x = 1$.")
    self.assertEqual(riv[5], "Did you check disposal?")

    self.assertIn("SCAR-PUB-0008", migrated)
    self.assertEqual(migrated["SCAR-PUB-0008"][0], "SCAR-PUB-08")
    self.assertEqual(migrated["SCAR-PUB-0008"][1], "PUB")

    self.assertIn("SCAR-LANG-DART-ASYNC-0001", migrated)
    self.assertIsNone(migrated["SCAR-LANG-DART-ASYNC-0001"][0])
    self.assertEqual(migrated["SCAR-LANG-DART-ASYNC-0001"][6], "makeflow")
    # NBO-1: renamed Makeflow scar records original_id for backward lookup
    self.assertEqual(migrated["SCAR-LANG-DART-ASYNC-0001"][7], "SCAR-LANG-DART-01")
    by_orig = query_scars(self.db_path, query="SCAR-LANG-DART-01", limit=5)
    self.assertEqual([r["id"] for r in by_orig], ["SCAR-LANG-DART-ASYNC-0001"])

    # --verify-transformed exits 0 on preserved transformed rows
    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=["--db-path", self.db_path, "--verify-transformed"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ),
        0,
    )
    # Re-running migrate_scars_ontology is idempotent
    second = migrate_scars_ontology(self.db_path)
    self.assertEqual(second["migrated_scars"], 3)

  def test_sync_preserves_transformed_fields_over_pre_gh193_db_with_matching_raw_hash(self):
    item = {
        "id": "SCAR-RIV-01",
        "title": "Riverpod AsyncNotifier Guard",
        "category": "RIV",
        "categorySlug": "riv",
        "visibility": "public",
        "workflowPhases": ["Target 4"],
        "appliesWhen": "Using Flutter Riverpod AsyncNotifier",
        "graduationStatus": "graduated",
        "wound": "Raw riverpod disposal wound with $x = 1$.",
        "trap": "Raw riverpod trap.",
        "circuitBreaker": "If disposed, stop.",
        "reflex": "Check `!isDisposed` in Dart.",
    }
    raw_hash = hashlib.md5(
        json.dumps(item, sort_keys=True).encode("utf-8")
    ).hexdigest()

    conn = sqlite3.connect(self.db_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
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
        INSERT INTO scars VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upstream', NULL
        )
        """,
        (
            "SCAR-RIV-01",
            item["title"],
            "RIV",
            "riv",
            "public",
            '["Target 4"]',
            item["appliesWhen"],
            "graduated",
            item["wound"],
            item["trap"],
            item["circuitBreaker"],
            item["reflex"],
            raw_hash,
            "Simple pre-GH-193 wound with $x = 1$.",
            "Simple pre-GH-193 trap.",
            "If disposed, stop.",
            "Check `disposed` in Dart.",
            "Did you guard disposed state before updating?",
        ),
    )
    conn.commit()
    conn.close()

    payload = json.dumps({"scars": [item]}).encode("utf-8")
    out = io.StringIO()
    err = io.StringIO()
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-pre-gh193"'),
    ):
      code = SyncSyntheticScarsScript().execute(
          args=["--db-path", self.db_path], stdout=out, stderr=err
      )
    self.assertEqual(code, 0, err.getvalue())
    self.assertNotIn("Pending Simple English transformations", out.getvalue())

    conn = sqlite3.connect(self.db_path)
    row = conn.execute(
        "SELECT id, ss_id, category, ss_targets, mf_targets,"
        " wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE ss_id = 'SCAR-RIV-01'"
    ).fetchone()
    conn.close()
    self.assertIsNotNone(row)
    self.assertEqual(row[0], "SCAR-LANG-DART-RIV-0001")
    self.assertEqual(row[1], "SCAR-RIV-01")
    self.assertEqual(row[2], "RIV")
    self.assertEqual(json.loads(row[3]), ["Target 4"])
    self.assertEqual(json.loads(row[4]), [20, 30, 40])
    # BLOCKER-1: Pre-existing Simple English & Socratic Probe columns preserved byte-for-byte
    self.assertEqual(row[5], "Simple pre-GH-193 wound with $x = 1$.")
    self.assertEqual(row[6], "Simple pre-GH-193 trap.")
    self.assertEqual(row[7], "If disposed, stop.")
    self.assertEqual(row[8], "Check `disposed` in Dart.")
    self.assertEqual(row[9], "Did you guard disposed state before updating?")


class TestUnifiedBatchSyncAgyCli(unittest.TestCase):
  """Tests unified --batch-size / --no-fetch agy CLI batch transformation (GH-193)."""

  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.db_path = os.path.join(self.temp_dir.name, "batch_scars.db")

  def tearDown(self):
    self.temp_dir.cleanup()

  def _make_agy_result(self, payload: dict):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"structured_output": payload})
    mock_proc.stderr = ""
    return mock_proc

  def test_fetch_plus_batch_size_runs_two_agy_passes_and_validates(self):
    scars_input = [
        {
            "id": "SCAR-ARCH-05",
            "title": "Python Architecture Guard",
            "category": "Architecture",
            "categorySlug": "architecture",
            "visibility": "public",
            "workflowPhases": ["Target 4"],
            "appliesWhen": "Organizing modules in Python",
            "graduationStatus": "graduated",
            "wound": "Mixed layers cause $O(n^2)$ regressions.",
            "trap": "Importing entrypoints inside library code.",
            "circuitBreaker": "If a library imports a script, stop.",
            "reflex": "Check ```bash\nmake test-static\n``` first.",
        }
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    seen_calls = []

    def fake_run(cmd, **kwargs):
      seen_calls.append((cmd, kwargs))
      prompt = cmd[-1]
      if "Simple English" in prompt or "raw_wound" in prompt:
        return self._make_agy_result(
            {
                "wound": "Mixed layers cause $O(n^2)$ slowdowns.",
                "trap": "Library code imports entrypoint scripts.",
                "circuit_breaker": "If a library imports scripts, stop.",
                "reflex": "Run ```bash\nmake test-static\n``` first.",
            }
        )
      return self._make_agy_result(
          {"socratic_probe": "Did you check layer imports before editing?"}
      )

    out = io.StringIO()
    err = io.StringIO()
    with (
        patch(
            "urllib.request.urlopen",
            return_value=MockHTTPResponse(payload, etag='"etag-batch-1"'),
        ),
        patch("subprocess.run", side_effect=fake_run),
    ):
      code = SyncSyntheticScarsScript().execute(
          args=[
              "--db-path",
              self.db_path,
              "--batch-size",
              "2",
              "--agy-bin",
              "agy",
              "--model",
              "test-model",
          ],
          stdout=out,
          stderr=err,
      )
    self.assertEqual(code, 0, err.getvalue())
    self.assertEqual(len(seen_calls), 2)
    for cmd, kwargs in seen_calls:
      self.assertFalse(kwargs.get("shell", False))
      self.assertIn("--print", cmd)
      self.assertIn("--output-format", cmd)
      self.assertIn("--json-schema", cmd)
      self.assertIn("--disable-slash-commands", cmd)
      self.assertIn("--model", cmd)

    pass1_prompt = seen_calls[0][0][-1]
    pass2_prompt = seen_calls[1][0][-1]
    self.assertIn("Simple English", pass1_prompt)
    self.assertIn("<untrusted_upstream_scar>", pass1_prompt)
    self.assertIn("</untrusted_upstream_scar>", pass1_prompt)
    self.assertIn("passive", pass1_prompt.lower())
    self.assertIn("kinetic", pass2_prompt.lower())
    self.assertIn("<untrusted_upstream_scar>", pass2_prompt)
    self.assertIn("</untrusted_upstream_scar>", pass2_prompt)

    self.assertEqual(
        SyncSyntheticScarsScript().execute(
            args=["--db-path", self.db_path, "--verify-transformed"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ),
        0,
    )
    results = query_scars(self.db_path, query="SCAR-ARCH-05", limit=5)
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-LANG-PYTHON-ARCH-0005")
    self.assertEqual(
        results[0]["socraticProbe"],
        "Did you check layer imports before editing?",
    )

  def test_304_continues_batch_and_no_fetch_fills_missing_socratic_probe_only(self):
    scars_input = [
        {
            "id": "SCAR-TEST-01",
            "title": "Partial Scar Missing Probe",
            "category": "Testing",
            "categorySlug": "testing",
            "visibility": "public",
            "workflowPhases": ["Target 3"],
            "appliesWhen": "Writing unit tests",
            "graduationStatus": "graduated",
            "wound": "Raw wound one.",
            "trap": "Raw trap one.",
            "circuitBreaker": "",
            "reflex": "Raw reflex one.",
        },
        {
            "id": "SCAR-TEST-02",
            "title": "Empty Scar Needing Both Passes",
            "category": "Testing",
            "categorySlug": "testing",
            "visibility": "public",
            "workflowPhases": ["Target 3"],
            "appliesWhen": "Writing integration tests",
            "graduationStatus": "graduated",
            "wound": "Raw wound two.",
            "trap": "Raw trap two.",
            "circuitBreaker": "",
            "reflex": "Raw reflex two.",
        },
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-304"'),
    ):
      self.assertEqual(
          SyncSyntheticScarsScript().execute(
              args=["--db-path", self.db_path], stdout=io.StringIO()
          ),
          0,
      )

    # Populate Simple English fields for SCAR-TEST-01 only, leaving socratic_probe NULL
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        """
        UPDATE scars
        SET wound = 'Existing wound preserved.',
            trap = 'Existing trap preserved.',
            circuit_breaker = '',
            reflex = 'Existing reflex preserved.'
        WHERE ss_id = 'SCAR-TEST-01'
        """
    )
    conn.commit()
    conn.close()

    # HTTP 304 + --batch-size 1 runs ONLY Pass 2 for SCAR-TEST-01 and preserves wound
    http_304 = urllib.error.HTTPError(
        url="https://syntheticscars.org/registry.json",
        code=304,
        msg="Not Modified",
        hdrs={},
        fp=None,
    )
    calls_304 = []

    def fake_304(cmd, **kwargs):
      calls_304.append(cmd[-1])
      return self._make_agy_result({"socratic_probe": "Did tests run first?"})

    with (
        patch("urllib.request.urlopen", side_effect=http_304),
        patch("subprocess.run", side_effect=fake_304),
    ):
      code_304 = SyncSyntheticScarsScript().execute(
          args=["--db-path", self.db_path, "--batch-size", "1"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
    self.assertEqual(code_304, 0)
    self.assertEqual(len(calls_304), 1)

    conn = sqlite3.connect(self.db_path)
    row1 = conn.execute(
        "SELECT wound, socratic_probe FROM scars WHERE ss_id = 'SCAR-TEST-01'"
    ).fetchone()
    conn.close()
    self.assertEqual(row1, ("Existing wound preserved.", "Did tests run first?"))

    # --no-fetch --batch-size 1 skips HTTP completely and transforms SCAR-TEST-02
    def fake_nofetch(cmd, **kwargs):
      prompt = cmd[-1]
      if "raw_wound" in prompt:
        return self._make_agy_result(
            {
                "wound": "Simple wound two.",
                "trap": "Simple trap two.",
                "circuit_breaker": "",
                "reflex": "Simple reflex two.",
            }
        )
      return self._make_agy_result({"socratic_probe": "Did you isolate DB?"})

    with (
        patch("urllib.request.urlopen", side_effect=AssertionError("Must not fetch")),
        patch("subprocess.run", side_effect=fake_nofetch),
    ):
      code_nf = SyncSyntheticScarsScript().execute(
          args=["--db-path", self.db_path, "--no-fetch", "--batch-size", "1"],
          stdout=io.StringIO(),
          stderr=io.StringIO(),
      )
    self.assertEqual(code_nf, 0)
    self.assertEqual(len(query_scars(self.db_path, query="Simple wound two")), 1)

  def test_batch_retry_feedback_and_per_scar_commit_safety(self):
    scars_input = [
        {
            "id": "SCAR-TEST-01",
            "title": "Retry Success Scar",
            "category": "Testing",
            "categorySlug": "testing",
            "visibility": "public",
            "workflowPhases": ["Target 3"],
            "appliesWhen": "Testing retry feedback",
            "graduationStatus": "graduated",
            "wound": "Raw wound with $k = 1$.",
            "trap": "Raw trap.",
            "circuitBreaker": "",
            "reflex": "Raw reflex.",
        },
        {
            "id": "SCAR-TEST-02",
            "title": "Always Failing Scar",
            "category": "Testing",
            "categorySlug": "testing",
            "visibility": "public",
            "workflowPhases": ["Target 3"],
            "appliesWhen": "Testing failure rollback",
            "graduationStatus": "graduated",
            "wound": "Raw wound two.",
            "trap": "Raw trap two.",
            "circuitBreaker": "",
            "reflex": "Raw reflex two.",
        },
    ]
    payload = json.dumps(scars_input).encode("utf-8")
    with patch(
        "urllib.request.urlopen",
        return_value=MockHTTPResponse(payload, etag='"etag-retry"'),
    ):
      self.assertEqual(
          SyncSyntheticScarsScript().execute(
              args=["--db-path", self.db_path], stdout=io.StringIO()
          ),
          0,
      )

    prompts_seen = []
    attempt_counts = {"p1_1": 0, "p2_1": 0}

    def fake_retry(cmd, **kwargs):
      prompt = cmd[-1]
      prompts_seen.append(prompt)
      if "SCAR-TEST-0001" in prompt and "raw_wound" in prompt:
        attempt_counts["p1_1"] += 1
        if attempt_counts["p1_1"] == 1:
          return self._make_agy_result(
              {
                  "wound": "Bad semicolon; missing math.",
                  "trap": "Simple trap.",
                  "circuit_breaker": "",
                  "reflex": "Simple reflex.",
              }
          )
        return self._make_agy_result(
            {
                "wound": "Simple wound with $k = 1$.",
                "trap": "Simple trap.",
                "circuit_breaker": "",
                "reflex": "Simple reflex.",
            }
        )
      if "SCAR-TEST-0001" in prompt:
        attempt_counts["p2_1"] += 1
        if attempt_counts["p2_1"] == 1:
          return self._make_agy_result({"socratic_probe": "Missing question mark."})
        return self._make_agy_result({"socratic_probe": "Did you preserve $k = 1$?"})
      # Second scar always fails Pass 1 validation
      return self._make_agy_result(
          {
              "wound": "Bad semicolon; fails.",
              "trap": "T.",
              "circuit_breaker": "",
              "reflex": "R.",
          }
      )

    out = io.StringIO()
    err = io.StringIO()
    with patch("subprocess.run", side_effect=fake_retry):
      code = SyncSyntheticScarsScript().execute(
          args=[
              "--db-path",
              self.db_path,
              "--no-fetch",
              "--batch-size",
              "2",
              "--max-retries",
              "1",
          ],
          stdout=out,
          stderr=err,
      )
    self.assertEqual(code, 1)
    self.assertTrue(any("semicolon" in p or "inline_math" in p for p in prompts_seen))
    self.assertTrue(any("?" in p for p in prompts_seen))

    # First scar must remain committed despite second scar failing
    committed = query_scars(self.db_path, query="SCAR-TEST-01", limit=5)
    self.assertEqual(len(committed), 1)
    self.assertEqual(committed[0]["id"], "SCAR-TEST-0001")
    uncommitted = query_scars(self.db_path, query="SCAR-TEST-02", limit=5)
    self.assertEqual(uncommitted, [])


class TestExcludeCategoriesQuery(unittest.TestCase):
  """Tests WORKFLOW_CATEGORY_TAGS, exclude_categories filtering, and mandatory socratic_probe (GH-252)."""

  def _build_scars_db_with_rows(
      self,
      db_path: str,
      rows: list[tuple[str, str | None, str | None, str | None]],
  ) -> None:
    """Create a hermetic SQLite scars database populated with custom test rows."""
    conn = sqlite3.connect(db_path)
    try:
      conn.execute(
          """
          CREATE TABLE scars (
              id TEXT PRIMARY KEY,
              ss_id TEXT,
              title TEXT,
              category TEXT,
              category_slug TEXT,
              visibility TEXT,
              workflow_phases TEXT,
              ss_targets TEXT,
              mf_targets TEXT,
              applies_when TEXT,
              graduation_status TEXT,
              wound TEXT,
              trap TEXT,
              circuit_breaker TEXT,
              reflex TEXT,
              socratic_probe TEXT,
              source TEXT DEFAULT 'upstream',
              original_id TEXT,
              raw_hash TEXT,
              keywords TEXT,
              languages TEXT,
              frameworks TEXT,
              diagnostic_codes TEXT
          )
          """
      )
      conn.execute(
          """
          CREATE VIRTUAL TABLE scars_fts USING fts5(
              id,
              ss_id,
              original_id,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              socratic_probe,
              keywords,
              languages,
              frameworks,
              diagnostic_codes,
              workflow_phases,
              source UNINDEXED
          )
          """
      )
      for scar_id, cat, slug, probe in rows:
        title = f"{scar_id} sharedmarker title"
        phases = json.dumps(["Target 15: archaeology", "Target 20: plan", "Target 30: tdd"])
        mf_targets = json.dumps([15, 20, 30])
        languages = json.dumps(["universal"])
        cat_label = (cat or slug or "").lower()
        keywords_list = ["sharedmarker", cat_label] if cat_label else ["sharedmarker"]
        conn.execute(
            """
            INSERT INTO scars (
                id, ss_id, title, category, category_slug, visibility,
                workflow_phases, ss_targets, mf_targets, applies_when,
                graduation_status, wound, trap, circuit_breaker, reflex,
                socratic_probe, source, original_id, raw_hash, keywords,
                languages, frameworks, diagnostic_codes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scar_id,
                None,
                title,
                cat,
                slug,
                "public",
                phases,
                phases,
                mf_targets,
                "Planning and coding",
                "graduated",
                "Observed defect wound.",
                "Observed shortcut trap.",
                "",
                "Apply the permanent reflex.",
                probe,
                "upstream",
                None,
                f"hash-{scar_id}",
                json.dumps(keywords_list),
                languages,
                json.dumps([]),
                json.dumps([]),
            ),
        )
        conn.execute(
            """
            INSERT INTO scars_fts (
                id, ss_id, original_id, title, category, applies_when,
                wound, trap, reflex, socratic_probe, keywords, languages,
                frameworks, diagnostic_codes, workflow_phases, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scar_id,
                "",
                "",
                title,
                cat or "",
                "Planning and coding",
                "Observed defect wound.",
                "Observed shortcut trap.",
                "Apply the permanent reflex.",
                probe or "",
                f"sharedmarker {cat_label}".strip(),
                "universal",
                "",
                "",
                "Target 15: archaeology Target 20: plan Target 30: tdd",
                "upstream",
            ),
        )
      conn.commit()
    finally:
      conn.close()

  def _build_universal_scars_db(self, db_path: str) -> None:
    """Create a hermetic SQLite database with universal dev and workflow scars."""
    rows = [
        ("SCAR-ARCH-0001", "ARCH", "arch", "Did you check module boundaries?"),
        ("SCAR-API-0001", "API", None, "Is the parameter composable?"),
        ("SCAR-TEST-0001", "TEST", "test", "Is the test fixture isolated?"),
        ("SCAR-CRAFT-0001", "CRAFT", "craft", "Is the helper direct and simple?"),
        ("SCAR-ARCH-0099", "ARCH", "arch", ""),
        ("SCAR-API-0099", "API", "api", None),
        ("SCAR-PROC-0001", "PROC", "proc", "Did you run the workflow gate?"),
        ("SCAR-REVIEW-0001", "REVIEW", "review", "Did the critic audit the diff?"),
        ("SCAR-HCI-0001", "HCI", "hci", "Did you present facts clearly?"),
        ("SCAR-GIT-0001", "GIT", "git", "Did you lock the tree hash?"),
        ("SCAR-GOV-0001", "GOV", "gov", "Is the guard invariant intact?"),
        ("SCAR-META-0001", "META", "meta", "Did you inspect reflection logs?"),
        ("SCAR-LEGACY-PROC-01", "Process", "process", "Did legacy process pass?"),
        ("SCAR-LEGACY-TOOL-01", "Tooling", "tooling", "Did legacy tooling pass?"),
    ]
    self._build_scars_db_with_rows(db_path, rows)

  def test_workflow_category_tags_ontology_membership(self):
    from lib.scar_ontology import DOMAIN_CATEGORY_ONTOLOGY, WORKFLOW_CATEGORY_TAGS

    expected_workflow_tags = {
        "PROC",
        "REVIEW",
        "HCI",
        "GOV",
        "META",
        "METRIC",
        "DIFF",
        "COLLAB",
        "PEDAGOGY",
        "VERIF",
        "SKILL",
        "HOOK",
        "TOOL",
        "CI",
        "GIT",
    }
    self.assertEqual(len(WORKFLOW_CATEGORY_TAGS), 15)
    self.assertEqual(set(WORKFLOW_CATEGORY_TAGS), expected_workflow_tags)
    for tag in WORKFLOW_CATEGORY_TAGS:
      self.assertIn(tag, DOMAIN_CATEGORY_ONTOLOGY)

    for dev_tag in (
        "ARCH",
        "API",
        "CRAFT",
        "TEST",
        "ASYNC",
        "DATA",
        "SEC",
        "DB",
        "STORAGE",
        "STATE",
        "PERF",
        "UI",
        "DOC",
        "BUILD",
        "DEP",
        "DIR",
        "ERR",
        "FAIL",
        "INV",
        "WIRE",
    ):
      self.assertNotIn(dev_tag, WORKFLOW_CATEGORY_TAGS)

  def test_resolve_excluded_category_tokens_expansion_and_deduplication(self):
    from lib.scar_ontology import (
        WORKFLOW_CATEGORY_TAGS,
        resolve_excluded_category_tokens,
    )

    self.assertEqual(resolve_excluded_category_tokens(None), ())
    self.assertEqual(resolve_excluded_category_tokens([]), ())
    self.assertEqual(resolve_excluded_category_tokens(["", "   ", None]), ())

    workflow_tokens = resolve_excluded_category_tokens(["WORKFLOW"])
    self.assertEqual(len(workflow_tokens), len(set(workflow_tokens)))
    for tag in WORKFLOW_CATEGORY_TAGS:
      self.assertIn(tag, workflow_tokens)
    self.assertIn("PROCESS", workflow_tokens)
    self.assertIn("TOOLING", workflow_tokens)
    for dev_tag in ("ARCH", "API", "TEST", "CRAFT"):
      self.assertNotIn(dev_tag, workflow_tokens)

    deduped = resolve_excluded_category_tokens(
        ["WORKFLOW", "proc", "Process", "tooling", "WORKFLOW", "  "]
    )
    self.assertEqual(deduped, workflow_tokens)

    explicit = resolve_excluded_category_tokens(["Process", "GIT", "proc"])
    self.assertEqual(explicit, ("PROC", "PROCESS", "GIT"))

  def test_query_scars_universal_excludes_workflow_and_requires_socratic_probe(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "exclude_workflow.db")
      self._build_universal_scars_db(db_path)

      expected_dev_ids = {
          "SCAR-ARCH-0001",
          "SCAR-API-0001",
          "SCAR-TEST-0001",
          "SCAR-CRAFT-0001",
      }

      # Filter-only path
      filter_rows = query_scars(
          db_path=db_path,
          language="universal",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual({r["id"] for r in filter_rows}, expected_dev_ids)

      # FTS5 keyword search path
      fts_rows = query_scars(
          db_path=db_path,
          query="sharedmarker",
          language="universal",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual({r["id"] for r in fts_rows}, expected_dev_ids)

  def test_query_scars_explicit_category_exclusions(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "exclude_explicit.db")
      self._build_universal_scars_db(db_path)

      rows = query_scars(
          db_path=db_path,
          language="universal",
          exclude_categories=["PROC", "GIT"],
          limit=20,
      )
      returned_ids = {r["id"] for r in rows}
      self.assertNotIn("SCAR-PROC-0001", returned_ids)
      self.assertNotIn("SCAR-LEGACY-PROC-01", returned_ids)
      self.assertNotIn("SCAR-GIT-0001", returned_ids)
      self.assertNotIn("SCAR-ARCH-0099", returned_ids)
      self.assertNotIn("SCAR-API-0099", returned_ids)
      self.assertEqual(
          returned_ids,
          {
              "SCAR-ARCH-0001",
              "SCAR-API-0001",
              "SCAR-TEST-0001",
              "SCAR-CRAFT-0001",
              "SCAR-REVIEW-0001",
              "SCAR-HCI-0001",
              "SCAR-GOV-0001",
              "SCAR-META-0001",
              "SCAR-LEGACY-TOOL-01",
          },
      )

  def test_resolve_excluded_category_tokens_string_case_hyphen_and_unknown(self):
    """Covers bare strings, lowercase workflow, hyphen normalization, and unknown tokens (GH-252)."""
    from lib.scar_ontology import resolve_excluded_category_tokens

    workflow_tokens = resolve_excluded_category_tokens(["WORKFLOW"])
    self.assertEqual(
        resolve_excluded_category_tokens("WORKFLOW"),
        workflow_tokens,
    )
    self.assertEqual(
        resolve_excluded_category_tokens("proc"),
        ("PROC", "PROCESS"),
    )
    self.assertEqual(
        resolve_excluded_category_tokens(["workflow"]),
        workflow_tokens,
    )
    self.assertEqual(
        resolve_excluded_category_tokens("workflow"),
        workflow_tokens,
    )
    self.assertEqual(
        resolve_excluded_category_tokens(["custom-tag"]),
        ("CUSTOM_TAG",),
    )
    self.assertEqual(
        resolve_excluded_category_tokens(["unknown_domain", "mystery-cat"]),
        ("UNKNOWN_DOMAIN", "MYSTERY_CAT"),
    )

  def test_query_scars_standalone_exclude_categories_without_positive_filter(self):
    """Standalone exclude_categories works without positive filters; empty list returns empty (GH-252)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "exclude_standalone.db")
      self._build_universal_scars_db(db_path)

      rows = query_scars(
          db_path=db_path,
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual(
          {r["id"] for r in rows},
          {
              "SCAR-ARCH-0001",
              "SCAR-API-0001",
              "SCAR-TEST-0001",
              "SCAR-CRAFT-0001",
          },
      )
      self.assertEqual(
          query_scars(db_path=db_path, exclude_categories=[], limit=20),
          [],
      )
      self.assertEqual(
          query_scars(db_path=db_path, exclude_categories=["   "], limit=20),
          [],
      )

  def test_query_scars_stacked_databases_with_list_and_single_pass_generator(self):
    """Stacked multi-DB query_scars preserves exclude_categories across all DBs for lists and generators (GH-252)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db1_path = os.path.join(tmp_dir, "stack_workspace.db")
      db2_path = os.path.join(tmp_dir, "stack_bundled.db")
      self._build_scars_db_with_rows(
          db1_path,
          [
              ("SCAR-ARCH-0001", "ARCH", "arch", "Is module cohesive?"),
              ("SCAR-PROC-0001", "PROC", "proc", "Did workflow gate pass?"),
          ],
      )
      self._build_scars_db_with_rows(
          db2_path,
          [
              ("SCAR-API-0001", "API", "api", "Is parameter composable?"),
              ("SCAR-GIT-0001", "GIT", "git", "Did tree lock match?"),
              ("SCAR-REVIEW-0001", "REVIEW", "review", "Did critic audit diff?"),
          ],
      )

      expected_ids = {"SCAR-ARCH-0001", "SCAR-API-0001"}

      list_rows = query_scars(
          db_path=[db1_path, db2_path],
          language="universal",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual({r["id"] for r in list_rows}, expected_ids)

      gen_rows = query_scars(
          db_path=[db1_path, db2_path],
          language="universal",
          exclude_categories=(tag for tag in ["WORKFLOW"]),
          limit=20,
      )
      self.assertEqual({r["id"] for r in gen_rows}, expected_ids)

      standalone_gen_rows = query_scars(
          db_path=[db1_path, db2_path],
          exclude_categories=(tag for tag in ["WORKFLOW"]),
          limit=20,
      )
      self.assertEqual({r["id"] for r in standalone_gen_rows}, expected_ids)

  def test_query_scars_combined_category_and_exclude_categories(self):
    """Combined category and exclude_categories supports compatible and contradictory filters (GH-252)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "exclude_combined.db")
      self._build_universal_scars_db(db_path)

      compatible = query_scars(
          db_path=db_path,
          category="ARCH",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual({r["id"] for r in compatible}, {"SCAR-ARCH-0001"})

      contradictory_group = query_scars(
          db_path=db_path,
          category="PROC",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual(contradictory_group, [])

      contradictory_alias = query_scars(
          db_path=db_path,
          category="Process",
          exclude_categories=["PROC"],
          limit=20,
      )
      self.assertEqual(contradictory_alias, [])

  def test_query_scars_null_category_coalesce_protection(self):
    """COALESCE protects NULL s.category rows with non-NULL s.category_slug from SQLite NULL NOT IN traps (GH-252)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "exclude_null_cat.db")
      self._build_scars_db_with_rows(
          db_path,
          [
              ("SCAR-NULLCAT-ARCH-01", None, "arch", "Is module cohesive?"),
              ("SCAR-NULLCAT-PROC-01", None, "proc", "Did workflow gate pass?"),
              ("SCAR-NULLBOTH-01", None, None, "Does null metadata survive?"),
          ],
      )

      expected_ids = {"SCAR-NULLCAT-ARCH-01", "SCAR-NULLBOTH-01"}

      filter_rows = query_scars(
          db_path=db_path,
          language="universal",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual({r["id"] for r in filter_rows}, expected_ids)

      fts_rows = query_scars(
          db_path=db_path,
          query="sharedmarker",
          language="universal",
          exclude_categories=["WORKFLOW"],
          limit=20,
      )
      self.assertEqual({r["id"] for r in fts_rows}, expected_ids)

  def test_query_scars_sql_limit_starvation_immunity(self):
    """SQL-level NOT IN executes before LIMIT so leading workflow IDs never starve dev scars (GH-252)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_path = os.path.join(tmp_dir, "exclude_limit_starvation.db")
      self._build_scars_db_with_rows(
          db_path,
          [
              ("SCAR-AAA-0001", "PROC", "proc", "Did gate pass?"),
              ("SCAR-AAA-0002", "GIT", "git", "Did tree lock pass?"),
              ("SCAR-ZZZ-0001", "ARCH", "arch", "Is module boundary clean?"),
          ],
      )

      rows = query_scars(
          db_path=db_path,
          language="universal",
          exclude_categories=["WORKFLOW"],
          limit=1,
      )
      self.assertEqual([r["id"] for r in rows], ["SCAR-ZZZ-0001"])

      standalone_rows = query_scars(
          db_path=db_path,
          exclude_categories=["WORKFLOW"],
          limit=1,
      )
      self.assertEqual([r["id"] for r in standalone_rows], ["SCAR-ZZZ-0001"])


if __name__ == "__main__":
  unittest.main()
