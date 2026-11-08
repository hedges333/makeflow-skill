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
        resolved = resolve_scars_db_path(workspace_dir=tmp_ws)
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
    for agent_name, expected_path in expected_agents.items():
      resolved = resolve_scars_db_path(agent=agent_name)
      self.assertEqual(resolved, expected_path)

  def test_default_xdg_cache_fallback(self):
    home = os.path.expanduser("~")
    expected_default = os.path.join(home, ".cache", "makeflow", "scars_registry.db")
    with patch.dict(os.environ, {}, clear=True):
      resolved = resolve_scars_db_path()
      self.assertEqual(resolved, expected_default)


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


if __name__ == "__main__":
  unittest.main()
