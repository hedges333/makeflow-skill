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
| `SCAR-DOC-16` | Concentric Documentation Rings | Layered Architecture & Readability |

---

### SCAR-TOOL-07: Prefer Installed Dart and Temporary Files for Scratch Scripts

- **Wound**: Inline shell scripts with nested quoting fail or corrupt strings, and Python scratch scripts fail on missing packages or virtual environment restrictions.
- **Trap**: Defaulting blindly to inline multiline Python commands with complex shell escapes instead of checking for installed Dart or using script files.
- **Permanent Reflex**: If Dart is installed and the language is not specified, prefer Dart over Python for scratch scripts. If Dart is not installed, use Python without attempting to install Dart. If shell commands require complex string escaping, write the script to a file in a temporary directory.
- **Socratic Probe**: Will long inline scripts run directly in a shell risk string escape defects or environment conflicts? If Dart is installed, would a temporary Dart script run more reliably without virtual environment overhead? If Dart is not available, would a temporary script on the filesystem be more reliable?

### SCAR-TOOL-13: Single State Writer Isolation

- **Wound**: Child subagents modify `.workflow/state/*.md` files concurrently with the orchestrator, corrupting state and causing race conditions.
- **Trap**: Allowing child subagents write access to workflow state directory cards.
- **Permanent Reflex**: Only the Depth-0 supervisor writes `.workflow/state/*.md` cards. Subagents return concise Markdown summaries in their completion messages for the supervisor to record.
- **Socratic Probe**: If child subagents directly edit state files while the supervisor is running, will concurrent writes corrupt workflow state? Should only the supervisor write state cards while subagents return markdown summaries?

### SCAR-HCI-08: Multi-Choice Prompt Rule

- **Wound**: Calling `ask_question` on single-option gates or opening modal prompts without printing the full proposal in chat degrades user experience.
- **Trap**: Using choice widgets for simple single-action approvals.
- **Permanent Reflex**: Call `ask_question` only when two or more choices exist ($N \ge 2$). For single approvals ($N = 1$), print the proposal in chat and stop tool calls.
- **Socratic Probe**: If there is only one approval action, does opening a multiple-choice widget add unnecessary friction? Should you present the full proposal in chat with file links and reserve `ask_question` for multiple alternatives?

### SCAR-DOC-16: Concentric Documentation Rings

- **Wound**: Deep implementation mechanics mixed into top-level user guides overwhelm readers and cause prompt token exhaustion.
- **Trap**: Treating all documentation as a single flat manual without audience layering.
- **Permanent Reflex**: Layer documentation into concentric rings: Ring 1 (Surface Rules for agents and quick lookup), Ring 2 (Workflows and Procedures), and Ring 3 (Deep Architecture and Manuals).
- **Socratic Probe**: If you put deep architectural internals into surface quickstart guides, will readers and models suffer context overload? Can you layer documentation into concentric rings so readers access detail only as needed?

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


