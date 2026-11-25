"""Behavioral tests for 4-tier synthetic scar discovery across lib/scars_db.py and MCP (GH-117)."""

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

from lib.agent_paths import AgentPathRegistry
from lib.scar_miner import MarkdownScarStore, MinedScarRecord
from lib.scars_db import (
    _infer_markdown_dir_tier,
    enrich_scars_database,
    init_scars_db_schema,
    query_scars,
    resolve_scar_markdown_dirs,
    resolve_scars_db_path,
    resolve_scars_db_paths,
)
from mcp import server as mcp_server


def _create_temp_sqlite_registry(db_path, rows):
  """Create an isolated enriched SQLite scars database inside a temporary directory."""
  conn = sqlite3.connect(db_path)
  try:
    init_scars_db_schema(conn)
    for row in rows:
      conn.execute(
          """
          INSERT INTO scars (
              id, title, category, category_slug, visibility, workflow_phases,
              applies_when, graduation_status, raw_wound, raw_trap,
              raw_circuit_breaker, raw_reflex, wound, trap, circuit_breaker,
              reflex, socratic_probe, source
          ) VALUES (?, ?, ?, ?, 'public', ?, ?, 'graduated', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          """,
          (
              row["id"],
              row["title"],
              row["category"],
              row["category"].lower(),
              json.dumps(row.get("workflow_phases", ["Target 30: tdd"])),
              row.get("applies_when", ""),
              row["wound"],
              row["trap"],
              row.get("circuit_breaker", ""),
              row["reflex"],
              row["wound"],
              row["trap"],
              row.get("circuit_breaker", ""),
              row["reflex"],
              row["socratic_probe"],
              row.get("source", "makeflow"),
          ),
      )
    conn.commit()
  finally:
    conn.close()
  enrich_scars_database(db_path)


def _isolated_env(home_dir, extra=None):
  """Build a minimal isolated environment pointing HOME to a temporary directory."""
  env = {
      "HOME": home_dir,
      "PATH": os.environ.get("PATH", ""),
  }
  if extra:
    env.update(extra)
  return env


class TestMultiTierScarDiscovery(unittest.TestCase):
  """Tests 4-tier discovery across project, user-wide agent, custom dirs, and bundled SQLite DB."""

  def test_resolve_scars_db_path_supports_codex_via_agent_paths(self):
    """Verifies lib/scars_db.py delegates all six agents including codex to lib/agent_paths.py."""
    with tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as empty_pkg:
      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
        resolved = resolve_scars_db_path(agent="codex", package_root=empty_pkg)
      expected = os.path.join(
          fake_home, ".codex", "plugin_data", "makeflow", "scars_registry.db"
      )
      self.assertEqual(resolved, expected)

  def test_four_tier_discovery_and_precedence_in_lib_scars_db(self):
    """Verifies project -> user-wide -> custom -> bundled precedence and FTS5 filtering."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as pkg_root:
      store = MarkdownScarStore()
      registry = AgentPathRegistry(home_dir=fake_home)

      # Tier 1 (highest precedence): Project-local ./.workflow/scars/
      proj_scars_dir = os.path.join(ws_dir, ".workflow", "scars")
      store.save_scar(
          proj_scars_dir,
          MinedScarRecord(
              title="Project Python Quantum Cache Guard",
              category="STORAGE",
              wound="Python cache writes raced during local builds.",
              trap="Workers shared one cache file without locking.",
              circuit_breaker="If lock file exists, stop immediately.",
              reflex="Use atomic rename when updating Python cache files.",
              socratic_probe="Did you write cache files atomically in Python?",
          ),
          tier="project",
      )

      # Tier 2: User-wide agent plugin_data/makeflow/scars/
      user_scars_dir = registry.get_profile("claude").scars_dir
      store.save_scar(
          user_scars_dir,
          MinedScarRecord(
              title="User Quantum Retry Guard",
              category="NET",
              wound="HTTP retries hammered downstream APIs without backoff.",
              trap="The loop retried immediately on timeout.",
              reflex="Add exponential backoff with jitter to Python HTTP retries.",
              socratic_probe="Did you add exponential backoff before retrying requests?",
          ),
          tier="user",
      )

      # Tier 3: Custom configured directory (Markdown + optional SQLite DB)
      custom_scars_dir = os.path.join(fake_home, "org_shared_scars")
      store.save_scar(
          custom_scars_dir,
          MinedScarRecord(
              title="Custom Quantum Schema Guard",
              category="SCHEMA",
              wound="SQLite migrations dropped columns without backup.",
              trap="Scripts ran destructive ALTER TABLE statements directly.",
              reflex="Verify SQLite schema migrations on a temporary copy first.",
              socratic_probe="Did you test the SQLite migration on a temporary copy?",
          ),
          tier="custom",
      )
      custom_db = os.path.join(custom_scars_dir, "scars_registry.db")
      _create_temp_sqlite_registry(
          custom_db,
          [
              {
                  "id": "SCAR-CUSTOM-DB-0099",
                  "title": "Custom SQLite Quantum Index Guard",
                  "category": "DB",
                  "wound": "Missing indexes slowed SQLite queries.",
                  "trap": "Queries scanned full tables in production.",
                  "reflex": "Add covering indexes for frequent SQLite predicates.",
                  "socratic_probe": "Does this SQLite query use an index?",
                  "source": "custom",
              }
          ],
      )

      # Tier 4: Bundled SQLite registry under package_root
      bundled_db = os.path.join(
          pkg_root, "skills", "makeflow", "data", "scars_registry.db"
      )
      os.makedirs(os.path.dirname(bundled_db), exist_ok=True)
      _create_temp_sqlite_registry(
          bundled_db,
          [
              {
                  "id": "SCAR-ARCH-0001",
                  "title": "Bundled Quantum Architecture Guard",
                  "category": "ARCH",
                  "wound": "Bundled architecture failure wound.",
                  "trap": "Bundled architecture trap.",
                  "reflex": "Keep core modules decoupled from entrypoints.",
                  "socratic_probe": "Are core modules decoupled from entrypoints?",
                  "source": "makeflow",
              },
              {
                  "id": "SCAR-PROC-0001",
                  "title": "Bundled Quantum Workflow Guard",
                  "category": "PROC",
                  "wound": "Workflow gate skipped.",
                  "trap": "Agent bypassed pause gate.",
                  "reflex": "Stop at every human pause gate.",
                  "socratic_probe": "Did you pause for human approval?",
                  "source": "makeflow",
              },
          ],
      )

      with mock.patch.dict(
          os.environ, _isolated_env(fake_home, {"MAKEFLOW_AGENT": "claude"}), clear=True
      ):
        md_dirs = resolve_scar_markdown_dirs(
            workspace_dir=ws_dir,
            custom_dirs=[custom_scars_dir],
            home_dir=fake_home,
        )
        db_paths = resolve_scars_db_paths(
            workspace_dir=ws_dir,
            package_root=pkg_root,
            custom_dirs=[custom_scars_dir],
        )

      self.assertEqual(md_dirs, [proj_scars_dir, user_scars_dir, custom_scars_dir])
      self.assertEqual(db_paths, [custom_db, bundled_db])

      # Query across all 4 tiers excluding WORKFLOW categories
      results = query_scars(
          db_path=db_paths,
          markdown_dirs=md_dirs,
          query="Quantum",
          exclude_categories=["WORKFLOW"],
          limit=10,
      )
      ids = [r["id"] for r in results]
      self.assertEqual(
          ids,
          [
              "SCAR-LOCAL-STORAGE-0001",
              "SCAR-USER-NET-0001",
              "SCAR-CUSTOM-SCHEMA-0001",
              "SCAR-CUSTOM-DB-0099",
              "SCAR-ARCH-0001",
          ],
      )
      self.assertNotIn("SCAR-PROC-0001", ids)

      # Verify language + framework + category filters work on Markdown scars via FTS5
      py_results = query_scars(
          db_path=[],
          markdown_dirs=md_dirs,
          language="python",
          category="STORAGE",
          limit=5,
      )
      self.assertEqual(len(py_results), 1)
      self.assertEqual(py_results[0]["id"], "SCAR-LOCAL-STORAGE-0001")
      self.assertEqual(
          py_results[0]["circuitBreaker"],
          "If lock file exists, stop immediately.",
      )

  def test_mcp_workflow_query_scars_and_get_config_multi_tier(self):
    """Verifies MCP workflow_query_scars discovers all 4 tiers via cascaded config and returns searchedDirectories."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as pkg_root:
      store = MarkdownScarStore()
      registry = AgentPathRegistry(home_dir=fake_home)

      # Create project-local scar, user-wide scar, custom scar (configured in global + local configs), and bundled DB
      proj_scars_dir = os.path.join(ws_dir, ".workflow", "scars")
      store.save_scar(
          proj_scars_dir,
          MinedScarRecord(
              title="Nebula Local Scar",
              category="TEST",
              wound="Local nebula test leaked temp files.",
              trap="Test wrote to a fixed directory.",
              reflex="Use TemporaryDirectory for every nebula test.",
              socratic_probe="Does every nebula test use TemporaryDirectory?",
          ),
          tier="project",
      )

      user_scars_dir = registry.get_profile("gemini").scars_dir
      store.save_scar(
          user_scars_dir,
          MinedScarRecord(
              title="Nebula User Scar",
              category="API",
              wound="User nebula wrapper added boolean flags.",
              trap="Added mode flag instead of subcommand.",
              reflex="Use explicit subcommands instead of boolean flags.",
              socratic_probe="Can you replace boolean mode flags with subcommands?",
          ),
          tier="user",
      )

      custom_global_dir = os.path.join(fake_home, "global_nebula_scars")
      store.save_scar(
          custom_global_dir,
          MinedScarRecord(
              title="Nebula Global Custom Scar",
              category="DATA",
              wound="Global custom nebula serializer dropped keys.",
              trap="Serializer overwrote unknown JSON fields.",
              reflex="Preserve unrelated JSON fields when saving config.",
              socratic_probe="Does config persistence preserve unrelated JSON keys?",
          ),
          tier="custom",
      )

      custom_local_dir = os.path.join(ws_dir, "team_nebula_scars")
      store.save_scar(
          custom_local_dir,
          MinedScarRecord(
              title="Nebula Local Custom Scar",
              category="SEC",
              wound="Local custom nebula path allowed traversal.",
              trap="Joined raw category string directly into path.",
              reflex="Reject path separators in category names before writing.",
              socratic_probe="Did you reject path traversal tokens before writing?",
          ),
          tier="custom",
      )

      # Configure user-global and project-local configs
      global_cfg_path = registry.get_profile("gemini").global_config_path
      os.makedirs(os.path.dirname(global_cfg_path), exist_ok=True)
      with open(global_cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"scars": {"directories": [custom_global_dir]}}, fh)

      local_cfg_path = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"scars": {"directories": [custom_local_dir]}}, fh)

      bundled_db = os.path.join(
          pkg_root, "skills", "makeflow", "data", "scars_registry.db"
      )
      os.makedirs(os.path.dirname(bundled_db), exist_ok=True)
      _create_temp_sqlite_registry(
          bundled_db,
          [
              {
                  "id": "SCAR-ARCH-0117",
                  "title": "Nebula Bundled Tier1 Scar",
                  "category": "ARCH",
                  "wound": "Bundled nebula tier1 wound.",
                  "trap": "Bundled nebula tier1 trap.",
                  "reflex": "Keep nebula modules cohesive.",
                  "socratic_probe": "Is this nebula module cohesive?",
                  "source": "makeflow",
              }
          ],
      )

      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True), mock.patch.object(
          mcp_server, "_REPO_ROOT", pkg_root
      ):
        # 1. workflow_get_config returns unioned scars.directories and configSources
        cfg_resp = mcp_server.handle_tools_call(
            1, "workflow_get_config", {"workspaceDir": ws_dir}
        )
        cfg_payload = json.loads(cfg_resp["result"]["content"][0]["text"])
        self.assertEqual(
            cfg_payload["scars"]["directories"],
            [custom_local_dir, custom_global_dir],
        )
        self.assertEqual(
            cfg_payload["configSources"],
            [global_cfg_path, local_cfg_path],
        )

        # 2. workflow_query_scars finds all 5 Nebula scars across all 4 tiers in precedence order
        q_resp = mcp_server.handle_tools_call(
            2,
            "workflow_query_scars",
            {
                "query": "Nebula",
                "workspaceDir": ws_dir,
                "limit": 10,
                "refreshLoaded": True,
            },
        )
        q_payload = json.loads(q_resp["result"]["content"][0]["text"])
        self.assertEqual(q_payload["status"], "OK")
        self.assertEqual(
            q_payload["searchedDirectories"],
            [proj_scars_dir, user_scars_dir, custom_local_dir, custom_global_dir],
        )
        self.assertEqual(q_payload["searchedDatabases"], [bundled_db])
        returned_ids = [s["id"] for s in q_payload["scars"]]
        self.assertEqual(
            returned_ids,
            [
                "SCAR-LOCAL-TEST-0001",
                "SCAR-USER-API-0001",
                "SCAR-CUSTOM-SEC-0001",
                "SCAR-CUSTOM-DATA-0001",
                "SCAR-ARCH-0117",
            ],
        )

  def test_infer_markdown_dir_tier_uses_exact_path_component_boundaries(self):
    """Verifies path-component tier classification avoids substring false positives (NBO #4)."""
    self.assertEqual(_infer_markdown_dir_tier("/ws/project/.workflow/scars"), "local")
    self.assertEqual(_infer_markdown_dir_tier("/ws/project/my.workflow/scars"), "custom")
    self.assertEqual(
        _infer_markdown_dir_tier("/home/u/.claude/plugin_data/makeflow/scars"),
        "user",
    )
    self.assertEqual(
        _infer_markdown_dir_tier("/home/u/.claude/plugin_data/makeflow/scars/extra"),
        "custom",
    )
    self.assertEqual(_infer_markdown_dir_tier("/home/u/team_scars"), "custom")

  def test_markdown_only_mcp_query_empty_vs_ok_and_schema_metadata(self):
    """Verifies Markdown-only MCP queries distinguish EMPTY vs OK and expose multi-tier schema docs (NBO #5)."""
    # Verify MCP tool schema reflects multi-tier Markdown directories and local/user/custom sources
    tools_resp = mcp_server.handle_tools_list(10)
    tools = tools_resp["result"]["tools"]
    query_tool = next(t for t in tools if t["name"] == "workflow_query_scars")
    self.assertIn("Markdown", query_tool["description"])
    source_desc = query_tool["inputSchema"]["properties"]["source"]["description"]
    for tier_token in ("'local'", "'user'", "'custom'"):
      self.assertIn(tier_token, source_desc)

    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as empty_pkg:
      proj_scars_dir = os.path.join(ws_dir, ".workflow", "scars")
      os.makedirs(proj_scars_dir, exist_ok=True)

      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True), mock.patch.object(
          mcp_server, "_REPO_ROOT", empty_pkg
      ):
        # 0 SQLite DBs and 0 Markdown scars -> status == EMPTY
        empty_resp = mcp_server.handle_tools_call(
            11,
            "workflow_query_scars",
            {"query": "Anything", "workspaceDir": ws_dir, "refreshLoaded": True},
        )
        empty_payload = json.loads(empty_resp["result"]["content"][0]["text"])
        self.assertEqual(empty_payload["status"], "EMPTY")

        # Add one Markdown scar (still 0 SQLite DBs)
        MarkdownScarStore().save_scar(
            proj_scars_dir,
            MinedScarRecord(
                title="Markdown Only Pulsar Guard",
                category="TEST",
                wound="Pulsar test wrote outside tempdir.",
                trap="Ignored temporary directory isolation.",
                reflex="Use TemporaryDirectory in every pulsar test.",
                socratic_probe="Did you isolate every pulsar test?",
            ),
            tier="project",
        )

        # Non-matching query returns status OK (not EMPTY) because count_markdown_dir_scars > 0
        miss_resp = mcp_server.handle_tools_call(
            12,
            "workflow_query_scars",
            {"query": "UnmatchedTokenXYZ", "workspaceDir": ws_dir, "refreshLoaded": True},
        )
        miss_payload = json.loads(miss_resp["result"]["content"][0]["text"])
        self.assertEqual(miss_payload["status"], "OK")
        self.assertEqual(miss_payload["totalResults"], 0)
        self.assertEqual(miss_payload["searchedDatabases"], [])
        self.assertEqual(miss_payload["searchedDirectories"], [proj_scars_dir])

        # Matching query returns the Markdown scar with searchedDatabases == []
        hit_resp = mcp_server.handle_tools_call(
            13,
            "workflow_query_scars",
            {"query": "Pulsar", "workspaceDir": ws_dir, "refreshLoaded": True},
        )
        hit_payload = json.loads(hit_resp["result"]["content"][0]["text"])
        self.assertEqual(hit_payload["status"], "OK")
        self.assertEqual(hit_payload["totalResults"], 1)
        self.assertEqual(hit_payload["scars"][0]["id"], "SCAR-LOCAL-TEST-0001")
        self.assertEqual(hit_payload["searchedDatabases"], [])
        self.assertEqual(hit_payload["searchedDirectories"], [proj_scars_dir])

  def test_relative_custom_dir_resolution_in_markdown_and_db_resolvers(self):
    """Verifies relative custom scar directory paths resolve against workspace_dir."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home, tempfile.TemporaryDirectory() as empty_pkg:
      rel_dir = os.path.join("shared", "custom_scars")
      abs_custom_dir = os.path.join(ws_dir, rel_dir)
      os.makedirs(abs_custom_dir, exist_ok=True)
      custom_db = os.path.join(abs_custom_dir, "scars_registry.db")
      _create_temp_sqlite_registry(
          custom_db,
          [
              {
                  "id": "SCAR-CUSTOM-ARCH-0007",
                  "title": "Relative Path Custom Guard",
                  "category": "ARCH",
                  "wound": "Relative custom directory was ignored.",
                  "trap": "Resolved relative paths against CWD.",
                  "reflex": "Resolve relative custom directories against workspace_dir.",
                  "socratic_probe": "Did you resolve relative directories against workspace_dir?",
                  "source": "custom",
              }
          ],
      )

      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
        md_dirs = resolve_scar_markdown_dirs(
            workspace_dir=ws_dir,
            custom_dirs=[rel_dir],
            home_dir=fake_home,
        )
        db_paths = resolve_scars_db_paths(
            workspace_dir=ws_dir,
            package_root=empty_pkg,
            custom_dirs=[rel_dir],
        )

      self.assertEqual(md_dirs, [abs_custom_dir])
      self.assertEqual(db_paths, [custom_db])

  def test_cross_tier_duplicate_scar_id_precedence_deduplication(self):
    """Verifies higher-precedence project-local scar overrides duplicate scar IDs in user and bundled tiers."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      proj_dir = os.path.join(ws_dir, ".workflow", "scars")
      user_dir = AgentPathRegistry(home_dir=fake_home).get_profile("gemini").scars_dir
      os.makedirs(proj_dir, exist_ok=True)
      os.makedirs(user_dir, exist_ok=True)

      dup_id = "SCAR-ARCH-0999"
      with open(os.path.join(proj_dir, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"# Synthetic Scars: Architecture\n\n"
            f"### {dup_id}: Project Wins Precedence\n\n"
            f"- **Category**: `ARCH`\n"
            f"- **Wound**: Project duplicate wound.\n"
            f"- **Trap**: Project duplicate trap.\n"
            f"- **Permanent Reflex**: Project duplicate reflex.\n"
            f"- **Socratic Probe**: Does project tier win precedence?\n"
        )
      with open(os.path.join(user_dir, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write(
            f"# Synthetic Scars: Architecture\n\n"
            f"### {dup_id}: User Tier Shadowed\n\n"
            f"- **Category**: `ARCH`\n"
            f"- **Wound**: User duplicate wound.\n"
            f"- **Trap**: User duplicate trap.\n"
            f"- **Permanent Reflex**: User duplicate reflex.\n"
            f"- **Socratic Probe**: Is user tier shadowed?\n"
        )
      bundled_db = os.path.join(fake_home, "bundled.db")
      _create_temp_sqlite_registry(
          bundled_db,
          [
              {
                  "id": dup_id,
                  "title": "Bundled Tier Shadowed",
                  "category": "ARCH",
                  "wound": "Bundled duplicate wound.",
                  "trap": "Bundled duplicate trap.",
                  "reflex": "Bundled duplicate reflex.",
                  "socratic_probe": "Is bundled tier shadowed?",
                  "source": "makeflow",
              }
          ],
      )

      results = query_scars(
          db_path=[bundled_db],
          markdown_dirs=[proj_dir, user_dir],
          query=dup_id,
          limit=10,
      )
      self.assertEqual(len(results), 1)
      self.assertEqual(results[0]["id"], dup_id)
      self.assertEqual(results[0]["title"], "Project Wins Precedence")
      self.assertEqual(results[0]["source"], "local")


if __name__ == "__main__":
  unittest.main()
