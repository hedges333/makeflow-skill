"""Behavioral tests for ordered SQLite multi-tier scar discovery and ID overrides (GH-304)."""

import inspect
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.agent_paths import AgentPathRegistry, PathContext
import lib.scars_db as scars_db_mod
from lib.scars_db import (
    next_tier_scar_id,
    query_scars,
    resolve_scars_db_path,
    resolve_scars_db_paths,
    upsert_scar,
)
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

  def test_scars_db_resolvers_support_path_context_package_root_and_validation(self):
    """Verifies resolve_scars_db_path/paths accept keyword-only PathContext, reject conflicts, and keep storage pure (GH-319)."""
    for fn in (resolve_scars_db_path, resolve_scars_db_paths):
      with self.subTest(fn=fn.__name__):
        sig = inspect.signature(fn)
        self.assertIn("context", sig.parameters)
        self.assertEqual(
            sig.parameters["context"].kind, inspect.Parameter.KEYWORD_ONLY
        )
        self.assertIsNone(sig.parameters["context"].default)

    # Low-level storage helpers stay free of home_dir, agent, and context (SCAR-API-0006)
    for fn in (upsert_scar, next_tier_scar_id, query_scars):
      with self.subTest(low_level_fn=fn.__name__):
        params = inspect.signature(fn).parameters
        for forbidden in ("home_dir", "agent", "context"):
          self.assertNotIn(forbidden, params)

    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])
      user_db = registry.get_profile("codex").scars_db_path
      _seed_db(fs["project_db"], [_scar_dict("SCAR-LOCAL-ARCH-0001", source="local")])
      _seed_db(user_db, [_scar_dict("SCAR-USER-ARCH-0001", source="user")])
      _seed_db(fs["bundled_db"], [_scar_dict("SCAR-ARCH-0001", source="makeflow")])

      ctx = PathContext(
          workspace_dir=fs["workspace"],
          agent="codex",
          home_dir=fs["home"],
          plugin_root=fs["plugin_root"],
      )
      expected_codex_db = os.path.join(
          fs["home"], ".codex", "plugin_data", "makeflow", "scars_registry.db"
      )

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        self.assertEqual(
            resolve_scars_db_path(context=ctx),
            fs["bundled_db"],
        )
        # Missing bundled DB falls back to project DB then agent DB via PathContext
        empty_plugin = os.path.join(tmp_root, "empty_plugin")
        os.makedirs(empty_plugin, exist_ok=True)
        agent_only_ctx = PathContext(
            workspace_dir=None,
            agent="codex",
            home_dir=fs["home"],
            plugin_root=empty_plugin,
        )
        self.assertEqual(
            resolve_scars_db_path(context=agent_only_ctx),
            expected_codex_db,
        )
        # Invalid agent inside PathContext still falls back cleanly without swallowing validation errors
        bad_agent_ctx = PathContext(
            workspace_dir=fs["workspace"],
            agent="unknown-agent",
            home_dir=fs["home"],
            plugin_root=empty_plugin,
        )
        self.assertEqual(
            resolve_scars_db_path(context=bad_agent_ctx),
            fs["project_db"],
        )

        # resolve_scars_db_paths parity between PathContext and legacy package_root kwargs
        via_ctx = resolve_scars_db_paths(context=ctx)
        via_legacy = resolve_scars_db_paths(
            agent="codex",
            workspace_dir=fs["workspace"],
            package_root=fs["plugin_root"],
            home_dir=fs["home"],
        )
        self.assertEqual(via_ctx, [fs["project_db"], expected_codex_db, fs["bundled_db"]])
        self.assertEqual(via_ctx, via_legacy)

        # Explicit db_path isolation still expands ~ using ctx.home_dir
        self.assertEqual(
            resolve_scars_db_paths(
                db_path="~/.codex/plugin_data/makeflow/scars_registry.db",
                context=ctx,
            ),
            [expected_codex_db],
        )

      # TypeError on non-PathContext and ValueError on mixing context with package_root or legacy kwargs
      for fn in (resolve_scars_db_path, resolve_scars_db_paths):
        with self.subTest(validation_fn=fn.__name__):
          with self.assertRaises(TypeError):
            fn(context={"home_dir": fs["home"]})  # type: ignore[arg-type]
          with self.assertRaises(ValueError) as pkg_exc:
            fn(package_root=fs["plugin_root"], context=ctx)
          self.assertIn("package_root", str(pkg_exc.exception))
          with self.assertRaises(ValueError) as ws_exc:
            fn(workspace_dir=fs["workspace"], context=ctx)
          self.assertIn("workspace_dir", str(ws_exc.exception))


if __name__ == "__main__":
  unittest.main()
