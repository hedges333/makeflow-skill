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
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

# Ensure scripts directory and repo root are on sys.path
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from base_script import BaseScript
from sync_synthetic_scars import (
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

  def tearDown(self):
    self.tmp_dir.cleanup()

  def test_fts5_keyword_search(self):
    results = query_scars(self.db_path, query="BaseScript")
    self.assertGreaterEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-ARCH-05")
    self.assertIn("Template Method", results[0]["title"])

  def test_category_filter(self):
    results = query_scars(self.db_path, query="isolation", category="Testing")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-TEST-08")

    # Mismatched category should return empty
    empty_results = query_scars(self.db_path, query="isolation", category="Architecture")
    self.assertEqual(len(empty_results), 0)

  def test_language_filter(self):
    results = query_scars(self.db_path, query="strict", language="python")
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["id"], "SCAR-LANG-01")

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
            reflex TEXT
        )
    """)
    for s in SAMPLE_SCARS:
      conn.execute(
          """
          INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    cur.execute("PRAGMA table_info(scars_fts)")
    fts_cols = [r[1] for r in cur.fetchall()]
    self.assertIn("source", fts_cols)
    self.assertIn("original_id", fts_cols)
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
    cur.execute("SELECT id, raw_hash FROM scars")
    rows = cur.fetchall()
    for scar_id, raw_hash in rows:
      self.assertIsNotNone(raw_hash)
      self.assertEqual(len(raw_hash), 32)

    # Inject canary into SCAR-ARCH-05 while leaving raw_hash unchanged
    cur.execute("UPDATE scars SET wound = 'Canary wound' WHERE id = 'SCAR-ARCH-05'")
    conn.commit()
    conn.close()

    # Second sync with --force: SCAR-ARCH-05 payload matches raw_hash, so rewrite is skipped
    script2 = SyncSyntheticScarsScript()
    with patch("urllib.request.urlopen", return_value=MockHTTPResponse(payload, etag='"etag-s2"')):
      code2 = script2.execute(args=["--db-path", self.db_path, "--force"], stdout=io.StringIO())
    self.assertEqual(code2, 0)

    conn = sqlite3.connect(self.db_path)
    cur = conn.cursor()
    cur.execute("SELECT wound FROM scars WHERE id = 'SCAR-ARCH-05'")
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
            wound, trap, circuit_breaker, reflex, source, original_id, raw_hash
        ) VALUES (
            'SCAR-LANG-PERL-001', 'Makeflow Perl Guidelines', 'Languages', 'languages', 'public',
            '["T30"]', 'Writing Perl scripts', 'graduated',
            'Makeflow Perl wound', 'Makeflow Perl trap', 'Makeflow breaker', 'Makeflow reflex',
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
    cur = conn.cursor()

    # Verify upstream scar is inserted with source='upstream' and original id
    cur.execute("SELECT id, source, original_id, title FROM scars WHERE id = 'SCAR-LANG-PERL-001'")
    up_row = cur.fetchone()
    self.assertIsNotNone(up_row)
    self.assertEqual(up_row[1], "upstream")
    self.assertIsNone(up_row[2])
    self.assertEqual(up_row[3], "Upstream Perl Scars")

    # Verify makeflow scar is renamed with source='makeflow' and original_id='SCAR-LANG-PERL-001'
    cur.execute("SELECT id, source, original_id, title FROM scars WHERE id = 'SCAR-MF-LANG-PERL-001'")
    mf_row = cur.fetchone()
    self.assertIsNotNone(mf_row)
    self.assertEqual(mf_row[1], "makeflow")
    self.assertEqual(mf_row[2], "SCAR-LANG-PERL-001")
    self.assertEqual(mf_row[3], "Makeflow Perl Guidelines")

    # Verify both are searchable in FTS5
    up_search = query_scars(self.db_path, query="Upstream")
    self.assertTrue(any(r["id"] == "SCAR-LANG-PERL-001" for r in up_search))

    mf_search = query_scars(self.db_path, query="Guidelines")
    self.assertTrue(any(r["id"] == "SCAR-MF-LANG-PERL-001" for r in mf_search))
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

    # Filter by source='makeflow'
    mf_only = query_scars(self.db_path, query="Perl", source="makeflow")
    self.assertTrue(len(mf_only) >= 1)
    self.assertTrue(all(r["source"] == "makeflow" for r in mf_only))
    self.assertEqual(mf_only[0]["id"], "SCAR-MF-LANG-PERL-001")
    self.assertEqual(mf_only[0]["originalId"], "SCAR-LANG-PERL-001")

    # Filter by source='upstream'
    up_only = query_scars(self.db_path, query="Perl", source="upstream")
    self.assertTrue(len(up_only) >= 1)
    self.assertTrue(all(r["source"] == "upstream" for r in up_only))
    self.assertEqual(up_only[0]["id"], "SCAR-LANG-PERL-001")

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
    # Insert both SCAR-MF-LANG-PERL-001 and a distinct SCAR-LANG-PERL-001 with source='makeflow'
    conn.execute("""
        INSERT INTO scars (
            id, title, category, category_slug, visibility,
            workflow_phases, applies_when, graduation_status,
            wound, trap, circuit_breaker, reflex, source, original_id, raw_hash
        ) VALUES (
            'SCAR-MF-LANG-PERL-001', 'First Makeflow Perl Rule', 'Polyglot', 'polyglot', 'public',
            '["T30"]', 'Perl', 'graduated', 'First wound', 'First trap', 'NOOO', 'First reflex',
            'makeflow', 'SCAR-LANG-PERL-001', 'hash_mf_1'
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
    cur.execute("SELECT id, title, source FROM scars WHERE id LIKE '%PERL-001%' ORDER BY id")
    rows = cur.fetchall()
    ids = {r[0]: (r[1], r[2]) for r in rows}
    self.assertIn("SCAR-LANG-PERL-001", ids)
    self.assertEqual(ids["SCAR-LANG-PERL-001"], ("Upstream Perl Rule", "upstream"))
    self.assertIn("SCAR-MF-LANG-PERL-001", ids)
    self.assertEqual(ids["SCAR-MF-LANG-PERL-001"], ("First Makeflow Perl Rule", "makeflow"))
    self.assertIn("SCAR-MF-LANG-PERL-001-2", ids)
    self.assertEqual(ids["SCAR-MF-LANG-PERL-001-2"], ("Second Makeflow Perl Rule", "makeflow"))
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
            graduation_status TEXT, wound TEXT, trap TEXT, circuit_breaker TEXT, reflex TEXT
        )
    """)
    s = SAMPLE_SCARS[0]
    conn.execute(
        "INSERT INTO scars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            s["id"], s["title"], s["category"], s["categorySlug"], s["visibility"],
            json.dumps(s["workflowPhases"]), s["appliesWhen"], s["graduationStatus"],
            s["wound"], s["trap"], s["circuitBreaker"], s["reflex"],
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


if __name__ == "__main__":
  unittest.main()
