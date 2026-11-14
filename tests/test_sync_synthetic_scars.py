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
        "SELECT raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,"
        " wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE id = 'SCAR-MATH-01'"
    )
    row_math = cur.fetchone()
    self.assertEqual(row_math[0], "Lost inline $x_i + y_i = z_i$ formula.")
    self.assertEqual(
        row_math[1], "Stripping LaTeX $$\\sum_{i=1}^n x_i$$ during rewrite."
    )
    self.assertEqual(row_math[2], "Verify spans before commit.")
    self.assertEqual(
        row_math[3], "Preserve `$VAR` and ```python\nprint(1)\n``` blocks."
    )
    self.assertTrue(row_math[4])
    self.assertIsNone(row_math[5])
    self.assertIsNone(row_math[6])
    self.assertIsNone(row_math[7])
    self.assertIsNone(row_math[8])
    self.assertIsNone(row_math[9])

    cur.execute(
        "SELECT raw_circuit_breaker, circuit_breaker, wound, trap, reflex,"
        " socratic_probe FROM scars WHERE id = 'SCAR-NOCB-01'"
    )
    row_nocb = cur.fetchone()
    self.assertEqual(row_nocb[0], "")
    self.assertEqual(row_nocb[1], "")
    self.assertIsNone(row_nocb[2])
    self.assertIsNone(row_nocb[3])
    self.assertIsNone(row_nocb[4])
    self.assertIsNone(row_nocb[5])

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

    # Populate Simple English fields for SCAR-MATH-01
    cur.execute(
        "UPDATE scars SET wound = ?, trap = ?, circuit_breaker = ?, reflex = ?,"
        " socratic_probe = ? WHERE id = 'SCAR-MATH-01'",
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
        " FROM scars WHERE id = 'SCAR-MATH-01'"
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
        " FROM scars WHERE id = 'SCAR-MATH-01'"
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

    # Populate all four Simple English fields for both rows -> 0 pending -> notice omitted
    cur.execute(
        "UPDATE scars SET wound = 'Simple wound.', trap = 'Simple trap.',"
        " circuit_breaker = 'Simple breaker.', reflex = 'Simple reflex.'"
    )
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

    # Null or omitted socratic_probe -> allowed (populated separately by socratic-prompt-writer)
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
        "UPDATE scars SET wound = ? WHERE id = 'SCAR-PIPE-01'",
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
    self.assertEqual(exported[0]["id"], "SCAR-PIPE-01")
    self.assertNotIn("wound", exported[0]["null_fields"])
    for nf in ("trap", "circuit_breaker", "reflex", "socratic_probe"):
      self.assertIn(nf, exported[0]["null_fields"])

    # 2. --apply-transformed merges only into NULL fields and keeps existing non-null wound
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
        "UPDATE scars SET wound = 'Corrupted wound missing formula.' WHERE id = 'SCAR-PIPE-01'"
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
        "UPDATE scars SET socratic_probe = ? WHERE id = 'SCAR-MATH-01'",
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
        " FROM scars WHERE id = 'SCAR-MATH-01'"
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
        " FROM scars WHERE id = 'SCAR-MATH-01'"
    )
    row_math = cur.fetchone()
    self.assertEqual(row_math[0], "Revised wound with $x_i + y_i = z_i$ intact.")
    self.assertEqual(row_math[4], "Existing Socratic question?")

    cur.execute(
        "SELECT wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE id = 'SCAR-NOCB-01'"
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
    self.assertEqual(results[0]["id"], "SCAR-MATH-01")
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
    cur.execute("SELECT wound FROM scars WHERE id = 'SCAR-MATH-01'")
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
        WHERE id = 'SCAR-RIV-01'
        """
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        "SELECT raw_hash, wound, trap, circuit_breaker, reflex, socratic_probe"
        " FROM scars WHERE id = 'SCAR-RIV-01'"
    )
    before_row = cur.fetchone()
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
        " workflow_phases FROM scars WHERE id = 'SCAR-RIV-01'"
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
        "UPDATE scars SET wound = 'Simple widget wound.' WHERE id = 'SCAR-FLUTTER-10'"
    )
    conn.commit()
    conn.close()
    SyncSyntheticScarsScript().execute(
        args=["--db-path", self.db_path, "--enrich"], stdout=io.StringIO()
    )

    # 1. Search by enriched keyword ('reentrancy') that was simplified out of in-use wound
    reent_res = query_scars(self.db_path, query="reentrancy")
    self.assertEqual(len(reent_res), 1)
    self.assertEqual(reent_res[0]["id"], "SCAR-FLUTTER-10")
    for expected_field in (
        "keywords",
        "languages",
        "frameworks",
        "diagnosticCodes",
        "diagnostic_codes",
        "workflowPhases",
        "workflow_phases",
    ):
      self.assertIn(expected_field, reent_res[0])

    # 2. Filter by framework
    fw_res = query_scars(self.db_path, query="", framework="riverpod")
    self.assertEqual(len(fw_res), 1)
    self.assertEqual(fw_res[0]["id"], "SCAR-FLUTTER-10")

    fw_py = query_scars(self.db_path, query="Worker", framework="fastapi")
    self.assertEqual(len(fw_py), 1)
    self.assertEqual(fw_py[0]["id"], "SCAR-PY-20")

    # 3. Filter by diagnostic_code
    diag_res = query_scars(self.db_path, query="", diagnostic_code="SIGTERM")
    self.assertEqual(len(diag_res), 1)
    self.assertEqual(diag_res[0]["id"], "SCAR-PY-20")

    diag_dart = query_scars(
        self.db_path, query="Completer", diagnostic_code="UNDEFINED_GETTER"
    )
    self.assertEqual(len(diag_dart), 1)
    self.assertEqual(diag_dart[0]["id"], "SCAR-FLUTTER-10")

    # 4. Filter by workflow_phase
    wp_plan = query_scars(
        self.db_path, query="", workflow_phase="Target 20: plan"
    )
    self.assertEqual(len(wp_plan), 1)
    self.assertEqual(wp_plan[0]["id"], "SCAR-FLUTTER-10")

    wp_rev = query_scars(
        self.db_path, query="", workflow_phase="Target 40: review"
    )
    self.assertEqual(len(wp_rev), 1)
    self.assertEqual(wp_rev[0]["id"], "SCAR-PY-20")

    wp_short = query_scars(self.db_path, query="", workflow_phase="T40")
    self.assertEqual(len(wp_short), 1)
    self.assertEqual(wp_short[0]["id"], "SCAR-PY-20")

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

    # 5. CLI flags --framework, --diagnostic-code, --workflow-phase
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
        ],
        stdout=out_cli,
    )
    self.assertEqual(code_cli, 0)
    cli_data = json.loads(out_cli.getvalue())
    self.assertEqual(len(cli_data), 1)
    self.assertEqual(cli_data[0]["id"], "SCAR-PY-20")

  def test_bundled_scars_registry_db_is_enriched_and_indexed(self):
    bundled_db = os.path.join(
        _REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(bundled_db))

    conn = sqlite3.connect(bundled_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM scars")
    total_scars = cur.fetchone()[0]
    self.assertEqual(total_scars, 595)

    cur.execute("SELECT COUNT(*) FROM scars_fts")
    fts_scars = cur.fetchone()[0]
    self.assertEqual(fts_scars, total_scars)

    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    fts_sql = cur.fetchone()[0]
    self.assertNotIn("id unindexed", fts_sql.lower())

    cur.execute(
        "SELECT id, keywords, languages, frameworks, diagnostic_codes,"
        " workflow_phases FROM scars"
    )
    for row in cur.fetchall():
      scar_id = row[0]
      keywords = json.loads(row[1] or "null")
      languages = json.loads(row[2] or "null")
      frameworks = json.loads(row[3] or "null")
      diag_codes = json.loads(row[4] or "null")
      phases = json.loads(row[5] or "null")

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
    conn.close()

    id_matches = query_scars(bundled_db, query="SCAR-ARCH-05", limit=5)
    self.assertTrue(any(m["id"] == "SCAR-ARCH-05" for m in id_matches))


if __name__ == "__main__":
  unittest.main()
