# Tooling, HCI, and Documentation Scars

This reference manual documents developer tooling, interaction patterns, prompt gates, and documentation architecture scars (`SCAR-TOOL-*`, `SCAR-HCI-*`, and `SCAR-DOC-*`).

## Scars in this Category

| ID | Title | Scope |
| :--- | :--- | :--- |
| `SCAR-TOOL-07` | Prefer Dart Over Python for Scratch Scripts | Temporary Scripts & Escaping |
| `SCAR-TOOL-13` | Single State Writer Isolation | Subagent State File Concurrency |
| `SCAR-TOOL-14` | Programmatic Tool Polling vs Subagent Scrape Loops | Remote Polling & Subagent Latency |
| `SCAR-TOOL-15` | Anchored State Marker Detection vs Substring Matching | Workflow State Parsing |
| `SCAR-TOOL-16` | Prohibit Arbitrary Output Limits and Require Volume Triage Interview | Information Triage & Output Limits |
| `SCAR-HCI-08` | Multi-Choice Prompt Rule | User Interaction & Choice Gates |
| `SCAR-DOC-16` | Documentation Tree (Progressive Disclosure) | Layered Architecture & Readability |

---

### SCAR-TOOL-07: Prefer Installed Dart and Temporary Files for Scratch Scripts

- **Wound**: Inline shell scripts with nested quoting fail or corrupt strings, and Python scratch scripts fail on missing packages or virtual environment restrictions.
- **Trap**: Defaulting blindly to inline multiline Python commands with complex shell escapes instead of checking for installed Dart or using script files.
- **Permanent Reflex**: If Dart is installed and the language is not specified, prefer Dart over Python for scratch scripts. If Dart is not installed, use Python without attempting to install Dart. If shell commands require complex string escaping, write the script to a file in a temporary directory. Before writing the temporary script, call `workflow_query_scars` for the selected language (`dart` or `python`).
- **Socratic Probe**: Will long inline scripts run directly in a shell risk string escape defects or environment conflicts? Did you call `workflow_query_scars` for the selected temporary script language (`dart` or `python`) before writing the script?

### SCAR-TOOL-13: Single State Writer Isolation

- **Wound**: Child subagents modify `.workflow/state/*.md` files concurrently with the orchestrator, corrupting state and causing race conditions.
- **Trap**: Allowing child subagents direct filesystem writes to workflow state cards instead of one atomic boundary.
- **Permanent Reflex**: Only the Depth-0 supervisor writes `.workflow/state/` files directly. Child subagents submit validated JSON reports via MCP `workflow_submit_report`. The server writes canonical `.json` and rendered `.md` files atomically while the supervisor halts (`SCAR-PROC-95`).
- **Socratic Probe**: If child subagents edit state files directly, will concurrent writes corrupt workflow state? Do subagents submit reports through atomic MCP `workflow_submit_report` calls while the supervisor halts?

### SCAR-HCI-08: Multi-Choice Prompt Rule

- **Wound**: Calling `ask_question` on single-option gates or opening modal prompts without printing the full proposal in chat degrades user experience.
- **Trap**: Using choice widgets for simple single-action approvals.
- **Circuit Breaker**: If a decision has fewer than two concrete alternatives, never call `ask_question`. Print the full proposal in chat with clickable links and stop tool calls.
- **Permanent Reflex**: Call `ask_question` only when two or more choices exist ($N \ge 2$). For single approvals ($N = 1$), print the proposal in chat and stop tool calls.
- **Socratic Probe**: If there is only one approval action, does opening a multiple-choice widget add unnecessary friction? Should you present the full proposal in chat with file links and reserve `ask_question` for multiple alternatives?

### SCAR-DOC-16: Documentation Tree (Progressive Disclosure)

- **Wound**: Deep implementation mechanics mixed into top-level user guides overwhelm readers and cause prompt token exhaustion.
- **Trap**: Treating all documentation as a single flat manual without progressive disclosure.
- **Permanent Reflex**: Organize documentation as a progressive disclosure tree: Root Overview and Index, Branch Workflow Guides, and Leaf API Reference Manuals.
- **Socratic Probe**: If you put deep internals into root guides, will readers suffer context overload? Can you use a progressive disclosure tree?

### SCAR-TOOL-14: Programmatic Tool Polling vs Subagent Scrape Loops

- **Wound**: Polling pull request status checks and review bot comments by delegating to a read-only subagent triggered 29 sequential HTTP API round-trips via `read_url_content` and 89 seconds of model inference latency.
- **Trap**: Assuming subagents can poll remote endpoints efficiently without recognizing that read-only subagents lack shell tools and must scrape REST APIs across dozens of sequential model turns.
- **Permanent Reflex**: Keep mechanical polling loops inside programmatic native tools or scripts (`workflow_poll_ci`). Invoke subagents only for single-turn cognitive evaluation after pre-fetching data.
- **Socratic Probe**: If a read-only subagent polls an API across dozens of turns, will it waste context and add severe latency? Can you use native programmatic tools to poll status quickly, invoking subagents only for single-turn analysis?

### SCAR-TOOL-15: Anchored State Marker Detection vs Substring Matching

- **Wound**: Coarse substring checks on workflow state files (`"Target 90" in txt` or `"finish" in txt.lower()`) falsely matched static milestone checklists in state card templates, suppressing review checks on feature branches.
- **Trap**: Assuming substring matching is sufficient to identify active workflow targets without recognizing that milestone templates contain all target names.
- **Permanent Reflex**: When parsing workflow state files, never use unanchored substring matching. Always use anchored regular expressions that match the active target line or active milestone marker (such as `re.search(r"(?im)^-\s*Active Target:\s*...", txt)`).
- **Socratic Probe**: If you test for target names with loose substring matching, will milestone checklists trigger false positive transitions? Are you using anchored regular expressions to match only the active target header?

### SCAR-TOOL-16: Prohibit Arbitrary Output Limits and Require Volume Triage Interview

- **Wound**: An agent investigates or triages an information source and unilaterally restricts output with an arbitrary limit argument. The capped output omits vital records, leading to an incomplete assessment and flawed conclusions.
- **Trap**: The agent assumes high output volume will exhaust context. It adds arbitrary limits instead of inspecting total volume or consulting the user.
- **Circuit Breaker**: If you investigate or triage an information source, never append arbitrary limit arguments. If high volume threatens context limits, halt tool execution immediately and interview the user.
- **Permanent Reflex**: Never arbitrarily impose `--limit` or truncation flags before assessing the full dataset. If you suspect output volume will crowd context or make the directive unfeasible, report the volume risk first. Interview the user to select one of four paths:
  1. Revise the directive or filter criteria.
  2. Impose explicit user-approved limits.
  3. Count total rows first before reading detailed content.
  4. Proceed without limits.
- **Socratic Probe**: Did you arbitrarily impose a `--limit` flag on an information source during triage? If output volume might crowd context, did you stop to count rows and interview the user before restricting data?


