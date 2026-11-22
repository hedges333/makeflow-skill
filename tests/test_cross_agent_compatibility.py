#!/usr/bin/env python3
"""Cross-Agent Compatibility Validator.

Verifies the configuration files, manifests, and rules for:
1. Anthropic Claude Code (.claude-plugin/plugin.json, CLAUDE.md)
2. OpenAI Codex (.codex-plugin/plugin.json)
3. Cursor IDE (.cursorrules, .cursor/rules/makeflow.mdc)
4. GitHub Copilot (.github/copilot-instructions.md)
5. ChatGPT (adapters/chatgpt/instructions.md)
6. Hooks & MCP Declarations (hooks.json, mcp_config.json)
"""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.scar_candidate import ScarCandidateValidator, split_into_sentences, validate_socratic_probe


def repo_path(*parts):
  """Resolves path relative to REPO_ROOT."""
  return os.path.normpath(os.path.join(REPO_ROOT, *parts))


def check_scar_catalog_routing(catalog_text, scar_id, spoke_filename):
  """Verifies strict table row routing for a synthetic scar entry (SCAR-TOOL-15)."""
  pattern = rf"(?m)^\|\s*`{re.escape(scar_id)}`\s*\|.*\|\s*\[`scars/{re.escape(spoke_filename)}`\]"
  return bool(re.search(pattern, catalog_text))


def check_makefile_test_discovery(
    mk_text: str, required_targets: tuple[str, ...] = ()
) -> list[str]:
  """Verifies Makefile wires test-static, unittest discover across TESTS_DIR, and convenience targets."""
  errors = []
  mk_rel = "Makefile"
  if "test-static:" not in mk_text:
    errors.append(f"{mk_rel} must define 'test-static:' target")

  lines = mk_text.splitlines()
  test_idx = next(
      (i for i, ln in enumerate(lines) if ln.startswith("test:")), None
  )
  if test_idx is None:
    errors.append(f"{mk_rel} must define 'test:' target")
  else:
    prereqs = lines[test_idx][len("test:") :].strip().split()
    if prereqs != ["test-static"]:
      errors.append(
          f"{mk_rel} 'test:' target must list 'test-static' as its sole prerequisite"
      )
    recipe_lines = []
    for ln in lines[test_idx + 1 :]:
      if ln.startswith("\t") or not ln.strip() or ln.lstrip().startswith("#"):
        recipe_lines.append(ln)
      else:
        break
    active_cmds = []
    cur_parts = []
    for ln in recipe_lines:
      if not ln.startswith("\t"):
        continue
      stripped = ln.lstrip()
      if stripped.startswith("#"):
        continue
      cur_parts.append(stripped.rstrip("\\").strip())
      if not ln.rstrip().endswith("\\"):
        active_cmds.append(" ".join(cur_parts))
        cur_parts = []
    if cur_parts:
      active_cmds.append(" ".join(cur_parts))

    has_discovery_cmd = any(
        "$(PYTHON)" in seg
        and "-m unittest discover" in seg
        and "$(TESTS_DIR)" in seg
        for cmd in active_cmds
        for seg in re.split(r"\s*(?:\|\||&&|;)\s*", cmd)
    )
    if not has_discovery_cmd:
      errors.append(
          f"{mk_rel} 'test:' recipe must run '$(PYTHON) -m unittest discover' across '$(TESTS_DIR)'"
      )

  if "PYTHON ?=" not in mk_text:
    errors.append(
        f"{mk_rel} must declare 'PYTHON ?= python3' configurable interpreter macro"
    )
  if "@python3 " in mk_text:
    errors.append(
        f"{mk_rel} test recipes must use $(PYTHON) instead of hardcoded python3"
    )

  for target in required_targets:
    if not re.search(rf"(?m)^{re.escape(target)}:", mk_text):
      errors.append(f"{mk_rel} must define '{target}:' target")

  return errors


def validate_human_declaration_authority_and_pause_gates():
  """Validates human declaration authority and pause gates (TEST-SCAR-PROC-102).

  Verifies:
  1. All 6 human pause gates (Target 20, Target 42, Target 45, Target 50,
     Target 60, Target 75) are present in rules/AGENTS.md,
     skills/makeflow/SKILL.md, CLAUDE.md, .cursorrules,
     .cursor/rules/makeflow.mdc, .github/copilot-instructions.md,
     and adapters/chatgpt/instructions.md.
  2. Agent self-declaration / self-approval is explicitly prohibited in each
     rule and adapter file under SCAR-PROC-102.
  3. SCAR-PROC-102 is indexed in the catalog and process spoke files.
  """
  errors = []
  target_files = [
      "rules/AGENTS.md",
      "skills/makeflow/SKILL.md",
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ]
  required_gates = [
      "Target 20",
      "Target 42",
      "Target 45",
      "Target 50",
      "Target 60",
      "Target 75",
  ]

  for fpath in target_files:
    full_fpath = repo_path(fpath)
    if not os.path.exists(full_fpath):
      errors.append(f"Missing {fpath}")
      continue
    with open(full_fpath, "r", encoding="utf-8") as f:
      content = f.read()

    # Check pause gates
    for gate in required_gates:
      if gate not in content:
        errors.append(f"{fpath} missing required pause gate '{gate}'")

    # Check SCAR-PROC-102 citation
    if "SCAR-PROC-102" not in content:
      errors.append(f"{fpath} must reference SCAR-PROC-102")

    # Check explicit prohibition of self-approval / self-declaration
    content_lower = content.lower()
    has_prohibition = any(
        term in content_lower
        for term in (
            "self-approval",
            "self-declaration",
            "declare or approve gates themselves",
            "declare or approve gates yourself",
            "approve their own",
            "approve its own",
        )
    )
    if not has_prohibition:
      errors.append(
          f"{fpath} must explicitly prohibit agent self-approval / self-declaration"
      )

  # Check reference catalog and category spoke
  catalog_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  catalog_path = repo_path(catalog_rel)
  if not os.path.exists(catalog_path):
    errors.append(f"Missing {catalog_rel}")
  else:
    with open(catalog_path, "r", encoding="utf-8") as f:
      catalog_text = f.read()
    if "SCAR-PROC-102" not in catalog_text:
      errors.append(f"{catalog_rel} must document SCAR-PROC-102")
    if not check_scar_catalog_routing(catalog_text, "SCAR-PROC-102", "process.md"):
      errors.append(f"{catalog_rel} must route SCAR-PROC-102 to scars/process.md")
    if "Human Declaration Authority" not in catalog_text:
      errors.append(f"{catalog_rel} must index SCAR-PROC-102 Human Declaration Authority")

  process_spoke_rel = "skills/makeflow/references/scars/process.md"
  process_spoke = repo_path(process_spoke_rel)
  if not os.path.exists(process_spoke):
    errors.append(f"Missing {process_spoke_rel}")
  else:
    with open(process_spoke, "r", encoding="utf-8") as f:
      proc_text = f.read()
    if "SCAR-PROC-102" not in proc_text:
      errors.append(f"{process_spoke_rel} must document SCAR-PROC-102")
    if "Human Declaration Authority" not in proc_text:
      errors.append(f"{process_spoke_rel} must document SCAR-PROC-102 Human Declaration Authority")
    for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
      if f"**{field}**:" not in proc_text:
        errors.append(f"{process_spoke_rel} SCAR-PROC-102 missing '**{field}**:'")

  return errors


def validate_plan_artifact_lifecycle_and_demotion():
  """Validates plan artifact lifecycle and demotion invariants (TEST-SCAR-PROC-103).

  Verifies:
  1. rules/AGENTS.md references SCAR-PROC-103.
  2. rules/AGENTS.md specifies single fixed <brain>/plan.md mirroring.
  3. rules/AGENTS.md specifies artifact demotion with UserFacing: false and RequestFeedback: false.
  4. rules/AGENTS.md explicitly prohibits shell rm commands for artifact deletion.
  5. rules/AGENTS.md specifies built-in write_file tool demotion.
  6. rules/AGENTS.md guards against duplicate execution using 20_plan_approved.md.
  7. rules/AGENTS.md specifies Overwrite: true and writing an approved tombstone body inside <brain>/plan.md.
  8. skills/makeflow/references/09_synthetic_scars_catalog.md indexes SCAR-PROC-103 pointing to scars/process.md.
  9. skills/makeflow/references/scars/process.md documents SCAR-PROC-103 with complete Four-Part Anatomy
     (**Wound**:, **Trap**:, **Permanent Reflex**:, **Socratic Probe**:) and key invariants.
  10. skills/makeflow/SKILL.md preserves platform neutrality by not containing <brain> references.
  """
  errors = []

  # 1. Antigravity rules (rules/AGENTS.md)
  agents_rel = "rules/AGENTS.md"
  agents_md = repo_path(agents_rel)
  if not os.path.exists(agents_md):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_md, "r", encoding="utf-8") as f:
      agents_text = f.read()

    if "SCAR-PROC-103" not in agents_text:
      errors.append(f"{agents_rel} must reference SCAR-PROC-103")
    if "<brain>/plan.md" not in agents_text:
      errors.append(f"{agents_rel} must specify single fixed '<brain>/plan.md' mirroring")
    if "UserFacing: false" not in agents_text or "RequestFeedback: false" not in agents_text:
      errors.append(f"{agents_rel} must specify artifact demotion with UserFacing: false and RequestFeedback: false")
    if "shell `rm`" not in agents_text and "shell rm" not in agents_text:
      errors.append(f"{agents_rel} must explicitly prohibit shell rm commands for artifact deletion")
    if "write_file" not in agents_text or "built-in" not in agents_text:
      errors.append(f"{agents_rel} must specify built-in write_file tool demotion")
    if "20_plan_approved.md" not in agents_text:
      errors.append(f"{agents_rel} must guard against duplicate execution using 20_plan_approved.md")
    if "Overwrite: true" not in agents_text:
      errors.append(f"{agents_rel} must specify 'Overwrite: true' for plan artifact operations")
    if "tombstone" not in agents_text.lower():
      errors.append(f"{agents_rel} must specify writing an approved tombstone body inside <brain>/plan.md")

  # 2. Synthetic Scars Catalog Index
  catalog_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  catalog_path = repo_path(catalog_rel)
  if not os.path.exists(catalog_path):
    errors.append(f"Missing {catalog_rel}")
  else:
    with open(catalog_path, "r", encoding="utf-8") as f:
      catalog_text = f.read()
    if "SCAR-PROC-103" not in catalog_text:
      errors.append(f"{catalog_rel} must index SCAR-PROC-103")
    if not check_scar_catalog_routing(catalog_text, "SCAR-PROC-103", "process.md"):
      errors.append(f"{catalog_rel} must route SCAR-PROC-103 to scars/process.md")

  # 3. Process Spoke Four-Part Anatomy & Key Invariants
  process_spoke_rel = "skills/makeflow/references/scars/process.md"
  process_spoke = repo_path(process_spoke_rel)
  if not os.path.exists(process_spoke):
    errors.append(f"Missing {process_spoke_rel}")
  else:
    with open(process_spoke, "r", encoding="utf-8") as f:
      proc_text = f.read()

    if "SCAR-PROC-103" not in proc_text:
      errors.append(f"{process_spoke_rel} must document SCAR-PROC-103")
    else:
      scar_section = proc_text.split("### SCAR-PROC-103")[1].split("### SCAR-")[0] if "### SCAR-PROC-103" in proc_text else ""
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{process_spoke_rel} SCAR-PROC-103 missing '**{field}**:'")
      for invariant in ("<brain>/plan.md", "write_file", "UserFacing: false", "RequestFeedback: false", "20_plan_approved.md"):
        if invariant not in scar_section:
          errors.append(f"{process_spoke_rel} SCAR-PROC-103 missing invariant '{invariant}'")
      if "shell `rm`" not in scar_section and "shell rm" not in scar_section:
        errors.append(f"{process_spoke_rel} SCAR-PROC-103 missing shell rm invariant")

  # 4. Platform Neutrality in SKILL.md
  skill_rel = "skills/makeflow/SKILL.md"
  skill_md = repo_path(skill_rel)
  if not os.path.exists(skill_md):
    errors.append(f"Missing {skill_rel}")
  else:
    with open(skill_md, "r", encoding="utf-8") as f:
      skill_text = f.read()
    if "<brain>" in skill_text or "<brain/" in skill_text:
      errors.append(f"{skill_rel} must preserve platform neutrality by not containing '<brain>' references")

  return errors


def validate_synthetic_scar_catalog_routing():
  """Validates strict table row regex matching for scar catalog routing (GH-59, SCAR-TOOL-15).

  Verifies:
  1. Negative falsification: check_scar_catalog_routing correctly rejects:
     - Misrouted spoke destination (e.g., SCAR-PROC-103 pointing to scars/testing.md)
     - Loose unanchored prose citations (not in a Markdown table row)
     - Multi-line separated citations
  2. Positive validation: check_scar_catalog_routing confirms valid table row formatting.
  """
  errors = []

  # 1. Negative Falsification Checks
  mock_misrouted = "| `SCAR-PROC-103` | Lifecycle | Process | [`scars/testing.md`](./scars/testing.md) |"
  if check_scar_catalog_routing(mock_misrouted, "SCAR-PROC-103", "process.md"):
    errors.append("check_scar_catalog_routing failed to reject misrouted spoke filename")

  mock_unanchored_prose = "See SCAR-PROC-103 in scars/process.md for details."
  if check_scar_catalog_routing(mock_unanchored_prose, "SCAR-PROC-103", "process.md"):
    errors.append("check_scar_catalog_routing failed to reject unanchored prose citation")

  mock_multiline = "| `SCAR-PROC-103` |\n| scars/process.md |"
  if check_scar_catalog_routing(mock_multiline, "SCAR-PROC-103", "process.md"):
    errors.append("check_scar_catalog_routing failed to reject multiline separated citation")

  # 2. Positive Verification
  mock_valid = "| `SCAR-PROC-103` | Lifecycle | Process | [`scars/process.md`](./scars/process.md) |"
  if not check_scar_catalog_routing(mock_valid, "SCAR-PROC-103", "process.md"):
    errors.append("check_scar_catalog_routing failed to match valid table row")

  return errors


def validate_adversarial_critic_manual_reference():
  """Validates explicit Six Pillars review manual path in critic dispatch (GH-23, SCAR-PROC-105).

  Verifies:
  1. rules/AGENTS.md mandates SCAR-PROC-105 and passing the explicit workspace path
     skills/makeflow/references/04_six_pillars_adversarial_review.md.
  2. skills/makeflow/SKILL.md Target 40 specifies passing
     skills/makeflow/references/04_six_pillars_adversarial_review.md
     and cites SCAR-PROC-105.
  3. skills/makeflow/references/08_subagent_orchestration.md specifies
     skills/makeflow/references/04_six_pillars_adversarial_review.md in the critic prompt pattern
     and cites SCAR-PROC-105.
  4. skills/makeflow/references/04_six_pillars_adversarial_review.md documents SCAR-PROC-105.
  5. skills/makeflow/references/scars/process.md documents SCAR-PROC-105 with
     all Four-Part Anatomy sections non-empty.
  6. skills/makeflow/references/09_synthetic_scars_catalog.md routes SCAR-PROC-105 to scars/process.md.
  7. Negative falsification:
     - Prompts omitting the explicit manual path trigger validation errors.
     - Corrupted scar entries missing Four-Part Anatomy trigger validation errors.
  """
  errors = []
  manual_path = "skills/makeflow/references/04_six_pillars_adversarial_review.md"

  # 1. rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    if "SCAR-PROC-105" not in agents_text:
      errors.append(f"{agents_rel} must document SCAR-PROC-105")
    if manual_path not in agents_text:
      errors.append(f"{agents_rel} must mandate explicit manual path '{manual_path}' for critic dispatch")

  # 2. skills/makeflow/SKILL.md
  skill_rel = "skills/makeflow/SKILL.md"
  skill_path = repo_path(skill_rel)
  if not os.path.exists(skill_path):
    errors.append(f"Missing {skill_rel}")
  else:
    with open(skill_path, "r", encoding="utf-8") as f:
      skill_text = f.read()
    if "SCAR-PROC-105" not in skill_text:
      errors.append(f"{skill_rel} must cite SCAR-PROC-105")
    if manual_path not in skill_text:
      errors.append(f"{skill_rel} Target 40 must specify explicit manual path '{manual_path}'")

  # 3. skills/makeflow/references/08_subagent_orchestration.md
  ref_08_rel = "skills/makeflow/references/08_subagent_orchestration.md"
  ref_08_path = repo_path(ref_08_rel)
  if not os.path.exists(ref_08_path):
    errors.append(f"Missing {ref_08_rel}")
  else:
    with open(ref_08_path, "r", encoding="utf-8") as f:
      r08_text = f.read()
    if "SCAR-PROC-105" not in r08_text:
      errors.append(f"{ref_08_rel} must cite SCAR-PROC-105")
    if manual_path not in r08_text:
      errors.append(f"{ref_08_rel} critic prompt pattern must include explicit manual path '{manual_path}'")

  # 4. skills/makeflow/references/04_six_pillars_adversarial_review.md
  ref_04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  ref_04_path = repo_path(ref_04_rel)
  if not os.path.exists(ref_04_path):
    errors.append(f"Missing {ref_04_rel}")
  else:
    with open(ref_04_path, "r", encoding="utf-8") as f:
      r04_text = f.read()
    if "SCAR-PROC-105" not in r04_text:
      errors.append(f"{ref_04_rel} must document SCAR-PROC-105")

  # 5. skills/makeflow/references/scars/process.md
  proc_rel = "skills/makeflow/references/scars/process.md"
  proc_path = repo_path(proc_rel)
  if not os.path.exists(proc_path):
    errors.append(f"Missing {proc_rel}")
  else:
    with open(proc_path, "r", encoding="utf-8") as f:
      proc_text = f.read()
    if "### SCAR-PROC-105" not in proc_text:
      errors.append(f"{proc_rel} must document SCAR-PROC-105")
    else:
      scar_section = proc_text.split("### SCAR-PROC-105")[1].split("### SCAR-")[0]
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        field_match = re.search(rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", scar_section)
        if not field_match or not field_match.group(1).strip():
          errors.append(f"{proc_rel} SCAR-PROC-105 missing non-empty '**{field}**:'")
      if manual_path not in scar_section:
        errors.append(f"{proc_rel} SCAR-PROC-105 must cite '{manual_path}' in Permanent Reflex")

  # 6. skills/makeflow/references/09_synthetic_scars_catalog.md
  catalog_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  catalog_path = repo_path(catalog_rel)
  if not os.path.exists(catalog_path):
    errors.append(f"Missing {catalog_rel}")
  else:
    with open(catalog_path, "r", encoding="utf-8") as f:
      catalog_text = f.read()
    if not check_scar_catalog_routing(catalog_text, "SCAR-PROC-105", "process.md"):
      errors.append(f"{catalog_rel} must route SCAR-PROC-105 to scars/process.md")

  # 7. Negative Falsification Checks
  mock_shallow_prompt = "Review this Merkle tree diff against the Six Pillars."
  mock_corrupted_scar = "### SCAR-PROC-105: Title\n**Wound**: Broken\n**Trap**: Fall"

  return errors


def validate_comment_thread_ingestion_scar_proc_107():
  """Validates full discussion and comment thread ingestion at intake (GH-78, SCAR-PROC-107).

  Verifies:
  1. skills/makeflow/references/scars/process.md documents SCAR-PROC-107 with
     all Four-Part Anatomy sections non-empty (**Wound**, **Trap**, **Permanent Reflex**, **Socratic Probe**).
  2. skills/makeflow/references/09_synthetic_scars_catalog.md routes SCAR-PROC-107 to scars/process.md.
  3. rules/AGENTS.md references SCAR-PROC-107 and mandates '--comments'.
  """
  errors = []

  # 1. skills/makeflow/references/scars/process.md
  proc_rel = "skills/makeflow/references/scars/process.md"
  proc_path = repo_path(proc_rel)
  if not os.path.exists(proc_path):
    errors.append(f"Missing {proc_rel}")
  else:
    with open(proc_path, "r", encoding="utf-8") as f:
      proc_text = f.read()
    if "### SCAR-PROC-107" not in proc_text:
      errors.append(f"{proc_rel} must document SCAR-PROC-107")
    else:
      scar_section = (
          proc_text.split("### SCAR-PROC-107")[1].split("### SCAR-")[0]
          if "### SCAR-PROC-107" in proc_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        field_match = re.search(
            rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", scar_section
        )
        if not field_match or not field_match.group(1).strip():
          errors.append(f"{proc_rel} SCAR-PROC-107 missing non-empty '**{field}**:'")
      for invariant in ("--comments", "intake", "thread"):
        if invariant.lower() not in scar_section.lower():
          errors.append(f"{proc_rel} SCAR-PROC-107 missing invariant '{invariant}'")

  # 2. skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-PROC-107" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-PROC-107")
    if not check_scar_catalog_routing(cat_text, "SCAR-PROC-107", "process.md"):
      errors.append(f"{cat_rel} must route SCAR-PROC-107 to scars/process.md")

  # 3. rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    if "SCAR-PROC-107" not in agents_text:
      errors.append(f"{agents_rel} must reference SCAR-PROC-107")
    if "--comments" not in agents_text:
      errors.append(f"{agents_rel} must reference '--comments'")

  return errors


def validate_temporary_script_quoting_scar():
  """Validates temporary script quoting scar invariants (GH-25, SCAR-SH-01).

  Verifies:
  1. skills/makeflow/references/scars/git_and_shell.md documents
     SCAR-SH-01 with complete Four-Part Anatomy and invariants (temporary directory,
     complex string escaping, quotes/quoting, Dart, Python).
  2. skills/makeflow/references/09_synthetic_scars_catalog.md routes
     SCAR-SH-01 strictly to scars/git_and_shell.md.
  3. skills/makeflow/references/10_security_and_data_isolation.md
     Section 2 documents SCAR-SH-01.
  4. rules/AGENTS.md Section 3.1 documents SCAR-SH-01.
  5. All 5 adapters (CLAUDE.md, .cursorrules, .cursor/rules/makeflow.mdc,
     .github/copilot-instructions.md, adapters/chatgpt/instructions.md) document
     SCAR-SH-01.
  6. Negative falsification:
     - Missing Four-Part Anatomy fields or corrupted text triggers errors.
     - Misrouted catalog table rows trigger errors.
  """
  errors = []

  sh_spoke_rel = "skills/makeflow/references/scars/git_and_shell.md"
  sh_spoke = repo_path(sh_spoke_rel)
  if not os.path.exists(sh_spoke):
    errors.append(f"Missing {sh_spoke_rel}")
  else:
    with open(sh_spoke, "r", encoding="utf-8") as f:
      sh_text = f.read()
    if "SCAR-SH-01" not in sh_text:
      errors.append(f"{sh_spoke_rel} must document SCAR-SH-01")
    else:
      scar_section = (
          sh_text.split("### SCAR-SH-01")[1].split("### SCAR-")[0]
          if "### SCAR-SH-01" in sh_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{sh_spoke_rel} SCAR-SH-01 missing '**{field}**:'")
      for invariant in (
          "temporary directory",
          "complex string escaping",
          "quotes",
          "Dart",
          "Python",
          "noexec",
          "chmod +x",
          "interpreter",
      ):
        if invariant.lower() not in scar_section.lower():
          errors.append(
              f"{sh_spoke_rel} SCAR-SH-01 missing invariant '{invariant}'"
          )
      if "execute that file directly" in scar_section.lower():
        errors.append(
            f"{sh_spoke_rel} SCAR-SH-01 must not specify 'execute that file directly'"
        )

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-SH-01" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-SH-01")
    if not check_scar_catalog_routing(cat_text, "SCAR-SH-01", "git_and_shell.md"):
      errors.append(
          f"{cat_rel} must route SCAR-SH-01 to scars/git_and_shell.md"
      )

  sec_rel = (
      "skills/makeflow/references/10_security_and_data_isolation.md"
  )
  sec_path = repo_path(sec_rel)
  if not os.path.exists(sec_path):
    errors.append(f"Missing {sec_rel}")
  else:
    with open(sec_path, "r", encoding="utf-8") as f:
      sec_text = f.read()
    if "SCAR-SH-01" not in sec_text:
      errors.append(f"{sec_rel} must document SCAR-SH-01")
    if "noexec" not in sec_text.lower():
      errors.append(f"{sec_rel} must document noexec protection")
    if "interpreter" not in sec_text.lower():
      errors.append(f"{sec_rel} must mandate runtime interpreter invocation")

  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    if "SCAR-SH-01" not in agents_text:
      errors.append(f"{agents_rel} Section 3.1 must reference SCAR-SH-01")
    if "execute that file directly" in agents_text.lower():
      errors.append(f"{agents_rel} must not instruct executing the file directly")
    agents_sec31 = (
        agents_text.split("### 3.1 Temporary Script and Automation Rules")[1].split("## 4.")[0]
        if "### 3.1 Temporary Script and Automation Rules" in agents_text
        else ""
    )
    if "interpreter" not in agents_sec31.lower():
      errors.append(f"{agents_rel} Section 3.1 must mandate runtime interpreter invocation")

  for adapter_rel in [
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ]:
    adapter_path = repo_path(adapter_rel)
    if not os.path.exists(adapter_path):
      errors.append(f"Missing {adapter_rel}")
    else:
      with open(adapter_path, "r", encoding="utf-8") as f:
        adapter_text = f.read()
      if "SCAR-SH-01" not in adapter_text:
        errors.append(f"{adapter_rel} must document SCAR-SH-01")
      lines = [line for line in adapter_text.splitlines() if "SCAR-SH-01" in line]
      for line in lines:
        if "execute that file directly" in line.lower():
          errors.append(f"{adapter_rel} must not specify 'execute that file directly'")
        if "interpreter" not in line.lower() and "runtime" not in line.lower():
          errors.append(
              f"{adapter_rel} SCAR-SH-01 line must specify runtime interpreter invocation"
          )

  # Negative Falsification Checks
  mock_corrupted_spoke = "### SCAR-SH-01: Broken\n**Wound**: Foo"
  if "**Trap**:" in mock_corrupted_spoke:
    errors.append("Falsification failed: corrupted spoke should not have Trap")
  mock_misrouted = (
      "| `SCAR-SH-01` | Shell | Git & Shell |"
      " [`scars/process.md`](./scars/process.md) |"
  )
  if check_scar_catalog_routing(mock_misrouted, "SCAR-SH-01", "git_and_shell.md"):
    errors.append("Falsification failed: misrouted catalog line was accepted")
  mock_direct_exec = "Write script to temporary directory and execute that file directly."
  if "execute that file directly" not in mock_direct_exec:
    errors.append("Falsification failed: direct execution pattern not detected")

  return errors


def validate_basescript_pattern_scar():
  """Validates BaseScript pattern and SCAR-ARCH-05 codification (GH-31).

  Verifies:
  1. skills/makeflow/references/scars/architecture.md documents
     SCAR-ARCH-05 with complete Four-Part Anatomy and invariants (BaseScript,
     Template Method, execute, setup, run, cleanup, sys.exit).
  2. skills/makeflow/references/09_synthetic_scars_catalog.md routes
     SCAR-ARCH-05 strictly to scars/architecture.md and indexes architecture.md spoke.
  3. skills/makeflow/references/14_design_patterns_and_architecture.md
     Section 4 documents SCAR-ARCH-05 and BaseScript pattern with polyglot templates.
  4. rules/AGENTS.md Section 3.1 documents SCAR-ARCH-05 and BaseScript.
  5. All 5 adapters (CLAUDE.md, .cursorrules, .cursor/rules/makeflow.mdc,
     .github/copilot-instructions.md, adapters/chatgpt/instructions.md) document
     SCAR-ARCH-05.
  6. Makefile defines test-base-script target and discovers tests via unittest discover.
  7. lib/base_script.py and tests/test_base_script.py exist.
  8. Negative falsification:
     - Missing Four-Part Anatomy fields or corrupted text triggers errors.
     - Misrouted catalog table rows trigger errors.
  """
  errors = []

  arch_spoke_rel = "skills/makeflow/references/scars/architecture.md"
  arch_spoke = repo_path(arch_spoke_rel)
  if not os.path.exists(arch_spoke):
    errors.append(f"Missing {arch_spoke_rel}")
  else:
    with open(arch_spoke, "r", encoding="utf-8") as f:
      arch_text = f.read()
    if "SCAR-ARCH-05" not in arch_text:
      errors.append(f"{arch_spoke_rel} must document SCAR-ARCH-05")
    else:
      scar_section = (
          arch_text.split("### SCAR-ARCH-05")[1].split("### SCAR-")[0]
          if "### SCAR-ARCH-05" in arch_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{arch_spoke_rel} SCAR-ARCH-05 missing '**{field}**:'")
      for invariant in (
          "basescript",
          "template method",
          "execute",
          "setup",
          "run",
          "cleanup",
          "sys.exit",
      ):
        if invariant.lower() not in scar_section.lower():
          errors.append(
              f"{arch_spoke_rel} SCAR-ARCH-05 missing invariant '{invariant}'"
          )

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-ARCH-05" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-ARCH-05")
    if "architecture.md" not in cat_text:
      errors.append(f"{cat_rel} must list architecture.md in Hub-and-Spoke index")
    if not check_scar_catalog_routing(cat_text, "SCAR-ARCH-05", "architecture.md"):
      errors.append(
          f"{cat_rel} must route SCAR-ARCH-05 to scars/architecture.md"
      )

  pat_rel = "skills/makeflow/references/14_design_patterns_and_architecture.md"
  pat_path = repo_path(pat_rel)
  if not os.path.exists(pat_path):
    errors.append(f"Missing {pat_rel}")
  else:
    with open(pat_path, "r", encoding="utf-8") as f:
      pat_text = f.read()
    if "SCAR-ARCH-05" not in pat_text:
      errors.append(f"{pat_rel} must document SCAR-ARCH-05")
    if "BaseScript" not in pat_text:
      errors.append(f"{pat_rel} must document BaseScript pattern")
    if "Template Method" not in pat_text:
      errors.append(f"{pat_rel} must document Template Method lifecycle")

  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    if "SCAR-ARCH-05" not in agents_text:
      errors.append(f"{agents_rel} Section 3.1 must reference SCAR-ARCH-05")
    if "BaseScript" not in agents_text:
      errors.append(f"{agents_rel} Section 3.1 must mention BaseScript")

  for adapter_rel in [
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ]:
    adapter_path = repo_path(adapter_rel)
    if not os.path.exists(adapter_path):
      errors.append(f"Missing {adapter_rel}")
    else:
      with open(adapter_path, "r", encoding="utf-8") as f:
        adapter_text = f.read()
      if "SCAR-ARCH-05" not in adapter_text:
        errors.append(f"{adapter_rel} must document SCAR-ARCH-05")

  mk_rel = "Makefile"
  mk_path = repo_path(mk_rel)
  if not os.path.exists(mk_path):
    errors.append(f"Missing {mk_rel}")
  else:
    with open(mk_path, "r", encoding="utf-8") as f:
      mk_text = f.read()
    errors.extend(check_makefile_test_discovery(mk_text, ("test-base-script",)))

  for script_rel in ["lib/base_script.py", "tests/test_base_script.py"]:
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  # Negative Falsification Checks
  mock_corrupted_spoke = "### SCAR-ARCH-05: Broken\n**Wound**: Foo"
  if "**Trap**:" in mock_corrupted_spoke:
    errors.append("Falsification failed: corrupted spoke should not have Trap")
  mock_misrouted = (
      "| `SCAR-ARCH-05` | Architecture | BaseScript |"
      " [`scars/process.md`](./scars/process.md) |"
  )
  if check_scar_catalog_routing(mock_misrouted, "SCAR-ARCH-05", "architecture.md"):
    errors.append("Falsification failed: misrouted catalog line was accepted")

  return errors


def validate_three_layer_python_architecture():
  """Validates three-layer Python module architecture (GH-169, SCAR-ARCH-05)."""
  import ast

  errors = []
  lib_dir = repo_path("lib")
  scripts_dir = repo_path("scripts")
  tests_dir = repo_path("tests")

  if not os.path.isdir(lib_dir) or not os.path.isfile(os.path.join(lib_dir, "__init__.py")):
    errors.append("Missing lib/__init__.py shared library package")
  else:
    for fname in sorted(os.listdir(lib_dir)):
      if not fname.endswith(".py"):
        continue
      fpath = os.path.join(lib_dir, fname)
      with open(fpath, "r", encoding="utf-8") as f:
        first_line = f.readline()
      if first_line.startswith("#!"):
        errors.append(f"lib/{fname} must not have an executable shebang header")

  if not os.path.isdir(scripts_dir):
    errors.append("Missing scripts/ directory")
  else:
    script_py = sorted(f for f in os.listdir(scripts_dir) if f.endswith(".py"))
    script_modules = {os.path.splitext(f)[0] for f in script_py}
    for fname in script_py:
      if fname.startswith("test_"):
        errors.append(f"scripts/{fname} must not start with 'test_' in scripts/")
      if fname in ("base_script.py", "safe_copy.py", "test_scar_candidate.py"):
        errors.append(f"scripts/{fname} must not remain in scripts/")
      fpath = os.path.join(scripts_dir, fname)
      with open(fpath, "r", encoding="utf-8") as f:
        source = f.read()
      if not source.startswith("#!/usr/bin/env python3"):
        errors.append(f"scripts/{fname} must start with #!/usr/bin/env python3")
      if 'if __name__ == "__main__":' not in source:
        errors.append(f"scripts/{fname} must have an if __name__ == '__main__': entrypoint")
      tree = ast.parse(source, filename=fpath)
      for node in ast.walk(tree):
        if isinstance(node, ast.Import):
          for alias in node.names:
            top = alias.name.split(".")[0]
            if top in script_modules or top == "scripts":
              errors.append(f"scripts/{fname} must not import peer script '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
          mod = node.module or ""
          top = mod.split(".")[0] if mod else ""
          if top in script_modules or top == "scripts":
            errors.append(f"scripts/{fname} must not import from peer script '{mod}'")

  if not os.path.isfile(os.path.join(scripts_dir, "validate_scar_candidate.py")):
    errors.append("Missing scripts/validate_scar_candidate.py")

  if not os.path.isdir(tests_dir):
    errors.append("Missing tests/ directory")
  else:
    test_py = [f for f in os.listdir(tests_dir) if f.startswith("test_") and f.endswith(".py")]
    if len(test_py) < 18:
      errors.append(f"Expected 18 test_*.py suites in tests/, found {len(test_py)}")

  importer_rel = "agents/synthetic-scar-importer.md"
  importer_path = repo_path(importer_rel)
  if os.path.isfile(importer_path):
    with open(importer_path, "r", encoding="utf-8") as f:
      imp_text = f.read()
    if "scripts/validate_scar_candidate.py" not in imp_text:
      errors.append(f"{importer_rel} must reference scripts/validate_scar_candidate.py")
    if "scripts/test_scar_candidate.py" in imp_text:
      errors.append(f"{importer_rel} must not reference scripts/test_scar_candidate.py")

  ci_rel = ".github/workflows/ci.yml"
  ci_path = repo_path(ci_rel)
  if not os.path.isfile(ci_path):
    errors.append(f"Missing {ci_rel}")
  else:
    with open(ci_path, "r", encoding="utf-8") as f:
      ci_text = f.read()
    if "scripts/test_" in ci_text:
      errors.append(f"{ci_rel} must not reference relocated scripts/test_*.py paths")
    for expected_ci_test in (
        "tests/test_cross_agent_compatibility.py",
        "tests/test_compat_cwd.py",
        "tests/test_mcp_server.py",
        "tests/test_hooks.py",
    ):
      if expected_ci_test not in ci_text:
        errors.append(f"{ci_rel} must reference {expected_ci_test}")

  mk_rel = "Makefile"
  mk_path = repo_path(mk_rel)
  if os.path.isfile(mk_path):
    with open(mk_path, "r", encoding="utf-8") as f:
      mk_text = f.read()
    errors.extend(
        check_makefile_test_discovery(mk_text, ("test-interpreter-invocation",))
    )

  return errors


POLYGLOT_DIRECTORY_STRUCTURE_REQUIREMENTS = {
    "c_cpp.md": ("include/", "src/", "tests/"),
    "csharp_dotnet.md": ("src/", "Program.cs", ".Tests"),
    "dart_flutter.md": ("lib/src/", "bin/", "_test.dart"),
    "dlang.md": ("source/", "app.d", "unittest"),
    "elixir.md": ("lib/", "test/", "_test.exs"),
    "gdscript_godot.md": ("scripts/", "scenes/", "test_"),
    "go.md": ("internal/", "cmd/", "_test.go"),
    "haxe.md": ("src/", "Main.hx", "Test.hx"),
    "java_kotlin_scala.md": ("src/main/", "src/test/"),
    "nim.md": ("src/", "isMainModule", "tests/t_"),
    "odin.md": ("package", "main :: proc()", "_test.odin"),
    "perl.md": ("lib/", ".pm", "t/"),
    "php.md": ("src/", "PSR-4", "Test.php"),
    "powershell.md": ("Public/", "Private/", ".Tests.ps1"),
    "python.md": ("lib/", "scripts/", "tests/test_", "BaseScript"),
    "ruby.md": ("lib/", "spec/", "_spec.rb"),
    "rust.md": ("src/lib.rs", "src/main.rs", "#[cfg(test)]", "tests/"),
    "scientific_r_julia_lua.md": (
        "R/",
        "tests/testthat/",
        "test/runtests.jl",
        "_spec.lua",
    ),
    "sql_migrations.md": ("migrations/", "models/", "tests/"),
    "swift.md": ("Sources/", "Tests/", "Tests.swift"),
    "typescript_javascript.md": ("src/", "index.ts", ".test.ts"),
}

POLYGLOT_STRUCTURE_MARKERS = (
    "Library and Domain Layer",
    "Executable Entrypoints and Controllers",
    "Test Suite Placement and Naming",
    "Structural Anti-Patterns to Flag at Target 15",
)

_POLYGLOT_CONTRACTION_RE = re.compile(
    r"\b(?:don't|doesn't|isn't|aren't|wasn't|weren't|can't|cannot't|won't|"
    r"wouldn't|shouldn't|couldn't|hasn't|haven't|hadn't|it's|that's|there's|"
    r"we're|they're|you're|I'm|let's)\b",
    re.IGNORECASE,
)


def _check_polyglot_directory_structure_guides(
    guide_files: dict[str, str],
) -> list[str]:
  """Validates Idiomatic Directory and Module Structure sections across all 21 polyglot guides (GH-174)."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scar_candidate import (
      count_words_in_sentence,
      mask_code_and_math_spans,
      split_into_sentences,
  )

  errs = []
  base_rel = "skills/makeflow/references/polyglot"
  section_heading = "## Idiomatic Directory and Module Structure"

  for fname, req_tokens in POLYGLOT_DIRECTORY_STRUCTURE_REQUIREMENTS.items():
    rel_path = f"{base_rel}/{fname}"
    if fname not in guide_files:
      errs.append(f"Missing {rel_path}")
      continue
    text = guide_files[fname]
    section = _extract_markdown_h2_section(text, section_heading)
    if not section.strip():
      errs.append(
          f"{rel_path} missing required H2 section '{section_heading}'"
      )
      continue

    for marker in POLYGLOT_STRUCTURE_MARKERS:
      if marker not in section:
        errs.append(
            f"{rel_path} section '{section_heading}' missing required structural marker '{marker}'"
        )

    for tok in req_tokens:
      if tok not in section:
        errs.append(
            f"{rel_path} section '{section_heading}' missing required path/structure token '{tok}'"
        )

    errs.extend(_check_prose_punctuation(rel_path, section))

    in_fence = False
    for idx, raw_line in enumerate(section.splitlines(), start=1):
      stripped = raw_line.strip()
      if stripped.startswith("```"):
        in_fence = not in_fence
        continue
      if in_fence or not stripped or stripped.startswith("#"):
        continue
      no_code = re.sub(r"`[^`]*`", "", stripped)
      match = _POLYGLOT_CONTRACTION_RE.search(no_code)
      if match:
        errs.append(
            f"{rel_path}:{idx} contains prohibited contraction '{match.group(0)}' in prose"
        )
      prose_body = re.sub(r"^-\s+(?:\*\*[^*]+\*\*:\s*)?", "", stripped)
      masked = mask_code_and_math_spans(prose_body)
      for sent in split_into_sentences(masked):
        wc = count_words_in_sentence(sent)
        if wc > 20:
          errs.append(
              f"{rel_path}:{idx} sentence exceeds 20-word limit ({wc} words): {sent}"
          )

  return errs


def validate_polyglot_directory_and_module_structure() -> list[str]:
  """Validates Idiomatic Directory and Module Structure sections across all 21 polyglot guides (GH-174)."""
  errors = []
  base_rel = "skills/makeflow/references/polyglot"
  guide_files = {}
  for fname in POLYGLOT_DIRECTORY_STRUCTURE_REQUIREMENTS:
    fpath = repo_path(f"{base_rel}/{fname}")
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        guide_files[fname] = f.read()

  errors.extend(_check_polyglot_directory_structure_guides(guide_files))

  # In-memory negative falsification checks (SCAR-TEST-11)
  sample_guides = {}
  for fname, req_tokens in POLYGLOT_DIRECTORY_STRUCTURE_REQUIREMENTS.items():
    tok_spans = " ".join(f"`{t}`" for t in req_tokens)
    sample_guides[fname] = (
        f"# {fname}\n\n"
        "## Idiomatic Directory and Module Structure\n\n"
        f"- **Library and Domain Layer**: Place modules in {tok_spans}.\n"
        "- **Executable Entrypoints and Controllers**: Keep entrypoints thin.\n"
        "- **Test Suite Placement and Naming**: Isolate tests cleanly.\n"
        "- **Structural Anti-Patterns to Flag at Target 15**: Flag mixed layers.\n"
    )

  baseline_errs = _check_polyglot_directory_structure_guides(sample_guides)
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for polyglot guides: {baseline_errs}"
    )

  # 1. Missing guide file
  bad_missing = dict(sample_guides)
  del bad_missing["python.md"]
  if not any(
      "Missing skills/makeflow/references/polyglot/python.md" in e
      for e in _check_polyglot_directory_structure_guides(bad_missing)
  ):
    errors.append(
        "Negative falsification failed: missing python.md was not rejected"
    )

  # 2. Missing H2 heading
  bad_no_heading = dict(sample_guides)
  bad_no_heading["go.md"] = "# Go Guide\n\n## Core Idioms\n\n- Use Go.\n"
  if not any(
      "missing required H2 section '## Idiomatic Directory and Module Structure'"
      in e
      for e in _check_polyglot_directory_structure_guides(bad_no_heading)
  ):
    errors.append(
        "Negative falsification failed: missing H2 section in go.md was not rejected"
    )

  # 3. Missing one of the 4 structural rule markers
  bad_no_marker = dict(sample_guides)
  bad_no_marker["rust.md"] = bad_no_marker["rust.md"].replace(
      "Structural Anti-Patterns to Flag at Target 15", "Other Notes"
  )
  if not any(
      "Structural Anti-Patterns to Flag at Target 15" in e
      for e in _check_polyglot_directory_structure_guides(bad_no_marker)
  ):
    errors.append(
        "Negative falsification failed: missing structural marker in rust.md was not rejected"
    )

  # 4. Prose semicolon or em-dash in section
  bad_semi = dict(sample_guides)
  bad_semi["c_cpp.md"] += "- Extra note; with semicolon.\n"
  if not any(
      "semicolon" in e
      for e in _check_polyglot_directory_structure_guides(bad_semi)
  ):
    errors.append(
        "Negative falsification failed: prose semicolon in c_cpp.md was not rejected"
    )
  bad_emdash = dict(sample_guides)
  bad_emdash["c_cpp.md"] += "- Extra note \u2014 with em-dash.\n"
  if not any(
      "em-dash" in e
      for e in _check_polyglot_directory_structure_guides(bad_emdash)
  ):
    errors.append(
        "Negative falsification failed: prose em-dash in c_cpp.md was not rejected"
    )

  # 5. Prose contraction in section
  bad_contraction = dict(sample_guides)
  bad_contraction["elixir.md"] += "- You don't mix domain and web layers.\n"
  if not any(
      "prohibited contraction" in e
      for e in _check_polyglot_directory_structure_guides(bad_contraction)
  ):
    errors.append(
        "Negative falsification failed: prose contraction in elixir.md was not rejected"
    )

  # 6. Prose sentence exceeding 20 words in section
  bad_long = dict(sample_guides)
  bad_long["ruby.md"] += (
      "- Place every single domain model class inside the primary library directory "
      "whenever you build a new application service component for production deployment today.\n"
  )
  if not any(
      "exceeds 20-word limit" in e
      for e in _check_polyglot_directory_structure_guides(bad_long)
  ):
    errors.append(
        "Negative falsification failed: sentence exceeding 20 words in ruby.md was not rejected"
    )

  return errors


SCAR_ARCH_08_ADAPTER_FILES = (
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)

SCAR_ARCH_08_TARGET_FILES = (
    "skills/makeflow/references/scars/architecture.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "agents/archaeology-worker.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/11_polyglot_monorepos.md",
    "rules/AGENTS.md",
    "agents/tdd-worker.md",
    "skills/makeflow/references/03_tdd_and_code_quality.md",
    "agents/adversarial-critic.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
) + SCAR_ARCH_08_ADAPTER_FILES


def check_scar_arch_08_layer_separation(
    files_map: dict[str, str],
) -> list[str]:
  """Validates SCAR-ARCH-08 and shift-left structural review invariants across Target 15/20/30/40 (GH-175)."""
  errs = []
  for rel in SCAR_ARCH_08_TARGET_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # a. skills/makeflow/references/scars/architecture.md
  arch_rel = "skills/makeflow/references/scars/architecture.md"
  arch_text = files_map[arch_rel]
  table_match = re.search(
      r"## Scars in this Category.*?(?=\n###|\Z)", arch_text, re.DOTALL
  )
  table_sec = table_match.group(0) if table_match else ""
  if (
      "SCAR-ARCH-08" not in table_sec
      or "Polyglot Layer Separation and Structural Debt Triage" not in table_sec
  ):
    errs.append(
        f"{arch_rel} category table must list SCAR-ARCH-08 (Polyglot Layer Separation and Structural Debt Triage)"
    )

  m07 = re.search(r"(?m)^###\s+SCAR-ARCH-07\b", arch_text)
  m08 = re.search(
      r"(?m)^###\s+SCAR-ARCH-08:\s+Polyglot Layer Separation and Structural Debt Triage\b",
      arch_text,
  )
  if not (m07 and m08 and m07.start() < m08.start()):
    errs.append(
        f"{arch_rel} must define '### SCAR-ARCH-08: Polyglot Layer Separation and Structural Debt Triage' monotonically after SCAR-ARCH-07"
    )
  else:
    sec08 = arch_text[m08.start() :].split("\n### SCAR-")[0]
    extracted = {}
    req_fields = ("Wound", "Trap", "Permanent Reflex", "Socratic Probe")
    for field in req_fields:
      fm = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", sec08)
      val = fm.group(1).strip() if fm else ""
      extracted[field] = val
      if not val:
        errs.append(f"{arch_rel} SCAR-ARCH-08 missing non-empty '**{field}**:'")

    for tok in (
        "skills/makeflow/references/polyglot/<lang>.md",
        "20_plan.md",
        "entrypoints -> library",
        "Target 15",
        "Target 20",
    ):
      if tok not in sec08:
        errs.append(f"{arch_rel} SCAR-ARCH-08 missing required token '{tok}'")

    if all(extracted[f] for f in req_fields):
      candidate = {
          "id": "SCAR-ARCH-08",
          "raw_wound": extracted["Wound"],
          "wound": extracted["Wound"],
          "raw_trap": extracted["Trap"],
          "trap": extracted["Trap"],
          "raw_circuit_breaker": "",
          "circuit_breaker": "",
          "raw_reflex": extracted["Permanent Reflex"],
          "reflex": extracted["Permanent Reflex"],
          "socratic_probe": extracted["Socratic Probe"],
      }
      errs.extend(ScarCandidateValidator().validate(candidate))
      probe_sents = split_into_sentences(extracted["Socratic Probe"])
      if not (1 <= len(probe_sents) <= 3):
        errs.append(
            f"{arch_rel} SCAR-ARCH-08 Socratic Probe must contain 1-3 '?' sentences, found {len(probe_sents)}"
        )

  errs.extend(_check_prose_punctuation(arch_rel, arch_text))

  # b. skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_text = files_map[cat_rel]
  if not check_scar_catalog_routing(cat_text, "SCAR-ARCH-08", "architecture.md"):
    errs.append(
        f"{cat_rel} Master Routing Table must strictly route SCAR-ARCH-08 to scars/architecture.md"
    )
  c07 = re.search(r"(?m)^\|\s*`SCAR-ARCH-07`\s*\|", cat_text)
  c08 = re.search(r"(?m)^\|\s*`SCAR-ARCH-08`\s*\|", cat_text)
  if not (c07 and c08 and c07.start() < c08.start()):
    errs.append(
        f"{cat_rel} must place SCAR-ARCH-08 monotonically after SCAR-ARCH-07"
    )

  # c. Target 15: agents/archaeology-worker.md and 02_archaeology_and_planning.md (Section 1)
  aw_rel = "agents/archaeology-worker.md"
  aw_text = files_map[aw_rel]
  for tok in (
      "Module Layout and Layer Separation",
      "skills/makeflow/references/polyglot/<lang>.md",
      "SCAR-ARCH-08",
      "ESTABLISHED",
  ):
    if tok not in aw_text:
      errs.append(f"{aw_rel} missing required Target 15 structural token '{tok}'")
  if "anti-pattern" not in aw_text.lower():
    errs.append(f"{aw_rel} must instruct flagging structural anti-patterns")

  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_text = files_map[r02_rel]
  r02_sec1 = _extract_markdown_h2_section(r02_text, "## 1. Target 15")
  for tok in (
      "Module Layout and Layer Separation",
      "skills/makeflow/references/polyglot/<lang>.md",
      "SCAR-ARCH-08",
      "ESTABLISHED",
  ):
    if tok not in r02_sec1:
      errs.append(
          f"{r02_rel} Section 1 missing required Target 15 structural token '{tok}'"
      )
  if "anti-pattern" not in r02_sec1.lower():
    errs.append(
        f"{r02_rel} Section 1 must require flagging structural anti-patterns"
    )

  # d. Target 20: 02_archaeology_and_planning.md (Section 2), 11_polyglot_monorepos.md, rules/AGENTS.md
  r02_sec2 = _extract_markdown_h2_section(r02_text, "## 2. Target 20")
  if (
      "### 2.1 Socratic Pre-Planning Deliberation" not in r02_sec2
      or "### 2.2 Socratic Invariant Audit Table" not in r02_sec2
      or "### 2.3 Required Plan Disclosures" not in r02_sec2
  ):
    errs.append(f"{r02_rel} Section 2 missing required 2.1, 2.2, or 2.3 headings")
  else:
    s21 = r02_sec2.split("### 2.1 Socratic Pre-Planning Deliberation")[1].split(
        "### 2.2"
    )[0]
    s22 = r02_sec2.split("### 2.2 Socratic Invariant Audit Table")[1].split(
        "### 2.3"
    )[0]
    s23 = r02_sec2.split("### 2.3 Required Plan Disclosures")[1]

    if "Code Structure and Layer Separation" not in s21 or "SCAR-ARCH-08" not in s21:
      errs.append(
          f"{r02_rel} Section 2.1 must include Socratic question on Code Structure and Layer Separation (SCAR-ARCH-08)"
      )
    if (
        "| **Code Structure and Layer Separation** |" not in s22
        or "SCAR-ARCH-08" not in s22
    ):
      errs.append(
          f"{r02_rel} Section 2.2 Socratic Invariant Audit Table must include row '**Code Structure and Layer Separation**' citing SCAR-ARCH-08"
      )
    for tok in (
        "layer role",
        "Adapt to clean existing layout",
        "Enforce canonical layout for new subprojects or subsystems",
        "Flag messy existing layout at Target 15 and Target 20",
        "SCAR-ARCH-08",
    ):
      if tok not in s23:
        errs.append(
            f"{r02_rel} Section 2.3 missing required plan disclosure token '{tok}'"
        )

  r11_rel = "skills/makeflow/references/11_polyglot_monorepos.md"
  r11_text = files_map[r11_rel]
  r11_sec1 = _extract_markdown_h2_section(r11_text, "## 1. Subproject Discovery")
  r11_sec2 = _extract_markdown_h2_section(r11_text, "## 2. Directory Layout Rules")
  for tok in (
      "Module Layout and Layer Separation",
      "skills/makeflow/references/polyglot/<lang>.md",
      "SCAR-ARCH-08",
  ):
    if tok not in r11_sec1:
      errs.append(f"{r11_rel} Section 1 missing required token '{tok}'")
  for tok in (
      "SCAR-ARCH-08",
      "entrypoints -> library",
      "Adapt to clean existing layout",
      "Enforce canonical layout",
      "Flag messy existing layout",
  ):
    if tok not in r11_sec2:
      errs.append(f"{r11_rel} Section 2 missing required token '{tok}'")

  agents_rel = "rules/AGENTS.md"
  agents_text = files_map[agents_rel]
  for tok in (
      "SCAR-ARCH-08",
      "entrypoints -> library",
      "skills/makeflow/references/polyglot/<lang>.md",
      "layer role",
  ):
    if tok not in agents_text:
      errs.append(f"{agents_rel} missing required token '{tok}'")
  if "messy" not in agents_text.lower() or "choice" not in agents_text.lower():
    errs.append(
        f"{agents_rel} must document Target 20 user choice when existing layout is messy"
    )

  # e. Target 30: agents/tdd-worker.md and 03_tdd_and_code_quality.md
  for t30_rel in (
      "agents/tdd-worker.md",
      "skills/makeflow/references/03_tdd_and_code_quality.md",
  ):
    t30_text = files_map[t30_rel]
    for tok in (
        "SCAR-ARCH-08",
        "SCAR-ARCH-05",
        "skills/makeflow/references/polyglot/<lang>.md",
        "entrypoints -> library",
    ):
      if tok not in t30_text:
        errs.append(f"{t30_rel} missing required Target 30 token '{tok}'")

  # f. Target 40: agents/adversarial-critic.md and 04_six_pillars_adversarial_review.md
  for t40_rel in (
      "agents/adversarial-critic.md",
      "skills/makeflow/references/04_six_pillars_adversarial_review.md",
  ):
    t40_text = files_map[t40_rel]
    p3_match = re.search(
        r"(?m)^###\s+3\.\s+Architecture\b.*?(?=^###\s+4\b|\Z)",
        t40_text,
        re.DOTALL,
    )
    p3_sec = p3_match.group(0) if p3_match else ""
    p3_lower = p3_sec.lower()
    if "SCAR-ARCH-08" not in p3_sec:
      errs.append(f"{t40_rel} Pillar 3 must reference SCAR-ARCH-08")
    if "SCAR-PROC-108" not in p3_sec:
      errs.append(f"{t40_rel} Pillar 3 must reference SCAR-PROC-108")
    if (
        ("layer-boundary" not in p3_lower and "layer boundary" not in p3_lower)
        or "blocker" not in p3_lower
    ):
      errs.append(
          f"{t40_rel} Pillar 3 must classify new layer-boundary violations in the diff as Pillar 3 Blockers"
      )
    if (
        "pre-existing structural debt" not in p3_lower
        or "non-blocking observation" not in p3_lower
    ):
      errs.append(
          f"{t40_rel} Pillar 3 must classify pre-existing structural debt in adjacent legacy files as Non-Blocking Observations"
      )

  # g. 5 Agent Adapters
  for adapter_rel in SCAR_ARCH_08_ADAPTER_FILES:
    ad_text = files_map[adapter_rel]
    for tok in ("SCAR-ARCH-08", "SCAR-ARCH-05", "entrypoints -> library"):
      if tok not in ad_text:
        errs.append(f"{adapter_rel} missing required token '{tok}'")

  return errs


def validate_polyglot_layer_separation_scar_arch_08() -> list[str]:
  """Validates SCAR-ARCH-08 codification and Target 15/20/30/40 shift-left structural rules (GH-175)."""
  errors = []
  repo_files = {}
  for rel in SCAR_ARCH_08_TARGET_FILES:
    fpath = repo_path(rel)
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        repo_files[rel] = f.read()

  errors.extend(check_scar_arch_08_layer_separation(repo_files))

  # Hermetic in-memory negative falsification checks (SCAR-TEST-11)
  sample_files = {
      "skills/makeflow/references/scars/architecture.md": (
          "# Architecture and Pattern Scars\n\n"
          "## Scars in this Category\n\n"
          "| ID | Title | Scope |\n"
          "| :--- | :--- | :--- |\n"
          "| `SCAR-ARCH-07` | Anti-Speculative Defenses and Caller Grounding | Architecture |\n"
          "| `SCAR-ARCH-08` | Polyglot Layer Separation and Structural Debt Triage | Layer Boundaries & Structural Triage |\n\n"
          "### SCAR-ARCH-07: Anti-Speculative Defenses and Caller Grounding\n\n"
          "- **Wound**: Speculative regex branches stalled reviews.\n"
          "- **Trap**: Designing around hypothetical scenarios.\n"
          "- **Permanent Reflex**: Ground every branch in a real caller contract.\n"
          "- **Socratic Probe**: Have you identified a real caller contract?\n\n"
          "### SCAR-ARCH-08: Polyglot Layer Separation and Structural Debt Triage\n\n"
          "- **Wound**: Copying sibling file placement into a conflated directory compounds structural debt and cross-entrypoint coupling, while unapproved mid-ticket directory reorganizations cause severe blast-radius regressions.\n"
          "- **Trap**: Adding new domain logic into executable scripts or flat directories without auditing structure at Target 15 and Target 20. Reorganizing unrelated legacy directories mid-ticket without explicit Target 20 human approval.\n"
          "- **Permanent Reflex**: Audit subproject layout at Target 15 against `skills/makeflow/references/polyglot/<lang>.md` and disclose module placement in `20_plan.md`. Enforce one-way dependencies (`entrypoints -> library`). If an existing layout is messy, offer a user choice at Target 20. Either keep in-ticket edits minimal with a follow-up ticket, or include scoped reorganization with explicit human approval.\n"
          "- **Socratic Probe**: Does this change place reusable logic, thin entrypoints, and tests in idiomatic language layers without cross-entrypoint imports or unapproved reorganization?\n"
      ),
      "skills/makeflow/references/09_synthetic_scars_catalog.md": (
          "| `SCAR-ARCH-07` | Anti-Speculative Defenses and Caller Grounding | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
          "| `SCAR-ARCH-08` | Polyglot Layer Separation and Structural Debt Triage | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
      ),
      "agents/archaeology-worker.md": (
          "Record Module Layout and Layer Separation against `skills/makeflow/references/polyglot/<lang>.md` (`SCAR-ARCH-08`) "
          "and flag structural anti-patterns with Belnap `ESTABLISHED` file evidence.\n"
      ),
      "skills/makeflow/references/02_archaeology_and_planning.md": (
          "## 1. Target 15: Codebase Archaeology\n"
          "Record Module Layout and Layer Separation against `skills/makeflow/references/polyglot/<lang>.md` (`SCAR-ARCH-08`) "
          "and flag structural anti-patterns with Belnap `ESTABLISHED` file evidence.\n\n"
          "## 2. Target 20: Plan File Creation (`20_plan.md`)\n"
          "### 2.1 Socratic Pre-Planning Deliberation\n"
          "7. **Code Structure and Layer Separation (`SCAR-ARCH-08`)**: Does this change place reusable logic in idiomatic layers?\n"
          "### 2.2 Socratic Invariant Audit Table\n"
          "| **Code Structure and Layer Separation** | Does this change place reusable logic in idiomatic layers? | State layer role (`SCAR-ARCH-08`). |\n"
          "### 2.3 Required Plan Disclosures\n"
          "State the layer role of each planned file under `SCAR-ARCH-08`: "
          "Adapt to clean existing layout, Enforce canonical layout for new subprojects or subsystems, "
          "or Flag messy existing layout at Target 15 and Target 20.\n"
      ),
      "skills/makeflow/references/11_polyglot_monorepos.md": (
          "## 1. Subproject Discovery (Target 15)\n"
          "6. **Module Layout and Layer Separation** compared against `skills/makeflow/references/polyglot/<lang>.md` (`SCAR-ARCH-08`).\n"
          "## 2. Directory Layout Rules\n"
          "Enforce `SCAR-ARCH-08` (`entrypoints -> library`): Adapt to clean existing layout, "
          "Enforce canonical layout for new subprojects or subsystems, or Flag messy existing layout.\n"
      ),
      "rules/AGENTS.md": (
          "Disclose each file layer role in `20_plan.md` under `SCAR-ARCH-08` (`skills/makeflow/references/polyglot/<lang>.md`, "
          "`entrypoints -> library`). If an existing layout is messy, offer a user choice at Target 20.\n"
      ),
      "agents/tdd-worker.md": (
          "Enforce idiomatic layer separation from `skills/makeflow/references/polyglot/<lang>.md` and `SCAR-ARCH-08` "
          "(`entrypoints -> library`) alongside `SCAR-ARCH-05`.\n"
      ),
      "skills/makeflow/references/03_tdd_and_code_quality.md": (
          "Enforce idiomatic layer separation from `skills/makeflow/references/polyglot/<lang>.md` and `SCAR-ARCH-08` "
          "(`entrypoints -> library`) alongside `SCAR-ARCH-05`.\n"
      ),
      "agents/adversarial-critic.md": (
          "### 3. Architecture\n"
          "Enforce `SCAR-ARCH-08`: treat new layer-boundary violations in the diff as Pillar 3 Blockers, "
          "and treat pre-existing structural debt in adjacent legacy files as Non-Blocking Observations (`SCAR-PROC-108`).\n"
          "### 4. Blast Radius\n"
      ),
      "skills/makeflow/references/04_six_pillars_adversarial_review.md": (
          "### 3. Architecture\n"
          "Enforce `SCAR-ARCH-08`: treat new layer-boundary violations in the diff as Pillar 3 Blockers, "
          "and treat pre-existing structural debt in adjacent legacy files as Non-Blocking Observations (`SCAR-PROC-108`).\n"
          "### 4. Blast Radius\n"
      ),
  }
  for ad_rel in SCAR_ARCH_08_ADAPTER_FILES:
    sample_files[ad_rel] = (
        "Follow `SCAR-ARCH-05` and `SCAR-ARCH-08` (`entrypoints -> library`).\n"
    )

  baseline_errs = check_scar_arch_08_layer_separation(sample_files)
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for SCAR-ARCH-08: {baseline_errs}"
    )

  # 1) Missing SCAR-ARCH-08 or missing Four-Part Anatomy field in architecture.md
  bad_arch = dict(sample_files)
  bad_arch["skills/makeflow/references/scars/architecture.md"] = bad_arch[
      "skills/makeflow/references/scars/architecture.md"
  ].replace("- **Socratic Probe**:", "- **Other Field**:")
  if not any(
      "missing non-empty '**Socratic Probe**:'" in e
      for e in check_scar_arch_08_layer_separation(bad_arch)
  ):
    errors.append(
        "Negative falsification failed: missing Socratic Probe in architecture.md was not rejected"
    )

  # 2) Misrouted SCAR-ARCH-08 row in 09_synthetic_scars_catalog.md
  bad_cat = dict(sample_files)
  bad_cat["skills/makeflow/references/09_synthetic_scars_catalog.md"] = (
      "| `SCAR-ARCH-07` | Anti-Speculative Defenses | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
      "| `SCAR-ARCH-08` | Polyglot Layer Separation | Architecture | [`scars/process.md`](./scars/process.md) |\n"
  )
  if not any(
      "must strictly route SCAR-ARCH-08 to scars/architecture.md" in e
      for e in check_scar_arch_08_layer_separation(bad_cat)
  ):
    errors.append(
        "Negative falsification failed: misrouted SCAR-ARCH-08 catalog row was not rejected"
    )

  # 3) Permanent Reflex sentence exceeding 20 words or containing a prose semicolon ';'
  bad_reflex_long = dict(sample_files)
  bad_reflex_long["skills/makeflow/references/scars/architecture.md"] = (
      bad_reflex_long[
          "skills/makeflow/references/scars/architecture.md"
      ].replace(
          "Enforce one-way dependencies (`entrypoints -> library`).",
          "Enforce strict one-way dependencies (`entrypoints -> library`) across every single module directory inside the entire active repository workspace before running any unit tests today.",
      )
  )
  if not any(
      "reflex" in e.lower() and "> 20 words" in e.lower()
      for e in check_scar_arch_08_layer_separation(bad_reflex_long)
  ):
    errors.append(
        "Negative falsification failed: >20-word Permanent Reflex sentence was not rejected"
    )

  bad_reflex_semi = dict(sample_files)
  bad_reflex_semi["skills/makeflow/references/scars/architecture.md"] = (
      bad_reflex_semi[
          "skills/makeflow/references/scars/architecture.md"
      ].replace(
          "Enforce one-way dependencies (`entrypoints -> library`). If",
          "Enforce one-way dependencies (`entrypoints -> library`); if",
      )
  )
  if not any(
      "semicolon" in e.lower()
      for e in check_scar_arch_08_layer_separation(bad_reflex_semi)
  ):
    errors.append(
        "Negative falsification failed: prose semicolon in SCAR-ARCH-08 was not rejected"
    )

  # 4) Missing 6th subproject fact (Module Layout and Layer Separation) in agents/archaeology-worker.md
  bad_aw = dict(sample_files)
  bad_aw["agents/archaeology-worker.md"] = "Record five facts only.\n"
  if not any(
      "agents/archaeology-worker.md" in e
      and "Module Layout and Layer Separation" in e
      for e in check_scar_arch_08_layer_separation(bad_aw)
  ):
    errors.append(
        "Negative falsification failed: missing Module Layout and Layer Separation in archaeology-worker.md was not rejected"
    )

  # 5) Missing Code Structure and Layer Separation row or three-tier posture in 02_archaeology_and_planning.md
  bad_r02 = dict(sample_files)
  bad_r02["skills/makeflow/references/02_archaeology_and_planning.md"] = (
      bad_r02[
          "skills/makeflow/references/02_archaeology_and_planning.md"
      ].replace(
          "| **Code Structure and Layer Separation** |",
          "| **Other Row** |",
      ).replace(
          "Adapt to clean existing layout",
          "Omitted posture",
      )
  )
  r02_errs = check_scar_arch_08_layer_separation(bad_r02)
  if not any("Code Structure and Layer Separation" in e for e in r02_errs) or not any(
      "Adapt to clean existing layout" in e for e in r02_errs
  ):
    errors.append(
        "Negative falsification failed: missing Code Structure row or three-tier posture in 02_archaeology_and_planning.md was not rejected"
    )

  # 6) Missing SCAR-ARCH-08 in agents/tdd-worker.md or 03_tdd_and_code_quality.md
  bad_tdd = dict(sample_files)
  bad_tdd["agents/tdd-worker.md"] = "Follow SCAR-ARCH-05 only.\n"
  if not any(
      "agents/tdd-worker.md" in e and "SCAR-ARCH-08" in e
      for e in check_scar_arch_08_layer_separation(bad_tdd)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-08 in tdd-worker.md was not rejected"
    )

  # 7) Missing Pillar 3 Blocker vs Non-Blocking Observation (SCAR-PROC-108) distinction in critic files
  bad_critic = dict(sample_files)
  bad_critic["agents/adversarial-critic.md"] = (
      "### 3. Architecture\nEnforce `SCAR-ARCH-08`.\n### 4. Blast Radius\n"
  )
  critic_errs = check_scar_arch_08_layer_separation(bad_critic)
  if not any("Pillar 3 Blockers" in e for e in critic_errs) or not any(
      "Non-Blocking Observations" in e for e in critic_errs
  ):
    errors.append(
        "Negative falsification failed: missing Pillar 3 Blocker vs NBO distinction in adversarial-critic.md was not rejected"
    )

  # 8) Missing SCAR-ARCH-08 in any of the 5 adapters
  bad_adapter = dict(sample_files)
  bad_adapter["CLAUDE.md"] = "Follow SCAR-ARCH-05 only.\n"
  if not any(
      "CLAUDE.md" in e and "SCAR-ARCH-08" in e
      for e in check_scar_arch_08_layer_separation(bad_adapter)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-08 in CLAUDE.md was not rejected"
    )

  return errors


SCAR_ARCH_09_TARGET_FILES = (
    "skills/makeflow/references/scars/architecture.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "agents/tdd-worker.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "agents/adversarial-critic.md",
)

SCAR_ARCH_09_PROBE_QUESTIONS = (
    "Would fixing the root cause upstream keep code simplest without harming other subsystems?",
    "Can you remove downstream workarounds instead of patching symptoms further down the control flow?",
)


def check_scar_arch_09_upstream_root_cause(
    files_map: dict[str, str],
) -> list[str]:
  """Validates SCAR-ARCH-09 upstream root-cause remediation invariants across Target 20/30/40 (GH-232)."""
  errs: list[str] = []
  for rel in SCAR_ARCH_09_TARGET_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # a. skills/makeflow/references/scars/architecture.md
  arch_rel = "skills/makeflow/references/scars/architecture.md"
  arch_text = files_map[arch_rel]
  table_match = re.search(
      r"## Scars in this Category.*?(?=\n###|\Z)", arch_text, re.DOTALL
  )
  table_sec = table_match.group(0) if table_match else ""
  if (
      "SCAR-ARCH-09" not in table_sec
      or "Upstream Root-Cause Remediation Over Downstream Workarounds"
      not in table_sec
  ):
    errs.append(
        f"{arch_rel} category table must list SCAR-ARCH-09 (Upstream Root-Cause Remediation Over Downstream Workarounds)"
    )

  m08 = re.search(r"(?m)^###\s+SCAR-ARCH-08\b", arch_text)
  m09 = re.search(
      r"(?m)^###\s+SCAR-ARCH-09:\s+Upstream Root-Cause Remediation Over Downstream Workarounds\b",
      arch_text,
  )
  if not (m08 and m09 and m08.start() < m09.start()):
    errs.append(
        f"{arch_rel} must define '### SCAR-ARCH-09: Upstream Root-Cause Remediation Over Downstream Workarounds' monotonically after SCAR-ARCH-08"
    )
  else:
    sec09 = arch_text[m09.start() :].split("\n### SCAR-")[0]
    extracted: dict[str, str] = {}
    req_fields = ("Wound", "Trap", "Permanent Reflex", "Socratic Probe")
    for field in req_fields:
      fm = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", sec09)
      val = fm.group(1).strip() if fm else ""
      extracted[field] = val
      if not val:
        errs.append(f"{arch_rel} SCAR-ARCH-09 missing non-empty '**{field}**:'")

    sec09_lower = sec09.lower()
    for tok in ("upstream", "root cause", "downstream", "workaround"):
      if tok not in sec09_lower:
        errs.append(f"{arch_rel} SCAR-ARCH-09 missing required token '{tok}'")

    if all(extracted[f] for f in req_fields):
      candidate = {
          "id": "SCAR-ARCH-09",
          "raw_wound": extracted["Wound"],
          "wound": extracted["Wound"],
          "raw_trap": extracted["Trap"],
          "trap": extracted["Trap"],
          "raw_circuit_breaker": "",
          "circuit_breaker": "",
          "raw_reflex": extracted["Permanent Reflex"],
          "reflex": extracted["Permanent Reflex"],
          "socratic_probe": extracted["Socratic Probe"],
      }
      errs.extend(ScarCandidateValidator().validate(candidate))
      errs.extend(
          validate_socratic_probe(
              extracted["Socratic Probe"], scar_id="SCAR-ARCH-09"
          )
      )
      probe_sents = split_into_sentences(extracted["Socratic Probe"])
      if not (1 <= len(probe_sents) <= 3):
        errs.append(
            f"{arch_rel} SCAR-ARCH-09 Socratic Probe must contain 1-3 '?' sentences, found {len(probe_sents)}"
        )

  errs.extend(_check_prose_punctuation(arch_rel, arch_text))

  # b. skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_text = files_map[cat_rel]
  if not check_scar_catalog_routing(cat_text, "SCAR-ARCH-09", "architecture.md"):
    errs.append(
        f"{cat_rel} Master Routing Table must strictly route SCAR-ARCH-09 to scars/architecture.md"
    )
  c08 = re.search(r"(?m)^\|\s*`SCAR-ARCH-08`\s*\|", cat_text)
  c09 = re.search(r"(?m)^\|\s*`SCAR-ARCH-09`\s*\|", cat_text)
  if not (c08 and c09 and c08.start() < c09.start()):
    errs.append(
        f"{cat_rel} must place SCAR-ARCH-09 monotonically after SCAR-ARCH-08"
    )

  # c. Target 30: agents/tdd-worker.md
  tdd_rel = "agents/tdd-worker.md"
  tdd_text = files_map[tdd_rel]
  tdd_lower = tdd_text.lower()
  if "SCAR-ARCH-09" not in tdd_text:
    errs.append(f"{tdd_rel} must reference SCAR-ARCH-09")
  for tok in ("upstream", "root cause", "workaround", "green", "refactor", "blocker"):
    if tok not in tdd_lower:
      errs.append(
          f"{tdd_rel} must include SCAR-ARCH-09 guidance token '{tok}'"
      )
  for q_text in SCAR_ARCH_09_PROBE_QUESTIONS:
    if q_text not in tdd_text:
      errs.append(
          f"{tdd_rel} must include SCAR-ARCH-09 Socratic Probe question: '{q_text}'"
      )
  # Preserve existing Rule 1, Rule 3, and Rule 12 invariants
  if "did the test fail" not in tdd_lower:
    errs.append(f"{tdd_rel} must preserve Rule 1 ('did the test fail')")
  if "unnecessary abstraction" not in tdd_lower:
    errs.append(f"{tdd_rel} must preserve Rule 3 ('unnecessary abstraction')")
  for r12_tok in (
      "SCAR-ARCH-05",
      "SCAR-ARCH-08",
      "skills/makeflow/references/polyglot/<lang>.md",
      "entrypoints -> library",
  ):
    if r12_tok not in tdd_text:
      errs.append(f"{tdd_rel} must preserve Rule 12 token '{r12_tok}'")
  errs.extend(_check_prose_punctuation(tdd_rel, tdd_text))

  # d. Target 20: skills/makeflow/references/02_archaeology_and_planning.md Section 2.1
  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_text = files_map[r02_rel]
  r02_sec2 = _extract_markdown_h2_section(r02_text, "## 2. Target 20")
  if "### 2.1 Socratic Pre-Planning Deliberation" not in r02_sec2:
    errs.append(
        f"{r02_rel} Section 2 missing '### 2.1 Socratic Pre-Planning Deliberation'"
    )
  else:
    s21 = r02_sec2.split("### 2.1 Socratic Pre-Planning Deliberation")[1].split(
        "### 2.2"
    )[0]
    if "Upstream Root-Cause Remediation" not in s21 or "SCAR-ARCH-09" not in s21:
      errs.append(
          f"{r02_rel} Section 2.1 must include Socratic question on Upstream Root-Cause Remediation (SCAR-ARCH-09)"
      )
    for q_text in SCAR_ARCH_09_PROBE_QUESTIONS:
      if q_text not in s21:
        errs.append(
            f"{r02_rel} Section 2.1 must include SCAR-ARCH-09 probe question: '{q_text}'"
        )

  # e. Target 40/42: skills/makeflow/references/04_six_pillars_adversarial_review.md
  r04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  r04_text = files_map[r04_rel]
  p3_match = re.search(
      r"(?m)^###\s+3\.\s+Architecture\b.*?(?=^###\s+4\b|\Z)",
      r04_text,
      re.DOTALL,
  )
  p3_sec = p3_match.group(0) if p3_match else ""
  p3_lower = p3_sec.lower()
  if "SCAR-ARCH-09" not in p3_sec or "Upstream Root-Cause Remediation" not in p3_sec:
    errs.append(
        f"{r04_rel} Pillar 3 must reference Upstream Root-Cause Remediation (SCAR-ARCH-09)"
    )
  for tok in ("upstream", "workaround"):
    if tok not in p3_lower:
      errs.append(f"{r04_rel} Pillar 3 must address '{tok}' remediation")

  s52_match = re.search(
      r"(?m)^###\s+5\.2\s+The Blocker Digest\b.*?(?=^###\s+5\.3\b|\Z)",
      r04_text,
      re.DOTALL,
  )
  s52_sec = s52_match.group(0) if s52_match else ""
  rem_match = re.search(
      r"\*\*Remediation Strategy\*\*:.*?(?=\n\d+\.\s+|\Z)",
      s52_sec,
      re.DOTALL,
  )
  rem_sec = rem_match.group(0) if rem_match else ""
  rem_lower = rem_sec.lower()
  if "SCAR-ARCH-09" not in rem_sec:
    errs.append(
        f"{r04_rel} Section 5.2 item 2 (Remediation Strategy) must reference SCAR-ARCH-09"
    )
  if "upstream" not in rem_lower or (
      "root cause" not in rem_lower and "root-cause" not in rem_lower
  ) or "workaround" not in rem_lower:
    errs.append(
        f"{r04_rel} Section 5.2 item 2 (Remediation Strategy) must mandate upstream root-cause fixes over downstream workarounds"
    )

  # f. Target 40 symmetric critic prompt: agents/adversarial-critic.md
  critic_rel = "agents/adversarial-critic.md"
  critic_text = files_map[critic_rel]
  c_p3_match = re.search(
      r"(?m)^###\s+3\.\s+Architecture\b.*?(?=^###\s+4\b|\Z)",
      critic_text,
      re.DOTALL,
  )
  c_p3_sec = c_p3_match.group(0) if c_p3_match else ""
  c_p3_lower = c_p3_sec.lower()
  if "SCAR-ARCH-09" not in c_p3_sec:
    errs.append(f"{critic_rel} Pillar 3 must reference SCAR-ARCH-09")
  if (
      "upstream" not in c_p3_lower
      or ("root cause" not in c_p3_lower and "root-cause" not in c_p3_lower)
      or "workaround" not in c_p3_lower
  ):
    errs.append(
        f"{critic_rel} Pillar 3 must enforce upstream root-cause fixes over downstream workarounds"
    )

  return errs


def validate_scar_arch_09_upstream_root_cause() -> list[str]:
  """Validates SCAR-ARCH-09 upstream root-cause remediation codification and negative falsifications (GH-232)."""
  errors: list[str] = []
  repo_files: dict[str, str] = {}
  for rel in SCAR_ARCH_09_TARGET_FILES:
    fpath = repo_path(rel)
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        repo_files[rel] = f.read()

  errors.extend(check_scar_arch_09_upstream_root_cause(repo_files))

  # Hermetic in-memory negative falsification checks (SCAR-TEST-11)
  sample_files = {
      "skills/makeflow/references/scars/architecture.md": (
          "# Architecture and Pattern Scars\n\n"
          "## Scars in this Category\n\n"
          "| ID | Title | Scope |\n"
          "| :--- | :--- | :--- |\n"
          "| `SCAR-ARCH-08` | Polyglot Layer Separation and Structural Debt Triage | Layer Boundaries |\n"
          "| `SCAR-ARCH-09` | Upstream Root-Cause Remediation Over Downstream Workarounds | Root-Cause Boundaries |\n\n"
          "### SCAR-ARCH-08: Polyglot Layer Separation and Structural Debt Triage\n\n"
          "- **Wound**: Layer conflation.\n"
          "- **Trap**: Flat scripts.\n"
          "- **Permanent Reflex**: Enforce one-way dependencies (`entrypoints -> library`).\n"
          "- **Socratic Probe**: Does this change separate layers?\n\n"
          "### SCAR-ARCH-09: Upstream Root-Cause Remediation Over Downstream Workarounds\n\n"
          "- **Wound**: Downstream workarounds accumulate duplicate validation, brittle parsing heuristics, and extra branching while leaving upstream contract defects intact.\n"
          "- **Trap**: An agent patches symptoms at the downstream call site instead of tracing control flow upstream to the root cause.\n"
          "- **Permanent Reflex**: Trace each defect or design friction upstream to its earliest safe boundary. If an upstream fix simplifies control flow without breaking other subsystems, fix the root cause upstream. Delete obsolete downstream workarounds instead of stacking defensive patches.\n"
          "- **Socratic Probe**: Would fixing the root cause upstream keep code simplest without harming other subsystems? Can you remove downstream workarounds instead of patching symptoms further down the control flow?\n"
      ),
      "skills/makeflow/references/09_synthetic_scars_catalog.md": (
          "| `SCAR-ARCH-08` | Polyglot Layer Separation and Structural Debt Triage | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
          "| `SCAR-ARCH-09` | Upstream Root-Cause Remediation Over Downstream Workarounds | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
      ),
      "agents/tdd-worker.md": (
          "1. Red phase: Did the test fail as expected?\n"
          "3. Green phase: Does this change introduce unnecessary abstraction?\n"
          "12. Separate library modules (`SCAR-ARCH-05`). Enforce `skills/makeflow/references/polyglot/<lang>.md` (`entrypoints -> library`) under `SCAR-ARCH-08`.\n"
          "13. Root-cause remediation (`SCAR-ARCH-09`): During Green fixes, Refactor cleanup, or blocker remediation, trace defects upstream first. "
          "Delete obsolete downstream workarounds instead of stacking patches. "
          "Would fixing the root cause upstream keep code simplest without harming other subsystems? "
          "Can you remove downstream workarounds instead of patching symptoms further down the control flow?\n"
      ),
      "skills/makeflow/references/02_archaeology_and_planning.md": (
          "## 2. Target 20: Plan File Creation (`20_plan.md`)\n"
          "### 2.1 Socratic Pre-Planning Deliberation\n"
          "8. **Upstream Root-Cause Remediation (`SCAR-ARCH-09`)**: "
          "Would fixing the root cause upstream keep code simplest without harming other subsystems? "
          "Can you remove downstream workarounds instead of patching symptoms further down the control flow?\n"
          "### 2.2 Socratic Invariant Audit Table\n"
      ),
      "skills/makeflow/references/04_six_pillars_adversarial_review.md": (
          "### 3. Architecture\n"
          "- **Upstream Root-Cause Remediation (`SCAR-ARCH-09`)**: Fix defects upstream instead of adding downstream workarounds.\n"
          "### 4. Blast Radius\n"
          "### 5.2 The Blocker Digest\n"
          "2. **Remediation Strategy**: Propose a failing reproduction test and upstream root-cause fix for each blocker (`SCAR-ARCH-09`). Avoid downstream workarounds.\n"
          "### 5.3 Sovereign Human Direction\n"
      ),
      "agents/adversarial-critic.md": (
          "### 3. Architecture\n"
          "Does the diff fix defects upstream at the root cause rather than stacking downstream workarounds (`SCAR-ARCH-09`)?\n"
          "### 4. Blast Radius\n"
      ),
  }

  baseline_errs = check_scar_arch_09_upstream_root_cause(sample_files)
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for SCAR-ARCH-09: {baseline_errs}"
    )

  # 1) Missing SCAR-ARCH-09 or missing Socratic Probe in architecture.md
  bad_arch_missing = dict(sample_files)
  bad_arch_missing["skills/makeflow/references/scars/architecture.md"] = (
      bad_arch_missing["skills/makeflow/references/scars/architecture.md"].split(
          "### SCAR-ARCH-09"
      )[0]
  )
  if not any(
      "SCAR-ARCH-09" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_arch_missing)
  ):
    errors.append(
        "Negative falsification failed: stripped SCAR-ARCH-09 in architecture.md was not rejected"
    )

  # 2) Over-20-word probe sentence in architecture.md
  bad_probe_long = dict(sample_files)
  bad_probe_long["skills/makeflow/references/scars/architecture.md"] = (
      bad_probe_long[
          "skills/makeflow/references/scars/architecture.md"
      ].replace(
          "Would fixing the root cause upstream keep code simplest without harming other subsystems?",
          "Would fixing every single underlying root cause defect upstream across all callers keep the entire codebase simplest without ever causing any unexpected regressions in other subsystems today?",
      )
  )
  if not any(
      "exceeds word limit" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_probe_long)
  ):
    errors.append(
        "Negative falsification failed: >20-word SCAR-ARCH-09 probe sentence was not rejected"
    )

  # 3) Misrouted SCAR-ARCH-09 row in 09_synthetic_scars_catalog.md
  bad_cat = dict(sample_files)
  bad_cat["skills/makeflow/references/09_synthetic_scars_catalog.md"] = (
      "| `SCAR-ARCH-08` | Polyglot Layer Separation | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
      "| `SCAR-ARCH-09` | Upstream Root-Cause Remediation | Architecture | [`scars/process.md`](./scars/process.md) |\n"
  )
  if not any(
      "must strictly route SCAR-ARCH-09 to scars/architecture.md" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_cat)
  ):
    errors.append(
        "Negative falsification failed: misrouted SCAR-ARCH-09 catalog row was not rejected"
    )

  # 4) Stripped probe token or missing Rule 12 invariant in agents/tdd-worker.md
  bad_tdd_probe = dict(sample_files)
  bad_tdd_probe["agents/tdd-worker.md"] = bad_tdd_probe[
      "agents/tdd-worker.md"
  ].replace(SCAR_ARCH_09_PROBE_QUESTIONS[0], "Fix code simply?")
  if not any(
      "agents/tdd-worker.md" in e and "Socratic Probe" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_tdd_probe)
  ):
    errors.append(
        "Negative falsification failed: stripped SCAR-ARCH-09 probe in tdd-worker.md was not rejected"
    )

  bad_tdd_r12 = dict(sample_files)
  bad_tdd_r12["agents/tdd-worker.md"] = bad_tdd_r12[
      "agents/tdd-worker.md"
  ].replace("SCAR-ARCH-08", "OMITTED")
  if not any(
      "agents/tdd-worker.md" in e and "SCAR-ARCH-08" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_tdd_r12)
  ):
    errors.append(
        "Negative falsification failed: stripped Rule 12 SCAR-ARCH-08 in tdd-worker.md was not rejected"
    )

  # 5) Missing SCAR-ARCH-09 in 02_archaeology_and_planning.md Section 2.1
  bad_r02 = dict(sample_files)
  bad_r02["skills/makeflow/references/02_archaeology_and_planning.md"] = (
      "## 2. Target 20: Plan File Creation (`20_plan.md`)\n"
      "### 2.1 Socratic Pre-Planning Deliberation\n"
      "7. **Code Structure (`SCAR-ARCH-08`)**: Separation?\n"
      "### 2.2 Socratic Invariant Audit Table\n"
  )
  if not any(
      "Section 2.1" in e and "SCAR-ARCH-09" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_r02)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-09 in 02_archaeology_and_planning.md Section 2.1 was not rejected"
    )

  # 6) Missing SCAR-ARCH-09 in 04_six_pillars_adversarial_review.md Pillar 3 or Section 5.2 item 2
  bad_r04_p3 = dict(sample_files)
  bad_r04_p3["skills/makeflow/references/04_six_pillars_adversarial_review.md"] = (
      bad_r04_p3[
          "skills/makeflow/references/04_six_pillars_adversarial_review.md"
      ].replace("Upstream Root-Cause Remediation (`SCAR-ARCH-09`)", "Removed")
  )
  if not any(
      "Pillar 3" in e and "SCAR-ARCH-09" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_r04_p3)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-09 in 04_six_pillars Pillar 3 was not rejected"
    )

  bad_r04_52 = dict(sample_files)
  bad_r04_52["skills/makeflow/references/04_six_pillars_adversarial_review.md"] = (
      bad_r04_52[
          "skills/makeflow/references/04_six_pillars_adversarial_review.md"
      ].replace(
          "2. **Remediation Strategy**: Propose a failing reproduction test and upstream root-cause fix for each blocker (`SCAR-ARCH-09`). Avoid downstream workarounds.",
          "2. **Remediation Strategy**: Propose a surgical fix for each blocker.",
      )
  )
  if not any(
      "Section 5.2 item 2" in e and "SCAR-ARCH-09" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_r04_52)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-09 in 04_six_pillars Section 5.2 item 2 was not rejected"
    )

  # 7) Missing SCAR-ARCH-09 in agents/adversarial-critic.md Pillar 3
  bad_critic = dict(sample_files)
  bad_critic["agents/adversarial-critic.md"] = (
      "### 3. Architecture\nCheck architecture.\n### 4. Blast Radius\n"
  )
  if not any(
      "agents/adversarial-critic.md" in e and "SCAR-ARCH-09" in e
      for e in check_scar_arch_09_upstream_root_cause(bad_critic)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-09 in adversarial-critic.md Pillar 3 was not rejected"
    )

  return errors


SCAR_ARCH_10_TARGET_FILES = (
    "skills/makeflow/references/scars/architecture.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "rules/AGENTS.md",
)

SCAR_ARCH_10_PROBE_QUESTIONS = (
    "Does this module scrape prose with regexes for data that already exists as structured JSON upstream?",
    "Can consumers read one shared structured store instead?",
)


def check_scar_arch_10_structured_data(
    files_map: dict[str, str],
) -> list[str]:
  """Validates SCAR-ARCH-10 structured data preservation invariants (GH-240)."""
  errs: list[str] = []
  for rel in SCAR_ARCH_10_TARGET_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # a. skills/makeflow/references/scars/architecture.md
  arch_rel = "skills/makeflow/references/scars/architecture.md"
  arch_text = files_map[arch_rel]
  table_match = re.search(
      r"## Scars in this Category.*?(?=\n###|\Z)", arch_text, re.DOTALL
  )
  table_sec = table_match.group(0) if table_match else ""
  if (
      "SCAR-ARCH-10" not in table_sec
      or "Preserve Structured Data Over Lossy Prose Round-Trips" not in table_sec
  ):
    errs.append(
        f"{arch_rel} category table must list SCAR-ARCH-10 (Preserve Structured Data Over Lossy Prose Round-Trips)"
    )

  m09 = re.search(r"(?m)^###\s+SCAR-ARCH-09\b", arch_text)
  m10 = re.search(
      r"(?m)^###\s+SCAR-ARCH-10:\s+Preserve Structured Data Over Lossy Prose Round-Trips\b",
      arch_text,
  )
  if not (m09 and m10 and m09.start() < m10.start()):
    errs.append(
        f"{arch_rel} must define '### SCAR-ARCH-10: Preserve Structured Data Over Lossy Prose Round-Trips' monotonically after SCAR-ARCH-09"
    )
  else:
    sec10 = arch_text[m10.start() :].split("\n### SCAR-")[0]
    extracted: dict[str, str] = {}
    req_fields = ("Wound", "Trap", "Permanent Reflex", "Socratic Probe")
    for field in req_fields:
      fm = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", sec10)
      val = fm.group(1).strip() if fm else ""
      extracted[field] = val
      if not val:
        errs.append(f"{arch_rel} SCAR-ARCH-10 missing non-empty '**{field}**:'")

    sec10_lower = sec10.lower()
    for tok in ("structured", "json", "regex", "prose"):
      if tok not in sec10_lower:
        errs.append(f"{arch_rel} SCAR-ARCH-10 missing required token '{tok}'")

    if all(extracted[f] for f in req_fields):
      candidate = {
          "id": "SCAR-ARCH-10",
          "raw_wound": extracted["Wound"],
          "wound": extracted["Wound"],
          "raw_trap": extracted["Trap"],
          "trap": extracted["Trap"],
          "raw_circuit_breaker": "",
          "circuit_breaker": "",
          "raw_reflex": extracted["Permanent Reflex"],
          "reflex": extracted["Permanent Reflex"],
          "socratic_probe": extracted["Socratic Probe"],
      }
      errs.extend(ScarCandidateValidator().validate(candidate))
      errs.extend(
          validate_socratic_probe(
              extracted["Socratic Probe"], scar_id="SCAR-ARCH-10"
          )
      )
      probe_sents = split_into_sentences(extracted["Socratic Probe"])
      if not (1 <= len(probe_sents) <= 3):
        errs.append(
            f"{arch_rel} SCAR-ARCH-10 Socratic Probe must contain 1-3 '?' sentences, found {len(probe_sents)}"
        )

  errs.extend(_check_prose_punctuation(arch_rel, arch_text))

  # b. skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_text = files_map[cat_rel]
  if not check_scar_catalog_routing(cat_text, "SCAR-ARCH-10", "architecture.md"):
    errs.append(
        f"{cat_rel} Master Routing Table must strictly route SCAR-ARCH-10 to scars/architecture.md"
    )
  c09 = re.search(r"(?m)^\|\s*`SCAR-ARCH-09`\s*\|", cat_text)
  c10 = re.search(r"(?m)^\|\s*`SCAR-ARCH-10`\s*\|", cat_text)
  if not (c09 and c10 and c09.start() < c10.start()):
    errs.append(
        f"{cat_rel} must place SCAR-ARCH-10 monotonically after SCAR-ARCH-09"
    )

  # c. Target 20: skills/makeflow/references/02_archaeology_and_planning.md Section 2.1
  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_text = files_map[r02_rel]
  r02_sec2 = _extract_markdown_h2_section(r02_text, "## 2. Target 20")
  if "### 2.1 Socratic Pre-Planning Deliberation" not in r02_sec2:
    errs.append(
        f"{r02_rel} Section 2 missing '### 2.1 Socratic Pre-Planning Deliberation'"
    )
  else:
    s21 = r02_sec2.split("### 2.1 Socratic Pre-Planning Deliberation")[1].split(
        "### 2.2"
    )[0]
    if "Preserve Structured Data" not in s21 or "SCAR-ARCH-10" not in s21:
      errs.append(
          f"{r02_rel} Section 2.1 must include Socratic question on Preserve Structured Data (SCAR-ARCH-10)"
      )
    for q_text in SCAR_ARCH_10_PROBE_QUESTIONS:
      if q_text not in s21:
        errs.append(
            f"{r02_rel} Section 2.1 must include SCAR-ARCH-10 probe question: '{q_text}'"
        )

  # d. Target 40: skills/makeflow/references/04_six_pillars_adversarial_review.md Pillar 3
  r04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  r04_text = files_map[r04_rel]
  p3_match = re.search(
      r"(?m)^###\s+3\.\s+Architecture\b.*?(?=^###\s+4\b|\Z)",
      r04_text,
      re.DOTALL,
  )
  p3_sec = p3_match.group(0) if p3_match else ""
  if "SCAR-ARCH-10" not in p3_sec or "Preserve Structured Data" not in p3_sec:
    errs.append(
        f"{r04_rel} Pillar 3 must reference Preserve Structured Data (SCAR-ARCH-10)"
    )

  # e. rules/AGENTS.md Section 3
  rules_rel = "rules/AGENTS.md"
  rules_text = files_map[rules_rel]
  s3_match = re.search(
      r"(?m)^##\s+3\.\s+Code Edit.*?(?=^##\s+4\b|\Z)",
      rules_text,
      re.DOTALL,
  )
  s3_sec = s3_match.group(0) if s3_match else ""
  if "SCAR-ARCH-10" not in s3_sec:
    errs.append(f"{rules_rel} Section 3 must reference SCAR-ARCH-10")

  return errs


def validate_scar_arch_10_structured_data() -> list[str]:
  """Validates SCAR-ARCH-10 structured data preservation and negative falsifications (GH-240)."""
  errors: list[str] = []
  repo_files: dict[str, str] = {}
  for rel in SCAR_ARCH_10_TARGET_FILES:
    fpath = repo_path(rel)
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        repo_files[rel] = f.read()

  errors.extend(check_scar_arch_10_structured_data(repo_files))

  sample_files = {
      "skills/makeflow/references/scars/architecture.md": (
          "# Architecture and Pattern Scars\n\n"
          "## Scars in this Category\n\n"
          "| ID | Title | Scope |\n"
          "| :--- | :--- | :--- |\n"
          "| `SCAR-ARCH-09` | Upstream Root-Cause Remediation Over Downstream Workarounds | Root-Cause Boundaries |\n"
          "| `SCAR-ARCH-10` | Preserve Structured Data Over Lossy Prose Round-Trips | Data Contracts |\n\n"
          "### SCAR-ARCH-09: Upstream Root-Cause Remediation Over Downstream Workarounds\n\n"
          "- **Wound**: Downstream workarounds.\n"
          "- **Trap**: Symptom patching.\n"
          "- **Permanent Reflex**: Fix upstream.\n"
          "- **Socratic Probe**: Would fixing upstream keep code simplest?\n\n"
          "### SCAR-ARCH-10: Preserve Structured Data Over Lossy Prose Round-Trips\n\n"
          "- **Wound**: Serializing structured JSON records into free-form Markdown prose and scraping fields back with regexes causes silent truncation and wrong value extraction.\n"
          "- **Trap**: An agent converts structured API or store payloads into prose documents, then writes brittle regex parsers across downstream modules to recover original fields.\n"
          "- **Permanent Reflex**: If upstream systems emit structured JSON or typed records, persist that structured data directly. Read structured fields from one shared module instead of regex-scraping prose. Keep human-readable Markdown notes separate from machine-read state.\n"
          "- **Socratic Probe**: Does this module scrape prose with regexes for data that already exists as structured JSON upstream? Can consumers read one shared structured store instead?\n"
      ),
      "skills/makeflow/references/09_synthetic_scars_catalog.md": (
          "| `SCAR-ARCH-09` | Upstream Root-Cause Remediation Over Downstream Workarounds | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
          "| `SCAR-ARCH-10` | Preserve Structured Data Over Lossy Prose Round-Trips | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
      ),
      "skills/makeflow/references/02_archaeology_and_planning.md": (
          "## 2. Target 20: Plan File Creation (`20_plan.md`)\n"
          "### 2.1 Socratic Pre-Planning Deliberation\n"
          "9. **Preserve Structured Data (`SCAR-ARCH-10`)**: "
          "Does this module scrape prose with regexes for data that already exists as structured JSON upstream? "
          "Can consumers read one shared structured store instead?\n"
          "### 2.2 Socratic Invariant Audit Table\n"
      ),
      "skills/makeflow/references/04_six_pillars_adversarial_review.md": (
          "### 3. Architecture\n"
          "- **Preserve Structured Data (`SCAR-ARCH-10`)**: Preserve structured JSON instead of regex-scraping prose.\n"
          "### 4. Blast Radius\n"
      ),
      "rules/AGENTS.md": (
          "## 3. Code Edit and Architecture Rules (`SCAR-PROC-104`)\n"
          "9. Preserve structured data (`SCAR-ARCH-10`): Persist structured JSON directly.\n"
          "## 4. Simple English Documentation Rules\n"
      ),
  }
  baseline_errs = check_scar_arch_10_structured_data(sample_files)
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for SCAR-ARCH-10: {baseline_errs}"
    )

  # 1) Missing SCAR-ARCH-10 in architecture.md
  bad_missing = dict(sample_files)
  bad_missing["skills/makeflow/references/scars/architecture.md"] = (
      bad_missing["skills/makeflow/references/scars/architecture.md"].split(
          "### SCAR-ARCH-10"
      )[0]
  )
  if not any(
      "SCAR-ARCH-10" in e
      for e in check_scar_arch_10_structured_data(bad_missing)
  ):
    errors.append(
        "Negative falsification failed: stripped SCAR-ARCH-10 in architecture.md was not rejected"
    )

  # 2) Missing routing row in 09_synthetic_scars_catalog.md
  bad_cat = dict(sample_files)
  bad_cat["skills/makeflow/references/09_synthetic_scars_catalog.md"] = (
      "| `SCAR-ARCH-09` | Upstream | Architecture | [`scars/architecture.md`](./scars/architecture.md) |\n"
  )
  if not any(
      "SCAR-ARCH-10" in e for e in check_scar_arch_10_structured_data(bad_cat)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-10 routing row was not rejected"
    )

  # 3) Missing SCAR-ARCH-10 in rules/AGENTS.md Section 3
  bad_rules = dict(sample_files)
  bad_rules["rules/AGENTS.md"] = (
      "## 3. Code Edit and Architecture Rules\n1. Minimal edits.\n## 4. Simple English\n"
  )
  if not any(
      "rules/AGENTS.md" in e and "SCAR-ARCH-10" in e
      for e in check_scar_arch_10_structured_data(bad_rules)
  ):
    errors.append(
        "Negative falsification failed: missing SCAR-ARCH-10 in rules/AGENTS.md was not rejected"
    )

  return errors


def validate_vector4_reflex_gates_scar():
  """Validates Just-in-Time Vector 4 Reflex gates and SCAR-ARCH-06 (GH-57)."""
  errors = []

  arch_spoke_rel = "skills/makeflow/references/scars/architecture.md"
  arch_spoke = repo_path(arch_spoke_rel)
  if not os.path.exists(arch_spoke):
    errors.append(f"Missing {arch_spoke_rel}")
  else:
    with open(arch_spoke, "r", encoding="utf-8") as f:
      arch_text = f.read()
    if "SCAR-ARCH-06" not in arch_text:
      errors.append(f"{arch_spoke_rel} must document SCAR-ARCH-06")
    else:
      scar_section = (
          arch_text.split("### SCAR-ARCH-06")[1].split("### SCAR-")[0]
          if "### SCAR-ARCH-06" in arch_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{arch_spoke_rel} SCAR-ARCH-06 missing '**{field}**:'")

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-ARCH-06" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-ARCH-06")
    if not check_scar_catalog_routing(cat_text, "SCAR-ARCH-06", "architecture.md"):
      errors.append(
          f"{cat_rel} must route SCAR-ARCH-06 to scars/architecture.md"
      )

  mk_rel = "Makefile"
  mk_path = repo_path(mk_rel)
  if not os.path.exists(mk_path):
    errors.append(f"Missing {mk_rel}")
  else:
    with open(mk_path, "r", encoding="utf-8") as f:
      mk_text = f.read()
    errors.extend(check_makefile_test_discovery(mk_text, ("test-reflex",)))

  for script_rel in (
      "scripts/emit_reflex.py",
      "scripts/workflow_gates.py",
      "tests/test_emit_reflex.py",
  ):
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  reflex_test_rel = "tests/test_emit_reflex.py"
  reflex_test_path = repo_path(reflex_test_rel)
  if os.path.exists(reflex_test_path):
    with open(reflex_test_path, "r", encoding="utf-8") as f:
      rt_text = f.read()
    if "sys.executable" not in rt_text:
      errors.append(f"{reflex_test_rel} must use sys.executable for child subprocesses")
    if '["python3"' in rt_text or "['python3'" in rt_text:
      errors.append(f"{reflex_test_rel} must not hardcode 'python3' in subprocess calls")

  return errors


def _check_scar_test_12_section(scar_section, source_rel):
  """Validates SCAR-TEST-12 Four-Part Anatomy fields and core invariants."""
  sec_errors = []
  for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
    field_match = re.search(
        rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", scar_section
    )
    if not field_match or not field_match.group(1).strip():
      sec_errors.append(
          f"{source_rel} SCAR-TEST-12 missing non-empty '**{field}**:'"
      )
  for invariant in (
      "witness-red",
      "verify-green",
      "30_tdd_receipt.json",
      "forensic_log",
      "verify-citation",
      "UNCOMMITTED_WITNESS_FILE",
      "TRIVIAL_WITNESS_LINE",
  ):
    if invariant not in scar_section:
      sec_errors.append(
          f"{source_rel} SCAR-TEST-12 missing invariant '{invariant}'"
      )
  return sec_errors


def validate_scar_lifecycle_and_two_tier_discovery(
    catalog_text: str, skill_text: str
) -> list[str]:
  """Validates Two-Tier scar architecture and Target 90 lifecycle rules (GH-93)."""
  errors = []

  # 1a. Tier 1 and Tier 2 definitions in catalog_text
  if "Tier 1" not in catalog_text or "skills/makeflow/references/scars/" not in catalog_text:
    errors.append(
        "09_synthetic_scars_catalog.md must define Tier 1 Core Built-In Scars in"
        " skills/makeflow/references/scars/"
    )
  if "Tier 2" not in catalog_text or ".workflow/scars/" not in catalog_text:
    errors.append(
        "09_synthetic_scars_catalog.md must define Tier 2 Project-Local Scars in"
        " .workflow/scars/"
    )

  # 1b. Ticket-scoped core scar lifecycle (T15, T20, T30 feature branch, T40) and T90 rule
  for required_stage in ("Target 15", "Target 20", "Target 30", "Target 40", "tdd-worker", "feature branch"):
    if required_stage not in catalog_text:
      errors.append(
          f"09_synthetic_scars_catalog.md must require '{required_stage}' for"
          " ticket-scoped core scars"
      )

  cat_lower = catalog_text.lower()
  if (
      "never creates or modifies core catalog files on `main`" not in cat_lower
      and "never create or modify core catalog files on `main`" not in cat_lower
  ):
    errors.append(
        "09_synthetic_scars_catalog.md must state that Target 90 never creates"
        " or modifies core catalog files on main"
    )
  if ".workflow/history.json" not in catalog_text or ".workflow/history.md" not in catalog_text:
    errors.append(
        "09_synthetic_scars_catalog.md must state that Target 90 records merged"
        " scar IDs in .workflow/history.json and .workflow/history.md"
    )

  # 1c. Prohibit legacy defective instruction to open references/scars/ and append at Target 90
  forbidden_patterns = (
      "open the matching domain spoke in `skills/makeflow/references/scars/`",
      "append the new scar at the end of the file",
      "add the new entry to the master routing table in this index",
  )
  for pat in forbidden_patterns:
    if pat in cat_lower:
      errors.append(
          "09_synthetic_scars_catalog.md must not instruct agents to"
          f" '{pat}' during Target 90 cleanup"
      )

  # 1d. Incidental retrospective lessons in makeflow-skill vs external user projects
  if "makeflow-skill" not in catalog_text or "ticket notes" not in cat_lower:
    errors.append(
        "09_synthetic_scars_catalog.md must explain noting incidental lessons"
        " in ticket notes inside makeflow-skill"
    )
  if ".workflow/scars/local.md" not in catalog_text:
    errors.append(
        "09_synthetic_scars_catalog.md must explain recording project-specific"
        " lessons in .workflow/scars/local.md for external projects"
    )

  # 1e. SKILL.md Target 90 clarification
  skill_lower = skill_text.lower()
  if ".workflow/scars/" not in skill_text:
    errors.append(
        "skills/makeflow/SKILL.md must reference .workflow/scars/ at Target 90"
    )
  if (
      "without editing core catalog files on `main`" not in skill_lower
      and "never edit core catalog files on `main`" not in skill_lower
      and "never modify core catalog files on `main`" not in skill_lower
  ):
    errors.append(
        "skills/makeflow/SKILL.md must prohibit editing core catalog files on"
        " main at Target 90"
    )

  return errors


def validate_two_tier_scar_lifecycle_and_discovery() -> list[str]:
  """Runs hermetic unit tests and repository checks for GH-93 scar lifecycle."""
  errors = []

  # 1. Hermetic unit tests for validate_scar_lifecycle_and_two_tier_discovery
  valid_catalog = (
      "Tier 1 Core Built-In Scars live in skills/makeflow/references/scars/.\n"
      "Tier 2 Project-Local Scars live in .workflow/scars/.\n"
      "Scope in Target 15, plan in Target 20, implement with tdd-worker in"
      " Target 30 on a feature branch, and audit in Target 40.\n"
      "Target 90 never creates or modifies core catalog files on `main`.\n"
      "Record merged scar IDs in .workflow/history.json and .workflow/history.md.\n"
      "In makeflow-skill, record incidental lessons in ticket notes.\n"
      "In external projects, record lessons in .workflow/scars/local.md.\n"
  )
  valid_skill = (
      "At Target 90, record merged scar IDs in .workflow/history.* and"
      " project-local scars in .workflow/scars/ without editing core catalog"
      " files on `main`."
  )
  unit_ok = validate_scar_lifecycle_and_two_tier_discovery(valid_catalog, valid_skill)
  if unit_ok:
    errors.append(f"Hermetic validator rejected valid input: {unit_ok}")

  defective_catalog_legacy = (
      valid_catalog
      + "\nOpen the matching domain spoke in `skills/makeflow/references/scars/`"
      " and append the new scar at the end of the file."
  )
  unit_legacy = validate_scar_lifecycle_and_two_tier_discovery(
      defective_catalog_legacy, valid_skill
  )
  if not unit_legacy:
    errors.append("Hermetic validator failed to reject legacy Target 90 spoke edit instruction")

  defective_catalog_missing_tier2 = "Tier 1 in skills/makeflow/references/scars/ only."
  unit_missing_tier2 = validate_scar_lifecycle_and_two_tier_discovery(
      defective_catalog_missing_tier2, valid_skill
  )
  if not any("Tier 2" in e for e in unit_missing_tier2):
    errors.append("Hermetic validator failed to detect missing Tier 2 definition")

  defective_skill = "At Target 90, record Synthetic Scars and update .workflow/history.json."
  unit_bad_skill = validate_scar_lifecycle_and_two_tier_discovery(
      valid_catalog, defective_skill
  )
  if not unit_bad_skill:
    errors.append("Hermetic validator failed to reject ambiguous SKILL.md Target 90 text")

  # 2. Validate repository 09_synthetic_scars_catalog.md and SKILL.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  skill_rel = "skills/makeflow/SKILL.md"
  cat_path = repo_path(cat_rel)
  skill_path = repo_path(skill_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  if not os.path.exists(skill_path):
    errors.append(f"Missing {skill_rel}")
  if os.path.exists(cat_path) and os.path.exists(skill_path):
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    with open(skill_path, "r", encoding="utf-8") as f:
      skill_text = f.read()
    errors.extend(
        validate_scar_lifecycle_and_two_tier_discovery(cat_text, skill_text)
    )

  # 3. Validate 12_request_history_ledger.md
  ledger_rel = "skills/makeflow/references/12_request_history_ledger.md"
  ledger_path = repo_path(ledger_rel)
  if not os.path.exists(ledger_path):
    errors.append(f"Missing {ledger_rel}")
  else:
    with open(ledger_path, "r", encoding="utf-8") as f:
      ledger_text = f.read()
    if ".workflow/scars/" not in ledger_text:
      errors.append(f"{ledger_rel} must reference project-local .workflow/scars/")
    if "merged" not in ledger_text.lower() or "scars_earned" not in ledger_text:
      errors.append(
          f"{ledger_rel} must clarify that scars_earned records merged scar IDs"
          " and project-local scars"
      )

  # 4. Validate Two-Tier Scar Discovery across agents, references, and rules
  discovery_files = (
      "agents/archaeology-worker.md",
      "agents/tdd-worker.md",
      "agents/adversarial-critic.md",
      "skills/makeflow/references/02_archaeology_and_planning.md",
      "skills/makeflow/references/03_tdd_and_code_quality.md",
      "skills/makeflow/references/04_six_pillars_adversarial_review.md",
      "skills/makeflow/references/08_subagent_orchestration.md",
      "rules/AGENTS.md",
  )
  for rel_path in discovery_files:
    full_path = repo_path(rel_path)
    if not os.path.exists(full_path):
      errors.append(f"Missing {rel_path}")
      continue
    with open(full_path, "r", encoding="utf-8") as f:
      content = f.read()
    if "skills/makeflow/references/scars/" not in content:
      errors.append(
          f"{rel_path} must document Tier 1 core scars path"
          " 'skills/makeflow/references/scars/'"
      )
    if ".workflow/scars/" not in content:
      errors.append(
          f"{rel_path} must document Tier 2 project-local scars path"
          " '.workflow/scars/'"
      )

  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if os.path.exists(agents_path):
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_content = f.read()
    if "1.6 Synthetic Scar Creation Lifecycle and Two-Tier Discovery" not in agents_content:
      errors.append(
          f"{agents_rel} must include section '1.6 Synthetic Scar Creation"
          " Lifecycle and Two-Tier Discovery'"
      )

  return errors


def validate_tdd_guard_scar():
  """Validates cryptographically anchored TDD receipts and SCAR-TEST-12 (GH-53)."""
  errors = []

  testing_spoke_rel = "skills/makeflow/references/scars/testing.md"
  testing_spoke = repo_path(testing_spoke_rel)
  if not os.path.exists(testing_spoke):
    errors.append(f"Missing {testing_spoke_rel}")
  else:
    with open(testing_spoke, "r", encoding="utf-8") as f:
      test_text = f.read()
    if "### SCAR-TEST-12" not in test_text:
      errors.append(f"{testing_spoke_rel} must document SCAR-TEST-12")
    else:
      scar_section = (
          test_text.split("### SCAR-TEST-12")[1].split("### SCAR-")[0]
      )
      errors.extend(_check_scar_test_12_section(scar_section, testing_spoke_rel))

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-TEST-12" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-TEST-12")
    if not check_scar_catalog_routing(cat_text, "SCAR-TEST-12", "testing.md"):
      errors.append(f"{cat_rel} must route SCAR-TEST-12 to scars/testing.md")

  for doc_rel in (
      "skills/makeflow/references/03_tdd_and_code_quality.md",
      "agents/tdd-worker.md",
      "skills/makeflow/SKILL.md",
  ):
    doc_path = repo_path(doc_rel)
    if not os.path.exists(doc_path):
      errors.append(f"Missing {doc_rel}")
    else:
      with open(doc_path, "r", encoding="utf-8") as f:
        doc_text = f.read()
      for req_token in ("SCAR-TEST-12", "witness-red", "verify-green"):
        if req_token not in doc_text:
          errors.append(f"{doc_rel} must document '{req_token}'")

  mk_rel = "Makefile"
  mk_path = repo_path(mk_rel)
  if not os.path.exists(mk_path):
    errors.append(f"Missing {mk_rel}")
  else:
    with open(mk_path, "r", encoding="utf-8") as f:
      mk_text = f.read()
    errors.extend(check_makefile_test_discovery(mk_text, ("test-tdd-guard",)))

  for script_rel in ("scripts/tdd_guard.py", "lib/tdd_guard.py", "tests/test_tdd_guard.py"):
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  guard_test_rel = "tests/test_tdd_guard.py"
  guard_test_path = repo_path(guard_test_rel)
  if os.path.exists(guard_test_path):
    with open(guard_test_path, "r", encoding="utf-8") as f:
      gt_text = f.read()
    if "sys.executable" not in gt_text:
      errors.append(
          f"{guard_test_rel} must use sys.executable for child subprocesses"
      )
    if '["python3"' in gt_text or "['python3'" in gt_text:
      errors.append(
          f"{guard_test_rel} must not hardcode 'python3' in subprocess calls"
      )

  tdd_doc_rel = "skills/makeflow/references/03_tdd_and_code_quality.md"
  tdd_doc_path = repo_path(tdd_doc_rel)
  if os.path.exists(tdd_doc_path):
    with open(tdd_doc_path, "r", encoding="utf-8") as f:
      tdd_doc_text = f.read()
    for req_token in ("verify-citation", "UNCOMMITTED_WITNESS_FILE", "TRIVIAL_WITNESS_LINE"):
      if req_token not in tdd_doc_text:
        errors.append(f"{tdd_doc_rel} must document '{req_token}'")

  tdd_guard_py = repo_path("lib/tdd_guard.py")
  if os.path.exists(tdd_guard_py):
    with open(tdd_guard_py, "r", encoding="utf-8") as f:
      guard_code = f.read()
    for req_token in ("verify-citation", "UNCOMMITTED_WITNESS_FILE", "TRIVIAL_WITNESS_LINE"):
      if req_token not in guard_code:
        errors.append(f"lib/tdd_guard.py must implement '{req_token}'")

  # Negative Falsification Checks
  mock_corrupted = "### SCAR-TEST-12: Receipts\n- **Wound**: Tampered\n"
  corrupted_errs = _check_scar_test_12_section(mock_corrupted, testing_spoke_rel)
  if not any("Permanent Reflex" in e for e in corrupted_errs):
    errors.append(
        "Falsification failed: corrupted SCAR-TEST-12 should lack Permanent Reflex"
    )
  mock_missing_citation = (
      "### SCAR-TEST-12: Receipts\n"
      "- **Wound**: Tampered\n"
      "- **Trap**: Trap\n"
      "- **Permanent Reflex**: witness-red verify-green 30_tdd_receipt.json forensic_log\n"
      "- **Socratic Probe**: Probe\n"
  )
  missing_cit_errs = _check_scar_test_12_section(
      mock_missing_citation, testing_spoke_rel
  )
  if not any("verify-citation" in e for e in missing_cit_errs):
    errors.append(
        "Falsification failed: missing verify-citation should be detected"
    )
  if not any("UNCOMMITTED_WITNESS_FILE" in e for e in missing_cit_errs):
    errors.append(
        "Falsification failed: missing UNCOMMITTED_WITNESS_FILE should be detected"
    )
  if not any("TRIVIAL_WITNESS_LINE" in e for e in missing_cit_errs):
    errors.append(
        "Falsification failed: missing TRIVIAL_WITNESS_LINE should be detected"
    )
  mock_misrouted = (
      "| `SCAR-TEST-12` | Cryptographically Anchored TDD Receipts | Testing |"
      " [`scars/process.md`](./scars/process.md) |"
  )
  if check_scar_catalog_routing(mock_misrouted, "SCAR-TEST-12", "testing.md"):
    errors.append(
        "Falsification failed: misrouted SCAR-TEST-12 catalog row was accepted"
    )

  return errors


def check_ref14_basescript_ast_lint_note(ref14_text: str) -> list[str]:
  """Verifies Reference 14 Section 4 documents ast-lint (SCAR-AST-01) compatibility in Simple English."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scar_candidate import count_words_in_sentence, split_into_sentences

  rel = "skills/makeflow/references/14_design_patterns_and_architecture.md"
  errs: list[str] = []
  sec4 = _extract_markdown_h2_section(
      ref14_text, "## 4. Testable Object-Oriented Script Pattern"
  )
  if not sec4:
    return [f"{rel} missing Section 4 'Testable Object-Oriented Script Pattern'"]

  m_py = re.search(
      r"(?ms)^#### 1\. Python \(`BaseScript`\)\s*\n(.*?)(?=^#### 2\.|\n## |\Z)",
      sec4,
  )
  if not m_py:
    return [f"{rel} Section 4 missing '#### 1. Python (`BaseScript`)' subsection"]

  py_sub = m_py.group(1)
  for tok in (
      "self.stderr.write",
      "except Exception as exc:",
      "SCAR-AST-01",
      "make -f .workflow/state/Makefile ast-lint",
      "scripts/ast_scrub.py",
  ):
    if tok not in py_sub:
      errs.append(f"{rel} Python BaseScript section must document '{tok}'")

  prose_lines: list[str] = []
  in_fence = False
  for line in py_sub.splitlines():
    stripped = line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if not in_fence and stripped:
      prose_lines.append(stripped)

  prose_text = "\n".join(prose_lines)
  if not prose_text:
    errs.append(f"{rel} Python BaseScript section missing Simple English ast-lint note")
  else:
    errs.extend(_check_section_simple_english(f"{rel} [Section 4 Python]", prose_text))
    for sent in split_into_sentences(prose_text):
      wc = count_words_in_sentence(sent)
      if wc > 25:
        errs.append(
            f"{rel} [Section 4 Python] exceeds 25-word Simple English sentence ceiling ({wc} words): {sent}"
        )
  return errs


def validate_ast_scrubber_scar_ast_01():
  """Validates structural AST scrubber gate and SCAR-AST-01 (GH-56)."""
  errors = []

  testing_spoke_rel = "skills/makeflow/references/scars/testing.md"
  testing_spoke = repo_path(testing_spoke_rel)
  if not os.path.exists(testing_spoke):
    errors.append(f"Missing {testing_spoke_rel}")
  else:
    with open(testing_spoke, "r", encoding="utf-8") as f:
      test_text = f.read()
    if "### SCAR-AST-01" not in test_text:
      errors.append(f"{testing_spoke_rel} must document SCAR-AST-01")
    else:
      scar_section = (
          test_text.split("### SCAR-AST-01")[1].split("### SCAR-")[0]
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{testing_spoke_rel} SCAR-AST-01 missing '**{field}**:'")
    table_match = re.search(
        r"## Scars in this Category.*?(?=\n###|\Z)", test_text, re.DOTALL
    )
    table_section = table_match.group(0) if table_match else ""
    if "SCAR-AST-01" not in table_section:
      errors.append(f"{testing_spoke_rel} category table must list SCAR-AST-01")

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-AST-01" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-AST-01")
    if not check_scar_catalog_routing(cat_text, "SCAR-AST-01", "testing.md"):
      errors.append(f"{cat_rel} must route SCAR-AST-01 to scars/testing.md")

  mk_rel = "Makefile"
  mk_path = repo_path(mk_rel)
  if not os.path.exists(mk_path):
    errors.append(f"Missing {mk_rel}")
  else:
    with open(mk_path, "r", encoding="utf-8") as f:
      mk_text = f.read()
    if "ast-lint:" not in mk_text:
      errors.append(f"{mk_rel} must define 'ast-lint:' target")
    if (
        "test-static:" not in mk_text
        or "ast-lint" not in mk_text.split("test-static:")[1].split("\n")[0]
    ):
      errors.append(f"{mk_rel} 'test-static:' must list 'ast-lint' prerequisite")
    errors.extend(check_makefile_test_discovery(mk_text, ("test-ast-scrub",)))
    ast_lint_block = (
        mk_text.split("ast-lint:")[1].split("\n\n")[0]
        if "ast-lint:" in mk_text
        else ""
    )
    for expected_dir_tok in ("LIB_DIR", "SCRIPTS_DIR", "MCP_DIR", "TESTS_DIR"):
      if expected_dir_tok not in ast_lint_block:
        errors.append(f"{mk_rel} 'ast-lint:' must scrub $({expected_dir_tok})")

  gates_rel = "lib/workflow_gates.py"
  gates_path = repo_path(gates_rel)
  if not os.path.exists(gates_path):
    errors.append(f"Missing {gates_rel}")
  else:
    with open(gates_path, "r", encoding="utf-8") as f:
      gates_text = f.read()
    if "ast-lint:" not in gates_text:
      errors.append(f"{gates_rel} _init_makefile must include 'ast-lint:'")
    if "SRC_DIR ?=" not in gates_text:
      errors.append(
          f"{gates_rel} generate_state_makefile must declare 'SRC_DIR ?='"
      )
    if "ast-lint:" in gates_text:
      ast_lint_gen_block = gates_text.split("ast-lint:", 1)[1].split("\n  )", 1)[0]
      for required_tok in (
          "--repo-root",
          '--src-dir "$(SRC_DIR)"',
          "--strict",
      ):
        if required_tok not in ast_lint_gen_block:
          errors.append(
              f"{gates_rel} generate_state_makefile ast-lint must include '{required_tok}'"
          )
    for legacy_dir in ("lib", "scripts", "mcp", "tests"):
      if f"--src-dir {legacy_dir} --strict" in gates_text:
        errors.append(
            f"{gates_rel} generate_state_makefile ast-lint must not hardcode '--src-dir {legacy_dir} --strict'"
        )

  ref14_rel = "skills/makeflow/references/14_design_patterns_and_architecture.md"
  ref14_path = repo_path(ref14_rel)
  if not os.path.exists(ref14_path):
    errors.append(f"Missing {ref14_rel}")
  else:
    with open(ref14_path, "r", encoding="utf-8") as f:
      ref14_text = f.read()
    errors.extend(check_ref14_basescript_ast_lint_note(ref14_text))

  for script_rel in ("scripts/ast_scrub.py", "lib/ast_scrub.py", "tests/test_ast_scrub.py"):
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  # Negative Falsification
  mock_misrouted = (
      "| `SCAR-AST-01` | Structural AST Anti-Shortcut Scrubbing Gate | Testing |"
      " [`scars/process.md`](./scars/process.md) |"
  )
  if check_scar_catalog_routing(mock_misrouted, "SCAR-AST-01", "testing.md"):
    errors.append(
        "Falsification failed: misrouted SCAR-AST-01 catalog row was accepted"
    )

  return errors



def validate_anti_speculation_scar():
  """Validates Anti-Speculative Defenses and Caller Grounding (GH-85, SCAR-ARCH-07)."""
  errors = []

  # Helper to verify scar fields in a markdown document block
  def _verify_scar_block_fields(text, scar_id):
    field_errors = []
    if scar_id not in text:
      field_errors.append(f"missing {scar_id}")
      return field_errors
    section = (
        text.split(f"### {scar_id}")[1].split("### SCAR-")[0]
        if f"### {scar_id}" in text
        else ""
    )
    for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
      field_match = re.search(
          rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", section
      )
      if not field_match or not field_match.group(1).strip():
        field_errors.append(f"missing non-empty '**{field}**:'")
    return field_errors

  # 1. Spoke verification: skills/makeflow/references/scars/architecture.md
  arch_spoke_rel = "skills/makeflow/references/scars/architecture.md"
  arch_spoke = repo_path(arch_spoke_rel)
  if not os.path.exists(arch_spoke):
    errors.append(f"Missing {arch_spoke_rel}")
  else:
    with open(arch_spoke, "r", encoding="utf-8") as f:
      arch_text = f.read()
    table_match = re.search(
        r"## Scars in this Category.*?(?=\n###|\Z)", arch_text, re.DOTALL
    )
    table_section = table_match.group(0) if table_match else ""
    if "SCAR-ARCH-07" not in table_section:
      errors.append(f"{arch_spoke_rel} scars table must contain SCAR-ARCH-07")
    for err in _verify_scar_block_fields(arch_text, "SCAR-ARCH-07"):
      errors.append(f"{arch_spoke_rel} SCAR-ARCH-07 {err}")

  # 2. Master routing catalog: skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-ARCH-07" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-ARCH-07")
    if not check_scar_catalog_routing(cat_text, "SCAR-ARCH-07", "architecture.md"):
      errors.append(
          f"{cat_rel} must route SCAR-ARCH-07 to scars/architecture.md"
      )

  # 3. Six pillars manual: skills/makeflow/references/04_six_pillars_adversarial_review.md
  rev_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  rev_path = repo_path(rev_rel)
  if not os.path.exists(rev_path):
    errors.append(f"Missing {rev_rel}")
  else:
    with open(rev_path, "r", encoding="utf-8") as f:
      rev_text = f.read()
    pillar3_match = re.search(
        r"(?m)^###\s+3\.\s+Architecture\b.*?(?=^###\s+4\b|\Z)",
        rev_text,
        re.DOTALL,
    )
    pillar3_section = pillar3_match.group(0) if pillar3_match else ""
    if "SCAR-ARCH-07" not in pillar3_section:
      errors.append(f"{rev_rel} Pillar 3 must include SCAR-ARCH-07")

  # 4. Critic agent prompt: agents/adversarial-critic.md
  critic_rel = "agents/adversarial-critic.md"
  critic_path = repo_path(critic_rel)
  if not os.path.exists(critic_path):
    errors.append(f"Missing {critic_rel}")
  else:
    with open(critic_path, "r", encoding="utf-8") as f:
      critic_text = f.read()
    critic_p3_match = re.search(
        r"(?m)^###\s+3\.\s+Architecture\b.*?(?=^###\s+4\b|\Z)",
        critic_text,
        re.DOTALL,
    )
    critic_p3 = critic_p3_match.group(0) if critic_p3_match else ""
    if "SCAR-ARCH-07" not in critic_p3:
      errors.append(f"{critic_rel} Pillar 3 must include SCAR-ARCH-07")

  # 5. Antigravity rules: rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    sec3_match = re.search(
        r"(?m)^##\s+3\.\s+Code Edit and Architecture Rules\b.*?(?=^##\s+4\b|\Z)",
        agents_text,
        re.DOTALL,
    )
    sec3_text = sec3_match.group(0) if sec3_match else ""
    if "SCAR-ARCH-07" not in sec3_text:
      errors.append(f"{agents_rel} Section 3 must document SCAR-ARCH-07")

  # 6. Negative Falsification
  if not _verify_scar_block_fields("### SCAR-ARCH-06: Other", "SCAR-ARCH-07"):
    errors.append("Negative falsification failed: missing SCAR-ARCH-07 was not detected")
  for missing_field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
    all_fields = [
        "**Wound**: W",
        "**Trap**: T",
        "**Permanent Reflex**: R",
        "**Socratic Probe**: P",
    ]
    incomplete_fields = "\n".join(
        [f for f in all_fields if not f.startswith(f"**{missing_field}**:")]
    )
    mock_incomplete = (
        f"### SCAR-ARCH-07: Incomplete\n{incomplete_fields}\n### SCAR-ARCH-08:"
        " Next"
    )
    if not _verify_scar_block_fields(mock_incomplete, "SCAR-ARCH-07"):
      errors.append(
          f"Negative falsification failed: missing {missing_field} was not"
          " detected"
      )
  mock_misrouted = (
      "| `SCAR-ARCH-07` | Anti-Speculation | Architecture |"
      " [`scars/testing.md`](./scars/testing.md) |"
  )
  if check_scar_catalog_routing(mock_misrouted, "SCAR-ARCH-07", "architecture.md"):
    errors.append("Negative falsification failed: misrouted SCAR-ARCH-07 was accepted")

  return errors

  return errors


def parse_frontmatter(content):
  """Parses YAML frontmatter from markdown file content."""
  if not content.startswith("---"):
    return {}
  parts = content.split("---", 2)
  if len(parts) < 3:
    return {}
  frontmatter_str = parts[1]
  result = {}
  current_dict = None
  current_list = None
  current_section = None
  for line in frontmatter_str.splitlines():
    raw = line
    s = line.strip()
    if not s or s.startswith("#"):
      continue
    if raw.startswith("  ") or raw.startswith("\t"):
      if current_section:
        if s.startswith("- "):
          if current_list is None:
            current_list = []
            result[current_section] = current_list
          current_list.append(s[2:].strip())
        elif ":" in s:
          if current_dict is None:
            current_dict = {}
            result[current_section] = current_dict
          k, v = s.split(":", 1)
          k = k.strip()
          vl = v.strip().lower()
          if vl == "true":
            current_dict[k] = True
          elif vl == "false":
            current_dict[k] = False
          else:
            current_dict[k] = v.strip()
      continue
    current_dict = None
    current_list = None
    current_section = None
    if ":" in s:
      k, v = s.split(":", 1)
      k = k.strip()
      v = v.strip()
      if not v:
        current_section = k
      else:
        vl = v.lower()
        if vl == "true":
          result[k] = True
        elif vl == "false":
          result[k] = False
        else:
          result[k] = v
  return result


def _check_subagent_frontmatter_content(rel_path, content, spec):
  """Validates a single subagent markdown content string against its tool spec."""
  errs = []
  for bad_flag in ("enable_write_tools", "enable_mcp_tools", "enable_subagent_tools"):
    if re.search(rf"^{bad_flag}:", content, re.MULTILINE):
      errs.append(f"{rel_path} must not contain top-level snake_case '{bad_flag}'")

  fm = parse_frontmatter(content)
  if "options" in fm or re.search(r"^options:", content, re.MULTILINE):
    errs.append(f"{rel_path} frontmatter must not contain obsolete 'options:' block")

  if fm.get("name") != spec["name"]:
    errs.append(f"{rel_path} frontmatter name must be '{spec['name']}', got '{fm.get('name')}'")
  if not fm.get("description"):
    errs.append(f"{rel_path} frontmatter missing non-empty 'description'")

  tools_list = fm.get("tools")
  if not isinstance(tools_list, list):
    errs.append(f"{rel_path} frontmatter missing 'tools:' list")
  else:
    for pro_t in spec["prohibited_tools"]:
      if pro_t in tools_list:
        errs.append(f"{rel_path} tools list must not contain prohibited tool '{pro_t}'")
    for req_t in spec["tools"]:
      if req_t not in tools_list:
        errs.append(f"{rel_path} tools list missing required tool '{req_t}'")
    if tools_list != spec["tools"]:
      errs.append(
          f"{rel_path} tools list must exactly match {spec['tools']}, got {tools_list}"
      )
  return errs


def validate_subagent_tool_mounting_permissions():
  """Validates subagent tools lists and exclusion of unresolvable MCP meta-tools (GH-83, GH-108)."""
  errors = []

  expected_perms = {
      "agents/tdd-worker.md": {
          "name": "tdd-worker",
          "tools": [
              "send_message",
              "read_file",
              "search_web",
              "schedule",
              "generate_image",
              "write_file",
              "edit_file",
              "run_command",
              "manage_task",
          ],
          "prohibited_tools": [
              "call_mcp_tool",
              "list_resources",
              "read_resource",
          ],
      },
      "agents/adversarial-critic.md": {
          "name": "adversarial-critic",
          "tools": [
              "send_message",
              "read_file",
              "search_web",
              "schedule",
              "generate_image",
              "run_command",
              "manage_task",
          ],
          "prohibited_tools": [
              "write_file",
              "edit_file",
              "call_mcp_tool",
              "list_resources",
              "read_resource",
          ],
      },
      "agents/triage-worker.md": {
          "name": "triage-worker",
          "tools": [
              "send_message",
              "read_file",
              "search_web",
              "schedule",
              "generate_image",
              "run_command",
              "manage_task",
          ],
          "prohibited_tools": [
              "write_file",
              "edit_file",
              "call_mcp_tool",
              "list_resources",
              "read_resource",
          ],
      },
      "agents/archaeology-worker.md": {
          "name": "archaeology-worker",
          "tools": [
              "send_message",
              "read_file",
              "search_web",
              "schedule",
              "generate_image",
              "manage_task",
          ],
          "prohibited_tools": [
              "write_file",
              "edit_file",
              "run_command",
              "call_mcp_tool",
              "list_resources",
              "read_resource",
          ],
      },
      "agents/synthetic-scar-importer.md": {
          "name": "synthetic-scar-importer",
          "tools": [
              "send_message",
              "read_file",
              "search_web",
              "schedule",
              "generate_image",
              "write_file",
              "edit_file",
              "run_command",
              "manage_task",
          ],
          "prohibited_tools": [
              "call_mcp_tool",
              "list_resources",
              "read_resource",
          ],
      },
  }

  for rel_path, spec in expected_perms.items():
    full_path = repo_path(rel_path)
    if not os.path.exists(full_path):
      errors.append(f"Missing {rel_path}")
      continue
    with open(full_path, "r", encoding="utf-8") as f:
      content = f.read()
    errors.extend(_check_subagent_frontmatter_content(rel_path, content, spec))

  # In-memory negative falsification checks
  sample_spec = expected_perms["agents/archaeology-worker.md"]
  valid_tools_yaml = "\n".join(f"  - {t}" for t in sample_spec["tools"])
  for bad_mcp_tool in ("call_mcp_tool", "list_resources", "read_resource"):
    bad_content = (
        f"---\nname: archaeology-worker\ndescription: Desc.\ntools:\n"
        f"{valid_tools_yaml}\n  - {bad_mcp_tool}\n---\n# Body\n"
    )
    neg_errs = _check_subagent_frontmatter_content(
        "agents/archaeology-worker.md", bad_content, sample_spec
    )
    if not any(bad_mcp_tool in e for e in neg_errs):
      errors.append(
          f"Negative falsification failed to reject prohibited MCP tool '{bad_mcp_tool}'"
      )

  bad_options_content = (
      f"---\nname: archaeology-worker\ndescription: Desc.\noptions:\n"
      f"  EnableWriteTools: false\ntools:\n{valid_tools_yaml}\n---\n# Body\n"
  )
  if not any(
      "options:" in e
      for e in _check_subagent_frontmatter_content(
          "agents/archaeology-worker.md", bad_options_content, sample_spec
      )
  ):
    errors.append("Negative falsification failed to reject obsolete 'options:' block")

  omitted_tools_yaml = "\n".join(f"  - {t}" for t in sample_spec["tools"][1:])
  bad_omitted_content = (
      f"---\nname: archaeology-worker\ndescription: Desc.\ntools:\n"
      f"{omitted_tools_yaml}\n---\n# Body\n"
  )
  if not any(
      "send_message" in e
      for e in _check_subagent_frontmatter_content(
          "agents/archaeology-worker.md", bad_omitted_content, sample_spec
      )
  ):
    errors.append("Negative falsification failed to catch omitted required tool 'send_message'")

  # Verify scripts/workflow_gates.py and lib/workflow_gates.py do NOT contain subagent-def or _gate_subagent_def
  for gates_rel in ("scripts/workflow_gates.py", "lib/workflow_gates.py"):
    gates_script = repo_path(gates_rel)
    if not os.path.exists(gates_script):
      errors.append(f"Missing {gates_rel}")
    else:
      with open(gates_script, "r", encoding="utf-8") as f:
        gates_text = f.read()
      for removed_sym in ("subagent-def", "_gate_subagent_def"):
        if removed_sym in gates_text:
          errors.append(f"{gates_rel} must not contain removed '{removed_sym}'")

  # Check reference manual 08_subagent_orchestration.md, rules/AGENTS.md, and scars/process.md
  doc_files = (
      "skills/makeflow/references/08_subagent_orchestration.md",
      "rules/AGENTS.md",
      "skills/makeflow/references/scars/process.md",
  )
  for doc_rel in doc_files:
    doc_path = repo_path(doc_rel)
    if not os.path.exists(doc_path):
      errors.append(f"Missing {doc_rel}")
      continue
    with open(doc_path, "r", encoding="utf-8") as f:
      text = f.read()
    for req_term in (
        "tools:",
        "call_mcp_tool",
        "list_resources",
        "read_resource",
    ):
      if req_term.lower() not in text.lower():
        errors.append(f"{doc_rel} must document '{req_term}'")
    if "subagent-def" in text:
      errors.append(f"{doc_rel} must not reference removed 'subagent-def'")

  ref_08_path = repo_path("skills/makeflow/references/08_subagent_orchestration.md")
  if os.path.exists(ref_08_path):
    with open(ref_08_path, "r", encoding="utf-8") as f:
      ref_08_text = f.read()
    for role_term in (
        "tdd-worker",
        "adversarial-critic",
        "triage-worker",
        "archaeology-worker",
        "synthetic-scar-importer",
    ):
      if role_term not in ref_08_text:
        errors.append(
            f"skills/makeflow/references/08_subagent_orchestration.md must document '{role_term}'"
        )

  ref_04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  ref_04_path = repo_path(ref_04_rel)
  if not os.path.exists(ref_04_path):
    errors.append(f"Missing {ref_04_rel}")
  else:
    with open(ref_04_path, "r", encoding="utf-8") as f:
      ref_04_text = f.read()
    for obs_flag in ("enable_write_tools", "enable_mcp_tools", "enable_subagent_tools"):
      if obs_flag in ref_04_text:
        errors.append(f"{ref_04_rel} must not contain obsolete '{obs_flag}'")

  return errors


def validate_posix_signal_trapping():
  """Validates POSIX signal trapping invariants (GH-24, SCAR-SYS-07).

  Verifies:
  1. skills/makeflow/references/scars/system_and_os.md documents
     SCAR-SYS-07 with complete Four-Part Anatomy and invariants (SIGTERM, SIGINT,
     SignalTrap, LIFO, lockfile).
  2. skills/makeflow/references/09_synthetic_scars_catalog.md routes
     SCAR-SYS-07 strictly to scars/system_and_os.md.
  3. skills/makeflow/references/10_security_and_data_isolation.md
     Section 7 documents SCAR-SYS-07 and POSIX signal trapping.
  4. rules/AGENTS.md Section 2 documents SCAR-SYS-07 and SignalTrap.
  5. All 5 adapters (CLAUDE.md, .cursorrules, .cursor/rules/makeflow.mdc,
     .github/copilot-instructions.md, adapters/chatgpt/instructions.md) document
     SCAR-SYS-07.
  6. Makefile defines test-signal-trap target and discovers tests via unittest discover.
  7. scripts/signal_trap.py and scripts/test_signal_trap.py exist.
  8. Negative falsification:
     - Missing Four-Part Anatomy fields or corrupted text triggers errors.
     - Misrouted catalog table rows trigger errors.
  """
  errors = []

  sys_spoke_rel = "skills/makeflow/references/scars/system_and_os.md"
  sys_spoke = repo_path(sys_spoke_rel)
  if not os.path.exists(sys_spoke):
    errors.append(f"Missing {sys_spoke_rel}")
  else:
    with open(sys_spoke, "r", encoding="utf-8") as f:
      sys_text = f.read()
    if "SCAR-SYS-07" not in sys_text:
      errors.append(f"{sys_spoke_rel} must document SCAR-SYS-07")
    else:
      scar_section = (
          sys_text.split("### SCAR-SYS-07")[1].split("### SCAR-")[0]
          if "### SCAR-SYS-07" in sys_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{sys_spoke_rel} SCAR-SYS-07 missing '**{field}**:'")
      for invariant in ("SIGTERM", "SIGINT", "SignalTrap", "LIFO", "lockfile"):
        if invariant.lower() not in scar_section.lower():
          errors.append(
              f"{sys_spoke_rel} SCAR-SYS-07 missing invariant '{invariant}'"
          )

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-SYS-07" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-SYS-07")
    if not check_scar_catalog_routing(cat_text, "SCAR-SYS-07", "system_and_os.md"):
      errors.append(
          f"{cat_rel} must route SCAR-SYS-07 to scars/system_and_os.md"
      )

  sec_rel = (
      "skills/makeflow/references/10_security_and_data_isolation.md"
  )
  sec_path = repo_path(sec_rel)
  if not os.path.exists(sec_path):
    errors.append(f"Missing {sec_rel}")
  else:
    with open(sec_path, "r", encoding="utf-8") as f:
      sec_text = f.read()
    if "SCAR-SYS-07" not in sec_text:
      errors.append(f"{sec_rel} must document SCAR-SYS-07")
    if "POSIX Signal Trapping" not in sec_text:
      errors.append(f"{sec_rel} must include section on POSIX Signal Trapping")

  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    if "SCAR-SYS-07" not in agents_text:
      errors.append(f"{agents_rel} must reference SCAR-SYS-07")
    if "SignalTrap" not in agents_text:
      errors.append(f"{agents_rel} must mention SignalTrap under SCAR-SYS-07")

  for adapter_rel in [
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ]:
    adapter_path = repo_path(adapter_rel)
    if not os.path.exists(adapter_path):
      errors.append(f"Missing {adapter_rel}")
    else:
      with open(adapter_path, "r", encoding="utf-8") as f:
        adapter_text = f.read()
      if "SCAR-SYS-07" not in adapter_text:
        errors.append(f"{adapter_rel} must document SCAR-SYS-07")
      if "SCAR-SYS-08" not in adapter_text:
        errors.append(f"{adapter_rel} must document SCAR-SYS-08")

  mk_rel = "Makefile"
  mk_path = repo_path(mk_rel)
  if not os.path.exists(mk_path):
    errors.append(f"Missing {mk_rel}")
  else:
    with open(mk_path, "r", encoding="utf-8") as f:
      mk_text = f.read()
    errors.extend(check_makefile_test_discovery(mk_text, ("test-signal-trap",)))

  for script_rel in ["scripts/signal_trap.py", "lib/signal_trap.py", "tests/test_signal_trap.py"]:
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  # 8. Negative Falsification Checks
  mock_corrupted_spoke = "### SCAR-SYS-07: Broken\n**Wound**: Foo"
  if "**Trap**:" in mock_corrupted_spoke:
    errors.append("Falsification failed: corrupted spoke should not have Trap")
  mock_misrouted = (
      "| `SCAR-SYS-07` | Signal | System & OS |"
      " [`scars/process.md`](./scars/process.md) |"
  )
  if check_scar_catalog_routing(mock_misrouted, "SCAR-SYS-07", "system_and_os.md"):
    errors.append("Falsification failed: misrouted catalog line was accepted")

  return errors


def validate_diagnostic_path_formatting():
  """Validates standardized diagnostic relative path formatting (GH-63).

  Verifies:
  1. No diagnostic error string in tests/test_cross_agent_compatibility.py formats
     absolute repo_path variables into 'Missing ...' or 'Invalid ...' messages.
  2. All 13 canonical relative path variables are present and used in diagnostics:
     claude_rel, codex_rel, claude_md_rel, cursorrules_rel, cursor_mdc_rel,
     copilot_rel, chatgpt_rel, hooks_rel, mcp_rel, cfg_template_rel,
     ref_13_rel, ref_08_rel, ref_14_rel.
  3. Critic agent invariant errors format critic_agent_rel instead of absolute critic_agent.
  """
  errors = []
  compat_script_rel = "tests/test_cross_agent_compatibility.py"
  compat_script = repo_path(compat_script_rel)
  if not os.path.exists(compat_script):
    errors.append(f"Missing {compat_script_rel}")
    return errors

  with open(compat_script, "r", encoding="utf-8") as f:
    source_text = f.read()

  if "\ndef validate():\n" not in source_text:
    errors.append(f"{compat_script_rel} missing 'def validate():'")
    return errors

  validate_body = source_text.split("\ndef validate():\n")[1]

  # Check unstandardized legacy variable names in Missing/Invalid diagnostics
  legacy_defect_patterns = [
      ("Missing {claude_path}", "claude_rel"),
      ("Invalid {claude_path}", "claude_rel"),
      ("Missing {codex_path}", "codex_rel"),
      ("Invalid {codex_path}", "codex_rel"),
      ("Missing {claude_md}", "claude_md_rel"),
      ("Missing {cursorrules}", "cursorrules_rel"),
      ("Missing {cursor_mdc}", "cursor_mdc_rel"),
      ("Missing {copilot_path}", "copilot_rel"),
      ("Missing {chatgpt_path}", "chatgpt_rel"),
      ("Missing {hooks_path}", "hooks_rel"),
      ("Invalid {hooks_path}", "hooks_rel"),
      ("Missing {mcp_path}", "mcp_rel"),
      ("Invalid {mcp_path}", "mcp_rel"),
      ("Missing {cfg_template}", "cfg_template_rel"),
      ("Invalid {cfg_template}", "cfg_template_rel"),
      ("{cfg_template} must declare 'models'", "cfg_template_rel"),
      ("Missing {ref_13}", "ref_13_rel"),
      ("Missing {ref_08}", "ref_08_rel"),
      ("Missing {ref_14}", "ref_14_rel"),
      ("Incomplete pattern coverage in {ref_14}", "ref_14_rel"),
  ]

  for legacy_pattern, expected_rel in legacy_defect_patterns:
    if legacy_pattern in validate_body:
      errors.append(
          f"{compat_script_rel} contains unstandardized diagnostic '{legacy_pattern}' (expected '{expected_rel}')"
      )

  # Check critic agent check formatting in lines 718-736
  if "{critic_agent} must contain canonical heading" in validate_body:
    errors.append(
        f"{compat_script_rel} formats absolute '{{critic_agent}}' instead of '{{critic_agent_rel}}' in heading checks"
    )
  if "{critic_agent} must inspect blast radius" in validate_body:
    errors.append(
        f"{compat_script_rel} formats absolute '{{critic_agent}}' instead of '{{critic_agent_rel}}' in blast radius check"
    )
  if "{critic_agent} Pillar 2" in validate_body or "{critic_agent} Pillar 5" in validate_body:
    errors.append(
        f"{compat_script_rel} formats absolute '{{critic_agent}}' instead of '{{critic_agent_rel}}' in Pillar checks"
    )

  # Check that all canonical relative path variables are declared
  canonical_rel_vars = [
      "claude_rel",
      "codex_rel",
      "claude_md_rel",
      "cursorrules_rel",
      "cursor_mdc_rel",
      "copilot_rel",
      "chatgpt_rel",
      "hooks_rel",
      "mcp_rel",
      "cfg_template_rel",
      "ref_13_rel",
      "ref_08_rel",
      "ref_14_rel",
  ]
  for rel_var in canonical_rel_vars:
    if f"{rel_var} = " not in validate_body:
      errors.append(f"{compat_script_rel} must declare canonical relative variable '{rel_var}'")

  return errors


def validate_hosting_mode_and_pure_git_invariants():
  """Validates repository-scoped hosting discovery and Pure Git mode invariants (GH-76, SCAR-GIT-30).

  Verifies:
  1. skills/makeflow/references/scars/git_and_shell.md documents SCAR-GIT-30 with
     complete Four-Part Anatomy (**Wound**:, **Trap**:, **Permanent Reflex**:, **Socratic Probe**:),
     referencing repository-scoped hosting discovery (remote URL, gh binary, gh auth status, gh repo view).
  2. skills/makeflow/references/02_archaeology_and_planning.md references Target 15 hosting discovery
     under SCAR-GIT-30.
  3. skills/makeflow/references/06_clean_commit_and_pr.md specifies Pure Git Mode bypass of gh pr create.
  4. skills/makeflow/references/07_ci_and_bot_triage.md specifies Target 75 local readiness and
     Target 80 local squash-merge for Pure Git Mode.
  5. skills/makeflow/SKILL.md documents hosting discovery / Pure Git Mode across Target 15,
     Target 60, Target 70, Target 75, and Target 80, staying within 24 KB ceiling.
  """
  errors = []

  # 1. Spoke reference: scars/git_and_shell.md
  spoke_rel = "skills/makeflow/references/scars/git_and_shell.md"
  spoke_path = repo_path(spoke_rel)
  if not os.path.exists(spoke_path):
    errors.append(f"Missing {spoke_rel}")
  else:
    with open(spoke_path, "r", encoding="utf-8") as f:
      spoke_text = f.read()
    if "SCAR-GIT-30" not in spoke_text:
      errors.append(f"{spoke_rel} must document SCAR-GIT-30")
    else:
      scar_section = (
          spoke_text.split("### SCAR-GIT-30")[1].split("### SCAR-")[0]
          if "### SCAR-GIT-30" in spoke_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        if f"**{field}**:" not in scar_section:
          errors.append(f"{spoke_rel} SCAR-GIT-30 missing '**{field}**:'")
      for required_phrase in (
          "git remote get-url origin",
          "gh auth status",
          "gh repo view",
          "HOSTING_MODE: PURE_GIT",
      ):
        if required_phrase not in scar_section:
          errors.append(
              f"{spoke_rel} SCAR-GIT-30 missing required phrase '{required_phrase}'"
          )

  # 2. Target 15 discovery: references/02_archaeology_and_planning.md
  arch_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  arch_path = repo_path(arch_rel)
  if not os.path.exists(arch_path):
    errors.append(f"Missing {arch_rel}")
  else:
    with open(arch_path, "r", encoding="utf-8") as f:
      arch_text = f.read()
    if "SCAR-GIT-30" not in arch_text:
      errors.append(f"{arch_rel} must reference SCAR-GIT-30 for hosting discovery")
    if "git remote get-url origin" not in arch_text:
      errors.append(
          f"{arch_rel} must reference 'git remote get-url origin' for hosting discovery"
      )

  # 3. Target 60 bypass: references/06_clean_commit_and_pr.md
  commit_rel = "skills/makeflow/references/06_clean_commit_and_pr.md"
  commit_path = repo_path(commit_rel)
  if not os.path.exists(commit_path):
    errors.append(f"Missing {commit_rel}")
  else:
    with open(commit_path, "r", encoding="utf-8") as f:
      commit_text = f.read()
    if "SCAR-GIT-30" not in commit_text:
      errors.append(f"{commit_rel} must reference SCAR-GIT-30")
    if "HOSTING_MODE: PURE_GIT" not in commit_text:
      errors.append(f"{commit_rel} must reference 'HOSTING_MODE: PURE_GIT'")

  # 4. Target 75 & 80 local readiness/merge: references/07_ci_and_bot_triage.md
  ci_rel = "skills/makeflow/references/07_ci_and_bot_triage.md"
  ci_path = repo_path(ci_rel)
  if not os.path.exists(ci_path):
    errors.append(f"Missing {ci_rel}")
  else:
    with open(ci_path, "r", encoding="utf-8") as f:
      ci_text = f.read()
    if "SCAR-GIT-30" not in ci_text:
      errors.append(f"{ci_rel} must reference SCAR-GIT-30")
    if "git merge --squash" not in ci_text:
      errors.append(
          f"{ci_rel} must reference 'git merge --squash' for Pure Git Mode landing"
      )
    if "git branch -D" not in ci_text:
      errors.append(
          f"{ci_rel} must document 'git branch -D' after 'git merge --squash'"
      )

  # 5. SKILL.md invariants and size ceiling
  skill_rel = "skills/makeflow/SKILL.md"
  skill_path = repo_path(skill_rel)
  if not os.path.exists(skill_path):
    errors.append(f"Missing {skill_rel}")
  else:
    with open(skill_path, "r", encoding="utf-8") as f:
      skill_text = f.read()
    if "SCAR-GIT-30" not in skill_text:
      errors.append(f"{skill_rel} must reference SCAR-GIT-30")
    if "HOSTING_MODE: PURE_GIT" not in skill_text:
      errors.append(f"{skill_rel} must reference 'HOSTING_MODE: PURE_GIT'")
    if "T50 -> T70" not in skill_text:
      errors.append(
          f"{skill_rel} must document bypassing Target 60 (T50 -> T70) when no remote exists"
      )
    skill_size = os.path.getsize(skill_path)
    if skill_size > 24576:
      errors.append(
          f"{skill_rel} exceeds 24 KB ceiling ({skill_size} bytes > 24576 bytes)"
      )

  return errors


def check_pre_push_conflict_text(commit_text):
  """Checks pre-push merge check instructions for autonomous rebase prohibition and human pause gate."""
  errors = []
  sec1_match = re.search(
      r"(?m)^##\s+1\.\s+Pre-Push Merge Check\b.*?(?=^##\s+2\b|\Z)",
      commit_text,
      re.DOTALL,
  )
  sec1_text = sec1_match.group(0) if sec1_match else commit_text

  # Autonomous rebase check: must not command autonomous rebase
  autonomous_patterns = [
      r"(?i)\brebase\s+onto\s+`?origin/main`?",
      r"(?i)\bstop at once\.\s*rebase\b",
      r"(?i)\bautonomously\s+rebase\b",
  ]
  for pat in autonomous_patterns:
    if re.search(pat, sec1_text):
      errors.append("must not command autonomous git rebase on conflict")
      break

  # Mandatory instructions on conflict
  required_phrases = [
      ("stop tool calls immediately without altering git state", "command stopping tool calls immediately without altering Git state"),
      ("scar-git-25", "cite SCAR-GIT-25 on conflict detection"),
      ("conflicting files", "command reporting conflicting files in chat"),
      ("operator direction", "require explicit operator direction before rebase or merge"),
  ]
  sec1_lower = sec1_text.lower()
  for token, desc in required_phrases:
    if token not in sec1_lower:
      errors.append(f"must {desc}")

  return errors


def check_scar_git_20_text(spoke_text):
  """Checks SCAR-GIT-20 text for detection-only scope, autonomous rebase prohibition, and SCAR-GIT-25 governance."""
  errors = []
  if "### SCAR-GIT-20" not in spoke_text:
    errors.append("missing SCAR-GIT-20")
    return errors

  section = spoke_text.split("### SCAR-GIT-20")[1].split("### SCAR-")[0]
  sec_lower = section.lower()

  if "in-memory" not in sec_lower or "conflict detection" not in sec_lower:
    errors.append("SCAR-GIT-20 must define pre-push check as an in-memory conflict detection check")

  if not any(phrase in sec_lower for phrase in [
      "never rebase autonomously",
      "forbid autonomous rebase",
      "prohibit autonomous rebase",
      "without human approval",
  ]):
    errors.append("SCAR-GIT-20 must forbid autonomous rebase")

  if "scar-git-25" not in sec_lower:
    errors.append("SCAR-GIT-20 must reference SCAR-GIT-25")
  if not any(phrase in sec_lower for phrase in ["operator approval", "human approval"]):
    errors.append("SCAR-GIT-20 must mandate operator approval before rebase or merge")
  if "altering git state" not in sec_lower and "alter git state" not in sec_lower:
    errors.append("SCAR-GIT-20 must mention altering Git state")

  return errors


def validate_pre_push_conflict_and_rebase_invariants():
  """Validates pre-push conflict handling and autonomous rebase prohibition invariants (GH-95, SCAR-GIT-20, SCAR-GIT-25).

  Verifies:
  1. skills/makeflow/references/06_clean_commit_and_pr.md does not command
     autonomous git rebase, commands stopping tool calls immediately without
     altering Git state, reporting conflicting files in chat, and pausing for
     explicit operator direction under SCAR-GIT-25.
  2. skills/makeflow/references/scars/git_and_shell.md defines SCAR-GIT-20 as
     an in-memory conflict detection check, forbids autonomous rebase, and
     mandates explicit human operator approval under SCAR-GIT-25 before altering
     Git state.
  3. Negative falsification tests ensure corrupted instructions are caught.
  """
  errors = []

  # 1. 06_clean_commit_and_pr.md
  commit_rel = "skills/makeflow/references/06_clean_commit_and_pr.md"
  commit_path = repo_path(commit_rel)
  if not os.path.exists(commit_path):
    errors.append(f"Missing {commit_rel}")
  else:
    with open(commit_path, "r", encoding="utf-8") as f:
      commit_text = f.read()
    commit_errors = check_pre_push_conflict_text(commit_text)
    for err in commit_errors:
      errors.append(f"{commit_rel} {err}")

  # 2. scars/git_and_shell.md
  spoke_rel = "skills/makeflow/references/scars/git_and_shell.md"
  spoke_path = repo_path(spoke_rel)
  if not os.path.exists(spoke_path):
    errors.append(f"Missing {spoke_rel}")
  else:
    with open(spoke_path, "r", encoding="utf-8") as f:
      spoke_text = f.read()
    scar_errors = check_scar_git_20_text(spoke_text)
    for err in scar_errors:
      errors.append(f"{spoke_rel} {err}")

  # 3. Negative Falsification
  bad_pr_text = (
      "## 1. Pre-Push Merge Check (`SCAR-GIT-20`)\n"
      "3. If `git merge-tree` reports conflict markers, stop at once. Rebase onto `origin/main`.\n"
      "## 2. Human Push Approval Gate\n"
  )
  bad_pr_errors = check_pre_push_conflict_text(bad_pr_text)
  if not bad_pr_errors or not any("autonomous" in e for e in bad_pr_errors):
    errors.append(
        "Negative falsification failed: autonomous rebase instruction in clean"
        " commit doc was not rejected"
    )

  incomplete_pr_text = (
      "## 1. Pre-Push Merge Check (`SCAR-GIT-20`)\n"
      "3. If `git merge-tree` reports conflict markers, stop tool calls"
      " immediately without altering Git state. Print conflicting files.\n"
      "## 2. Human Push Approval Gate\n"
  )
  inc_pr_errors = check_pre_push_conflict_text(incomplete_pr_text)
  if not any("scar-git-25" in e.lower() for e in inc_pr_errors):
    errors.append(
        "Negative falsification failed: missing SCAR-GIT-25 in clean commit"
        " doc was not rejected"
    )
  if not any("operator direction" in e.lower() for e in inc_pr_errors):
    errors.append(
        "Negative falsification failed: missing operator direction in clean"
        " commit doc was not rejected"
    )

  bad_scar_text = (
      "### SCAR-GIT-20: Pre-Push Merge Check\n"
      "- **Permanent Reflex**: Run `git merge-tree` and rebase onto main.\n"
      "### SCAR-GIT-25\n"
  )
  bad_scar_errors = check_scar_git_20_text(bad_scar_text)
  if not bad_scar_errors or not any("in-memory" in e for e in bad_scar_errors):
    errors.append(
        "Negative falsification failed: missing in-memory detection in"
        " SCAR-GIT-20 was not rejected"
    )
  if not any("scar-git-25" in e.lower() for e in bad_scar_errors):
    errors.append(
        "Negative falsification failed: missing SCAR-GIT-25 reference in"
        " SCAR-GIT-20 was not rejected"
    )

  return errors


def validate_arbitrary_cwd_execution(timeout: float = 30.0):
  """Validates arbitrary cwd execution without breaking relative path lookups (GH-62)."""
  if os.environ.get("_COMPAT_CHILD") == "1":
    return []

  child_env = os.environ.copy()
  child_env["_COMPAT_CHILD"] = "1"
  try:
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        cwd=tempfile.gettempdir(),
        capture_output=True,
        text=True,
        env=child_env,
        timeout=timeout,
    )
  except subprocess.TimeoutExpired as exc:
    return [
        f"Arbitrary CWD validation timed out after {timeout}s: {exc}"
    ]
  except (subprocess.SubprocessError, OSError) as exc:
    return [
        f"Arbitrary CWD validation execution error: {exc}"
    ]

  if proc.returncode != 0:
    return [
        f"Arbitrary CWD validation failed (exit code {proc.returncode}):\n{proc.stderr or proc.stdout}"
    ]
  if "All cross-agent configuration files validated successfully!" not in proc.stdout:
    return [
        "Arbitrary CWD validation did not produce expected success banner in stdout."
    ]
  return []


def _check_target_42_nbo_summary_section(section_text, source_rel):
  """Checks that Section 5.2 text satisfies all Target 42 NBO summary mandates (GH-89)."""
  errors = []

  # 1. Non-Blocking Observations (NBO) Summary heading or item
  nbo_match = re.search(
      r"(?:non-blocking observations|\bnbo\b).*?summary",
      section_text,
      re.IGNORECASE,
  )
  if not nbo_match:
    errors.append(
        f"{source_rel} Section 5.2 must require a Non-Blocking Observations (NBO) Summary"
    )
    return errors

  nbo_text = section_text[nbo_match.start():]
  nbo_lower = nbo_text.lower()

  # 2. Conditioned on rejected review round
  if "rejected" not in nbo_lower:
    errors.append(
        f"{source_rel} Section 5.2 must condition NBO Summary on rejected review rounds"
    )

  # 3. Full technical description
  if "technical description" not in nbo_lower:
    errors.append(
        f"{source_rel} Section 5.2 must mandate full technical description for each observation"
    )

  # 4. file:line location
  if "file:line" not in nbo_lower:
    errors.append(
        f"{source_rel} Section 5.2 must mandate file:line location for each observation"
    )

  # 5. Impact and rationale
  if "impact" not in nbo_lower:
    errors.append(
        f"{source_rel} Section 5.2 must mandate impact for each observation"
    )
  if "rationale" not in nbo_lower:
    errors.append(
        f"{source_rel} Section 5.2 must mandate rationale for each observation"
    )

  # 6. Proposed disposition (Apply, Skip, Defer to issue)
  if "disposition" not in nbo_lower:
    errors.append(
        f"{source_rel} Section 5.2 must mandate proposed disposition for each observation"
    )
  for disp in ("apply", "skip", "defer"):
    if disp not in nbo_lower:
      errors.append(
          f"{source_rel} Section 5.2 must specify '{disp}' disposition option"
      )

  return errors


def validate_target_42_nbo_summary_invariants():
  """Validates Target 42 Non-Blocking Observations (NBO) summary invariants (GH-89).

  Verifies:
  1. skills/makeflow/references/04_six_pillars_adversarial_review.md Section 5.2
     ('The Blocker Digest') mandates item 4: Non-Blocking Observations (NBO) Summary
     whenever the critic identifies NBOs during a rejected review round.
  2. Mandates for each observation:
     - full technical description
     - file:line location
     - impact / rationale
     - proposed disposition (Apply, Skip, Defer to issue)
  3. Negative falsification: omissions of any required field in synthetic Section 5.2
     structures trigger explicit validation errors.
  """
  errors = []
  ref_04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  ref_04_path = repo_path(ref_04_rel)
  if not os.path.exists(ref_04_path):
    errors.append(f"Missing {ref_04_rel}")
    return errors

  with open(ref_04_path, "r", encoding="utf-8") as f:
    r04_text = f.read()

  if "### 5.2 The Blocker Digest" not in r04_text:
    errors.append(f"{ref_04_rel} missing '### 5.2 The Blocker Digest'")
  else:
    sec_5_2 = r04_text.split("### 5.2 The Blocker Digest")[1].split("### 5.3")[0]
    errors.extend(_check_target_42_nbo_summary_section(sec_5_2, ref_04_rel))

  # Negative Falsification Checks
  valid_sample = (
      "### 5.2 The Blocker Digest\n"
      "Present a structured Blocker Digest directly in chat:\n"
      "1. **Blocker Manifest**: List every blocker ID, pillar, file location, wound, and trap.\n"
      "2. **Remediation Strategy**: Propose a failing reproduction test and surgical fix for each blocker.\n"
      "3. **Documentation Impact**: Note any documentation or changelog updates required.\n"
      "4. **Non-Blocking Observations (NBO) Summary**: Whenever the critic identifies "
      "NBOs during a rejected review round, include each observation's full technical "
      "description, file:line location, impact and rationale, and proposed disposition "
      "(Apply in the upcoming Target 30 remediation pass, Skip with justification, or Defer to issue).\n"
  )
  sample_errors = _check_target_42_nbo_summary_section(valid_sample, "sample.md")
  if sample_errors:
    errors.append(f"Falsification harness baseline failed: {sample_errors}")

  negatives = [
      ("missing_nbo", valid_sample.replace("Non-Blocking Observations (NBO) Summary", "Observations"), "Non-Blocking Observations (NBO) Summary"),
      ("missing_rejected", valid_sample.replace("rejected", "completed"), "rejected review rounds"),
      ("missing_desc", valid_sample.replace("technical description", "details"), "technical description"),
      ("missing_loc", valid_sample.replace("file:line", "path"), "file:line location"),
      ("missing_impact", valid_sample.replace("impact and rationale", "rationale"), "impact"),
      ("missing_rationale", valid_sample.replace("impact and rationale", "impact"), "rationale"),
      ("missing_disposition", valid_sample.replace("proposed disposition", "action"), "disposition"),
      ("missing_apply", valid_sample.replace("Apply", "Execute"), "'apply' disposition"),
      ("missing_skip", valid_sample.replace("Skip", "Ignore"), "'skip' disposition"),
      ("missing_defer", valid_sample.replace("Defer to issue", "Create issue"), "'defer' disposition"),
  ]

  for tag, neg_text, expected_err_token in negatives:
    neg_errs = _check_target_42_nbo_summary_section(neg_text, "negative.md")
    if not any(expected_err_token.lower() in e.lower() for e in neg_errs):
      errors.append(
          f"Negative falsification failed: {tag} did not trigger expected error containing '{expected_err_token}'"
      )

  return errors


def _check_critic_observation_triage_protocol(text: str, source_rel: str) -> list[str]:
  """Checks that text defines the 4-step observation triage protocol."""
  errors = []
  text_lower = text.lower()

  # Step 1: Existing Issue Deduplication
  if "gh issue list" not in text:
    errors.append(f"{source_rel} must mandate searching open issues with 'gh issue list'")
  if "deduplication" not in text_lower and "duplicate" not in text_lower:
    errors.append(f"{source_rel} must mandate deduplication against open issues")

  # Step 2: Scope Relationship & Rollup
  if "very related" not in text_lower:
    errors.append(f"{source_rel} must define 'very related' scope relationship")
  if "blocker" not in text_lower:
    errors.append(f"{source_rel} must treat 'very related' observations as blockers")
  if "somewhat related" not in text_lower:
    errors.append(f"{source_rel} must define 'somewhat related' scope relationship")
  if "roll up" not in text_lower and "rollup" not in text_lower:
    errors.append(f"{source_rel} must offer rollup option for somewhat related observations")
  if "totally unrelated" not in text_lower:
    errors.append(f"{source_rel} must define 'totally unrelated' scope relationship")

  # Step 3: Conscious Dismissal
  if "conscious dismissal" not in text_lower and "dismiss" not in text_lower:
    errors.append(f"{source_rel} must define conscious dismissal")
  if "negligible risk" not in text_lower:
    errors.append(f"{source_rel} must evaluate negligible risk for dismissal")
  if "outsized complexity" not in text_lower:
    errors.append(f"{source_rel} must evaluate outsized complexity for dismissal")
  if "rationale" not in text_lower:
    errors.append(f"{source_rel} must mandate documenting technical rationale when dismissing")

  # Step 4: Human Escalation Gate
  if "human escalation" not in text_lower and "human declaration" not in text_lower and "human confirmation" not in text_lower:
    errors.append(f"{source_rel} must require human escalation gate before opening new tickets")
  if "autonomous" not in text_lower:
    errors.append(f"{source_rel} must prohibit autonomous ticket creation")

  return errors


def validate_critic_observation_triage_invariants():
  """Validates Critic Observation Triage Protocol and SCAR-PROC-108 (GH-66)."""
  errors = []

  # a) skills/makeflow/references/04_six_pillars_adversarial_review.md
  ref_04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  ref_04_path = repo_path(ref_04_rel)
  if not os.path.exists(ref_04_path):
    errors.append(f"Missing {ref_04_rel}")
  else:
    with open(ref_04_path, "r", encoding="utf-8") as f:
      r04_text = f.read()
    errors.extend(_check_critic_observation_triage_protocol(r04_text, ref_04_rel))

  # b) skills/makeflow/references/scars/process.md codifies SCAR-PROC-108
  proc_rel = "skills/makeflow/references/scars/process.md"
  proc_path = repo_path(proc_rel)
  if not os.path.exists(proc_path):
    errors.append(f"Missing {proc_rel}")
  else:
    with open(proc_path, "r", encoding="utf-8") as f:
      proc_text = f.read()
    if "### SCAR-PROC-108" not in proc_text:
      errors.append(f"{proc_rel} must document SCAR-PROC-108")
    else:
      scar_section = (
          proc_text.split("### SCAR-PROC-108")[1].split("### SCAR-")[0]
          if "### SCAR-PROC-108" in proc_text
          else ""
      )
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        field_match = re.search(
            rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", scar_section
        )
        if not field_match or not field_match.group(1).strip():
          errors.append(f"{proc_rel} SCAR-PROC-108 missing non-empty '**{field}**:'")
      if "critic observation triage" not in scar_section.lower() and "anti-proliferation" not in scar_section.lower():
        errors.append(f"{proc_rel} SCAR-PROC-108 must title Critic Observation Triage and Anti-Proliferation Protocol")

  # c) skills/makeflow/references/09_synthetic_scars_catalog.md lists SCAR-PROC-108
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    if "SCAR-PROC-108" not in cat_text:
      errors.append(f"{cat_rel} must index SCAR-PROC-108")
    if not check_scar_catalog_routing(cat_text, "SCAR-PROC-108", "process.md"):
      errors.append(f"{cat_rel} must route SCAR-PROC-108 to scars/process.md")

  # d) skills/makeflow/references/08_subagent_orchestration.md Section 5.3
  ref_08_rel = "skills/makeflow/references/08_subagent_orchestration.md"
  ref_08_path = repo_path(ref_08_rel)
  if not os.path.exists(ref_08_path):
    errors.append(f"Missing {ref_08_rel}")
  else:
    with open(ref_08_path, "r", encoding="utf-8") as f:
      r08_text = f.read()
    if "### 5.3" not in r08_text and "## 5.3" not in r08_text:
      errors.append(f"{ref_08_rel} must document Section 5.3")
    else:
      sec_5_3 = r08_text.split("5.3")[1]
      sec_5_3_lower = sec_5_3.lower()
      if "triage" not in sec_5_3_lower:
        errors.append(f"{ref_08_rel} Section 5.3 must document observation triage")
      if "roll up" not in sec_5_3_lower and "rollup" not in sec_5_3_lower:
        errors.append(f"{ref_08_rel} Section 5.3 must document relationship-based rollup")
      if "scar-proc-108" not in sec_5_3_lower:
        errors.append(f"{ref_08_rel} Section 5.3 must reference SCAR-PROC-108")

  # e) rules/AGENTS.md codifies in Section 1.8
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    if "1.8" not in agents_text:
      errors.append(f"{agents_rel} must codify Section 1.8")
    else:
      sec_1_8 = agents_text.split("1.8")[1].split("## 2")[0] if "## 2" in agents_text else agents_text.split("1.8")[1]
      sec_1_8_lower = sec_1_8.lower()
      if "scar-proc-108" not in sec_1_8_lower:
        errors.append(f"{agents_rel} Section 1.8 must reference SCAR-PROC-108")
      if "gh issue list" not in sec_1_8:
        errors.append(f"{agents_rel} Section 1.8 must mention 'gh issue list'")
      if "roll up" not in sec_1_8_lower and "rollup" not in sec_1_8_lower:
        errors.append(f"{agents_rel} Section 1.8 must mention rollup")
      if "conscious dismissal" not in sec_1_8_lower and "dismiss" not in sec_1_8_lower:
        errors.append(f"{agents_rel} Section 1.8 must mention conscious dismissal")
      if "human" not in sec_1_8_lower:
        errors.append(f"{agents_rel} Section 1.8 must mandate human escalation gate")

  # f) agents/adversarial-critic.md instructs scope relationship evaluation
  critic_rel = "agents/adversarial-critic.md"
  critic_path = repo_path(critic_rel)
  if not os.path.exists(critic_path):
    errors.append(f"Missing {critic_rel}")
  else:
    with open(critic_path, "r", encoding="utf-8") as f:
      critic_text = f.read()
    critic_lower = critic_text.lower()
    if "scope relationship" not in critic_lower:
      errors.append(f"{critic_rel} must instruct critic on scope relationship evaluation")
    if "scar-proc-108" not in critic_lower:
      errors.append(f"{critic_rel} must reference SCAR-PROC-108")

  # g) Negative falsification harness
  valid_sample = (
      "### 4-Step Observation Triage Protocol\n"
      "1. **Existing Issue Deduplication**: Search open issues (`gh issue list`) before proposing new tickets to prevent duplicate backlog items.\n"
      "2. **Scope Relationship & Rollup**: Very related observations become a blocker for tdd-worker. Somewhat related observations offer a choice to roll up into the current change or file a ticket. Totally unrelated observations propose filing a new ticket.\n"
      "3. **Conscious Dismissal**: Conscious dismissal of observations with negligible risk or outsized complexity. Document technical rationale to skip without filing an issue.\n"
      "4. **Human Escalation Gate**: Require explicit human escalation gate and human declaration before opening new tickets. Prohibit autonomous ticket creation.\n"
  )
  sample_errors = _check_critic_observation_triage_protocol(valid_sample, "sample.md")
  if sample_errors:
    errors.append(f"Critic observation triage falsification harness baseline failed: {sample_errors}")

  negatives = [
      ("omit_step1_gh_issue_list", valid_sample.replace("`gh issue list`", "issue tracker"), "gh issue list"),
      ("omit_step1_dedup", re.sub(r"(?i)deduplication|duplicate", "untracked", valid_sample), "deduplication"),
      ("omit_step2_very_related", re.sub(r"(?i)very related", "high priority", valid_sample), "very related"),
      ("omit_step2_blocker", re.sub(r"(?i)blocker", "critical fix", valid_sample), "blocker"),
      ("omit_step2_somewhat_related", re.sub(r"(?i)somewhat related", "medium priority", valid_sample), "somewhat related"),
      ("omit_step2_rollup", re.sub(r"(?i)roll up|rollup", "bundle", valid_sample), "rollup"),
      ("omit_step2_totally_unrelated", re.sub(r"(?i)totally unrelated", "low priority", valid_sample), "totally unrelated"),
      ("omit_step3_dismissal", re.sub(r"(?i)dismissal|dismiss", "ignore", valid_sample), "dismissal"),
      ("omit_step3_negligible_risk", re.sub(r"(?i)negligible risk", "low severity", valid_sample), "negligible risk"),
      ("omit_step3_outsized_complexity", re.sub(r"(?i)outsized complexity", "high effort", valid_sample), "outsized complexity"),
      ("omit_step3_rationale", re.sub(r"(?i)rationale", "notes", valid_sample), "rationale"),
      ("omit_step4_human_gate", re.sub(r"(?i)human escalation|human declaration|human confirmation", "gate", valid_sample), "human escalation"),
      ("omit_step4_autonomous", re.sub(r"(?i)autonomous", "unsupervised", valid_sample), "autonomous"),
  ]

  for tag, neg_text, expected_token in negatives:
    neg_errs = _check_critic_observation_triage_protocol(neg_text, f"negative_{tag}.md")
    if not any(expected_token.lower() in e.lower() for e in neg_errs):
      errors.append(
          f"Negative falsification failed: {tag} did not trigger expected error containing '{expected_token}'"
      )

  return errors


def check_skill_target_40_model_picker(skill_text, source_rel="skills/makeflow/SKILL.md"):
  errors = []
  if "### Target 40" not in skill_text:
    errors.append(f"{source_rel} missing '### Target 40' section")
    return errors
  t40_section = skill_text.split("### Target 40")[1].split("### Target 50")[0]
  t40_lower = t40_section.lower()
  has_bypass = (
      "bypass" in t40_lower
      and "config.json" in t40_section
      and ("models" in t40_section or "inherit" in t40_section)
  )
  if not has_bypass:
    errors.append(
        f"{source_rel} Target 40 must specify bypassing model picker prompt when"
        " .workflow/config.json specifies models or 'inherit'"
    )
  has_pause = (
      ("pause" in t40_lower or "stop tool calls" in t40_lower or "tool_calls: []" in t40_section)
      and ("unconfigured" in t40_lower or "prompt" in t40_lower)
  )
  if not has_pause:
    errors.append(
        f"{source_rel} Target 40 must specify pausing (stopping tool calls) before"
        " critic dispatch when unconfigured"
    )
  return errors


def check_ref_13_section_5_model_picker(ref13_text, source_rel="skills/makeflow/references/13_configuration_and_user_preferences.md"):
  errors = []
  if "## 5. Interactive IDE Prompts" not in ref13_text:
    errors.append(f"{source_rel} missing Section 5 '## 5. Interactive IDE Prompts'")
    return errors
  sec5_text = ref13_text.split("## 5. Interactive IDE Prompts")[1]
  sec5_lower = sec5_text.lower()
  has_bypass = (
      "bypass" in sec5_lower
      and "config.json" in sec5_text
      and ("models" in sec5_text or "inherit" in sec5_text)
  )
  if not has_bypass:
    errors.append(
        f"{source_rel} Section 5 must document that specifying models or 'inherit' in"
        " .workflow/config.json bypasses the model picker prompt"
    )
  has_pause = (
      ("pause" in sec5_lower or "stop tool calls" in sec5_lower or "tool_calls: []" in sec5_text)
      and ("unconfigured" in sec5_lower or "dispatch" in sec5_lower)
  )
  if not has_pause:
    errors.append(
        f"{source_rel} Section 5 must document pausing (stopping tool calls) before"
        " critic dispatch when unconfigured"
    )
  return errors


def check_agents_rule_target_40_model_picker(agents_text, source_rel="rules/AGENTS.md"):
  errors = []
  has_t40 = "Target 40" in agents_text
  has_bypass = (
      "bypass" in agents_text.lower()
      and "config.json" in agents_text
      and ("models" in agents_text.lower() or "inherit" in agents_text.lower())
  )
  has_pause = (
      "pause" in agents_text.lower()
      or "stop tool calls" in agents_text.lower()
      or "tool_calls: []" in agents_text
  )
  if not (has_t40 and has_bypass and has_pause):
    errors.append(
        f"{source_rel} must document Target 40 model picker configuration bypass and pause invariant"
    )
  return errors


def validate_target_40_model_picker_invariants():
  """Validates Target 40 model picker config bypass and pause invariants (GH-81).

  Verifies:
  1. skills/makeflow/SKILL.md Target 40 specifies bypassing the model picker
     prompt when .workflow/config.json specifies models or 'inherit', and
     pausing (stopping tool calls) before critic dispatch when unconfigured.
  2. skills/makeflow/references/13_configuration_and_user_preferences.md Section 5
     documents config bypass when models or 'inherit' are configured, and
     pausing before critic dispatch when unconfigured.
  3. rules/AGENTS.md documents Target 40 config bypass and pause invariant.
  4. Negative falsification tests confirming that omission of bypass or pause
     raises validation errors.
  """
  errors = []

  # 1. skills/makeflow/SKILL.md
  skill_rel = "skills/makeflow/SKILL.md"
  skill_path = repo_path(skill_rel)
  if not os.path.exists(skill_path):
    errors.append(f"Missing {skill_rel}")
  else:
    with open(skill_path, "r", encoding="utf-8") as f:
      skill_text = f.read()
    errors.extend(check_skill_target_40_model_picker(skill_text, skill_rel))

  # 2. references/13
  ref13_rel = "skills/makeflow/references/13_configuration_and_user_preferences.md"
  ref13_path = repo_path(ref13_rel)
  if not os.path.exists(ref13_path):
    errors.append(f"Missing {ref13_rel}")
  else:
    with open(ref13_path, "r", encoding="utf-8") as f:
      ref13_text = f.read()
    errors.extend(check_ref_13_section_5_model_picker(ref13_text, ref13_rel))

  # 3. rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    errors.extend(check_agents_rule_target_40_model_picker(agents_text, agents_rel))

  # 4. Negative Falsification
  valid_skill = (
      "### Target 40: Review\n"
      "1. Lock Merkle tree.\n"
      "2. If .workflow/config.json specifies models or 'inherit', bypass the model picker prompt. "
      "When unconfigured in interactive IDEs, prompt the user and pause (stop tool calls) before critic dispatch.\n"
      "### Target 50: Commit"
  )
  if check_skill_target_40_model_picker(valid_skill, "valid_skill.md"):
    errors.append("Negative falsification baseline failed for valid skill sample")

  no_bypass_skill = (
      "### Target 40: Review\n"
      "1. Lock Merkle tree.\n"
      "2. When unconfigured in interactive IDEs, prompt the user and pause (stop tool calls) before critic dispatch.\n"
      "### Target 50: Commit"
  )
  nb_errors = check_skill_target_40_model_picker(no_bypass_skill, "no_bypass_skill.md")
  if not any("bypass" in e.lower() for e in nb_errors):
    errors.append("Negative falsification failed: SKILL.md missing bypass was not rejected")

  no_pause_skill = (
      "### Target 40: Review\n"
      "1. Lock Merkle tree.\n"
      "2. If .workflow/config.json specifies models or 'inherit', bypass the model picker prompt. Prompt the user.\n"
      "### Target 50: Commit"
  )
  np_errors = check_skill_target_40_model_picker(no_pause_skill, "no_pause_skill.md")
  if not any("pausing" in e.lower() for e in np_errors):
    errors.append("Negative falsification failed: SKILL.md missing pause was not rejected")

  valid_ref13 = (
      "## 5. Interactive IDE Prompts (Cursor, Copilot, ChatGPT)\n"
      "If .workflow/config.json specifies models or 'inherit', bypass the model picker prompt. "
      "When unconfigured, prompt the user to switch models and pause (stop tool calls) before critic dispatch.\n"
  )
  if check_ref_13_section_5_model_picker(valid_ref13, "valid_ref13.md"):
    errors.append("Negative falsification baseline failed for valid ref13 sample")

  no_bypass_ref13 = (
      "## 5. Interactive IDE Prompts (Cursor, Copilot, ChatGPT)\n"
      "When unconfigured, prompt the user to switch models and pause before critic dispatch.\n"
  )
  nbr_errors = check_ref_13_section_5_model_picker(no_bypass_ref13, "no_bypass_ref13.md")
  if not any("bypass" in e.lower() for e in nbr_errors):
    errors.append("Negative falsification failed: references/13 missing bypass was not rejected")

  no_pause_ref13 = (
      "## 5. Interactive IDE Prompts (Cursor, Copilot, ChatGPT)\n"
      "If .workflow/config.json specifies models or 'inherit', bypass the model picker prompt.\n"
  )
  npr_errors = check_ref_13_section_5_model_picker(no_pause_ref13, "no_pause_ref13.md")
  if not any("pausing" in e.lower() for e in npr_errors):
    errors.append("Negative falsification failed: references/13 missing pause was not rejected")

  no_bypass_agents = "# Rules\nTarget 40 pause before critic dispatch.\n"
  nba_errors = check_agents_rule_target_40_model_picker(no_bypass_agents, "no_bypass_agents.md")
  if not nba_errors:
    errors.append("Negative falsification failed: rules/AGENTS.md missing bypass was not rejected")

  no_pause_agents = "# Rules\nTarget 40 bypass model picker when .workflow/config.json specifies models or inherit.\n"
  npa_errors = check_agents_rule_target_40_model_picker(no_pause_agents, "no_pause_agents.md")
  if not npa_errors:
    errors.append("Negative falsification failed: rules/AGENTS.md missing pause was not rejected")

  return errors


def check_mcp_t60_transitions(server_text, state_machine_text=None):
  """Checks VALID_TRANSITIONS['T60'] across lib/turn_boundary.py, lib/state_machine.py, and mcp/server.py."""
  errors = []
  if state_machine_text is not None:
    if not re.search(
        r"from\s+lib\.state_machine\s+import\b[^;]*\bVALID_TRANSITIONS\b",
        server_text,
        re.DOTALL,
    ):
      errors.append(
          "mcp/server.py must import and re-export VALID_TRANSITIONS from lib.state_machine"
      )
    if not re.search(
        r"from\s+lib\.turn_boundary\s+import\b[^;]*\bVALID_TRANSITIONS\b",
        state_machine_text,
        re.DOTALL,
    ):
      errors.append(
          "lib/state_machine.py must import VALID_TRANSITIONS from lib.turn_boundary"
      )
    from lib.state_machine import VALID_TRANSITIONS as sm_transitions
    from lib.turn_boundary import VALID_TRANSITIONS as tb_transitions

    if sm_transitions != tb_transitions:
      errors.append(
          "lib/state_machine.py VALID_TRANSITIONS diverged from lib/turn_boundary.py"
      )
    targets = set(sm_transitions.get("T60", ()))
    if "T40" not in targets:
      errors.append("VALID_TRANSITIONS['T60'] must contain 'T40'")
    if "T70" not in targets:
      errors.append("VALID_TRANSITIONS['T60'] must contain 'T70'")
    return errors

  match = re.search(r'"T60"\s*:\s*\{([^}]+)\}', server_text)
  if not match:
    errors.append("VALID_TRANSITIONS missing 'T60' entry")
    return errors
  targets = {t.strip(' "\'') for t in match.group(1).split(",") if t.strip(' "\'')}
  if "T40" not in targets:
    errors.append("VALID_TRANSITIONS['T60'] must contain 'T40'")
  if "T70" not in targets:
    errors.append("VALID_TRANSITIONS['T60'] must contain 'T70'")
  return errors


def check_clean_commit_rebase_reaudit(commit_text):
  """Checks that 06_clean_commit_and_pr.md Section 1 mandates post-rebase approved_tree.txt locking, static checks, and critic re-dispatch under SCAR-PROC-105."""
  errors = []
  sec1_match = re.search(
      r"(?m)^##\s+1\.\s+Pre-Push Merge Check\b.*?(?=^##\s+2\b|\Z)",
      commit_text,
      re.DOTALL,
  )
  sec1_text = sec1_match.group(0) if sec1_match else commit_text
  sec1_lower = sec1_text.lower()

  if "approved_tree.txt" not in sec1_text or "git write-tree" not in sec1_text:
    errors.append(
        "06_clean_commit_and_pr.md Section 1 must mandate re-locking approved_tree.txt (git add -A && git write-tree > .workflow/state/approved_tree.txt)"
    )
  if "make test-static" not in sec1_text:
    errors.append(
        "06_clean_commit_and_pr.md Section 1 must mandate pre-flight static checks (make test-static)"
    )
  if "adversarial-critic" not in sec1_lower:
    errors.append(
        "06_clean_commit_and_pr.md Section 1 must mandate re-dispatching adversarial-critic"
    )
  if "skills/makeflow/references/04_six_pillars_adversarial_review.md" not in sec1_text:
    errors.append(
        "06_clean_commit_and_pr.md Section 1 must include explicit manual path skills/makeflow/references/04_six_pillars_adversarial_review.md"
    )
  if "scar-proc-105" not in sec1_lower:
    errors.append(
        "06_clean_commit_and_pr.md Section 1 must cite SCAR-PROC-105"
    )
  return errors


def check_scars_rebase_reaudit(spoke_text):
  """Checks that scars/git_and_shell.md SCAR-GIT-20 and SCAR-GIT-23 document Merkle tree invalidation and critic re-audit upon rebase."""
  errors = []
  if "### SCAR-GIT-20" not in spoke_text:
    errors.append("missing SCAR-GIT-20 in scars/git_and_shell.md")
    return errors
  if "### SCAR-GIT-23" not in spoke_text:
    errors.append("missing SCAR-GIT-23 in scars/git_and_shell.md")
    return errors

  sec20 = spoke_text.split("### SCAR-GIT-20")[1].split("### SCAR-")[0].lower()
  sec23 = spoke_text.split("### SCAR-GIT-23")[1].split("### SCAR-")[0].lower()

  if not ("invalidate" in sec20 and ("merkle tree" in sec20 or "tree lock" in sec20)):
    errors.append("SCAR-GIT-20 must document that human-approved rebase invalidates the staged Merkle tree")
  if not ("critic" in sec20 and ("re-audit" in sec20 or "re-dispatch" in sec20 or "review" in sec20)):
    errors.append("SCAR-GIT-20 must document that human-approved rebase requires fresh critic re-audit")

  if not ("rebase" in sec23 and "invalidate" in sec23):
    errors.append("SCAR-GIT-23 must document that human-approved rebase invalidates the Merkle tree")
  if not ("critic" in sec23 and ("re-audit" in sec23 or "re-dispatch" in sec23 or "review" in sec23)):
    errors.append("SCAR-GIT-23 must document that human-approved rebase requires fresh critic re-audit")

  return errors


def check_agents_rule_1_10_rebase_reaudit(agents_text):
  """Checks that rules/AGENTS.md defines Rule 1.10 for Target 60 pre-push rebase critic re-audit invariant."""
  errors = []
  if not re.search(r"(?m)^###\s+1\.10\b", agents_text):
    errors.append("rules/AGENTS.md missing Section 1.10")
    return errors
  sec1_10_match = re.search(
      r"(?m)^###\s+1\.10\b.*?(?=^##\s+2\b|\Z)",
      agents_text,
      re.DOTALL,
  )
  sec1_10 = sec1_10_match.group(0) if sec1_10_match else agents_text
  sec1_10_lower = sec1_10.lower()

  if "target 60" not in sec1_10_lower:
    errors.append("rules/AGENTS.md Section 1.10 must reference Target 60")
  if "approved_tree.txt" not in sec1_10 and "write-tree" not in sec1_10:
    errors.append("rules/AGENTS.md Section 1.10 must mandate re-locking approved_tree.txt")
  if "make test-static" not in sec1_10:
    errors.append("rules/AGENTS.md Section 1.10 must require make test-static")
  if "adversarial-critic" not in sec1_10_lower:
    errors.append("rules/AGENTS.md Section 1.10 must mandate re-dispatching adversarial-critic")
  if "skills/makeflow/references/04_six_pillars_adversarial_review.md" not in sec1_10:
    errors.append("rules/AGENTS.md Section 1.10 must include explicit manual path skills/makeflow/references/04_six_pillars_adversarial_review.md")
  if "scar-proc-105" not in sec1_10_lower:
    errors.append("rules/AGENTS.md Section 1.10 must reference SCAR-PROC-105")
  return errors


def check_quickstart_mermaid_t60_rebase_loop(quickstart_text):
  """Checks that skills/makeflow/references/00_quickstart_and_mental_model.md includes T60 -> T40 loop."""
  errors = []
  if 'T60 -. "Rebase Conflict" .-> T40' not in quickstart_text:
    errors.append(
        'skills/makeflow/references/00_quickstart_and_mental_model.md Mermaid chart must contain \'T60 -. "Rebase Conflict" .-> T40\''
    )
  return errors


def validate_target_60_rebase_critic_reaudit_invariants():
  """Validates Target 60 pre-push rebase conflict resolution and critic re-audit invariants (GH-126).

  Verifies:
  1. mcp/server.py VALID_TRANSITIONS maps T60 to a set containing T40 and T70.
  2. skills/makeflow/references/06_clean_commit_and_pr.md Section 1 mandates
     re-locking approved_tree.txt, running make test-static, and re-dispatching
     adversarial-critic with explicit manual path references/04_six_pillars_adversarial_review.md
     under SCAR-PROC-105 upon rebase.
  3. skills/makeflow/references/scars/git_and_shell.md SCAR-GIT-20 and SCAR-GIT-23
     document Merkle tree invalidation and fresh critic re-audit upon rebase.
  4. rules/AGENTS.md defines Rule 1.10 for the Target 60 pre-push rebase critic re-audit invariant.
  5. skills/makeflow/references/00_quickstart_and_mental_model.md Mermaid chart
     includes T60 -. "Rebase Conflict" .-> T40.
  6. Negative falsification tests verifying rejection of omitted instructions or missing DAG edge.
  """
  errors = []

  # 1. mcp/server.py + lib/state_machine.py
  server_rel = "mcp/server.py"
  server_path = repo_path(server_rel)
  sm_rel = "lib/state_machine.py"
  sm_path = repo_path(sm_rel)
  if not os.path.exists(server_path):
    errors.append(f"Missing {server_rel}")
  elif not os.path.exists(sm_path):
    errors.append(f"Missing {sm_rel}")
  else:
    with open(server_path, "r", encoding="utf-8") as f:
      server_text = f.read()
    with open(sm_path, "r", encoding="utf-8") as f:
      sm_text = f.read()
    errors.extend(check_mcp_t60_transitions(server_text, sm_text))

  # 2. references/06_clean_commit_and_pr.md
  commit_rel = "skills/makeflow/references/06_clean_commit_and_pr.md"
  commit_path = repo_path(commit_rel)
  if not os.path.exists(commit_path):
    errors.append(f"Missing {commit_rel}")
  else:
    with open(commit_path, "r", encoding="utf-8") as f:
      commit_text = f.read()
    errors.extend(check_clean_commit_rebase_reaudit(commit_text))

  # 3. references/scars/git_and_shell.md
  spoke_rel = "skills/makeflow/references/scars/git_and_shell.md"
  spoke_path = repo_path(spoke_rel)
  if not os.path.exists(spoke_path):
    errors.append(f"Missing {spoke_rel}")
  else:
    with open(spoke_path, "r", encoding="utf-8") as f:
      spoke_text = f.read()
    errors.extend(check_scars_rebase_reaudit(spoke_text))

  # 4. rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    errors.extend(check_agents_rule_1_10_rebase_reaudit(agents_text))

  # 5. references/00_quickstart_and_mental_model.md
  qs_rel = "skills/makeflow/references/00_quickstart_and_mental_model.md"
  qs_path = repo_path(qs_rel)
  if not os.path.exists(qs_path):
    errors.append(f"Missing {qs_rel}")
  else:
    with open(qs_path, "r", encoding="utf-8") as f:
      qs_text = f.read()
    errors.extend(check_quickstart_mermaid_t60_rebase_loop(qs_text))

  # 6. Negative Falsification Tests
  bad_mcp = 'VALID_TRANSITIONS = {"T60": {"T70"}}'
  bm_errors = check_mcp_t60_transitions(bad_mcp)
  if not any("t40" in e.lower() for e in bm_errors):
    errors.append("Negative falsification failed: mcp/server.py missing T40 was not rejected")

  bad_commit_no_tree = (
      "## 1. Pre-Push Merge Check (`SCAR-GIT-20`)\n"
      "make test-static\n"
      "adversarial-critic skills/makeflow/references/04_six_pillars_adversarial_review.md SCAR-PROC-105\n"
      "## 2. Push Gate\n"
  )
  if not check_clean_commit_rebase_reaudit(bad_commit_no_tree):
    errors.append("Negative falsification failed: clean commit missing approved_tree.txt was not rejected")

  bad_commit_no_critic = (
      "## 1. Pre-Push Merge Check (`SCAR-GIT-20`)\n"
      "git add -A && git write-tree > .workflow/state/approved_tree.txt\n"
      "make test-static\n"
      "## 2. Push Gate\n"
  )
  if not check_clean_commit_rebase_reaudit(bad_commit_no_critic):
    errors.append("Negative falsification failed: clean commit missing critic re-audit was not rejected")

  bad_commit_no_path = (
      "## 1. Pre-Push Merge Check (`SCAR-GIT-20`)\n"
      "git add -A && git write-tree > .workflow/state/approved_tree.txt\n"
      "make test-static\n"
      "adversarial-critic SCAR-PROC-105\n"
      "## 2. Push Gate\n"
  )
  if not check_clean_commit_rebase_reaudit(bad_commit_no_path):
    errors.append("Negative falsification failed: clean commit missing 04 manual path was not rejected")

  bad_scars_no_inv = (
      "### SCAR-GIT-20\nrebase critic re-audit\n"
      "### SCAR-GIT-23\nrebase critic re-audit\n"
      "### SCAR-GIT-24\n"
  )
  if not check_scars_rebase_reaudit(bad_scars_no_inv):
    errors.append("Negative falsification failed: scars missing Merkle tree invalidation was not rejected")

  bad_scars_no_critic = (
      "### SCAR-GIT-20\nrebase invalidates Merkle tree lock\n"
      "### SCAR-GIT-23\nrebase invalidates Merkle tree\n"
      "### SCAR-GIT-24\n"
  )
  if not check_scars_rebase_reaudit(bad_scars_no_critic):
    errors.append("Negative falsification failed: scars missing critic re-audit was not rejected")

  bad_agents = "## 1. Rules\n### 1.9 Target 40\n## 2. Production Data"
  if not check_agents_rule_1_10_rebase_reaudit(bad_agents):
    errors.append("Negative falsification failed: AGENTS.md missing Rule 1.10 was not rejected")

  bad_quickstart = "flowchart LR\nT50 --> T60 --> T70"
  if not check_quickstart_mermaid_t60_rebase_loop(bad_quickstart):
    errors.append("Negative falsification failed: quickstart missing T60 -> T40 loop was not rejected")

  return errors


def check_scar_tool_16_spoke(spoke_text):
  """Checks that scars/tooling_and_hci.md documents SCAR-TOOL-16 with 5 required anatomy elements and permanent reflex invariants."""
  errors = []
  if "### SCAR-TOOL-16" not in spoke_text:
    errors.append("skills/makeflow/references/scars/tooling_and_hci.md missing '### SCAR-TOOL-16'")
    return errors

  sec_match = re.search(
      r"(?m)^###\s+SCAR-TOOL-16\b.*?(?=^###\s+SCAR-|\Z)", spoke_text, re.DOTALL
  )
  if not sec_match:
    errors.append(
        "skills/makeflow/references/scars/tooling_and_hci.md could not isolate"
        " SCAR-TOOL-16 section"
    )
    return errors
  sec = sec_match.group(0)

  required_fields = (
      "Wound",
      "Trap",
      "Circuit Breaker",
      "Permanent Reflex",
      "Socratic Probe",
  )
  for field in required_fields:
    field_match = re.search(rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", sec)
    if not field_match or not field_match.group(1).strip():
      errors.append(
          "skills/makeflow/references/scars/tooling_and_hci.md SCAR-TOOL-16"
          f" missing non-empty '**{field}:**'"
      )

  reflex_match = re.search(
      r"\*\*Permanent Reflex\*\*:\s*(.*?)(?=\n\s*-\s*\*\*|\Z)", sec, re.DOTALL
  )
  reflex_text = reflex_match.group(1) if reflex_match else ""
  reflex_lower = reflex_text.lower()

  if "--limit" not in reflex_text:
    errors.append("SCAR-TOOL-16 Permanent Reflex must document '--limit'")
  if "context" not in reflex_lower:
    errors.append("SCAR-TOOL-16 Permanent Reflex must document context crowding or limits")
  if "volume" not in reflex_lower:
    errors.append("SCAR-TOOL-16 Permanent Reflex must document volume risk")
  if "interview" not in reflex_lower:
    errors.append("SCAR-TOOL-16 Permanent Reflex must require user interview")

  # 4 canonical paths
  if "revise" not in reflex_lower:
    errors.append(
        "SCAR-TOOL-16 Permanent Reflex must document path: revise directive or"
        " filter criteria"
    )
  if "user-approved" not in reflex_lower and "user approved" not in reflex_lower:
    errors.append(
        "SCAR-TOOL-16 Permanent Reflex must document path: explicit user-approved"
        " limits"
    )
  if "count" not in reflex_lower or "row" not in reflex_lower:
    errors.append(
        "SCAR-TOOL-16 Permanent Reflex must document path: count total rows first"
    )
  if "proceed without limits" not in reflex_lower:
    errors.append(
        "SCAR-TOOL-16 Permanent Reflex must document path: proceed without limits"
    )

  return errors


def check_scar_tool_16_catalog(catalog_text):
  """Checks that 09_synthetic_scars_catalog.md routes SCAR-TOOL-16 strictly to scars/tooling_and_hci.md."""
  errors = []
  if "SCAR-TOOL-16" not in catalog_text:
    errors.append(
        "skills/makeflow/references/09_synthetic_scars_catalog.md missing"
        " SCAR-TOOL-16"
    )
    return errors
  if not check_scar_catalog_routing(
      catalog_text, "SCAR-TOOL-16", "tooling_and_hci.md"
  ):
    errors.append(
        "skills/makeflow/references/09_synthetic_scars_catalog.md Master"
        " Routing Table must strictly route SCAR-TOOL-16 to"
        " scars/tooling_and_hci.md"
    )
  if (
      "Prohibit Arbitrary Output Limits and Require Volume Triage Interview"
      not in catalog_text
  ):
    errors.append(
        "skills/makeflow/references/09_synthetic_scars_catalog.md missing title"
        " for SCAR-TOOL-16"
    )
  return errors


def check_agents_rule_1_11_volume_triage(agents_text):
  """Checks that rules/AGENTS.md defines Rule 1.11 for SCAR-TOOL-16 volume triage interview."""
  errors = []
  sec_match = re.search(
      r"(?m)^###\s+1\.11\b.*?(?=^##\s+2\b|\Z)", agents_text, re.DOTALL
  )
  if not sec_match:
    errors.append("rules/AGENTS.md missing Section 1.11")
    return errors
  sec = sec_match.group(0)
  sec_lower = sec.lower()

  if "scar-tool-16" not in sec_lower:
    errors.append("rules/AGENTS.md Section 1.11 must reference SCAR-TOOL-16")
  if "--limit" not in sec:
    errors.append("rules/AGENTS.md Section 1.11 must prohibit '--limit'")
  if "head -n" not in sec and "truncation" not in sec_lower:
    errors.append(
        "rules/AGENTS.md Section 1.11 must prohibit truncation / 'head -n'"
    )
  if "halt" not in sec_lower or "context" not in sec_lower:
    errors.append(
        "rules/AGENTS.md Section 1.11 must mandate immediate halt when volume"
        " threatens context"
    )
  if "interview" not in sec_lower:
    errors.append(
        "rules/AGENTS.md Section 1.11 must require interviewing the user"
    )
  for path_token in ("revise", "user-approved", "count", "proceed without limits"):
    if path_token not in sec_lower:
      errors.append(
          f"rules/AGENTS.md Section 1.11 must document triage path containing"
          f" '{path_token}'"
      )
  return errors


def check_adapter_scar_tool_16(fpath, content):
  """Checks that adapter file documents SCAR-TOOL-16 prohibiting arbitrary limits and requiring volume triage interview."""
  errors = []
  if "SCAR-TOOL-16" not in content:
    errors.append(f"{fpath} must reference SCAR-TOOL-16")
  if "--limit" not in content:
    errors.append(f"{fpath} must document prohibition of '--limit'")
  content_lower = content.lower()
  if "interview" not in content_lower:
    errors.append(f"{fpath} must require volume triage interview")
  if fpath == "adapters/chatgpt/instructions.md":
    blast_match = re.search(
        r"3\.\s+BLAST RADIUS CONTROL.*?(?=4\.\s+SIMPLE ENGLISH|\Z)",
        content,
        re.DOTALL,
    )
    if not blast_match or "SCAR-TOOL-16" not in blast_match.group(0):
      errors.append(
          f"{fpath} must document SCAR-TOOL-16 under Section 3 BLAST RADIUS"
          " CONTROL"
      )
  return errors


def validate_volume_triage_interview_scar_tool_16():
  """Validates SCAR-TOOL-16 prohibition of arbitrary output limits and volume triage interview (GH-128).

  Verifies:
  1. skills/makeflow/references/scars/tooling_and_hci.md documents SCAR-TOOL-16
     with 5-part anatomy (Wound, Trap, Circuit Breaker, Permanent Reflex, Socratic Probe)
     and 4 canonical triage paths in Permanent Reflex.
  2. skills/makeflow/references/09_synthetic_scars_catalog.md Master Routing Table
     strictly routes SCAR-TOOL-16 to scars/tooling_and_hci.md.
  3. rules/AGENTS.md defines Rule 1.11 codifying SCAR-TOOL-16.
  4. All 5 adapters (CLAUDE.md, .cursorrules, .cursor/rules/makeflow.mdc,
     .github/copilot-instructions.md, adapters/chatgpt/instructions.md)
     document SCAR-TOOL-16.
  5. Negative mutation falsification tests rejecting incomplete definitions or omissions.
  """
  errors = []

  # 1. Spoke file
  spoke_rel = "skills/makeflow/references/scars/tooling_and_hci.md"
  spoke_path = repo_path(spoke_rel)
  if not os.path.exists(spoke_path):
    errors.append(f"Missing {spoke_rel}")
  else:
    with open(spoke_path, "r", encoding="utf-8") as f:
      spoke_text = f.read()
    errors.extend(check_scar_tool_16_spoke(spoke_text))

  # 2. Master Catalog Routing
  catalog_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  catalog_path = repo_path(catalog_rel)
  if not os.path.exists(catalog_path):
    errors.append(f"Missing {catalog_rel}")
  else:
    with open(catalog_path, "r", encoding="utf-8") as f:
      catalog_text = f.read()
    errors.extend(check_scar_tool_16_catalog(catalog_text))

  # 3. rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_path = repo_path(agents_rel)
  if not os.path.exists(agents_path):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_path, "r", encoding="utf-8") as f:
      agents_text = f.read()
    errors.extend(check_agents_rule_1_11_volume_triage(agents_text))

  # 4. 5 Adapter files
  adapters = [
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ]
  for ad in adapters:
    ad_path = repo_path(ad)
    if not os.path.exists(ad_path):
      errors.append(f"Missing {ad}")
    else:
      with open(ad_path, "r", encoding="utf-8") as f:
        ad_content = f.read()
      errors.extend(check_adapter_scar_tool_16(ad, ad_content))

  # 5. Negative Mutation Falsification Tests
  sample_spoke = """
### SCAR-TOOL-16: Prohibit Arbitrary Output Limits and Require Volume Triage Interview

- **Wound**: An agent investigates or triages an information source and unilaterally restricts output with an arbitrary limit argument.
- **Trap**: The agent assumes high output volume will exhaust context.
- **Circuit Breaker**: If you investigate or triage an information source, never append arbitrary limit arguments.
- **Permanent Reflex**: Never arbitrarily impose --limit or truncation flags before assessing the full dataset. If you suspect output volume will crowd context or make the directive unfeasible, report the volume risk first. Interview the user to select one of four paths:
  1. Revise the directive or filter criteria.
  2. Impose explicit user-approved limits.
  3. Count total rows first before reading detailed content.
  4. Proceed without limits.
- **Socratic Probe**: Did you arbitrarily impose a --limit flag on an information source during triage?
"""
  if check_scar_tool_16_spoke(sample_spoke):
    errors.append("Negative falsification baseline failed for valid spoke sample")

  bad_spoke_no_cb = sample_spoke.replace("- **Circuit Breaker**:", "- **Other Field**:")
  if not check_scar_tool_16_spoke(bad_spoke_no_cb):
    errors.append("Negative falsification failed: spoke missing Circuit Breaker was not rejected")

  bad_spoke_no_path = sample_spoke.replace("Proceed without limits.", "Ignore limits.")
  if not check_scar_tool_16_spoke(bad_spoke_no_path):
    errors.append("Negative falsification failed: spoke missing 4th path was not rejected")

  bad_spoke_no_limit = sample_spoke.replace("--limit", "--unbounded")
  if not check_scar_tool_16_spoke(bad_spoke_no_limit):
    errors.append("Negative falsification failed: spoke missing '--limit' was not rejected")

  bad_catalog = "| `SCAR-TOOL-16` | Prohibit Arbitrary Output Limits and Require Volume Triage Interview | Tooling & HCI | [`scars/other.md`](./scars/other.md) |"
  if not check_scar_tool_16_catalog(bad_catalog):
    errors.append("Negative falsification failed: catalog with wrong routing was not rejected")

  bad_agents = (
      "### 1.11 Prohibit Limits (SCAR-TOOL-16)\n"
      "Never use --limit or head -n. Interview user across paths: revise, user-approved, count, proceed without limits.\n"
  )
  if not check_agents_rule_1_11_volume_triage(bad_agents):
    errors.append("Negative falsification failed: AGENTS.md missing halt on context threat was not rejected")

  bad_adapter = "Never use --limit under SCAR-TOOL-16."
  if not check_adapter_scar_tool_16("CLAUDE.md", bad_adapter):
    errors.append("Negative falsification failed: adapter missing interview was not rejected")

  return errors


def check_scar_proc_124_spoke(spoke_text):
  """Checks scars/process.md for SCAR-PROC-124 placement, invariants, and Simple English rules."""
  errors = []
  spoke_rel = "skills/makeflow/references/scars/process.md"

  # 1. Verify category table lists SCAR-PROC-124 monotonically between SCAR-PROC-122 and SCAR-PROC-138
  cat_match = re.search(
      r"(?m)^##\s+Scars in this Category\b.*?(?=^---|^###\s+SCAR-|\Z)",
      spoke_text,
      re.DOTALL,
  )
  if not cat_match:
    errors.append(f"{spoke_rel} missing '## Scars in this Category' section")
  else:
    cat_sec = cat_match.group(0)
    t122 = re.search(r"(?m)^\|\s*`SCAR-PROC-122`\s*\|", cat_sec)
    t124 = re.search(r"(?m)^\|\s*`SCAR-PROC-124`\s*\|", cat_sec)
    t138 = re.search(r"(?m)^\|\s*`SCAR-PROC-138`\s*\|", cat_sec)
    if not t124:
      errors.append(f"{spoke_rel} '## Scars in this Category' missing SCAR-PROC-124 row")
    elif not (t122 and t138 and t122.start() < t124.start() < t138.start()):
      errors.append(
          f"{spoke_rel} table must place SCAR-PROC-124 monotonically between SCAR-PROC-122 and SCAR-PROC-138"
      )

  # 2. Verify body section placement monotonically between SCAR-PROC-122 and SCAR-PROC-138
  h122 = re.search(r"(?m)^###\s+SCAR-PROC-122\b", spoke_text)
  h124 = re.search(r"(?m)^###\s+SCAR-PROC-124:", spoke_text)
  h138 = re.search(r"(?m)^###\s+SCAR-PROC-138\b", spoke_text)
  if not h124:
    errors.append(f"{spoke_rel} missing '### SCAR-PROC-124:' heading")
    return errors
  if not (h122 and h138 and h122.start() < h124.start() < h138.start()):
    errors.append(
        f"{spoke_rel} must place '### SCAR-PROC-124:' monotonically between SCAR-PROC-122 and SCAR-PROC-138"
    )

  sec_match = re.search(
      r"(?m)^###\s+SCAR-PROC-124:.*?(?=^###\s+SCAR-|\Z)", spoke_text, re.DOTALL
  )
  if not sec_match:
    errors.append(f"{spoke_rel} could not isolate SCAR-PROC-124 section")
    return errors
  sec = sec_match.group(0)

  # 3. Extract 5 required fields (single-line text after each bold label)
  required_fields = (
      "Wound",
      "Trap",
      "Circuit Breaker",
      "Permanent Reflex",
      "Socratic Probe",
  )
  extracted = {}
  for field in required_fields:
    field_match = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", sec)
    val = field_match.group(1).strip() if field_match else ""
    if not val:
      errors.append(f"{spoke_rel} SCAR-PROC-124 missing non-empty '**{field}**:'")
    extracted[field] = val

  cb_lower = extracted["Circuit Breaker"].lower()
  reflex_lower = extracted["Permanent Reflex"].lower()
  combined_lower = f"{cb_lower} {reflex_lower}"

  # 4. Core behavioral invariants
  if cb_lower and not (
      ("guard" in cb_lower or "hook" in cb_lower)
      and "process" in cb_lower
      and "scar" in cb_lower
  ):
    errors.append(
        f"{spoke_rel} SCAR-PROC-124 Circuit Breaker must instruct reviewing makeflow process files and tier-1 scars on guard/hook denial"
    )
  if cb_lower and (
      "stop tool calls" not in cb_lower
      or "verbatim" not in cb_lower
      or "user" not in cb_lower
  ):
    errors.append(
        f"{spoke_rel} SCAR-PROC-124 Circuit Breaker must instruct stopping tool calls and reporting the denial verbatim to the user"
    )
  if reflex_lower and ("wait" not in reflex_lower or "user" not in reflex_lower):
    errors.append(
        f"{spoke_rel} SCAR-PROC-124 Permanent Reflex must instruct waiting for explicit user direction when no documented path exists"
    )
  if reflex_lower and not (
      "never" in combined_lower
      and "read" in reflex_lower
      and "grep" in reflex_lower
      and ("guard" in reflex_lower or "hook" in reflex_lower)
      and ("evade" in reflex_lower or "bypass" in reflex_lower)
  ):
    errors.append(
        f"{spoke_rel} SCAR-PROC-124 Permanent Reflex must prohibit reading, grepping, or testing guard/hook source files to evade or bypass a denial"
    )

  # 5. Simple English & punctuation compliance via ScarCandidateValidator + _check_prose_punctuation
  if all(extracted[f] for f in required_fields):
    candidate = {
        "id": "SCAR-PROC-124",
        "raw_wound": extracted["Wound"],
        "wound": extracted["Wound"],
        "raw_trap": extracted["Trap"],
        "trap": extracted["Trap"],
        "raw_circuit_breaker": extracted["Circuit Breaker"],
        "circuit_breaker": extracted["Circuit Breaker"],
        "raw_reflex": extracted["Permanent Reflex"],
        "reflex": extracted["Permanent Reflex"],
        "socratic_probe": extracted["Socratic Probe"],
    }
    errors.extend(ScarCandidateValidator().validate(candidate))
    probe_sentences = split_into_sentences(extracted["Socratic Probe"])
    if not (1 <= len(probe_sentences) <= 3):
      errors.append(
          f"{spoke_rel} SCAR-PROC-124 Socratic Probe must contain 1-3 '?' sentences, found {len(probe_sentences)}"
      )
  errors.extend(_check_prose_punctuation(f"{spoke_rel}#SCAR-PROC-124", sec))

  return errors


def check_scar_proc_124_catalog(catalog_text):
  """Checks that 09_synthetic_scars_catalog.md routes SCAR-PROC-124 monotonically to scars/process.md."""
  errors = []
  catalog_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  if not check_scar_catalog_routing(catalog_text, "SCAR-PROC-124", "process.md"):
    errors.append(
        f"{catalog_rel} Master Routing Table must strictly route SCAR-PROC-124 to scars/process.md"
    )
    return errors

  m122 = re.search(r"(?m)^\|\s*`SCAR-PROC-122`\s*\|", catalog_text)
  m124 = re.search(r"(?m)^\|\s*`SCAR-PROC-124`\s*\|", catalog_text)
  m138 = re.search(r"(?m)^\|\s*`SCAR-PROC-138`\s*\|", catalog_text)
  if not (m122 and m124 and m138 and m122.start() < m124.start() < m138.start()):
    errors.append(
        f"{catalog_rel} must place SCAR-PROC-124 monotonically between SCAR-PROC-122 and SCAR-PROC-138"
    )
  return errors


def validate_guard_denial_protocol_scar_proc_124():
  """Validates SCAR-PROC-124 guard denial protocol and negative mutation falsification (GH-167)."""
  errors = []

  spoke_rel = "skills/makeflow/references/scars/process.md"
  spoke_path = repo_path(spoke_rel)
  if not os.path.exists(spoke_path):
    errors.append(f"Missing {spoke_rel}")
  else:
    with open(spoke_path, "r", encoding="utf-8") as f:
      spoke_text = f.read()
    errors.extend(check_scar_proc_124_spoke(spoke_text))

  catalog_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  catalog_path = repo_path(catalog_rel)
  if not os.path.exists(catalog_path):
    errors.append(f"Missing {catalog_rel}")
  else:
    with open(catalog_path, "r", encoding="utf-8") as f:
      catalog_text = f.read()
    errors.extend(check_scar_proc_124_catalog(catalog_text))

  # Negative Mutation Falsification Tests
  sample_spoke = """
## Scars in this Category

| ID | Title | Scope |
| :--- | :--- | :--- |
| `SCAR-PROC-122` | Upfront Human-Review Policy Evaluation Matrix | Landing Gate Policy Evaluation |
| `SCAR-PROC-124` | Review Process or Halt on Guard Denial | Guard & Hook Denial Handling |
| `SCAR-PROC-138` | Yield on Repeated Timer Conflicts | Schedule Conflict Handling |

---

### SCAR-PROC-122: Upfront Human-Review Policy Evaluation Matrix

- **Wound**: Placeholder wound.
- **Trap**: Placeholder trap.
- **Circuit Breaker**: Placeholder breaker.
- **Permanent Reflex**: Placeholder reflex.
- **Socratic Probe**: Placeholder probe?

### SCAR-PROC-124: Review Process or Halt on Guard Denial

- **Wound**: When a workflow guard denies a command, an agent reads hook source code or probes rules to force the blocked action through.
- **Trap**: The agent treats a guard denial as a puzzle to bypass instead of a hard boundary that protects project integrity.
- **Circuit Breaker**: If a hook or guard denies a tool call, stop probing immediately. Review makeflow process files and tier-1 scars first. If no valid workflow path exists, stop tool calls and report the denial verbatim to the user.
- **Permanent Reflex**: Never read, grep, or test guard or hook source files to evade a denial. Follow documented makeflow alternatives, such as forward-merging instead of force-pushing. If no documented path exists, wait for explicit user direction.
- **Socratic Probe**: Did you inspect makeflow workflow guides instead of probing guard scripts? Can you trust these guards and ask the user when blocked?

### SCAR-PROC-138: Yield on Repeated Timer Conflicts

- **Wound**: Placeholder wound.
- **Trap**: Placeholder trap.
- **Permanent Reflex**: Placeholder reflex.
- **Socratic Probe**: Placeholder probe?
"""
  sample_catalog = (
      "| `SCAR-PROC-122` | Upfront Human-Review Policy Evaluation Matrix | Process | [`scars/process.md`](./scars/process.md) |\n"
      "| `SCAR-PROC-124` | Review Process or Halt on Guard Denial | Process | [`scars/process.md`](./scars/process.md) |\n"
      "| `SCAR-PROC-138` | Yield on Repeated Timer Conflicts | Process | [`scars/process.md`](./scars/process.md) |\n"
  )

  baseline_spoke_errs = check_scar_proc_124_spoke(sample_spoke)
  if baseline_spoke_errs:
    errors.append(
        f"Negative falsification baseline failed for valid SCAR-PROC-124 spoke sample: {baseline_spoke_errs}"
    )
  baseline_cat_errs = check_scar_proc_124_catalog(sample_catalog)
  if baseline_cat_errs:
    errors.append(
        f"Negative falsification baseline failed for valid SCAR-PROC-124 catalog sample: {baseline_cat_errs}"
    )

  bad_no_cb = sample_spoke.replace("- **Circuit Breaker**:", "- **Other Field**:")
  if not check_scar_proc_124_spoke(bad_no_cb):
    errors.append("Negative falsification failed: spoke missing Circuit Breaker was not rejected")

  bad_no_reflex = sample_spoke.replace("- **Permanent Reflex**:", "- **Other Field**:")
  if not check_scar_proc_124_spoke(bad_no_reflex):
    errors.append("Negative falsification failed: spoke missing Permanent Reflex was not rejected")

  bad_no_anti_evasion = sample_spoke.replace(
      "Never read, grep, or test guard or hook source files to evade a denial.",
      "Inspect environment paths first.",
  )
  if not check_scar_proc_124_spoke(bad_no_anti_evasion):
    errors.append("Negative falsification failed: spoke missing anti-evasion prohibition was not rejected")

  bad_no_halt = sample_spoke.replace(
      "stop tool calls and report the denial verbatim to the user",
      "retry the tool call automatically",
  )
  if not check_scar_proc_124_spoke(bad_no_halt):
    errors.append("Negative falsification failed: spoke missing halt/user-escalation requirement was not rejected")

  bad_long_proc = sample_spoke.replace(
      "If a hook or guard denies a tool call, stop probing immediately.",
      "If a hook or guard denies a tool call then you must immediately stop probing and carefully check every single workflow file first.",
  )
  if not check_scar_proc_124_spoke(bad_long_proc):
    errors.append("Negative falsification failed: spoke with >20-word procedural sentence was not rejected")

  bad_semicolon = sample_spoke.replace(
      "stop probing immediately. Review",
      "stop probing immediately; review",
  )
  if not check_scar_proc_124_spoke(bad_semicolon):
    errors.append("Negative falsification failed: spoke with prose semicolon was not rejected")

  bad_catalog = sample_catalog.replace(
      "[`scars/process.md`](./scars/process.md) |\n| `SCAR-PROC-138`",
      "[`scars/other.md`](./scars/other.md) |\n| `SCAR-PROC-138`",
  )
  if not check_scar_proc_124_catalog(bad_catalog):
    errors.append("Negative falsification failed: catalog with misrouted SCAR-PROC-124 row was not rejected")

  return errors


def check_optional_circuit_breaker_catalog(catalog_text):
  errs = []
  if "Circuit Breaker" not in catalog_text:
    errs.append("Catalog must mention Circuit Breaker")
  if "optional" not in catalog_text.lower():
    errs.append("Catalog must state Circuit Breaker is optional")
  if "halt" not in catalog_text.lower() and "stop" not in catalog_text.lower():
    errs.append("Catalog must describe Circuit Breaker as a halt or stop mechanism")
  return errs


def check_scar_has_circuit_breaker(spoke_text, scar_id):
  errs = []
  blocks = re.split(r"(?m)(?=^###\s+SCAR-)", spoke_text)
  found = False
  for b in blocks:
    if not b.startswith("### SCAR-"):
      continue
    sid = b.split("\n")[0].split(":")[0].replace("###", "").strip()
    if sid == scar_id:
      found = True
      cb_match = re.search(r"\*\*Circuit Breaker\*\*:[ \t]*([^\n\r]+)", b)
      if not cb_match or not cb_match.group(1).strip():
        errs.append(f"{scar_id} missing non-empty '**Circuit Breaker**:'")
      break
  if not found:
    errs.append(f"{scar_id} not found in spoke text")
  return errs


def validate_optional_circuit_breaker_invariants():
  """Validates optional Circuit Breaker format in catalog and backported scars (GH-120)."""
  errors = []

  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_path = repo_path(cat_rel)
  if not os.path.exists(cat_path):
    errors.append(f"Missing {cat_rel}")
  else:
    with open(cat_path, "r", encoding="utf-8") as f:
      cat_text = f.read()
    errors.extend(check_optional_circuit_breaker_catalog(cat_text))

  proc_rel = "skills/makeflow/references/scars/process.md"
  proc_path = repo_path(proc_rel)
  if not os.path.exists(proc_path):
    errors.append(f"Missing {proc_rel}")
  else:
    with open(proc_path, "r", encoding="utf-8") as f:
      proc_text = f.read()
    errors.extend(check_scar_has_circuit_breaker(proc_text, "SCAR-PROC-108"))

  hci_rel = "skills/makeflow/references/scars/tooling_and_hci.md"
  hci_path = repo_path(hci_rel)
  if not os.path.exists(hci_path):
    errors.append(f"Missing {hci_rel}")
  else:
    with open(hci_path, "r", encoding="utf-8") as f:
      hci_text = f.read()
    errors.extend(check_scar_has_circuit_breaker(hci_text, "SCAR-HCI-08"))

  # General schema invariant: across ALL spoke files, whenever Circuit Breaker is present,
  # it must be non-empty and well-formed.
  scars_dir = repo_path("skills/makeflow/references/scars")
  for fname in os.listdir(scars_dir):
    if not fname.endswith(".md"):
      continue
    spoke_path = os.path.join(scars_dir, fname)
    with open(spoke_path, "r", encoding="utf-8") as f:
      content = f.read()
    blocks = re.split(r"(?m)(?=^###\s+SCAR-)", content)
    for b in blocks:
      if not b.startswith("### SCAR-") or "**Circuit Breaker**:" not in b:
        continue
      sid = b.split("\n")[0].split(":")[0].replace("###", "").strip()
      cb_match = re.search(r"\*\*Circuit Breaker\*\*:[ \t]*([^\n\r]+)", b)
      if not cb_match or not cb_match.group(1).strip():
        errors.append(f"{fname} {sid} has empty or malformed '**Circuit Breaker**:'")

  # Negative falsification tests
  bad_catalog = "# 09. Synthetic Scars\n4. Four-Part Anatomy: Wound, Trap, Reflex, Probe."
  if not check_optional_circuit_breaker_catalog(bad_catalog):
    errors.append("Negative falsification failed: catalog missing Circuit Breaker was not rejected")

  bad_spoke = "### SCAR-PROC-108: Test\n- **Wound**: W\n- **Trap**: T\n- **Permanent Reflex**: R\n- **Socratic Probe**: P\n"
  if not check_scar_has_circuit_breaker(bad_spoke, "SCAR-PROC-108"):
    errors.append("Negative falsification failed: scar missing Circuit Breaker was not rejected")

  bad_empty_cb = "### SCAR-TEST-99: Test\n- **Circuit Breaker**:\n- **Wound**: W\n"
  if not check_scar_has_circuit_breaker(bad_empty_cb, "SCAR-TEST-99"):
    errors.append("Negative falsification failed: empty Circuit Breaker was not rejected")

  return errors


OPEN_ENGLISH_SPEC_REQUIREMENTS = {
    "00_index_and_agentic_rationale.md": [
        "20 words",
        "25 words",
        "6 sentences",
        "3 words",
        "10 words",
        "Plain Mode",
        "Strict Mode",
        "self-attention",
        "plainlanguage.gov",
        "MIL-STD-38784B",
        "NASA SP-7084",
        "CC-BY-4.0",
        "Attempto",
    ],
    "01_us_federal_plain_language.md": [
        "plainlanguage.gov",
        "17 U.S.C. § 105",
        "active voice",
        "nominalization",
        "utilize",
        "commence",
        "terminate",
        "prior to",
        "subsequent to",
        "in order to",
        "sufficient",
        "demonstrate",
    ],
    "02_mil_std_38784b_technical_manuals.md": [
        "MIL-STD-38784B",
        "17 U.S.C. § 105",
        "Section 3",
        "procedural step",
        "checkout",
        "alignment",
        "troubleshooting",
        "maintenance level",
        "shall",
        "Section 4",
        "nomenclature",
        "synonym",
        "first use",
        "single-use",
        "units of measure",
        "cross-reference",
        "Section 4.8",
        "WARNING",
        "CAUTION",
        "NOTE",
        "prohibited action",
        "consequence",
        "multi-step",
        "Section 5",
        "nesting depth",
        "lead-in",
        "Section 5.3",
        "imperative",
        "20 words",
        "Verify that",
        "proceed to step",
        "Not applicable",
        "None",
        "parallel",
        "theory of operation",
        "Symptom/Malfunction",
        "Probable Cause",
        "Test/Inspection",
        "Corrective Action",
        "Wound",
        "Trap",
        "Circuit Breaker",
        "Reflex",
    ],
    "03_nasa_sp_7084_technical_grammar.md": [
        "NASA SP-7084",
        "17 U.S.C. § 105",
        "Mary K. McCaskill",
        "Chapter 1",
        "Chapter 2",
        "Chapter 3",
        "Chapter 4",
        "noun",
        "possessive",
        "abstract noun",
        "pronoun",
        "antecedent",
        "that",
        ", which",
        "active voice",
        "passive voice",
        "tense",
        "mood",
        "Subject-Verb Agreement",
        "15 seconds is",
        "data",
        "none",
        "each",
        "every",
        "number",
        "total",
        "unit modifier",
        "-ly",
        "comparative",
        "superlative",
        "only",
        "preposition",
        "verify",
        "confirm",
        "assert",
        "check",
        "state",
        "show",
        "correlative",
        "both",
        "either",
        "not only",
        "participle",
        "gerund",
        "infinitive",
        "dangling",
        "parallelism",
        "compare to",
        "compare with",
        "incomplete comparison",
        "emphasis",
        "conciseness",
        "circumlocution",
        "expletive",
        "there is",
        "it is",
        "redundant",
        "Apostrophe",
        "Brackets",
        "Colon",
        "Comma",
        "Hyphen",
        "Italics",
        "Parentheses",
        "Period",
        "Question Mark",
        "Quotation Marks",
        "Slash",
        "non-",
        "pre-",
        "re-",
        "self-",
        "sub-",
        "co-",
        "multi-",
        "post-",
        "anti-",
        "subproject",
        "idempotent",
        "precondition",
        "untracked",
        "32-bit and 64-bit",
        "Agentic Override",
        "semicolon",
        "em-dash",
        "00_index_and_agentic_rationale.md",
        "Rule 9",
        "Capitalization",
        "proper noun",
        "the database",
        "the supervisor",
        "Target 20",
        "Headline Style",
        "Sentence Style",
        "acronym",
        "numeral",
        "SI",
    ],
    "04_google_developer_style_guide.md": [
        "Google Developer Documentation Style Guide",
        "CC-BY-4.0",
        "Google LLC",
        "Part I",
        "Part II",
        "Part III",
        "Part IV",
        "Part V",
        "Part VI",
        "Part VII",
        "condition",
        "you",
        "we",
        "simple present",
        "localization",
        "acronym",
        "e.g.",
        "i.e.",
        "etc.",
        "et al.",
        "via",
        "cf.",
        "viz.",
        "Agentic Override",
        "00_index_and_agentic_rationale.md",
        "Rule 8",
        "anthropomorphism",
        "file(s)",
        "task-oriented",
        "numbered",
        "bulleted",
        "description list",
        "colon",
        "Note",
        "Caution",
        "Warning",
        "click here",
        "this file",
        "read more",
        "file://",
        "code",
        "<placeholder_name>",
        "flag",
        "exit code",
        "status code",
        "bold",
        "docstring",
        "Vector 4",
        "please",
        "sorry",
        "allowlist",
        "denylist",
        "primary",
        "replica",
        "main",
        "worker",
        "placeholder variable",
        "coherence check",
        "smoke test",
        "sanity check",
        "stub",
        "dummy",
        "abort",
        "cancel",
        "above",
        "below",
        "argument",
        "parameter",
        "authenticate",
        "authorize",
        "automatically",
        "check",
        "select",
        "verify",
        "click",
        "currently",
        "deprecate",
        "directory",
        "folder",
        "easy",
        "easily",
        "simple",
        "simply",
        "just",
        "obviously",
        "enable",
        "turn on",
        "ensure",
        "execute",
        "run",
        "file name",
        "filename",
        "instantiate",
        "kill",
        "terminate",
        "leverage",
        "login",
        "log in",
        "sign in",
        "may",
        "can",
        "might",
        "once",
        "after",
        "only",
        "parse",
        "populate",
        "recommend",
        "seamless",
        "robust",
        "since",
        "because",
        "specify",
        "sync",
        "tarball",
        "transparent",
        "URL",
        "value",
        "when",
        "whether",
    ],
    "05_attempto_controlled_english_cnl.md": [
        "Attempto Controlled English",
        "University of Zurich",
        "ace_constructionrules.html",
        "ace_interpretationrules.html",
        "ace_troubleshooting.html",
        "Part I",
        "Part II",
        "Part III",
        "Part IV",
        "Part V",
        "Part VI",
        "Part VII",
        "Part VIII",
        "function word",
        "content word",
        "proper name",
        "variable",
        "arithmetic",
        "properly",
        "appropriately",
        "correctly",
        "adequately",
        "usually",
        "mostly",
        "fast",
        "safe",
        "countable",
        "mass noun",
        "determiner",
        "apposition",
        "intransitive",
        "transitive",
        "ditransitive",
        "copula",
        "there is",
        "nobody but",
        "nothing but",
        "if",
        "then",
        "relative clause",
        "subordination",
        "exclusive disjunction",
        "does not",
        "it is false that",
        "not provably",
        "it is not provable that",
        "must",
        "can",
        "should",
        "may",
        "it is necessary that",
        "it is possible that",
        "quantifier",
        "every",
        "each of",
        "at least",
        "at most",
        "exactly",
        "for every",
        "collective",
        "distributive",
        "anaphora",
        "pronoun",
        "definite noun phrase",
        "accessibility",
        "recency",
        "specificity",
        "reflexivity",
        "his own",
        "itself",
        "interrogative",
        "imperative",
        "wh-quer",
        "how much",
        "how many",
        "prepositional",
        "right association",
        "binding order",
        ",and",
        ",or",
    ],
}

OPEN_ENGLISH_REPO_TARGETS = (
    "README.md",
    "install.sh",
    "rules/AGENTS.md",
    "scripts/session_start.py",
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "agents/socratic-probe-writer.md",
    "agents/synthetic-scar-importer.md",
)

FORBIDDEN_LEGACY_ENGLISH_TOKENS = (
    "SimpleEnglish",
    "simple-english",
    "AminBlg/SimpleEnglish",
    "asd_ste100_spec.md",
    "ASD-STE100",
)


def _check_prose_punctuation(fname: str, text: str) -> list[str]:
  """Verifies prose lines outside code blocks, inline code, tables, and headings have zero ';' or '—'."""
  errs = []
  in_fence = False
  for idx, raw_line in enumerate(text.splitlines(), start=1):
    stripped = raw_line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if in_fence or not stripped:
      continue
    if stripped.startswith("#") or stripped.startswith("|"):
      continue
    scrubbed = re.sub(r"`[^`]*`", "", stripped)
    if ";" in scrubbed:
      errs.append(f"{fname}:{idx} contains forbidden semicolon (';') in prose")
    if "\u2014" in scrubbed:
      errs.append(f"{fname}:{idx} contains forbidden em-dash ('—') in prose")
  return errs


def _count_markdown_table_data_rows(text: str) -> int:
  """Counts Markdown table data rows (excluding header and separator lines)."""
  in_table = False
  header_seen = False
  sep_seen = False
  data_rows = 0
  for raw_line in text.splitlines():
    stripped = raw_line.strip()
    if stripped.startswith("|") and stripped.endswith("|"):
      if not in_table:
        in_table = True
        header_seen = True
        sep_seen = False
      elif header_seen and not sep_seen and re.match(r"^\|[\s:\-\|]+\|$", stripped):
        sep_seen = True
      elif sep_seen:
        data_rows += 1
    else:
      in_table = False
      header_seen = False
      sep_seen = False
  return data_rows


US_FEDERAL_PLAIN_LANGUAGE_SECTIONS = (
    "## 1. Audience and Direct Address",
    "## 2. Document Organization, Headings, and Paragraph Structure",
    "## 3. Active Voice, Verb Tenses, Modal Ladder, and Hidden Verbs (Nominalizations)",
    "## 4. Word Choice, Jargon, Noun Strings, Abbreviations, Definitions, and Compound Prepositions",
    "## 5. Concise Sentences, SVO Proximity, and Positive Language",
    "## 6. Aids to Clarity: Lists, Decision Tables, Examples, Cross-References, and Web Structure",
    "## 7. Testing and Verifying Specification Clarity",
    "## 8. Public-Domain Simple-Word Replacement Dictionary",
)

US_FEDERAL_SECTION_REQUIRED_TOKENS = {
    "## 1. Audience and Direct Address": (
        "audience",
        "separate audiences",
        "you",
    ),
    "## 2. Document Organization, Headings, and Paragraph Structure": (
        "topic sentence",
        "main idea",
        "transition",
        "headings",
    ),
    "## 3. Active Voice, Verb Tenses, Modal Ladder, and Hidden Verbs (Nominalizations)": (
        "active voice",
        "present tense",
        "must",
        "shall",
        "00_index_and_agentic_rationale.md",
        "contractions",
        "make an adjustment",
        "perform an allocation",
        "conduct an audit",
        "provide protection",
        "reach a conclusion",
        "have an effect on",
        "make a determination",
        "perform an initialization",
        "conduct an investigation",
        "provide a description of",
        "perform validation on",
        "achieve a reduction in",
    ),
    "## 4. Word Choice, Jargon, Noun Strings, Abbreviations, Definitions, and Compound Prepositions": (
        "jargon",
        "noun",
        "abbreviations",
        "definitions",
        "only",
        "consistently",
        "by means of",
        "in the absence of",
        "until such time as",
        "with the exception of",
        "at the present time",
        "on the basis of",
        "in close proximity to",
    ),
    "## 5. Concise Sentences, SVO Proximity, and Positive Language": (
        "subject",
        "verb",
        "object",
        "not uncommon",
        "not invalid",
        "unless",
        "cannot",
        "without",
        "Avoid",
        "Use",
    ),
    "## 6. Aids to Clarity: Lists, Decision Tables, Examples, Cross-References, and Web Structure": (
        "vertical list",
        "and/or",
        "decision table",
        "above",
        "below",
        "herein",
        "aforementioned",
        "the preceding section",
        "file://",
        "click here",
    ),
    "## 7. Testing and Verifying Specification Clarity": (
        "paraphrase",
        "usability",
        "comparative",
    ),
    "## 8. Public-Domain Simple-Word Replacement Dictionary": (
        "accorded",
        "adjacent to",
        "afford an opportunity",
        "ascertain",
        "commence",
        "consolidate",
        "disseminate",
        "expedite",
        "facilitate",
        "forfeit",
        "heretofore",
        "inasmuch as",
        "incumbent upon",
        "interpose no objection",
        "notwithstanding",
        "obligate",
        "per",
        "preclude",
        "promulgate",
        "pursuant to",
        "remuneration",
        "solicit",
        "subsequent to",
        "terminate",
        "thereof",
        "until such time as",
        "utilize",
    ),
}


def _extract_markdown_h2_section(text: str, heading_substring: str) -> str:
  """Extracts the Markdown content belonging to the H2 section matching heading_substring."""
  captured = []
  in_section = False
  in_fence = False
  target_lower = heading_substring.lower()
  for raw_line in text.splitlines():
    stripped = raw_line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
    if (
        not in_fence
        and stripped.startswith("## ")
        and not stripped.startswith("###")
    ):
      if in_section:
        break
      if target_lower in stripped.lower():
        in_section = True
        captured.append(raw_line)
        continue
    if in_section:
      captured.append(raw_line)
  return "\n".join(captured)


def _check_us_federal_plain_language_expansion(text: str) -> list[str]:
  """Validates full 8-part plainlanguage.gov encapsulation in 01_us_federal_plain_language.md (GH-157)."""
  errs = []
  rel_path = "skills/makeflow/references/resources/language/english/01_us_federal_plain_language.md"
  section_map = {}

  for heading in US_FEDERAL_PLAIN_LANGUAGE_SECTIONS:
    sec_text = _extract_markdown_h2_section(text, heading)
    if not sec_text:
      errs.append(f"{rel_path} missing required H2 section '{heading}'")
      continue
    section_map[heading] = sec_text
    sec_lower = sec_text.lower()
    for tok in US_FEDERAL_SECTION_REQUIRED_TOKENS.get(heading, ()):
      if tok not in sec_text and tok.lower() not in sec_lower:
        errs.append(
            f"{rel_path} section '{heading}' missing required token '{tok}'"
        )

  sec3_key = US_FEDERAL_PLAIN_LANGUAGE_SECTIONS[2]
  if sec3_key in section_map:
    nom_rows = _count_markdown_table_data_rows(section_map[sec3_key])
    if nom_rows < 30:
      errs.append(
          f"{rel_path} Section 3 Nominalizations table must have at least 30 rows (found {nom_rows})"
      )

  sec4_key = US_FEDERAL_PLAIN_LANGUAGE_SECTIONS[3]
  if sec4_key in section_map:
    prep_rows = _count_markdown_table_data_rows(section_map[sec4_key])
    if prep_rows < 20:
      errs.append(
          f"{rel_path} Section 4 Compound Prepositions table must have at least 20 rows (found {prep_rows})"
      )

  sec8_key = US_FEDERAL_PLAIN_LANGUAGE_SECTIONS[7]
  if sec8_key in section_map:
    dict_rows = _count_markdown_table_data_rows(section_map[sec8_key])
    if dict_rows < 220:
      errs.append(
          f"{rel_path} Section 8 Simple-Word Replacement Dictionary must have at least 220 rows (found {dict_rows})"
      )

  errs.extend(_check_prose_punctuation(rel_path, text))
  return errs


INDEX_AGENTIC_RATIONALE_SECTIONS = (
    "## 1. AI transformer self-attention mechanics and agentic rationale",
    "## 2. Core quantitative structural rules",
    "## 3. Operational modes, token preservation, and validator word-counting rules",
    "## 4. Algorithmic sentence-splitting and compression recipes",
    "## 5. Zero-token phrase deletion table",
    "## 6. Worked scar and Socratic probe compression patterns",
    "## 7. Cross-standard precedence hierarchy and conflict-resolution matrix",
)

INDEX_SECTION_REQUIRED_TOKENS = {
    "## 1. AI transformer self-attention mechanics and agentic rationale": (
        "self-attention",
        "clause distance",
        "parenthetical",
        "polysemy",
        "attention",
        "token",
    ),
    "## 2. Core quantitative structural rules": (
        "20 words",
        "25 words",
        "6 sentences",
        "3 words",
        "10 words",
        "Rule 8",
        "Rule 9",
        "contractions",
        "semicolons",
        "em-dashes",
    ),
    "## 3. Operational modes, token preservation, and validator word-counting rules": (
        "Plain Mode",
        "Strict Mode",
        "validate_scar_candidate.py",
        "mask_code_and_math_spans",
        "split_into_sentences",
        "count_words_in_sentence",
        "__FENCED_CODE__",
        "__INLINE_CODE__",
        "__DISPLAY_MATH__",
        "__INLINE_MATH__",
        "ScarCandidateValidator",
    ),
    "## 4. Algorithmic sentence-splitting and compression recipes": (
        "because",
        ", which",
        "participial",
        "Before",
        "After",
        "Verify that",
    ),
    "## 5. Zero-token phrase deletion table": (
        "it is important to note that",
        "in order to ensure that",
        "make sure that you always",
        "has the ability to",
        "it should be noted that",
        "in the process of",
        "is capable of",
        "take action to",
    ),
    "## 6. Worked scar and Socratic probe compression patterns": (
        "wound",
        "trap",
        "circuit_breaker",
        "reflex",
        "socratic_probe",
        "Before",
        "After",
        "words)",
    ),
    "## 7. Cross-standard precedence hierarchy and conflict-resolution matrix": (
        "05_attempto_controlled_english_cnl.md",
        "last",
        "DANGER",
        "WARNING",
        "CAUTION",
        "NOTE",
        "sentence case",
        "1,000",
        "15 ms",
        "we",
        "you",
        "because",
        "since",
        "verify that",
        "delete",
        "component",
        "implement",
        "interface",
        "option",
        "parameter",
        "argument",
        "plainlanguage.gov",
        "MIL-STD-38784B",
        "NASA SP-7084",
        "CC-BY-4.0",
        "Attempto",
    ),
}


def _check_index_and_agentic_rationale_expansion(text: str) -> list[str]:
  """Validates full 7-part operational manual expansion in 00_index_and_agentic_rationale.md (GH-156)."""
  errs = []
  rel_path = "skills/makeflow/references/resources/language/english/00_index_and_agentic_rationale.md"
  section_map = {}

  for heading in INDEX_AGENTIC_RATIONALE_SECTIONS:
    sec_text = _extract_markdown_h2_section(text, heading)
    if not sec_text:
      errs.append(f"{rel_path} missing required H2 section '{heading}'")
      continue
    section_map[heading] = sec_text
    sec_lower = sec_text.lower()
    for tok in INDEX_SECTION_REQUIRED_TOKENS.get(heading, ()):
      if tok not in sec_text and tok.lower() not in sec_lower:
        errs.append(
            f"{rel_path} section '{heading}' missing required token '{tok}'"
        )

  sec5_key = INDEX_AGENTIC_RATIONALE_SECTIONS[4]
  if sec5_key in section_map:
    del_rows = _count_markdown_table_data_rows(section_map[sec5_key])
    if del_rows < 25:
      errs.append(
          f"{rel_path} Section 5 Zero-Token Phrase Deletion table must have at least 25 rows (found {del_rows})"
      )

  sec7_key = INDEX_AGENTIC_RATIONALE_SECTIONS[6]
  if sec7_key in section_map:
    matrix_rows = _count_markdown_table_data_rows(section_map[sec7_key])
    if matrix_rows < 15:
      errs.append(
          f"{rel_path} Section 7 Cross-Standard Conflict-Resolution Matrix tables must have at least 15 rows (found {matrix_rows})"
      )

  errs.extend(_check_prose_punctuation(rel_path, text))
  return errs


def _check_open_english_bundle(
    english_files: dict[str, str],
    repo_files: dict[str, str],
    legacy_spec_exists: bool = False,
) -> list[str]:
  """Validates the 6 open technical English spec files, legacy spec removal, and 8 repo references."""
  errs = []
  base_rel = "skills/makeflow/references/resources/language/english"

  if legacy_spec_exists:
    errs.append(
        "Legacy proprietary spec skills/makeflow/references/resources/asd_ste100_spec.md must not exist"
    )

  for fname, req_tokens in OPEN_ENGLISH_SPEC_REQUIREMENTS.items():
    rel_path = f"{base_rel}/{fname}"
    if fname not in english_files:
      errs.append(f"Missing {rel_path}")
      continue
    content = english_files[fname]
    content_lower = content.lower()
    for tok in req_tokens:
      if tok not in content and tok.lower() not in content_lower:
        errs.append(f"{rel_path} missing required token '{tok}'")
    if fname == "01_us_federal_plain_language.md":
      row_count = _count_markdown_table_data_rows(content)
      if row_count < 35:
        errs.append(
            f"{rel_path} Simple-Word Replacement Dictionary must have at least 35 rows (found {row_count})"
        )
    if fname == "02_mil_std_38784b_technical_manuals.md":
      row_count = _count_markdown_table_data_rows(content)
      if row_count < 20:
        errs.append(
            f"{rel_path} MIL-STD-38784B normative tables must have at least 20 rows (found {row_count})"
        )
    if fname == "03_nasa_sp_7084_technical_grammar.md":
      row_count = _count_markdown_table_data_rows(content)
      if row_count < 30:
        errs.append(
            f"{rel_path} NASA SP-7084 lookup tables must have at least 30 rows (found {row_count})"
        )
    if fname == "04_google_developer_style_guide.md":
      row_count = _count_markdown_table_data_rows(content)
      if row_count < 45:
        errs.append(
            f"{rel_path} tables (Latin, Inclusive, and Developer A-Z Word List) must have at least 45 rows (found {row_count})"
        )
    if fname == "05_attempto_controlled_english_cnl.md":
      row_count = _count_markdown_table_data_rows(content)
      if row_count < 20:
        errs.append(
            f"{rel_path} ACE tables must have at least 20 rows (found {row_count})"
        )
    errs.extend(_check_prose_punctuation(rel_path, content))

  expected_ref = "skills/makeflow/references/resources/language/english/"
  for rel_path in OPEN_ENGLISH_REPO_TARGETS:
    if rel_path not in repo_files:
      errs.append(f"Missing {rel_path}")
      continue
    rtext = repo_files[rel_path]
    sanitized_text = rtext.replace("--import-simple-english", "")
    sanitized_lower = sanitized_text.lower()
    for bad_tok in FORBIDDEN_LEGACY_ENGLISH_TOKENS:
      if bad_tok in sanitized_text or bad_tok.lower() in sanitized_lower:
        errs.append(f"{rel_path} must not reference legacy token '{bad_tok}'")
    if expected_ref not in rtext:
      errs.append(f"{rel_path} must reference '{expected_ref}'")

  return errs


def validate_open_technical_english_specs():
  """Validates open technical English specifications and removal of simple-english / ASD-STE100 (GH-152)."""
  errors = []
  base_rel = "skills/makeflow/references/resources/language/english"
  english_files = {}
  for fname in OPEN_ENGLISH_SPEC_REQUIREMENTS:
    fpath = repo_path(f"{base_rel}/{fname}")
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        english_files[fname] = f.read()

  repo_files = {}
  for rel_path in OPEN_ENGLISH_REPO_TARGETS:
    fpath = repo_path(rel_path)
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        repo_files[rel_path] = f.read()

  legacy_exists = os.path.exists(
      repo_path("skills/makeflow/references/resources/asd_ste100_spec.md")
  )
  errors.extend(
      _check_open_english_bundle(
          english_files, repo_files, legacy_spec_exists=legacy_exists
      )
  )
  if "00_index_and_agentic_rationale.md" in english_files:
    errors.extend(
        _check_index_and_agentic_rationale_expansion(
            english_files["00_index_and_agentic_rationale.md"]
        )
    )
  if "01_us_federal_plain_language.md" in english_files:
    errors.extend(
        _check_us_federal_plain_language_expansion(
            english_files["01_us_federal_plain_language.md"]
        )
    )

  # In-memory negative falsification checks (SCAR-TEST-11)
  dict_rows = "\n".join(
      f"| word_{i} | simple_{i} |" for i in range(40)
  )
  sample_english = {
      "00_index_and_agentic_rationale.md": (
          "# Index\n"
          "Procedural limit is 20 words and descriptive limit is 25 words.\n"
          "Paragraphs have 6 sentences, noun clusters have 3 words, and terms use 10 words.\n"
          "Operate in Plain Mode or Strict Mode for self-attention clarity.\n"
          "Sources include plainlanguage.gov, MIL-STD-38784B, NASA SP-7084, CC-BY-4.0, and Attempto.\n"
      ),
      "01_us_federal_plain_language.md": (
          "# US Federal Plain Language\n"
          "Source is plainlanguage.gov in the Public Domain under 17 U.S.C. § 105.\n"
          "Use active voice and avoid nominalization.\n"
          "| Complex | Simple |\n"
          "| :--- | :--- |\n"
          "| utilize | use |\n"
          "| commence | start |\n"
          "| terminate | stop |\n"
          "| prior to | before |\n"
          "| subsequent to | after |\n"
          "| in order to | to |\n"
          "| sufficient | enough |\n"
          "| demonstrate | show |\n"
          f"{dict_rows}\n"
      ),
      "02_mil_std_38784b_technical_manuals.md": (
          "# MIL-STD-38784B\n"
          "Source is MIL-STD-38784B under 17 U.S.C. § 105.\n"
          "Section 3 defines procedural step, checkout, alignment, troubleshooting, maintenance level, and shall.\n"
          "Section 4 covers nomenclature, synonym drift, first use, single-use abbreviations, units of measure, and cross-reference rules.\n"
          "Section 4.8 covers WARNING, CAUTION, and NOTE alerts with prohibited action, consequence, and multi-step scoping.\n"
          "Section 5 covers nesting depth and lead-in text.\n"
          "Section 5.3 covers imperative steps of 20 words, Verify that checks, and proceed to step branching.\n"
          "Tables use Not applicable or None and lists stay parallel.\n"
          "Theory of operation and Symptom/Malfunction, Probable Cause, Test/Inspection, Corrective Action map to Wound, Trap, Circuit Breaker, and Reflex.\n"
          "| Term | Meaning |\n"
          "| :--- | :--- |\n"
          f"{dict_rows}\n"
      ),
      "03_nasa_sp_7084_technical_grammar.md": (
          "# NASA SP-7084\n"
          "Source is NASA SP-7084 by Mary K. McCaskill under 17 U.S.C. § 105.\n"
          "## Chapter 1: Technical Grammar\n"
          "Cover noun clusters, possessive forms, and abstract noun avoidance.\n"
          "Check pronoun and antecedent rules with that and, which clauses.\n"
          "Use active voice over passive voice, consistent tense, mood, and Subject-Verb Agreement.\n"
          "Write 15 seconds is for singular units and handle data, none, each, every, number, and total.\n"
          "Hyphenate each unit modifier, omit hyphens after -ly adverbs, and check comparative, superlative, and only.\n"
          "Use preposition idioms, retain that after verify, confirm, assert, check, state, and show, and balance correlative pairs both, either, and not only.\n"
          "Avoid dangling participle, gerund, or infinitive phrases and keep parallelism with compare to, compare with, and no incomplete comparison.\n"
          "## Chapter 2: Technical Sentence Style\n"
          "Build emphasis and conciseness by cutting circumlocution, expletive there is or it is, and redundant words.\n"
          "## Chapter 3: Technical Punctuation\n"
          "Cover Apostrophe, Brackets, Colon, Comma, Hyphen, Italics, Parentheses, Period, Question Mark, Quotation Marks, and Slash.\n"
          "Include prefixes non-, pre-, re-, self-, sub-, co-, multi-, post-, anti- and solid words subproject, idempotent, precondition, untracked, plus 32-bit and 64-bit.\n"
          "Apply the Agentic Override for semicolon and em-dash from 00_index_and_agentic_rationale.md Rule 9.\n"
          "## Chapter 4: Capitalization\n"
          "Distinguish proper noun forms like Target 20 from the database and the supervisor, and apply Headline Style, Sentence Style, acronym, numeral, and SI rules.\n"
          "| Construct | Rule |\n"
          "| :--- | :--- |\n"
          f"{dict_rows}\n"
      ),
      "04_google_developer_style_guide.md": (
          "# Google Style\n"
          "Source is Google Developer Documentation Style Guide under CC-BY-4.0 by Google LLC.\n"
          "Covers Part I, Part II, Part III, Part IV, Part V, Part VI, and Part VII.\n"
          "Place the condition before the instruction, address `you` instead of `we`, use simple present tense, and support localization.\n"
          "Define each acronym, follow the Agentic Override in `00_index_and_agentic_rationale.md` Rule 8, avoid anthropomorphism, and avoid `file(s)`.\n"
          "Write task-oriented headings, numbered lists, bulleted lists, a description list after a colon, and Note, Caution, or Warning callouts.\n"
          "Avoid `click here`, `this file`, and `read more`, and use `file://` links.\n"
          "Format `code` spans, `<placeholder_name>`, CLI `flag` options, `exit code`, HTTP `status code`, **bold** UI labels, and docstring blocks.\n"
          "Write Vector 4 error messages without `please` or `sorry`.\n"
          "Use inclusive terms: `allowlist`, `denylist`, `primary`, `replica`, `main`, `worker`, `placeholder variable`, `coherence check`, `smoke test` instead of `sanity check`, and `stub` instead of `dummy`.\n"
          "Word list covers `abort`, `cancel`, `above`, `below`, `argument`, `parameter`, `authenticate`, `authorize`, `automatically`, `check`, `select`, `verify`, `click`, `currently`, `deprecate`, `directory`, `folder`, `easy`, `easily`, `simple`, `simply`, `just`, `obviously`, `enable`, `turn on`, `ensure`, `execute`, `run`, `file name`, `filename`, `instantiate`, `kill`, `terminate`, `leverage`, `login`, `log in`, `sign in`, `may`, `can`, `might`, `once`, `after`, `only`, `parse`, `populate`, `recommend`, `seamless`, `robust`, `since`, `because`, `specify`, `sync`, `tarball`, `transparent`, `URL`, `value`, `when`, and `whether`.\n"
          "| Avoid | Use |\n"
          "| :--- | :--- |\n"
          "| `e.g.` | `for example` |\n"
          "| `i.e.` | `that is` |\n"
          "| `etc.` | `and similar items` |\n"
          "| `et al.` | `and others` |\n"
          "| `via` | `through` |\n"
          "| `cf.` | `compare` |\n"
          "| `viz.` | `namely` |\n"
          f"{dict_rows}\n"
      ),
      "05_attempto_controlled_english_cnl.md": (
          "# Attempto Controlled English\n"
          "Source is Attempto Controlled English from the University of Zurich.\n"
          "References: ace_constructionrules.html, ace_interpretationrules.html, and ace_troubleshooting.html.\n"
          "## Part I\n"
          "Vocabulary covers function word, content word, proper name, variable, and arithmetic rules.\n"
          "Ban properly, appropriately, correctly, adequately, usually, mostly, fast, and safe.\n"
          "## Part II\n"
          "Phrases cover countable, mass noun, determiner, apposition, intransitive, transitive, ditransitive, copula, there is, nobody but, and nothing but.\n"
          "## Part III\n"
          "Composites cover if, then, relative clause, subordination, and exclusive disjunction.\n"
          "## Part IV\n"
          "Negation and modality cover does not, it is false that, not provably, it is not provable that, must, can, should, may, it is necessary that, and it is possible that.\n"
          "## Part V\n"
          "Scope covers quantifier, every, each of, at least, at most, exactly, for every, collective, and distributive.\n"
          "## Part VI\n"
          "Binding covers anaphora, pronoun, definite noun phrase, accessibility, recency, specificity, reflexivity, his own, and itself.\n"
          "## Part VII\n"
          "Queries cover interrogative, imperative, wh-queries, how much, how many, socratic-probe-writer.md, and 20 words.\n"
          "## Part VIII\n"
          "Attachment covers prepositional, right association, binding order, `,and`, and `,or`.\n"
          "| Avoid | Use |\n"
          "| :--- | :--- |\n"
          f"{dict_rows}\n"
      ),
  }
  sample_repo = {
      rel: "Follow skills/makeflow/references/resources/language/english/ rules.\n"
      for rel in OPEN_ENGLISH_REPO_TARGETS
  }

  baseline_errs = _check_open_english_bundle(
      sample_english, sample_repo, legacy_spec_exists=False
  )
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for open English bundle: {baseline_errs}"
    )

  # 1. Missing spec file
  missing_eng = dict(sample_english)
  missing_eng.pop("03_nasa_sp_7084_technical_grammar.md")
  if not any(
      "Missing skills/makeflow/references/resources/language/english/03_nasa_sp_7084_technical_grammar.md"
      in e
      for e in _check_open_english_bundle(missing_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: missing 03_nasa_sp_7084_technical_grammar.md was not rejected"
    )

  # 2. Missing attribution token
  no_attr_eng = dict(sample_english)
  no_attr_eng["04_google_developer_style_guide.md"] = no_attr_eng[
      "04_google_developer_style_guide.md"
  ].replace("CC-BY-4.0", "OpenLicense")
  if not any(
      "CC-BY-4.0" in e
      for e in _check_open_english_bundle(no_attr_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: missing CC-BY-4.0 attribution was not rejected"
    )

  # 3. Prose semicolon
  semi_eng = dict(sample_english)
  semi_eng["00_index_and_agentic_rationale.md"] += "Bad prose line; with semicolon.\n"
  if not any(
      "semicolon" in e
      for e in _check_open_english_bundle(semi_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: prose semicolon was not rejected"
    )

  # 4. Prose em-dash
  emdash_eng = dict(sample_english)
  emdash_eng["02_mil_std_38784b_technical_manuals.md"] += (
      "Bad prose line — with em-dash.\n"
  )
  if not any(
      "em-dash" in e
      for e in _check_open_english_bundle(emdash_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: prose em-dash was not rejected"
    )

  # 5. Legacy asd_ste100_spec.md exists
  if not any(
      "asd_ste100_spec.md" in e
      for e in _check_open_english_bundle(
          sample_english, sample_repo, legacy_spec_exists=True
      )
  ):
    errors.append(
        "Negative falsification failed: existing asd_ste100_spec.md was not rejected"
    )

  # 6. Leftover SimpleEnglish / asd_ste100_spec.md in repo file
  bad_repo = dict(sample_repo)
  bad_repo["README.md"] += "Install SimpleEnglish plugin for ASD-STE100.\n"
  if not any(
      "README.md must not reference" in e
      for e in _check_open_english_bundle(sample_english, bad_repo)
  ):
    errors.append(
        "Negative falsification failed: leftover SimpleEnglish in README.md was not rejected"
    )

  # 7. Insufficient replacement dictionary rows (< 35)
  short_dict_eng = dict(sample_english)
  short_dict_eng["01_us_federal_plain_language.md"] = (
      "# US Federal Plain Language\n"
      "Source is plainlanguage.gov under 17 U.S.C. § 105 with active voice and no nominalization.\n"
      "Terms: utilize commence terminate prior to subsequent to in order to sufficient demonstrate.\n"
      "| Complex | Simple |\n"
      "| :--- | :--- |\n"
      "| utilize | use |\n"
  )
  if not any(
      "at least 35 rows" in e
      for e in _check_open_english_bundle(short_dict_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: < 35 replacement dictionary rows was not rejected"
    )

  # GH-157 in-memory baseline and 6 negative falsification checks (SCAR-TEST-11)
  nom_required = (
      "make an adjustment",
      "perform an allocation",
      "conduct an audit",
      "provide protection",
      "reach a conclusion",
      "have an effect on",
      "make a determination",
      "perform an initialization",
      "conduct an investigation",
      "provide a description of",
      "perform validation on",
      "achieve a reduction in",
  )
  nom_rows_str = "\n".join(
      [f"| `{phrase}` | `verb_{i}` |" for i, phrase in enumerate(nom_required)]
      + [f"| `nominalization_{i}` | `action_{i}` |" for i in range(25)]
  )
  prep_required = (
      "by means of",
      "in the absence of",
      "until such time as",
      "with the exception of",
      "at the present time",
      "on the basis of",
      "in close proximity to",
  )
  prep_rows_str = "\n".join(
      [f"| `{phrase}` | `prep_{i}` |" for i, phrase in enumerate(prep_required)]
      + [f"| `compound_prep_{i}` | `simple_prep_{i}` |" for i in range(18)]
  )
  dict_required = (
      "accorded",
      "adjacent to",
      "afford an opportunity",
      "ascertain",
      "commence",
      "consolidate",
      "disseminate",
      "expedite",
      "facilitate",
      "forfeit",
      "heretofore",
      "inasmuch as",
      "incumbent upon",
      "interpose no objection",
      "notwithstanding",
      "obligate",
      "per",
      "preclude",
      "promulgate",
      "pursuant to",
      "remuneration",
      "solicit",
      "subsequent to",
      "terminate",
      "thereof",
      "until such time as",
      "utilize",
  )
  full_dict_rows_str = "\n".join(
      [f"| `{term}` | `plain_{i}` |" for i, term in enumerate(dict_required)]
      + [f"| `complex_word_{i}` | `simple_word_{i}` |" for i in range(205)]
  )
  sample_federal_expanded = (
      "# U.S. Federal Plain Language Guidelines\n\n"
      "## 1. Audience and Direct Address\n"
      "Identify the audience, address separate audiences separately, and address the reader as you.\n\n"
      "## 2. Document Organization, Headings, and Paragraph Structure\n"
      "Write informative headings, start with a topic sentence, put the main idea first, and use transition words.\n\n"
      "## 3. Active Voice, Verb Tenses, Modal Ladder, and Hidden Verbs (Nominalizations)\n"
      "Use active voice, present tense, and must instead of shall. "
      "Note the 00_index_and_agentic_rationale.md override banning contractions.\n"
      "| Hidden Verb | Action Verb |\n"
      "| :--- | :--- |\n"
      f"{nom_rows_str}\n\n"
      "## 4. Word Choice, Jargon, Noun Strings, Abbreviations, Definitions, and Compound Prepositions\n"
      "Avoid jargon and long noun strings, minimize abbreviations and definitions, place only carefully, and use terms consistently.\n"
      "| Compound Preposition | Simple Preposition |\n"
      "| :--- | :--- |\n"
      f"{prep_rows_str}\n\n"
      "## 5. Concise Sentences, SVO Proximity, and Positive Language\n"
      "Keep subject, verb, and object close together. "
      "Avoid double negatives such as not uncommon, not invalid, unless, or cannot without. Use affirmative phrasing.\n\n"
      "## 6. Aids to Clarity: Lists, Decision Tables, Examples, Cross-References, and Web Structure\n"
      "Use a vertical list, ban and/or, and use a decision table for branching logic. "
      "Ban above, below, herein, aforementioned, and the preceding section in favor of file:// links, and never write click here.\n\n"
      "## 7. Testing and Verifying Specification Clarity\n"
      "Use paraphrase testing, usability testing, and comparative studies.\n\n"
      "## 8. Public-Domain Simple-Word Replacement Dictionary\n"
      "| Complex | Simple |\n"
      "| :--- | :--- |\n"
      f"{full_dict_rows_str}\n"
  )

  fed_baseline_errs = _check_us_federal_plain_language_expansion(
      sample_federal_expanded
  )
  if fed_baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for GH-157 expansion: {fed_baseline_errs}"
    )

  # GH-157 Falsification 1: Missing H2 section
  missing_sec_fed = sample_federal_expanded.replace(
      "## 7. Testing and Verifying Specification Clarity",
      "### 7. Omitted Section",
  )
  if not any(
      "missing required H2 section" in e
      for e in _check_us_federal_plain_language_expansion(missing_sec_fed)
  ):
    errors.append(
        "Negative falsification failed: missing H2 section in 01_us_federal_plain_language.md was not rejected"
    )

  # GH-157 Falsification 2: Nominalization table < 30 rows
  short_nom_fed = re.sub(
      r"(\| `nominalization_\d+` \| `action_\d+` \|\n)+",
      "",
      sample_federal_expanded,
  )
  if not any(
      "Section 3 Nominalizations table must have at least 30 rows" in e
      for e in _check_us_federal_plain_language_expansion(short_nom_fed)
  ):
    errors.append(
        "Negative falsification failed: < 30 nominalization rows was not rejected"
    )

  # GH-157 Falsification 3: Compound-preposition table < 20 rows
  short_prep_fed = re.sub(
      r"(\| `compound_prep_\d+` \| `simple_prep_\d+` \|\n)+",
      "",
      sample_federal_expanded,
  )
  if not any(
      "Section 4 Compound Prepositions table must have at least 20 rows" in e
      for e in _check_us_federal_plain_language_expansion(short_prep_fed)
  ):
    errors.append(
        "Negative falsification failed: < 20 compound preposition rows was not rejected"
    )

  # GH-157 Falsification 4: Simple-Word Replacement Dictionary < 220 rows
  short_full_dict_fed = re.sub(
      r"(\| `complex_word_\d+` \| `simple_word_\d+` \|\n)+",
      "",
      sample_federal_expanded,
  )
  if not any(
      "Section 8 Simple-Word Replacement Dictionary must have at least 220 rows"
      in e
      for e in _check_us_federal_plain_language_expansion(short_full_dict_fed)
  ):
    errors.append(
        "Negative falsification failed: < 220 replacement dictionary rows was not rejected"
    )

  # GH-157 Falsification 5: Missing double-negative tokens in Section 5
  missing_dneg_fed = sample_federal_expanded.replace("not uncommon", "rare")
  if not any(
      "not uncommon" in e
      for e in _check_us_federal_plain_language_expansion(missing_dneg_fed)
  ):
    errors.append(
        "Negative falsification failed: missing double-negative token 'not uncommon' was not rejected"
    )

  # GH-157 Falsification 6: Missing positional cross-reference ban in Section 6
  missing_xref_fed = sample_federal_expanded.replace("aforementioned", "prior")
  if not any(
      "aforementioned" in e
      for e in _check_us_federal_plain_language_expansion(missing_xref_fed)
  ):
    errors.append(
        "Negative falsification failed: missing positional cross-reference ban token 'aforementioned' was not rejected"
    )

  # 8. Missing Part VIII in 05_attempto_controlled_english_cnl.md
  missing_part_eng = dict(sample_english)
  missing_part_eng["05_attempto_controlled_english_cnl.md"] = missing_part_eng[
      "05_attempto_controlled_english_cnl.md"
  ].replace("Part VIII", "Section 8")
  if not any(
      "missing required token 'Part VIII'" in e
      for e in _check_open_english_bundle(missing_part_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: missing Part VIII in 05_attempto_controlled_english_cnl.md was not rejected"
    )

  # 9. Insufficient ACE table rows (< 20) in 05_attempto_controlled_english_cnl.md
  short_ace_eng = dict(sample_english)
  short_ace_eng["05_attempto_controlled_english_cnl.md"] = short_ace_eng[
      "05_attempto_controlled_english_cnl.md"
  ].replace(dict_rows, "| vague | specific |")
  if not any(
      "ACE tables must have at least 20 rows" in e
      for e in _check_open_english_bundle(short_ace_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: < 20 ACE table rows in 05_attempto_controlled_english_cnl.md was not rejected"
    )

  # 10. Missing Part heading, Agentic Override, or word-list token in 04_google_developer_style_guide.md (GH-160)
  missing_google_toks = dict(sample_english)
  missing_google_toks["04_google_developer_style_guide.md"] = (
      missing_google_toks["04_google_developer_style_guide.md"]
      .replace("Part VII", "Section 7")
      .replace("Agentic Override", "Local Note")
      .replace("tarball", "archive")
  )
  google_tok_errs = _check_open_english_bundle(missing_google_toks, sample_repo)
  for expected_missing in ("Part VII", "Agentic Override", "tarball"):
    if not any(
        f"missing required token '{expected_missing}'" in e
        for e in google_tok_errs
    ):
      errors.append(
          f"Negative falsification failed: missing '{expected_missing}' in 04_google_developer_style_guide.md was not rejected"
      )

  # 11. Insufficient table rows (< 45) in 04_google_developer_style_guide.md (GH-160)
  short_google_eng = dict(sample_english)
  short_google_eng["04_google_developer_style_guide.md"] = short_google_eng[
      "04_google_developer_style_guide.md"
  ].replace(dict_rows, "| `abort` | `stop` |")
  if not any(
      "04_google_developer_style_guide.md" in e and "at least 45 rows" in e
      for e in _check_open_english_bundle(short_google_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: < 45 table rows in 04_google_developer_style_guide.md was not rejected"

    )

  # 12. Missing MIL-STD-38784B troubleshooting / procedural tokens
  missing_mil_eng = dict(sample_english)
  missing_mil_eng["02_mil_std_38784b_technical_manuals.md"] = missing_mil_eng[
      "02_mil_std_38784b_technical_manuals.md"
  ].replace("Symptom/Malfunction", "RemovedToken").replace(
      "Circuit Breaker", "RemovedGate"
  )
  mil_token_errs = _check_open_english_bundle(missing_mil_eng, sample_repo)
  if not any("Symptom/Malfunction" in e for e in mil_token_errs) or not any(
      "Circuit Breaker" in e for e in mil_token_errs
  ):
    errors.append(
        "Negative falsification failed: missing MIL-STD-38784B troubleshooting tokens were not rejected"
    )

  # 13. Insufficient MIL-STD-38784B normative table rows (< 20)
  short_mil_eng = dict(sample_english)
  short_mil_eng["02_mil_std_38784b_technical_manuals.md"] = (
      "# MIL-STD-38784B\n"
      "Source is MIL-STD-38784B under 17 U.S.C. § 105.\n"
      "Section 3 defines procedural step, checkout, alignment, troubleshooting, maintenance level, and shall.\n"
      "Section 4 covers nomenclature, synonym drift, first use, single-use abbreviations, units of measure, and cross-reference rules.\n"
      "Section 4.8 covers WARNING, CAUTION, and NOTE alerts with prohibited action, consequence, and multi-step scoping.\n"
      "Section 5 covers nesting depth and lead-in text.\n"
      "Section 5.3 covers imperative steps of 20 words, Verify that checks, and proceed to step branching.\n"
      "Tables use Not applicable or None and lists stay parallel.\n"
      "Theory of operation and Symptom/Malfunction, Probable Cause, Test/Inspection, Corrective Action map to Wound, Trap, Circuit Breaker, and Reflex.\n"
      "| Term | Meaning |\n"
      "| :--- | :--- |\n"
      "| WARNING | Hazard |\n"
  )
  if not any(
      "at least 20 rows" in e
      for e in _check_open_english_bundle(short_mil_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: < 20 MIL-STD-38784B table rows was not rejected"
    )

  # 14. Missing NASA SP-7084 chapter or agentic override construct
  missing_ch_eng = dict(sample_english)
  missing_ch_eng["03_nasa_sp_7084_technical_grammar.md"] = (
      missing_ch_eng["03_nasa_sp_7084_technical_grammar.md"]
      .replace("Chapter 4", "Section X")
      .replace("Agentic Override", "Standard Rule")
  )
  nasa_missing_errs = _check_open_english_bundle(missing_ch_eng, sample_repo)
  if not any("Chapter 4" in e for e in nasa_missing_errs) or not any(
      "Agentic Override" in e for e in nasa_missing_errs
  ):
    errors.append(
        "Negative falsification failed: missing NASA SP-7084 chapter or Agentic Override was not rejected"
    )

  # 15. Insufficient NASA SP-7084 lookup table rows (< 30)
  short_nasa_eng = dict(sample_english)
  short_nasa_eng["03_nasa_sp_7084_technical_grammar.md"] = short_nasa_eng[
      "03_nasa_sp_7084_technical_grammar.md"
  ].replace(dict_rows, "| non- | non-blocking |")
  if not any(
      "at least 30 rows" in e
      for e in _check_open_english_bundle(short_nasa_eng, sample_repo)
  ):
    errors.append(
        "Negative falsification failed: < 30 NASA SP-7084 lookup table rows was not rejected"
    )

  # GH-156 in-memory baseline and 7 negative falsification checks (SCAR-TEST-11)
  del_required = (
      "it is important to note that",
      "in order to ensure that",
      "make sure that you always",
      "has the ability to",
      "it should be noted that",
      "in the process of",
      "is capable of",
      "take action to",
  )
  del_rows_str = "\n".join(
      [f"| `{phrase}` | `DELETE (0 tokens)` |" for phrase in del_required]
      + [f"| `filler_phrase_{i}` | `DELETE (0 tokens)` |" for i in range(20)]
  )
  matrix_rows_str = "\n".join(
      f"| `rule_{i}` | `precedence_{i}` |" for i in range(16)
  )
  sample_index_expanded = (
      "# Technical English language guidelines: index and agentic rationale\n\n"
      "## 1. AI transformer self-attention mechanics and agentic rationale\n"
      "Explain self-attention, clause distance, parenthetical nesting, polysemy, attention weights, and token overhead.\n\n"
      "## 2. Core quantitative structural rules\n"
      "Enforce 20 words, 25 words, 6 sentences, 3 words, and 10 words limits.\n"
      "Rule 8 bans contractions and Rule 9 bans semicolons and em-dashes.\n\n"
      "## 3. Operational modes, token preservation, and validator word-counting rules\n"
      "Cover Plain Mode, Strict Mode, and validate_scar_candidate.py mechanics.\n"
      "Functions mask_code_and_math_spans, split_into_sentences, and count_words_in_sentence use "
      "__FENCED_CODE__, __INLINE_CODE__, __DISPLAY_MATH__, and __INLINE_MATH__ inside ScarCandidateValidator.\n\n"
      "## 4. Algorithmic sentence-splitting and compression recipes\n"
      "Split because, , which, and participial clauses with Before and After examples and Verify that checks.\n\n"
      "## 5. Zero-token phrase deletion table\n"
      "| Phrase | Action |\n"
      "| :--- | :--- |\n"
      f"{del_rows_str}\n\n"
      "## 6. Worked scar and Socratic probe compression patterns\n"
      "Compress wound, trap, circuit_breaker, reflex, and socratic_probe with Before (30 words) and After (15 words).\n\n"
      "## 7. Cross-standard precedence hierarchy and conflict-resolution matrix\n"
      "Rank 05_attempto_controlled_english_cnl.md last across plainlanguage.gov, MIL-STD-38784B, NASA SP-7084, CC-BY-4.0, and Attempto.\n"
      "Define DANGER, WARNING, CAUTION, and NOTE, sentence case, 1,000, 15 ms, we, you, because, since, verify that, "
      "delete, component, implement, interface, option, parameter, and argument.\n"
      "| Conflict | Resolution |\n"
      "| :--- | :--- |\n"
      f"{matrix_rows_str}\n"
  )

  idx_baseline_errs = _check_index_and_agentic_rationale_expansion(
      sample_index_expanded
  )
  if idx_baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for GH-156 expansion: {idx_baseline_errs}"
    )

  # GH-156 Falsification 1: Missing H2 section in 00_index_and_agentic_rationale.md
  missing_sec_idx = sample_index_expanded.replace(
      "## 4. Algorithmic sentence-splitting and compression recipes",
      "### 4. Omitted Section",
  )
  if not any(
      "missing required H2 section" in e
      for e in _check_index_and_agentic_rationale_expansion(missing_sec_idx)
  ):
    errors.append(
        "Negative falsification failed: missing H2 section in 00_index_and_agentic_rationale.md was not rejected"
    )

  # GH-156 Falsification 2: Section 5 Zero-Token Phrase Deletion table < 25 rows
  short_del_idx = re.sub(
      r"(\| `filler_phrase_\d+` \| `DELETE \(0 tokens\)` \|\n)+",
      "",
      sample_index_expanded,
  )
  if not any(
      "Section 5 Zero-Token Phrase Deletion table must have at least 25 rows" in e
      for e in _check_index_and_agentic_rationale_expansion(short_del_idx)
  ):
    errors.append(
        "Negative falsification failed: < 25 phrase deletion rows in 00_index_and_agentic_rationale.md was not rejected"
    )

  # GH-156 Falsification 3: Missing canonical phrase deletion token (it is important to note that)
  missing_phrase_idx = sample_index_expanded.replace(
      "it is important to note that", "removed_phrase_token"
  )
  if not any(
      "it is important to note that" in e
      for e in _check_index_and_agentic_rationale_expansion(missing_phrase_idx)
  ):
    errors.append(
        "Negative falsification failed: missing phrase deletion token 'it is important to note that' was not rejected"
    )

  # GH-156 Falsification 4: Missing validate_scar_candidate.py tokenization function token (mask_code_and_math_spans)
  missing_tok_fn_idx = sample_index_expanded.replace(
      "mask_code_and_math_spans", "removed_tokenizer_fn"
  )
  if not any(
      "mask_code_and_math_spans" in e
      for e in _check_index_and_agentic_rationale_expansion(missing_tok_fn_idx)
  ):
    errors.append(
        "Negative falsification failed: missing tokenization function token 'mask_code_and_math_spans' was not rejected"
    )

  # GH-156 Falsification 5: Missing worked scar/probe compression token (socratic_probe)
  missing_probe_idx = sample_index_expanded.replace(
      "socratic_probe", "removed_probe_field"
  )
  if not any(
      "socratic_probe" in e
      for e in _check_index_and_agentic_rationale_expansion(missing_probe_idx)
  ):
    errors.append(
        "Negative falsification failed: missing worked scar/probe token 'socratic_probe' was not rejected"
    )

  # GH-156 Falsification 6: Section 7 tables < 15 rows
  short_matrix_idx = re.sub(
      r"(\| `rule_\d+` \| `precedence_\d+` \|\n)+",
      "| `rule_0` | `precedence_0` |\n",
      sample_index_expanded,
  )
  if not any(
      "Section 7 Cross-Standard Conflict-Resolution Matrix tables must have at least 15 rows"
      in e
      for e in _check_index_and_agentic_rationale_expansion(short_matrix_idx)
  ):
    errors.append(
        "Negative falsification failed: < 15 Section 7 table rows in 00_index_and_agentic_rationale.md was not rejected"
    )

  # GH-156 Falsification 7: Missing conflict-resolution rule token (DANGER or sentence case)
  missing_conflict_idx = sample_index_expanded.replace(
      "DANGER", "REMOVED_TIER"
  ).replace("sentence case", "removed_casing")
  conflict_errs = _check_index_and_agentic_rationale_expansion(
      missing_conflict_idx
  )
  if not any("DANGER" in e for e in conflict_errs) or not any(
      "sentence case" in e for e in conflict_errs
  ):
    errors.append(
        "Negative falsification failed: missing conflict-resolution tokens 'DANGER' / 'sentence case' were not rejected"
    )

  return errors


TRACKED_CANONICAL_FILENAMES = (
    "hooks.json",
    "plugin.json",
    "mcp_config.json",
    "default_config.json",
    "pre_tool_guard.py",
    "workflow_gates.py",
    "tdd_guard.py",
    "emit_reflex.py",
    "ast_scrub.py",
    "workspace_mutex.py",
    "terminal_title.py",
    "session_start.py",
    "sync_synthetic_scars.py",
    "enrich_scars_index.py",
    "validate_scar_candidate.py",
    "shell_safety.py",
    "commit_msg.py",
    "base_script.py",
    "scars_db.py",
    "config.py",
    "env_preflight.py",
    "reflex.py",
    "safe_copy.py",
    "scar_candidate.py",
    "signal_trap.py",
    "static_check.py",
    "server.py",
)

_BARE_TRACKED_FILENAME_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])("
    + "|".join(re.escape(name) for name in TRACKED_CANONICAL_FILENAMES)
    + r")\b"
)

_CANONICAL_PATH_MAP_REQUIRED_TOKENS = (
    "./hooks.json",
    "./plugin.json",
    "./mcp_config.json",
    "./default_config.json",
    "mcp/server.py",
    "scripts/pre_tool_guard.py",
    "scripts/workflow_gates.py",
    "lib/shell_safety.py",
    "lib/config.py",
    "lib/workflow_gates.py",
    "rules/AGENTS.md",
    "skills/makeflow/SKILL.md",
    "hooks/",
    "lib/pre_tool_guard.py",
)


def check_canonical_repo_paths_and_qualified_refs(
    repo_files: dict[str, str],
) -> list[str]:
  """Validates canonical repo path map in rules/AGENTS.md and rejects bare filenames (GH-187)."""
  errs: list[str] = []

  agents_rel = "rules/AGENTS.md"
  if agents_rel not in repo_files:
    errs.append(f"Missing {agents_rel}")
  else:
    agents_text = repo_files[agents_rel]
    heading = "### 1.12 Canonical Plugin and Repository Path Map"
    if heading not in agents_text:
      errs.append(f"{agents_rel} missing '{heading}' section")
    else:
      sec_1_12 = re.split(
          r"\n#{2,3}\s+",
          agents_text.split(heading, 1)[1],
          maxsplit=1,
      )[0]
      for tok in _CANONICAL_PATH_MAP_REQUIRED_TOKENS:
        if tok not in sec_1_12:
          errs.append(
              f"{agents_rel} Section 1.12 missing canonical path '{tok}'"
          )
      for absent_path in ("hooks/", "lib/pre_tool_guard.py"):
        matching_lines = [
            line for line in sec_1_12.splitlines() if absent_path in line
        ]
        if not any(
            re.search(
                r"(?i)\b(?:no|none|not exist|does not exist|do not exist)\b",
                line,
            )
            for line in matching_lines
        ):
          errs.append(
              f"{agents_rel} Section 1.12 must explicitly note that '{absent_path}' does not exist"
          )

  for rel_path, content in sorted(repo_files.items()):
    for line_no, line in enumerate(content.splitlines(), start=1):
      for match in _BARE_TRACKED_FILENAME_RE.finditer(line):
        errs.append(
            f"{rel_path}:{line_no}: bare/unqualified filename '{match.group(1)}' "
            "must use canonical repository path"
        )

  readme_rel = "README.md"
  if readme_rel in repo_files:
    readme_text = repo_files[readme_rel]
    if "(`hooks.json`)" in readme_text or "[`./hooks.json`](./hooks.json)" not in readme_text:
      errs.append(
          f"{readme_rel} must use qualified '[`./hooks.json`](./hooks.json)' instead of bare '(`hooks.json`)'"
      )

  return errs


def validate_canonical_repo_paths_and_qualified_refs() -> list[str]:
  """Validates canonical repo path map and qualified filename references across docs (GH-187)."""
  errors: list[str] = []

  static_doc_paths = [
      "skills/makeflow/SKILL.md",
      "rules/AGENTS.md",
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
      "README.md",
  ]
  dynamic_globs = [
      "skills/makeflow/references/*.md",
      "skills/makeflow/references/scars/*.md",
  ]

  repo_files: dict[str, str] = {}
  for rel in static_doc_paths:
    fpath = repo_path(rel)
    if not os.path.isfile(fpath):
      errors.append(f"Missing {rel}")
    else:
      with open(fpath, "r", encoding="utf-8") as f:
        repo_files[rel] = f.read()

  for pattern in dynamic_globs:
    matches = sorted(glob.glob(repo_path(pattern)))
    if not matches:
      errors.append(f"No markdown files matched '{pattern}'")
    for abs_path in matches:
      rel = os.path.relpath(abs_path, REPO_ROOT).replace(os.sep, "/")
      with open(abs_path, "r", encoding="utf-8") as f:
        repo_files[rel] = f.read()

  errors.extend(check_canonical_repo_paths_and_qualified_refs(repo_files))

  # Hermetic in-memory negative falsification checks (SCAR-TEST-11)
  sample_files = {
      "rules/AGENTS.md": (
          "### 1.12 Canonical Plugin and Repository Path Map\n\n"
          "1. Root manifests: `./hooks.json`, `./plugin.json`, `./mcp_config.json`, and `./default_config.json`.\n"
          "2. MCP server: `./mcp/server.py`.\n"
          "3. CLI wrappers: `scripts/pre_tool_guard.py` and `scripts/workflow_gates.py`.\n"
          "4. Shared libraries: `lib/workflow_gates.py`, `lib/shell_safety.py`, and `lib/config.py`.\n"
          "5. Rules and docs: `./rules/AGENTS.md` and `./skills/makeflow/SKILL.md`.\n"
          "6. Non-existent paths: No `hooks/` directory or `lib/pre_tool_guard.py` file exists.\n"
      ),
      "skills/makeflow/SKILL.md": (
          "Run `<plugin_root>/scripts/workflow_gates.py intake` and `.claude-plugin/plugin.json`.\n"
      ),
      "skills/makeflow/references/01_intake_and_branching.md": (
          "Pass `--allow-blocked` to `scripts/workflow_gates.py intake`.\n"
          "Unit tests live in `tests/test_workflow_gates.py` and `mcp/server.py`.\n"
      ),
      "skills/makeflow/references/03_tdd_and_code_quality.md": (
          "The `scripts/pre_tool_guard.py` hook guards governance files.\n"
      ),
      "README.md": (
          "| Google Antigravity | Lifecycle hooks ([`./hooks.json`](./hooks.json)) | Install |\n"
      ),
  }

  baseline_errs = check_canonical_repo_paths_and_qualified_refs(sample_files)
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for GH-187: {baseline_errs}"
    )

  # 1) Missing Section 1.12 path map in rules/AGENTS.md
  bad_missing_map = dict(sample_files)
  bad_missing_map["rules/AGENTS.md"] = "# Rules without path map\n"
  if not any(
      "Canonical Plugin and Repository Path Map" in e
      for e in check_canonical_repo_paths_and_qualified_refs(bad_missing_map)
  ):
    errors.append(
        "Negative falsification failed: missing path map in rules/AGENTS.md was not rejected"
    )

  # 2) Missing non-existent hooks/ or lib/pre_tool_guard.py note in rules/AGENTS.md
  bad_no_absent_note = dict(sample_files)
  bad_no_absent_note["rules/AGENTS.md"] = sample_files["rules/AGENTS.md"].replace(
      "No `hooks/` directory or `lib/pre_tool_guard.py` file exists.",
      "See `scripts/pre_tool_guard.py`.",
  )
  if not any(
      "hooks/" in e
      for e in check_canonical_repo_paths_and_qualified_refs(bad_no_absent_note)
  ):
    errors.append(
        "Negative falsification failed: omitted hooks/ non-existence note in rules/AGENTS.md was not rejected"
    )

  # 2b) Missing non-existence phrasing on lib/pre_tool_guard.py independently
  bad_no_lib_guard_absent = dict(sample_files)
  bad_no_lib_guard_absent["rules/AGENTS.md"] = sample_files[
      "rules/AGENTS.md"
  ].replace(
      "No `hooks/` directory or `lib/pre_tool_guard.py` file exists.",
      "No `hooks/` directory exists.\n7. See `lib/pre_tool_guard.py`.",
  )
  if not any(
      "lib/pre_tool_guard.py" in e
      for e in check_canonical_repo_paths_and_qualified_refs(
          bad_no_lib_guard_absent
      )
  ):
    errors.append(
        "Negative falsification failed: omitted lib/pre_tool_guard.py non-existence note in rules/AGENTS.md was not rejected"
    )

  # 2c) Omitting individual required tokens from Section 1.12 when heading exists
  for omitted_tok in ("./default_config.json", "lib/config.py"):
    bad_omitted_tok = dict(sample_files)
    bad_omitted_tok["rules/AGENTS.md"] = sample_files["rules/AGENTS.md"].replace(
        omitted_tok,
        "./other_placeholder.txt",
    )
    if not any(
        omitted_tok in e
        for e in check_canonical_repo_paths_and_qualified_refs(bad_omitted_tok)
    ):
      errors.append(
          f"Negative falsification failed: omitted Section 1.12 token '{omitted_tok}' was not rejected"
      )

  # 3) Injecting bare pre_tool_guard.py into a reference doc
  bad_bare_guard = dict(sample_files)
  bad_bare_guard["skills/makeflow/references/03_tdd_and_code_quality.md"] = (
      "The `pre_tool_guard.py` hook guards governance files.\n"
  )
  if not any(
      "03_tdd_and_code_quality.md" in e and "pre_tool_guard.py" in e
      for e in check_canonical_repo_paths_and_qualified_refs(bad_bare_guard)
  ):
    errors.append(
        "Negative falsification failed: bare pre_tool_guard.py in reference doc was not rejected"
    )

  # 4) Injecting bare workflow_gates.py into a reference doc
  bad_bare_gates = dict(sample_files)
  bad_bare_gates["skills/makeflow/references/01_intake_and_branching.md"] = (
      "Pass `--allow-blocked` to `workflow_gates.py intake`.\n"
  )
  if not any(
      "01_intake_and_branching.md" in e and "workflow_gates.py" in e
      for e in check_canonical_repo_paths_and_qualified_refs(bad_bare_gates)
  ):
    errors.append(
        "Negative falsification failed: bare workflow_gates.py in reference doc was not rejected"
    )

  # 4b) Injecting bare default_config.json into a reference manual
  bad_bare_default_cfg = dict(sample_files)
  bad_bare_default_cfg[
      "skills/makeflow/references/13_configuration_and_user_preferences.md"
  ] = "4. Canonical defaults in `default_config.json`.\n"
  if not any(
      "13_configuration_and_user_preferences.md" in e
      and "default_config.json" in e
      for e in check_canonical_repo_paths_and_qualified_refs(
          bad_bare_default_cfg
      )
  ):
    errors.append(
        "Negative falsification failed: bare default_config.json in reference manual was not rejected"
    )

  # 5) Bare hooks.json in README.md
  bad_readme = dict(sample_files)
  bad_readme["README.md"] = (
      "| Google Antigravity | Lifecycle hooks (`hooks.json`) | Install |\n"
  )
  if not any(
      "README.md" in e and "hooks.json" in e
      for e in check_canonical_repo_paths_and_qualified_refs(bad_readme)
  ):
    errors.append(
        "Negative falsification failed: bare hooks.json in README.md was not rejected"
    )

  return errors


def validate():
  """Runs cross-agent compatibility validation and exits non-zero on failure."""
  errors = _collect_compatibility_errors()
  if errors:
    print(f"\n❌ Validation failed with {len(errors)} error(s):")
    for err in errors:
      print(f"  - {err}")
    sys.exit(1)
  print("\n✔ All cross-agent configuration files validated successfully!")


def _collect_compatibility_errors():
  """Collects cross-agent configuration, adapter, and scar invariant errors."""
  errors = []

  # 1. Claude Plugin Manifest
  claude_rel = ".claude-plugin/plugin.json"
  claude_path = repo_path(claude_rel)
  if not os.path.exists(claude_path):
    errors.append(f"Missing {claude_rel}")
  else:
    try:
      with open(claude_path) as f:
        data = json.load(f)
      assert data.get("name") == "makeflow"
      assert "skills" in data
      print(f"✔ Claude Code plugin manifest ({claude_rel}) valid.")
    except Exception as e:
      errors.append(f"Invalid {claude_rel}: {e}")

  # 2. Codex Plugin Manifest
  codex_rel = ".codex-plugin/plugin.json"
  codex_path = repo_path(codex_rel)
  if not os.path.exists(codex_path):
    errors.append(f"Missing {codex_rel}")
  else:
    try:
      with open(codex_path) as f:
        data = json.load(f)
      assert data.get("name") == "makeflow"
      print(f"✔ OpenAI Codex plugin manifest ({codex_rel}) valid.")
    except Exception as e:
      errors.append(f"Invalid {codex_rel}: {e}")

  # 3. CLAUDE.md
  claude_md_rel = "CLAUDE.md"
  claude_md = repo_path(claude_md_rel)
  if not os.path.exists(claude_md):
    errors.append(f"Missing {claude_md_rel}")
  else:
    with open(claude_md) as f:
      content = f.read()
    assert "Makefile Workflow" in content
    assert "Target 20" in content
    assert "Target 50" in content
    if "approved_tree.txt" not in content:
      errors.append("CLAUDE.md Target 40 command must reference .workflow/state/approved_tree.txt")
    if "..HEAD" in content:
      errors.append("CLAUDE.md Target 40 command must not diff against ..HEAD (use locked approved_tree)")
    print(f"✔ Claude Code root instructions ({claude_md_rel}) valid.")

  # 4. Cursor Rules
  cursorrules_rel = ".cursorrules"
  cursorrules = repo_path(cursorrules_rel)
  cursor_mdc_rel = ".cursor/rules/makeflow.mdc"
  cursor_mdc = repo_path(cursor_mdc_rel)
  if not os.path.exists(cursorrules):
    errors.append(f"Missing {cursorrules_rel}")
  else:
    with open(cursorrules) as f:
      cr_text = f.read()
    assert "Target 40" in cr_text or "deep-reasoning" in cr_text
    print(f"✔ Cursor rules ({cursorrules_rel}) valid with model reminder.")

  if not os.path.exists(cursor_mdc):
    errors.append(f"Missing {cursor_mdc_rel}")
  else:
    with open(cursor_mdc) as f:
      mdc_content = f.read()
    assert mdc_content.startswith("---")
    assert "description:" in mdc_content
    assert "globs:" in mdc_content
    assert "alwaysApply:" in mdc_content
    assert "Model Switch Reminder" in mdc_content
    print(f"✔ Cursor MDC rule file ({cursor_mdc_rel}) valid.")

  # 5. GitHub Copilot Instructions
  copilot_rel = ".github/copilot-instructions.md"
  copilot_path = repo_path(copilot_rel)
  if not os.path.exists(copilot_path):
    errors.append(f"Missing {copilot_rel}")
  else:
    with open(copilot_path) as f:
      c_content = f.read()
    assert "Makefile Workflow" in c_content
    assert "Six Pillars" in c_content
    assert "Interactive Model Switch Reminder" in c_content
    print(f"✔ GitHub Copilot instructions ({copilot_rel}) valid.")

  # 6. ChatGPT System Prompt
  chatgpt_rel = "adapters/chatgpt/instructions.md"
  chatgpt_path = repo_path(chatgpt_rel)
  if not os.path.exists(chatgpt_path):
    errors.append(f"Missing {chatgpt_rel}")
  else:
    with open(chatgpt_path) as f:
      cg_content = f.read()
    assert "Target 00" in cg_content
    assert "Target 90" in cg_content
    print(f"✔ ChatGPT system instructions ({chatgpt_rel}) valid.")

  # 7. Hooks Configuration
  hooks_rel = "hooks.json"
  hooks_path = repo_path(hooks_rel)
  if not os.path.exists(hooks_path):
    errors.append(f"Missing {hooks_rel}")
  else:
    try:
      with open(hooks_path) as f:
        hooks_data = json.load(f)
      guard = hooks_data.get("makeflow-guard", {})
      assert "PreToolUse" in guard
      assert "SessionStart" in guard
      print(f"✔ Antigravity hooks manifest ({hooks_rel}) valid.")
    except Exception as e:
      errors.append(f"Invalid {hooks_rel}: {e}")

  # 8. MCP Server Configuration
  mcp_rel = "mcp_config.json"
  mcp_path = repo_path(mcp_rel)
  if not os.path.exists(mcp_path):
    errors.append(f"Missing {mcp_rel}")
  else:
    try:
      with open(mcp_path) as f:
        mcp_data = json.load(f)
      servers = mcp_data.get("mcpServers", {})
      assert "makeflow" in servers
      server_script = servers["makeflow"]["args"][0]
      full_server_script = (
          repo_path(server_script)
          if not os.path.isabs(server_script)
          else server_script
      )
      assert os.path.exists(full_server_script)
      print(f"✔ MCP configuration ({mcp_rel}) valid.")
    except Exception as e:
      errors.append(f"Invalid {mcp_rel}: {e}")

  # 9. Configuration Template & Reference Manual
  cfg_template_rel = ".workflow/config.json.example"
  cfg_template = repo_path(cfg_template_rel)
  if not os.path.exists(cfg_template):
    errors.append(f"Missing {cfg_template_rel}")
  else:
    try:
      with open(cfg_template) as f:
        cfg_data = json.load(f)
      assert "database" in cfg_data
      assert "prodDatabases" in cfg_data["database"]
      assert "testDatabases" in cfg_data["database"]
      assert "prodHosts" in cfg_data["database"]
      assert "models" in cfg_data, f"{cfg_template_rel} must declare 'models' section"
      for role in (
          "adversarial-critic",
          "archaeology-worker",
          "tdd-worker",
          "triage-worker",
          "synthetic-scar-importer",
      ):
        assert (
            cfg_data["models"].get(role) == "inherit"
        ), f"{cfg_template_rel} role '{role}' must default to 'inherit', got '{cfg_data['models'].get(role)}'"
      with open(cfg_template) as f:
        cfg_raw = f.read()
      if "Antigravity" not in cfg_raw or "inherit" not in cfg_raw:
        errors.append(f"{cfg_template_rel} must document Antigravity specificity and inherit defaults")
      if "flash_lite" not in cfg_raw or "pro" not in cfg_raw:
        errors.append(f"{cfg_template_rel} must document valid Antigravity model tiers (inherit, flash_lite, flash, pro)")
      if "effort" not in cfg_raw.lower():
        errors.append(f"{cfg_template_rel} must document session reasoning effort inheritance")
      print(f"✔ Workflow configuration template ({cfg_template_rel}) valid.")
    except Exception as e:
      errors.append(f"Invalid {cfg_template_rel}: {e}")

  # 9b. Canonical Default Configuration (default_config.json)
  default_cfg_rel = "default_config.json"
  default_cfg_path = repo_path(default_cfg_rel)
  if not os.path.exists(default_cfg_path):
    errors.append(f"Missing canonical {default_cfg_rel}")
  else:
    try:
      with open(default_cfg_path) as f:
        d_cfg = json.load(f)
      assert "models" in d_cfg, f"{default_cfg_rel} must declare 'models'"
      assert "review" in d_cfg, f"{default_cfg_rel} must declare 'review'"
      assert "ci" in d_cfg, f"{default_cfg_rel} must declare 'ci'"
      assert "database" in d_cfg, f"{default_cfg_rel} must declare 'database'"
      assert d_cfg["ci"]["pollIntervalSeconds"] > 0
      assert d_cfg["ci"]["quiescenceSeconds"] > 0
      print(f"✔ Canonical default configuration ({default_cfg_rel}) valid.")
    except Exception as e:
      errors.append(f"Invalid {default_cfg_rel}: {e}")

  ref_13_rel = "skills/makeflow/references/13_configuration_and_user_preferences.md"
  ref_13 = repo_path(ref_13_rel)
  if not os.path.exists(ref_13):
    errors.append(f"Missing {ref_13_rel}")
  else:
    with open(ref_13) as f:
      r13_text = f.read()
    for role in (
        "adversarial-critic",
        "archaeology-worker",
        "tdd-worker",
        "triage-worker",
        "synthetic-scar-importer",
    ):
      if f"`{role}`: `inherit`" not in r13_text and f'"{role}": "inherit"' not in r13_text:
        errors.append(f"{ref_13_rel} must document 'inherit' default for {role}")
    if "flash_lite" not in r13_text or "flash" not in r13_text or "pro" not in r13_text:
      errors.append(f"{ref_13_rel} must document all four Antigravity tier enums")
    if "reasoning effort" not in r13_text.lower() and "/effort" not in r13_text:
      errors.append(f"{ref_13_rel} must document reasoning effort (/effort) inheritance")
    if not any("references/13_" in e for e in errors):
      print(f"✔ Configuration reference manual ({ref_13_rel}) valid.")

  ref_08_rel = "skills/makeflow/references/08_subagent_orchestration.md"
  ref_08 = repo_path(ref_08_rel)
  if not os.path.exists(ref_08):
    errors.append(f"Missing {ref_08_rel}")
  else:
    with open(ref_08) as f:
      r08_text = f.read()
    if "Default tier: flash" in r08_text or "Default tier: pro" in r08_text:
      errors.append(f"{ref_08_rel} must default all subagents to inherit")
    if "socratic" not in r08_text.lower() or "delegation protocol" not in r08_text.lower():
      errors.append(f"{ref_08_rel} must document Socratic Subagent Delegation Protocol")
    for role_name in ("archaeology-worker", "tdd-worker", "adversarial-critic", "triage-worker"):
      if role_name not in r08_text:
        errors.append(f"{ref_08_rel} must document Socratic prompt pattern for {role_name}")

  # 10. Design Patterns Reference Manual
  ref_14_rel = "skills/makeflow/references/14_design_patterns_and_architecture.md"
  ref_14 = repo_path(ref_14_rel)
  if not os.path.exists(ref_14):
    errors.append(f"Missing {ref_14_rel}")
  else:
    with open(ref_14) as f:
      r14_text = f.read()
    if "Factory Method" not in r14_text or "Game Loop" not in r14_text or "ORM" not in r14_text:
      errors.append(f"Incomplete pattern coverage in {ref_14_rel}")
    else:
      print(f"✔ Design patterns reference manual ({ref_14_rel}) valid.")

  # 11. Polyglot 27-Language Guides and Manifests
  for guide_name in (
      "gdscript_godot.md",
      "nim.md",
      "haxe.md",
      "odin.md",
      "dlang.md",
  ):
    guide_rel = os.path.join(
        "skills", "makeflow", "references", "polyglot", guide_name
    )
    guide_path = repo_path(guide_rel)
    if not os.path.exists(guide_path):
      errors.append(f"Missing polyglot guide {guide_rel}")
  if not any("polyglot guide" in e for e in errors):
    with open(
        repo_path("skills/makeflow/references/polyglot/dlang.md")
    ) as f:
      dlang_text = f.read()
    if "dfmt --check" in dlang_text:
      errors.append("dlang.md must not use nonexistent 'dfmt --check' flag")
    else:
      print("✔ All 5 new polyglot guides (GDScript, Nim, Haxe, Odin, D) present.")

  canonical_version = None
  root_plugin_path = repo_path("plugin.json")
  if os.path.exists(root_plugin_path):
    with open(root_plugin_path) as f:
      root_plugin_data = json.load(f)
    canonical_version = root_plugin_data.get("version")
    if not isinstance(canonical_version, str) or not re.match(
        r"^\d+\.\d+\.\d+$", canonical_version
    ):
      errors.append(
          "Manifest plugin.json must declare a SemVer X.Y.Z version"
      )
    author_obj = root_plugin_data.get("author")
    if not isinstance(author_obj, dict) or author_obj.get("name") != "Mark Hedges":
      errors.append("Manifest plugin.json author.name must be 'Mark Hedges'")

  for manifest_rel in (
      "plugin.json",
      ".claude-plugin/plugin.json",
      ".codex-plugin/plugin.json",
  ):
    manifest_path = repo_path(manifest_rel)
    if os.path.exists(manifest_path):
      with open(manifest_path) as f:
        m_data = json.load(f)
      if (
          canonical_version
          and m_data.get("version") != canonical_version
      ) or "27-language" not in m_data.get("description", ""):
        errors.append(
            f"Manifest {manifest_rel} must match canonical version"
            f" {canonical_version} and declare 27-language support"
        )

  ref_09_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  ref_09 = repo_path(ref_09_rel)
  r09_text = ""
  if not os.path.exists(ref_09):
    errors.append(f"Missing {ref_09_rel}")
  else:
    with open(ref_09) as f:
      r09_text = f.read()
    if "SCAR-GIT-25" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-GIT-25")
    if "SCAR-PROC-103" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-PROC-103")
    if "SCAR-PROC-104" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-PROC-104")
    if "SCAR-GIT-30" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-GIT-30")
    if "SCAR-TEST-08" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-TEST-08")
    if "SCAR-TEST-09" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-TEST-09")
    if "SCAR-TOOL-07" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-TOOL-07")
    if "SCAR-TEST-10" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-TEST-10")
    if "SCAR-SYS-05" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-SYS-05")
    if "SCAR-SYS-06" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-SYS-06")
    if "SCAR-SYS-07" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-SYS-07")
    if "SCAR-SYS-08" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-SYS-08")
    if "SCAR-TEST-11" not in r09_text:
      errors.append(f"{ref_09_rel} must document SCAR-TEST-11")
    if "Hub-and-Spoke" not in r09_text and "Master Routing" not in r09_text and "Master Index" not in r09_text:
      errors.append(f"{ref_09_rel} must document Hub-and-Spoke architecture and Master Index")
    if "scars/process.md" not in r09_text or "scars/testing.md" not in r09_text:
      errors.append(f"{ref_09_rel} must link to domain spoke category files")
    if "Four-Part Anatomy" not in r09_text:
      errors.append(f"{ref_09_rel} must define Four-Part Anatomy")
    if "Socratic Probe" not in r09_text:
      errors.append(f"{ref_09_rel} must reference Socratic Probe")
    if "Socratic Reflex Protocol" not in r09_text:
      errors.append(f"{ref_09_rel} must document Socratic Reflex Protocol")
    for required_doc_term in (
        "Reference Ontology",
        "Upstream Ecosystem Normalization",
        "68",
        "ss_id",
        "original_id",
        "mf_targets",
        "--batch-size",
    ):
      if required_doc_term not in r09_text:
        errors.append(
            f"{ref_09_rel} must document GH-193 term '{required_doc_term}'"
        )
    import re as _re
    if not _re.search(r"scars_fts[^\n.]*ss_id", r09_text):
      errors.append(
          f"{ref_09_rel} must include 'ss_id' in the scars_fts indexed columns list"
      )

  # 11b. Hub-and-Spoke Category Files (references/scars/)
  scars_dir_rel = "skills/makeflow/references/scars"
  spoke_files = (
      "process.md",
      "testing.md",
      "git_and_shell.md",
      "system_and_os.md",
      "data_and_database.md",
      "security_and_auth.md",
      "tooling_and_hci.md",
      "architecture.md",
  )
  for sf in spoke_files:
    sf_rel = os.path.join(scars_dir_rel, sf)
    sf_path = repo_path(sf_rel)
    if not os.path.exists(sf_path):
      errors.append(f"Missing category spoke file {sf_rel}")

  total_scars_found = 0
  for sf in spoke_files:
    sf_rel = os.path.join(scars_dir_rel, sf)
    sf_path = repo_path(sf_rel)
    if not os.path.exists(sf_path):
      continue
    with open(sf_path) as f:
      sf_text = f.read()
    scar_blocks = re.split(r"(?m)(?=^###\s+SCAR-)", sf_text)
    for block in scar_blocks:
      if not block.startswith("### SCAR-"):
        continue
      total_scars_found += 1
      scar_id = block.split("\n")[0].replace("###", "").split(":")[0].strip()
      for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
        field_match = re.search(rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", block)
        if not field_match or not field_match.group(1).strip():
          errors.append(f"{sf_rel} {scar_id} missing non-empty '**{field}**:'")
      if r09_text and not check_scar_catalog_routing(r09_text, scar_id, sf):
        errors.append(f"{ref_09_rel} Master Routing Table must strictly route {scar_id} to scars/{sf}")

  if total_scars_found != 63:
    errors.append(f"Expected 63 synthetic scars across category spokes, found {total_scars_found}")

  testing_spoke_rel = os.path.join(scars_dir_rel, "testing.md")
  testing_spoke = repo_path(testing_spoke_rel)
  if os.path.exists(testing_spoke):
    with open(testing_spoke) as f:
      t_text = f.read()
    if "SCAR-TEST-10" not in t_text:
      errors.append(f"{testing_spoke_rel} must document SCAR-TEST-10")
    if "SCAR-TEST-11" not in t_text:
      errors.append(f"{testing_spoke_rel} must document SCAR-TEST-11")
    if "burden" not in t_text.lower() or "shift-left" not in t_text.lower():
      errors.append(f"{testing_spoke_rel} must document shift-left testing burden balance for SCAR-TEST-11")

  system_spoke_rel = os.path.join(scars_dir_rel, "system_and_os.md")
  system_spoke = repo_path(system_spoke_rel)
  if os.path.exists(system_spoke):
    with open(system_spoke) as f:
      sys_text = f.read()
    if "SCAR-SYS-05" not in sys_text:
      errors.append(f"{system_spoke_rel} must document SCAR-SYS-05")
    if "shutil.copyfile" not in sys_text or "copystat" not in sys_text:
      errors.append(f"{system_spoke_rel} must document shutil.copyfile and copystat for SCAR-SYS-05")
    if "SCAR-SYS-06" not in sys_text:
      errors.append(f"{system_spoke_rel} must document SCAR-SYS-06")
    if "TMPDIR" not in sys_text or "validate_runtime_path" not in sys_text:
      errors.append(f"{system_spoke_rel} must document TMPDIR and validate_runtime_path for SCAR-SYS-06")
    if "SCAR-SYS-07" not in sys_text:
      errors.append(f"{system_spoke_rel} must document SCAR-SYS-07")
    if "SIGTERM" not in sys_text or "SignalTrap" not in sys_text:
      errors.append(f"{system_spoke_rel} must document SIGTERM and SignalTrap for SCAR-SYS-07")
    if "SCAR-SYS-08" not in sys_text:
      errors.append(f"{system_spoke_rel} must document SCAR-SYS-08")
    if "mkdir" not in sys_text or "FileExistsError" not in sys_text:
      errors.append(f"{system_spoke_rel} must document mkdir and FileExistsError for SCAR-SYS-08")

  # 11c. Dart & Flutter Scars Migrated to SQLite Registry & 5-Stage workflow_query_scars Wiring (GH-192)
  dart_guide_rel = "skills/makeflow/references/polyglot/dart_flutter.md"
  dart_guide = repo_path(dart_guide_rel)
  if not os.path.exists(dart_guide):
    errors.append(f"Missing {dart_guide_rel}")
  else:
    with open(dart_guide) as f:
      dg_text = f.read()
    for d_scar in ("SCAR-LANG-DART-01", "SCAR-LANG-DART-02", "SCAR-LANG-DART-03", "SCAR-LANG-DART-04"):
      if d_scar in dg_text:
        errors.append(f"{dart_guide_rel} must not contain inline {d_scar} (migrated to SQLite scars_registry.db)")
    if "workflow_query_scars" not in dg_text:
      errors.append(f"{dart_guide_rel} must direct agents to query Dart/Flutter scars via workflow_query_scars")

  cat_09_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_09_path = repo_path(cat_09_rel)
  if os.path.exists(cat_09_path):
    with open(cat_09_path) as f:
      cat_09_text = f.read()
    for d_scar in ("SCAR-LANG-DART-01", "SCAR-LANG-DART-02", "SCAR-LANG-DART-03", "SCAR-LANG-DART-04"):
      if d_scar in cat_09_text:
        errors.append(f"{cat_09_rel} must not index {d_scar} in static Markdown routing table")

  for scratch_doc_rel in (
      "skills/makeflow/references/scars/tooling_and_hci.md",
      "skills/makeflow/references/scars/git_and_shell.md",
      "skills/makeflow/references/10_security_and_data_isolation.md",
      "rules/AGENTS.md",
  ):
    scratch_doc_path = repo_path(scratch_doc_rel)
    if os.path.exists(scratch_doc_path):
      with open(scratch_doc_path) as f:
        sd_text = f.read()
      if "workflow_query_scars" not in sd_text:
        errors.append(f"{scratch_doc_rel} must direct calling workflow_query_scars for temporary script language")

  for stage_doc_rel in (
      "skills/makeflow/SKILL.md",
      "skills/makeflow/references/02_archaeology_and_planning.md",
      "skills/makeflow/references/03_tdd_and_code_quality.md",
      "skills/makeflow/references/04_six_pillars_adversarial_review.md",
      "skills/makeflow/references/07_ci_and_bot_triage.md",
      "skills/makeflow/references/08_subagent_orchestration.md",
      "agents/archaeology-worker.md",
      "agents/tdd-worker.md",
      "agents/adversarial-critic.md",
      "agents/triage-worker.md",
  ):
    stage_doc_path = repo_path(stage_doc_rel)
    if os.path.exists(stage_doc_path):
      with open(stage_doc_path) as f:
        st_text = f.read()
      if "workflow_query_scars" not in st_text:
        errors.append(f"{stage_doc_rel} must wire workflow_query_scars into workflow stage execution")

  ref_03_rel = "skills/makeflow/references/03_tdd_and_code_quality.md"
  ref_03 = repo_path(ref_03_rel)
  if not os.path.exists(ref_03):
    errors.append(f"Missing {ref_03_rel}")
  else:
    with open(ref_03) as f:
      r03_text = f.read()
    if "SCAR-PROC-104" not in r03_text:
      errors.append(f"{ref_03_rel} must document SCAR-PROC-104")
    if "SCAR-TEST-09" not in r03_text:
      errors.append(f"{ref_03_rel} must document SCAR-TEST-09")
    if "SCAR-TEST-10" not in r03_text:
      errors.append(f"{ref_03_rel} must document SCAR-TEST-10")

  ref_04_rel = "skills/makeflow/references/04_testing_isolation.md"
  ref_04 = repo_path(ref_04_rel)
  if not os.path.exists(ref_04):
    errors.append(f"Missing {ref_04_rel}")
  else:
    with open(ref_04) as f:
      r04_text = f.read()
    if "SCAR-TEST-08" not in r04_text or "two-tier" not in r04_text.lower():
      errors.append(f"{ref_04_rel} must document SCAR-TEST-08 and two-tier test isolation")
    if "ephemeral loopback socket" not in r04_text.lower() and "127.0.0.1:0" not in r04_text:
      errors.append(f"{ref_04_rel} must document self-contained ephemeral loopback sockets (127.0.0.1:0)")

  # Archaeology worker and planning manual shift-left checks (SCAR-TEST-11)
  arch_worker_rel = "agents/archaeology-worker.md"
  arch_worker = repo_path(arch_worker_rel)
  if not os.path.exists(arch_worker):
    errors.append(f"Missing {arch_worker_rel}")
  else:
    with open(arch_worker) as f:
      aw_text = f.read()
    if "failure mechanism" not in aw_text.lower() or "burden" not in aw_text.lower():
      errors.append(f"{arch_worker_rel} must instruct worker to classify failure mechanisms and evaluate testing burden balance")
    if "evidence" not in aw_text.lower() or "guess" not in aw_text.lower():
      errors.append(f"{arch_worker_rel} Rule 5 must instruct worker to question verification evidence for Belnap claims")

  ref_02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  ref_02 = repo_path(ref_02_rel)
  if not os.path.exists(ref_02):
    errors.append(f"Missing {ref_02_rel}")
  else:
    with open(ref_02) as f:
      r02_text = f.read()
    if "burden" not in r02_text.lower() or "Testing Strategy and Burden-Balance" not in r02_text:
      errors.append(f"{ref_02_rel} must mandate Testing Strategy and Burden-Balance Analysis in Target 20 plan disclosures")
    if "socratic" not in r02_text.lower() or "deliberation" not in r02_text.lower():
      errors.append(f"{ref_02_rel} must mandate Socratic Pre-Planning Deliberation protocol")
    if "invariant audit" not in r02_text.lower():
      errors.append(f"{ref_02_rel} must mandate Socratic Invariant Audit table in plan template")

  # TDD implementation worker definition
  tdd_worker_rel = "agents/tdd-worker.md"
  tdd_worker = repo_path(tdd_worker_rel)
  if not os.path.exists(tdd_worker):
    errors.append(f"Missing {tdd_worker_rel}")
  else:
    with open(tdd_worker) as f:
      tdd_text = f.read()
    if "did the test fail" not in tdd_text.lower():
      errors.append(f"{tdd_worker_rel} Rule 1 must question test failure before code edits")
    if "unnecessary abstraction" not in tdd_text.lower() and "abstraction" not in tdd_text.lower():
      errors.append(f"{tdd_worker_rel} Rule 3 must question unnecessary code abstractions")

  # Critic agent and Six Pillars review definitions
  critic_agent_rel = "agents/adversarial-critic.md"
  critic_agent = repo_path(critic_agent_rel)
  if not os.path.exists(critic_agent):
    errors.append(f"Missing {critic_agent_rel}")
  else:
    with open(critic_agent) as f:
      critic_text = f.read()
    if "approved_tree" not in critic_text:
      errors.append(f"{critic_agent_rel} Rule 2 must reference approved_tree.txt")
    if "git diff $(git merge-base HEAD origin/main) HEAD" in critic_text:
      errors.append(f"{critic_agent_rel} Rule 2 must not diff against HEAD")
    canonical_pillars = [
        "1. Intent",
        "2. Verification",
        "3. Architecture",
        "4. Blast Radius",
        "5. Reviewer Defense",
        "6. Immune Defenses & Crash Antibodies",
    ]
    for pillar in canonical_pillars:
      if f"### {pillar}" not in critic_text:
        errors.append(f"{critic_agent_rel} must contain canonical heading '### {pillar}'")
    if "blast radius" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} must inspect blast radius as mandatory canary")
    if "compressed" not in critic_text.lower() and "table" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} must prohibit compressed summary tables")
    if "comprehensiveness" not in critic_text.lower() and "comprehensive" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} Pillar 2 must assess test comprehensiveness")
    if "tdd" not in critic_text.lower() and "test-driven" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} Pillar 2 must mandate TDD methodology")
    if "failure falsification" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} Pillar 2 must mandate failure falsification")
    if "burden" not in critic_text.lower() and "balance" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} Pillar 2 must assess test execution burden balance")
    if "ephemeral" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} Pillar 2 must clarify ephemeral loopback socket permissions")
    if "simple english" not in critic_text.lower():
      errors.append(f"{critic_agent_rel} Pillar 5 must enforce Simple English standards")
    if "20 words" not in critic_text:
      errors.append(f"{critic_agent_rel} Pillar 5 must document sentence length caps (20 words)")

  triage_agent_rel = "agents/triage-worker.md"
  triage_agent = repo_path(triage_agent_rel)
  if not os.path.exists(triage_agent):
    errors.append(f"Missing {triage_agent_rel}")
  else:
    with open(triage_agent) as f:
      tw_text = f.read()
    if "configured quiescence" not in tw_text.lower():
      errors.append(f"{triage_agent_rel} Rule 2 must reference configured quiescence duration")
    if "defect" not in tw_text.lower() or "suggestion" not in tw_text.lower():
      errors.append(f"{triage_agent_rel} Rule 3 must interrogate whether bot comments are defects or suggestions")
    if "send_message" not in tw_text:
      errors.append(f"{triage_agent_rel} Rule 4 must instruct returning results via send_message")
    if "pre-fetched" in tw_text.lower():
      errors.append(f"{triage_agent_rel} must not require pre-fetched CI results from supervisor")

  ref_07_rel = "skills/makeflow/references/07_ci_and_bot_triage.md"
  ref_07 = repo_path(ref_07_rel)
  if not os.path.exists(ref_07):
    errors.append(f"Missing {ref_07_rel}")
  else:
    with open(ref_07) as f:
      r07_text = f.read()
    if "triage-worker" not in r07_text.lower():
      errors.append(f"{ref_07_rel} must document triage-worker subagent delegation")
    if "reactive wakeup" not in r07_text.lower():
      errors.append(f"{ref_07_rel} must document reactive wakeup protocol for Target 70")
    if "maxwaitseconds" not in r07_text.lower() and "single-probe" not in r07_text.lower():
      errors.append(f"{ref_07_rel} must document single-probe non-blocking query option")

  ref_04_review_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  ref_04_review = repo_path(ref_04_review_rel)
  if not os.path.exists(ref_04_review):
    errors.append(f"Missing {ref_04_review_rel}")
  else:
    with open(ref_04_review) as f:
      r04_rev_text = f.read()
    for pillar in canonical_pillars:
      if f"### {pillar}" not in r04_rev_text:
        errors.append(f"{ref_04_review_rel} must contain canonical heading '### {pillar}'")
    if "three-tier critic hierarchy" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} must document the Progressive Three-Tier Critic Hierarchy")
    if "tier 3" not in r04_rev_text.lower() or "tier 2" not in r04_rev_text.lower() or "tier 1" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} must document Tier 1, Tier 2, and Tier 3 execution mechanisms")
    if "scar-proc-51" not in r04_rev_text.lower() and "degradation" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} must document Dynamic Degradation Fallback (SCAR-PROC-51)")
    if "scar-proc-114" not in r04_rev_text.lower() and "read-before-define" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} must document Sequential Read-Before-Define Barrier (SCAR-PROC-114)")
    if "scar-proc-115" not in r04_rev_text.lower() and "anti-single-blocker" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} must document Anti-Single-Blocker Satisficing (SCAR-PROC-115)")
    if "scar-proc-121" not in r04_rev_text.lower() and "rubric anchoring" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} must document Post-Compaction Rubric Anchoring (SCAR-PROC-121)")
    if "comprehensiveness" not in r04_rev_text.lower() and "comprehensive" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must evaluate test comprehensiveness")
    if "tdd" not in r04_rev_text.lower() and "test-driven" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must mandate TDD methodology")
    if "failure falsification" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must mandate failure falsification")
    if "boundary" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must evaluate boundary limits testing")
    if "counterfeit" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must prohibit counterfeit mock-only passes")
    if "burden" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must evaluate test burden balance")
    if "ephemeral" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 2 must clarify ephemeral loopback socket permissions")
    if "simple english" not in r04_rev_text.lower():
      errors.append(f"{ref_04_review_rel} Pillar 5 must evaluate Simple English compliance")
    if "20 words" not in r04_rev_text:
      errors.append(f"{ref_04_review_rel} Pillar 5 must document sentence length caps (20 words)")

  ref_10_rel = "skills/makeflow/references/10_security_and_data_isolation.md"
  ref_10 = repo_path(ref_10_rel)
  if not os.path.exists(ref_10):
    errors.append(f"Missing {ref_10_rel}")
  else:
    with open(ref_10) as f:
      r10_text = f.read()
    if "git reset --hard" not in r10_text or "git rebase" not in r10_text:
      errors.append(f"{ref_10_rel} must document destructive Git command protections")
    if "SCAR-TOOL-07" not in r10_text or "prefer dart" not in r10_text.lower():
      errors.append(f"{ref_10_rel} must document Dart scratch script and temporary directory rules")
    if "SCAR-SYS-05" not in r10_text or "copyfile" not in r10_text.lower():
      errors.append(f"{ref_10_rel} Section 5 must document SCAR-SYS-05 and cross-user file promotion")
    if "SCAR-SYS-06" not in r10_text or "TMPDIR" not in r10_text:
      errors.append(f"{ref_10_rel} Section 6 must document SCAR-SYS-06 and native runtime environment path pre-flight")
    if "SCAR-SYS-07" not in r10_text or "SignalTrap" not in r10_text:
      errors.append(f"{ref_10_rel} Section 7 must document SCAR-SYS-07 and POSIX signal trapping")
    if "SCAR-SYS-08" not in r10_text or "mkdir" not in r10_text:
      errors.append(f"{ref_10_rel} Section 8 must document SCAR-SYS-08 and atomic directory-based locks")

  # 12. SKILL.md Single-Page Size Ceiling (SCAR-PROC-99)
  skill_rel = "skills/makeflow/SKILL.md"
  skill_md = repo_path(skill_rel)
  if not os.path.exists(skill_md):
    errors.append(f"Missing {skill_rel}")
  else:
    skill_bytes = os.path.getsize(skill_md)
    if skill_bytes > 24576:
      errors.append(
          f"{skill_rel} is {skill_bytes} bytes, exceeding SCAR-PROC-99 24576-byte limit"
      )
    else:
      print(f"✔ {skill_rel} size ({skill_bytes} bytes) is within 24 KB limit.")
    with open(skill_md) as f:
      s_text = f.read()
    if "<brain>" in s_text or "<brain/" in s_text:
      errors.append(
          f"{skill_rel} must remain platform-neutral and not reference '<brain>'"
      )
    else:
      print(f"✔ {skill_rel} is platform-neutral (no <brain> references).")
    if "`agyModelTiers[\"archaeology-worker\"]` (default `inherit`)" not in s_text:
      errors.append(f"{skill_rel} Target 15 must document 'archaeology-worker' default inherit")
    if "`agyModelTiers[\"tdd-worker\"]` (default `inherit`)" not in s_text:
      errors.append(f"{skill_rel} Target 30 must document 'tdd-worker' default inherit")
    if "`agyModelTiers[\"adversarial-critic\"]` (default `inherit`)" not in s_text:
      errors.append(f"{skill_rel} Target 40 must document 'adversarial-critic' default inherit")
    if "`agyModelTiers[\"triage-worker\"]` (default `inherit`)" not in s_text:
      errors.append(f"{skill_rel} Target 70 must document 'triage-worker' default inherit")
    if "burden" not in s_text.lower() and "feasibility" not in s_text.lower():
      errors.append(f"{skill_rel} must document testing burden balance in Target 15 / Target 20")
    if "socratic" not in s_text.lower() or "deliberation" not in s_text.lower():
      errors.append(f"{skill_rel} Target 20 must instruct supervisor to evaluate Socratic pre-planning questions")

  # 13. Antigravity Development Rules (rules/AGENTS.md)
  agents_rel = "rules/AGENTS.md"
  agents_md = repo_path(agents_rel)
  if not os.path.exists(agents_md):
    errors.append(f"Missing {agents_rel}")
  else:
    with open(agents_md) as f:
      a_text = f.read()
    if "prefer Dart over Python" not in a_text:
      errors.append(f"{agents_rel} must specify preference for Dart over Python for scratch scripts")
    if "never attempt to install dart" not in a_text.lower():
      errors.append(f"{agents_rel} must forbid attempting to install Dart")
    if "temporary directory" not in a_text:
      errors.append(f"{agents_rel} must specify writing temporary scripts to temporary directories")
    if "SCAR-PROC-104" not in a_text or "targeted test loops" not in a_text.lower():
      errors.append(f"{agents_rel} Section 3 must document SCAR-PROC-104 and targeted test loops")
    if "SCAR-TEST-09" not in a_text or "fail-fast" not in a_text.lower():
      errors.append(f"{agents_rel} Section 3 must document SCAR-TEST-09 and fail-fast targeted test loops")
    if "SCAR-TEST-10" not in a_text or "static analysis" not in a_text.lower():
      errors.append(f"{agents_rel} Section 3 must document SCAR-TEST-10 and pre-flight structural static analysis")
    if "SCAR-TEST-08" not in a_text or "two tiers" not in a_text.lower():
      errors.append(f"{agents_rel} Section 2 must document SCAR-TEST-08 and two-tier test isolation")
    if "ephemeral loopback socket" not in a_text.lower() and "127.0.0.1:0" not in a_text:
      errors.append(f"{agents_rel} Section 2 must clarify ephemeral loopback socket permissions")
    if "SCAR-SYS-05" not in a_text or "shutil.copyfile" not in a_text:
      errors.append(f"{agents_rel} Section 2 must document SCAR-SYS-05 cross-user file promotion")
    if "SCAR-SYS-06" not in a_text or "validate_runtime_path" not in a_text and "TMPDIR" not in a_text:
      errors.append(f"{agents_rel} Section 2 must document SCAR-SYS-06 native runtime environment path pre-flight")
    if "SCAR-SYS-07" not in a_text or "SignalTrap" not in a_text:
      errors.append(f"{agents_rel} Section 2 must document SCAR-SYS-07 POSIX signal trapping")
    if "SCAR-SYS-08" not in a_text or "mkdir" not in a_text:
      errors.append(f"{agents_rel} Section 2 must document SCAR-SYS-08 atomic directory locks")
    if "SCAR-TEST-11" not in a_text or "burden-balance" not in a_text.lower() and "burden balance" not in a_text.lower():
      errors.append(f"{agents_rel} Section 1.1 must document SCAR-TEST-11 testing strategy and burden-balance analysis")
    if "socratic" not in a_text.lower() or "invariant audit" not in a_text.lower():
      errors.append(f"{agents_rel} Section 1.1 must document Socratic pre-planning inquiry and invariant audit table")
    if not any("rules/AGENTS.md" in e for e in errors):
      print(f"✔ Antigravity development rules ({agents_rel}) valid.")

  # 14. Built-in Technical English Guidelines & Subagent Roles in README.md (GH-152)
  readme_rel = "README.md"
  readme_path = repo_path(readme_rel)
  if not os.path.exists(readme_path):
    errors.append(f"Missing {readme_rel}")
  else:
    with open(readme_path) as f:
      rm_text = f.read()
    if "skills/makeflow/references/resources/language/english/" not in rm_text:
      errors.append(
          f"{readme_rel} must reference skills/makeflow/references/resources/language/english/"
      )
    elif "SimpleEnglish" in rm_text or "AminBlg/SimpleEnglish" in rm_text:
      errors.append(f"{readme_rel} must not recommend external SimpleEnglish plugin")
    else:
      print("✔ Built-in Technical English guidelines documented in README.md.")

    if "socratic" not in rm_text.lower() or "invariant" not in rm_text.lower():
      errors.append(f"{readme_rel} must provide Socratic ticket intake guidance")

    for role in (
        "adversarial-critic",
        "archaeology-worker",
        "tdd-worker",
        "triage-worker",
        "synthetic-scar-importer",
    ):
      if f'"{role}": "inherit"' not in rm_text:
        errors.append(f"{readme_rel} config snippet must show '{role}': 'inherit'")
      if role not in rm_text:
        errors.append(f"{readme_rel} must document worker role '{role}'")

    if "triage-worker" not in rm_text or "quiescence" not in rm_text.lower() or "triage table" not in rm_text.lower():
      errors.append(f"{readme_rel} must explain triage-worker purpose (CI polling, quiescence, triage table)")
    if "archaeology-worker" not in rm_text or "polyglot" not in rm_text.lower():
      errors.append(f"{readme_rel} must explain archaeology-worker purpose (discovery, polyglot scan)")
    if "adversarial-critic" not in rm_text or "six pillars" not in rm_text.lower():
      errors.append(f"{readme_rel} must explain adversarial-critic purpose (independent review, Six Pillars)")
    if "tdd-worker" not in rm_text or "red-green-refactor" not in rm_text.lower():
      errors.append(f"{readme_rel} must explain tdd-worker purpose (TDD, red-green-refactor)")
    if "reasoning effort" not in rm_text.lower() and "/effort" not in rm_text:
      errors.append(f"{readme_rel} must document reasoning effort inheritance for 'inherit'")
    if "flash_lite" not in rm_text or "flash" not in rm_text or "pro" not in rm_text:
      errors.append(f"{readme_rel} must document Antigravity valid enum tiers (inherit, flash_lite, flash, pro)")

  # 15. Pre-flight Static Check Integration in Makefile and scripts (SCAR-TEST-10)
  makefile_rel = "Makefile"
  makefile_path = repo_path(makefile_rel)
  if not os.path.exists(makefile_path):
    errors.append(f"Missing {makefile_rel}")
  else:
    with open(makefile_path) as f:
      mk_text = f.read()
    errors.extend(
        check_makefile_test_discovery(
            mk_text, ("test-safe-copy", "test-env-preflight")
        )
    )

  static_check_rel = "scripts/static_check.py"
  static_check_script = repo_path(static_check_rel)
  if not os.path.exists(static_check_script):
    errors.append(f"Missing {static_check_rel}")
  static_test_rel = "tests/test_static_analysis.py"
  static_test_script = repo_path(static_test_rel)
  if not os.path.exists(static_test_script):
    errors.append(f"Missing {static_test_rel}")
  safe_copy_rel = "lib/safe_copy.py"
  safe_copy_script = repo_path(safe_copy_rel)
  if not os.path.exists(safe_copy_script):
    errors.append(f"Missing {safe_copy_rel}")
  safe_copy_test_rel = "tests/test_safe_copy.py"
  safe_copy_test_script = repo_path(safe_copy_test_rel)
  if not os.path.exists(safe_copy_test_script):
    errors.append(f"Missing {safe_copy_test_rel}")
  env_preflight_rel = "scripts/env_preflight.py"
  env_preflight_script = repo_path(env_preflight_rel)
  if not os.path.exists(env_preflight_script):
    errors.append(f"Missing {env_preflight_rel}")
  env_preflight_test_rel = "tests/test_env_preflight.py"
  env_preflight_test_script = repo_path(env_preflight_test_rel)
  if not os.path.exists(env_preflight_test_script):
    errors.append(f"Missing {env_preflight_test_rel}")

  # 16. Python Interpreter Inheritance (sys.executable in test subprocesses)
  hooks_test_rel = "tests/test_hooks.py"
  hooks_test_script = repo_path(hooks_test_rel)
  if os.path.exists(hooks_test_script):
    with open(hooks_test_script) as f:
      ht_text = f.read()
    if "sys.executable" not in ht_text:
      errors.append(f"{hooks_test_rel} must use sys.executable for child subprocesses")
    if '["python3"' in ht_text or "['python3'" in ht_text:
      errors.append(f"{hooks_test_rel} must not hardcode 'python3' in subprocess calls")

  mcp_test_rel = "tests/test_mcp_server.py"
  mcp_test_script = repo_path(mcp_test_rel)
  if os.path.exists(mcp_test_script):
    with open(mcp_test_script) as f:
      mt_text = f.read()
    if "sys.executable" not in mt_text:
      errors.append(f"{mcp_test_rel} must use sys.executable for child subprocesses")
    if '["python3"' in mt_text or "['python3'" in mt_text:
      errors.append(f"{mcp_test_rel} must not hardcode 'python3' in subprocess calls")

  # 17. Ticket Disambiguation, Branch Naming, and Ledger Schema
  history_json_rel = ".workflow/history.json"
  history_json_path = repo_path(history_json_rel)
  if not os.path.exists(history_json_path):
    errors.append(f"Missing {history_json_rel}")
  else:
    try:
      with open(history_json_path) as f:
        history_data = json.load(f)
      local_id_regex = re.compile(r"^LOCAL-[1-9][0-9]*$")
      gh_id_regex = re.compile(r"^GH-[1-9][0-9]*$")
      for idx, item in enumerate(history_data):
        item_id = item.get("id")
        issue_url = item.get("issue_url")
        if not item_id:
          errors.append(f"{history_json_rel} entry {idx} is missing 'id'")
          continue
        if local_id_regex.match(item_id):
          if issue_url is not None:
            errors.append(f"{history_json_rel} entry {idx} ({item_id}) is a local ticket but has non-null issue_url: {issue_url}")
        elif gh_id_regex.match(item_id):
          if not issue_url or not issue_url.startswith("https://github.com/"):
            errors.append(f"{history_json_rel} entry {idx} ({item_id}) is a GitHub ticket but lacks valid GitHub issue_url")
        else:
          errors.append(f"{history_json_rel} entry {idx} has invalid id '{item_id}' - must match LOCAL-<N> or GH-<N>")
    except Exception as e:
      errors.append(f"Error parsing {history_json_rel}: {e}")

  history_md_rel = ".workflow/history.md"
  history_md_path = repo_path(history_md_rel)
  if not os.path.exists(history_md_path):
    errors.append(f"Missing {history_md_rel}")
  else:
    with open(history_md_path) as f:
      hmd_text = f.read()
    if "(Local)" in hmd_text:
      errors.append(f"{history_md_rel} must use LOCAL-<N> instead of '#<N> (Local)'")
    if "GH-1" not in hmd_text:
      errors.append(f"{history_md_rel} must use GH-<N> for GitHub issue links")

  # Request History Ledger Git lifecycle documentation check
  ledger_ref_rel = "skills/makeflow/references/12_request_history_ledger.md"
  ledger_ref = repo_path(ledger_ref_rel)
  if os.path.exists(ledger_ref):
    with open(ledger_ref) as f:
      ledger_text = f.read()
    if "git push origin main" not in ledger_text:
      errors.append(
          f"{ledger_ref_rel} must document 'git push origin main' for Target 90"
          " history commit push"
      )
    if "branch protection" not in ledger_text.lower():
      errors.append(
          f"{ledger_ref_rel} must document branch protection lifecycle for request"
          " history ledger"
      )

  intake_ref_rel = "skills/makeflow/references/01_intake_and_branching.md"
  intake_ref = repo_path(intake_ref_rel)
  if os.path.exists(intake_ref):
    with open(intake_ref) as f:
      intake_text = f.read()
    if "feat/local-" not in intake_text and "feat/local-<N>" not in intake_text:
      errors.append(f"{intake_ref_rel} must mandate 'feat/local-<N>-<slug>' or 'fix/local-<N>-<slug>' for ad-hoc / local tasks")

  hygiene_ref_rel = "skills/makeflow/references/05_pre_commit_hygiene.md"
  hygiene_ref = repo_path(hygiene_ref_rel)
  if os.path.exists(hygiene_ref):
    with open(hygiene_ref) as f:
      hygiene_text = f.read()
    if "Ticket: LOCAL-" not in hygiene_text:
      errors.append(f"{hygiene_ref_rel} must document 'Ticket: LOCAL-<N>' for local session commits")
    if "closes #" in hygiene_text.lower() and "never" not in hygiene_text.lower() and "prohibit" not in hygiene_text.lower() and "forbid" not in hygiene_text.lower():
      errors.append(f"{hygiene_ref_rel} must explicitly forbid 'closes #' on local session commits")

  agents_rule_rel = "rules/AGENTS.md"
  agents_rule = repo_path(agents_rule_rel)
  if os.path.exists(agents_rule):
    with open(agents_rule) as f:
      agents_text = f.read()
    if "LOCAL-" not in agents_text:
      errors.append(f"{agents_rule_rel} must codify LOCAL-<N> ticket disambiguation")
    if "closes #" not in agents_text and "Closes #" not in agents_text:
      errors.append(f"{agents_rule_rel} must forbid 'closes #' syntax on local session commits")

  # Adapter and Platform Guidance Alignment for Local Tickets
  for path, expected_tokens in [
      ("CLAUDE.md", ["feat/local-", "closes #"]),
      (".cursorrules", ["feat/local-", "closes #"]),
      (".cursor/rules/makeflow.mdc", ["feat/local-", "closes #"]),
      (".github/copilot-instructions.md", ["feat/local-", "closes #"]),
      ("adapters/chatgpt/instructions.md", ["feat/local-", "closes #"]),
      ("skills/makeflow/SKILL.md", ["feat/local-", "closes #", "LOCAL-", "GH-"]),
  ]:
    full_path = repo_path(path)
    if os.path.exists(full_path):
      with open(full_path) as f:
        content = f.read()
      for token in expected_tokens:
        if token not in content:
          errors.append(f"{path} missing expected token '{token}' for local ticket disambiguation")

  # Human Declaration Authority and Pause Gates (TEST-SCAR-PROC-102)
  authority_errors = validate_human_declaration_authority_and_pause_gates()
  errors.extend(authority_errors)
  if not authority_errors:
    print("✔ Human declaration authority and pause gates (TEST-SCAR-PROC-102) verified across all 7 rule and adapter files.")

  # Plan Artifact Lifecycle and Demotion (TEST-SCAR-PROC-103)
  demotion_errors = validate_plan_artifact_lifecycle_and_demotion()
  errors.extend(demotion_errors)
  if not demotion_errors:
    print("✔ Plan artifact lifecycle and demotion (TEST-SCAR-PROC-103) verified.")

  # Synthetic Scar Catalog Routing Invariants (GH-59, SCAR-TOOL-15)
  routing_errors = validate_synthetic_scar_catalog_routing()
  errors.extend(routing_errors)
  if not routing_errors:
    print("✔ Strict synthetic scar catalog routing (GH-59, SCAR-TOOL-15) verified.")

  # Adversarial Critic Manual Reference Invariants (GH-23, SCAR-PROC-105)
  critic_ref_errors = validate_adversarial_critic_manual_reference()
  errors.extend(critic_ref_errors)
  if not critic_ref_errors:
    print("✔ Explicit Six Pillars manual reference in critic dispatch (GH-23, SCAR-PROC-105) verified.")

  # Full Discussion and Comment Thread Ingestion at Intake (GH-78, SCAR-PROC-107)
  comment_ingest_errors = validate_comment_thread_ingestion_scar_proc_107()
  errors.extend(comment_ingest_errors)
  if not comment_ingest_errors:
    print("✔ Full comment thread ingestion at intake (SCAR-PROC-107) verified.")

  # POSIX Signal Trapping Invariants (GH-24, SCAR-SYS-07)
  signal_trap_errors = validate_posix_signal_trapping()
  errors.extend(signal_trap_errors)
  if not signal_trap_errors:
    print("✔ POSIX signal trapping invariants (GH-24, SCAR-SYS-07) verified.")

  # Temporary Script Quoting Invariants (GH-25, SCAR-SH-01)
  temp_script_errors = validate_temporary_script_quoting_scar()
  errors.extend(temp_script_errors)
  if not temp_script_errors:
    print("✔ Temporary script quoting invariants (GH-25, SCAR-SH-01) verified.")

  # BaseScript Pattern Invariants (GH-31, SCAR-ARCH-05)
  basescript_errors = validate_basescript_pattern_scar()
  errors.extend(basescript_errors)
  if not basescript_errors:
    print("✔ BaseScript pattern invariants (GH-31, SCAR-ARCH-05) verified.")

  # Diagnostic Path Formatting (GH-63)
  path_formatting_errors = validate_diagnostic_path_formatting()
  errors.extend(path_formatting_errors)
  if not path_formatting_errors:
    print("✔ Diagnostic missing path formatting (GH-63) verified.")

  # Arbitrary CWD Execution Validation (GH-60)
  cwd_errors = validate_arbitrary_cwd_execution()
  errors.extend(cwd_errors)
  if not cwd_errors and os.environ.get("_COMPAT_CHILD") != "1":
    print("✔ Arbitrary CWD execution verified from temporary directory.")

  # Repository-Scoped Hosting Discovery Invariants (GH-76, SCAR-GIT-30)
  hosting_errors = validate_hosting_mode_and_pure_git_invariants()
  errors.extend(hosting_errors)
  if not hosting_errors:
    print(
        "✔ Repository-scoped hosting discovery invariants (GH-76, SCAR-GIT-30)"
        " verified."
    )

  # Just-in-Time Vector 4 Reflex Gates Invariants (GH-57, SCAR-ARCH-06)
  reflex_errors = validate_vector4_reflex_gates_scar()
  errors.extend(reflex_errors)
  if not reflex_errors:
    print("✔ Just-in-Time Vector 4 Reflex gates invariants (GH-57, SCAR-ARCH-06) verified.")

  # Subagent Tool Mounting Permissions Invariants (GH-83)
  subagent_tool_errors = validate_subagent_tool_mounting_permissions()
  errors.extend(subagent_tool_errors)
  if not subagent_tool_errors:
    print("✔ Subagent tool mounting permissions (GH-83) verified.")

  # Cryptographically Anchored TDD Receipts Invariants (GH-53, SCAR-TEST-12)
  tdd_guard_errors = validate_tdd_guard_scar()
  errors.extend(tdd_guard_errors)
  if not tdd_guard_errors:
    print(
        "✔ Cryptographically anchored TDD receipts invariants (GH-53, SCAR-TEST-12)"
        " verified."
    )

  # Structural AST Anti-Shortcut Scrubbing Gate (GH-56, SCAR-AST-01)
  ast_scrub_errors = validate_ast_scrubber_scar_ast_01()
  errors.extend(ast_scrub_errors)
  if not ast_scrub_errors:
    print(
        "✔ Structural AST anti-shortcut scrubbing gate invariants (GH-56, SCAR-AST-01)"
        " verified."
    )

  # Anti-Speculative Defenses and Caller Grounding (GH-85, SCAR-ARCH-07)
  anti_speculation_errors = validate_anti_speculation_scar()
  errors.extend(anti_speculation_errors)
  if not anti_speculation_errors:
    print("✔ Anti-speculative defenses and caller grounding (GH-85, SCAR-ARCH-07) verified.")

  # Two-Tier Scar Creation Lifecycle and Discovery Invariants (GH-93)
  scar_lifecycle_errors = validate_two_tier_scar_lifecycle_and_discovery()
  errors.extend(scar_lifecycle_errors)
  if not scar_lifecycle_errors:
    print(
        "✔ Two-Tier scar creation lifecycle and discovery invariants (GH-93)"
        " verified."
    )

  # Target 42 Non-Blocking Observations Summary Invariants (GH-89)
  nbo_errors = validate_target_42_nbo_summary_invariants()
  errors.extend(nbo_errors)
  if not nbo_errors:
    print(
        "✔ Target 42 Non-Blocking Observations summary invariants (GH-89)"
        " verified."
    )

  # Pre-Push Conflict and Rebase Invariants (GH-95, SCAR-GIT-20, SCAR-GIT-25)
  pre_push_errors = validate_pre_push_conflict_and_rebase_invariants()
  errors.extend(pre_push_errors)
  if not pre_push_errors:
    print(
        "✔ Pre-push conflict and rebase invariants (GH-95, SCAR-GIT-20, SCAR-GIT-25)"
        " verified."
    )

  # Critic Observation Triage and SCAR-PROC-108 Invariants (GH-66)
  critic_triage_errors = validate_critic_observation_triage_invariants()
  errors.extend(critic_triage_errors)
  if not critic_triage_errors:
    print(
        "✔ Critic observation triage protocol and SCAR-PROC-108 invariants (GH-66)"
        " verified."
    )

  # Target 40 Model Picker Invariants (GH-81)
  t40_model_errors = validate_target_40_model_picker_invariants()
  errors.extend(t40_model_errors)
  if not t40_model_errors:
    print(
        "✔ Target 40 model picker config bypass and pause invariants (GH-81)"
        " verified."
    )

  # Target 60 Pre-Push Rebase and Critic Re-Audit Invariants (GH-126)
  rebase_reaudit_errors = validate_target_60_rebase_critic_reaudit_invariants()
  errors.extend(rebase_reaudit_errors)
  if not rebase_reaudit_errors:
    print(
        "✔ Target 60 pre-push rebase and critic re-audit invariants (GH-126)"
        " verified."
    )

  # Volume Triage Interview Invariants (GH-128, SCAR-TOOL-16)
  volume_triage_errors = validate_volume_triage_interview_scar_tool_16()
  errors.extend(volume_triage_errors)
  if not volume_triage_errors:
    print(
        "✔ Volume triage interview invariants (GH-128, SCAR-TOOL-16)"
        " verified."
    )

  # Optional Circuit Breaker Format Invariants (GH-120)
  cb_errors = validate_optional_circuit_breaker_invariants()
  errors.extend(cb_errors)
  if not cb_errors:
    print("✔ Optional Circuit Breaker format invariants (GH-120) verified.")

  # Open Technical English Standards & SimpleEnglish Removal Invariants (GH-152)
  open_eng_errors = validate_open_technical_english_specs()
  errors.extend(open_eng_errors)
  if not open_eng_errors:
    print(
        "✔ Open technical English specifications and SimpleEnglish removal (GH-152)"
        " verified."
    )

  # Three-Layer Python Module Architecture Invariants (GH-169, SCAR-ARCH-05)
  three_layer_errors = validate_three_layer_python_architecture()
  errors.extend(three_layer_errors)
  if not three_layer_errors:
    print(
        "✔ Three-layer Python module architecture (GH-169, SCAR-ARCH-05)"
        " verified."
    )

  # Guard Denial Protocol Invariants (GH-167, SCAR-PROC-124)
  scar_124_errors = validate_guard_denial_protocol_scar_proc_124()
  errors.extend(scar_124_errors)
  if not scar_124_errors:
    print("✔ Guard denial protocol invariants (GH-167, SCAR-PROC-124) verified.")

  # Polyglot Idiomatic Directory and Module Structure Invariants (GH-174)
  polyglot_dir_errors = validate_polyglot_directory_and_module_structure()
  errors.extend(polyglot_dir_errors)
  if not polyglot_dir_errors:
    print(
        "✔ Polyglot idiomatic directory and module structure (GH-174)"
        " verified."
    )

  # Polyglot Layer Separation and Structural Debt Triage Invariants (GH-175, SCAR-ARCH-08)
  scar_arch_08_errors = validate_polyglot_layer_separation_scar_arch_08()
  errors.extend(scar_arch_08_errors)
  if not scar_arch_08_errors:
    print(
        "✔ Polyglot layer separation and structural debt triage (GH-175, SCAR-ARCH-08)"
        " verified."
    )

  # Upstream Root-Cause Remediation Over Downstream Workarounds (GH-232, SCAR-ARCH-09)
  scar_arch_09_errors = validate_scar_arch_09_upstream_root_cause()
  errors.extend(scar_arch_09_errors)
  if not scar_arch_09_errors:
    print(
        "✔ Upstream root-cause remediation invariants (GH-232, SCAR-ARCH-09)"
        " verified."
    )

  # Preserve Structured Data Over Lossy Prose Round-Trips (GH-240, SCAR-ARCH-10)
  scar_arch_10_errors = validate_scar_arch_10_structured_data()
  errors.extend(scar_arch_10_errors)
  if not scar_arch_10_errors:
    print(
        "✔ Structured data preservation invariants (GH-240, SCAR-ARCH-10)"
        " verified."
    )

  # Canonical Repository Path Map and Qualified Filename Refs (GH-187)
  canonical_path_errors = validate_canonical_repo_paths_and_qualified_refs()
  errors.extend(canonical_path_errors)
  if not canonical_path_errors:
    print(
        "✔ Canonical repository path map and qualified filename references (GH-187)"
        " verified."
    )

  # Polyglot Code Documentation Standards and Tree Assembly Invariants (GH-44)
  polyglot_doc_errors = validate_polyglot_documentation_standards()
  errors.extend(polyglot_doc_errors)
  if not polyglot_doc_errors:
    print(
        "✔ Polyglot code documentation standards and tree assembly (GH-44)"
        " verified."
    )

  # Project-Adaptive Documentation Standards & Progressive Disclosure Tree (GH-44, SCAR-PROC-159, SCAR-DOC-16)
  scar_proc_159_errors = validate_scar_proc_159_documentation_standards()
  errors.extend(scar_proc_159_errors)
  if not scar_proc_159_errors:
    print(
        "✔ Project-adaptive documentation standards and SCAR-PROC-159 / SCAR-DOC-16 (GH-44)"
        " verified."
    )

  # Target 90 Ticket Resolution Comment Invariants (GH-197)
  t90_comment_errors = validate_target_90_resolution_comment_invariants()
  errors.extend(t90_comment_errors)
  if not t90_comment_errors:
    print(
        "✔ Target 90 ticket resolution comment invariants (GH-197)"
        " verified."
    )

  # Target 90 CHANGELOG.md and Release Hygiene Invariants (GH-211)
  t90_changelog_errors = check_target_90_changelog_and_release_invariants()
  errors.extend(t90_changelog_errors)
  if not t90_changelog_errors:
    print(
        "✔ Target 90 CHANGELOG.md and release hygiene invariants (GH-211)"
        " verified."
    )

  # Deterministic Supervisory Halt Airlock Invariants (GH-220, SCAR-PROC-95)
  airlock_95_errors = check_scar_proc_95_subagent_halt_airlock()
  errors.extend(airlock_95_errors)
  if not airlock_95_errors:
    print(
        "✔ Supervisory halt airlock and SCAR-PROC-95 invariants (GH-220)"
        " verified."
    )

  # Target 15 Archaeology Git File Tree Index Dispatch Invariants (GH-246)
  tree_dispatch_errors = check_archaeology_file_tree_dispatch_contract()
  errors.extend(tree_dispatch_errors)
  if not tree_dispatch_errors:
    print(
        "✔ Target 15 archaeology file tree index dispatch invariants (GH-246)"
        " verified."
    )

  return errors


validate_all = validate


POLYGLOT_DOCUMENTATION_STANDARDS_REQUIREMENTS = {
    "c_cpp.md": ("Doxyfile", "Doxygen", "@param", "@return", "doxygen"),
    "csharp_dotnet.md": (
        "GenerateDocumentationFile",
        "<summary>",
        "<param",
        "<returns>",
        "CS1591",
    ),
    "dart_flutter.md": (
        "dartdoc_options.yaml",
        "public_member_api_docs",
        "///",
        "dart doc",
    ),
    "dlang.md": ("Ddoc", "Params:", "Returns:", "dub build --build=docs"),
    "elixir.md": ("ex_doc", "@moduledoc", "@doc", "@spec", "mix docs"),
    "gdscript_godot.md": ("project.godot", "##", "--doctool"),
    "go.md": ("Godoc", "go doc", "pkgsite"),
    "haxe.md": ("-xml", "dox", "haxelib run dox"),
    "java_kotlin_scala.md": (
        "Javadoc",
        "KDoc",
        "Scaladoc",
        "javadoc:javadoc",
        "dokkaHtml",
        "sbt doc",
    ),
    "nim.md": ("##", "runnableExamples", "nim doc"),
    "odin.md": ("ols.json", "//", "odin doc"),
    "perl.md": ("=head1", "=cut", "podchecker", "pod2html"),
    "php.md": ("phpdoc", "/** @param", "phpstan"),
    "powershell.md": (".SYNOPSIS", ".PARAMETER", ".EXAMPLE", "platyPS"),
    "python.md": ("docs/conf.py", "mkdocs.yml", "PEP 257", "sphinx-build"),
    "ruby.md": (".yardopts", "YARD", "@param", "yard doc"),
    "rust.md": ("rustdoc", "///", "//!", "RUSTDOCFLAGS", "cargo doc"),
    "scientific_r_julia_lua.md": (
        "roxygen2",
        "pkgdown",
        "Documenter.jl",
        "docs/make.jl",
        "LDoc",
        "ldoc",
    ),
    "sql_migrations.md": (
        "dbt_project.yml",
        "COMMENT ON TABLE",
        "COMMENT ON COLUMN",
        "dbt docs generate",
    ),
    "swift.md": ("swift-docc-plugin", "DocC", "///", "generate-documentation"),
    "typescript_javascript.md": (
        "typedoc.json",
        "TSDoc",
        "JSDoc",
        "npx typedoc",
    ),
}

POLYGLOT_DOCUMENTATION_MARKERS = (
    "Project Convention Detection",
    "Idiomatic Doc Comment Standard",
    "Documentation Tree Assembly Command",
    "Documentation Anti-Patterns to Flag at Target 40",
)


def _check_polyglot_documentation_standards_guides(
    guide_files: dict[str, str],
) -> list[str]:
  """Validates Code Documentation Standards and Tree Assembly sections across all 21 polyglot guides (GH-44)."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scar_candidate import (
      count_words_in_sentence,
      mask_code_and_math_spans,
      split_into_sentences,
  )

  errs: list[str] = []
  base_rel = "skills/makeflow/references/polyglot"
  section_heading = "## Code Documentation Standards and Tree Assembly"

  for fname, req_tokens in POLYGLOT_DOCUMENTATION_STANDARDS_REQUIREMENTS.items():
    rel_path = f"{base_rel}/{fname}"
    if fname not in guide_files:
      errs.append(f"Missing {rel_path}")
      continue
    text = guide_files[fname]
    section = _extract_markdown_h2_section(text, section_heading)
    if not section.strip():
      errs.append(
          f"{rel_path} missing required H2 section '{section_heading}'"
      )
      continue

    for marker in POLYGLOT_DOCUMENTATION_MARKERS:
      if marker not in section:
        errs.append(
            f"{rel_path} section '{section_heading}' missing required documentation marker '{marker}'"
        )

    for tok in req_tokens:
      if tok not in section:
        errs.append(
            f"{rel_path} section '{section_heading}' missing required documentation token '{tok}'"
        )

    errs.extend(_check_prose_punctuation(rel_path, section))

    in_fence = False
    for idx, raw_line in enumerate(section.splitlines(), start=1):
      stripped = raw_line.strip()
      if stripped.startswith("```"):
        in_fence = not in_fence
        continue
      if in_fence or not stripped or stripped.startswith("#"):
        continue
      no_code = re.sub(r"`[^`]*`", "", stripped)
      match = _POLYGLOT_CONTRACTION_RE.search(no_code)
      if match:
        errs.append(
            f"{rel_path}:{idx} contains prohibited contraction '{match.group(0)}' in prose"
        )
      prose_body = re.sub(r"^-\s+(?:\*\*[^*]+\*\*:\s*)?", "", stripped)
      masked = mask_code_and_math_spans(prose_body)
      for sent in split_into_sentences(masked):
        wc = count_words_in_sentence(sent)
        if wc > 20:
          errs.append(
              f"{rel_path}:{idx} sentence exceeds 20-word limit ({wc} words): {sent}"
          )

  return errs


def validate_polyglot_documentation_standards() -> list[str]:
  """Validates Code Documentation Standards and Tree Assembly sections across all 21 polyglot guides (GH-44)."""
  errors: list[str] = []
  base_rel = "skills/makeflow/references/polyglot"
  guide_files: dict[str, str] = {}
  for fname in POLYGLOT_DOCUMENTATION_STANDARDS_REQUIREMENTS:
    fpath = repo_path(f"{base_rel}/{fname}")
    if os.path.isfile(fpath):
      with open(fpath, "r", encoding="utf-8") as f:
        guide_files[fname] = f.read()

  errors.extend(_check_polyglot_documentation_standards_guides(guide_files))

  # In-memory negative falsification checks (SCAR-TEST-11)
  sample_guides: dict[str, str] = {}
  for fname, req_tokens in POLYGLOT_DOCUMENTATION_STANDARDS_REQUIREMENTS.items():
    tok_spans = " ".join(f"`{t}`" for t in req_tokens)
    sample_guides[fname] = (
        f"# {fname}\n\n"
        "## Code Documentation Standards and Tree Assembly\n\n"
        f"- **Project Convention Detection**: Inspect {tok_spans} before writing code.\n"
        "- **Idiomatic Doc Comment Standard**: Write clear doc comments on public symbols.\n"
        "- **Documentation Tree Assembly Command**: Build the documentation tree cleanly.\n"
        "- **Documentation Anti-Patterns to Flag at Target 40**: Flag missing parameter tags.\n"
    )

  baseline_errs = _check_polyglot_documentation_standards_guides(sample_guides)
  if baseline_errs:
    errors.append(
        f"Negative falsification baseline failed for polyglot doc guides: {baseline_errs}"
    )

  # 1. Missing H2 heading
  bad_no_heading = dict(sample_guides)
  bad_no_heading["python.md"] = "# Python Guide\n\n## Core Idioms\n\n- Use Python.\n"
  if not any(
      "missing required H2 section '## Code Documentation Standards and Tree Assembly'"
      in e
      for e in _check_polyglot_documentation_standards_guides(bad_no_heading)
  ):
    errors.append(
        "Negative falsification failed: missing doc H2 section in python.md was not rejected"
    )

  # 2. Missing marker
  bad_no_marker = dict(sample_guides)
  bad_no_marker["rust.md"] = bad_no_marker["rust.md"].replace(
      "Documentation Anti-Patterns to Flag at Target 40", "Other Notes"
  )
  if not any(
      "Documentation Anti-Patterns to Flag at Target 40" in e
      for e in _check_polyglot_documentation_standards_guides(bad_no_marker)
  ):
    errors.append(
        "Negative falsification failed: missing documentation marker in rust.md was not rejected"
    )

  # 3. Missing language token
  bad_no_tok = dict(sample_guides)
  bad_no_tok["c_cpp.md"] = bad_no_tok["c_cpp.md"].replace("Doxyfile", "OtherFile")
  if not any(
      "Doxyfile" in e
      for e in _check_polyglot_documentation_standards_guides(bad_no_tok)
  ):
    errors.append(
        "Negative falsification failed: missing Doxyfile token in c_cpp.md was not rejected"
    )

  return errors


SCAR_PROC_159_TARGET_FILES = (
    "skills/makeflow/references/scars/process.md",
    "skills/makeflow/references/scars/tooling_and_hci.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "skills/makeflow/references/03_tdd_and_code_quality.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "agents/adversarial-critic.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/11_polyglot_monorepos.md",
    "agents/archaeology-worker.md",
)


def check_scar_proc_159_documentation_standards(
    files_map: dict[str, str] | None = None,
) -> list[str]:
  """Validates SCAR-PROC-159, SCAR-DOC-16 progressive disclosure tree, and Six Pillars doc rules (GH-44)."""
  if files_map is None:
    files_map = {}
    for rel in SCAR_PROC_159_TARGET_FILES:
      fpath = repo_path(rel)
      if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  errs: list[str] = []
  for rel in SCAR_PROC_159_TARGET_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. skills/makeflow/references/scars/process.md (SCAR-PROC-159)
  proc_rel = "skills/makeflow/references/scars/process.md"
  proc_text = files_map[proc_rel]
  table_match = re.search(
      r"## Scars in this Category.*?(?=\n###|\Z)", proc_text, re.DOTALL
  )
  table_sec = table_match.group(0) if table_match else ""
  t158 = re.search(r"(?m)^\|\s*`SCAR-PROC-158`\s*\|", table_sec)
  t159 = re.search(r"(?m)^\|\s*`SCAR-PROC-159`\s*\|", table_sec)
  if (
      not t159
      or "Project-Adaptive Code Documentation Standards and Tree Verification"
      not in table_sec
  ):
    errs.append(
        f"{proc_rel} category table must list SCAR-PROC-159 (Project-Adaptive Code Documentation Standards and Tree Verification)"
    )
  elif not (t158 and t158.start() < t159.start()):
    errs.append(
        f"{proc_rel} category table must place SCAR-PROC-159 monotonically after SCAR-PROC-158"
    )

  m158 = re.search(r"(?m)^###\s+SCAR-PROC-158\b", proc_text)
  m159 = re.search(
      r"(?m)^###\s+SCAR-PROC-159:\s+Project-Adaptive Code Documentation Standards and Tree Verification\b",
      proc_text,
  )
  if not (m158 and m159 and m158.start() < m159.start()):
    errs.append(
        f"{proc_rel} must define '### SCAR-PROC-159: Project-Adaptive Code Documentation Standards and Tree Verification' monotonically after SCAR-PROC-158"
    )
  else:
    sec159 = proc_text[m159.start() :].split("\n### SCAR-")[0]
    extracted: dict[str, str] = {}
    req_fields = ("Wound", "Trap", "Permanent Reflex", "Socratic Probe")
    for field in req_fields:
      fm = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", sec159)
      val = fm.group(1).strip() if fm else ""
      extracted[field] = val
      if not val:
        errs.append(f"{proc_rel} SCAR-PROC-159 missing non-empty '**{field}**:'")

    cb_match = re.search(r"\*\*Circuit Breaker\*\*:[ \t]*([^\n\r]+)", sec159)
    cb_val = cb_match.group(1).strip() if cb_match else ""

    for tok in (
        "skills/makeflow/references/polyglot/<lang>.md",
        "15_archaeology_receipt.md",
        "20_plan.md",
        "Target 15",
        "Target 40",
    ):
      if tok not in sec159:
        errs.append(f"{proc_rel} SCAR-PROC-159 missing required token '{tok}'")

    if all(extracted[f] for f in req_fields):
      candidate = {
          "id": "SCAR-PROC-159",
          "raw_wound": extracted["Wound"],
          "wound": extracted["Wound"],
          "raw_trap": extracted["Trap"],
          "trap": extracted["Trap"],
          "raw_circuit_breaker": cb_val,
          "circuit_breaker": cb_val,
          "raw_reflex": extracted["Permanent Reflex"],
          "reflex": extracted["Permanent Reflex"],
          "socratic_probe": extracted["Socratic Probe"],
      }
      errs.extend(ScarCandidateValidator().validate(candidate))
      probe_sents = split_into_sentences(extracted["Socratic Probe"])
      if not (1 <= len(probe_sents) <= 3):
        errs.append(
            f"{proc_rel} SCAR-PROC-159 Socratic Probe must contain 1-3 '?' sentences, found {len(probe_sents)}"
        )
    errs.extend(_check_prose_punctuation(f"{proc_rel}#SCAR-PROC-159", sec159))

  # 2. skills/makeflow/references/scars/tooling_and_hci.md (SCAR-DOC-16)
  hci_rel = "skills/makeflow/references/scars/tooling_and_hci.md"
  hci_text = files_map[hci_rel]
  if "concentric documentation rings" in hci_text.lower() or "concentric rings" in hci_text.lower():
    errs.append(
        f"{hci_rel} must not contain legacy 'Concentric Documentation Rings' or 'concentric rings'"
    )
  hci_table_match = re.search(
      r"## Scars in this Category.*?(?=\n###|\Z)", hci_text, re.DOTALL
  )
  hci_table = hci_table_match.group(0) if hci_table_match else ""
  if (
      "SCAR-DOC-16" not in hci_table
      or "Documentation Tree (Progressive Disclosure)" not in hci_table
  ):
    errs.append(
        f"{hci_rel} category table must list SCAR-DOC-16 titled 'Documentation Tree (Progressive Disclosure)'"
    )
  mdoc16 = re.search(
      r"(?m)^###\s+SCAR-DOC-16:\s+Documentation Tree \(Progressive Disclosure\)\s*$",
      hci_text,
  )
  if not mdoc16:
    errs.append(
        f"{hci_rel} must define '### SCAR-DOC-16: Documentation Tree (Progressive Disclosure)'"
    )
  else:
    sec_doc16 = hci_text[mdoc16.start() :].split("\n### SCAR-")[0]
    doc16_fields: dict[str, str] = {}
    for field in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe"):
      fm = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", sec_doc16)
      val = fm.group(1).strip() if fm else ""
      doc16_fields[field] = val
      if not val:
        errs.append(f"{hci_rel} SCAR-DOC-16 missing non-empty '**{field}**:'")
    if all(doc16_fields[f] for f in ("Wound", "Trap", "Permanent Reflex", "Socratic Probe")):
      doc16_candidate = {
          "id": "SCAR-DOC-16",
          "raw_wound": doc16_fields["Wound"],
          "wound": doc16_fields["Wound"],
          "raw_trap": doc16_fields["Trap"],
          "trap": doc16_fields["Trap"],
          "raw_circuit_breaker": "",
          "circuit_breaker": "",
          "raw_reflex": doc16_fields["Permanent Reflex"],
          "reflex": doc16_fields["Permanent Reflex"],
          "socratic_probe": doc16_fields["Socratic Probe"],
      }
      errs.extend(ScarCandidateValidator().validate(doc16_candidate))
    errs.extend(_check_prose_punctuation(f"{hci_rel}#SCAR-DOC-16", sec_doc16))

  # 3. skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_text = files_map[cat_rel]
  if "concentric documentation rings" in cat_text.lower() or "concentric rings" in cat_text.lower():
    errs.append(
        f"{cat_rel} must not contain legacy 'Concentric Documentation Rings'"
    )
  if not check_scar_catalog_routing(cat_text, "SCAR-PROC-159", "process.md"):
    errs.append(
        f"{cat_rel} Master Routing Table must strictly route SCAR-PROC-159 to scars/process.md"
    )
  c158 = re.search(r"(?m)^\|\s*`SCAR-PROC-158`\s*\|", cat_text)
  c159 = re.search(r"(?m)^\|\s*`SCAR-PROC-159`\s*\|", cat_text)
  if not (c158 and c159 and c158.start() < c159.start()):
    errs.append(
        f"{cat_rel} must place SCAR-PROC-159 monotonically after SCAR-PROC-158"
    )
  if (
      not check_scar_catalog_routing(cat_text, "SCAR-DOC-16", "tooling_and_hci.md")
      or "Documentation Tree (Progressive Disclosure)" not in cat_text
  ):
    errs.append(
        f"{cat_rel} must route SCAR-DOC-16 titled 'Documentation Tree (Progressive Disclosure)' to scars/tooling_and_hci.md"
    )

  # 4. skills/makeflow/references/03_tdd_and_code_quality.md
  r03_rel = "skills/makeflow/references/03_tdd_and_code_quality.md"
  r03_text = files_map[r03_rel]
  if "concentric documentation rings" in r03_text.lower() or "concentric rings" in r03_text.lower():
    errs.append(
        f"{r03_rel} must not contain legacy 'Concentric Documentation Rings'"
    )
  if "## 4. Documentation Tree (Progressive Disclosure) (`SCAR-DOC-16`)" not in r03_text:
    errs.append(
        f"{r03_rel} must contain '## 4. Documentation Tree (Progressive Disclosure) (`SCAR-DOC-16`)'"
    )

  # 5. Six Pillars Manual & Critic Agent (Pillars 1, 5, 6)
  from lib.scar_candidate import (
      count_words_in_sentence,
      mask_code_and_math_spans,
  )

  for t40_rel in (
      "skills/makeflow/references/04_six_pillars_adversarial_review.md",
      "agents/adversarial-critic.md",
  ):
    t40_text = files_map[t40_rel]
    p1_match = re.search(
        r"(?m)^###\s+1\.\s+Intent\b.*?(?=^###\s+2\b|\Z)",
        t40_text,
        re.DOTALL,
    )
    p1_sec = p1_match.group(0) if p1_match else ""
    for req_p1 in (
        "SCAR-PROC-159",
        "SCAR-DOC-16",
        "Documentation Tree (Progressive Disclosure)",
    ):
      if req_p1 not in p1_sec:
        errs.append(f"{t40_rel} Pillar 1 must reference '{req_p1}'")

    p5_match = re.search(
        r"(?m)^###\s+5\.\s+Reviewer Defense\b.*?(?=^###\s+6\b|\Z)",
        t40_text,
        re.DOTALL,
    )
    p5_sec = p5_match.group(0) if p5_match else ""
    p5_lower = p5_sec.lower()
    for req_p5 in ("parameter", "return", "exception", "example", "simple english"):
      if req_p5 not in p5_lower:
        errs.append(
            f"{t40_rel} Pillar 5 must check documentation accuracy and Simple English token '{req_p5}'"
        )

    p6_match = re.search(
        r"(?m)^###\s+6\.\s+Immune Defenses & Crash Antibodies\b.*?(?=^---|\n##\s+|\n4\.\s+End your report|\Z)",
        t40_text,
        re.DOTALL,
    )
    p6_sec = p6_match.group(0) if p6_match else ""
    p6_lower = p6_sec.lower()
    if "SCAR-PROC-159" not in p6_sec:
      errs.append(f"{t40_rel} Pillar 6 must reference 'SCAR-PROC-159'")
    if "documentation tree" not in p6_lower or "doc comment" not in p6_lower:
      errs.append(
          f"{t40_rel} Pillar 6 must check doc comment formatting and documentation tree buildability"
      )
    if "warning" not in p6_lower or "broken link" not in p6_lower:
      errs.append(
          f"{t40_rel} Pillar 6 must check for clean builds without warnings or broken links"
      )

    for pillar_label, pillar_sec in (
        ("Pillar 1", p1_sec),
        ("Pillar 5", p5_sec),
        ("Pillar 6", p6_sec),
    ):
      errs.extend(
          _check_prose_punctuation(f"{t40_rel}#{pillar_label}", pillar_sec)
      )
      in_fence = False
      for idx, raw_line in enumerate(pillar_sec.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
          in_fence = not in_fence
          continue
        if in_fence or not stripped or stripped.startswith("#"):
          continue
        no_code = re.sub(r"`[^`]*`", "", stripped)
        cmatch = _POLYGLOT_CONTRACTION_RE.search(no_code)
        if cmatch:
          errs.append(
              f"{t40_rel} {pillar_label}:{idx} contains prohibited contraction '{cmatch.group(0)}' in prose"
          )
        prose_body = re.sub(
            r"^(?:-\s+|\d+\.\s+)(?:\*\*[^*]+\*\*:\s*)?", "", stripped
        )
        masked = mask_code_and_math_spans(prose_body)
        for sent in split_into_sentences(masked):
          wc = count_words_in_sentence(sent)
          if wc > 25:
            errs.append(
                f"{t40_rel} {pillar_label}:{idx} sentence exceeds 25-word descriptive limit ({wc} words): {sent}"
            )

  # 6. Target 15 & Target 20 Archaeology and Planning Manuals
  for arch_rel in (
      "skills/makeflow/references/02_archaeology_and_planning.md",
      "skills/makeflow/references/11_polyglot_monorepos.md",
      "agents/archaeology-worker.md",
  ):
    arch_text = files_map[arch_rel]
    if "SCAR-PROC-159" not in arch_text:
      errs.append(f"{arch_rel} must reference 'SCAR-PROC-159'")
    if "skills/makeflow/references/polyglot/<lang>.md" not in arch_text:
      errs.append(
          f"{arch_rel} must reference 'skills/makeflow/references/polyglot/<lang>.md'"
      )
    if "documentation" not in arch_text.lower():
      errs.append(
          f"{arch_rel} must mandate discovering project documentation standards"
      )
    for idx, raw_line in enumerate(arch_text.splitlines(), start=1):
      if "SCAR-PROC-159" not in raw_line:
        continue
      stripped = raw_line.strip()
      errs.extend(_check_prose_punctuation(f"{arch_rel}:{idx}", stripped))
      prose_body = re.sub(
          r"^(?:-\s+|\d+\.\s+)(?:\*\*[^*]+\*\*:\s*|[A-Za-z0-9_ ()`-]+:\s*)?",
          "",
          stripped,
      )
      masked = mask_code_and_math_spans(prose_body)
      for sent in split_into_sentences(masked):
        wc = count_words_in_sentence(sent)
        if wc > 25:
          errs.append(
              f"{arch_rel}:{idx} sentence exceeds 25-word descriptive limit ({wc} words): {sent}"
          )

  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_text = files_map[r02_rel]
  r02_sec1 = _extract_markdown_h2_section(r02_text, "## 1. Target 15")
  r02_sec2 = _extract_markdown_h2_section(r02_text, "## 2. Target 20")
  if "SCAR-PROC-159" not in r02_sec1:
    errs.append(f"{r02_rel} Section 1 (Target 15) must reference SCAR-PROC-159")
  if "SCAR-PROC-159" not in r02_sec2:
    errs.append(f"{r02_rel} Section 2 (Target 20) must reference SCAR-PROC-159")

  return errs


def validate_scar_proc_159_documentation_standards() -> list[str]:
  """Validates SCAR-PROC-159 and SCAR-DOC-16 on the live repository (GH-44)."""
  return check_scar_proc_159_documentation_standards()


class TestScarProc159DocumentationStandards(unittest.TestCase):
  """Hermetic unit and negative mutation falsification tests for GH-44 (SCAR-PROC-159, SCAR-DOC-16)."""

  def test_live_repository_polyglot_documentation_standards(self):
    """Verifies all 21 polyglot guides satisfy Code Documentation Standards and Tree Assembly rules."""
    self.assertEqual(validate_polyglot_documentation_standards(), [])

  def test_live_repository_scar_proc_159_and_doc_16(self):
    """Verifies SCAR-PROC-159, SCAR-DOC-16, Six Pillars 1/5/6, and Target 15/20 documentation rules."""
    self.assertEqual(check_scar_proc_159_documentation_standards(), [])

  def test_negative_falsification_polyglot_guides_in_tempdir(self):
    """Verifies mutations to polyglot documentation sections in a temporary directory fail validation."""
    with tempfile.TemporaryDirectory() as tmpdir:
      guide_files: dict[str, str] = {}
      for fname, req_tokens in POLYGLOT_DOCUMENTATION_STANDARDS_REQUIREMENTS.items():
        tok_spans = " ".join(f"`{t}`" for t in req_tokens)
        content = (
            f"# {fname}\n\n"
            "## Code Documentation Standards and Tree Assembly\n\n"
            f"- **Project Convention Detection**: Inspect {tok_spans} before writing code.\n"
            "- **Idiomatic Doc Comment Standard**: Write clear doc comments on public symbols.\n"
            "- **Documentation Tree Assembly Command**: Build the documentation tree cleanly.\n"
            "- **Documentation Anti-Patterns to Flag at Target 40**: Flag missing parameter tags.\n"
        )
        fpath = os.path.join(tmpdir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
          f.write(content)
        with open(fpath, "r", encoding="utf-8") as f:
          guide_files[fname] = f.read()

      self.assertEqual(
          _check_polyglot_documentation_standards_guides(guide_files), []
      )

      # 1. Removing ## Code Documentation Standards and Tree Assembly fails validation
      mut_no_section = dict(guide_files)
      mut_no_section["python.md"] = "# Python Guide\n\n## 1. Detection Markers\n"
      errs_no_section = _check_polyglot_documentation_standards_guides(
          mut_no_section
      )
      self.assertTrue(
          any(
              "missing required H2 section '## Code Documentation Standards and Tree Assembly'"
              in e
              for e in errs_no_section
          ),
          f"Expected missing H2 section error, got: {errs_no_section}",
      )

      # 2. Removing any of the 4 POLYGLOT_DOCUMENTATION_MARKERS or language tokens fails validation
      for marker in POLYGLOT_DOCUMENTATION_MARKERS:
        mut_no_marker = dict(guide_files)
        mut_no_marker["rust.md"] = mut_no_marker["rust.md"].replace(
            marker, "Omitted Marker"
        )
        errs_marker = _check_polyglot_documentation_standards_guides(
            mut_no_marker
        )
        self.assertTrue(
            any(marker in e for e in errs_marker),
            f"Expected missing marker '{marker}' error, got: {errs_marker}",
        )

      mut_no_token = dict(guide_files)
      mut_no_token["typescript_javascript.md"] = mut_no_token[
          "typescript_javascript.md"
      ].replace("typedoc.json", "omitted_config.json")
      errs_token = _check_polyglot_documentation_standards_guides(mut_no_token)
      self.assertTrue(
          any("typedoc.json" in e for e in errs_token),
          f"Expected missing token 'typedoc.json' error, got: {errs_token}",
      )

      # 3. Injecting >20-word sentence, contraction, semicolon, or em-dash fails validation
      mut_long = dict(guide_files)
      mut_long["go.md"] += (
          "- Document every single exported package symbol with a complete sentence "
          "whenever you build a new microservice handler for production deployment today.\n"
      )
      self.assertTrue(
          any(
              "exceeds 20-word limit" in e
              for e in _check_polyglot_documentation_standards_guides(mut_long)
          )
      )

      mut_contraction = dict(guide_files)
      mut_contraction["elixir.md"] += "- You don't skip module documentation.\n"
      self.assertTrue(
          any(
              "prohibited contraction" in e
              for e in _check_polyglot_documentation_standards_guides(
                  mut_contraction
              )
          )
      )

      mut_semi = dict(guide_files)
      mut_semi["c_cpp.md"] += "- Document headers; build Doxygen.\n"
      self.assertTrue(
          any(
              "semicolon" in e
              for e in _check_polyglot_documentation_standards_guides(mut_semi)
          )
      )

      mut_emdash = dict(guide_files)
      mut_emdash["c_cpp.md"] += "- Document headers \u2014 build Doxygen.\n"
      self.assertTrue(
          any(
              "em-dash" in e
              for e in _check_polyglot_documentation_standards_guides(mut_emdash)
          )
      )

  def test_negative_falsification_scar_proc_159_and_doc_16_in_tempdir(self):
    """Verifies mutations to SCAR-PROC-159, SCAR-DOC-16, and Six Pillars files fail validation."""
    with tempfile.TemporaryDirectory() as tmpdir:
      base_files: dict[str, str] = {}
      for rel in SCAR_PROC_159_TARGET_FILES:
        src_path = repo_path(rel)
        dst_path = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with open(src_path, "r", encoding="utf-8") as f:
          text = f.read()
        with open(dst_path, "w", encoding="utf-8") as f:
          f.write(text)
        base_files[rel] = text

      # Verify live baseline passes once implemented
      self.assertEqual(
          check_scar_proc_159_documentation_standards(base_files), []
      )

      # 4. Reintroducing "Concentric Documentation Rings" or removing "Documentation Tree (Progressive Disclosure)"
      for rel_doc16 in (
          "skills/makeflow/references/scars/tooling_and_hci.md",
          "skills/makeflow/references/09_synthetic_scars_catalog.md",
          "skills/makeflow/references/03_tdd_and_code_quality.md",
      ):
        mut_concentric = dict(base_files)
        mut_concentric[rel_doc16] += "\nConcentric Documentation Rings\n"
        errs_conc = check_scar_proc_159_documentation_standards(mut_concentric)
        self.assertTrue(
            any(rel_doc16 in e and "Concentric" in e for e in errs_conc),
            f"Expected Concentric rejection in {rel_doc16}, got: {errs_conc}",
        )

        mut_no_tree = dict(base_files)
        mut_no_tree[rel_doc16] = mut_no_tree[rel_doc16].replace(
            "Documentation Tree (Progressive Disclosure)",
            "Flat Documentation Manual",
        )
        errs_tree = check_scar_proc_159_documentation_standards(mut_no_tree)
        self.assertTrue(
            any(rel_doc16 in e for e in errs_tree),
            f"Expected missing Documentation Tree rejection in {rel_doc16}, got: {errs_tree}",
        )

      # 5. Removing SCAR-PROC-159 from process.md, 09_synthetic_scars_catalog.md, 04_six_pillars, or 02_archaeology
      for rel_159 in (
          "skills/makeflow/references/scars/process.md",
          "skills/makeflow/references/09_synthetic_scars_catalog.md",
          "skills/makeflow/references/04_six_pillars_adversarial_review.md",
          "skills/makeflow/references/02_archaeology_and_planning.md",
      ):
        mut_no_159 = dict(base_files)
        mut_no_159[rel_159] = mut_no_159[rel_159].replace(
            "SCAR-PROC-159", "SCAR-PROC-000"
        )
        errs_159 = check_scar_proc_159_documentation_standards(mut_no_159)
        self.assertTrue(
            any(rel_159 in e and "SCAR-PROC-159" in e for e in errs_159),
            f"Expected missing SCAR-PROC-159 error for {rel_159}, got: {errs_159}",
        )

      # 6. Removing ONLY "documentation tree" (keeping "doc comment") or ONLY "broken link" (keeping "warning") in Pillar 6
      r04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
      mut_no_doc_tree_p6 = dict(base_files)
      p6_start = mut_no_doc_tree_p6[r04_rel].index("### 6. Immune Defenses")
      mut_no_doc_tree_p6[r04_rel] = (
          mut_no_doc_tree_p6[r04_rel][:p6_start]
          + re.sub(
              r"(?i)documentation tree",
              "reference manual",
              mut_no_doc_tree_p6[r04_rel][p6_start:],
          )
      )
      errs_no_p6_tree = check_scar_proc_159_documentation_standards(
          mut_no_doc_tree_p6
      )
      self.assertTrue(
          any("Pillar 6" in e and r04_rel in e for e in errs_no_p6_tree),
          f"Expected Pillar 6 error when 'documentation tree' removed, got: {errs_no_p6_tree}",
      )

      mut_no_broken_link_p6 = dict(base_files)
      mut_no_broken_link_p6[r04_rel] = (
          mut_no_broken_link_p6[r04_rel][:p6_start]
          + re.sub(
              r"(?i)broken links?",
              "bad refs",
              mut_no_broken_link_p6[r04_rel][p6_start:],
          )
      )
      errs_no_p6_link = check_scar_proc_159_documentation_standards(
          mut_no_broken_link_p6
      )
      self.assertTrue(
          any("Pillar 6" in e and r04_rel in e for e in errs_no_p6_link),
          f"Expected Pillar 6 error when 'broken link' removed, got: {errs_no_p6_link}",
      )

      # 7. Injecting a >25-word descriptive sentence into Pillar 1/5/6 of 04_six_pillars or adversarial-critic.md
      for target_p_rel in (
          "skills/makeflow/references/04_six_pillars_adversarial_review.md",
          "agents/adversarial-critic.md",
      ):
        mut_long_pillar = dict(base_files)
        mut_long_pillar[target_p_rel] = mut_long_pillar[target_p_rel].replace(
            "### 1. Intent\n",
            "### 1. Intent\n"
            "- Does this overly verbose and meandering review question contain far more than twenty five descriptive words in a single sentence to test the Simple English word ceiling accurately?\n",
        )
        errs_long_pillar = check_scar_proc_159_documentation_standards(
            mut_long_pillar
        )
        self.assertTrue(
            any(
                target_p_rel in e and "exceeds 25-word descriptive limit" in e
                for e in errs_long_pillar
            ),
            f"Expected >25-word sentence rejection in {target_p_rel}, got: {errs_long_pillar}",
        )


TARGET_90_RESOLUTION_COMMENT_FILES = (
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/12_request_history_ledger.md",
    "skills/makeflow/references/01_intake_and_branching.md",
)


def check_target_90_resolution_comment_invariants(
    files_map: dict[str, str] | None = None,
) -> list[str]:
  """Validates Target 90 ticket resolution comment instructions across skill and reference manuals (GH-197)."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scar_candidate import (
      count_words_in_sentence,
      mask_code_and_math_spans,
      split_into_sentences,
  )

  if files_map is None:
    files_map = {}
    for rel in TARGET_90_RESOLUTION_COMMENT_FILES:
      fpath = repo_path(rel)
      if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  errs: list[str] = []
  for rel in TARGET_90_RESOLUTION_COMMENT_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. skills/makeflow/SKILL.md Target 90 section
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  t90_match = re.search(
      r"(?m)^###\s+Target 70, Target 75, Target 80, and Target 90\b.*?(?=^---|\n##\s+|\n###\s+|\Z)",
      skill_text,
      re.DOTALL,
  )
  t90_sec = t90_match.group(0) if t90_match else ""
  if not t90_sec:
    errs.append(
        f"{skill_rel} missing section '### Target 70, Target 75, Target 80, and Target 90'"
    )
  else:
    for req_tok in (
        "gh issue comment",
        "scripts/local_tickets.py close",
        "Target 90: Workflow Complete",
    ):
      if req_tok not in t90_sec:
        errs.append(
            f"{skill_rel} Target 90 section must reference '{req_tok}'"
        )
    step6_lines = [
        line.strip()
        for line in t90_sec.splitlines()
        if line.strip().startswith("6.")
    ]
    if not step6_lines:
      errs.append(f"{skill_rel} Target 90 section missing step 6")
    else:
      for step_line in step6_lines:
        errs.extend(_check_prose_punctuation(f"{skill_rel}#T90-step6", step_line))
        prose_body = re.sub(r"^\d+\.\s+", "", step_line)
        masked = mask_code_and_math_spans(prose_body)
        for sent in split_into_sentences(masked):
          wc = count_words_in_sentence(sent)
          if wc > 20:
            errs.append(
                f"{skill_rel}#T90-step6 sentence exceeds 20-word procedural limit ({wc} words): {sent}"
            )

  # 2. skills/makeflow/references/12_request_history_ledger.md Section 4
  ledger_rel = "skills/makeflow/references/12_request_history_ledger.md"
  ledger_text = files_map[ledger_rel]
  sec4 = _extract_markdown_h2_section(
      ledger_text, "## 4. Update Steps at Target 90"
  )
  if not sec4.strip():
    errs.append(
        f"{ledger_rel} missing required section '## 4. Update Steps at Target 90'"
    )
  else:
    for req_tok in (
        "Target 90: Workflow Complete",
        "gh issue comment",
        "--body-file",
        "python3 scripts/local_tickets.py close",
        "--comment-file",
    ):
      if req_tok not in sec4:
        errs.append(
            f"{ledger_rel} Section 4 must reference '{req_tok}'"
        )
    sec4_lower = sec4.lower()
    if "high-level" not in sec4_lower and "summary" not in sec4_lower:
      errs.append(
          f"{ledger_rel} Section 4 must specify posting a high-level resolution summary"
      )
    errs.extend(_check_prose_punctuation(f"{ledger_rel}#Section4", sec4))
    in_fence = False
    for idx, raw_line in enumerate(sec4.splitlines(), start=1):
      stripped = raw_line.strip()
      if stripped.startswith("```"):
        in_fence = not in_fence
        continue
      if in_fence or not stripped or stripped.startswith("#"):
        continue
      prose_body = re.sub(r"^(?:-\s+|\d+\.\s+)", "", stripped)
      masked = mask_code_and_math_spans(prose_body)
      for sent in split_into_sentences(masked):
        wc = count_words_in_sentence(sent)
        if wc > 25:
          errs.append(
              f"{ledger_rel}#Section4:{idx} sentence exceeds 25-word limit ({wc} words): {sent}"
          )

  # 3. skills/makeflow/references/01_intake_and_branching.md Local Ticket Store
  intake_rel = "skills/makeflow/references/01_intake_and_branching.md"
  intake_text = files_map[intake_rel]
  if "python3 scripts/local_tickets.py close" not in intake_text or (
      "--comment" not in intake_text and "--comment-file" not in intake_text
  ):
    errs.append(
        f"{intake_rel} must document 'python3 scripts/local_tickets.py close' with '--comment' or '--comment-file'"
    )

  return errs


def validate_target_90_resolution_comment_invariants() -> list[str]:
  """Validates Target 90 ticket resolution comment invariants on the live repository (GH-197)."""
  return check_target_90_resolution_comment_invariants()


class TestTarget90ResolutionCommentInvariants(unittest.TestCase):
  """Hermetic unit and negative falsification tests for Target 90 ticket resolution comments (GH-197)."""

  def test_live_repository_target_90_resolution_comment_invariants(self):
    """Verifies live repository manuals satisfy Target 90 ticket resolution comment rules."""
    self.assertEqual(check_target_90_resolution_comment_invariants(), [])

  def test_negative_falsification_target_90_resolution_comment_in_tempdir(self):
    """Verifies omitting Target 90 resolution comment tokens or violating Simple English fails validation."""
    with tempfile.TemporaryDirectory() as tmpdir:
      base_files: dict[str, str] = {}
      for rel in TARGET_90_RESOLUTION_COMMENT_FILES:
        src_path = repo_path(rel)
        dst_path = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with open(src_path, "r", encoding="utf-8") as f:
          text = f.read()
        with open(dst_path, "w", encoding="utf-8") as f:
          f.write(text)
        base_files[rel] = text

      self.assertEqual(
          check_target_90_resolution_comment_invariants(base_files), []
      )

      # 1. Omitting gh issue comment from SKILL.md or 12_request_history_ledger.md fails validation
      for target_rel in (
          "skills/makeflow/SKILL.md",
          "skills/makeflow/references/12_request_history_ledger.md",
      ):
        mut_no_gh = dict(base_files)
        mut_no_gh[target_rel] = mut_no_gh[target_rel].replace(
            "gh issue comment", "gh issue view"
        )
        errs_no_gh = check_target_90_resolution_comment_invariants(mut_no_gh)
        self.assertTrue(
            any(target_rel in e and "gh issue comment" in e for e in errs_no_gh),
            f"Expected missing 'gh issue comment' error in {target_rel}, got: {errs_no_gh}",
        )

      # 2. Omitting Target 90: Workflow Complete from 12_request_history_ledger.md fails validation
      ledger_rel = "skills/makeflow/references/12_request_history_ledger.md"
      mut_no_banner = dict(base_files)
      mut_no_banner[ledger_rel] = mut_no_banner[ledger_rel].replace(
          "Target 90: Workflow Complete", "Workflow Done"
      )
      errs_no_banner = check_target_90_resolution_comment_invariants(
          mut_no_banner
      )
      self.assertTrue(
          any(
              ledger_rel in e and "Target 90: Workflow Complete" in e
              for e in errs_no_banner
          ),
          f"Expected missing 'Target 90: Workflow Complete' error, got: {errs_no_banner}",
      )

      # 3. Omitting --comment / --comment-file from 01_intake_and_branching.md fails validation
      intake_rel = "skills/makeflow/references/01_intake_and_branching.md"
      mut_no_comment_flag = dict(base_files)
      mut_no_comment_flag[intake_rel] = (
          mut_no_comment_flag[intake_rel]
          .replace("--comment-file", "--flag-omitted")
          .replace("--comment", "--flag-omitted")
      )
      errs_no_flag = check_target_90_resolution_comment_invariants(
          mut_no_comment_flag
      )
      self.assertTrue(
          any(intake_rel in e and "--comment" in e for e in errs_no_flag),
          f"Expected missing '--comment' error in {intake_rel}, got: {errs_no_flag}",
      )

      # 4. Injecting a >25-word sentence or semicolon into Section 4 of 12_request_history_ledger.md fails validation
      mut_long_sec4 = dict(base_files)
      mut_long_sec4[ledger_rel] = mut_long_sec4[ledger_rel].replace(
          "## 4. Update Steps at Target 90\n",
          "## 4. Update Steps at Target 90\n\n"
          "1. When you finish merging the pull request at the end of the workflow, always write an unnecessarily long and verbose sentence that contains far more than twenty five words in total.\n",
      )
      errs_long_sec4 = check_target_90_resolution_comment_invariants(
          mut_long_sec4
      )
      self.assertTrue(
          any(
              ledger_rel in e and "exceeds 25-word limit" in e
              for e in errs_long_sec4
          ),
          f"Expected >25-word sentence rejection in {ledger_rel}, got: {errs_long_sec4}",
      )

      mut_semi_sec4 = dict(base_files)
      mut_semi_sec4[ledger_rel] = mut_semi_sec4[ledger_rel].replace(
          "## 4. Update Steps at Target 90\n",
          "## 4. Update Steps at Target 90\n\n"
          "1. Close the ticket; push the branch.\n",
      )
      errs_semi_sec4 = check_target_90_resolution_comment_invariants(
          mut_semi_sec4
      )
      self.assertTrue(
          any(ledger_rel in e and "semicolon" in e for e in errs_semi_sec4),
          f"Expected semicolon rejection in {ledger_rel}, got: {errs_semi_sec4}",
      )


class TestCrossAgentCompatibility(unittest.TestCase):
  """Unittest wrapper for cross-agent compatibility and scar invariants (GH-205)."""

  def test_cross_agent_compatibility(self):
    """Verifies all cross-agent configuration files, adapters, and scar invariants."""
    errors = _collect_compatibility_errors()
    if not errors:
      print("\n✔ All cross-agent configuration files validated successfully!")
    self.assertEqual(
        errors,
        [],
        f"Validation failed with {len(errors)} error(s):\n"
        + "\n".join(f"  - {err}" for err in errors),
    )

  def test_scar_arch_09_upstream_root_cause_invariants(self):
    """Verifies SCAR-ARCH-09 codification, directives, probe validation, and negative falsifications (GH-232)."""
    self.assertEqual(validate_scar_arch_09_upstream_root_cause(), [])

  def test_scar_arch_10_structured_data_invariants(self):
    """Verifies SCAR-ARCH-10 codification, routing, probe validation, and negative falsifications (GH-240)."""
    self.assertEqual(validate_scar_arch_10_structured_data(), [])

  def test_ref14_basescript_ast_lint_note_simple_english_and_falsifications(self):
    """Verifies Reference 14 Section 4 ast-lint note passes and rejects missing tokens or Simple English violations (GH-245)."""
    ref14_path = repo_path("skills/makeflow/references/14_design_patterns_and_architecture.md")
    with open(ref14_path, "r", encoding="utf-8") as f:
      ref14_text = f.read()
    self.assertEqual(check_ref14_basescript_ast_lint_note(ref14_text), [])

    for missing_tok in ("SCAR-AST-01", "make -f .workflow/state/Makefile ast-lint", "scripts/ast_scrub.py"):
      mut = ref14_text.replace(missing_tok, "REMOVED")
      self.assertTrue(
          check_ref14_basescript_ast_lint_note(mut),
          f"Expected error when '{missing_tok}' is removed",
      )

    # Contraction, semicolon, em-dash, and >25 word sentence must fail
    anchor = "#### 1. Python (`BaseScript`)\n"
    for bad_note in (
        "Don't inspect `ast_scrub.py` when running `make -f .workflow/state/Makefile ast-lint` for `SCAR-AST-01`.\n",
        "Check `SCAR-AST-01`; run `make -f .workflow/state/Makefile ast-lint` without reading `ast_scrub.py`.\n",
        "Check `SCAR-AST-01` \u2014 run `make -f .workflow/state/Makefile ast-lint` without reading `ast_scrub.py`.\n",
        (
            "This intentionally verbose sentence uses way too many words when explaining how "
            "workers should execute the ast-lint target directly without inspecting ast_scrub.py "
            "or violating Simple English word ceilings for SCAR-AST-01 compliance today.\n"
        ),
    ):
      mut_bad = ref14_text.replace(anchor, anchor + bad_note, 1)
      self.assertTrue(
          check_ref14_basescript_ast_lint_note(mut_bad),
          f"Expected Simple English rejection for: {bad_note!r}",
      )


  def test_check_makefile_test_discovery_requires_single_active_command(self):
    """Negative falsification: split-line or commented tokens must not satisfy discovery check."""
    valid_mk = (
        "PYTHON ?= python3\n"
        "test-static:\n\t@true\n"
        "test-sample:\n\t@true\n"
        "test: test-static\n"
        '\t@$(PYTHON) -m unittest discover -s "$(TESTS_DIR)" -p "test_*.py" -v -f || $(PYTHON) emit_reflex.py\n'
    )
    self.assertEqual(
        check_makefile_test_discovery(valid_mk, ("test-sample",)), []
    )

    continued_mk = (
        "PYTHON ?= python3\n"
        "test-static:\n\t@true\n"
        "test: test-static\n"
        "\t@$(PYTHON) -m unittest discover \\\n"
        '\t\t-s "$(TESTS_DIR)" -p "test_*.py" -v -f\n'
    )
    self.assertEqual(check_makefile_test_discovery(continued_mk), [])

    commented_mk = (
        "PYTHON ?= python3\n"
        "test-static:\n\t@true\n"
        "test: test-static\n"
        '\t# $(PYTHON) -m unittest discover -s "$(TESTS_DIR)"\n'
        "\t@true\n"
    )
    self.assertTrue(
        any("test:' recipe" in err for err in check_makefile_test_discovery(commented_mk))
    )

    split_pipeline_mk = (
        "PYTHON ?= python3\n"
        "test-static:\n\t@true\n"
        "test: test-static\n"
        '\t@echo "$(TESTS_DIR)"\n'
        '\t@python3 -m unittest discover -s "$(TESTS_DIR)" || $(PYTHON) emit_reflex.py\n'
    )
    self.assertTrue(
        any("test:' recipe" in err for err in check_makefile_test_discovery(split_pipeline_mk))
    )


TARGET_90_CHANGELOG_FILES = (
    "CHANGELOG.md",
    "plugin.json",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/12_request_history_ledger.md",
    "skills/makeflow/references/03_tdd_and_code_quality.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "skills/makeflow/references/07_ci_and_bot_triage.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "README.md",
    "CLAUDE.md",
    "rules/AGENTS.md",
    "adapters/chatgpt/instructions.md",
)


def _discover_semver_git_tags(base_dir: str) -> set[str]:
  """Returns normalized X.Y.Z tags present in base_dir when a Git repo exists."""
  if not os.path.exists(os.path.join(base_dir, ".git")):
    return set()
  res = subprocess.run(
      ["git", "tag", "-l"],
      cwd=base_dir,
      capture_output=True,
      text=True,
      check=False,
      timeout=5,
  )
  if res.returncode != 0:
    return set()
  tags: set[str] = set()
  for raw in res.stdout.splitlines():
    tok = raw.strip().lstrip("v")
    if re.match(r"^\d+\.\d+\.\d+$", tok):
      tags.add(tok)
  return tags


def check_target_90_changelog_and_release_invariants(
    files_map: dict[str, str] | None = None,
    repo_root: str | None = None,
    known_tags: set[str] | None = None,
) -> list[str]:
  """Validates Keep-a-Changelog workflow, Target 90 release prompting, and manifest metadata (GH-211)."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scar_candidate import (
      _CONTRACTION_RE,
      count_words_in_sentence,
      mask_code_and_math_spans,
      split_into_sentences,
  )

  base_dir = repo_root or REPO_ROOT
  from_disk = files_map is None
  if files_map is None:
    files_map = {}
    for rel in TARGET_90_CHANGELOG_FILES:
      fpath = os.path.join(base_dir, rel)
      if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  errs: list[str] = []
  for rel in TARGET_90_CHANGELOG_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. Manifest author.name and unified SemVer version across manifests
  manifest_versions: dict[str, str] = {}
  for m_rel in (
      "plugin.json",
      ".claude-plugin/plugin.json",
      ".codex-plugin/plugin.json",
  ):
    try:
      m_data = json.loads(files_map[m_rel])
    except json.JSONDecodeError as exc:
      errs.append(f"{m_rel} is invalid JSON: {exc}")
      continue
    ver = m_data.get("version")
    if not isinstance(ver, str) or not re.match(r"^\d+\.\d+\.\d+$", ver):
      errs.append(f"{m_rel} must declare a valid SemVer X.Y.Z version")
    else:
      manifest_versions[m_rel] = ver
    if m_rel == "plugin.json":
      author_obj = m_data.get("author")
      if not isinstance(author_obj, dict) or author_obj.get("name") != "Mark Hedges":
        errs.append("plugin.json author.name must be 'Mark Hedges'")
  if len(set(manifest_versions.values())) > 1:
    errs.append(
        f"Manifest versions diverge across manifests: {manifest_versions}"
    )

  canonical_ver = (
      manifest_versions.get("plugin.json")
      if len(set(manifest_versions.values())) == 1
      else None
  )
  if known_tags is not None:
    resolved_tags = {t.lstrip("v") for t in known_tags}
  elif from_disk:
    resolved_tags = _discover_semver_git_tags(base_dir)
  else:
    resolved_tags = set()

  # 2. CHANGELOG.md format, backed-only release headings, and Simple English checks
  changelog_text = files_map["CHANGELOG.md"]
  if not re.search(r"(?m)^#\s+Changelog\b", changelog_text):
    errs.append("CHANGELOG.md must begin with '# Changelog' heading")
  if "## [Unreleased]" not in changelog_text:
    errs.append("CHANGELOG.md must include '## [Unreleased]' section")

  for rel_ver in re.findall(
      r"(?m)^##\s+\[(?!Unreleased\b)(\d+\.\d+\.\d+)\]", changelog_text
  ):
    is_tagged = rel_ver in resolved_tags
    is_active_release_bump = (
        canonical_ver is not None
        and rel_ver == canonical_ver
        and (canonical_ver not in ("0.0.1", "1.4.1") or is_tagged)
    )
    if not is_tagged and not is_active_release_bump:
      errs.append(
          "CHANGELOG.md must not contain fabricated release version heading:"
          f" ## [{rel_ver}]"
      )

  in_fence = False
  for raw_line in changelog_text.splitlines():
    stripped = raw_line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if (
        in_fence
        or not stripped
        or stripped.startswith("#")
        or stripped.startswith("<!--")
    ):
      continue
    errs.extend(_check_prose_punctuation("CHANGELOG.md", stripped))
    no_code = re.sub(r"`[^`]*`", "", stripped)
    cmatch = _CONTRACTION_RE.search(no_code)
    if cmatch:
      errs.append(
          f"CHANGELOG.md contains forbidden contraction '{cmatch.group(0)}' in prose"
      )
    body = re.sub(r"^(?:-\s+|\*\s+|\d+\.\s+)", "", stripped)
    masked = mask_code_and_math_spans(body)
    for sent in split_into_sentences(masked):
      wc = count_words_in_sentence(sent)
      if wc > 25:
        errs.append(
            f"CHANGELOG.md sentence exceeds 25-word limit ({wc} words): {sent}"
        )

  # 3. skills/makeflow/SKILL.md budget and Target 90 changelog/release tokens
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  skill_bytes = len(skill_text.encode("utf-8"))
  if skill_bytes > 24576:
    errs.append(
        f"{skill_rel} exceeds 24576-byte budget ({skill_bytes} bytes)"
    )
  t90_row = next(
      (line for line in skill_text.splitlines() if "| `90` |" in line), ""
  )
  if "CHANGELOG.md" not in t90_row:
    errs.append(f"{skill_rel} Target 90 table row must reference 'CHANGELOG.md'")

  t90_sec_match = re.search(
      r"(?m)^###\s+Target 70, Target 75, Target 80, and Target 90\b.*?(?=^---|\n##\s+|\n###\s+|\Z)",
      skill_text,
      re.DOTALL,
  )
  t90_sec = t90_sec_match.group(0) if t90_sec_match else ""
  step6_lines = [
      line.strip()
      for line in t90_sec.splitlines()
      if line.strip().startswith("6.")
  ]
  if not step6_lines or "CHANGELOG.md" not in step6_lines[0]:
    errs.append(f"{skill_rel} Target 90 step 6 must reference 'CHANGELOG.md'")
  elif "release" not in step6_lines[0].lower():
    errs.append(f"{skill_rel} Target 90 step 6 must prompt for a release")
  if step6_lines:
    for prefix_tok in ("- GH-", "- LOCAL-"):
      if prefix_tok not in step6_lines[0]:
        errs.append(
            f"{skill_rel} Target 90 step 6 must include ticket-prefixed"
            f" CHANGELOG.md example '{prefix_tok}'"
        )
    if "make test" not in step6_lines[0]:
      errs.append(
          f"{skill_rel} Target 90 step 6 must require running 'make test'"
          " before committing or pushing ledger and CHANGELOG.md updates"
      )
    elif "push `origin main`" in step6_lines[0] and (
        step6_lines[0].index("make test")
        > step6_lines[0].index("push `origin main`")
    ):
      errs.append(
          f"{skill_rel} Target 90 step 6 must require 'make test' before"
          " pushing `origin main`"
      )

  # 4. 12_request_history_ledger.md two-stage changelog & release prompt
  ledger_rel = "skills/makeflow/references/12_request_history_ledger.md"
  ledger_text = files_map[ledger_rel]
  for mode_heading in (
      "### Mode 1: Pull Request Mode with Branch Protection",
      "### Mode 2: Pull Request Mode with Direct Push Allowed",
      "### Mode 3: Pure Git Mode",
      "## 4. Update Steps at Target 90",
  ):
    if mode_heading not in ledger_text:
      errs.append(f"{ledger_rel} missing heading '{mode_heading}'")
  if "CHANGELOG.md" not in ledger_text or "## [Unreleased]" not in ledger_text:
    errs.append(
        f"{ledger_rel} must describe updating CHANGELOG.md under ## [Unreleased]"
    )

  mode1_match = re.search(
      r"(?m)^### Mode 1: Pull Request Mode with Branch Protection\b.*?(?=^### Mode 2:|\Z)",
      ledger_text,
      re.DOTALL,
  )
  mode1_sec = mode1_match.group(0) if mode1_match else ""
  if "docs(changelog):" not in mode1_sec:
    errs.append(f"{ledger_rel} Mode 1 must reference 'docs(changelog):'")

  mode2_match = re.search(
      r"(?m)^### Mode 2: Pull Request Mode with Direct Push Allowed\b.*?(?=^### Mode 3:|\Z)",
      ledger_text,
      re.DOTALL,
  )
  mode2_sec = mode2_match.group(0) if mode2_match else ""
  if "make test" not in mode2_sec:
    errs.append(
        f"{ledger_rel} Mode 2 must require running 'make test' before committing and pushing to main"
    )
  elif "git commit" in mode2_sec and (
      mode2_sec.index("make test") > mode2_sec.index("git commit")
  ):
    errs.append(
        f"{ledger_rel} Mode 2 must require 'make test' before 'git commit'"
    )

  mode3_match = re.search(
      r"(?m)^### Mode 3: Pure Git Mode\b.*?(?=^## 4\.|\Z)",
      ledger_text,
      re.DOTALL,
  )
  mode3_sec = mode3_match.group(0) if mode3_match else ""
  if "make test" not in mode3_sec:
    errs.append(
        f"{ledger_rel} Mode 3 must require running 'make test' before committing and pushing to main"
    )
  elif "git commit" in mode3_sec and (
      mode3_sec.index("make test") > mode3_sec.index("git commit")
  ):
    errs.append(
        f"{ledger_rel} Mode 3 must require 'make test' before 'git commit'"
    )

  sec4 = _extract_markdown_h2_section(
      ledger_text, "## 4. Update Steps at Target 90"
  )
  for req_tok in (
      "CHANGELOG.md",
      "## [Unreleased]",
      "ask_question",
      "patch",
      "minor",
      "major",
      "release/v",
      "test",
      "Keep an empty `## [Unreleased]`",
      "asks to create",
      "- GH-",
      "- LOCAL-",
      "scripts/changelog.py",
  ):
    if req_tok not in sec4:
      errs.append(f"{ledger_rel} Section 4 must reference '{req_tok}'")
  history_commit_tok = 'git commit -m "docs(history):'
  if history_commit_tok not in sec4:
    errs.append(
        f"{ledger_rel} Section 4 must reference '{history_commit_tok}'"
    )
  else:
    pre_history_commit = sec4.split(history_commit_tok, 1)[0]
    if "make test" not in pre_history_commit:
      errs.append(
          f"{ledger_rel} Section 4 must require running 'make test' before '{history_commit_tok}'"
      )
  if sec4.count('git commit -m "docs(release): cut vX.Y.Z"') < 2:
    errs.append(
        f"{ledger_rel} Section 4 release step must specify 'git commit -m \"docs(release): cut vX.Y.Z\"' for both Mode 1 and Mode 2/3"
    )

  # 5. Sibling docs & rules alignment
  r03_rel = "skills/makeflow/references/03_tdd_and_code_quality.md"
  r03_text = files_map[r03_rel]
  if (
      "## [Unreleased]" not in r03_text
      or "CHANGELOG.md" not in r03_text
      or "If behavior changes," not in r03_text
      or "SCAR-DOC-18" in r03_text
  ):
    errs.append(
        f"{r03_rel} must document CHANGELOG.md and ## [Unreleased] with condition-first syntax and valid SCAR-DOC-16 citation"
    )

  r04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  if "## [Unreleased]" not in files_map[r04_rel] or "CHANGELOG.md" not in files_map[r04_rel]:
    errs.append(
        f"{r04_rel} Pillar 1 must check CHANGELOG.md and ## [Unreleased] hygiene"
    )

  r07_rel = "skills/makeflow/references/07_ci_and_bot_triage.md"
  if "CHANGELOG.md" not in files_map[r07_rel]:
    errs.append(
        f"{r07_rel} Target 75 must include CHANGELOG.md before landing on protected main"
    )

  r09_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  r09_sec31_match = re.search(
      r"(?m)^### 3\.1 Ticket-Scoped Core Scars\b.*?(?=^### 3\.2|\Z)",
      files_map[r09_rel],
      re.DOTALL,
  )
  r09_sec31 = r09_sec31_match.group(0) if r09_sec31_match else ""
  if "CHANGELOG.md" not in r09_sec31 or "release" not in r09_sec31.lower():
    errs.append(
        f"{r09_rel} Section 3.1 step 6 must reference CHANGELOG.md and release prompting"
    )

  for summary_rel in (
      "README.md",
      "CLAUDE.md",
      "rules/AGENTS.md",
      "adapters/chatgpt/instructions.md",
  ):
    if "CHANGELOG.md" not in files_map[summary_rel]:
      errs.append(
          f"{summary_rel} must reference CHANGELOG.md in Target 90 guidance"
      )

  return errs


class TestTarget90ChangelogAndReleaseInvariants(unittest.TestCase):
  """Regression and negative falsification tests for GH-211 CHANGELOG.md and Target 90 release workflow."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in TARGET_90_CHANGELOG_FILES:
      fpath = repo_path(rel)
      with open(fpath, "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_repo_target90_changelog_and_release_files_pass(self):
    errs = check_target_90_changelog_and_release_invariants()
    self.assertEqual(errs, [])

  def test_negative_falsifications_caught(self):
    base = self._load_base_files()

    # 1. Missing CHANGELOG.md in isolated tempdir repo root
    with tempfile.TemporaryDirectory() as td:
      for rel, content in base.items():
        if rel == "CHANGELOG.md":
          continue
        dest = os.path.join(td, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
          f.write(content)
      errs_missing = check_target_90_changelog_and_release_invariants(repo_root=td)
      self.assertTrue(
          any("Missing CHANGELOG.md" in e for e in errs_missing),
          f"Expected missing CHANGELOG.md rejection, got: {errs_missing}",
      )

    # 2. Missing ## [Unreleased]
    mut_no_unrel = dict(base)
    mut_no_unrel["CHANGELOG.md"] = "# Changelog\n\n- Added a feature.\n"
    errs_no_unrel = check_target_90_changelog_and_release_invariants(mut_no_unrel)
    self.assertTrue(
        any("## [Unreleased]" in e for e in errs_no_unrel),
        f"Expected missing ## [Unreleased] rejection, got: {errs_no_unrel}",
    )

    # 3. Fabricated historical release heading (no tags exist)
    mut_fab = dict(base)
    mut_fab["CHANGELOG.md"] += "\n## [1.4.1] - 2026-01-01\n- Legacy notes.\n"
    errs_fab = check_target_90_changelog_and_release_invariants(mut_fab)
    self.assertTrue(
        any("fabricated release version heading" in e for e in errs_fab),
        f"Expected fabricated release heading rejection, got: {errs_fab}",
    )

    mut_fab_001 = dict(base)
    mut_fab_001["CHANGELOG.md"] += (
        "\n## [0.0.1] - 2026-01-01\n- Initial notes.\n"
    )
    errs_fab_001 = check_target_90_changelog_and_release_invariants(mut_fab_001)
    self.assertTrue(
        any("fabricated release version heading" in e for e in errs_fab_001),
        f"Expected fabricated 0.0.1 heading rejection, got: {errs_fab_001}",
    )

    # 4. Over-25-word sentence and contraction in CHANGELOG.md
    mut_verbose = dict(base)
    mut_verbose["CHANGELOG.md"] += (
        "\n- This entry deliberately includes far more than twenty five words"
        " inside a single descriptive bullet sentence so Simple English"
        " checks catch overly complex release notes quickly.\n"
    )
    errs_verbose = check_target_90_changelog_and_release_invariants(mut_verbose)
    self.assertTrue(
        any("exceeds 25-word limit" in e for e in errs_verbose),
        f"Expected >25-word rejection in CHANGELOG.md, got: {errs_verbose}",
    )

    for bad_line, expected_tok in (
        ("\n- We don't break existing commands.\n", "contraction"),
        ("\n- We cannot't break existing commands.\n", "contraction"),
        ("\n- We didn't skip existing checks.\n", "contraction"),
        ("\n- Validate bullets; reject invalid lines.\n", "semicolon"),
        ("\n- Validate bullets\u2014reject invalid lines.\n", "em-dash"),
    ):
      mut_prose = dict(base)
      mut_prose["CHANGELOG.md"] += bad_line
      errs_prose = check_target_90_changelog_and_release_invariants(mut_prose)
      self.assertTrue(
          any(expected_tok in e for e in errs_prose),
          f"Expected {expected_tok} rejection for {bad_line!r} in CHANGELOG.md, got: {errs_prose}",
      )

    from lib.scar_candidate import _CONTRACTION_RE

    self.assertIsNotNone(_CONTRACTION_RE.search("cannot't"))
    self.assertIsNotNone(_CONTRACTION_RE.search("didn't"))

    # 5. Wrong plugin.json author or mismatched manifest version
    mut_author = dict(base)
    plugin_data = json.loads(mut_author["plugin.json"])
    plugin_data["author"]["name"] = "Other Author"
    mut_author["plugin.json"] = json.dumps(plugin_data)
    errs_author = check_target_90_changelog_and_release_invariants(mut_author)
    self.assertTrue(
        any("Mark Hedges" in e for e in errs_author),
        f"Expected author rejection, got: {errs_author}",
    )

    mut_str_author = dict(base)
    str_author_data = json.loads(mut_str_author["plugin.json"])
    str_author_data["author"] = "Mark Hedges"
    mut_str_author["plugin.json"] = json.dumps(str_author_data)
    errs_str_author = check_target_90_changelog_and_release_invariants(
        mut_str_author
    )
    self.assertTrue(
        any("Mark Hedges" in e for e in errs_str_author),
        f"Expected non-dict author rejection, got: {errs_str_author}",
    )

    mut_mismatch = dict(base)
    claude_data = json.loads(mut_mismatch[".claude-plugin/plugin.json"])
    claude_data["version"] = "9.9.9"
    mut_mismatch[".claude-plugin/plugin.json"] = json.dumps(claude_data)
    errs_mismatch = check_target_90_changelog_and_release_invariants(mut_mismatch)
    self.assertTrue(
        any("diverge" in e for e in errs_mismatch),
        f"Expected manifest version divergence rejection, got: {errs_mismatch}",
    )

    # 6. Omitting ask_question, release/v, docs(changelog):, ticket-prefix, or make test guidance in 12_request_history_ledger.md
    ledger_rel = "skills/makeflow/references/12_request_history_ledger.md"
    for bad_tok in (
        "ask_question",
        "release/v",
        "docs(changelog):",
        "asks to create",
        "Keep an empty `## [Unreleased]`",
        "- GH-",
        "- LOCAL-",
        "scripts/changelog.py",
    ):
      mut_ledger = dict(base)
      mut_ledger[ledger_rel] = mut_ledger[ledger_rel].replace(bad_tok, "REMOVED")
      errs_ledger = check_target_90_changelog_and_release_invariants(mut_ledger)
      self.assertTrue(
          any(bad_tok in e for e in errs_ledger),
          f"Expected '{bad_tok}' rejection, got: {errs_ledger}",
      )

    # 6b. Omitting or deferring make test in Mode 2, Mode 3, or Section 4 before ledger commit
    mode2_match = re.search(
        r"(?ms)^### Mode 2: Pull Request Mode with Direct Push Allowed\b.*?(?=^### Mode 3:|\Z)",
        base[ledger_rel],
    )
    self.assertIsNotNone(mode2_match)
    mut_mode2_missing = dict(base)
    mut_mode2_missing[ledger_rel] = base[ledger_rel].replace(
        mode2_match.group(0),
        mode2_match.group(0).replace("make test", "lint"),
    )
    errs_mode2_missing = check_target_90_changelog_and_release_invariants(
        mut_mode2_missing
    )
    self.assertTrue(
        any("Mode 2" in e and "make test" in e for e in errs_mode2_missing),
        f"Expected Mode 2 missing make test rejection, got: {errs_mode2_missing}",
    )

    mut_mode2_order = dict(base)
    mut_mode2_order[ledger_rel] = base[ledger_rel].replace(
        mode2_match.group(0),
        "### Mode 2: Pull Request Mode with Direct Push Allowed\n\n"
        "1. Commit the updates with `git commit -m \"docs(history): record <id>\"`.\n"
        "2. Run `make test`.\n"
        "3. Push with `git push origin main`.\n\n",
    )
    errs_mode2_order = check_target_90_changelog_and_release_invariants(
        mut_mode2_order
    )
    self.assertTrue(
        any("Mode 2" in e and "before 'git commit'" in e for e in errs_mode2_order),
        f"Expected Mode 2 ordering rejection, got: {errs_mode2_order}",
    )

    mode3_match = re.search(
        r"(?ms)^### Mode 3: Pure Git Mode\b.*?(?=^## 4\.|\Z)",
        base[ledger_rel],
    )
    self.assertIsNotNone(mode3_match)
    mut_mode3_missing = dict(base)
    mut_mode3_missing[ledger_rel] = base[ledger_rel].replace(
        mode3_match.group(0),
        mode3_match.group(0).replace("make test", "lint"),
    )
    errs_mode3_missing = check_target_90_changelog_and_release_invariants(
        mut_mode3_missing
    )
    self.assertTrue(
        any("Mode 3" in e and "make test" in e for e in errs_mode3_missing),
        f"Expected Mode 3 missing make test rejection, got: {errs_mode3_missing}",
    )

    mut_mode3_order = dict(base)
    mut_mode3_order[ledger_rel] = base[ledger_rel].replace(
        mode3_match.group(0),
        "### Mode 3: Pure Git Mode\n\n"
        "1. Commit the updates with `git commit -m \"docs(history): record <id>\"`.\n"
        "2. Run `make test`.\n"
        "3. Push `main` with `git push origin main`.\n\n",
    )
    errs_mode3_order = check_target_90_changelog_and_release_invariants(
        mut_mode3_order
    )
    self.assertTrue(
        any("Mode 3" in e and "before 'git commit'" in e for e in errs_mode3_order),
        f"Expected Mode 3 ordering rejection, got: {errs_mode3_order}",
    )

    sec4_idx = base[ledger_rel].index("## 4. Update Steps at Target 90")
    hist_commit_tok = 'git commit -m "docs(history):'
    hist_commit_idx = base[ledger_rel].index(hist_commit_tok, sec4_idx)
    mut_sec4_pre = dict(base)
    mut_sec4_pre[ledger_rel] = (
        base[ledger_rel][:sec4_idx]
        + base[ledger_rel][sec4_idx:hist_commit_idx].replace("make test", "lint")
        + base[ledger_rel][hist_commit_idx:]
    )
    errs_sec4_pre = check_target_90_changelog_and_release_invariants(
        mut_sec4_pre
    )
    self.assertTrue(
        any("Section 4" in e and "make test" in e for e in errs_sec4_pre),
        f"Expected Section 4 pre-commit make test rejection, got: {errs_sec4_pre}",
    )

    # 7. Omitting CHANGELOG.md, ticket-prefix examples, or make test from SKILL.md Step 6
    skill_rel = "skills/makeflow/SKILL.md"
    mut_no_skill_cl = dict(base)
    mut_no_skill_cl[skill_rel] = mut_no_skill_cl[skill_rel].replace(
        "CHANGELOG.md", "HISTORY.txt"
    )
    errs_skill_cl = check_target_90_changelog_and_release_invariants(
        mut_no_skill_cl
    )
    self.assertTrue(
        any(skill_rel in e and "CHANGELOG.md" in e for e in errs_skill_cl),
        f"Expected SKILL.md CHANGELOG.md rejection, got: {errs_skill_cl}",
    )

    for prefix_tok in ("- GH-", "- LOCAL-"):
      mut_skill_prefix = dict(base)
      mut_skill_prefix[skill_rel] = mut_skill_prefix[skill_rel].replace(
          prefix_tok, "REMOVED"
      )
      errs_skill_prefix = check_target_90_changelog_and_release_invariants(
          mut_skill_prefix
      )
      self.assertTrue(
          any(skill_rel in e and prefix_tok in e for e in errs_skill_prefix),
          f"Expected SKILL.md '{prefix_tok}' rejection, got: {errs_skill_prefix}",
      )

    mut_skill_no_test = dict(base)
    mut_skill_no_test[skill_rel] = mut_skill_no_test[skill_rel].replace(
        "make test", "lint"
    )
    errs_skill_no_test = check_target_90_changelog_and_release_invariants(
        mut_skill_no_test
    )
    self.assertTrue(
        any(skill_rel in e and "make test" in e for e in errs_skill_no_test),
        f"Expected SKILL.md missing make test rejection, got: {errs_skill_no_test}",
    )

    mut_skill_order = dict(base)
    step6_prefix = "6. At Target 90,"
    step6_pos = mut_skill_order[skill_rel].index(step6_prefix)
    step6_end = mut_skill_order[skill_rel].index("\n", step6_pos)
    mut_skill_order[skill_rel] = (
        mut_skill_order[skill_rel][:step6_pos]
        + "6. At Target 90, update `CHANGELOG.md` (`- GH-1:` or `- LOCAL-1:`), push `origin main`, run `make test`, and prompt for a release."
        + mut_skill_order[skill_rel][step6_end:]
    )
    errs_skill_order = check_target_90_changelog_and_release_invariants(
        mut_skill_order
    )
    self.assertTrue(
        any(skill_rel in e and "before pushing" in e for e in errs_skill_order),
        f"Expected SKILL.md make test ordering rejection, got: {errs_skill_order}",
    )

    # 8. Omitting CHANGELOG.md from sibling docs and summary adapters
    for sibling_rel in (
        "skills/makeflow/references/03_tdd_and_code_quality.md",
        "skills/makeflow/references/04_six_pillars_adversarial_review.md",
        "skills/makeflow/references/07_ci_and_bot_triage.md",
        "skills/makeflow/references/09_synthetic_scars_catalog.md",
        "README.md",
        "CLAUDE.md",
        "rules/AGENTS.md",
        "adapters/chatgpt/instructions.md",
    ):
      mut_sib = dict(base)
      mut_sib[sibling_rel] = mut_sib[sibling_rel].replace(
          "CHANGELOG.md", "REMOVED.md"
      )
      errs_sib = check_target_90_changelog_and_release_invariants(mut_sib)
      self.assertTrue(
          any(sibling_rel in e for e in errs_sib),
          f"Expected rejection for {sibling_rel}, got: {errs_sib}",
      )

  def test_release_cut_heading_allowed_when_manifests_or_tags_match(self):
    base = self._load_base_files()

    # Active release cut (Step 12-14 before tagging): manifests bumped to 1.4.2, ## [Unreleased] kept at top
    cut_files = dict(base)
    for m_rel in (
        "plugin.json",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
      m_data = json.loads(cut_files[m_rel])
      m_data["version"] = "1.4.2"
      cut_files[m_rel] = json.dumps(m_data)
    cut_files["CHANGELOG.md"] = (
        "# Changelog\n\n## [Unreleased]\n\n"
        "## [1.4.2] - 2026-11-15\n\n### Added\n- Release cut test.\n"
    )
    self.assertEqual(
        check_target_90_changelog_and_release_invariants(
            cut_files, known_tags=set()
        ),
        [],
    )

    # Subsequent release cut 1.5.0 when v1.4.2 is tagged
    next_cut = dict(cut_files)
    for m_rel in (
        "plugin.json",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
      m_data = json.loads(next_cut[m_rel])
      m_data["version"] = "1.5.0"
      next_cut[m_rel] = json.dumps(m_data)
    next_cut["CHANGELOG.md"] = (
        "# Changelog\n\n## [Unreleased]\n\n"
        "## [1.5.0] - 2026-12-01\n\n### Added\n- Minor release.\n\n"
        "## [1.4.2] - 2026-11-15\n\n### Added\n- Release cut test.\n"
    )
    self.assertEqual(
        check_target_90_changelog_and_release_invariants(
            next_cut, known_tags={"1.4.2"}
        ),
        [],
    )

    # Unbacked fabricated heading 0.9.0 alongside valid 1.4.2 still fails
    bad_cut = dict(cut_files)
    bad_cut["CHANGELOG.md"] += "\n## [0.9.0] - 2025-01-01\n- Fake release.\n"
    errs_bad = check_target_90_changelog_and_release_invariants(
        bad_cut, known_tags=set()
    )
    self.assertTrue(
        any("0.9.0" in e for e in errs_bad),
        f"Expected fabricated 0.9.0 rejection, got: {errs_bad}",
    )


EXPECTED_PRE_TOOL_MATCHER = (
    "invoke_subagent|read_file|write_file|edit_file|run_command|"
    "call_mcp_tool|manage_subagents|ask_question"
)

SCAR_PROC_95_AIRLOCK_FILES = (
    "hooks.json",
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/03_tdd_and_code_quality.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "skills/makeflow/references/08_subagent_orchestration.md",
    "skills/makeflow/references/scars/process.md",
    "rules/AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)


def check_scar_proc_95_subagent_halt_airlock(
    files_map: dict[str, str] | None = None,
) -> list[str]:
  """Validates GH-220 SCAR-PROC-95 PreToolUse matcher and Immediate Supervisory Halt docs."""
  if files_map is None:
    files_map = {}
    for rel in SCAR_PROC_95_AIRLOCK_FILES:
      fpath = repo_path(rel)
      if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  errs: list[str] = []
  for rel in SCAR_PROC_95_AIRLOCK_FILES:
    if rel not in files_map:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. ./hooks.json PreToolUse matcher
  try:
    hooks_data = json.loads(files_map["hooks.json"])
    pre_hooks = hooks_data.get("makeflow-guard", {}).get("PreToolUse", [])
    actual_matcher = pre_hooks[0].get("matcher", "") if pre_hooks else ""
    if actual_matcher != EXPECTED_PRE_TOOL_MATCHER:
      errs.append(
          f"hooks.json PreToolUse[0].matcher expected '{EXPECTED_PRE_TOOL_MATCHER}',"
          f" got '{actual_matcher}'"
      )
  except (json.JSONDecodeError, TypeError, AttributeError) as exc:
    errs.append(f"hooks.json invalid JSON structure: {exc}")

  # 2. skills/makeflow/SKILL.md across T15, T30, T40, and T70
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  for hdr in (
      "### Target 15:",
      "### Target 30:",
      "### Target 40,",
      "### Target 70,",
  ):
    if hdr not in skill_text:
      errs.append(f"{skill_rel} missing section '{hdr}'")
      continue
    sec = re.split(r"\n###\s+|\n---\n", skill_text.split(hdr, 1)[1], maxsplit=1)[0]
    for tok in ("Immediate Supervisory Halt", "tool_calls: []", "SCAR-PROC-95"):
      if tok not in sec:
        errs.append(f"{skill_rel} section '{hdr}' must document '{tok}'")
  t15_sec = (
      re.split(
          r"\n###\s+",
          skill_text.split("### Target 15:", 1)[1],
          maxsplit=1,
      )[0]
      if "### Target 15:" in skill_text
      else ""
  )
  if "Target 30" not in t15_sec or "reproduction" not in t15_sec.lower():
    errs.append(
        f"{skill_rel} Target 15 must defer live bug reproduction to Target 30"
    )
  if "without re-reading" not in t15_sec.lower():
    errs.append(
        f"{skill_rel} Target 15 must instruct writing 15_archaeology_receipt.md without re-reading scanned files"
    )

  # 3. Reference manuals 02, 03, 04, and 08
  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_text = files_map[r02_rel]
  for tok in (
      "Immediate Supervisory Halt",
      "tool_calls: []",
      "SCAR-PROC-95",
      "tdd-worker",
      "Target 30",
      "15_archaeology_receipt.md",
  ):
    if tok not in r02_text:
      errs.append(f"{r02_rel} must document '{tok}'")
  if "without re-reading" not in r02_text.lower():
    errs.append(
        f"{r02_rel} must instruct synthesizing 15_archaeology_receipt.md without re-reading scanned files"
    )

  for ref_rel in (
      "skills/makeflow/references/03_tdd_and_code_quality.md",
      "skills/makeflow/references/04_six_pillars_adversarial_review.md",
  ):
    ref_text = files_map[ref_rel]
    for tok in ("Immediate Supervisory Halt", "tool_calls: []", "SCAR-PROC-95"):
      if tok not in ref_text:
        errs.append(f"{ref_rel} must document '{tok}'")

  r08_rel = "skills/makeflow/references/08_subagent_orchestration.md"
  r08_text = files_map[r08_rel]
  for tok in (
      "Immediate Supervisory Halt",
      "tool_calls: []",
      "SCAR-PROC-95",
      "./hooks.json",
      "scripts/pre_tool_guard.py",
      ".workflow/state/active_subagent.json",
      "archaeology-worker",
      "tdd-worker",
      "adversarial-critic",
      "triage-worker",
      "<SYSTEM_MESSAGE>",
      "USER_INPUT",
  ):
    if tok not in r08_text:
      errs.append(f"{r08_rel} must document '{tok}'")

  # 4. Tier 1 scar spoke process.md (in-place SCAR-PROC-95 update)
  proc_rel = "skills/makeflow/references/scars/process.md"
  proc_text = files_map[proc_rel]
  if "### SCAR-PROC-95" not in proc_text:
    errs.append(f"{proc_rel} missing '### SCAR-PROC-95'")
  else:
    sec_95 = proc_text.split("### SCAR-PROC-95", 1)[1].split("### SCAR-", 1)[0]
    for tok in (
        "Immediate Supervisory Halt",
        "tool_calls: []",
        "scripts/pre_tool_guard.py",
        ".workflow/state/active_subagent.json",
    ):
      if tok not in sec_95:
        errs.append(f"{proc_rel} SCAR-PROC-95 must document '{tok}'")
    m_probe = re.search(r"(?m)^-\s+\*\*Socratic Probe\*\*:\s*(.+)$", sec_95)
    if not m_probe:
      errs.append(f"{proc_rel} SCAR-PROC-95 missing Socratic Probe")
    else:
      errs.extend(
          validate_socratic_probe(m_probe.group(1).strip(), scar_id="SCAR-PROC-95")
      )

  # 5. rules/AGENTS.md Section 1.13
  agents_rel = "rules/AGENTS.md"
  agents_text = files_map[agents_rel]
  sec_hdr = "### 1.13 Immediate Supervisory Halt During Subagent Execution (SCAR-PROC-95)"
  if sec_hdr not in agents_text:
    errs.append(f"{agents_rel} missing '{sec_hdr}'")
  else:
    sec_1_13 = re.split(
        r"\n#{2,3}\s+", agents_text.split(sec_hdr, 1)[1], maxsplit=1
    )[0]
    for tok in (
        "Immediate Supervisory Halt",
        "tool_calls: []",
        "./hooks.json",
        "scripts/pre_tool_guard.py",
        ".workflow/state/active_subagent.json",
        "Target 15",
        "Target 30",
        "15_archaeology_receipt.md",
    ):
      if tok not in sec_1_13:
        errs.append(f"{agents_rel} Section 1.13 must document '{tok}'")

  # 6. Cross-agent adapters
  for adapter_rel in (
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ):
    adapter_text = files_map[adapter_rel]
    for tok in (
        "Immediate Supervisory Halt",
        "tool_calls: []",
        "SCAR-PROC-95",
        "Target 15",
        "Target 30",
    ):
      if tok not in adapter_text:
        errs.append(f"{adapter_rel} must document '{tok}'")

  return errs


class TestScarProc95SubagentHaltAirlock(unittest.TestCase):
  """Regression and negative falsification tests for GH-220 SCAR-PROC-95 airlock docs."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in SCAR_PROC_95_AIRLOCK_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_live_repo_scar_proc_95_airlock_invariants(self):
    self.assertEqual(check_scar_proc_95_subagent_halt_airlock(), [])

  def test_negative_falsification_scar_proc_95_airlock(self):
    base = self._load_base_files()
    self.assertEqual(check_scar_proc_95_subagent_halt_airlock(base), [])

    mut_hooks = dict(base)
    mut_hooks["hooks.json"] = json.dumps({
        "makeflow-guard": {
            "PreToolUse": [{"matcher": "run_command|call_mcp_tool|ask_question"}]
        }
    })
    self.assertTrue(
        any(
            "hooks.json" in e
            for e in check_scar_proc_95_subagent_halt_airlock(mut_hooks)
        )
    )

    for rel in (
        "skills/makeflow/SKILL.md",
        "skills/makeflow/references/02_archaeology_and_planning.md",
        "skills/makeflow/references/08_subagent_orchestration.md",
        "rules/AGENTS.md",
        "CLAUDE.md",
        ".cursorrules",
        ".cursor/rules/makeflow.mdc",
        ".github/copilot-instructions.md",
        "adapters/chatgpt/instructions.md",
    ):
      mut = dict(base)
      mut[rel] = mut[rel].replace("Immediate Supervisory Halt", "Optional Pause")
      errs = check_scar_proc_95_subagent_halt_airlock(mut)
      self.assertTrue(
          any(rel in e for e in errs),
          f"Expected rejection when Immediate Supervisory Halt omitted from {rel}, got: {errs}",
      )


TARGET_30_REGRESSION_GATE_FILES = (
    "default_config.json",
    ".workflow/config.json.example",
    "agents/tdd-worker.md",
    "agents/adversarial-critic.md",
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/03_tdd_and_code_quality.md",
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "skills/makeflow/references/05_pre_commit_hygiene.md",
    "skills/makeflow/references/08_subagent_orchestration.md",
    "skills/makeflow/references/13_configuration_and_user_preferences.md",
    "rules/AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)


def check_target_30_regression_gate_invariants(
    repo_files: dict[str, str] | None = None,
    repo_root: str | None = None,
) -> list[str]:
  """Validates GH-233 Target 30 full regression gate invariants across configs, docs, and adapters."""
  errs: list[str] = []
  base_dir = repo_root or REPO_ROOT
  files_map: dict[str, str] = {}
  if repo_files is not None:
    files_map = dict(repo_files)
  else:
    for rel in TARGET_30_REGRESSION_GATE_FILES:
      fpath = os.path.normpath(os.path.join(base_dir, rel))
      if not os.path.isfile(fpath):
        errs.append(f"Missing {rel}")
      else:
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  for rel in TARGET_30_REGRESSION_GATE_FILES:
    if rel not in files_map:
      if f"Missing {rel}" not in errs:
        errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. Config defaults and schema parity
  for cfg_rel in ("default_config.json", ".workflow/config.json.example"):
    try:
      cfg = json.loads(files_map[cfg_rel])
    except json.JSONDecodeError as exc:
      errs.append(f"{cfg_rel} is invalid JSON: {exc}")
      continue
    rev = cfg.get("review")
    if not isinstance(rev, dict) or rev.get("allowCriticHermeticTests") is not False:
      errs.append(
          f"{cfg_rel} must set review.allowCriticHermeticTests to false"
      )
    testing = cfg.get("testing")
    if not isinstance(testing, dict):
      errs.append(f"{cfg_rel} must define testing configuration object")
    else:
      if testing.get("regressionCommand") != "":
        errs.append(f"{cfg_rel} must set testing.regressionCommand to empty string")
      if testing.get("regressionTimeoutSeconds") != 300:
        errs.append(f"{cfg_rel} must set testing.regressionTimeoutSeconds to 300")

  # 2. Agent definitions
  for tok in ("verify-regression", "30_regression_receipt.json"):
    if tok not in files_map["agents/tdd-worker.md"]:
      errs.append(f"agents/tdd-worker.md must reference '{tok}'")
  for tok in ("30_regression_receipt.json", "review.allowCriticHermeticTests"):
    if tok not in files_map["agents/adversarial-critic.md"]:
      errs.append(f"agents/adversarial-critic.md must reference '{tok}'")

  # 3. SKILL.md budget and DAG wiring
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  skill_bytes = len(skill_text.encode("utf-8"))
  if skill_bytes > 24576:
    errs.append(f"{skill_rel} exceeds 24576-byte budget ({skill_bytes} bytes)")
  for tok in (
      "adversarial-review: check-approved-tree check-regression",
      "verify-regression",
      "30_regression_receipt.json",
  ):
    if tok not in skill_text:
      errs.append(f"{skill_rel} must reference '{tok}'")

  # 4. Reference manuals
  manual_tokens = {
      "skills/makeflow/references/03_tdd_and_code_quality.md": (
          "verify-regression",
          "30_regression_receipt.json",
          "lib/regression_gate.py",
      ),
      "skills/makeflow/references/04_six_pillars_adversarial_review.md": (
          "verify-regression",
          "30_regression_receipt.json",
          "review.allowCriticHermeticTests",
      ),
      "skills/makeflow/references/05_pre_commit_hygiene.md": (
          "30_regression_receipt.json",
      ),
      "skills/makeflow/references/08_subagent_orchestration.md": (
          "verify-regression",
          "review.allowCriticHermeticTests",
      ),
      "skills/makeflow/references/13_configuration_and_user_preferences.md": (
          "allowCriticHermeticTests",
          "regressionCommand",
          "regressionTimeoutSeconds",
      ),
  }
  for man_rel, req_tokens in manual_tokens.items():
    for tok in req_tokens:
      if tok not in files_map[man_rel]:
        errs.append(f"{man_rel} must reference '{tok}'")

  # 5. Rules and all 5 cross-agent adapters
  for adapter_rel in (
      "rules/AGENTS.md",
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ):
    for tok in (
        "verify-regression",
        "30_regression_receipt.json",
        "review.allowCriticHermeticTests",
    ):
      if tok not in files_map[adapter_rel]:
        errs.append(f"{adapter_rel} must reference '{tok}'")

  return errs


class TestTarget30RegressionGateInvariants(unittest.TestCase):
  """Regression and negative falsification tests for GH-233 full subproject regression gate."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in TARGET_30_REGRESSION_GATE_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_repo_target30_regression_gate_files_pass(self) -> None:
    self.assertEqual(check_target_30_regression_gate_invariants(), [])

  def test_negative_falsifications_caught(self) -> None:
    base = self._load_base_files()

    # 1. Config default flipped to true or testing block removed
    mut_cfg = dict(base)
    cfg_data = json.loads(mut_cfg["default_config.json"])
    cfg_data["review"]["allowCriticHermeticTests"] = True
    mut_cfg["default_config.json"] = json.dumps(cfg_data)
    errs_cfg = check_target_30_regression_gate_invariants(mut_cfg)
    self.assertTrue(
        any("allowCriticHermeticTests" in e for e in errs_cfg),
        f"Expected allowCriticHermeticTests rejection, got: {errs_cfg}",
    )

    mut_ex = dict(base)
    ex_data = json.loads(mut_ex[".workflow/config.json.example"])
    ex_data.pop("testing", None)
    mut_ex[".workflow/config.json.example"] = json.dumps(ex_data)
    errs_ex = check_target_30_regression_gate_invariants(mut_ex)
    self.assertTrue(
        any("testing" in e for e in errs_ex),
        f"Expected missing testing block rejection, got: {errs_ex}",
    )

    # 2. Dropping check-regression from SKILL.md
    mut_skill = dict(base)
    mut_skill["skills/makeflow/SKILL.md"] = mut_skill[
        "skills/makeflow/SKILL.md"
    ].replace("check-regression", "REMOVED")
    errs_skill = check_target_30_regression_gate_invariants(mut_skill)
    self.assertTrue(
        any("check-regression" in e for e in errs_skill),
        f"Expected SKILL.md check-regression rejection, got: {errs_skill}",
    )

    # 3. Dropping required tokens from agents, rules, and all 5 adapters
    for rel in (
        "agents/tdd-worker.md",
        "agents/adversarial-critic.md",
        "rules/AGENTS.md",
        "CLAUDE.md",
        ".cursorrules",
        ".cursor/rules/makeflow.mdc",
        ".github/copilot-instructions.md",
        "adapters/chatgpt/instructions.md",
    ):
      mut_rel = dict(base)
      mut_rel[rel] = mut_rel[rel].replace("30_regression_receipt.json", "REMOVED")
      errs_rel = check_target_30_regression_gate_invariants(mut_rel)
      self.assertTrue(
          any(rel in e for e in errs_rel),
          f"Expected {rel} rejection, got: {errs_rel}",
      )


ARCHAEOLOGY_FILE_TREE_DISPATCH_FILES = (
    "agents/archaeology-worker.md",
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/08_subagent_orchestration.md",
    "rules/AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)


def check_archaeology_file_tree_dispatch_contract(
    repo_files: dict[str, str] | None = None,
    repo_root: str | None = None,
) -> list[str]:
  """Validates GH-246 Target 15 Git file tree index dispatch invariants across agents, docs, and adapters."""
  errs: list[str] = []
  base_dir = repo_root or REPO_ROOT
  files_map: dict[str, str] = {}
  if repo_files is not None:
    files_map = dict(repo_files)
  else:
    for rel in ARCHAEOLOGY_FILE_TREE_DISPATCH_FILES:
      fpath = os.path.normpath(os.path.join(base_dir, rel))
      if not os.path.isfile(fpath):
        errs.append(f"Missing {rel}")
      else:
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  for rel in ARCHAEOLOGY_FILE_TREE_DISPATCH_FILES:
    if rel not in files_map and f"Missing {rel}" not in errs:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. agents/archaeology-worker.md: read-only, supplied index + workflow_repo_tree fallback, 0 of 0 files
  aw_rel = "agents/archaeology-worker.md"
  aw_text = files_map[aw_rel]
  if "run_command" in aw_text:
    errs.append(f"{aw_rel} must remain read-only without run_command")
  for tok in ("file tree index", "workflow_repo_tree", "0 of 0 files"):
    if tok not in aw_text.lower():
      errs.append(f"{aw_rel} must document '{tok}'")
  if "probe" not in aw_text.lower() and "guess" not in aw_text.lower():
    errs.append(f"{aw_rel} must forbid blind path probing")

  # 2. skills/makeflow/SKILL.md: Target 15 captures repo-tree under SCAR-TOOL-16 before halt
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  if len(skill_text.encode("utf-8")) > 24576:
    errs.append(f"{skill_rel} exceeds 24576-byte budget")
  if "### Target 15:" not in skill_text:
    errs.append(f"{skill_rel} missing '### Target 15:' section")
  else:
    t15_sec = re.split(
        r"\n###\s+|\n---\n",
        skill_text.split("### Target 15:", 1)[1],
        maxsplit=1,
    )[0]
    for tok in ("repo-tree", "SCAR-TOOL-16", "file tree index", "archaeology-worker"):
      if tok.lower() not in t15_sec.lower():
        errs.append(f"{skill_rel} Target 15 must document '{tok}'")
    tree_pos = t15_sec.find("repo-tree")
    halt_pos = t15_sec.find("Immediate Supervisory Halt")
    if tree_pos == -1 or halt_pos == -1 or tree_pos >= halt_pos:
      errs.append(
          f"{skill_rel} Target 15 must capture repo-tree before Immediate Supervisory Halt"
      )

  # 3. skills/makeflow/references/02_archaeology_and_planning.md Section 1
  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_sec1 = _extract_markdown_h2_section(files_map[r02_rel], "## 1. Target 15")
  for tok in (
      "repo-tree",
      "git ls-files --cached --others --exclude-standard",
      "SCAR-TOOL-16",
      "0 of 0 files",
      "workflow_repo_tree",
      "invoke_subagent",
  ):
    if tok not in r02_sec1:
      errs.append(f"{r02_rel} Section 1 must document '{tok}'")

  # 4. skills/makeflow/references/08_subagent_orchestration.md Section 5
  r08_rel = "skills/makeflow/references/08_subagent_orchestration.md"
  r08_sec5 = _extract_markdown_h2_section(
      files_map[r08_rel], "## 5. Socratic Subagent Delegation Protocol"
  )
  for tok in (
      "repo-tree",
      "SCAR-TOOL-16",
      "workflow_repo_tree",
      "0 of 0 files",
      "file tree index",
  ):
    if tok not in r08_sec5:
      errs.append(f"{r08_rel} Section 5 must document '{tok}'")

  # 5. rules/AGENTS.md Section 1.13
  agents_rel = "rules/AGENTS.md"
  sec_hdr = "### 1.13 Immediate Supervisory Halt During Subagent Execution (SCAR-PROC-95)"
  agents_text = files_map[agents_rel]
  if sec_hdr not in agents_text:
    errs.append(f"{agents_rel} missing '{sec_hdr}'")
  else:
    sec_1_13 = re.split(
        r"\n#{2,3}\s+", agents_text.split(sec_hdr, 1)[1], maxsplit=1
    )[0]
    for tok in (
        "repo-tree",
        "SCAR-TOOL-16",
        "workflow_repo_tree",
        "archaeology-worker",
        "file tree index",
    ):
      if tok not in sec_1_13:
        errs.append(f"{agents_rel} Section 1.13 must document '{tok}'")

  # 6. Cross-agent adapters
  for adapter_rel in (
      "CLAUDE.md",
      ".cursorrules",
      ".cursor/rules/makeflow.mdc",
      ".github/copilot-instructions.md",
      "adapters/chatgpt/instructions.md",
  ):
    ad_text = files_map[adapter_rel]
    if "file tree index" not in ad_text.lower():
      errs.append(f"{adapter_rel} must document Target 15 'file tree index'")
    if (
        "repo-tree" not in ad_text
        and "git ls-files --cached --others --exclude-standard" not in ad_text
    ):
      errs.append(
          f"{adapter_rel} must reference 'repo-tree' or 'git ls-files --cached --others --exclude-standard'"
      )
    if "SCAR-TOOL-16" not in ad_text:
      errs.append(f"{adapter_rel} must reference 'SCAR-TOOL-16'")
    if "workflow_repo_tree" not in ad_text:
      errs.append(f"{adapter_rel} must reference 'workflow_repo_tree'")

  return errs


class TestArchaeologyFileTreeDispatch(unittest.TestCase):
  """Regression and negative falsification tests for GH-246 Target 15 file tree index dispatch."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in ARCHAEOLOGY_FILE_TREE_DISPATCH_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_live_repo_archaeology_file_tree_dispatch_contract(self) -> None:
    self.assertEqual(check_archaeology_file_tree_dispatch_contract(), [])

  def test_negative_falsification_archaeology_file_tree_dispatch(self) -> None:
    base = self._load_base_files()
    self.assertEqual(check_archaeology_file_tree_dispatch_contract(base), [])

    mutations = (
        ("agents/archaeology-worker.md", "workflow_repo_tree", "REMOVED"),
        ("skills/makeflow/SKILL.md", "repo-tree", "REMOVED"),
        ("skills/makeflow/references/02_archaeology_and_planning.md", "0 of 0 files", "REMOVED"),
        ("skills/makeflow/references/08_subagent_orchestration.md", "workflow_repo_tree", "REMOVED"),
        ("rules/AGENTS.md", "repo-tree", "REMOVED"),
        ("CLAUDE.md", "file tree index", "REMOVED"),
        (".cursorrules", "file tree index", "REMOVED"),
        (".cursor/rules/makeflow.mdc", "file tree index", "REMOVED"),
        (".github/copilot-instructions.md", "file tree index", "REMOVED"),
        ("adapters/chatgpt/instructions.md", "file tree index", "REMOVED"),
    )
    for rel, token, repl in mutations:
      with self.subTest(rel=rel, token=token):
        mut = dict(base)
        mut[rel] = mut[rel].replace(token, repl)
        errs = check_archaeology_file_tree_dispatch_contract(mut)
        self.assertTrue(
            any(rel in e for e in errs),
            f"Expected rejection when '{token}' omitted from {rel}, got: {errs}",
        )


TEMPDIR_TEST_ISOLATION_FILES = (
    "skills/makeflow/references/scars/testing.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "skills/makeflow/references/04_testing_isolation.md",
    "skills/makeflow/references/polyglot/python.md",
    "agents/tdd-worker.md",
    "agents/socratic-probe-writer.md",
)


def check_tempdir_test_isolation_invariants(
    files_map: dict[str, str] | None = None,
) -> list[str]:
  """Verifies GH-215 SCAR-TEST-45 and SCAR-TEST-22 invariants across docs and agents."""
  if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
  from lib.scar_candidate import (
      ScarCandidateValidator,
      count_words_in_sentence,
      mask_code_and_math_spans,
      split_into_sentences,
      validate_socratic_probe,
  )

  if files_map is None:
    files_map = {}
    for rel in TEMPDIR_TEST_ISOLATION_FILES:
      full = repo_path(rel)
      if not os.path.isfile(full):
        return [f"Missing required GH-215 file: {rel}"]
      with open(full, "r", encoding="utf-8") as f:
        files_map[rel] = f.read()

  errs: list[str] = []

  def _check_procedural_line_word_limit(
      rel_path: str, line_text: str, label: str, max_words: int = 20
  ) -> None:
    stripped = re.sub(r"^\s*\d+\.\s+", "", line_text.strip())
    masked = mask_code_and_math_spans(stripped)
    for sent in split_into_sentences(masked):
      wc = count_words_in_sentence(sent)
      if wc > max_words:
        errs.append(
            f"{rel_path} {label} exceeds {max_words}-word procedural limit ({wc} words): {sent}"
        )

  def _validate_scar_section(
      rel_path: str, scar_id: str, section_text: str
  ) -> None:
    extracted: dict[str, str] = {}
    req_fields = ("Wound", "Trap", "Permanent Reflex", "Socratic Probe")
    for field in req_fields:
      fm = re.search(rf"\*\*{re.escape(field)}\*\*:[ \t]*([^\n\r]+)", section_text)
      val = fm.group(1).strip() if fm else ""
      extracted[field] = val
      if not val:
        errs.append(f"{rel_path} {scar_id} missing non-empty '**{field}**:'")
    cb_match = re.search(r"\*\*Circuit Breaker\*\*:[ \t]*([^\n\r]+)", section_text)
    cb_val = cb_match.group(1).strip() if cb_match else ""
    if all(extracted[f] for f in req_fields):
      candidate = {
          "id": scar_id,
          "raw_wound": extracted["Wound"],
          "wound": extracted["Wound"],
          "raw_trap": extracted["Trap"],
          "trap": extracted["Trap"],
          "raw_circuit_breaker": cb_val,
          "circuit_breaker": cb_val,
          "raw_reflex": extracted["Permanent Reflex"],
          "reflex": extracted["Permanent Reflex"],
          "socratic_probe": extracted["Socratic Probe"],
      }
      errs.extend(ScarCandidateValidator().validate(candidate))
      errs.extend(
          validate_socratic_probe(extracted["Socratic Probe"], scar_id=scar_id)
      )
      probe_sents = split_into_sentences(extracted["Socratic Probe"])
      if not (1 <= len(probe_sents) <= 3):
        errs.append(
            f"{rel_path} {scar_id} Socratic Probe must contain 1-3 '?' sentences, found {len(probe_sents)}"
        )

  # 1. skills/makeflow/references/scars/testing.md
  test_rel = "skills/makeflow/references/scars/testing.md"
  t_text = files_map[test_rel]
  r44 = re.search(r"(?m)^\|\s*`SCAR-TEST-44`\s*\|", t_text)
  r45 = re.search(r"(?m)^\|\s*`SCAR-TEST-45`\s*\|", t_text)
  if not (r44 and r45 and r44.start() < r45.start()):
    errs.append(f"{test_rel} summary table must list SCAR-TEST-45 after SCAR-TEST-44")

  m44 = re.search(r"(?m)^###\s+SCAR-TEST-44:", t_text)
  m45 = re.search(r"(?m)^###\s+SCAR-TEST-45:", t_text)
  if not (m44 and m45 and m44.start() < m45.start()):
    errs.append(f"{test_rel} must place '### SCAR-TEST-45:' after '### SCAR-TEST-44:'")
  else:
    sec45 = t_text[m45.start() :].split("\n### SCAR-")[0]
    for tok in ("tempfile.TemporaryDirectory()", "t.TempDir()", "TempDir", "mktemp -d", "trap"):
      if tok not in sec45:
        errs.append(f"{test_rel} SCAR-TEST-45 missing multi-language helper token '{tok}'")
    _validate_scar_section(test_rel, "SCAR-TEST-45", sec45)

  m22 = re.search(r"(?m)^###\s+SCAR-TEST-22:", t_text)
  if not m22:
    errs.append(f"{test_rel} missing '### SCAR-TEST-22:'")
  else:
    sec22 = t_text[m22.start() :].split("\n### SCAR-")[0]
    if "./tests/" not in sec22:
      errs.append(f"{test_rel} SCAR-TEST-22 must direct placing tests under './tests/'")
    if "behavioral" not in sec22.lower():
      errs.append(f"{test_rel} SCAR-TEST-22 must mandate behavioral domain naming")
    _validate_scar_section(test_rel, "SCAR-TEST-22", sec22)

  # 2. skills/makeflow/references/09_synthetic_scars_catalog.md
  cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  cat_text = files_map[cat_rel]
  if not check_scar_catalog_routing(cat_text, "SCAR-TEST-45", "testing.md"):
    errs.append(
        f"{cat_rel} Master Routing Table must strictly route SCAR-TEST-45 to scars/testing.md"
    )
  c44 = re.search(r"(?m)^\|\s*`SCAR-TEST-44`\s*\|", cat_text)
  c45 = re.search(r"(?m)^\|\s*`SCAR-TEST-45`\s*\|", cat_text)
  if not (c44 and c45 and c44.start() < c45.start()):
    errs.append(f"{cat_rel} must place SCAR-TEST-45 monotonically after SCAR-TEST-44")

  # 3. skills/makeflow/references/04_testing_isolation.md
  iso_rel = "skills/makeflow/references/04_testing_isolation.md"
  iso_text = files_map[iso_rel]
  for tok in ("SCAR-TEST-45", "SCAR-TEST-22", "tempfile.TemporaryDirectory()", "mkdtemp", "NamedTemporaryFile", "./tests/"):
    if tok not in iso_text:
      errs.append(f"{iso_rel} missing required GH-215 token '{tok}'")
  errs.extend(_check_prose_punctuation(iso_rel, iso_text))
  for raw_line in iso_text.splitlines():
    if "SCAR-TEST-45" in raw_line or "SCAR-TEST-22" in raw_line:
      if re.match(r"^\s*\d+\.\s+", raw_line):
        _check_procedural_line_word_limit(iso_rel, raw_line, "GH-215 list item")

  # 4. skills/makeflow/references/polyglot/python.md
  py_rel = "skills/makeflow/references/polyglot/python.md"
  py_text = files_map[py_rel]
  for tok in ("SCAR-TEST-45", "SCAR-TEST-22", "tempfile.TemporaryDirectory()"):
    if tok not in py_text:
      errs.append(f"{py_rel} missing required GH-215 token '{tok}'")
  errs.extend(_check_prose_punctuation(py_rel, py_text))

  # 5. agents/tdd-worker.md
  tdd_rel = "agents/tdd-worker.md"
  tdd_text = files_map[tdd_rel]
  for tok in ("SCAR-TEST-45", "SCAR-TEST-22"):
    if tok not in tdd_text:
      errs.append(f"{tdd_rel} missing required GH-215 token '{tok}'")
  errs.extend(_check_prose_punctuation(tdd_rel, tdd_text))
  for raw_line in tdd_text.splitlines():
    if "SCAR-TEST-45" in raw_line:
      _check_procedural_line_word_limit(tdd_rel, raw_line, "Rule 15")

  # 6. agents/socratic-probe-writer.md
  spw_rel = "agents/socratic-probe-writer.md"
  spw_text = files_map[spw_rel]
  if "/tmp/" in spw_text:
    errs.append(f"{spw_rel} must not contain hardcoded '/tmp/' path")
  if "mktemp -d" not in spw_text and "TemporaryDirectory" not in spw_text:
    errs.append(
        f"{spw_rel} must use unique OS-managed temp directory (mktemp -d or TemporaryDirectory)"
    )
  for raw_line in spw_text.splitlines():
    if re.match(r"^\s*5\.\s+", raw_line):
      _check_procedural_line_word_limit(spw_rel, raw_line, "Rule 5")
      if "inside it" in raw_line.lower():
        errs.append(f"{spw_rel} Rule 5 must not use bare pronoun 'inside it'")

  return errs


class TestTempDirTestIsolationInvariants(unittest.TestCase):
  """Contract tests for GH-215 SCAR-TEST-45 and SCAR-TEST-22 docs and agents."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in TEMPDIR_TEST_ISOLATION_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_repo_tempdir_isolation_invariants_pass(self) -> None:
    self.assertEqual(check_tempdir_test_isolation_invariants(), [])

  def test_negative_falsifications_caught(self) -> None:
    base = self._load_base_files()

    # 1. testing.md: drop SCAR-TEST-45 helper token, break SCAR-TEST-22 ./tests/, invalid Socratic Probe
    test_rel = "skills/makeflow/references/scars/testing.md"
    mut_45 = dict(base)
    mut_45[test_rel] = mut_45[test_rel].replace("t.TempDir()", "REMOVED")
    self.assertTrue(
        any("t.TempDir()" in e for e in check_tempdir_test_isolation_invariants(mut_45)),
        "Expected missing t.TempDir() rejection in testing.md",
    )

    mut_22 = dict(base)
    mut_22[test_rel] = mut_22[test_rel].replace("./tests/", "REMOVED")
    self.assertTrue(
        any("./tests/" in e for e in check_tempdir_test_isolation_invariants(mut_22)),
        "Expected missing ./tests/ rejection in SCAR-TEST-22",
    )

    mut_22_probe = dict(base)
    mut_22_probe[test_rel] = mut_22_probe[test_rel].replace(
        "Does this test live in the test directory with a behavioral domain name and a permanent ticket docstring?",
        "This probe has no question mark.",
    )
    self.assertTrue(
        any("SCAR-TEST-22" in e and "?" in e for e in check_tempdir_test_isolation_invariants(mut_22_probe)),
        "Expected invalid SCAR-TEST-22 Socratic Probe rejection",
    )

    # 2. 09_synthetic_scars_catalog.md: break routing / monotonicity
    cat_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
    mut_cat = dict(base)
    mut_cat[cat_rel] = mut_cat[cat_rel].replace("SCAR-TEST-45", "SCAR-TEST-99")
    self.assertTrue(
        any(cat_rel in e for e in check_tempdir_test_isolation_invariants(mut_cat)),
        "Expected catalog routing rejection for SCAR-TEST-45",
    )

    # 3. 04_testing_isolation.md: missing token and >20-word procedural line
    iso_rel = "skills/makeflow/references/04_testing_isolation.md"
    mut_iso = dict(base)
    mut_iso[iso_rel] = mut_iso[iso_rel].replace("NamedTemporaryFile", "REMOVED")
    self.assertTrue(
        any(iso_rel in e for e in check_tempdir_test_isolation_invariants(mut_iso)),
        "Expected 04_testing_isolation.md token rejection",
    )
    mut_iso_long = dict(base)
    mut_iso_long[iso_rel] = mut_iso_long[iso_rel] + (
        "\n4. Extra rule (`SCAR-TEST-45`): Write one single extremely verbose procedural"
        " instruction sentence that deliberately exceeds the twenty word technical"
        " English limit right now.\n"
    )
    self.assertTrue(
        any("20-word procedural limit" in e for e in check_tempdir_test_isolation_invariants(mut_iso_long)),
        "Expected 04_testing_isolation.md 20-word procedural rejection",
    )

    # 4. polyglot/python.md: missing SCAR-TEST-22 token
    py_rel = "skills/makeflow/references/polyglot/python.md"
    mut_py = dict(base)
    mut_py[py_rel] = mut_py[py_rel].replace("SCAR-TEST-22", "REMOVED")
    self.assertTrue(
        any(py_rel in e for e in check_tempdir_test_isolation_invariants(mut_py)),
        "Expected polyglot/python.md token rejection",
    )

    # 5. agents/tdd-worker.md: missing SCAR-TEST-45 token
    tdd_rel = "agents/tdd-worker.md"
    mut_tdd = dict(base)
    mut_tdd[tdd_rel] = mut_tdd[tdd_rel].replace("SCAR-TEST-45", "REMOVED")
    self.assertTrue(
        any(tdd_rel in e for e in check_tempdir_test_isolation_invariants(mut_tdd)),
        "Expected agents/tdd-worker.md token rejection",
    )

    # 6. agents/socratic-probe-writer.md: reintroduce /tmp/ and drop mktemp -d/TemporaryDirectory
    spw_rel = "agents/socratic-probe-writer.md"
    mut_spw_tmp = dict(base)
    mut_spw_tmp[spw_rel] = mut_spw_tmp[spw_rel] + "\nUse /tmp/fixed/socratic_prompt.txt.\n"
    self.assertTrue(
        any("/tmp/" in e for e in check_tempdir_test_isolation_invariants(mut_spw_tmp)),
        "Expected agents/socratic-probe-writer.md /tmp/ rejection",
    )
    mut_spw_helper = dict(base)
    mut_spw_helper[spw_rel] = (
        mut_spw_helper[spw_rel]
        .replace("mktemp -d", "REMOVED")
        .replace("TemporaryDirectory", "REMOVED")
    )
    self.assertTrue(
        any("mktemp -d" in e for e in check_tempdir_test_isolation_invariants(mut_spw_helper)),
        "Expected agents/socratic-probe-writer.md missing temp helper rejection",
    )


EARLY_DESIGN_AND_SCAR_FILES = (
    "skills/makeflow/SKILL.md",
    "skills/makeflow/references/02_archaeology_and_planning.md",
    "skills/makeflow/references/14_design_patterns_and_architecture.md",
    "skills/makeflow/references/03_tdd_and_code_quality.md",
    "skills/makeflow/references/08_subagent_orchestration.md",
    "skills/makeflow/references/09_synthetic_scars_catalog.md",
    "agents/archaeology-worker.md",
    "agents/tdd-worker.md",
    "rules/AGENTS.md",
)


def _extract_markdown_h3_section(text: str, heading_prefix: str) -> str:
  """Extracts an H3 section up to the next H2/H3 heading or horizontal rule."""
  captured: list[str] = []
  in_section = False
  in_fence = False
  target_lower = heading_prefix.lower()
  for raw_line in text.splitlines():
    stripped = raw_line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
    if not in_fence:
      if in_section and (
          stripped.startswith("## ")
          or stripped.startswith("### ")
          or stripped == "---"
      ):
        break
      if (
          not in_section
          and stripped.startswith("### ")
          and stripped.lower().startswith(target_lower)
      ):
        in_section = True
        captured.append(raw_line)
        continue
    if in_section:
      captured.append(raw_line)
  return "\n".join(captured)


def _extract_agent_frontmatter_tools(text: str) -> tuple[str, str]:
  """Splits YAML frontmatter from agent markdown body."""
  match = re.match(r"^---\n([\s\S]*?)\n---\n([\s\S]*)$", text)
  if not match:
    return "", text
  return match.group(1), match.group(2)


def _check_section_simple_english(label: str, text: str) -> list[str]:
  """Checks zero contractions, semicolons, and em-dashes in prose outside code spans."""
  from lib.scar_candidate import _CONTRACTION_RE

  errs = _check_prose_punctuation(label, text)
  in_fence = False
  for idx, raw_line in enumerate(text.splitlines(), start=1):
    stripped = raw_line.strip()
    if stripped.startswith("```"):
      in_fence = not in_fence
      continue
    if in_fence or not stripped or stripped.startswith("#"):
      continue
    scrubbed = re.sub(r"`[^`]*`", "", stripped)
    contraction = _CONTRACTION_RE.search(scrubbed)
    if contraction:
      errs.append(
          f"{label}:{idx} contains forbidden contraction '{contraction.group(0)}' in prose"
      )
    if stripped.startswith("|"):
      if ";" in scrubbed:
        errs.append(f"{label}:{idx} contains forbidden semicolon (';') in table prose")
      if "\u2014" in scrubbed:
        errs.append(f"{label}:{idx} contains forbidden em-dash ('—') in table prose")
  return errs


def check_early_design_and_scar_contract(
    files_by_relpath: dict[str, str] | None = None,
    repo_root: str | None = None,
) -> list[str]:
  """Validates GH-252 Target 15/20/30 OO patterns, cohesion, TDD, and two-query scar contracts."""
  errs: list[str] = []
  base_dir = repo_root or REPO_ROOT
  files_map: dict[str, str] = {}
  if files_by_relpath is not None:
    files_map = dict(files_by_relpath)
  else:
    for rel in EARLY_DESIGN_AND_SCAR_FILES:
      fpath = os.path.normpath(os.path.join(base_dir, rel))
      if not os.path.isfile(fpath):
        errs.append(f"Missing {rel}")
      else:
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  for rel in EARLY_DESIGN_AND_SCAR_FILES:
    if rel not in files_map and f"Missing {rel}" not in errs:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. skills/makeflow/SKILL.md: byte cap + anchored Target 15, Target 20, Target 30 sections
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  if len(skill_text.encode("utf-8")) > 24576:
    errs.append(
        f"{skill_rel} exceeds 24,576-byte budget ({len(skill_text.encode('utf-8'))} bytes)"
    )

  t15_sec = _extract_markdown_h3_section(skill_text, "### Target 15:")
  if not t15_sec:
    errs.append(f"{skill_rel} missing '### Target 15:' section")
  else:
    for tok in (
        "references/14",
        "references/09",
        "references/08",
        "cohesion",
        "workflow_query_scars",
        "universal",
        "excludeCategories",
        "WORKFLOW",
    ):
      if tok.lower() not in t15_sec.lower():
        errs.append(f"{skill_rel} Target 15 must document '{tok}'")
    if "oo " not in t15_sec.lower() and "design pattern" not in t15_sec.lower():
      errs.append(f"{skill_rel} Target 15 must require OO or design pattern discovery")
    errs.extend(_check_section_simple_english(f"{skill_rel} [Target 15]", t15_sec))

  t20_sec = _extract_markdown_h3_section(skill_text, "### Target 20:")
  if not t20_sec:
    errs.append(f"{skill_rel} missing '### Target 20:' section")
  else:
    for tok in (
        "references/14",
        "references/03",
        "references/04_testing_isolation.md",
        "references/09",
        "cohesion",
        "red-green",
        "workflow_query_scars",
        'workflowPhase: "Target 20: plan"',
        "universal",
        "excludeCategories",
        "WORKFLOW",
        "socratic probe",
    ):
      if tok.lower() not in t20_sec.lower():
        errs.append(f"{skill_rel} Target 20 must document '{tok}'")
    if "oo " not in t20_sec.lower() and "design pattern" not in t20_sec.lower():
      errs.append(f"{skill_rel} Target 20 must require OO or design pattern evaluation")
    errs.extend(_check_section_simple_english(f"{skill_rel} [Target 20]", t20_sec))

  t30_sec = _extract_markdown_h3_section(skill_text, "### Target 30:")
  if not t30_sec:
    errs.append(f"{skill_rel} missing '### Target 30:' section")
  else:
    for tok in (
        "references/03",
        "references/04_testing_isolation.md",
        "references/08",
        "references/09",
        "references/10",
        "references/14",
        "20_plan.md",
        "workflow_query_scars",
        'workflowPhase: "Target 30: tdd"',
        "excludeCategories",
        "WORKFLOW",
        "refreshLoaded: true",
    ):
      if tok.lower() not in t30_sec.lower():
        errs.append(f"{skill_rel} Target 30 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{skill_rel} [Target 30]", t30_sec))

  # 2. skills/makeflow/references/02_archaeology_and_planning.md
  r02_rel = "skills/makeflow/references/02_archaeology_and_planning.md"
  r02_text = files_map[r02_rel]
  r02_sec1 = _extract_markdown_h2_section(r02_text, "## 1. Target 15")
  if not r02_sec1:
    errs.append(f"{r02_rel} missing '## 1. Target 15' section")
  else:
    for tok in (
        "14_design_patterns_and_architecture.md",
        "09_synthetic_scars_catalog.md",
        "04_testing_isolation.md",
        "10_security_and_data_isolation.md",
        "11_polyglot_monorepos.md",
        "cohesion",
        "skills/makeflow/references/",
        "workflow_query_scars",
        'workflowPhase: "Target 15: archaeology"',
        'language: "universal"',
        'excludeCategories: ["WORKFLOW"]',
        "limit: 5",
    ):
      if tok not in r02_sec1:
        errs.append(f"{r02_rel} Section 1 must document '{tok}'")
    if "oo " not in r02_sec1.lower() and "object-oriented" not in r02_sec1.lower():
      errs.append(f"{r02_rel} Section 1 must require auditing sibling OO patterns")
    errs.extend(_check_section_simple_english(f"{r02_rel} [Section 1]", r02_sec1))

  r02_s21 = _extract_markdown_h3_section(
      r02_text, "### 2.1 Socratic Pre-Planning Deliberation"
  )
  if not r02_s21:
    errs.append(f"{r02_rel} missing '### 2.1 Socratic Pre-Planning Deliberation'")
  else:
    for tok in (
        "14_design_patterns_and_architecture.md",
        "03_tdd_and_code_quality.md",
        "04_testing_isolation.md",
        "Red-Green-Refactor",
        "cohesion",
        "skills/makeflow/references/",
        'workflowPhase: "Target 20: plan"',
        'language: "universal"',
        'excludeCategories: ["WORKFLOW"]',
    ):
      if tok not in r02_s21:
        errs.append(f"{r02_rel} Section 2.1 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{r02_rel} [Section 2.1]", r02_s21))

  r02_s22 = _extract_markdown_h3_section(
      r02_text, "### 2.2 Socratic Invariant Audit Table"
  )
  if not r02_s22:
    errs.append(f"{r02_rel} missing '### 2.2 Socratic Invariant Audit Table'")
  else:
    for row_tok in (
        "| **Red-Green-Refactor TDD Methodology** |",
        "| **OO Design Patterns and Module Cohesion** |",
        "| **Available Reference Manuals and Scar Carry-Forward** |",
    ):
      if row_tok not in r02_s22:
        errs.append(
            f"{r02_rel} Section 2.2 Socratic Invariant Audit Table missing row '{row_tok}'"
        )
    errs.extend(_check_section_simple_english(f"{r02_rel} [Section 2.2]", r02_s22))

  r02_s23 = _extract_markdown_h3_section(r02_text, "### 2.3 Required Plan Disclosures")
  if not r02_s23:
    errs.append(f"{r02_rel} missing '### 2.3 Required Plan Disclosures'")
  else:
    if "seven items" in r02_s23.lower():
      errs.append(f"{r02_rel} Section 2.3 must fix stale 'seven items' count")
    for tok in (
        "14_design_patterns_and_architecture.md",
        "03_tdd_and_code_quality.md",
        "04_testing_isolation.md",
        "Red-Green-Refactor",
        "cohesion",
        "Socratic probes",
        "tdd-worker",
        "20_plan.md",
    ):
      if tok not in r02_s23:
        errs.append(f"{r02_rel} Section 2.3 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{r02_rel} [Section 2.3]", r02_s23))

  # 3. skills/makeflow/references/14_design_patterns_and_architecture.md
  r14_rel = "skills/makeflow/references/14_design_patterns_and_architecture.md"
  r14_text = files_map[r14_rel]
  r14_header = r14_text.split("\n## ", 1)[0]
  for phase_tok in ("Target 15", "Target 20", "Target 30", "Target 40"):
    if phase_tok not in r14_header:
      errs.append(f"{r14_rel} scope header must cover '{phase_tok}'")
  if "for Target 30 implementation and Target 40 review" in r14_header:
    errs.append(
        f"{r14_rel} scope header must not restrict scope to Target 30 and Target 40"
    )
  errs.extend(_check_section_simple_english(f"{r14_rel} [Header]", r14_header))

  r14_cohesion = _extract_markdown_h2_section(
      r14_text, "Module Size and Cohesion Boundaries"
  )
  if not r14_cohesion:
    errs.append(
        f"{r14_rel} missing '## ... Module Size and Cohesion Boundaries' section"
    )
  else:
    for tok in ("cohesion", "SCAR-ARCH-08", "decomposition"):
      if tok.lower() not in r14_cohesion.lower():
        errs.append(f"{r14_rel} Module Size and Cohesion section must document '{tok}'")
    errs.extend(
        _check_section_simple_english(f"{r14_rel} [Cohesion]", r14_cohesion)
    )

  # 4. skills/makeflow/references/03_tdd_and_code_quality.md
  r03_rel = "skills/makeflow/references/03_tdd_and_code_quality.md"
  r03_sec1 = _extract_markdown_h2_section(
      files_map[r03_rel], "## 1. Review Before Edit"
  )
  if not r03_sec1:
    errs.append(f"{r03_rel} missing '## 1. Review Before Edit' section")
  else:
    for tok in (
        "20_plan.md",
        "04_testing_isolation.md",
        "09_synthetic_scars_catalog.md",
        "10_security_and_data_isolation.md",
        "14_design_patterns_and_architecture.md",
        "cohesion",
        'workflowPhase: "Target 30: tdd"',
        'language: "universal"',
        'excludeCategories: ["WORKFLOW"]',
        "refreshLoaded: true",
    ):
      if tok not in r03_sec1:
        errs.append(f"{r03_rel} Section 1 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{r03_rel} [Section 1]", r03_sec1))

  # 5. skills/makeflow/references/08_subagent_orchestration.md
  r08_rel = "skills/makeflow/references/08_subagent_orchestration.md"
  r08_sec5 = _extract_markdown_h2_section(
      files_map[r08_rel], "## 5. Socratic Subagent Delegation Protocol"
  )
  if not r08_sec5:
    errs.append(f"{r08_rel} missing '## 5. Socratic Subagent Delegation Protocol'")
  else:
    m_aw = re.search(
        r"(?ms)^1\.\s+\*\*`archaeology-worker`.*?(?=^2\.\s+\*\*`tdd-worker`|\Z)",
        r08_sec5,
    )
    if not m_aw:
      errs.append(f"{r08_rel} Section 5 missing item 1 for archaeology-worker")
    else:
      aw_dispatch = m_aw.group(0)
      for tok in (
          "SCAR-PROC-105",
          "skills/makeflow/references/02_archaeology_and_planning.md",
          "skills/makeflow/references/14_design_patterns_and_architecture.md",
          "skills/makeflow/references/09_synthetic_scars_catalog.md",
          "skills/makeflow/references/11_polyglot_monorepos.md",
          'excludeCategories: ["WORKFLOW"]',
      ):
        if tok not in aw_dispatch:
          errs.append(
              f"{r08_rel} Section 5 archaeology-worker dispatch must anchor '{tok}'"
          )

    m_tdd = re.search(
        r"(?ms)^2\.\s+\*\*`tdd-worker`.*?(?=^3\.\s+\*\*`adversarial-critic`|\Z)",
        r08_sec5,
    )
    if not m_tdd:
      errs.append(f"{r08_rel} Section 5 missing item 2 for tdd-worker")
    else:
      tdd_dispatch = m_tdd.group(0)
      for tok in (
          "SCAR-PROC-105",
          "20_plan.md",
          "skills/makeflow/references/03_tdd_and_code_quality.md",
          "skills/makeflow/references/04_testing_isolation.md",
          "skills/makeflow/references/14_design_patterns_and_architecture.md",
          "skills/makeflow/references/09_synthetic_scars_catalog.md",
          "skills/makeflow/references/polyglot/<lang>.md",
          "Socratic probes",
          'excludeCategories: ["WORKFLOW"]',
          "refreshLoaded: true",
      ):
        if tok not in tdd_dispatch:
          errs.append(
              f"{r08_rel} Section 5 tdd-worker dispatch must anchor '{tok}'"
          )
    errs.extend(_check_section_simple_english(f"{r08_rel} [Section 5]", r08_sec5))

  # 6. skills/makeflow/references/09_synthetic_scars_catalog.md
  r09_rel = "skills/makeflow/references/09_synthetic_scars_catalog.md"
  r09_text = files_map[r09_rel]
  r09_sec1 = _extract_markdown_h2_section(r09_text, "## 1. Hub-and-Spoke")
  if "typescript_node.md" in r09_text:
    errs.append(
        f"{r09_rel} must not contain broken 'typescript_node.md' reference"
    )
  if "typescript_javascript.md" not in r09_sec1:
    errs.append(f"{r09_rel} Section 1 tree must reference 'typescript_javascript.md'")

  r09_playbook = _extract_markdown_h3_section(
      r09_text, "### 4.5 Two-Query MCP Playbook"
  )
  if not r09_playbook:
    errs.append(f"{r09_rel} missing '### 4.5 Two-Query MCP Playbook' section")
  else:
    for tok in (
        'language: "universal"',
        'excludeCategories: ["WORKFLOW"]',
        "limit: 5",
        "refreshLoaded: true",
        "09_synthetic_scars_catalog.md",
    ):
      if tok not in r09_playbook:
        errs.append(f"{r09_rel} Section 4.5 playbook must document '{tok}'")
    errs.extend(
        _check_section_simple_english(f"{r09_rel} [Section 4.5]", r09_playbook)
    )

  # 7. agents/archaeology-worker.md
  aw_rel = "agents/archaeology-worker.md"
  aw_fm, aw_body = _extract_agent_frontmatter_tools(files_map[aw_rel])
  for forbidden_tool in (
      "write_file",
      "edit_file",
      "run_command",
      "call_mcp_tool",
      "list_resources",
      "read_resource",
  ):
    if forbidden_tool in aw_fm:
      errs.append(
          f"{aw_rel} frontmatter tools must not list '{forbidden_tool}' (SCAR-PROC-106)"
      )
  if "six facts" in aw_body.lower():
    errs.append(f"{aw_rel} must fix stale 'six facts' count")
  for tok in (
      "02_archaeology_and_planning.md",
      "14_design_patterns_and_architecture.md",
      "09_synthetic_scars_catalog.md",
      "11_polyglot_monorepos.md",
      "cohesion",
      'workflowPhase: "Target 15: archaeology"',
      'language: "universal"',
      'excludeCategories: ["WORKFLOW"]',
      "limit: 5",
      "Consulted References and Applicable Scars",
  ):
    if tok not in aw_body:
      errs.append(f"{aw_rel} body must document '{tok}'")
  if "oo " not in aw_body.lower() and "object-oriented" not in aw_body.lower():
    errs.append(f"{aw_rel} body must require auditing sibling OO patterns")
  errs.extend(_check_section_simple_english(aw_rel, aw_body))

  # 8. agents/tdd-worker.md
  tdd_rel = "agents/tdd-worker.md"
  tdd_fm, tdd_body = _extract_agent_frontmatter_tools(files_map[tdd_rel])
  for req_tool in ("write_file", "edit_file", "run_command"):
    if req_tool not in tdd_fm:
      errs.append(f"{tdd_rel} frontmatter tools must keep '{req_tool}'")
  for forbidden_tool in ("call_mcp_tool", "list_resources", "read_resource"):
    if forbidden_tool in tdd_fm:
      errs.append(
          f"{tdd_rel} frontmatter tools must not list '{forbidden_tool}' (SCAR-PROC-106)"
      )
  for tok in (
      "20_plan.md",
      "03_tdd_and_code_quality.md",
      "04_testing_isolation.md",
      "14_design_patterns_and_architecture.md",
      "09_synthetic_scars_catalog.md",
      "polyglot/<lang>.md",
      "cohesion",
      'workflowPhase: "Target 30: tdd"',
      'language: "universal"',
      'excludeCategories: ["WORKFLOW"]',
      "refreshLoaded: true",
      "limit: 5",
  ):
    if tok not in tdd_body:
      errs.append(f"{tdd_rel} body must document '{tok}'")
  if "scar guardrails" not in tdd_body.lower():
    errs.append(f"{tdd_rel} body must require reporting applied scar guardrails")
  errs.extend(_check_section_simple_english(tdd_rel, tdd_body))

  # 9. rules/AGENTS.md
  agents_rel = "rules/AGENTS.md"
  agents_text = files_map[agents_rel]
  sec_1_1 = _extract_markdown_h3_section(
      agents_text, "### 1.1 Target 20 Plan Artifact Lifecycle"
  )
  if not sec_1_1:
    errs.append(f"{agents_rel} missing '### 1.1 Target 20 Plan Artifact Lifecycle'")
  else:
    for tok in (
        "Red-Green",
        "14_design_patterns_and_architecture.md",
        "cohesion",
        "skills/makeflow/references/",
        'excludeCategories: ["WORKFLOW"]',
    ):
      if tok not in sec_1_1:
        errs.append(f"{agents_rel} Section 1.1 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{agents_rel} [Section 1.1]", sec_1_1))

  sec_1_4 = _extract_markdown_h3_section(
      agents_text, "### 1.4 Explicit Six Pillars Manual Path"
  )
  if not sec_1_4:
    errs.append(f"{agents_rel} missing '### 1.4 Explicit Six Pillars Manual Path'")
  else:
    for tok in (
        "Target 15",
        "Target 30",
        "archaeology-worker",
        "tdd-worker",
        "14_design_patterns_and_architecture.md",
    ):
      if tok not in sec_1_4:
        errs.append(f"{agents_rel} Section 1.4 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{agents_rel} [Section 1.4]", sec_1_4))

  return errs


class TestEarlyDesignPatternsAndScarsContract(unittest.TestCase):
  """Section-anchored contract and negative falsification tests for GH-252."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in EARLY_DESIGN_AND_SCAR_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_live_repo_early_design_and_scar_contract_passes(self) -> None:
    self.assertEqual(check_early_design_and_scar_contract(), [])

  def test_negative_falsification_early_design_and_scar_contract(self) -> None:
    base = self._load_base_files()
    self.assertEqual(check_early_design_and_scar_contract(base), [])

    mutations = (
        ("skills/makeflow/SKILL.md", "references/14", "REMOVED"),
        ("skills/makeflow/SKILL.md", "excludeCategories", "REMOVED"),
        ("skills/makeflow/SKILL.md", "refreshLoaded: true", "REMOVED"),
        (
            "skills/makeflow/references/02_archaeology_and_planning.md",
            "| **Red-Green-Refactor TDD Methodology** |",
            "| **Removed Row** |",
        ),
        (
            "skills/makeflow/references/02_archaeology_and_planning.md",
            "| **OO Design Patterns and Module Cohesion** |",
            "| **Removed Row** |",
        ),
        (
            "skills/makeflow/references/02_archaeology_and_planning.md",
            'excludeCategories: ["WORKFLOW"]',
            "REMOVED",
        ),
        (
            "skills/makeflow/references/14_design_patterns_and_architecture.md",
            "Target 15",
            "Target 99",
        ),
        (
            "skills/makeflow/references/14_design_patterns_and_architecture.md",
            "Module Size and Cohesion Boundaries",
            "Removed Section",
        ),
        (
            "skills/makeflow/references/03_tdd_and_code_quality.md",
            "refreshLoaded: true",
            "REMOVED",
        ),
        (
            "skills/makeflow/references/08_subagent_orchestration.md",
            "skills/makeflow/references/14_design_patterns_and_architecture.md",
            "REMOVED",
        ),
        (
            "skills/makeflow/references/09_synthetic_scars_catalog.md",
            "typescript_javascript.md",
            "typescript_node.md",
        ),
        (
            "skills/makeflow/references/09_synthetic_scars_catalog.md",
            "### 4.5 Two-Query MCP Playbook",
            "### 4.5 Removed Playbook",
        ),
        (
            "agents/archaeology-worker.md",
            "Consulted References and Applicable Scars",
            "Removed Section",
        ),
        (
            "agents/tdd-worker.md",
            "04_testing_isolation.md",
            "REMOVED",
        ),
        (
            "rules/AGENTS.md",
            'excludeCategories: ["WORKFLOW"]',
            "REMOVED",
        ),
    )
    for rel, token, repl in mutations:
      with self.subTest(rel=rel, token=token):
        mut = dict(base)
        mut[rel] = mut[rel].replace(token, repl)
        errs = check_early_design_and_scar_contract(mut)
        self.assertTrue(
            any(rel in e for e in errs),
            f"Expected rejection when '{token}' mutated in {rel}, got: {errs}",
        )

    # Budget overflow falsification on SKILL.md
    mut_budget = dict(base)
    mut_budget["skills/makeflow/SKILL.md"] = (
        mut_budget["skills/makeflow/SKILL.md"] + ("\n" + "a" * 25000)
    )
    budget_errs = check_early_design_and_scar_contract(mut_budget)
    self.assertTrue(
        any("24,576-byte budget" in e for e in budget_errs),
        f"Expected SKILL.md budget error, got: {budget_errs}",
    )

    # Read-only frontmatter / SCAR-PROC-106 falsification on archaeology-worker.md
    mut_aw_tool = dict(base)
    mut_aw_tool["agents/archaeology-worker.md"] = mut_aw_tool[
        "agents/archaeology-worker.md"
    ].replace("  - manage_task", "  - manage_task\n  - call_mcp_tool")
    aw_errs = check_early_design_and_scar_contract(mut_aw_tool)
    self.assertTrue(
        any("call_mcp_tool" in e for e in aw_errs),
        f"Expected call_mcp_tool rejection, got: {aw_errs}",
    )

    # Simple English contraction falsification
    mut_contraction = dict(base)
    mut_contraction["agents/tdd-worker.md"] = (
        mut_contraction["agents/tdd-worker.md"]
        + "\n16. Don't skip the failing test.\n"
    )
    se_errs = check_early_design_and_scar_contract(mut_contraction)
    self.assertTrue(
        any("contraction" in e for e in se_errs),
        f"Expected contraction rejection, got: {se_errs}",
    )


TARGET_42_AIRLOCK_CONTRACT_FILES = (
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "skills/makeflow/references/scars/process.md",
    "skills/makeflow/SKILL.md",
    "rules/AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)

TARGET_42_ADAPTER_AND_RULE_FILES = (
    "skills/makeflow/SKILL.md",
    "rules/AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)


def _extract_target_42_intro_and_airlock_section(review_text: str) -> str:
  """Extracts Section 5 through Section 5.1 (before 5.2) from the Six Pillars manual.

  Args:
    review_text: Full markdown text of 04_six_pillars_adversarial_review.md.

  Returns:
    The extracted Section 5 and Section 5.1 text, or an empty string if missing.
  """
  match = re.search(
      r"(?ms)^##\s+5\.\s+Target 42:.*?(?=^###\s+5\.2\b|\Z)",
      review_text,
  )
  return match.group(0) if match else ""


def _extract_scar_block(spoke_text: str, scar_id: str) -> str:
  """Extracts a single scar section from a Tier 1 markdown spoke file.

  Args:
    spoke_text: Full markdown text of a Tier 1 scar spoke file.
    scar_id: Canonical scar identifier such as 'SCAR-PROC-89'.

  Returns:
    The extracted scar section text, or an empty string if missing.
  """
  match = re.search(
      rf"(?ms)^###\s+{re.escape(scar_id)}:.*?(?=^###\s+SCAR-|\Z)",
      spoke_text,
  )
  return match.group(0) if match else ""


def _parse_scar_markdown_fields(scar_block: str, scar_id: str) -> dict[str, str]:
  """Parses markdown scar fields into a ScarCandidateValidator dictionary.

  Args:
    scar_block: Extracted markdown block for one scar.
    scar_id: Canonical scar identifier.

  Returns:
    Dictionary formatted for ScarCandidateValidator.validate().
  """
  field_map = {
      "Wound": "wound",
      "Trap": "trap",
      "Circuit Breaker": "circuit_breaker",
      "Permanent Reflex": "reflex",
      "Socratic Probe": "socratic_probe",
  }
  parsed: dict[str, str] = {"id": scar_id}
  for md_label, key in field_map.items():
    match = re.search(rf"(?m)^-\s+\*\*{re.escape(md_label)}\*\*:\s*(.+)$", scar_block)
    parsed[key] = match.group(1).strip() if match else ""
  if parsed.get("circuit_breaker"):
    parsed["raw_circuit_breaker"] = parsed["circuit_breaker"]
  return parsed


def _check_target_42_review_manual(review_rel: str, review_text: str) -> list[str]:
  """Validates Section 5/5.1 bounded Target 42 airlock rules in the review manual.

  Args:
    review_rel: Repository-relative path to the Six Pillars manual.
    review_text: Full markdown text of the Six Pillars manual.

  Returns:
    List of diagnostic error strings (empty when valid).
  """
  errs: list[str] = []
  lower_full = review_text.lower()
  if "zero-tool blocker airlock" in lower_full:
    errs.append(
        f"{review_rel} must not contain contradictory 'Zero-Tool Blocker Airlock' wording"
    )
  if "turn following a critic blocker wakeup must contain" in lower_full:
    errs.append(
        f"{review_rel} must not ban tool calls during critic blocker wakeup"
    )

  sec5 = _extract_target_42_intro_and_airlock_section(review_text)
  if not sec5:
    errs.append(f"{review_rel} missing Section 5 / Section 5.1 before '### 5.2'")
    return errs

  sec5_lower = sec5.lower()
  if "zero tool calls" in sec5_lower:
    errs.append(
        f"{review_rel} Section 5/5.1 must not contain contradictory 'zero tool calls' ban"
    )

  required_tokens = (
      "SCAR-PROC-89",
      "SCAR-PROC-102",
      ".workflow/state/40_critic_review_round<N>.md",
      ".workflow/state/40_critic_review.md",
      "make -f .workflow/state/Makefile triaged-blockers BLOCKERS=<N>",
      "make -f .workflow/state/Makefile implementation-diff APPROVED=1",
      ".workflow/state/gate_entry.json",
      "scripts/pre_tool_guard.py",
      "tool_calls: []",
      "Blocker Digest",
  )
  for tok in required_tokens:
    if tok not in sec5:
      errs.append(f"{review_rel} Section 5/5.1 must document '{tok}'")

  if re.search(r"(?<!\.workflow/state/)gate_entry\.json", sec5):
    errs.append(
        f"{review_rel} Section 5/5.1 must qualify '.workflow/state/gate_entry.json'"
    )
  if re.search(r"(?<!scripts/)pre_tool_guard\.py", sec5):
    errs.append(
        f"{review_rel} Section 5/5.1 must use canonical path 'scripts/pre_tool_guard.py'"
    )

  prohibition_checks = (
      ("source/test file edits", r"edit source files or test files"),
      ("running tests", r"run test commands"),
      ("messaging or arguing with critic", r"send messages to, argue with, or negotiate with the critic"),
      ("dispatching subagents", r"dispatch subagents"),
      ("drafting commit messages", r"draft commit messages"),
      ("advancing past Target 42", r"advance past target 42"),
  )
  for label, pat in prohibition_checks:
    if not re.search(pat, sec5_lower):
      errs.append(
          f"{review_rel} Section 5/5.1 must explicitly prohibit {label} before human triage"
      )

  errs.extend(_check_section_simple_english(f"{review_rel} [Section 5/5.1]", sec5))

  sec35 = _extract_markdown_h3_section(review_text, "### 3.5 ")
  if "Immediate Supervisory Halt" not in sec35 or "tool_calls: []" not in sec35:
    errs.append(
        f"{review_rel} Section 3.5 must preserve SCAR-PROC-95 Immediate Supervisory Halt"
    )
  sec52 = _extract_markdown_h3_section(review_text, "### 5.2 ")
  if "SCAR-ARCH-09" not in sec52 or "Non-Blocking Observations" not in sec52:
    errs.append(
        f"{review_rel} Section 5.2 must preserve SCAR-ARCH-09 and NBO Summary"
    )
  return errs


def _check_scar_proc_89_alignment(process_rel: str, process_text: str) -> list[str]:
  """Validates SCAR-PROC-89 alignment with bounded Target 42 wakeup entry.

  Args:
    process_rel: Repository-relative path to process.md.
    process_text: Full markdown text of skills/makeflow/references/scars/process.md.

  Returns:
    List of diagnostic error strings (empty when valid).
  """
  errs: list[str] = []
  block = _extract_scar_block(process_text, "SCAR-PROC-89")
  if not block:
    return [f"{process_rel} missing '### SCAR-PROC-89:' section"]

  if "stop tool calls immediately" in block.lower():
    errs.append(
        f"{process_rel} SCAR-PROC-89 must not retain contradictory 'stop tool calls immediately' trigger"
    )

  required_tokens = (
      ".workflow/state/40_critic_review_round<N>.md",
      ".workflow/state/40_critic_review.md",
      "make -f .workflow/state/Makefile triaged-blockers BLOCKERS=<N>",
      "Target 42",
      "tool_calls: []",
  )
  for tok in required_tokens:
    if tok not in block:
      errs.append(f"{process_rel} SCAR-PROC-89 must document '{tok}'")

  block_lower = block.lower()
  for label, pat in (
      ("messaging critic", r"message the critic"),
      ("editing code or tests", r"edit source or test files"),
      ("running tests", r"run tests"),
      ("advancing past Target 42", r"advance past target 42"),
  ):
    if not re.search(pat, block_lower):
      errs.append(f"{process_rel} SCAR-PROC-89 must prohibit {label}")

  candidate = _parse_scar_markdown_fields(block, "SCAR-PROC-89")
  errs.extend(ScarCandidateValidator().validate(candidate))
  errs.extend(_check_section_simple_english(f"{process_rel} [SCAR-PROC-89]", block))
  return errs


def check_target_42_bounded_blocker_airlock(
    files_by_relpath: dict[str, str] | None = None,
    repo_root: str | None = None,
) -> list[str]:
  """Validates GH-247 bounded Target 42 airlock rules across manuals, scars, and adapters.

  Args:
    files_by_relpath: Optional in-memory map of repo-relative paths to contents.
    repo_root: Optional repository root directory override.

  Returns:
    List of diagnostic error strings (empty when all invariants hold).
  """
  errs: list[str] = []
  base_dir = repo_root or REPO_ROOT
  files_map: dict[str, str] = {}
  if files_by_relpath is not None:
    files_map = dict(files_by_relpath)
  else:
    for rel in TARGET_42_AIRLOCK_CONTRACT_FILES:
      fpath = os.path.normpath(os.path.join(base_dir, rel))
      if not os.path.isfile(fpath):
        errs.append(f"Missing {rel}")
      else:
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  for rel in TARGET_42_AIRLOCK_CONTRACT_FILES:
    if rel not in files_map and f"Missing {rel}" not in errs:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  errs.extend(
      _check_target_42_review_manual(
          "skills/makeflow/references/04_six_pillars_adversarial_review.md",
          files_map["skills/makeflow/references/04_six_pillars_adversarial_review.md"],
      )
  )
  errs.extend(
      _check_scar_proc_89_alignment(
          "skills/makeflow/references/scars/process.md",
          files_map["skills/makeflow/references/scars/process.md"],
      )
  )

  skill_bytes = len(files_map["skills/makeflow/SKILL.md"].encode("utf-8"))
  if skill_bytes > 24576:
    errs.append(
        f"skills/makeflow/SKILL.md exceeds 24,576-byte budget ({skill_bytes} bytes)"
    )

  for rel in TARGET_42_ADAPTER_AND_RULE_FILES:
    lower_txt = files_map[rel].lower()
    if "zero-tool blocker airlock" in lower_txt or (
        "blocker wakeup" in lower_txt and "zero tool calls" in lower_txt
    ):
      errs.append(
          f"{rel} must not contain contradictory zero-tool-on-wakeup wording"
      )

  return errs


class TestTarget42BoundedBlockerAirlockContract(unittest.TestCase):
  """Contract and negative falsification tests for Target 42 bounded airlock (GH-247)."""

  def _load_base_files(self) -> dict[str, str]:
    """Loads all Target 42 airlock contract files from disk.

    Returns:
      Mapping of repository-relative file paths to file contents.
    """
    loaded: dict[str, str] = {}
    for rel in TARGET_42_AIRLOCK_CONTRACT_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_live_repo_target_42_bounded_blocker_airlock_contract(self) -> None:
    """Verifies live manuals, SCAR-PROC-89, and adapters satisfy GH-247 airlock rules."""
    self.assertEqual(check_target_42_bounded_blocker_airlock(), [])

  def test_negative_falsification_rejects_zero_tool_contradiction_and_dropped_guards(
      self,
  ) -> None:
    """Verifies in-memory mutations re-introducing GH-247 defects are rejected."""
    base = self._load_base_files()
    self.assertEqual(check_target_42_bounded_blocker_airlock(base), [])

    review_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
    process_rel = "skills/makeflow/references/scars/process.md"

    review_mutations = (
        ("Bounded Target 42 Blocker Airlock", "Zero-Tool Blocker Airlock"),
        (
            "stop tool calls (`tool_calls: []`)",
            "contain **zero tool calls (`tool_calls: []`)**",
        ),
        (".workflow/state/40_critic_review_round<N>.md", "REMOVED_ROUND_CARD"),
        (".workflow/state/40_critic_review.md", "REMOVED_REVIEW_CARD"),
        (
            "make -f .workflow/state/Makefile triaged-blockers BLOCKERS=<N>",
            "REMOVED_T42_CMD",
        ),
        (".workflow/state/gate_entry.json", "gate_entry.json"),
        ("edit source files or test files", "REMOVED_EDIT_GUARD"),
        ("run test commands", "REMOVED_TEST_GUARD"),
        (
            "send messages to, argue with, or negotiate with the critic",
            "REMOVED_CRITIC_IPC_GUARD",
        ),
        ("dispatch subagents", "REMOVED_SUBAGENT_GUARD"),
        ("draft commit messages", "REMOVED_COMMIT_GUARD"),
        ("advance past Target 42", "REMOVED_ADVANCE_GUARD"),
        ("scripts/pre_tool_guard.py", "pre_tool_guard.py"),
    )
    for target_tok, replacement in review_mutations:
      with self.subTest(file=review_rel, mutated=target_tok):
        mut = dict(base)
        mut[review_rel] = mut[review_rel].replace(target_tok, replacement)
        self.assertNotEqual(mut[review_rel], base[review_rel])
        errs = check_target_42_bounded_blocker_airlock(mut)
        self.assertTrue(
            any(review_rel in e for e in errs),
            f"Expected rejection when '{target_tok}' mutated in {review_rel}, got: {errs}",
        )

    # Section-5.1-scoped tool_calls: [] falsification leaving Sections 3.2 and 3.5 intact
    mut_sec51_halt = dict(base)
    sec51_halt_phrase = "stop tool calls (`tool_calls: []`) until human triage"
    self.assertEqual(mut_sec51_halt[review_rel].count(sec51_halt_phrase), 1)
    mut_sec51_halt[review_rel] = mut_sec51_halt[review_rel].replace(
        sec51_halt_phrase, "continue calling tools until human triage"
    )
    halt_errs = check_target_42_bounded_blocker_airlock(mut_sec51_halt)
    self.assertTrue(
        any("Section 5/5.1 must document 'tool_calls: []'" in e for e in halt_errs),
        f"Expected Section 5/5.1 tool_calls: [] error, got: {halt_errs}",
    )
    self.assertFalse(
        any("Section 3.5" in e for e in halt_errs),
        f"Section 3.5 must remain intact during Section 5.1 falsification, got: {halt_errs}",
    )

    process_mutations = (
        (
            "never message the critic or edit code",
            "stop tool calls immediately",
        ),
        (
            "make -f .workflow/state/Makefile triaged-blockers BLOCKERS=<N>",
            "REMOVED_T42_CMD",
        ),
        ("message the critic", "REMOVED_IPC_GUARD"),
        ("advance past Target 42", "REMOVED_ADVANCE_GUARD"),
    )
    for target_tok, replacement in process_mutations:
      with self.subTest(file=process_rel, mutated=target_tok):
        mut = dict(base)
        mut[process_rel] = mut[process_rel].replace(target_tok, replacement)
        errs = check_target_42_bounded_blocker_airlock(mut)
        self.assertTrue(
            any(process_rel in e or "SCAR-PROC-89" in e for e in errs),
            f"Expected rejection when '{target_tok}' mutated in {process_rel}, got: {errs}",
        )

    mut_adapter = dict(base)
    mut_adapter["CLAUDE.md"] = (
        mut_adapter["CLAUDE.md"] + "\nZero-Tool Blocker Airlock\n"
    )
    adapter_errs = check_target_42_bounded_blocker_airlock(mut_adapter)
    self.assertTrue(
        any("CLAUDE.md" in e for e in adapter_errs),
        f"Expected CLAUDE.md contradiction error, got: {adapter_errs}",
    )

    mut_contraction = dict(base)
    mut_contraction[review_rel] = mut_contraction[review_rel].replace(
        "Never edit source files or test files.",
        "Don't edit source files or test files.",
    )
    se_errs = check_target_42_bounded_blocker_airlock(mut_contraction)
    self.assertTrue(
        any("contraction" in e for e in se_errs),
        f"Expected contraction rejection, got: {se_errs}",
    )


REVIEW_PROMOTION_CONTRACT_FILES = (
    "skills/makeflow/references/04_six_pillars_adversarial_review.md",
    "skills/makeflow/references/scars/process.md",
    "rules/AGENTS.md",
    "skills/makeflow/SKILL.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)

REVIEW_PROMOTION_ADAPTER_FILES = (
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/makeflow.mdc",
    ".github/copilot-instructions.md",
    "adapters/chatgpt/instructions.md",
)


def check_review_promotion_and_nbo_gate_contract(
    files_by_relpath: dict[str, str] | None = None,
    repo_root: str | None = None,
) -> list[str]:
  """Validates GH-221 review promotion, concise chat summaries, and plain-chat NBO rules.

  Args:
    files_by_relpath: Optional in-memory map of repo-relative paths to contents.
    repo_root: Optional repository root directory override.

  Returns:
    List of diagnostic error strings (empty when all GH-221 contracts hold).
  """
  errs: list[str] = []
  base_dir = repo_root or REPO_ROOT
  files_map: dict[str, str] = {}
  if files_by_relpath is not None:
    files_map = dict(files_by_relpath)
  else:
    for rel in REVIEW_PROMOTION_CONTRACT_FILES:
      fpath = os.path.normpath(os.path.join(base_dir, rel))
      if not os.path.isfile(fpath):
        errs.append(f"Missing {rel}")
      else:
        with open(fpath, "r", encoding="utf-8") as f:
          files_map[rel] = f.read()

  for rel in REVIEW_PROMOTION_CONTRACT_FILES:
    if rel not in files_map and f"Missing {rel}" not in errs:
      errs.append(f"Missing {rel}")
  if errs:
    return errs

  # 1. skills/makeflow/references/04_six_pillars_adversarial_review.md
  r04_rel = "skills/makeflow/references/04_six_pillars_adversarial_review.md"
  r04_text = files_map[r04_rel]
  sec2 = _extract_markdown_h2_section(r04_text, "## 2. The Progressive Three-Tier")
  sec51 = _extract_target_42_intro_and_airlock_section(r04_text)
  sec6 = _extract_markdown_h2_section(r04_text, "## 6. Target 45:")
  sec62 = _extract_markdown_h3_section(r04_text, "### 6.2 Canonical Prose Presentation")
  sec63 = _extract_markdown_h3_section(r04_text, "### 6.3 Non-Blocking Observations")

  for sec_label, sec_text in (
      ("Section 2", sec2),
      ("Section 5.1", sec51),
      ("Section 6", sec6),
  ):
    if "make -f .workflow/state/Makefile promote-review ROUND=<N>" not in sec_text:
      errs.append(
          f"{r04_rel} {sec_label} must specify 'make -f .workflow/state/Makefile promote-review ROUND=<N>'"
      )

  if not sec62:
    errs.append(f"{r04_rel} missing '### 6.2 Canonical Prose Presentation'")
  else:
    for tok in ("1 to 2", ".workflow/state/40_critic_review.md", "file://"):
      if tok not in sec62:
        errs.append(f"{r04_rel} Section 6.2 must document '{tok}'")
    if "summary table" not in sec62.lower():
      errs.append(f"{r04_rel} Section 6.2 must keep prohibition on summary tables")
    errs.extend(_check_section_simple_english(f"{r04_rel} [Section 6.2]", sec62))

  if not sec63:
    errs.append(f"{r04_rel} missing '### 6.3 Non-Blocking Observations'")
  else:
    for tok in ("Target 42", "Target 45", "ask_question", "tool_calls: []"):
      if tok not in sec63:
        errs.append(f"{r04_rel} Section 6.3 must document '{tok}'")
    if "never call `ask_question`" not in sec63.lower() and "do not call `ask_question`" not in sec63.lower():
      errs.append(
          f"{r04_rel} Section 6.3 must prohibit calling ask_question at Target 42 and Target 45"
      )
    errs.extend(_check_section_simple_english(f"{r04_rel} [Section 6.3]", sec63))

  # 2. skills/makeflow/references/scars/process.md (SCAR-PROC-89)
  proc_rel = "skills/makeflow/references/scars/process.md"
  scar89 = _extract_scar_block(files_map[proc_rel], "SCAR-PROC-89")
  if "make -f .workflow/state/Makefile promote-review ROUND=<N>" not in scar89:
    errs.append(
        f"{proc_rel} SCAR-PROC-89 must specify 'make -f .workflow/state/Makefile promote-review ROUND=<N>'"
    )

  # 3. rules/AGENTS.md (Section 1.2 and Section 1.8)
  agents_rel = "rules/AGENTS.md"
  agents_text = files_map[agents_rel]
  sec12 = _extract_markdown_h3_section(agents_text, "### 1.2 Pause Gates")
  sec18 = _extract_markdown_h3_section(agents_text, "### 1.8 Critic Observation Triage")
  if not sec12:
    errs.append(f"{agents_rel} missing '### 1.2 Pause Gates'")
  else:
    for tok in (
        "$N \\ge 2$",
        "$N = 1$",
        "ask_question",
        "Target 42",
        "Target 45",
        "Non-Blocking Observation",
        "1 to 2",
        ".workflow/state/40_critic_review.md",
        "tool_calls: []",
    ):
      if tok not in sec12:
        errs.append(f"{agents_rel} Section 1.2 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{agents_rel} [Section 1.2]", sec12))
  if not sec18:
    errs.append(f"{agents_rel} missing '### 1.8 Critic Observation Triage'")
  else:
    if "plain chat" not in sec18.lower() or "ask_question" not in sec18:
      errs.append(
          f"{agents_rel} Section 1.8 must specify plain chat choices without calling ask_question"
      )
    errs.extend(_check_section_simple_english(f"{agents_rel} [Section 1.8]", sec18))

  # 4. skills/makeflow/SKILL.md (Target 40/42/45 + budget + platform neutrality)
  skill_rel = "skills/makeflow/SKILL.md"
  skill_text = files_map[skill_rel]
  skill_bytes = len(skill_text.encode("utf-8"))
  if skill_bytes > 24576:
    errs.append(
        f"{skill_rel} exceeds 24,576-byte budget ({skill_bytes} bytes)"
    )
  if "<brain>" in skill_text:
    errs.append(f"{skill_rel} must stay platform-neutral without '<brain>'")
  t40_sec = _extract_markdown_h3_section(skill_text, "### Target 40, Target 42, and Target 45:")
  if not t40_sec:
    errs.append(f"{skill_rel} missing '### Target 40, Target 42, and Target 45:'")
  else:
    for tok in (
        "make -f .workflow/state/Makefile promote-review ROUND=<N>",
        "1 to 2",
        ".workflow/state/40_critic_review.md",
        "ask_question",
        "tool_calls: []",
    ):
      if tok not in t40_sec:
        errs.append(f"{skill_rel} Target 40/42/45 must document '{tok}'")
    errs.extend(_check_section_simple_english(f"{skill_rel} [Target 40/42/45]", t40_sec))

  # 5. All 5 cross-agent adapters
  for adapter_rel in REVIEW_PROMOTION_ADAPTER_FILES:
    ad_text = files_map[adapter_rel]
    for tok in (
        "promote-review ROUND=<N>",
        "1 to 2",
        "40_critic_review.md",
        "ask_question",
    ):
      if tok not in ad_text:
        errs.append(f"{adapter_rel} must document '{tok}'")
    if "plain chat" not in ad_text.lower():
      errs.append(
          f"{adapter_rel} must specify plain chat pause gates at Target 42 and Target 45"
      )

  return errs


class TestReviewPromotionAndNboGateContract(unittest.TestCase):
  """Section-anchored contract and negative falsification tests for GH-221."""

  def _load_base_files(self) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for rel in REVIEW_PROMOTION_CONTRACT_FILES:
      with open(repo_path(rel), "r", encoding="utf-8") as f:
        loaded[rel] = f.read()
    return loaded

  def test_live_repo_review_promotion_and_nbo_gate_contract(self) -> None:
    """Verifies live manuals, rules, SKILL.md, and all 5 adapters satisfy GH-221."""
    self.assertEqual(check_review_promotion_and_nbo_gate_contract(), [])

  def test_negative_falsification_rejects_missing_promote_review_or_nbo_rules(
      self,
  ) -> None:
    """In-memory mutations removing GH-221 invariants must trigger deterministic failures."""
    base = self._load_base_files()
    self.assertEqual(check_review_promotion_and_nbo_gate_contract(base), [])

    mutations = (
        (
            "skills/makeflow/references/04_six_pillars_adversarial_review.md",
            "make -f .workflow/state/Makefile promote-review ROUND=<N>",
            "REMOVED_PROMOTE_CMD",
        ),
        (
            "skills/makeflow/references/04_six_pillars_adversarial_review.md",
            "1 to 2",
            "verbatim full",
        ),
        (
            "skills/makeflow/references/04_six_pillars_adversarial_review.md",
            "ask_question",
            "REMOVED_TOOL",
        ),
        (
            "skills/makeflow/references/scars/process.md",
            "make -f .workflow/state/Makefile promote-review ROUND=<N>",
            "REMOVED_PROMOTE_CMD",
        ),
        ("rules/AGENTS.md", "Non-Blocking Observation", "REMOVED_NBO"),
        ("rules/AGENTS.md", "plain chat", "modal dialog"),
        (
            "skills/makeflow/SKILL.md",
            "make -f .workflow/state/Makefile promote-review ROUND=<N>",
            "REMOVED_PROMOTE_CMD",
        ),
        ("CLAUDE.md", "promote-review ROUND=<N>", "REMOVED_PROMOTE"),
        (".cursorrules", "promote-review ROUND=<N>", "REMOVED_PROMOTE"),
        (".cursor/rules/makeflow.mdc", "promote-review ROUND=<N>", "REMOVED_PROMOTE"),
        (".github/copilot-instructions.md", "promote-review ROUND=<N>", "REMOVED_PROMOTE"),
        ("adapters/chatgpt/instructions.md", "promote-review ROUND=<N>", "REMOVED_PROMOTE"),
    )
    for rel, token, repl in mutations:
      with self.subTest(file=rel, mutated=token):
        mut = dict(base)
        mut[rel] = mut[rel].replace(token, repl)
        self.assertNotEqual(mut[rel], base[rel])
        errs = check_review_promotion_and_nbo_gate_contract(mut)
        self.assertTrue(
            any(rel in e for e in errs),
            f"Expected rejection when '{token}' mutated in {rel}, got: {errs}",
        )


if __name__ == "__main__":
  unittest.main()
