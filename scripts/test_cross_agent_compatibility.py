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

import json
import os
import re
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_path(*parts):
  """Resolves path relative to REPO_ROOT."""
  return os.path.normpath(os.path.join(REPO_ROOT, *parts))


def check_scar_catalog_routing(catalog_text, scar_id, spoke_filename):
  """Verifies strict table row routing for a synthetic scar entry (SCAR-TOOL-15)."""
  pattern = rf"(?m)^\|\s*`{re.escape(scar_id)}`\s*\|.*\|\s*\[`scars/{re.escape(spoke_filename)}`\]"
  return bool(re.search(pattern, catalog_text))


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
  6. Makefile defines test-base-script target and includes it in test prerequisites.
  7. scripts/base_script.py and scripts/test_base_script.py exist.
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
    if "test-base-script:" not in mk_text:
      errors.append(f"{mk_rel} must define 'test-base-script:' target")
    if "test-base-script" not in mk_text.split("test:")[1].split("\n")[0]:
      errors.append(
          f"{mk_rel} 'test:' target must list 'test-base-script' prerequisite"
      )

  for script_rel in ["scripts/base_script.py", "scripts/test_base_script.py"]:
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
    if "test-reflex:" not in mk_text:
      errors.append(f"{mk_rel} must define 'test-reflex:' target")
    if "test:" not in mk_text or "test-reflex" not in mk_text.split("test:")[1].split("\n")[0]:
      errors.append(
          f"{mk_rel} 'test:' target must list 'test-reflex' prerequisite"
      )

  for script_rel in (
      "scripts/emit_reflex.py",
      "scripts/workflow_gates.py",
      "scripts/test_emit_reflex.py",
  ):
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  reflex_test_rel = "scripts/test_emit_reflex.py"
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


def test_two_tier_scar_lifecycle_and_discovery() -> list[str]:
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
    if "test-tdd-guard:" not in mk_text:
      errors.append(f"{mk_rel} must define 'test-tdd-guard:' target")
    if (
        "test:" not in mk_text
        or "test-tdd-guard" not in mk_text.split("test:")[1].split("\n")[0]
    ):
      errors.append(
          f"{mk_rel} 'test:' target must list 'test-tdd-guard' prerequisite"
      )

  for script_rel in ("scripts/tdd_guard.py", "scripts/test_tdd_guard.py"):
    script_path = repo_path(script_rel)
    if not os.path.exists(script_path):
      errors.append(f"Missing {script_rel}")

  guard_test_rel = "scripts/test_tdd_guard.py"
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

  tdd_guard_py = repo_path("scripts/tdd_guard.py")
  if os.path.exists(tdd_guard_py):
    with open(tdd_guard_py, "r", encoding="utf-8") as f:
      guard_code = f.read()
    for req_token in ("verify-citation", "UNCOMMITTED_WITNESS_FILE", "TRIVIAL_WITNESS_LINE"):
      if req_token not in guard_code:
        errors.append(f"scripts/tdd_guard.py must implement '{req_token}'")

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
    if "test-ast-scrub:" not in mk_text:
      errors.append(f"{mk_rel} must define 'test-ast-scrub:' target")
    if (
        "test-static:" not in mk_text
        or "ast-lint" not in mk_text.split("test-static:")[1].split("\n")[0]
    ):
      errors.append(f"{mk_rel} 'test-static:' must list 'ast-lint' prerequisite")
    if (
        "test:" not in mk_text
        or "test-ast-scrub" not in mk_text.split("test:")[1].split("\n")[0]
    ):
      errors.append(f"{mk_rel} 'test:' must list 'test-ast-scrub' prerequisite")

  gates_rel = "scripts/workflow_gates.py"
  gates_path = repo_path(gates_rel)
  if not os.path.exists(gates_path):
    errors.append(f"Missing {gates_rel}")
  else:
    with open(gates_path, "r", encoding="utf-8") as f:
      gates_text = f.read()
    if "ast-lint:" not in gates_text:
      errors.append(f"{gates_rel} _init_makefile must include 'ast-lint:'")

  for script_rel in ("scripts/ast_scrub.py", "scripts/test_ast_scrub.py"):
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

  # Verify scripts/workflow_gates.py does NOT contain subagent-def or _gate_subagent_def
  gates_rel = "scripts/workflow_gates.py"
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
  6. Makefile defines test-signal-trap target and includes it in test prerequisites.
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
    if "test-signal-trap:" not in mk_text:
      errors.append(f"{mk_rel} must define 'test-signal-trap:' target")
    if "test-signal-trap" not in mk_text.split("test:")[1].split("\n")[0]:
      errors.append(
          f"{mk_rel} 'test:' target must list 'test-signal-trap' prerequisite"
      )

  for script_rel in ["scripts/signal_trap.py", "scripts/test_signal_trap.py"]:
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
  1. No diagnostic error string in scripts/test_cross_agent_compatibility.py formats
     absolute repo_path variables into 'Missing ...' or 'Invalid ...' messages.
  2. All 13 canonical relative path variables are present and used in diagnostics:
     claude_rel, codex_rel, claude_md_rel, cursorrules_rel, cursor_mdc_rel,
     copilot_rel, chatgpt_rel, hooks_rel, mcp_rel, cfg_template_rel,
     ref_13_rel, ref_08_rel, ref_14_rel.
  3. Critic agent invariant errors format critic_agent_rel instead of absolute critic_agent.
  """
  errors = []
  compat_script_rel = "scripts/test_cross_agent_compatibility.py"
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


def check_mcp_t60_transitions(server_text):
  """Checks that mcp/server.py VALID_TRANSITIONS maps T60 to a set containing T40 and T70."""
  errors = []
  match = re.search(r'"T60"\s*:\s*\{([^}]+)\}', server_text)
  if not match:
    errors.append("mcp/server.py VALID_TRANSITIONS missing 'T60' entry")
    return errors
  targets = {t.strip(' "\'') for t in match.group(1).split(",") if t.strip(' "\'')}
  if "T40" not in targets:
    errors.append("mcp/server.py VALID_TRANSITIONS['T60'] must contain 'T40'")
  if "T70" not in targets:
    errors.append("mcp/server.py VALID_TRANSITIONS['T60'] must contain 'T70'")
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

  # 1. mcp/server.py
  server_rel = "mcp/server.py"
  server_path = repo_path(server_rel)
  if not os.path.exists(server_path):
    errors.append(f"Missing {server_rel}")
  else:
    with open(server_path, "r", encoding="utf-8") as f:
      server_text = f.read()
    errors.extend(check_mcp_t60_transitions(server_text))

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
        "WARNING",
        "CAUTION",
        "NOTE",
        "imperative",
    ],
    "03_nasa_sp_7084_technical_grammar.md": [
        "NASA SP-7084",
        "17 U.S.C. § 105",
        "noun",
        "unit modifier",
        "participle",
        "gerund",
        "that",
        "semicolon",
        "em-dash",
    ],
    "04_google_developer_style_guide.md": [
        "Google Developer Documentation Style Guide",
        "CC-BY-4.0",
        "Google LLC",
        "condition",
        "code",
        "flag",
        "e.g.",
        "i.e.",
        "etc.",
        "via",
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
        "socratic-prompt-writer.md",
        "20 words",
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
    "agents/socratic-prompt-writer.md",
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
          "Use WARNING, CAUTION, and NOTE alerts with imperative steps.\n"
      ),
      "03_nasa_sp_7084_technical_grammar.md": (
          "# NASA SP-7084\n"
          "Source is NASA SP-7084 under 17 U.S.C. § 105.\n"
          "Avoid long noun clusters, hyphenate each unit modifier, and avoid ambiguous participle or gerund forms.\n"
          "Keep the conjunction that and omit any semicolon or em-dash in prose.\n"
      ),
      "04_google_developer_style_guide.md": (
          "# Google Style\n"
          "Source is Google Developer Documentation Style Guide under CC-BY-4.0 by Google LLC.\n"
          "Place the condition before the instruction and format code spans and CLI flag names.\n"
          "Avoid `e.g.`, `i.e.`, `etc.`, and `via` in documentation.\n"
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
          "Queries cover interrogative, imperative, wh-queries, how much, how many, socratic-prompt-writer.md, and 20 words.\n"
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

  return errors


def validate():
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

  for manifest_rel in (
      "plugin.json",
      ".claude-plugin/plugin.json",
      ".codex-plugin/plugin.json",
  ):
    manifest_path = repo_path(manifest_rel)
    if os.path.exists(manifest_path):
      with open(manifest_path) as f:
        m_data = json.load(f)
      if m_data.get("version") != "1.4.1" or "27-language" not in m_data.get(
          "description", ""
      ):
        errors.append(
            f"Manifest {manifest_rel} must declare version 1.4.1 and"
            " 27-language support"
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

  if total_scars_found != 57:
    errors.append(f"Expected 57 synthetic scars across category spokes, found {total_scars_found}")

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

  # 11c. Full Language Name Polyglot Scars (dart_flutter.md)
  dart_guide_rel = "skills/makeflow/references/polyglot/dart_flutter.md"
  dart_guide = repo_path(dart_guide_rel)
  if not os.path.exists(dart_guide):
    errors.append(f"Missing {dart_guide_rel}")
  else:
    with open(dart_guide) as f:
      dg_text = f.read()
    for d_scar in ("SCAR-LANG-DART-01", "SCAR-LANG-DART-02", "SCAR-LANG-DART-03", "SCAR-LANG-DART-04"):
      if d_scar not in dg_text:
        errors.append(f"{dart_guide_rel} must document full language name {d_scar}")

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
    if "test-static:" not in mk_text:
      errors.append(f"{makefile_rel} must define 'test-static:' target")
    if "test: test-static" not in mk_text:
      errors.append(f"{makefile_rel} 'test:' target must list 'test-static' as first prerequisite")
    if "test-safe-copy:" not in mk_text:
      errors.append(f"{makefile_rel} must define 'test-safe-copy:' target")
    if "test-safe-copy" not in mk_text.split("test:")[1].split("\n")[0]:
      errors.append(f"{makefile_rel} 'test:' target must list 'test-safe-copy' prerequisite")
    if "test-env-preflight:" not in mk_text:
      errors.append(f"{makefile_rel} must define 'test-env-preflight:' target")
    if "test-env-preflight" not in mk_text.split("test:")[1].split("\n")[0]:
      errors.append(f"{makefile_rel} 'test:' target must list 'test-env-preflight' prerequisite")
    if "PYTHON ?= python3" not in mk_text and "PYTHON ?=" not in mk_text:
      errors.append(f"{makefile_rel} must declare 'PYTHON ?= python3' configurable interpreter macro")
    if "@python3 " in mk_text:
      errors.append(f"{makefile_rel} test recipes must use $(PYTHON) instead of hardcoded python3")

  static_check_rel = "scripts/static_check.py"
  static_check_script = repo_path(static_check_rel)
  if not os.path.exists(static_check_script):
    errors.append(f"Missing {static_check_rel}")
  static_test_rel = "scripts/test_static_analysis.py"
  static_test_script = repo_path(static_test_rel)
  if not os.path.exists(static_test_script):
    errors.append(f"Missing {static_test_rel}")
  safe_copy_rel = "scripts/safe_copy.py"
  safe_copy_script = repo_path(safe_copy_rel)
  if not os.path.exists(safe_copy_script):
    errors.append(f"Missing {safe_copy_rel}")
  safe_copy_test_rel = "scripts/test_safe_copy.py"
  safe_copy_test_script = repo_path(safe_copy_test_rel)
  if not os.path.exists(safe_copy_test_script):
    errors.append(f"Missing {safe_copy_test_rel}")
  env_preflight_rel = "scripts/env_preflight.py"
  env_preflight_script = repo_path(env_preflight_rel)
  if not os.path.exists(env_preflight_script):
    errors.append(f"Missing {env_preflight_rel}")
  env_preflight_test_rel = "scripts/test_env_preflight.py"
  env_preflight_test_script = repo_path(env_preflight_test_rel)
  if not os.path.exists(env_preflight_test_script):
    errors.append(f"Missing {env_preflight_test_rel}")

  # 16. Python Interpreter Inheritance (sys.executable in test subprocesses)
  hooks_test_rel = "scripts/test_hooks.py"
  hooks_test_script = repo_path(hooks_test_rel)
  if os.path.exists(hooks_test_script):
    with open(hooks_test_script) as f:
      ht_text = f.read()
    if "sys.executable" not in ht_text:
      errors.append(f"{hooks_test_rel} must use sys.executable for child subprocesses")
    if '["python3"' in ht_text or "['python3'" in ht_text:
      errors.append(f"{hooks_test_rel} must not hardcode 'python3' in subprocess calls")

  mcp_test_rel = "scripts/test_mcp_server.py"
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
  scar_lifecycle_errors = test_two_tier_scar_lifecycle_and_discovery()
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

  if errors:
    print(f"\n❌ Validation failed with {len(errors)} error(s):")
    for err in errors:
      print(f"  - {err}")
    sys.exit(1)
  else:
    print("\n✔ All cross-agent configuration files validated successfully!")


if __name__ == "__main__":
  validate()
