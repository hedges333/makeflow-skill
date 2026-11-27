"""Behavioral tests for SQLite Rapid-Regret Miner library and thin CLI wrapper (GH-117, GH-304)."""

import ast
import importlib.util
import inspect
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.agent_paths import AgentPathRegistry
from lib.config import DEFAULT_BUNDLED_SCAR_DB_TOKEN, DEFAULT_SCAR_DATABASES
from lib.scar_miner import (
    TIER_ID_PREFIXES,
    UPSTREAM_REPO,
    MinedScarRecord,
    ScarConfigStore,
    ScarMinerScript,
    SqliteScarStore,
    UpstreamIssuePublisher,
    validate_mined_scar,
    validate_scar_id,
)
from lib.scars_db import next_tier_scar_id, query_scars


def _sample_record(
    title="Guard Empty Database URLs",
    category="DB",
    circuit_breaker="If URL is empty, halt before opening a connection.",
    wound="An empty database URL opened an unintended local file.",
    trap="Callers assumed configuration loading always returned a valid connection string.",
    reflex="Validate the connection URL before opening a database handle.",
    socratic_probe="Did you validate the database URL before opening a connection?",
    applies_when="",
):
  """Construct a valid Simple English MinedScarRecord fixture."""
  return MinedScarRecord(
      title=title,
      category=category,
      wound=wound,
      trap=trap,
      circuit_breaker=circuit_breaker,
      reflex=reflex,
      socratic_probe=socratic_probe,
      applies_when=applies_when,
  )


def _isolated_env(home_dir, extra=None):
  """Build a minimal isolated environment pointing HOME to a temporary directory."""
  env = {
      "HOME": home_dir,
      "PATH": os.environ.get("PATH", ""),
  }
  if extra:
    env.update(extra)
  return env


class TestScarMinerLayerSeparation(unittest.TestCase):
  """Tests SCAR-ARCH-05, SCAR-ARCH-08, and SCAR-ARCH-0002 invariants."""

  def test_lib_scar_miner_has_no_shebang_and_no_dead_passthrough_shim(self):
    """Verifies lib/scar_miner.py has no shebang and SqliteScarStore omits pass-through allocate_scar_id."""
    lib_path = os.path.join(REPO_ROOT, "lib", "scar_miner.py")
    with open(lib_path, "r", encoding="utf-8") as fh:
      first_line = fh.readline()
    self.assertFalse(first_line.startswith("#!"))
    self.assertFalse(hasattr(SqliteScarStore, "allocate_scar_id"))

  def test_scripts_scar_miner_is_thin_executable_wrapper(self):
    """Verifies scripts/scar_miner.py is executable with zero local defs/classes."""
    script_path = os.path.join(REPO_ROOT, "scripts", "scar_miner.py")
    self.assertTrue(os.path.isfile(script_path))
    mode = os.stat(script_path).st_mode
    self.assertTrue(bool(mode & stat.S_IXUSR), "scripts/scar_miner.py must be executable")

    with open(script_path, "r", encoding="utf-8") as fh:
      source = fh.read()
    self.assertTrue(source.startswith("#!/usr/bin/env python3"))
    self.assertIn("sys.exit(ScarMinerScript().execute())", source)

    tree = ast.parse(source, filename=script_path)
    local_defs = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    self.assertEqual(local_defs, [])

    spec = importlib.util.spec_from_file_location("scar_miner_wrapper", script_path)
    self.assertIsNotNone(spec)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    self.assertIn("ScarMinerScript", module.__all__)
    self.assertIn("SqliteScarStore", module.__all__)
    self.assertIs(module.ScarMinerScript, ScarMinerScript)
    self.assertIs(module.SqliteScarStore, SqliteScarStore)


class TestValidationAndScarIdRules(unittest.TestCase):
  """Tests scar record validation, category guards, and explicit --id validation."""

  def test_validate_mined_scar_accepts_valid_and_rejects_invalid(self):
    """Verifies validation enforces four-part anatomy, Simple English, and Socratic probe rules."""
    self.assertEqual(validate_mined_scar(_sample_record()), [])
    self.assertEqual(validate_mined_scar(_sample_record(circuit_breaker="")), [])

    bad_probe = _sample_record(socratic_probe="This probe has no question mark.")
    self.assertTrue(any("?" in err for err in validate_mined_scar(bad_probe)))

    bad_contraction = _sample_record(
        wound="It's broken because tests don't isolate state."
    )
    self.assertTrue(
        any("contraction" in err.lower() for err in validate_mined_scar(bad_contraction))
    )

    missing_title = _sample_record(title="   ")
    self.assertTrue(
        any("title" in err.lower() for err in validate_mined_scar(missing_title))
    )

    for bad_cat in ("", "../evil", "foo/bar", "a\\b", "proc\x00"):
      with self.subTest(bad_cat=bad_cat):
        self.assertTrue(validate_mined_scar(_sample_record(category=bad_cat)))

    for bad_applies in ("Target 30\nExtra", "Target 30\rExtra"):
      with self.subTest(bad_applies=bad_applies):
        errors = validate_mined_scar(_sample_record(applies_when=bad_applies))
        self.assertTrue(any("applies_when" in err for err in errors))

  def test_validate_scar_id_accepts_canonical_and_rejects_malformed(self):
    """Verifies explicit scar ID validation accepts canonical forms and rejects malformed values."""
    self.assertEqual(validate_scar_id("SCAR-ARCH-0001"), "SCAR-ARCH-0001")
    self.assertEqual(validate_scar_id("scar-local-data-0001"), "SCAR-LOCAL-DATA-0001")
    self.assertEqual(
        validate_scar_id("SCAR-LANG-PYTHON-ARCH-0005"),
        "SCAR-LANG-PYTHON-ARCH-0005",
    )
    self.assertEqual(validate_scar_id("SCAR-TEST-45"), "SCAR-TEST-45")

    for bad_id in (
        "",
        "   ",
        "NOT-A-SCAR",
        "SCAR-ARCH",
        "SCAR-123",
        "SCAR-ARCH-01 extra",
        "../SCAR-ARCH-01",
        "SCAR-ARCH-0001\n",
    ):
      with self.subTest(bad_id=bad_id):
        with self.assertRaises(ValueError):
          validate_scar_id(bad_id)


class TestSqliteScarStoreAndConfigStore(unittest.TestCase):
  """Tests SQLite tier saves, explicit --id upserts, FTS5 refresh, and save-config persistence."""

  def test_sqlite_tier_saves_enrichment_and_fts5_searchability(self):
    """Verifies project, user, and custom SQLite saves allocate IDs, populate raw_hash/metadata, and update FTS5."""
    with tempfile.TemporaryDirectory() as tmp_root:
      store = SqliteScarStore()
      self.assertEqual(TIER_ID_PREFIXES["project"], "LOCAL")
      self.assertEqual(TIER_ID_PREFIXES["user"], "USER")
      self.assertEqual(TIER_ID_PREFIXES["custom"], "CUSTOM")

      proj_db = os.path.join(tmp_root, "workspace", ".workflow", "scars", "scars_registry.db")
      first = store.save_scar(
          proj_db,
          _sample_record(
              title="First Python SQLite Guard",
              category="DB",
              wound="Python sqlite3 connection leaked temporary handles.",
              reflex="Close Python sqlite3 handles inside a finally block.",
              socratic_probe="Did you close sqlite3 handles in a finally block?",
          ),
          tier="project",
      )
      second = store.save_scar(
          proj_db,
          _sample_record(title="Second DB Guard", category="DB", circuit_breaker=""),
          tier="project",
      )
      self.assertEqual(first["scarId"], "SCAR-LOCAL-DB-0001")
      self.assertEqual(second["scarId"], "SCAR-LOCAL-DB-0002")
      self.assertEqual(first["dbPath"], proj_db)
      self.assertEqual(first["source"], "local")
      self.assertFalse(first["updated"])
      self.assertTrue(os.path.isfile(proj_db))

      # next_tier_scar_id expands tilde paths safely (NBO-1)
      with mock.patch.dict(os.environ, _isolated_env(tmp_root), clear=True):
        self.assertEqual(
            next_tier_scar_id(
                "~/workspace/.workflow/scars/scars_registry.db", "DB", tier="project"
            ),
            "SCAR-LOCAL-DB-0003",
        )

      conn = sqlite3.connect(proj_db)
      try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        self.assertTrue({"scars", "scars_fts", "meta"}.issubset(tables))

        cur.execute(
            "SELECT raw_wound, raw_hash, keywords, languages, workflow_phases, source "
            "FROM scars WHERE id = 'SCAR-LOCAL-DB-0001'"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertIn("sqlite3", row[0])
        self.assertEqual(len(row[1]), 64)
        self.assertTrue(json.loads(row[2]))
        self.assertIn("python", json.loads(row[3]))
        self.assertTrue(json.loads(row[4]))
        self.assertEqual(row[5], "local")
      finally:
        conn.close()

      hits = query_scars(proj_db, query="sqlite3", language="python", limit=5)
      self.assertEqual([h["id"] for h in hits], ["SCAR-LOCAL-DB-0001"])

      user_db = os.path.join(tmp_root, "home", ".claude", "plugin_data", "makeflow", "scars_registry.db")
      custom_db = os.path.join(tmp_root, "shared_team", "team.db")
      user_res = store.save_scar(user_db, _sample_record(category="PROC"), tier="user")
      custom_res = store.save_scar(custom_db, _sample_record(category="TEST"), tier="custom")
      self.assertEqual(user_res["scarId"], "SCAR-USER-PROC-0001")
      self.assertEqual(user_res["source"], "user")
      self.assertEqual(custom_res["scarId"], "SCAR-CUSTOM-TEST-0001")
      self.assertEqual(custom_res["source"], "custom")

  def test_explicit_id_override_idempotent_upsert_and_fts5_refresh(self):
    """Verifies --id override inserts exact ID, updates in place on re-save, and purges stale FTS5 tokens."""
    with tempfile.TemporaryDirectory() as tmp_root:
      store = SqliteScarStore()
      proj_db = os.path.join(tmp_root, "workspace", ".workflow", "scars", "scars_registry.db")

      ins = store.save_scar(
          proj_db,
          _sample_record(
              title="Initial Override TokenAlpha",
              category="ARCH",
              wound="Initial failure mentioning tokenalpha_only.",
              reflex="Handle tokenalpha_only safely.",
              socratic_probe="Did you handle tokenalpha_only safely?",
          ),
          tier="project",
          scar_id="SCAR-ARCH-0001",
      )
      self.assertEqual(ins["scarId"], "SCAR-ARCH-0001")
      self.assertFalse(ins["updated"])
      self.assertEqual(
          [r["id"] for r in query_scars(proj_db, query="tokenalpha_only", limit=5)],
          ["SCAR-ARCH-0001"],
      )

      upd = store.save_scar(
          proj_db,
          _sample_record(
              title="Updated Override TokenBeta",
              category="ARCH",
              wound="Updated failure mentioning tokenbeta_only.",
              reflex="Handle tokenbeta_only safely.",
              socratic_probe="Did you handle tokenbeta_only safely?",
          ),
          tier="project",
          scar_id="SCAR-ARCH-0001",
      )
      self.assertEqual(upd["scarId"], "SCAR-ARCH-0001")
      self.assertTrue(upd["updated"])

      conn = sqlite3.connect(proj_db)
      try:
        count = conn.execute("SELECT COUNT(*) FROM scars").fetchone()[0]
      finally:
        conn.close()
      self.assertEqual(count, 1)

      self.assertEqual(query_scars(proj_db, query="tokenalpha_only", limit=5), [])
      beta_hits = query_scars(proj_db, query="tokenbeta_only", limit=5)
      self.assertEqual(len(beta_hits), 1)
      self.assertEqual(beta_hits[0]["title"], "Updated Override TokenBeta")

  def test_validation_rejects_malformed_id_or_payload_before_mutating_sqlite(self):
    """Verifies invalid --id, invalid record, or invalid db_path never creates or mutates SQLite files."""
    with tempfile.TemporaryDirectory() as tmp_root:
      store = SqliteScarStore()
      db_path = os.path.join(tmp_root, "never_created.db")

      with self.assertRaises(ValueError):
        store.save_scar(db_path, _sample_record(), tier="project", scar_id="BAD-ID")
      self.assertFalse(os.path.exists(db_path))

      bad_rec = _sample_record(socratic_probe="Missing question mark.")
      with self.assertRaises(ValueError):
        store.save_scar(db_path, bad_rec, tier="project", scar_id="SCAR-ARCH-0001")
      self.assertFalse(os.path.exists(db_path))

      for bad_db in ("", "   ", None, os.path.join(tmp_root, "bad\x00.db"), tmp_root):
        with self.subTest(bad_db=bad_db):
          with self.assertRaises(ValueError):
            store.save_scar(bad_db, _sample_record(), tier="project")  # type: ignore[arg-type]

  def test_scar_config_store_persists_scars_databases_without_clobbering_keys(self):
    """Verifies save-config updates scars.databases before bundled DB or appends if bundled omitted."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fake_home = os.path.join(tmp_root, "home")
      ws_dir = os.path.join(fake_home, "my_workspace")
      os.makedirs(ws_dir, exist_ok=True)

      registry = AgentPathRegistry(home_dir=fake_home)
      cfg_store = ScarConfigStore(registry=registry)

      # Local config omits bundled DB -> new DB appends at end
      local_cfg_path = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"tdd-worker": "pro"},
                "ci": {"quiescenceSeconds": 0},
                "scars": {"databases": [".workflow/scars/scars_registry.db"]},
            },
            fh,
        )

      # Global config lacks scars.databases -> seeds from DEFAULT_SCAR_DATABASES and inserts before bundled
      global_cfg_path = registry.get_profile("cursor").global_config_path
      os.makedirs(os.path.dirname(global_cfg_path), exist_ok=True)
      with open(global_cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"state": {"completedCardAction": "archive"}}, fh)

      team_db_a = os.path.join(tmp_root, "shared_team", "team_a.db")
      team_db_b = os.path.join(tmp_root, "shared_team", "team_b.db")

      # Tilde workspace_dir resolves against registry.home_dir (NBO-1)
      res_both = cfg_store.add_scar_database(
          team_db_a,
          scope="both",
          workspace_dir="~/my_workspace",
          agent="cursor",
      )
      self.assertEqual(set(res_both["updatedFiles"]), {global_cfg_path, local_cfg_path})

      # Re-adding is idempotent
      cfg_store.add_scar_database(
          team_db_a, scope="project-local", workspace_dir=ws_dir, agent="cursor"
      )
      cfg_store.add_scar_database(
          team_db_b, scope="user-global", workspace_dir=ws_dir, agent="cursor"
      )

      with open(local_cfg_path, "r", encoding="utf-8") as fh:
        local_data = json.load(fh)
      self.assertEqual(local_data["models"]["tdd-worker"], "pro")
      self.assertEqual(
          local_data["scars"]["databases"],
          [".workflow/scars/scars_registry.db", team_db_a],
      )
      self.assertNotIn("extraDatabases", local_data["scars"])

      with open(global_cfg_path, "r", encoding="utf-8") as fh:
        global_data = json.load(fh)
      self.assertEqual(global_data["state"]["completedCardAction"], "archive")
      self.assertEqual(
          global_data["scars"]["databases"],
          [
              DEFAULT_SCAR_DATABASES[0],
              DEFAULT_SCAR_DATABASES[1],
              team_db_a,
              team_db_b,
              DEFAULT_BUNDLED_SCAR_DB_TOKEN,
          ],
      )

      with self.assertRaises(ValueError):
        cfg_store.add_scar_database(team_db_a, scope="invalid-scope", workspace_dir=ws_dir)

  def test_scar_config_store_error_paths(self):
    """Verifies ScarConfigStore rejects invalid paths, missing workspace_dir, and corrupt/non-dict JSON (BLOCKER-2)."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
      cfg_store = ScarConfigStore(registry=registry)

      for bad_db in ("", "   ", None, os.path.join(ws_dir, "bad\x00.db")):
        with self.subTest(bad_db=bad_db):
          with self.assertRaises(ValueError):
            cfg_store.add_scar_database(
                bad_db, scope="user-global", workspace_dir=ws_dir  # type: ignore[arg-type]
            )

      for bad_ws in (None, "", "   ", os.path.join(ws_dir, "bad\x00ws")):
        with self.subTest(bad_ws=bad_ws):
          with self.assertRaises(ValueError):
            cfg_store.add_scar_database(
                os.path.join(fake_home, "team.db"),
                scope="project-local",
                workspace_dir=bad_ws,  # type: ignore[arg-type]
            )

      local_cfg_path = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        fh.write("{corrupt json")
      with self.assertRaises(json.JSONDecodeError):
        cfg_store.add_scar_database(
            os.path.join(fake_home, "team.db"),
            scope="project-local",
            workspace_dir=ws_dir,
        )

      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)
      with self.assertRaises(ValueError):
        cfg_store.add_scar_database(
            os.path.join(fake_home, "team.db"),
            scope="project-local",
            workspace_dir=ws_dir,
        )


class TestUpstreamIssuePublisher(unittest.TestCase):
  """Tests gh auth preflight, tier+built-in integer-ID stripping, --approved gate, and failure paths."""

  def test_format_issue_payload_strips_tier_builtin_and_lang_integer_ids(self):
    """Verifies upstream payload strips SCAR-LOCAL-*, SCAR-ARCH-0001, and SCAR-LANG-PYTHON-ARCH-0001 IDs."""
    publisher = UpstreamIssuePublisher()
    self.assertEqual(publisher.repo, UPSTREAM_REPO)

    cases = [
        ("SCAR-LOCAL-DATA-0001: Track Fenced Code Blocks", "SCAR-LOCAL-DATA-0001"),
        ("SCAR-ARCH-0001: Premature Abstraction Guard", "SCAR-ARCH-0001"),
        ("SCAR-LANG-PYTHON-ARCH-0001: Python Layer Separation", "SCAR-LANG-PYTHON-ARCH-0001"),
    ]
    for raw_title, forbidden_id in cases:
      with self.subTest(raw_title=raw_title):
        rec = _sample_record(
            title=raw_title,
            category="ARCH",
            wound=f"Failure recorded under {forbidden_id} in local registry.",
            trap=f"Overrides referencing {forbidden_id} leaked assigned integer IDs.",
            circuit_breaker=f"If {forbidden_id} appears in draft text, strip it first.",
            reflex=f"Always strip {forbidden_id} while keeping SCAR-TEST-45 references.",
            socratic_probe=f"Did you strip {forbidden_id} before opening an upstream issue?",
        )
        payload = publisher.format_issue_payload(rec)
        self.assertNotIn(forbidden_id, payload["title"])
        self.assertNotIn(forbidden_id, payload["body"])
        self.assertIn("SCAR-TEST-45", payload["body"])

    for id_only in ("SCAR-LOCAL-DB-0042", "SCAR-ARCH-0001:", "SCAR-LANG-PYTHON-ARCH-0001 - "):
      with self.subTest(id_only=id_only):
        with self.assertRaises(ValueError):
          publisher.format_issue_payload(_sample_record(title=id_only))

  def test_check_access_handles_missing_gh_unauth_and_ready(self):
    """Verifies preflight handles missing gh binary, unauthenticated state, and full access (BLOCKER-2)."""
    publisher = UpstreamIssuePublisher()
    with mock.patch("shutil.which", return_value=None):
      res_missing = publisher.check_access()
      self.assertFalse(res_missing["ready"])
      self.assertFalse(res_missing["ghInstalled"])

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run",
        return_value=mock.MagicMock(returncode=1, stdout="", stderr="not logged in"),
    ):
      res_unauth = publisher.check_access()
      self.assertFalse(res_unauth["ready"])
      self.assertTrue(res_unauth["ghInstalled"])
      self.assertFalse(res_unauth["authenticated"])

    def _ok_subp(cmd, **kwargs):
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="Logged in", stderr="")
      if cmd[:3] == ["gh", "repo", "view"]:
        return mock.MagicMock(
            returncode=0,
            stdout='{"nameWithOwner":"hedges333/makeflow-skill"}',
            stderr="",
        )
      return mock.MagicMock(returncode=0, stdout="", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_ok_subp
    ):
      res_ready = publisher.check_access()
      self.assertTrue(res_ready["ready"])
      self.assertTrue(res_ready["authenticated"])
      self.assertTrue(res_ready["repoAccessible"])

  def test_submit_issue_requires_approval_and_uses_body_file(self):
    """Verifies unapproved submit is blocked and approved submit passes --body-file via temp dir."""
    publisher = UpstreamIssuePublisher()
    rec = _sample_record(title="SCAR-ARCH-0001: Guard Empty URLs")

    with mock.patch("subprocess.run") as run_mock:
      with self.assertRaises(PermissionError):
        publisher.submit_issue(rec, approved=False)
      run_mock.assert_not_called()

    captured_body = {}

    def _mock_create(cmd, **kwargs):
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="Logged in", stderr="")
      if cmd[:3] == ["gh", "repo", "view"]:
        return mock.MagicMock(
            returncode=0, stdout='{"nameWithOwner":"hedges333/makeflow-skill"}', stderr=""
        )
      if cmd[:3] == ["gh", "issue", "create"]:
        self.assertIn("--body-file", cmd)
        body_path = cmd[cmd.index("--body-file") + 1]
        captured_body["path"] = body_path
        with open(body_path, "r", encoding="utf-8") as fh:
          captured_body["text"] = fh.read()
        return mock.MagicMock(
            returncode=0,
            stdout="https://github.com/hedges333/makeflow-skill/issues/999\n",
            stderr="",
        )
      return mock.MagicMock(returncode=1, stdout="", stderr="unexpected")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_mock_create
    ):
      result = publisher.submit_issue(rec, approved=True)

    self.assertTrue(result["submitted"])
    self.assertNotIn("SCAR-ARCH-0001", result["title"])
    self.assertNotIn("SCAR-ARCH-0001", captured_body["text"])
    self.assertFalse(os.path.exists(captured_body["path"]))

  def test_repo_validation_failure_paths_and_subprocess_timeouts(self):
    """Verifies owner/repo validation, timeout=30, TimeoutExpired/OSError handling, and gh failure paths (BLOCKER-2)."""
    for bad_repo in ("-flag", "-owner/repo", "noslash", "a/b/c", "", "owner/bad repo"):
      with self.subTest(bad_repo=bad_repo):
        with self.assertRaises(ValueError):
          UpstreamIssuePublisher(repo=bad_repo)

    publisher = UpstreamIssuePublisher(repo="org_1.2/repo-3.4")
    self.assertEqual(publisher.repo, "org_1.2/repo-3.4")

    # gh repo view inaccessible + verify timeout=30 passed
    seen_timeouts = []

    def _repo_inaccessible(cmd, **kwargs):
      seen_timeouts.append(kwargs.get("timeout"))
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")
      return mock.MagicMock(returncode=1, stdout="", stderr="HTTP 404")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_repo_inaccessible
    ):
      res = publisher.check_access()
    self.assertFalse(res["ready"])
    self.assertTrue(res["authenticated"])
    self.assertFalse(res["repoAccessible"])
    self.assertEqual(seen_timeouts, [30, 30])

    # TimeoutExpired on gh auth status returns structured ready=False
    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["gh", "auth", "status"], timeout=30),
    ):
      res_timeout = publisher.check_access()
    self.assertFalse(res_timeout["ready"])
    self.assertFalse(res_timeout["authenticated"])

    # OSError on gh repo view returns structured ready=False
    def _repo_oserror(cmd, **kwargs):
      if cmd[:3] == ["gh", "auth", "status"]:
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")
      raise OSError("exec error")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_repo_oserror
    ):
      res_oserr = publisher.check_access()
    self.assertFalse(res_oserr["ready"])
    self.assertTrue(res_oserr["authenticated"])
    self.assertFalse(res_oserr["repoAccessible"])

    # submit_issue rejects invalid scar before calling gh
    invalid_rec = MinedScarRecord(
        title="Bad Scar",
        category="DB",
        wound="Wound.",
        trap="Trap.",
        reflex="Reflex.",
        socratic_probe="Missing question mark.",
    )
    with mock.patch("subprocess.run") as run_mock:
      with self.assertRaises(ValueError):
        publisher.submit_issue(invalid_rec, approved=True)
      run_mock.assert_not_called()

    # submit_issue converts gh issue create non-zero exit into RuntimeError and passes timeout=30
    create_timeouts = []

    def _create_fails(cmd, **kwargs):
      create_timeouts.append(kwargs.get("timeout"))
      if cmd[:3] == ["gh", "issue", "create"]:
        return mock.MagicMock(returncode=1, stdout="", stderr="permission denied")
      return mock.MagicMock(returncode=0, stdout="ok", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
        "subprocess.run", side_effect=_create_fails
    ):
      with self.assertRaises(RuntimeError):
        publisher.submit_issue(_sample_record(), approved=True)
    self.assertEqual(create_timeouts, [30, 30, 30])

    # submit_issue converts gh issue create TimeoutExpired/OSError into RuntimeError
    for exc in (
        subprocess.TimeoutExpired(cmd=["gh", "issue", "create"], timeout=30),
        OSError("spawn failed"),
    ):
      def _create_raises(cmd, _exc=exc, **kwargs):
        if cmd[:3] == ["gh", "issue", "create"]:
          raise _exc
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")

      with self.subTest(exc=type(exc).__name__):
        with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
            "subprocess.run", side_effect=_create_raises
        ):
          with self.assertRaises(RuntimeError):
            publisher.submit_issue(_sample_record(), approved=True)


class TestScarMinerScriptSubcommands(unittest.TestCase):
  """Tests all five explicit BaseScript CLI subcommands for SQLite storage (GH-304)."""

  def test_all_five_subcommands_via_execute(self):
    """Verifies paths, save (with --id/--custom-db), save-config (--db), upstream-check, upstream-submit."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fake_home = os.path.join(tmp_root, "home")
      ws_dir = os.path.join(tmp_root, "workspace")
      plugin_root = os.path.join(tmp_root, "plugin_root")
      custom_db = os.path.join(tmp_root, "shared_team", "team.db")
      for d in (fake_home, ws_dir, plugin_root):
        os.makedirs(d, exist_ok=True)
      spokes_dir = os.path.join(plugin_root, "skills", "makeflow", "references", "scars")
      os.makedirs(spokes_dir, exist_ok=True)
      spoke_file = os.path.join(spokes_dir, "process.md")
      with open(spoke_file, "w", encoding="utf-8") as fh:
        fh.write("# Process\n")

      # 1. paths subcommand emits process_scars_dir, process_scar_files, project_db, user_db, bundled_db, configured_databases, existing_databases
      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
        out, err = io.StringIO(), io.StringIO()
        rc = ScarMinerScript().execute(
            args=[
                "paths",
                "--workspace",
                ws_dir,
                "--agent",
                "claude",
                "--home",
                fake_home,
                "--plugin-root",
                plugin_root,
            ],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(rc, 0, msg=err.getvalue())
      paths_data = json.loads(out.getvalue())
      expected_proj_db = os.path.join(ws_dir, ".workflow", "scars", "scars_registry.db")
      expected_user_db = os.path.join(
          fake_home, ".claude", "plugin_data", "makeflow", "scars_registry.db"
      )
      expected_bundled_db = os.path.join(
          plugin_root, "skills", "makeflow", "data", "scars_registry.db"
      )
      self.assertEqual(paths_data["process_scars_dir"], spokes_dir)
      self.assertEqual(paths_data["process_scar_files"], [spoke_file])
      self.assertEqual(paths_data["project_db"], expected_proj_db)
      self.assertEqual(paths_data["user_db"], expected_user_db)
      self.assertEqual(paths_data["bundled_db"], expected_bundled_db)
      self.assertEqual(
          paths_data["configured_databases"],
          [expected_proj_db, expected_user_db, expected_bundled_db],
      )
      self.assertEqual(paths_data["existing_databases"], [])

      # 2a. save --tier project with explicit --id override
      common_flags = [
          "--title",
          "Guard Empty URLs",
          "--category",
          "DB",
          "--wound",
          "An empty database URL opened an unintended local file.",
          "--trap",
          "Callers assumed configuration always returned a connection string.",
          "--circuit-breaker",
          "If URL is empty, stop before opening a handle.",
          "--reflex",
          "Validate the connection URL before opening a handle.",
          "--socratic-probe",
          "Did you validate the database URL before opening a handle?",
      ]
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save",
              "--tier",
              "project",
              "--workspace",
              ws_dir,
              "--home",
              fake_home,
              "--id",
              "SCAR-ARCH-0001",
          ]
          + common_flags,
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      save_data = json.loads(out.getvalue())
      self.assertEqual(save_data["scarId"], "SCAR-ARCH-0001")
      self.assertEqual(save_data["dbPath"], expected_proj_db)
      self.assertTrue(os.path.isfile(expected_proj_db))

      # 2b. save --tier user
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "user", "--agent", "claude", "--home", fake_home]
          + common_flags,
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      user_save = json.loads(out.getvalue())
      self.assertEqual(user_save["scarId"], "SCAR-USER-DB-0001")
      self.assertEqual(user_save["dbPath"], expected_user_db)

      # 2c. save --tier custom with --custom-db (and failure when missing --custom-db)
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=["save", "--tier", "custom", "--custom-db", custom_db] + common_flags,
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      custom_save = json.loads(out.getvalue())
      self.assertEqual(custom_save["scarId"], "SCAR-CUSTOM-DB-0001")
      self.assertEqual(custom_save["dbPath"], custom_db)

      out_bad, err_bad = io.StringIO(), io.StringIO()
      rc_bad = ScarMinerScript().execute(
          args=["save", "--tier", "custom"] + common_flags,
          stdout=out_bad,
          stderr=err_bad,
      )
      self.assertNotEqual(rc_bad, 0)
      self.assertIn("--custom-db", err_bad.getvalue())

      # 3. save-config --db --scope both
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save-config",
              "--db",
              custom_db,
              "--scope",
              "both",
              "--workspace",
              ws_dir,
              "--agent",
              "claude",
              "--home",
              fake_home,
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      cfg_res = json.loads(out.getvalue())
      self.assertEqual(cfg_res["scope"], "both")
      self.assertEqual(cfg_res["database"], custom_db)
      self.assertEqual(len(cfg_res["updatedFiles"]), 2)

      # 4 & 5. upstream-check and upstream-submit
      def _ok_gh(cmd, **kwargs):
        if cmd[:3] == ["gh", "issue", "create"]:
          return mock.MagicMock(
              returncode=0,
              stdout="https://github.com/hedges333/makeflow-skill/issues/777\n",
              stderr="",
          )
        return mock.MagicMock(returncode=0, stdout="ok", stderr="")

      with mock.patch("shutil.which", return_value="/usr/bin/gh"), mock.patch(
          "subprocess.run", side_effect=_ok_gh
      ):
        out, err = io.StringIO(), io.StringIO()
        rc = ScarMinerScript().execute(args=["upstream-check"], stdout=out, stderr=err)
        self.assertEqual(rc, 0, msg=err.getvalue())
        self.assertTrue(json.loads(out.getvalue())["ready"])

        out_un, err_un = io.StringIO(), io.StringIO()
        rc_un = ScarMinerScript().execute(
            args=["upstream-submit"] + common_flags, stdout=out_un, stderr=err_un
        )
        self.assertNotEqual(rc_un, 0)
        self.assertIn("--approved", err_un.getvalue())

        out_ok, err_ok = io.StringIO(), io.StringIO()
        rc_ok = ScarMinerScript().execute(
            args=[
                "upstream-submit",
                "--approved",
                "--title",
                "SCAR-LANG-PYTHON-DB-0001: Guard Empty URLs",
                "--category",
                "DB",
                "--wound",
                "An empty database URL opened an unintended local file.",
                "--trap",
                "Callers assumed configuration always returned a connection string.",
                "--reflex",
                "Validate the connection URL before opening a handle.",
                "--socratic-probe",
                "Did you validate the database URL before opening a handle?",
            ],
            stdout=out_ok,
            stderr=err_ok,
        )
        self.assertEqual(rc_ok, 0, msg=err_ok.getvalue())
        sub_res = json.loads(out_ok.getvalue())
        self.assertTrue(sub_res["submitted"])
        self.assertNotIn("SCAR-LANG-PYTHON-DB-0001", sub_res["title"])

  def test_cli_json_file_snake_camel_overrides_and_non_object_rejection(self):
    """Verifies --json-file supports snake_case and camelCase, CLI overrides, and rejects non-objects (BLOCKER-2)."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      snake_path = os.path.join(fake_home, "snake.json")
      with open(snake_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "title": "Snake Case Scar",
                "category": "DB",
                "wound": "An empty database URL opened an unintended local file.",
                "trap": "Callers assumed configuration always returned a connection string.",
                "circuit_breaker": "If URL is empty, halt before connecting.",
                "permanent_reflex": "Validate the connection URL before opening a handle.",
                "socratic_probe": "Did you validate the database URL before connecting?",
                "applies_when": "Target 30: tdd",
            },
            fh,
        )

      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save",
              "--tier",
              "project",
              "--workspace",
              ws_dir,
              "--home",
              fake_home,
              "--json-file",
              snake_path,
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      snake_res = json.loads(out.getvalue())
      self.assertEqual(snake_res["scarId"], "SCAR-LOCAL-DB-0001")

      camel_path = os.path.join(fake_home, "camel.json")
      with open(camel_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "title": "Original Camel Title",
                "category": "TEST",
                "wound": "Tests wrote files into the workspace root.",
                "trap": "Tests skipped temporary directories.",
                "circuitBreaker": "If writing test files, stop before using CWD.",
                "permanentReflex": "Use TemporaryDirectory for every test file write.",
                "socraticProbe": "Does this test use TemporaryDirectory?",
                "appliesWhen": "Target 30: tdd",
            },
            fh,
        )
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save",
              "--tier",
              "project",
              "--workspace",
              ws_dir,
              "--home",
              fake_home,
              "--json-file",
              camel_path,
              "--title",
              "CLI Override Title",
          ],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(rc, 0, msg=err.getvalue())
      camel_res = json.loads(out.getvalue())
      saved_rows = query_scars(camel_res["dbPath"], query=camel_res["scarId"], limit=5)
      self.assertEqual(len(saved_rows), 1)
      self.assertEqual(saved_rows[0]["title"], "CLI Override Title")
      self.assertEqual(saved_rows[0]["appliesWhen"], "Target 30: tdd")

      bad_json_path = os.path.join(fake_home, "array.json")
      with open(bad_json_path, "w", encoding="utf-8") as fh:
        json.dump(["not", "an", "object"], fh)
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(
          args=[
              "save",
              "--tier",
              "project",
              "--workspace",
              ws_dir,
              "--home",
              fake_home,
              "--json-file",
              bad_json_path,
          ],
          stdout=out,
          stderr=err,
      )
      self.assertNotEqual(rc, 0)
      self.assertIn("JSON object", err.getvalue())

  def test_cli_upstream_check_returns_exit_code_1_when_not_ready(self):
    """Verifies upstream-check CLI returns exit code 1 when gh access is not ready (BLOCKER-2)."""
    with mock.patch("shutil.which", return_value=None):
      out, err = io.StringIO(), io.StringIO()
      rc = ScarMinerScript().execute(args=["upstream-check"], stdout=out, stderr=err)
    self.assertEqual(rc, 1)
    payload = json.loads(out.getvalue())
    self.assertFalse(payload["ready"])
    self.assertFalse(payload["ghInstalled"])

  def test_save_scar_signature_invariance_and_paths_context_cwd_isolation(self):
    """Verifies SqliteScarStore.save_scar stays free of path context and paths subcommand isolates CWD (GH-319)."""
    save_sig = inspect.signature(SqliteScarStore.save_scar)
    self.assertEqual(
        list(save_sig.parameters.keys()),
        ["self", "db_path", "record", "tier", "scar_id"],
    )
    for forbidden in ("home_dir", "agent", "context"):
      self.assertNotIn(forbidden, save_sig.parameters)

    with tempfile.TemporaryDirectory() as tmp_root:
      fake_home = os.path.join(tmp_root, "home")
      ws_dir = os.path.join(tmp_root, "workspace")
      plugin_root = os.path.join(tmp_root, "plugin_root")
      ambient_db = os.path.join(tmp_root, "ambient_cwd.db")
      for d in (fake_home, ws_dir, plugin_root):
        os.makedirs(d, exist_ok=True)
      local_cfg = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg), exist_ok=True)
      with open(local_cfg, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        ".workflow/scars/scars_registry.db",
                        ambient_db,
                        "{agent_data}/scars_registry.db",
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      prev_cwd = os.getcwd()
      try:
        os.chdir(ws_dir)
        with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
          out, err = io.StringIO(), io.StringIO()
          rc = ScarMinerScript().execute(
              args=[
                  "paths",
                  "--agent",
                  "claude",
                  "--home",
                  fake_home,
                  "--plugin-root",
                  plugin_root,
              ],
              stdout=out,
              stderr=err,
          )
      finally:
        os.chdir(prev_cwd)

      self.assertEqual(rc, 0, msg=err.getvalue())
      data = json.loads(out.getvalue())
      self.assertIsNone(data["project_db"])
      self.assertNotIn(ambient_db, data["configured_databases"])
      self.assertEqual(
          data["user_db"],
          os.path.join(
              fake_home, ".claude", "plugin_data", "makeflow", "scars_registry.db"
          ),
      )


class TestRepositoryMigrationVerification(unittest.TestCase):
  """Verifies SCAR-LOCAL-DATA-0001 migration into .workflow/scars/scars_registry.db (GH-304)."""

  def test_scar_local_data_0001_migrated_to_project_sqlite_db_and_markdown_removed(self):
    """Verifies real workspace .workflow/scars/scars_registry.db holds SCAR-LOCAL-DATA-0001 and data.md is gone."""
    legacy_md = os.path.join(REPO_ROOT, ".workflow", "scars", "data.md")
    project_db = os.path.join(REPO_ROOT, ".workflow", "scars", "scars_registry.db")
    self.assertFalse(
        os.path.exists(legacy_md),
        "Obsolete .workflow/scars/data.md must be deleted after migration.",
    )
    self.assertTrue(
        os.path.isfile(project_db),
        "Migrated .workflow/scars/scars_registry.db must exist.",
    )
    hits = query_scars(project_db, query="fenced", limit=5)
    self.assertEqual(len(hits), 1)
    self.assertEqual(hits[0]["id"], "SCAR-LOCAL-DATA-0001")
    self.assertEqual(hits[0]["category"], "DATA")
    self.assertEqual(hits[0]["source"], "local")
    self.assertTrue(hits[0]["keywords"])
    self.assertTrue(hits[0]["workflowPhases"])
    self.assertIn("fenced", hits[0]["circuitBreaker"].lower())


if __name__ == "__main__":
  unittest.main()
