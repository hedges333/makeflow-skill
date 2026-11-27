"""Canonical cross-agent plugin data, configuration, and scar path registry (GH-117, GH-304).

Maps all six supported Makeflow agent platforms (gemini, claude, cursor,
copilot, codex, chatgpt) to immutable AgentPathProfile value objects covering
user-global JSON config files, legacy config fallbacks, and user-wide SQLite
scar registries.
"""

from dataclasses import dataclass
import os
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_AGENTS: Tuple[str, ...] = (
    "gemini",
    "claude",
    "cursor",
    "copilot",
    "codex",
    "chatgpt",
)

AGENT_ALIASES: Dict[str, str] = {
    "agy": "gemini",
    "antigravity": "gemini",
    "antigravity-cli": "gemini",
    "gemini-cli": "gemini",
    "claude-code": "claude",
    "claude_code": "claude",
    "github-copilot": "copilot",
    "github_copilot": "copilot",
    "openai-codex": "codex",
    "openai_codex": "codex",
    "gpt": "chatgpt",
}

_AGENT_PLUGIN_DATA_SEGMENTS: Dict[str, Tuple[str, ...]] = {
    "gemini": (".gemini", "antigravity-cli", "plugin_data", "makeflow"),
    "claude": (".claude", "plugin_data", "makeflow"),
    "cursor": (".cursor", "plugin_data", "makeflow"),
    "copilot": (".copilot", "plugin_data", "makeflow"),
    "codex": (".codex", "plugin_data", "makeflow"),
    "chatgpt": (".chatgpt", "plugin_data", "makeflow"),
}


def normalize_agent_name(agent: Optional[str]) -> str:
  """Normalize an agent name or alias to a canonical SUPPORTED_AGENTS key.

  Args:
    agent: Optional agent identifier or alias string. Defaults to ``gemini``
      when None or blank.

  Returns:
    Canonical lowercase agent name in ``SUPPORTED_AGENTS``.

  Raises:
    ValueError: If a non-empty agent name is not recognized.
  """
  cleaned = str(agent or "").strip().lower()
  if not cleaned:
    return "gemini"
  canonical = AGENT_ALIASES.get(cleaned, cleaned)
  if canonical not in _AGENT_PLUGIN_DATA_SEGMENTS:
    supported = ", ".join(SUPPORTED_AGENTS)
    raise ValueError(
        f"Unsupported agent '{agent}'. Expected one of: {supported}."
    )
  return canonical


@dataclass(frozen=True)
class PathContext:
  """Immutable value object bundling path-resolution boundary parameters (GH-319).

  Performs zero filesystem I/O on construction so callers can safely pass
  canonical or sentinel values (such as ``workspace_dir=""`` to disable ambient
  CWD fallback) across configuration and scar-resolution boundaries.

  Attributes:
    workspace_dir: Optional project repository root directory. Use ``""`` to
      explicitly suppress ambient CWD fallback.
    agent: Optional active agent identifier or alias override.
    home_dir: Optional home directory override for hermetic execution.
    plugin_root: Optional Makeflow plugin root directory override.
  """

  workspace_dir: Optional[str] = None
  agent: Optional[str] = None
  home_dir: Optional[str] = None
  plugin_root: Optional[str] = None


def resolve_path_context(
    context: Optional[PathContext] = None,
    *,
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    home_dir: Optional[str] = None,
    plugin_root: Optional[str] = None,
    package_root: Optional[str] = None,
) -> PathContext:
  """Normalize either a ``PathContext`` or legacy path kwargs at a boundary.

  Enforces type safety and mutual exclusion so boundary entrypoints share one
  consistent validation contract without duplicating argument checks.

  Args:
    context: Optional pre-built ``PathContext`` instance.
    workspace_dir: Optional legacy project root directory path.
    agent: Optional legacy active agent identifier override.
    home_dir: Optional legacy home directory override.
    plugin_root: Optional legacy Makeflow plugin root directory override.
    package_root: Optional legacy alias for ``plugin_root`` used by scar DB
      resolvers.

  Returns:
    Validated ``PathContext`` instance preserving all supplied fields,
    including the ``workspace_dir=""`` sentinel.

  Raises:
    TypeError: If ``context`` is not ``None`` and not a ``PathContext`` instance.
    ValueError: If ``context`` is combined with any non-``None`` legacy path
      keyword argument, or if both ``plugin_root`` and ``package_root`` are
      supplied.
  """
  if context is not None:
    if not isinstance(context, PathContext):
      raise TypeError(
          f"context must be a PathContext instance, got {type(context).__name__}."
      )
    conflicting = [
        name
        for name, val in (
            ("workspace_dir", workspace_dir),
            ("agent", agent),
            ("home_dir", home_dir),
            ("plugin_root", plugin_root),
            ("package_root", package_root),
        )
        if val is not None
    ]
    if conflicting:
      joined = ", ".join(conflicting)
      raise ValueError(
          f"Cannot specify both 'context' and legacy path parameter(s): {joined}."
      )
    return context

  if plugin_root is not None and package_root is not None:
    raise ValueError("Cannot specify both 'plugin_root' and 'package_root'.")

  return PathContext(
      workspace_dir=workspace_dir,
      agent=agent,
      home_dir=home_dir,
      plugin_root=plugin_root if plugin_root is not None else package_root,
  )


@dataclass(frozen=True)
class AgentPathProfile:
  """Immutable path profile for one Makeflow-supported agent platform.

  Attributes:
    name: Canonical agent name in ``SUPPORTED_AGENTS``.
    plugin_data_dir: Absolute path to agent-specific Makeflow plugin data root.
    global_config_path: Absolute path to user-global JSON configuration file.
    legacy_config_paths: Ordered tuple of backward-compatible legacy config paths.
    scars_db_path: Absolute path to user-wide SQLite synthetic scars registry.
  """

  name: str
  plugin_data_dir: str
  global_config_path: str
  legacy_config_paths: Tuple[str, ...]
  scars_db_path: str


class AgentPathRegistry:
  """Registry resolving canonical Makeflow paths across all six supported agents."""

  def __init__(self, home_dir: Optional[str] = None) -> None:
    """Initialize the registry anchored to a home directory.

    Args:
      home_dir: Optional home directory override. Defaults to ``os.path.expanduser("~")``.
    """
    raw_home = home_dir if home_dir is not None else os.path.expanduser("~")
    self.home_dir = os.path.abspath(os.path.expanduser(raw_home))
    legacy_path = os.path.join(
        self.home_dir, ".gemini", "config", "makeflow.json"
    )
    self._profiles: Dict[str, AgentPathProfile] = {}
    for agent_name in SUPPORTED_AGENTS:
      segments = _AGENT_PLUGIN_DATA_SEGMENTS[agent_name]
      plugin_data_dir = os.path.join(self.home_dir, *segments)
      self._profiles[agent_name] = AgentPathProfile(
          name=agent_name,
          plugin_data_dir=plugin_data_dir,
          global_config_path=os.path.join(plugin_data_dir, "config.json"),
          legacy_config_paths=(legacy_path,),
          scars_db_path=os.path.join(plugin_data_dir, "scars_registry.db"),
      )

  def resolve_active_agent(self, agent: Optional[str] = None) -> str:
    """Resolve the active agent from an explicit argument or MAKEFLOW_AGENT env var.

    Explicitly passed unknown agent names raise ValueError. Unrecognized
    ``MAKEFLOW_AGENT`` environment values fall back safely to ``gemini``.

    Args:
      agent: Optional explicit agent identifier.

    Returns:
      Canonical lowercase agent name in ``SUPPORTED_AGENTS``.

    Raises:
      ValueError: If an explicit non-empty ``agent`` argument is unrecognized.
    """
    if agent is not None and str(agent).strip():
      return normalize_agent_name(agent)
    env_agent = os.getenv("MAKEFLOW_AGENT", "").strip()
    if not env_agent:
      return "gemini"
    try:
      return normalize_agent_name(env_agent)
    except ValueError:
      return "gemini"

  def get_profile(self, agent: Optional[str] = None) -> AgentPathProfile:
    """Return the immutable AgentPathProfile for the resolved agent.

    Args:
      agent: Optional explicit agent identifier.

    Returns:
      Frozen ``AgentPathProfile`` instance.

    Raises:
      ValueError: If an explicit non-empty ``agent`` argument is unrecognized.
    """
    canonical = self.resolve_active_agent(agent)
    return self._profiles[canonical]

  def all_profiles(
      self, active_agent: Optional[str] = None
  ) -> List[AgentPathProfile]:
    """Return all six agent profiles ordered with the active agent first.

    Args:
      active_agent: Optional active agent override.

    Returns:
      Ordered list of all six ``AgentPathProfile`` objects.
    """
    active = self.resolve_active_agent(active_agent)
    ordered = [self._profiles[active]]
    for name in SUPPORTED_AGENTS:
      if name != active:
        ordered.append(self._profiles[name])
    return ordered

  def scars_db_paths(self, active_agent: Optional[str] = None) -> List[str]:
    """Return all six user-wide SQLite scar DB paths, active agent first.

    Args:
      active_agent: Optional active agent override.

    Returns:
      Ordered list of absolute ``scars_registry.db`` file paths.
    """
    return [p.scars_db_path for p in self.all_profiles(active_agent)]

  def to_dict(
      self,
      agent: Optional[str] = None,
      workspace_dir: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Build a structured JSON-serializable summary of resolved paths.

    Args:
      agent: Optional explicit agent identifier.
      workspace_dir: Optional project repository root directory.

    Returns:
      Dictionary containing active agent paths, project paths, and all agent profiles.
    """
    profile = self.get_profile(agent)
    ws_abs = (
        os.path.abspath(os.path.expanduser(workspace_dir))
        if workspace_dir
        else None
    )
    return {
        "activeAgent": profile.name,
        "pluginDataDir": profile.plugin_data_dir,
        "userConfigPath": profile.global_config_path,
        "legacyConfigPaths": list(profile.legacy_config_paths),
        "userScarsDbPath": profile.scars_db_path,
        "projectConfigPath": (
            os.path.join(ws_abs, ".workflow", "config.json") if ws_abs else None
        ),
        "projectScarsDbPath": (
            os.path.join(ws_abs, ".workflow", "scars", "scars_registry.db")
            if ws_abs
            else None
        ),
        "allAgents": {
            p.name: {
                "pluginDataDir": p.plugin_data_dir,
                "globalConfigPath": p.global_config_path,
                "scarsDbPath": p.scars_db_path,
            }
            for p in self.all_profiles(profile.name)
        },
    }
