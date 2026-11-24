"""Behavioral tests for cross-agent path registry and configuration cascade (GH-117)."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.agent_paths import (
    AGENT_ALIASES,
    SUPPORTED_AGENTS,
    AgentPathProfile,
    AgentPathRegistry,
    normalize_agent_name,
)
from lib.config import (
    DEFAULT_WORKFLOW_CONFIG,
    SCARS_ENV_VAR,
    get_workflow_config,
    load_database_config,
    load_default_config,
    resolve_workflow_config,
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


class TestAgentPathRegistry(unittest.TestCase):
  """Tests canonical cross-agent path profiles for all six supported agents."""

  def test_lib_agent_paths_has_no_shebang(self):
    """Verifies lib/agent_paths.py obeys SCAR-ARCH-05 with zero shebang header."""
    lib_path = os.path.join(REPO_ROOT, "lib", "agent_paths.py")
    with open(lib_path, "r", encoding="utf-8") as fh:
      first_line = fh.readline()
    self.assertFalse(first_line.startswith("#!"))

  def test_six_agents_profiles_and_frozen_immutability(self):
    """Verifies all six agents map to canonical config, scars, and SQLite paths."""
    with tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
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
          self.assertEqual(profile.name, agent_name)
          self.assertEqual(profile.plugin_data_dir, expected_dir)
          self.assertEqual(
              profile.global_config_path,
              os.path.join(expected_dir, "config.json"),
          )
          self.assertEqual(profile.scars_dir, os.path.join(expected_dir, "scars"))
          self.assertEqual(
              profile.scars_db_path,
              os.path.join(expected_dir, "scars_registry.db"),
          )
          self.assertIn(legacy_cfg, profile.legacy_config_paths)
          with self.assertRaises(AttributeError):
            profile.name = "mutated"  # type: ignore[misc]

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
            registry.scars_dirs()[0],
            os.path.join(fake_home, ".codex", "plugin_data", "makeflow", "scars"),
        )
        self.assertEqual(
            registry.scars_db_paths()[0],
            os.path.join(
                fake_home, ".codex", "plugin_data", "makeflow", "scars_registry.db"
            ),
        )
        # Explicit argument overrides env
        self.assertEqual(registry.resolve_active_agent("cursor"), "cursor")
        # Invalid explicit argument raises ValueError
        with self.assertRaises(ValueError):
          registry.resolve_active_agent("not-a-real-agent")

      # Unrecognized env variable falls back safely to gemini
      with mock.patch.dict(
          os.environ,
          _isolated_env(fake_home, {"MAKEFLOW_AGENT": "bogus_env_agent"}),
          clear=True,
      ):
        self.assertEqual(registry.resolve_active_agent(), "gemini")


class TestConfigCascadeAndListUnion(unittest.TestCase):
  """Tests default -> legacy -> user-global -> project-local -> env cascade."""

  def test_defaults_include_scars_directories_everywhere(self):
    """Verifies default_config.json, config.json.example, and DEFAULT_WORKFLOW_CONFIG declare scars.directories."""
    self.assertEqual(SCARS_ENV_VAR, "MAKEFILE_WORKFLOW_SCAR_DIRS")
    self.assertEqual(DEFAULT_WORKFLOW_CONFIG["scars"], {"directories": []})
    loaded_default = load_default_config()
    self.assertEqual(loaded_default["scars"], {"directories": []})

    for rel_path in ("default_config.json", os.path.join(".workflow", "config.json.example")):
      with open(os.path.join(REPO_ROOT, rel_path), "r", encoding="utf-8") as fh:
        data = json.load(fh)
      self.assertEqual(data["scars"], {"directories": []}, msg=f"Missing scars.directories in {rel_path}")

  def test_full_cascade_precedence_and_list_union(self):
    """Verifies scalar override order and order-preserving list unions across all config layers."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      # 1. Legacy ~/.gemini/config/makeflow.json (lowest file precedence)
      legacy_path = os.path.join(fake_home, ".gemini", "config", "makeflow.json")
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
                "scars": {"directories": ["/scars/legacy", "/scars/shared"]},
            },
            fh,
        )

      # 2. Active agent user-global config (claude) overrides legacy scalars and unions lists
      registry = AgentPathRegistry(home_dir=fake_home)
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
                "scars": {"directories": ["/scars/global", "/scars/shared"]},
            },
            fh,
        )

      # 3. Project-local .workflow/config.json overrides user-global scalars and puts local scars first
      local_cfg_path = os.path.join(ws_dir, ".workflow", "config.json")
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
                "scars": {"directories": ["/scars/local", "/scars/global"]},
            },
            fh,
        )

      env = _isolated_env(
          fake_home,
          {
              "MAKEFLOW_AGENT": "claude",
              "MAKEFILE_WORKFLOW_SCAR_DIRS": "/scars/env,/scars/local",
              "MAKEFILE_WORKFLOW_PROD_DBS": "env_prod",
          },
      )
      with mock.patch.dict(os.environ, env, clear=True):
        mcp_cfg = resolve_workflow_config(ws_dir)
        hook_cfg, hook_src = get_workflow_config([ws_dir])
        db_cfg = load_database_config({"workspacePaths": [ws_dir]})

      # Scalar precedence: default < legacy < user-global < project-local < env
      for cfg in (mcp_cfg, hook_cfg):
        self.assertEqual(cfg["models"]["archaeology-worker"], "pro")
        self.assertEqual(cfg["models"]["adversarial-critic"], "pro")
        self.assertEqual(cfg["models"]["triage-worker"], "flash")
        self.assertEqual(cfg["models"]["tdd-worker"], "pro")
        self.assertEqual(cfg["models"]["synthetic-scar-importer"], "inherit")
        self.assertEqual(cfg["ci"]["pollIntervalSeconds"], 15)
        self.assertEqual(cfg["ci"]["quiescenceSeconds"], 0)
        # scars.directories union: project-local -> user-global -> legacy -> env (deduplicated)
        self.assertEqual(
            cfg["scars"]["directories"],
            ["/scars/local", "/scars/global", "/scars/shared", "/scars/legacy", "/scars/env"],
        )

      # Database lists merged safely across all layers without wiping global protections
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

      # configSource stays backward-compatible (highest-precedence file), configSources lists full cascade
      self.assertEqual(mcp_cfg["configSource"], local_cfg_path)
      self.assertEqual(
          mcp_cfg["configSources"],
          [legacy_path, claude_cfg_path, local_cfg_path],
      )
      self.assertIn(f"{SCARS_ENV_VAR}=/scars/env,/scars/local", mcp_cfg["envOverrides"])
      self.assertIsNotNone(hook_src)
      self.assertIn(SCARS_ENV_VAR, hook_src)

  def test_legacy_global_only_preserves_backward_compatible_source_labels(self):
    """Verifies legacy ~/.gemini/config/makeflow.json alone preserves GH-217 source labels."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      legacy_path = os.path.join(fake_home, ".gemini", "config", "makeflow.json")
      os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
      with open(legacy_path, "w", encoding="utf-8") as fh:
        json.dump({"ci": {"quiescenceSeconds": 0}}, fh)

      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
        hook_cfg, hook_src = get_workflow_config([ws_dir])
        mcp_cfg = resolve_workflow_config(ws_dir)

      self.assertEqual(hook_src, "~/.gemini/config/makeflow.json")
      self.assertEqual(mcp_cfg["configSource"], legacy_path)
      self.assertEqual(mcp_cfg["configSources"], [legacy_path])
      self.assertEqual(hook_cfg["scars"]["directories"], [])
      self.assertEqual(mcp_cfg["scars"]["directories"], [])

  def test_corrupt_and_non_dict_json_recovery_and_workspace_fallback(self):
    """Verifies corrupt or non-dict JSON configs recover and fall back across workspace_paths (NBO #6)."""
    with tempfile.TemporaryDirectory() as ws_corrupt, tempfile.TemporaryDirectory() as ws_list, tempfile.TemporaryDirectory() as ws_valid, tempfile.TemporaryDirectory() as fake_home:
      # Corrupt legacy config is ignored cleanly
      legacy_path = os.path.join(fake_home, ".gemini", "config", "makeflow.json")
      os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
      with open(legacy_path, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")

      # Valid user-global config loads cleanly
      registry = AgentPathRegistry(home_dir=fake_home)
      global_path = registry.get_profile("gemini").global_config_path
      os.makedirs(os.path.dirname(global_path), exist_ok=True)
      with open(global_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"adversarial-critic": "pro"},
                "scars": {"directories": ["/scars/from-global"]},
            },
            fh,
        )

      # First workspace has syntactically invalid JSON
      corrupt_local = os.path.join(ws_corrupt, ".workflow", "config.json")
      os.makedirs(os.path.dirname(corrupt_local), exist_ok=True)
      with open(corrupt_local, "w", encoding="utf-8") as fh:
        fh.write("{broken")

      # Second workspace has valid JSON but wrong top-level type (list instead of dict)
      list_local = os.path.join(ws_list, ".workflow", "config.json")
      os.makedirs(os.path.dirname(list_local), exist_ok=True)
      with open(list_local, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)

      # Third workspace has valid JSON object and must be used as fallback
      valid_local = os.path.join(ws_valid, ".workflow", "config.json")
      os.makedirs(os.path.dirname(valid_local), exist_ok=True)
      with open(valid_local, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "models": {"tdd-worker": "flash"},
                "database": {"testDatabases": ["fallback_test_db"]},
                "scars": {"directories": ["/scars/from-fallback-ws"]},
            },
            fh,
        )

      with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
        hook_cfg, hook_src = get_workflow_config([ws_corrupt, ws_list, ws_valid])
        db_cfg = load_database_config({"workspacePaths": [ws_corrupt, ws_list, ws_valid]})

      self.assertEqual(hook_cfg["models"]["adversarial-critic"], "pro")
      self.assertEqual(hook_cfg["models"]["tdd-worker"], "flash")
      self.assertEqual(
          hook_cfg["scars"]["directories"],
          ["/scars/from-fallback-ws", "/scars/from-global"],
      )
      self.assertIn("fallback_test_db", db_cfg["testDatabases"])
      self.assertEqual(hook_src, f"{global_path} + {valid_local}")
      self.assertEqual(hook_cfg["configSources"], [global_path, valid_local])

      # When two valid workspaces exist, only the first valid workspace loads
      with tempfile.TemporaryDirectory() as ws_second_valid:
        second_local = os.path.join(ws_second_valid, ".workflow", "config.json")
        os.makedirs(os.path.dirname(second_local), exist_ok=True)
        with open(second_local, "w", encoding="utf-8") as fh:
          json.dump({"models": {"tdd-worker": "pro"}}, fh)

        with mock.patch.dict(os.environ, _isolated_env(fake_home), clear=True):
          first_wins_cfg, first_wins_src = get_workflow_config(
              [ws_valid, ws_second_valid]
          )
        self.assertEqual(first_wins_cfg["models"]["tdd-worker"], "flash")
        self.assertEqual(first_wins_src, f"{global_path} + {valid_local}")

  def test_scars_directories_deduplicates_tilde_and_trailing_slashes(self):
    """Verifies scars.directories deduplicates equivalent tilde and trailing-slash paths."""
    with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as fake_home:
      registry = AgentPathRegistry(home_dir=fake_home)
      shared_abs = os.path.join(fake_home, "shared_scars")

      global_path = registry.get_profile("gemini").global_config_path
      os.makedirs(os.path.dirname(global_path), exist_ok=True)
      with open(global_path, "w", encoding="utf-8") as fh:
        json.dump({"scars": {"directories": [shared_abs + os.sep]}}, fh)

      local_path = os.path.join(ws_dir, ".workflow", "config.json")
      os.makedirs(os.path.dirname(local_path), exist_ok=True)
      with open(local_path, "w", encoding="utf-8") as fh:
        json.dump({"scars": {"directories": ["~/shared_scars/", "~/other_scars"]}}, fh)

      env = _isolated_env(
          fake_home,
          {"MAKEFILE_WORKFLOW_SCAR_DIRS": f"{shared_abs},~/other_scars/"},
      )
      with mock.patch.dict(os.environ, env, clear=True):
        resolved = resolve_workflow_config(ws_dir)

      self.assertEqual(
          resolved["scars"]["directories"],
          ["~/shared_scars/", "~/other_scars"],
      )


if __name__ == "__main__":
  unittest.main()
