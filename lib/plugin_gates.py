"""Plugin manifest completeness and SKILL.md context-budget gates (GH-274).

Validates that all required Makeflow plugin manifests exist and that
``skills/makeflow/SKILL.md`` remains within its context byte ceiling
(SCAR-PROC-99, SCAR-DOC-16).
"""

import os
from typing import Optional, Tuple

from lib.reflex import GateFailure

DEFAULT_SKILL_MAX_BYTES = 24576

REQUIRED_PLUGIN_FILES: Tuple[str, ...] = (
    "plugin.json",
    "hooks.json",
    "mcp_config.json",
    "default_config.json",
    "skills/makeflow/SKILL.md",
    "rules/AGENTS.md",
)


def check_plugin_files(repo_root: str) -> Optional[GateFailure]:
  """Verifies that all required Makeflow plugin files exist under repo_root.

  Args:
    repo_root: Repository root directory to inspect.

  Returns:
    ``None`` when all ``REQUIRED_PLUGIN_FILES`` exist; otherwise a
    ``GateFailure`` describing the missing files.
  """
  missing = [
      rel
      for rel in REQUIRED_PLUGIN_FILES
      if not os.path.isfile(os.path.join(repo_root, rel))
  ]
  if not missing:
    return None
  joined = ", ".join(missing)
  return GateFailure(
      scar_id="DOC-16",
      code="MISSING_REQUIRED_PLUGIN_FILE",
      trigger=f"Missing required plugin file(s) in {repo_root}: {joined}",
      rejected="Packaging or testing an incomplete plugin manifest, skill, or rule ring.",
      reflex=f"Restore {joined} in {repo_root} and re-run make check-plugin-files.",
  )


def check_skill_budget(
    repo_root: str, max_bytes: int = DEFAULT_SKILL_MAX_BYTES
) -> Optional[GateFailure]:
  """Verifies that skills/makeflow/SKILL.md exists and stays within max_bytes.

  Args:
    repo_root: Repository root directory containing ``skills/makeflow/SKILL.md``.
    max_bytes: Maximum allowed file size in bytes.

  Returns:
    ``None`` when the skill file exists and is within budget; otherwise a
    ``GateFailure`` describing the missing file or ceiling violation.
  """
  skill_rel = "skills/makeflow/SKILL.md"
  skill_path = os.path.join(repo_root, skill_rel)
  if not os.path.isfile(skill_path):
    return GateFailure(
        scar_id="DOC-16",
        code="MISSING_REQUIRED_PLUGIN_FILE",
        trigger=f"Missing {skill_rel} in {repo_root}.",
        rejected="Evaluating skill context budget without skills/makeflow/SKILL.md.",
        reflex=f"Restore {skill_rel} and re-run make check-skill-budget.",
    )
  actual_bytes = os.path.getsize(skill_path)
  if actual_bytes <= max_bytes:
    return None
  return GateFailure(
      scar_id="PROC-99",
      code="SKILL_CONTEXT_CEILING_EXCEEDED",
      trigger=f"{skill_rel} is {actual_bytes} bytes, exceeding ceiling of {max_bytes} bytes.",
      rejected="Packing verbose procedures or negative guardrails into static SKILL.md prose.",
      reflex="Move detailed rules into skills/makeflow/references/ or deterministic gates, then re-run make check-skill-budget.",
  )
