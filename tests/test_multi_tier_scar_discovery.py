"""Behavioral tests for ordered SQLite multi-tier scar discovery and ID overrides (GH-304)."""

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.agent_paths import AgentPathRegistry
from lib.scar_candidate import ScarCandidateValidator, validate_socratic_probe
import lib.scars_db as scars_db_mod
from lib.scars_db import (
    compute_raw_fields_hash,
    query_scars,
    resolve_scars_db_path,
    resolve_scars_db_paths,
    upsert_scar,
)
from lib.sync_synthetic_scars import SyncSyntheticScarsScript
from mcp import server as mcp_server


def _isolated_env(home_dir, extra=None):
  """Build a minimal isolated environment pointing HOME to a temporary directory."""
  env = {
      "HOME": home_dir,
      "PATH": os.environ.get("PATH", ""),
  }
  if extra:
    env.update(extra)
  return env


def _build_mock_fs_root(tmp_root):
  """Construct a hermetic mocked filesystem root tree inside tmp_root."""
  home_dir = os.path.join(tmp_root, "home")
  ws_dir = os.path.join(tmp_root, "workspace")
  plugin_root = os.path.join(tmp_root, "plugin_root")
  shared_dir = os.path.join(tmp_root, "shared_team")
  for d in (home_dir, ws_dir, plugin_root, shared_dir):
    os.makedirs(d, exist_ok=True)
  return {
      "home": home_dir,
      "workspace": ws_dir,
      "plugin_root": plugin_root,
      "shared": shared_dir,
      "project_db": os.path.join(ws_dir, ".workflow", "scars", "scars_registry.db"),
      "bundled_db": os.path.join(
          plugin_root, "skills", "makeflow", "data", "scars_registry.db"
      ),
  }


def _seed_db(db_path, rows):
  """Populate an isolated SQLite scars DB via canonical upsert_scar()."""
  for row in rows:
    upsert_scar(db_path, row)


def _scar_dict(
    scar_id,
    title="Sample Scar",
    category="ARCH",
    wound="Sample failure wound.",
    trap="Sample false assumption.",
    reflex="Apply the permanent engineering reflex.",
    socratic_probe="Did you apply the permanent reflex?",
    circuit_breaker="",
    source="local",
    ss_id=None,
    original_id=None,
    workflow_phases=None,
    applies_when="",
):
  """Build a complete scar record dictionary for upsert_scar()."""
  rec = {
      "id": scar_id,
      "title": title,
      "category": category,
      "wound": wound,
      "trap": trap,
      "circuit_breaker": circuit_breaker,
      "reflex": reflex,
      "socratic_probe": socratic_probe,
      "source": source,
      "ss_id": ss_id,
      "original_id": original_id,
      "applies_when": applies_when,
  }
  if workflow_phases is not None:
    rec["workflow_phases"] = workflow_phases
  return rec


class TestMultiTierSqliteScarDiscovery(unittest.TestCase):
  """Tests ordered SQLite multi-tier discovery, same-ID overrides, and namespace-safe shadowing (GH-304)."""

  def test_obsolete_markdown_tier_query_helpers_removed(self):
    """Verifies Markdown tier query helpers were removed from lib/scars_db.py."""
    for removed in (
        "resolve_scar_markdown_dirs",
        "_infer_markdown_dir_tier",
        "_query_markdown_scars_dir",
        "count_markdown_dir_scars",
    ):
      self.assertFalse(
          hasattr(scars_db_mod, removed),
          f"Obsolete Markdown helper '{removed}' must be removed from lib/scars_db.py",
      )

  def test_resolve_scars_db_path_supports_codex_via_agent_paths(self):
    """Verifies resolve_scars_db_path delegates agent lookup cleanly with injected home_dir."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        resolved = resolve_scars_db_path(
            agent="codex",
            package_root=fs["plugin_root"],
            home_dir=fs["home"],
        )
      expected = os.path.join(
          fs["home"], ".codex", "plugin_data", "makeflow", "scars_registry.db"
      )
      self.assertEqual(resolved, expected)

  def test_full_multi_tier_precedence_ordering_and_missing_db_resilience(self):
    """Verifies multi-tier SQLite precedence order and clean skipping of missing configured DBs."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])
      user_db = registry.get_profile("claude").scars_db_path
      proj_custom_db = os.path.join(fs["shared"], "project_custom.db")
      env_custom_db = os.path.join(fs["shared"], "env_custom.db")
      missing_db = os.path.join(fs["shared"], "does_not_exist_yet.db")

      _seed_db(
          fs["project_db"],
          [
              _scar_dict(
                  "SCAR-LOCAL-STORAGE-0001",
                  title="Project Quantum Cache Guard",
                  category="STORAGE",
                  wound="Python cache writes raced during quantum builds.",
                  reflex="Use atomic rename for quantum cache files.",
                  socratic_probe="Did you write quantum cache files atomically?",
                  source="local",
              )
          ],
      )
      _seed_db(
          user_db,
          [
              _scar_dict(
                  "SCAR-USER-NET-0001",
                  title="User Quantum Retry Guard",
                  category="NET",
                  wound="Quantum HTTP retries lacked backoff.",
                  reflex="Add exponential backoff to quantum HTTP retries.",
                  socratic_probe="Did you add backoff to quantum retries?",
                  source="user",
              )
          ],
      )
      _seed_db(
          proj_custom_db,
          [
              _scar_dict(
                  "SCAR-CUSTOM-SCHEMA-0001",
                  title="Project Custom Quantum Schema Guard",
                  category="SCHEMA",
                  wound="Quantum SQLite migrations dropped columns.",
                  reflex="Test quantum migrations on a temporary copy.",
                  socratic_probe="Did you test the quantum migration safely?",
                  source="custom",
              )
          ],
      )
      _seed_db(
          env_custom_db,
          [
              _scar_dict(
                  "SCAR-CUSTOM-SEC-0003",
                  title="Env Custom Quantum Secret Guard",
                  category="SEC",
                  wound="Quantum secrets leaked in logs.",
                  reflex="Redact quantum secrets before logging.",
                  socratic_probe="Did you redact quantum secrets?",
                  source="custom",
              )
          ],
      )
      _seed_db(
          fs["bundled_db"],
          [
              _scar_dict(
                  "SCAR-ARCH-0001",
                  title="Bundled Quantum Architecture Guard",
                  category="ARCH",
                  wound="Bundled quantum architecture wound.",
                  reflex="Keep quantum modules decoupled.",
                  socratic_probe="Are quantum modules decoupled?",
                  source="makeflow",
              ),
              _scar_dict(
                  "SCAR-PROC-0001",
                  title="Bundled Quantum Workflow Guard",
                  category="PROC",
                  wound="Quantum workflow pause skipped.",
                  reflex="Pause at every quantum human gate.",
                  socratic_probe="Did you pause for human approval?",
                  source="makeflow",
              ),
          ],
      )

      proj_cfg = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(proj_cfg), exist_ok=True)
      with open(proj_cfg, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        ".workflow/scars/scars_registry.db",
                        "{agent_data}/scars_registry.db",
                        proj_custom_db,
                        missing_db,
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      env = _isolated_env(
          fs["home"],
          {
              "MAKEFLOW_AGENT": "claude",
              "MAKEFILE_WORKFLOW_SCAR_DBS": env_custom_db,
          },
      )
      with mock.patch.dict(os.environ, env, clear=True):
        db_paths = resolve_scars_db_paths(
            workspace_dir=fs["workspace"],
            package_root=fs["plugin_root"],
            home_dir=fs["home"],
        )

      # Missing DB skipped without auto-creating an empty file on disk
      self.assertFalse(os.path.exists(missing_db))
      self.assertEqual(
          db_paths,
          [
              fs["project_db"],
              user_db,
              proj_custom_db,
              env_custom_db,
              fs["bundled_db"],
          ],
      )

      results = query_scars(
          db_path=db_paths,
          query="Quantum",
          exclude_categories=["WORKFLOW"],
          limit=10,
      )
      self.assertEqual(
          [r["id"] for r in results],
          [
              "SCAR-LOCAL-STORAGE-0001",
              "SCAR-USER-NET-0001",
              "SCAR-CUSTOM-SCHEMA-0001",
              "SCAR-CUSTOM-SEC-0003",
              "SCAR-ARCH-0001",
          ],
      )

  def test_direct_and_multi_way_same_id_overrides_across_sources(self):
    """Verifies 2-way, 3-way, and cross-source same-ID overrides strictly let highest tier win."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])
      user_db = registry.get_profile("gemini").scars_db_path
      custom_db = os.path.join(fs["shared"], "team.db")

      _seed_db(
          fs["bundled_db"],
          [
              _scar_dict("SCAR-ARCH-0001", title="Bundled ARCH", category="ARCH", source="upstream"),
              _scar_dict("SCAR-API-0001", title="Bundled API", category="API", source="makeflow"),
              _scar_dict("SCAR-DB-0001", title="Bundled DB", category="DB", source="upstream"),
              _scar_dict("SCAR-TEST-0001", title="Bundled TEST", category="TEST", source="makeflow"),
          ],
      )
      _seed_db(
          custom_db,
          [
              _scar_dict("SCAR-ARCH-0001", title="Custom ARCH", category="ARCH", source="custom"),
              _scar_dict("SCAR-DB-0001", title="Custom DB", category="DB", source="custom"),
          ],
      )
      _seed_db(
          user_db,
          [
              _scar_dict("SCAR-ARCH-0001", title="User ARCH", category="ARCH", source="user"),
              _scar_dict("SCAR-API-0001", title="User API", category="API", source="user"),
          ],
      )
      _seed_db(
          fs["project_db"],
          [
              _scar_dict("SCAR-ARCH-0001", title="Project ARCH", category="ARCH", source="local"),
              _scar_dict("SCAR-TEST-0001", title="Project TEST", category="TEST", source="local"),
          ],
      )

      ordered = [fs["project_db"], user_db, custom_db, fs["bundled_db"]]

      res_arch = query_scars(ordered, query="SCAR-ARCH-0001", limit=10)
      self.assertEqual(len(res_arch), 1)
      self.assertEqual(res_arch[0]["title"], "Project ARCH")
      self.assertEqual(res_arch[0]["source"], "local")

      res_test = query_scars(ordered, query="SCAR-TEST-0001", limit=10)
      self.assertEqual(len(res_test), 1)
      self.assertEqual(res_test[0]["title"], "Project TEST")
      self.assertEqual(res_test[0]["source"], "local")

      res_api = query_scars(ordered, query="SCAR-API-0001", limit=10)
      self.assertEqual(len(res_api), 1)
      self.assertEqual(res_api[0]["title"], "User API")
      self.assertEqual(res_api[0]["source"], "user")

      res_user_arch = query_scars([user_db, custom_db, fs["bundled_db"]], query="SCAR-ARCH-0001", limit=10)
      self.assertEqual(len(res_user_arch), 1)
      self.assertEqual(res_user_arch[0]["title"], "User ARCH")

      res_db = query_scars(ordered, query="SCAR-DB-0001", limit=10)
      self.assertEqual(len(res_db), 1)
      self.assertEqual(res_db[0]["title"], "Custom DB")
      self.assertEqual(res_db[0]["source"], "custom")

  def test_same_db_and_cross_tier_namespace_safe_ssid_vs_original_id_shadowing(self):
    """Verifies sibling rows never shadow each other and original_id never extinguishes ss_id (BLOCKER-1)."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)

      # 1. Single DB containing Makeflow scar (original_id='SCAR-ARCH-09') and Upstream scar (ss_id='SCAR-ARCH-09')
      _seed_db(
          fs["bundled_db"],
          [
              _scar_dict(
                  "SCAR-ARCH-0009",
                  original_id="SCAR-ARCH-09",
                  title="Makeflow Upstream Root Cause Remediation",
                  category="ARCH",
                  wound="Workarounds masked upstream defects.",
                  reflex="Fix defects upstream instead of stacking workarounds.",
                  socratic_probe="Can you fix the root cause upstream?",
                  source="makeflow",
              ),
              _scar_dict(
                  "SCAR-LANG-DART-ARCH-0009",
                  ss_id="SCAR-ARCH-09",
                  title="Dart Widget Boundary Architecture",
                  category="ARCH",
                  wound="Dart widgets coupled storage state directly.",
                  reflex="Isolate Dart storage behind a repository boundary.",
                  socratic_probe="Are Dart widgets decoupled from storage?",
                  source="upstream",
              ),
          ],
      )

      # Both rows return on query='SCAR-ARCH-09' AND on category='ARCH'
      by_query = query_scars(fs["bundled_db"], query="SCAR-ARCH-09", limit=10)
      self.assertEqual(
          {r["id"] for r in by_query},
          {"SCAR-ARCH-0009", "SCAR-LANG-DART-ARCH-0009"},
      )
      by_cat = query_scars(fs["bundled_db"], category="ARCH", limit=10)
      self.assertEqual(
          {r["id"] for r in by_cat},
          {"SCAR-ARCH-0009", "SCAR-LANG-DART-ARCH-0009"},
      )

      # 2. Across tiers: overriding SCAR-ARCH-0009 (with original_id='SCAR-ARCH-09') in project DB
      # suppresses ONLY bundled SCAR-ARCH-0009 and does NOT extinguish SCAR-LANG-DART-ARCH-0009
      _seed_db(
          fs["project_db"],
          [
              _scar_dict(
                  "SCAR-ARCH-0009",
                  original_id="SCAR-ARCH-09",
                  title="Project Override Root Cause Remediation",
                  category="ARCH",
                  wound="Project override wound.",
                  reflex="Project override upstream reflex.",
                  socratic_probe="Did you apply the project override?",
                  source="local",
              )
          ],
      )

      tiered = query_scars(
          [fs["project_db"], fs["bundled_db"]],
          query="SCAR-ARCH-09",
          limit=10,
      )
      self.assertEqual(len(tiered), 2)
      by_id = {r["id"]: r for r in tiered}
      self.assertEqual(set(by_id.keys()), {"SCAR-ARCH-0009", "SCAR-LANG-DART-ARCH-0009"})
      self.assertEqual(by_id["SCAR-ARCH-0009"]["title"], "Project Override Root Cause Remediation")
      self.assertEqual(by_id["SCAR-ARCH-0009"]["source"], "local")
      self.assertEqual(by_id["SCAR-LANG-DART-ARCH-0009"]["source"], "upstream")

  def test_bundled_db_readonly_smoke_preserves_distinct_original_id_and_ss_id_rows(self):
    """Read-only smoke check against bundled scars_registry.db for SCAR-ARCH-09 and SCAR-ARCH-10 (BLOCKER-1)."""
    real_bundled = os.path.join(
        REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
    )
    self.assertTrue(os.path.isfile(real_bundled))

    res_09 = query_scars(real_bundled, query="SCAR-ARCH-09", limit=10)
    ids_09 = {r["id"] for r in res_09}
    self.assertIn("SCAR-ARCH-0009", ids_09)
    self.assertIn("SCAR-LANG-DART-ARCH-0009", ids_09)

    res_10 = query_scars(real_bundled, query="SCAR-ARCH-10", limit=10)
    ids_10 = {r["id"] for r in res_10}
    self.assertIn("SCAR-ARCH-0010", ids_10)
    self.assertIn("SCAR-LANG-DART-ARCH-0010", ids_10)

  def test_non_matching_prose_filter_mismatch_and_ssid_override_shadowing(self):
    """Verifies higher-tier overrides strictly suppress lower-tier rows even when queries do not match override."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)

      _seed_db(
          fs["bundled_db"],
          [
              _scar_dict(
                  "SCAR-ARCH-0001",
                  ss_id="SCAR-ARCH-01",
                  title="Legacy Dart Abstraction Rule",
                  category="ARCH",
                  wound="Dart code suffered legacy_unique_keyword failure.",
                  trap="Used legacy_unique_keyword prematurely in Dart.",
                  reflex="Avoid legacy_unique_keyword in Dart modules.",
                  socratic_probe="Did you avoid legacy_unique_keyword in Dart?",
                  workflow_phases=["Target 40: review"],
                  source="upstream",
              ),
          ],
      )
      _seed_db(
          fs["project_db"],
          [
              _scar_dict(
                  "SCAR-ARCH-0001",
                  title="Project Python Replacement Rule",
                  category="ARCH",
                  wound="Python code requires replacement_unique_keyword handling.",
                  trap="Ignored replacement_unique_keyword in Python.",
                  reflex="Apply replacement_unique_keyword in Python modules.",
                  socratic_probe="Did you apply replacement_unique_keyword in Python?",
                  workflow_phases=["Target 20: plan"],
                  source="local",
              ),
          ],
      )

      ordered = [fs["project_db"], fs["bundled_db"]]

      self.assertEqual(query_scars(ordered, query="legacy_unique_keyword", limit=5), [])
      rep = query_scars(ordered, query="replacement_unique_keyword", limit=5)
      self.assertEqual(len(rep), 1)
      self.assertEqual(rep[0]["title"], "Project Python Replacement Rule")

      self.assertEqual(query_scars(ordered, language="dart", limit=5), [])
      self.assertEqual(
          query_scars(ordered, workflow_phase="Target 40: review", limit=5),
          [],
      )
      self.assertEqual(query_scars(ordered, query="SCAR-ARCH-01", limit=5), [])

  def test_accurate_limit_budget_under_shadowing(self):
    """Verifies limit=N returns N distinct scars even when higher tiers shadow lower-tier top matches."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)

      _seed_db(
          fs["bundled_db"],
          [
              _scar_dict("SCAR-ARCH-0001", title="Bundled Zeta One", category="ARCH", wound="Zeta alpha failure.", reflex="Fix zeta one.", socratic_probe="Fixed zeta one?", source="upstream"),
              _scar_dict("SCAR-ARCH-0002", title="Bundled Zeta Two", category="ARCH", wound="Zeta beta failure.", reflex="Fix zeta two.", socratic_probe="Fixed zeta two?", source="upstream"),
              _scar_dict("SCAR-ARCH-0003", title="Bundled Zeta Three", category="ARCH", wound="Zeta gamma failure.", reflex="Fix zeta three.", socratic_probe="Fixed zeta three?", source="upstream"),
          ],
      )
      _seed_db(
          fs["project_db"],
          [
              _scar_dict("SCAR-ARCH-0001", title="Project Zeta One Override", category="ARCH", wound="Project zeta override.", reflex="Apply project zeta.", socratic_probe="Applied project zeta?", source="local"),
          ],
      )

      ordered = [fs["project_db"], fs["bundled_db"]]
      res = query_scars(ordered, query="Zeta", limit=2)
      self.assertEqual(len(res), 2)
      self.assertEqual([r["id"] for r in res], ["SCAR-ARCH-0001", "SCAR-ARCH-0002"])
      self.assertEqual(res[0]["title"], "Project Zeta One Override")
      self.assertEqual(res[1]["title"], "Bundled Zeta Two")

  def test_explicit_db_path_isolation_and_corrupt_tier_resilience(self):
    """Verifies explicit db_path isolates to one DB and corrupt/empty sibling DBs do not crash queries."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      corrupt_db = os.path.join(fs["shared"], "corrupt.db")
      with open(corrupt_db, "wb") as fh:
        fh.write(b"not a valid sqlite database file at all")

      schemaless_db = os.path.join(fs["shared"], "empty_schema.db")
      conn = sqlite3.connect(schemaless_db)
      conn.execute("CREATE TABLE unrelated (x INTEGER)")
      conn.commit()
      conn.close()

      _seed_db(
          fs["project_db"],
          [_scar_dict("SCAR-LOCAL-ARCH-0001", title="Project Isolated", category="ARCH", wound="Alpha isolated.", reflex="Keep alpha isolated.", socratic_probe="Is alpha isolated?", source="local")],
      )
      _seed_db(
          fs["bundled_db"],
          [_scar_dict("SCAR-ARCH-0009", title="Bundled Valid Survivor", category="ARCH", wound="Alpha survivor.", reflex="Keep alpha surviving.", socratic_probe="Did alpha survive?", source="makeflow")],
      )

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        isolated_paths = resolve_scars_db_paths(
            db_path=fs["project_db"],
            workspace_dir=fs["workspace"],
            package_root=fs["plugin_root"],
            home_dir=fs["home"],
        )
      self.assertEqual(isolated_paths, [fs["project_db"]])

      res = query_scars(
          [corrupt_db, schemaless_db, fs["bundled_db"]],
          query="Survivor",
          limit=5,
      )
      self.assertEqual(len(res), 1)
      self.assertEqual(res[0]["id"], "SCAR-ARCH-0009")

  def test_mcp_workflow_query_scars_and_get_config_multi_tier_sqlite(self):
    """Verifies MCP workflow_query_scars discovers ordered SQLite tiers and omits searchedDirectories."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])
      user_db = registry.get_profile("gemini").scars_db_path
      local_custom_db = os.path.join(fs["shared"], "local_custom.db")
      env_custom_db = os.path.join(fs["shared"], "env_custom.db")

      _seed_db(fs["project_db"], [_scar_dict("SCAR-LOCAL-TEST-0001", title="Nebula Local Scar", category="TEST", source="local")])
      _seed_db(user_db, [_scar_dict("SCAR-USER-API-0001", title="Nebula User Scar", category="API", source="user")])
      _seed_db(local_custom_db, [_scar_dict("SCAR-CUSTOM-SEC-0001", title="Nebula Local Custom Scar", category="SEC", source="custom")])
      _seed_db(env_custom_db, [_scar_dict("SCAR-CUSTOM-DATA-0001", title="Nebula Env Custom Scar", category="DATA", source="custom")])
      _seed_db(fs["bundled_db"], [_scar_dict("SCAR-ARCH-0117", title="Nebula Bundled Scar", category="ARCH", source="makeflow")])

      local_cfg_path = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        ".workflow/scars/scars_registry.db",
                        "{agent_data}/scars_registry.db",
                        local_custom_db,
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      env = _isolated_env(
          fs["home"], {"MAKEFILE_WORKFLOW_SCAR_DBS": env_custom_db}
      )
      with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
          mcp_server, "_REPO_ROOT", fs["plugin_root"]
      ):
        tools_resp = mcp_server.handle_tools_list(1)
        query_tool = next(
            t for t in tools_resp["result"]["tools"] if t["name"] == "workflow_query_scars"
        )
        self.assertIn("SQLite", query_tool["description"])
        self.assertNotIn("Markdown", query_tool["description"])

        cfg_resp = mcp_server.handle_tools_call(
            2, "workflow_get_config", {"workspaceDir": fs["workspace"]}
        )
        cfg_payload = json.loads(cfg_resp["result"]["content"][0]["text"])
        self.assertEqual(
            cfg_payload["scars"]["databases"],
            [
                ".workflow/scars/scars_registry.db",
                "{agent_data}/scars_registry.db",
                local_custom_db,
                env_custom_db,
                "{plugin_root}/skills/makeflow/data/scars_registry.db",
            ],
        )

        q_resp = mcp_server.handle_tools_call(
            3,
            "workflow_query_scars",
            {
                "query": "Nebula",
                "workspaceDir": fs["workspace"],
                "limit": 10,
                "refreshLoaded": True,
            },
        )
        q_payload = json.loads(q_resp["result"]["content"][0]["text"])
        self.assertEqual(q_payload["status"], "OK")
        self.assertNotIn("searchedDirectories", q_payload)
        self.assertEqual(
            q_payload["searchedDatabases"],
            [
                fs["project_db"],
                user_db,
                local_custom_db,
                env_custom_db,
                fs["bundled_db"],
            ],
        )
        self.assertEqual(
            [s["id"] for s in q_payload["scars"]],
            [
                "SCAR-LOCAL-TEST-0001",
                "SCAR-USER-API-0001",
                "SCAR-CUSTOM-SEC-0001",
                "SCAR-CUSTOM-DATA-0001",
                "SCAR-ARCH-0117",
            ],
        )


def _fetch_scar_row_readonly(db_path, scar_id):
  """Read one scar row from a SQLite registry using read-only URI mode.

  Args:
    db_path: Absolute path to the SQLite scars database file.
    scar_id: Canonical scar identifier to look up.

  Returns:
    Dictionary mapping column names to stored values, or None when absent.
  """
  conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
  try:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM scars WHERE id = ?", (scar_id,))
    row = cur.fetchone()
    return dict(row) if row is not None else None
  finally:
    conn.close()


class _FakeHttpJsonResponse:
  """Minimal context-manager HTTP response double for mocked registry sync."""

  def __init__(self, payload, status=200, etag='"mock-sync-etag"'):
    """Initialize fake HTTP response with JSON payload, status, and ETag.

    Args:
      payload: JSON-serializable object returned by read().
      status: HTTP status code integer.
      etag: Optional ETag header value.
    """
    self._body = json.dumps(payload).encode("utf-8")
    self.status = status
    self.headers = {"ETag": etag} if etag else {}

  def read(self):
    """Return encoded JSON response bytes."""
    return self._body

  def __enter__(self):
    """Enter context manager returning this response double."""
    return self

  def __exit__(self, exc_type, exc, tb):
    """Exit context manager without suppressing exceptions."""
    return False


class TestDualTierShadowingRegistryScars(unittest.TestCase):
  """Behavioral tests for GH-323 dual-tier shadowing scars in SQLite registries."""

  PROJECT_LOCAL_DB = os.path.join(
      REPO_ROOT, ".workflow", "scars", "scars_registry.db"
  )
  BUNDLED_DB = os.path.join(
      REPO_ROOT, "skills", "makeflow", "data", "scars_registry.db"
  )

  def test_project_local_shadowing_scar_schema_validation_and_preservation(self):
    """Verifies SCAR-LOCAL-DATA-0002 schema, Simple English rules, and SCAR-LOCAL-DATA-0001 preservation."""
    self.assertTrue(os.path.isfile(self.PROJECT_LOCAL_DB))

    local_one = _fetch_scar_row_readonly(
        self.PROJECT_LOCAL_DB, "SCAR-LOCAL-DATA-0001"
    )
    self.assertIsNotNone(local_one)
    self.assertEqual(local_one["source"], "local")

    expected_title = (
        "Keep Legacy Original IDs Disjoint from Upstream Short IDs "
        "During Tiered Registry Shadowing"
    )
    expected_wound = (
        "Collapsing primary ID, upstream short ID, and legacy original ID "
        "into one shadowing set drops unrelated upstream scars during registry queries."
    )
    expected_trap = (
        "Developers assume short legacy tokens belong to one namespace and "
        "update shadowing sets mid-batch while iterating rows inside a single database."
    )
    expected_circuit_breaker = (
        "If a query reads multiple identifiers inside one database tier, "
        "stop mutating shadowing state until that entire database finishes."
    )
    expected_reflex = (
        "Keep upstream `ss_id` separate from legacy `original_id` across "
        "database tiers, and advance shadowing sets only after each "
        "higher-precedence database completes."
    )
    expected_probe = (
        "If two unrelated records share a legacy token in different columns, "
        "will one flat shadowing set drop valid rows? "
        "Do you advance shadowing state only between database tiers?"
    )

    row = _fetch_scar_row_readonly(
        self.PROJECT_LOCAL_DB, "SCAR-LOCAL-DATA-0002"
    )
    self.assertIsNotNone(
        row, "SCAR-LOCAL-DATA-0002 must exist in .workflow/scars/scars_registry.db"
    )
    self.assertEqual(row["title"], expected_title)
    self.assertEqual(row["wound"], expected_wound)
    self.assertEqual(row["trap"], expected_trap)
    self.assertEqual(row["circuit_breaker"], expected_circuit_breaker)
    self.assertEqual(row["reflex"], expected_reflex)
    self.assertEqual(row["socratic_probe"], expected_probe)
    self.assertEqual(row["source"], "local")
    self.assertEqual(row["visibility"], "local")
    self.assertEqual(row["graduation_status"], "local")
    self.assertIsNone(row["ss_id"])
    self.assertIsNone(row["original_id"])
    self.assertEqual(row["category"], "DATA")
    self.assertEqual(row["category_slug"], "data")
    self.assertEqual(json.loads(row["languages"]), ["universal"])
    self.assertEqual(json.loads(row["mf_targets"]), [15, 20, 30, 40])
    self.assertEqual(
        json.loads(row["workflow_phases"]),
        [
            "Target 15: archaeology",
            "Target 20: plan",
            "Target 30: tdd",
            "Target 40: review",
        ],
    )
    self.assertEqual(
        json.loads(row["ss_targets"]),
        ["Target 20: plan", "Target 30: tdd", "Target 40: review"],
    )
    self.assertEqual(
        row["raw_hash"],
        compute_raw_fields_hash(
            expected_wound,
            expected_trap,
            expected_circuit_breaker,
            expected_reflex,
        ),
    )

    validator = ScarCandidateValidator()
    self.assertEqual(validator.validate(row), [])
    self.assertEqual(
        validate_socratic_probe(row["socratic_probe"], "SCAR-LOCAL-DATA-0002"),
        [],
    )

    # Negative falsification checks confirm validators catch contract violations.
    mutated = dict(row)
    mutated["wound"] = f"{row['wound']}; forbidden semicolon."
    self.assertNotEqual(validator.validate(mutated), [])
    self.assertNotEqual(
        validate_socratic_probe("Not a question.", "SCAR-LOCAL-DATA-0002"), []
    )

  def test_project_local_shadowing_scar_fts5_and_target_queries(self):
    """Verifies SCAR-LOCAL-DATA-0002 is discoverable by ID, keywords, and Targets 15..40."""
    by_id = query_scars(
        self.PROJECT_LOCAL_DB, query="SCAR-LOCAL-DATA-0002", limit=5
    )
    self.assertEqual([r["id"] for r in by_id], ["SCAR-LOCAL-DATA-0002"])
    self.assertEqual(by_id[0]["languages"], ["universal"])
    self.assertEqual(by_id[0]["mf_targets"], [15, 20, 30, 40])

    for keyword in ("ss_id", "original_id", "shadowing"):
      matches = query_scars(self.PROJECT_LOCAL_DB, query=keyword, limit=5)
      self.assertIn(
          "SCAR-LOCAL-DATA-0002",
          [r["id"] for r in matches],
          f"Expected SCAR-LOCAL-DATA-0002 to match FTS5 query '{keyword}'",
      )

    for target in (15, 20, 30, 40):
      target_matches = query_scars(
          self.PROJECT_LOCAL_DB,
          category="DATA",
          language="universal",
          mf_target=target,
          limit=10,
      )
      self.assertIn(
          "SCAR-LOCAL-DATA-0002",
          [r["id"] for r in target_matches],
          f"Expected SCAR-LOCAL-DATA-0002 to match mf_target={target}",
      )

  def test_bundled_shadowing_scar_schema_generalization_and_upstream_preservation(self):
    """Verifies SCAR-DATA-0007 schema, generalization hygiene, and upstream DATA row preservation."""
    self.assertTrue(os.path.isfile(self.BUNDLED_DB))

    for upstream_id in (
        "SCAR-DATA-0002",
        "SCAR-DATA-0005",
        "SCAR-LANG-DART-DATA-0003",
        "SCAR-LANG-DART-DATA-0004",
        "SCAR-LANG-DART-DATA-0006",
    ):
      upstream_row = _fetch_scar_row_readonly(self.BUNDLED_DB, upstream_id)
      self.assertIsNotNone(
          upstream_row,
          f"Existing upstream DATA scar {upstream_id} must remain intact",
      )
      self.assertEqual(upstream_row["source"], "upstream")

    expected_title = (
        "Keep Identifier Namespaces Disjoint and Advance Shadowing Only Between Tiers"
    )
    expected_wound = (
        "Collapsing primary keys, external IDs, and legacy aliases into one "
        "shadowing set drops unrelated valid records during layered registry queries."
    )
    expected_trap = (
        "Developers treat distinct identifier columns as one shared namespace and "
        "update shadowing sets mid-batch while scanning rows inside one data tier."
    )
    expected_circuit_breaker = (
        "If a layered query inspects multiple key columns inside one tier, "
        "stop mutating shadowing sets until that tier finishes."
    )
    expected_reflex = (
        "Keep canonical primary keys, external IDs, and legacy aliases in disjoint "
        "shadowing sets. Advance shadowing sets only after each higher-precedence "
        "tier completes."
    )
    expected_probe = (
        "If two unrelated records share a token across different key columns, "
        "will one flat shadowing set drop valid rows? "
        "Do you advance shadowing sets only between data tiers?"
    )

    row = _fetch_scar_row_readonly(self.BUNDLED_DB, "SCAR-DATA-0007")
    self.assertIsNotNone(
        row, "SCAR-DATA-0007 must exist in skills/makeflow/data/scars_registry.db"
    )
    self.assertEqual(row["title"], expected_title)
    self.assertEqual(row["wound"], expected_wound)
    self.assertEqual(row["trap"], expected_trap)
    self.assertEqual(row["circuit_breaker"], expected_circuit_breaker)
    self.assertEqual(row["reflex"], expected_reflex)
    self.assertEqual(row["socratic_probe"], expected_probe)
    self.assertEqual(row["source"], "makeflow")
    self.assertEqual(row["visibility"], "public")
    self.assertEqual(row["graduation_status"], "graduated")
    self.assertIsNone(row["ss_id"])
    self.assertIsNone(row["original_id"])
    self.assertEqual(row["category"], "DATA")
    self.assertEqual(row["category_slug"], "data")
    self.assertEqual(json.loads(row["languages"]), ["universal"])
    self.assertEqual(json.loads(row["mf_targets"]), [15, 20, 30, 40])
    self.assertEqual(
        json.loads(row["workflow_phases"]),
        [
            "Target 15: archaeology",
            "Target 20: plan",
            "Target 30: tdd",
            "Target 40: review",
        ],
    )
    self.assertEqual(
        json.loads(row["ss_targets"]),
        ["Target 20: plan", "Target 30: tdd", "Target 40: review"],
    )
    self.assertEqual(
        row["raw_hash"],
        compute_raw_fields_hash(
            expected_wound,
            expected_trap,
            expected_circuit_breaker,
            expected_reflex,
        ),
    )

    validator = ScarCandidateValidator()
    self.assertEqual(validator.validate(row), [])
    self.assertEqual(
        validate_socratic_probe(row["socratic_probe"], "SCAR-DATA-0007"), []
    )

    # Zero repository-internal column-name leakage into Tier 1B bundled scar.
    inspected_text = " ".join(
        str(row.get(col) or "")
        for col in (
            "title",
            "wound",
            "trap",
            "circuit_breaker",
            "reflex",
            "socratic_probe",
            "keywords",
        )
    ).lower()
    self.assertNotIn("ss_id", inspected_text)
    self.assertNotIn("original_id", inspected_text)

    # Negative falsification check confirms validator catches em-dash leakage.
    mutated = dict(row)
    mutated["reflex"] = f"{row['reflex']} \u2014 forbidden em-dash."
    self.assertNotEqual(validator.validate(mutated), [])

  def test_bundled_shadowing_scar_fts5_and_target_queries(self):
    """Verifies SCAR-DATA-0007 is discoverable by ID, shadowing keyword, universal filter, and Targets 15..40."""
    by_id = query_scars(self.BUNDLED_DB, query="SCAR-DATA-0007", limit=5)
    self.assertEqual([r["id"] for r in by_id], ["SCAR-DATA-0007"])
    self.assertEqual(by_id[0]["source"], "makeflow")
    self.assertEqual(by_id[0]["languages"], ["universal"])
    self.assertEqual(by_id[0]["mf_targets"], [15, 20, 30, 40])

    by_keyword = query_scars(
        self.BUNDLED_DB,
        query="shadowing",
        category="DATA",
        language="universal",
        exclude_categories=["WORKFLOW"],
        limit=10,
    )
    self.assertIn("SCAR-DATA-0007", [r["id"] for r in by_keyword])

    for target in (15, 20, 30, 40):
      target_matches = query_scars(
          self.BUNDLED_DB,
          category="DATA",
          language="universal",
          exclude_categories=["WORKFLOW"],
          mf_target=target,
          limit=10,
      )
      self.assertIn(
          "SCAR-DATA-0007",
          [r["id"] for r in target_matches],
          f"Expected SCAR-DATA-0007 to match mf_target={target}",
      )

  def test_multi_tier_coexistence_returns_local_and_bundled_shadowing_scars(self):
    """Verifies Tier 2 SCAR-LOCAL-DATA-0002 and Tier 1B SCAR-DATA-0007 coexist without shadowing."""
    results = query_scars(
        [self.PROJECT_LOCAL_DB, self.BUNDLED_DB],
        query="shadowing",
        category="DATA",
        limit=10,
    )
    ids = [r["id"] for r in results]
    self.assertIn("SCAR-LOCAL-DATA-0002", ids)
    self.assertIn("SCAR-DATA-0007", ids)
    self.assertLess(
        ids.index("SCAR-LOCAL-DATA-0002"),
        ids.index("SCAR-DATA-0007"),
    )

  def test_upstream_sync_preserves_makeflow_bundled_shadowing_scar(self):
    """Verifies SyncSyntheticScarsScript preserves SCAR-DATA-0007 on an isolated DB copy."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      db_copy = os.path.join(tmp_dir, "scars_registry_copy.db")
      shutil.copyfile(self.BUNDLED_DB, db_copy)

      before_row = _fetch_scar_row_readonly(db_copy, "SCAR-DATA-0007")
      self.assertIsNotNone(
          before_row,
          "SCAR-DATA-0007 must exist in bundled DB before upstream sync check",
      )

      stdout_buf = io.StringIO()
      stderr_buf = io.StringIO()
      with mock.patch(
          "urllib.request.urlopen",
          return_value=_FakeHttpJsonResponse({"scars": []}),
      ):
        exit_code = SyncSyntheticScarsScript().execute(
            args=["--db-path", db_copy],
            stdout=stdout_buf,
            stderr=stderr_buf,
        )

      self.assertEqual(exit_code, 0, stderr_buf.getvalue())
      after_row = _fetch_scar_row_readonly(db_copy, "SCAR-DATA-0007")
      self.assertIsNotNone(after_row)
      self.assertEqual(after_row["source"], "makeflow")
      self.assertEqual(after_row["title"], before_row["title"])
      self.assertEqual(after_row["raw_hash"], before_row["raw_hash"])
      self.assertEqual(after_row["socratic_probe"], before_row["socratic_probe"])

      synced_query = query_scars(db_copy, query="SCAR-DATA-0007", limit=5)
      self.assertEqual([r["id"] for r in synced_query], ["SCAR-DATA-0007"])


if __name__ == "__main__":
  unittest.main()
