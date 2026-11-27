"""Behavioral tests for cross-agent path registry, ScarLocations, and SQLite scar config cascade (GH-117, GH-304)."""

import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

import lib.config as config_mod
import lib.scar_locations as scar_locations_mod
from lib.agent_paths import (
    AGENT_ALIASES,
    SUPPORTED_AGENTS,
    AgentPathProfile,
    AgentPathRegistry,
    PathContext,
    normalize_agent_name,
    resolve_path_context,
)
from lib.config import (
    DEFAULT_SCAR_DATABASES,
    DEFAULT_WORKFLOW_CONFIG,
    SCARS_ENV_VAR,
    expand_scar_database_path,
    expand_user_path,
    get_workflow_config,
    insert_scar_databases_before_bundled,
    load_database_config,
    load_default_config,
    resolve_workflow_config,
)
from lib.reflex import resolve_core_scar_probe
from lib.scar_locations import (
    ScarLocations,
    get_configured_scar_databases,
    resolve_scar_locations,
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


def _build_mock_fs_root(tmp_root):
  """Create a hermetic virtual filesystem root inside tmp_root."""
  home_dir = os.path.join(tmp_root, "home")
  ws_dir = os.path.join(tmp_root, "workspace")
  plugin_root = os.path.join(tmp_root, "plugin_root")
  shared_dir = os.path.join(tmp_root, "shared_team")
  for directory in (home_dir, ws_dir, plugin_root, shared_dir):
    os.makedirs(directory, exist_ok=True)
  return {
      "home": home_dir,
      "workspace": ws_dir,
      "plugin_root": plugin_root,
      "shared": shared_dir,
  }


class TestAgentPathRegistry(unittest.TestCase):
  """Tests canonical cross-agent path profiles for all six supported agents."""

  def test_lib_agent_paths_and_scar_locations_have_no_shebang(self):
    """Verifies lib/agent_paths.py and lib/scar_locations.py obey SCAR-ARCH-05 with zero shebang header."""
    for rel in ("lib/agent_paths.py", "lib/scar_locations.py"):
      with self.subTest(rel=rel):
        with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as fh:
          first_line = fh.readline()
        self.assertFalse(first_line.startswith("#!"))

  def test_six_agents_profiles_frozen_and_markdown_dir_attrs_removed(self):
    """Verifies all six agents map to canonical SQLite/config paths with obsolete Markdown scars_dir removed."""
    with tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
      self.assertFalse(hasattr(registry, "scars_dirs"))
      self.assertEqual(
          SUPPORTED_AGENTS,
          ("gemini", "claude", "cursor", "copilot", "codex", "chatgpt"),
      )
      expected_plugin_dirs = {
          "gemini": os.path.join(
              fake_home, ".gemini", "antigravity-cli", "plugin_data", "makeflow"
          ),
          "claude": os.path.join(
              fake_home, ".claude", "plugin_data", "makeflow"
          ),
          "cursor": os.path.join(
              fake_home, ".cursor", "plugin_data", "makeflow"
          ),
          "copilot": os.path.join(
              fake_home, ".copilot", "plugin_data", "makeflow"
          ),
          "codex": os.path.join(
              fake_home, ".codex", "plugin_data", "makeflow"
          ),
          "chatgpt": os.path.join(
              fake_home, ".chatgpt", "plugin_data", "makeflow"
          ),
      }
      legacy_cfg = os.path.join(fake_home, ".gemini", "config", "makeflow.json")
      for agent_name, expected_dir in expected_plugin_dirs.items():
        with self.subTest(agent=agent_name):
          profile = registry.get_profile(agent_name)
          self.assertIsInstance(profile, AgentPathProfile)
          self.assertFalse(hasattr(profile, "scars_dir"))
          self.assertEqual(profile.name, agent_name)
          self.assertEqual(profile.plugin_data_dir, expected_dir)
          self.assertEqual(
              profile.global_config_path,
              os.path.join(expected_dir, "config.json"),
          )
          self.assertEqual(
              profile.scars_db_path,
              os.path.join(expected_dir, "scars_registry.db"),
          )
          self.assertIn(legacy_cfg, profile.legacy_config_paths)
          with self.assertRaises(AttributeError):
            profile.name = "mutated"  # type: ignore[misc]

      summary = registry.to_dict(agent="claude", workspace_dir="/tmp/ws")
      self.assertNotIn("userScarsDir", summary)
      self.assertNotIn("projectScarsDir", summary)
      self.assertIn("userScarsDbPath", summary)
      self.assertIn("projectScarsDbPath", summary)
      for agent_meta in summary["allAgents"].values():
        self.assertNotIn("scarsDir", agent_meta)
        self.assertIn("scarsDbPath", agent_meta)

  def test_agent_normalization_aliases_env_and_invalid_rejection(self):
    """Verifies alias normalization, MAKEFLOW_AGENT env resolution, and invalid input rejection."""
    self.assertEqual(normalize_agent_name(None), "gemini")
    self.assertEqual(normalize_agent_name(""), "gemini")
    self.assertEqual(normalize_agent_name("claude-code"), "claude")
    self.assertEqual(normalize_agent_name("agy"), "gemini")
    self.assertIn("claude-code", AGENT_ALIASES)
    with self.assertRaises(ValueError):
      normalize_agent_name("unknown-agent-xyz")

    with tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
      with mock.patch.dict(
          os.environ, _isolated_env(fake_home, {"MAKEFLOW_AGENT": "codex"}), clear=True
      ):
        self.assertEqual(registry.resolve_active_agent(), "codex")
        ordered = registry.all_profiles()
        self.assertEqual(ordered[0].name, "codex")
        self.assertEqual(len(ordered), 6)
        self.assertEqual(
            registry.scars_db_paths()[0],
            os.path.join(
                fake_home, ".codex", "plugin_data", "makeflow", "scars_registry.db"
            ),
        )
        self.assertEqual(registry.resolve_active_agent("cursor"), "cursor")
        with self.assertRaises(ValueError):
          registry.resolve_active_agent("not-a-real-agent")

      with mock.patch.dict(
          os.environ,
          _isolated_env(fake_home, {"MAKEFLOW_AGENT": "bogus_env_agent"}),
          clear=True,
      ):
        self.assertEqual(registry.resolve_active_agent(), "gemini")


class TestScarLocationsAndConfigCascade(unittest.TestCase):
  """Tests ScarLocations single source of truth and single-list scars.databases cascade (GH-304)."""

  def test_default_schema_parity_across_manifests_and_code(self):
    """Verifies default_config.json, config.json.example, and DEFAULT_WORKFLOW_CONFIG declare single scars.databases."""
    expected_databases = [
        ".workflow/scars/scars_registry.db",
        "{agent_data}/scars_registry.db",
        "{plugin_root}/skills/makeflow/data/scars_registry.db",
    ]
    expected_scars = {
        "databases": expected_databases,
    }
    self.assertEqual(SCARS_ENV_VAR, "MAKEFILE_WORKFLOW_SCAR_DBS")
    self.assertEqual(list(DEFAULT_SCAR_DATABASES), expected_databases)
    self.assertEqual(DEFAULT_WORKFLOW_CONFIG["scars"], expected_scars)

    loaded_default = load_default_config()
    self.assertEqual(loaded_default["scars"], expected_scars)

    for rel_path in (
        "default_config.json",
        os.path.join(".workflow", "config.json.example"),
    ):
      with open(os.path.join(REPO_ROOT, rel_path), "r", encoding="utf-8") as fh:
        data = json.load(fh)
      self.assertEqual(
          data["scars"],
          expected_scars,
          msg=f"Schema mismatch in {rel_path}",
      )
      self.assertNotIn("directories", data["scars"])
      self.assertNotIn("extraDatabases", data["scars"])

  def test_token_and_relative_path_expansion_across_agents(self):
    """Verifies public expand_user_path and expand_scar_database_path expand tokens strictly inside mock root."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])

      self.assertEqual(expand_user_path("~", home_dir=fs["home"]), fs["home"])
      self.assertEqual(
          expand_user_path("~/team.db", home_dir=fs["home"]),
          os.path.join(fs["home"], "team.db"),
      )

      for agent_alias, canonical in (
          ("antigravity", "gemini"),
          ("claude-code", "claude"),
          ("codex", "codex"),
          ("cursor", "cursor"),
          ("copilot", "copilot"),
      ):
        with self.subTest(agent=agent_alias):
          expected_agent_db = registry.get_profile(canonical).scars_db_path
          self.assertEqual(
              expand_scar_database_path(
                  "{agent_data}/scars_registry.db",
                  workspace_dir=fs["workspace"],
                  agent=agent_alias,
                  home_dir=fs["home"],
                  plugin_root=fs["plugin_root"],
              ),
              expected_agent_db,
          )

      self.assertEqual(
          expand_scar_database_path(
              ".workflow/scars/scars_registry.db",
              workspace_dir=fs["workspace"],
              home_dir=fs["home"],
              plugin_root=fs["plugin_root"],
          ),
          os.path.join(fs["workspace"], ".workflow", "scars", "scars_registry.db"),
      )
      self.assertEqual(
          expand_scar_database_path(
              "{workspace}/custom.db",
              workspace_dir=fs["workspace"],
              home_dir=fs["home"],
              plugin_root=fs["plugin_root"],
          ),
          os.path.join(fs["workspace"], "custom.db"),
      )
      self.assertEqual(
          expand_scar_database_path(
              "{plugin_root}/skills/makeflow/data/scars_registry.db",
              workspace_dir=fs["workspace"],
              home_dir=fs["home"],
              plugin_root=fs["plugin_root"],
          ),
          os.path.join(
              fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
          ),
      )

  def test_resolve_scar_locations_immutability_process_spokes_env_and_reflex_delegation(self):
    """Verifies ScarLocations fields, process_scar_files, env ordering, existing_databases, and lib/reflex delegation."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      spokes_dir = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "references", "scars"
      )
      os.makedirs(spokes_dir, exist_ok=True)
      spoke_a = os.path.join(spokes_dir, "alpha.md")
      spoke_b = os.path.join(spokes_dir, "beta.md")
      with open(spoke_a, "w", encoding="utf-8") as fh:
        fh.write(
            "### SCAR-CUSTOM-99: Synthetic Spoke Test\n"
            "- **Socratic Probe**: Did lib/reflex delegate to resolve_scar_locations?\n"
        )
      with open(spoke_b, "w", encoding="utf-8") as fh:
        fh.write("# Beta\n")
      with open(os.path.join(spokes_dir, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("ignored non-md file\n")

      proj_db = os.path.join(fs["workspace"], ".workflow", "scars", "scars_registry.db")
      os.makedirs(os.path.dirname(proj_db), exist_ok=True)
      with open(proj_db, "wb") as fh:
        fh.write(b"")

      env_single_db = os.path.join(fs["shared"], "single_override.db")
      env_extra_db = os.path.join(fs["shared"], "env_extra.db")
      with open(env_single_db, "wb") as fh:
        fh.write(b"")

      registry = AgentPathRegistry(home_dir=fs["home"])
      user_db = registry.get_profile("claude").scars_db_path
      bundled_db = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
      )

      env = _isolated_env(
          fs["home"],
          {
              "MAKEFLOW_AGENT": "claude",
              "MAKEFLOW_SCARS_DB": env_single_db,
              SCARS_ENV_VAR: env_extra_db,
          },
      )
      with mock.patch.dict(os.environ, env, clear=True):
        loc = resolve_scar_locations(
            workspace_dir=fs["workspace"],
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )

      self.assertIsInstance(loc, ScarLocations)
      with self.assertRaises(AttributeError):
        loc.process_scars_dir = "/mutated"  # type: ignore[misc]

      self.assertEqual(loc.process_scars_dir, spokes_dir)
      self.assertEqual(loc.process_scar_files, (spoke_a, spoke_b))
      self.assertEqual(loc.project_db, proj_db)
      self.assertEqual(loc.user_db, user_db)
      self.assertEqual(loc.bundled_db, bundled_db)
      # MAKEFLOW_SCARS_DB prepends first; MAKEFILE_WORKFLOW_SCAR_DBS inserts before bundled_db
      self.assertEqual(
          loc.databases,
          (env_single_db, proj_db, user_db, env_extra_db, bundled_db),
      )
      # Only files existing on disk appear in existing_databases
      self.assertEqual(loc.existing_databases, (env_single_db, proj_db))

      as_dict = loc.to_dict()
      self.assertEqual(as_dict["process_scars_dir"], spokes_dir)
      self.assertEqual(as_dict["process_scar_files"], [spoke_a, spoke_b])
      self.assertEqual(as_dict["configured_databases"], list(loc.databases))
      self.assertEqual(as_dict["existing_databases"], [env_single_db, proj_db])

      # lib/reflex.resolve_core_scar_probe delegates to resolve_scar_locations().process_scar_files
      probe = resolve_core_scar_probe("SCAR-CUSTOM-99", repo_root=fs["plugin_root"])
      self.assertEqual(probe, "Did lib/reflex delegate to resolve_scar_locations?")

  def test_explicit_scars_databases_override_reordering_and_bundled_omission(self):
    """Verifies scars.databases uses strict override semantics across user-global and project-local layers."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])
      user_cfg_path = registry.get_profile("claude").global_config_path
      os.makedirs(os.path.dirname(user_cfg_path), exist_ok=True)

      # User-global overrides default ordering (user before project, omitting bundled)
      with open(user_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        "{agent_data}/scars_registry.db",
                        ".workflow/scars/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        user_resolved = get_configured_scar_databases(
            workspace_dir=fs["workspace"],
            agent="claude",
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )

      expected_user_db = registry.get_profile("claude").scars_db_path
      expected_proj_db = os.path.join(
          fs["workspace"], ".workflow", "scars", "scars_registry.db"
      )
      expected_bundled_db = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
      )
      self.assertEqual(user_resolved, [expected_user_db, expected_proj_db])
      self.assertNotIn(expected_bundled_db, user_resolved)

      # Project-local overrides user-global ordering verbatim (custom before project, no bundled)
      custom_first_db = os.path.join(fs["shared"], "override_first.db")
      local_cfg_path = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        custom_first_db,
                        ".workflow/scars/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        proj_resolved = get_configured_scar_databases(
            workspace_dir=fs["workspace"],
            agent="claude",
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )

      self.assertEqual(proj_resolved, [custom_first_db, expected_proj_db])
      self.assertNotIn(expected_user_db, proj_resolved)
      self.assertNotIn(expected_bundled_db, proj_resolved)

      # MAKEFILE_WORKFLOW_SCAR_DBS appends at the end when bundled entry was omitted
      env_extra_db = os.path.join(fs["shared"], "env_appended.db")
      with mock.patch.dict(
          os.environ,
          _isolated_env(fs["home"], {SCARS_ENV_VAR: env_extra_db}),
          clear=True,
      ):
        appended = get_configured_scar_databases(
            workspace_dir=fs["workspace"],
            agent="claude",
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )
      self.assertEqual(appended, [custom_first_db, expected_proj_db, env_extra_db])

  def test_full_config_cascade_layers_and_env_scar_db_insertion(self):
    """Verifies default -> legacy -> user-global -> project-local -> env cascade with single scars.databases."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      registry = AgentPathRegistry(home_dir=fs["home"])

      proj_custom_db = os.path.join(fs["shared"], "project_custom.db")
      env_custom_db = os.path.join(fs["shared"], "env_custom.db")

      # 1. Legacy global config
      legacy_path = os.path.join(fs["home"], ".gemini", "config", "makeflow.json")
      os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
      with open(legacy_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {
                    "adversarial-critic": "flash",
                    "tdd-worker": "flash_lite",
                    "archaeology-worker": "pro",
                },
                "ci": {"quiescenceSeconds": 90, "pollIntervalSeconds": 15},
                "database": {
                    "prodDatabases": ["legacy_prod"],
                    "prodHosts": ["legacy.db.internal"],
                },
            },
            fh,
        )

      # 2. Active agent user-global config
      claude_cfg_path = registry.get_profile("claude").global_config_path
      os.makedirs(os.path.dirname(claude_cfg_path), exist_ok=True)
      with open(claude_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {
                    "adversarial-critic": "pro",
                    "triage-worker": "flash",
                },
                "ci": {"quiescenceSeconds": 60},
                "database": {
                    "prodDatabases": ["global_prod"],
                    "testDatabases": ["global_test"],
                },
            },
            fh,
        )

      # 3. Project-local config defines ordered scars.databases
      local_cfg_path = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"tdd-worker": "pro"},
                "ci": {"quiescenceSeconds": 0},
                "database": {
                    "testDatabases": ["local_test"],
                    "prodHosts": ["local.prod.host"],
                },
                "scars": {
                    "databases": [
                        ".workflow/scars/scars_registry.db",
                        "{agent_data}/scars_registry.db",
                        proj_custom_db,
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                },
            },
            fh,
        )

      env = _isolated_env(
          fs["home"],
          {
              "MAKEFLOW_AGENT": "claude",
              SCARS_ENV_VAR: f"{env_custom_db},{proj_custom_db}",
              "MAKEFILE_WORKFLOW_PROD_DBS": "env_prod",
          },
      )
      with mock.patch.dict(os.environ, env, clear=True):
        mcp_cfg = resolve_workflow_config(
            fs["workspace"], home_dir=fs["home"], plugin_root=fs["plugin_root"]
        )
        hook_cfg, hook_src = get_workflow_config(
            [fs["workspace"]], home_dir=fs["home"], plugin_root=fs["plugin_root"]
        )
        db_cfg = load_database_config(
            {"workspacePaths": [fs["workspace"]]}, home_dir=fs["home"]
        )
        ordered_dbs = get_configured_scar_databases(
            workspace_dir=fs["workspace"],
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )

      expected_raw_dbs = [
          ".workflow/scars/scars_registry.db",
          "{agent_data}/scars_registry.db",
          proj_custom_db,
          env_custom_db,
          "{plugin_root}/skills/makeflow/data/scars_registry.db",
      ]
      for cfg in (mcp_cfg, hook_cfg):
        self.assertEqual(cfg["models"]["archaeology-worker"], "pro")
        self.assertEqual(cfg["models"]["adversarial-critic"], "pro")
        self.assertEqual(cfg["models"]["triage-worker"], "flash")
        self.assertEqual(cfg["models"]["tdd-worker"], "pro")
        self.assertEqual(cfg["ci"]["pollIntervalSeconds"], 15)
        self.assertEqual(cfg["ci"]["quiescenceSeconds"], 0)
        self.assertEqual(cfg["scars"]["databases"], expected_raw_dbs)
        self.assertNotIn("extraDatabases", cfg["scars"])

      expected_proj_db = os.path.join(
          fs["workspace"], ".workflow", "scars", "scars_registry.db"
      )
      expected_user_db = registry.get_profile("claude").scars_db_path
      expected_bundled_db = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
      )
      self.assertEqual(
          ordered_dbs,
          [
              expected_proj_db,
              expected_user_db,
              proj_custom_db,
              env_custom_db,
              expected_bundled_db,
          ],
      )

      for db_map in (mcp_cfg["database"], hook_cfg["database"], db_cfg):
        self.assertEqual(
            db_map["prodDatabases"],
            ["legacy_prod", "global_prod", "env_prod"],
        )
        self.assertEqual(
            db_map["prodHosts"],
            ["legacy.db.internal", "local.prod.host"],
        )
        self.assertEqual(
            db_map["testDatabases"],
            ["global_test", "local_test"],
        )

      self.assertEqual(mcp_cfg["configSource"], local_cfg_path)
      self.assertEqual(
          mcp_cfg["configSources"],
          [legacy_path, claude_cfg_path, local_cfg_path],
      )
      self.assertIn(
          f"{SCARS_ENV_VAR}={env_custom_db},{proj_custom_db}",
          mcp_cfg["envOverrides"],
      )
      self.assertIsNotNone(hook_src)
      self.assertIn(SCARS_ENV_VAR, hook_src)

  def test_legacy_global_only_preserves_backward_compatible_source_labels(self):
    """Verifies legacy ~/.gemini/config/makeflow.json alone preserves GH-217 source labels."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      legacy_path = os.path.join(fs["home"], ".gemini", "config", "makeflow.json")
      os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
      with open(legacy_path, "w", encoding="utf-8") as fh:
        json.dump({"ci": {"quiescenceSeconds": 0}}, fh)

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        hook_cfg, hook_src = get_workflow_config(
            [fs["workspace"]], home_dir=fs["home"], plugin_root=fs["plugin_root"]
        )
        mcp_cfg = resolve_workflow_config(
            fs["workspace"], home_dir=fs["home"], plugin_root=fs["plugin_root"]
        )

      self.assertEqual(hook_src, "~/.gemini/config/makeflow.json")
      self.assertEqual(mcp_cfg["configSource"], legacy_path)
      self.assertEqual(mcp_cfg["configSources"], [legacy_path])
      self.assertEqual(hook_cfg["scars"]["databases"], list(DEFAULT_SCAR_DATABASES))
      self.assertEqual(mcp_cfg["scars"]["databases"], list(DEFAULT_SCAR_DATABASES))

  def test_corrupt_and_non_dict_json_recovery_and_workspace_fallback(self):
    """Verifies corrupt or non-dict JSON configs recover and fall back across workspace_paths."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      ws_corrupt = os.path.join(tmp_root, "ws_corrupt")
      ws_list = os.path.join(tmp_root, "ws_list")
      ws_valid = os.path.join(tmp_root, "ws_valid")
      ws_second_valid = os.path.join(tmp_root, "ws_second_valid")
      for d in (ws_corrupt, ws_list, ws_valid, ws_second_valid):
        os.makedirs(d, exist_ok=True)

      legacy_path = os.path.join(fs["home"], ".gemini", "config", "makeflow.json")
      os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
      with open(legacy_path, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")

      registry = AgentPathRegistry(home_dir=fs["home"])
      global_path = registry.get_profile("gemini").global_config_path
      os.makedirs(os.path.dirname(global_path), exist_ok=True)
      global_db = os.path.join(fs["shared"], "from_global.db")
      with open(global_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"adversarial-critic": "pro"},
                "scars": {"databases": [global_db]},
            },
            fh,
        )

      corrupt_local = os.path.join(ws_corrupt, ".workflow", "config.json")
      os.makedirs(os.path.dirname(corrupt_local), exist_ok=True)
      with open(corrupt_local, "w", encoding="utf-8") as fh:
        fh.write("{broken")

      list_local = os.path.join(ws_list, ".workflow", "config.json")
      os.makedirs(os.path.dirname(list_local), exist_ok=True)
      with open(list_local, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)

      fallback_db = os.path.join(fs["shared"], "from_fallback_ws.db")
      valid_local = os.path.join(ws_valid, ".workflow", "config.json")
      os.makedirs(os.path.dirname(valid_local), exist_ok=True)
      with open(valid_local, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"tdd-worker": "flash"},
                "database": {"testDatabases": ["fallback_test_db"]},
                "scars": {"databases": [fallback_db, global_db]},
            },
            fh,
        )

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        hook_cfg, hook_src = get_workflow_config(
            [ws_corrupt, ws_list, ws_valid],
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )
        db_cfg = load_database_config(
            {"workspacePaths": [ws_corrupt, ws_list, ws_valid]},
            home_dir=fs["home"],
        )

      self.assertEqual(hook_cfg["models"]["adversarial-critic"], "pro")
      self.assertEqual(hook_cfg["models"]["tdd-worker"], "flash")
      self.assertEqual(
          hook_cfg["scars"]["databases"],
          [fallback_db, global_db],
      )
      self.assertIn("fallback_test_db", db_cfg["testDatabases"])
      self.assertEqual(hook_src, f"{global_path} + {valid_local}")
      self.assertEqual(hook_cfg["configSources"], [global_path, valid_local])

      second_local = os.path.join(ws_second_valid, ".workflow", "config.json")
      os.makedirs(os.path.dirname(second_local), exist_ok=True)
      with open(second_local, "w", encoding="utf-8") as fh:
        json.dump({"models": {"tdd-worker": "pro"}}, fh)

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        first_wins_cfg, first_wins_src = get_workflow_config(
            [ws_valid, ws_second_valid],
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )
      self.assertEqual(first_wins_cfg["models"]["tdd-worker"], "flash")
      self.assertEqual(first_wins_src, f"{global_path} + {valid_local}")

  def test_path_deduplication_and_normalization_with_relative_tilde_and_symlinks(self):
    """Verifies duplicate relative, absolute, tilde, and symlinked paths collapse keeping highest precedence."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      target_db = os.path.join(fs["home"], "shared_scars.db")
      with open(target_db, "wb") as fh:
        fh.write(b"")
      symlink_db = os.path.join(fs["shared"], "linked_scars.db")
      os.symlink(target_db, symlink_db)

      rel_proj_db = ".workflow/scars/scars_registry.db"
      abs_proj_db = os.path.join(fs["workspace"], ".workflow", "scars", "scars_registry.db")
      os.makedirs(os.path.dirname(abs_proj_db), exist_ok=True)
      with open(abs_proj_db, "wb") as fh:
        fh.write(b"")

      unique_env_db = os.path.join(fs["shared"], "unique_env.db")
      local_cfg = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg), exist_ok=True)
      with open(local_cfg, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        rel_proj_db,
                        "~/shared_scars.db",
                        abs_proj_db,
                        symlink_db,
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      with mock.patch.dict(
          os.environ,
          _isolated_env(
              fs["home"],
              {SCARS_ENV_VAR: f"{target_db},{symlink_db},{unique_env_db}"},
          ),
          clear=True,
      ):
        ordered = get_configured_scar_databases(
            workspace_dir=fs["workspace"],
            home_dir=fs["home"],
            plugin_root=fs["plugin_root"],
        )

      expected_bundled = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
      )
      self.assertEqual(
          ordered,
          [
              abs_proj_db,
              target_db,
              unique_env_db,
              expected_bundled,
          ],
      )

  def test_resolve_scar_locations_workspace_none_isolates_from_ambient_cwd_config(self):
    """Verifies workspace_dir=None ignores ambient CWD .workflow/config.json while explicit workspace loads it."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      ambient_custom_db = os.path.join(fs["shared"], "ambient_cwd_custom.db")
      local_cfg_path = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        ".workflow/scars/scars_registry.db",
                        ambient_custom_db,
                        "{agent_data}/scars_registry.db",
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      prev_cwd = os.getcwd()
      try:
        os.chdir(fs["workspace"])
        with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
          isolated = resolve_scar_locations(
              workspace_dir=None,
              home_dir=fs["home"],
              plugin_root=fs["plugin_root"],
          )
          explicit = resolve_scar_locations(
              workspace_dir=fs["workspace"],
              home_dir=fs["home"],
              plugin_root=fs["plugin_root"],
          )
      finally:
        os.chdir(prev_cwd)

      self.assertIsNone(isolated.project_db)
      self.assertNotIn(ambient_custom_db, isolated.databases)
      self.assertEqual(
          explicit.project_db,
          os.path.join(fs["workspace"], ".workflow", "scars", "scars_registry.db"),
      )
      self.assertIn(ambient_custom_db, explicit.databases)


class TestPathContextBoundary(unittest.TestCase):
  """Behavioral tests for PathContext value object and resolve_path_context validator (GH-319)."""

  def test_path_context_defaults_immutability_hashability_and_zero_io(self):
    """Verifies PathContext is frozen, hashable, defaults to None, and performs zero construction I/O."""
    default_ctx = PathContext()
    self.assertIsNone(default_ctx.workspace_dir)
    self.assertIsNone(default_ctx.agent)
    self.assertIsNone(default_ctx.home_dir)
    self.assertIsNone(default_ctx.plugin_root)

    with mock.patch("os.path.expanduser", side_effect=AssertionError("unexpected I/O")), mock.patch(
        "os.path.abspath", side_effect=AssertionError("unexpected I/O")
    ):
      ctx = PathContext(
          workspace_dir="~/ws",
          agent="claude",
          home_dir="~/home",
          plugin_root="~/plugin",
      )

    self.assertEqual(ctx.workspace_dir, "~/ws")
    self.assertEqual(ctx.agent, "claude")
    self.assertEqual(ctx.home_dir, "~/home")
    self.assertEqual(ctx.plugin_root, "~/plugin")

    with self.assertRaises(AttributeError):
      ctx.agent = "gemini"  # type: ignore[misc]

    duplicate = PathContext(
        workspace_dir="~/ws",
        agent="claude",
        home_dir="~/home",
        plugin_root="~/plugin",
    )
    self.assertEqual(ctx, duplicate)
    self.assertEqual(hash(ctx), hash(duplicate))
    self.assertEqual({ctx: "ok"}[duplicate], "ok")

  def test_resolve_path_context_builds_from_legacy_kwargs_and_package_root(self):
    """Verifies resolve_path_context normalizes legacy kwargs, package_root alias, and empty workspace sentinel."""
    empty_ctx = resolve_path_context()
    self.assertEqual(empty_ctx, PathContext())

    from_legacy = resolve_path_context(
        workspace_dir="/repo",
        agent="codex",
        home_dir="/home/dev",
        plugin_root="/plugin",
    )
    self.assertEqual(
        from_legacy,
        PathContext(
            workspace_dir="/repo",
            agent="codex",
            home_dir="/home/dev",
            plugin_root="/plugin",
        ),
    )

    from_pkg = resolve_path_context(
        workspace_dir="",
        home_dir="/home/dev",
        package_root="/pkg_root",
    )
    self.assertEqual(from_pkg.workspace_dir, "")
    self.assertEqual(from_pkg.home_dir, "/home/dev")
    self.assertEqual(from_pkg.plugin_root, "/pkg_root")

    existing = PathContext(workspace_dir="", agent="claude")
    self.assertIs(resolve_path_context(existing), existing)

  def test_resolve_path_context_rejects_invalid_type_and_conflicting_arguments(self):
    """Verifies resolve_path_context raises TypeError on non-PathContext and ValueError on mixed args."""
    with self.assertRaises(TypeError):
      resolve_path_context(context={"workspace_dir": "/repo"})  # type: ignore[arg-type]

    with self.assertRaises(TypeError):
      resolve_path_context(context="/repo")  # type: ignore[arg-type]

    ctx = PathContext(home_dir="/home/dev")
    for kwargs, expected_name in (
        ({"workspace_dir": "/repo"}, "workspace_dir"),
        ({"workspace_dir": ""}, "workspace_dir"),
        ({"agent": "claude"}, "agent"),
        ({"home_dir": "/other"}, "home_dir"),
        ({"plugin_root": "/plugin"}, "plugin_root"),
        ({"package_root": "/pkg"}, "package_root"),
    ):
      with self.subTest(kwargs=kwargs):
        with self.assertRaises(ValueError) as exc_info:
          resolve_path_context(ctx, **kwargs)
        self.assertIn(expected_name, str(exc_info.exception))

    with self.assertRaises(ValueError) as pkg_conflict:
      resolve_path_context(plugin_root="/plugin", package_root="/pkg")
    self.assertIn("plugin_root", str(pkg_conflict.exception))
    self.assertIn("package_root", str(pkg_conflict.exception))

  def test_config_reexports_canonical_path_context_and_keeps_load_default_config_pure(self):
    """Verifies lib.config re-exports PathContext canonically and load_default_config stays free of context."""
    self.assertIs(config_mod.PathContext, PathContext)

    for fn in (
        expand_scar_database_path,
        insert_scar_databases_before_bundled,
        resolve_workflow_config,
    ):
      with self.subTest(fn=fn.__name__):
        sig = inspect.signature(fn)
        self.assertIn("context", sig.parameters)
        self.assertEqual(
            sig.parameters["context"].kind, inspect.Parameter.KEYWORD_ONLY
        )
        self.assertIsNone(sig.parameters["context"].default)

    default_sig = inspect.signature(load_default_config)
    self.assertEqual(list(default_sig.parameters.keys()), ["plugin_root"])
    for forbidden in ("home_dir", "agent", "context"):
      self.assertNotIn(forbidden, default_sig.parameters)

  def test_config_helpers_accept_path_context_with_parity_and_validation(self):
    """Verifies expand_scar_database_path, insert_scar_databases_before_bundled, and resolve_workflow_config."""
    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      ctx = PathContext(
          workspace_dir=fs["workspace"],
          agent="claude",
          home_dir=fs["home"],
          plugin_root=fs["plugin_root"],
      )
      expected_agent_db = os.path.join(
          fs["home"], ".claude", "plugin_data", "makeflow", "scars_registry.db"
      )
      expected_bundled_db = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
      )
      expected_proj_db = os.path.join(
          fs["workspace"], ".workflow", "scars", "scars_registry.db"
      )

      # 1. expand_scar_database_path parity
      self.assertEqual(
          expand_scar_database_path("{agent_data}/scars_registry.db", context=ctx),
          expected_agent_db,
      )
      self.assertEqual(
          expand_scar_database_path(".workflow/scars/scars_registry.db", context=ctx),
          expected_proj_db,
      )
      self.assertEqual(
          expand_scar_database_path(
              "{plugin_root}/skills/makeflow/data/scars_registry.db", context=ctx
          ),
          expected_bundled_db,
      )

      # 2. insert_scar_databases_before_bundled parity
      extra_db = os.path.join(fs["shared"], "team_extra.db")
      inserted = insert_scar_databases_before_bundled(
          [expected_proj_db, expected_bundled_db],
          [extra_db],
          context=ctx,
      )
      self.assertEqual(inserted, [expected_proj_db, extra_db, expected_bundled_db])

      # 3. resolve_workflow_config parity, positional compatibility, and "" CWD isolation
      local_cfg_path = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"tdd-worker": "pro"},
                "ci": {"quiescenceSeconds": 42},
            },
            fh,
        )

      with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
        via_ctx = resolve_workflow_config(context=ctx)
        via_positional = resolve_workflow_config(
            fs["workspace"], "claude", fs["home"], fs["plugin_root"]
        )
        self.assertEqual(via_ctx["models"]["tdd-worker"], "pro")
        self.assertEqual(via_ctx["ci"]["quiescenceSeconds"], 42)
        self.assertEqual(via_ctx["configSource"], local_cfg_path)
        self.assertEqual(via_ctx, via_positional)

        prev_cwd = os.getcwd()
        try:
          os.chdir(fs["workspace"])
          isolated_ctx = PathContext(
              workspace_dir="",
              agent="claude",
              home_dir=fs["home"],
              plugin_root=fs["plugin_root"],
          )
          isolated_cfg = resolve_workflow_config(context=isolated_ctx)
        finally:
          os.chdir(prev_cwd)

      self.assertEqual(isolated_cfg["configSource"], "defaults")
      self.assertEqual(isolated_cfg["models"]["tdd-worker"], "inherit")

      # 4. Validation errors (TypeError and ValueError) across all three config functions
      with self.assertRaises(TypeError):
        expand_scar_database_path("x.db", context="not-a-context")  # type: ignore[arg-type]
      with self.assertRaises(ValueError):
        expand_scar_database_path("x.db", home_dir=fs["home"], context=ctx)

      with self.assertRaises(TypeError):
        insert_scar_databases_before_bundled([], [], context={})  # type: ignore[arg-type]
      with self.assertRaises(ValueError):
        insert_scar_databases_before_bundled(
            [], [extra_db], plugin_root=fs["plugin_root"], context=ctx
        )

      with self.assertRaises(TypeError):
        resolve_workflow_config(context=123)  # type: ignore[arg-type]
      with self.assertRaises(ValueError):
        resolve_workflow_config(fs["workspace"], context=ctx)

  def test_scar_locations_reexports_path_context_and_supports_context_and_cwd_isolation(self):
    """Verifies resolve_scar_locations and get_configured_scar_databases accept PathContext and isolate CWD."""
    self.assertIs(scar_locations_mod.PathContext, PathContext)

    for fn in (resolve_scar_locations, get_configured_scar_databases):
      with self.subTest(fn=fn.__name__):
        sig = inspect.signature(fn)
        self.assertIn("context", sig.parameters)
        self.assertEqual(
            sig.parameters["context"].kind, inspect.Parameter.KEYWORD_ONLY
        )
        self.assertIsNone(sig.parameters["context"].default)

    with tempfile.TemporaryDirectory() as tmp_root:
      fs = _build_mock_fs_root(tmp_root)
      ambient_custom_db = os.path.join(fs["shared"], "ambient_ctx_custom.db")
      local_cfg_path = os.path.join(fs["workspace"], ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_cfg_path), exist_ok=True)
      with open(local_cfg_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scars": {
                    "databases": [
                        ".workflow/scars/scars_registry.db",
                        ambient_custom_db,
                        "{agent_data}/scars_registry.db",
                        "{plugin_root}/skills/makeflow/data/scars_registry.db",
                    ]
                }
            },
            fh,
        )

      explicit_ctx = PathContext(
          workspace_dir=fs["workspace"],
          agent="claude",
          home_dir=fs["home"],
          plugin_root=fs["plugin_root"],
      )
      isolated_ctx = PathContext(
          workspace_dir=None,
          agent="claude",
          home_dir=fs["home"],
          plugin_root=fs["plugin_root"],
      )
      expected_proj_db = os.path.join(
          fs["workspace"], ".workflow", "scars", "scars_registry.db"
      )
      expected_user_db = os.path.join(
          fs["home"], ".claude", "plugin_data", "makeflow", "scars_registry.db"
      )
      expected_bundled_db = os.path.join(
          fs["plugin_root"], "skills", "makeflow", "data", "scars_registry.db"
      )

      prev_cwd = os.getcwd()
      try:
        os.chdir(fs["workspace"])
        with mock.patch.dict(os.environ, _isolated_env(fs["home"]), clear=True):
          loc_explicit = resolve_scar_locations(context=explicit_ctx)
          dbs_explicit = get_configured_scar_databases(context=explicit_ctx)
          loc_isolated = resolve_scar_locations(context=isolated_ctx)
          dbs_isolated = get_configured_scar_databases(context=isolated_ctx)
      finally:
        os.chdir(prev_cwd)

      self.assertEqual(loc_explicit.project_db, expected_proj_db)
      self.assertEqual(loc_explicit.user_db, expected_user_db)
      self.assertEqual(loc_explicit.bundled_db, expected_bundled_db)
      self.assertEqual(
          list(loc_explicit.databases),
          [expected_proj_db, ambient_custom_db, expected_user_db, expected_bundled_db],
      )
      self.assertEqual(dbs_explicit, list(loc_explicit.databases))

      self.assertIsNone(loc_isolated.project_db)
      self.assertNotIn(ambient_custom_db, loc_isolated.databases)
      self.assertEqual(
          list(loc_isolated.databases),
          [expected_user_db, expected_bundled_db],
      )
      self.assertEqual(dbs_isolated, [expected_user_db, expected_bundled_db])

      with self.assertRaises(TypeError):
        resolve_scar_locations(context="bad")  # type: ignore[arg-type]
      with self.assertRaises(ValueError):
        resolve_scar_locations(agent="claude", context=explicit_ctx)
      with self.assertRaises(TypeError):
        get_configured_scar_databases(context="bad")  # type: ignore[arg-type]
      with self.assertRaises(ValueError):
        get_configured_scar_databases(home_dir=fs["home"], context=explicit_ctx)


if __name__ == "__main__":
  unittest.main()
