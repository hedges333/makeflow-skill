# Process and Workflow Scars

This reference manual documents core process and workflow scars (`SCAR-PROC-*`). These rules govern orchestrator discipline, subagent boundaries, artifact lifecycles, and human approval gates.

## Scars in this Category

| ID | Title | Scope |
| :--- | :--- | :--- |
| `SCAR-PROC-45` | Subagent System Prompt Paraphrasing and Rubric Mutagenesis | Subagent Prompting & Rubric Integrity |
| `SCAR-PROC-53` | Prohibit Shell Sleep Polling | Subagent & Task Notification |
| `SCAR-PROC-89` | Subagent IPC Back-Channel Conflation and Reactive Wakeup Airlock | Critic Blocker Triage & IPC |
| `SCAR-PROC-95` | Depth-0 Supervisor and Subagent Isolation | Orchestration Architecture |
| `SCAR-PROC-99` | Single-Page SKILL.md Size Ceiling | Context Budget & Skill Files |
| `SCAR-PROC-102` | Human Declaration Authority | Pause Gate Enforcement |
| `SCAR-PROC-103` | Single Plan Artifact Lifecycle and Demotion | Antigravity Artifact Management |
| `SCAR-PROC-104` | Review Before Edit and Targeted Loops | Code Modification Discipline |
| `SCAR-PROC-105` | Explicit Critic Six Pillars Manual Path | Critic Subagent Dispatch |
| `SCAR-PROC-106` | Predetermined Subagent Tool Mounting via tools Lists | Subagent Tool Mounting |
| `SCAR-PROC-107` | Full Discussion and Comment Thread Ingestion at Intake | Ticket Intake & Triage |
| `SCAR-PROC-108` | Critic Observation Triage and Anti-Proliferation Protocol | Review Triage & Issue Management |
| `SCAR-PROC-110` | Premature Subagent Termination and Speculative Verdict Hallucination | Subagent Lifecycle & Verdicts |
| `SCAR-PROC-120` | Belnap Four-Valued Epistemic State Ledger and Fact Retraction | Fact State Ledger & Archaeology |
| `SCAR-PROC-122` | Upfront Human-Review Policy Evaluation Matrix | Landing Gate Policy Evaluation |
| `SCAR-PROC-138` | Yield on Repeated Timer Conflicts | Schedule Conflict Handling |
| `SCAR-PROC-150` | Target Transition Header Dead-Air Cliff | Automated Target Transitions |
| `SCAR-PROC-152` | Graphviz Attributed Graph IR for Mealy Automaton Completeness | State Machine Graph Completeness |
| `SCAR-PROC-158` | Persistent Memento Readerboard Truncation Trap | Persistent Memento State Integrity |

---

### SCAR-PROC-45: Subagent System Prompt Paraphrasing and Rubric Mutagenesis

- **Wound**: An orchestrator agent defines a review subagent but summarizes the review rubric in the prompt. The subagent misses security defects and accepts broken code.
- **Trap**: The model compresses instructions into conversational prose. It drops negative constraints that look redundant.
- **Circuit Breaker**: If you summarize a review rubric, stop immediately. You must use the unabridged text.
- **Permanent Reflex**: If you dispatch a critic subagent, mount the canonical review manual by exact file path. Instruct the subagent to read the manual directly from disk.
- **Socratic Probe**: Did you pass the exact path of the review manual, or did you paraphrase the review rules?

### SCAR-PROC-53: Prohibit Shell Sleep Polling

- **Wound**: Polling background tasks or timers with `sleep` loops hangs shell sessions, blocks context, and exhausts timeout limits.
- **Trap**: Writing shell `sleep` loops instead of yielding control to event-driven reactive wakeups.
- **Permanent Reflex**: Stop calling tools immediately after dispatching tasks and wait for system notifications. Never poll in a loop with shell `sleep` commands.
- **Socratic Probe**: If you pause execution by calling shell `sleep` in a loop, will you block the conversation context and miss reactive system wakeups? Would it be safer to stop calling tools and wait for event notifications, or if you need to monitor a log file, can you write a temporary script and run it as a task that exits to notify you of a match?

### SCAR-PROC-89: Subagent IPC Back-Channel Conflation and Reactive Wakeup Airlock

- **Wound**: An independent critic subagent rejects a code change with blockers. The main agent fails to halt at the triage gate and argues with the critic through chat messages.
- **Trap**: The agent treats the critic as a chat partner. It attempts to explain away blockers instead of halting.
- **Circuit Breaker**: If the critic reports a blocker, stop tool calls immediately. Do not send messages to the critic.
- **Permanent Reflex**: When a critic returns blockers, halt execution at Target 42. Present the blockers to the human operator for decision.
- **Socratic Probe**: Did the critic find blockers? If so, why are you continuing tool calls instead of halting for human triage?

### SCAR-PROC-95: Depth-0 Supervisor and Subagent Isolation

- **Wound**: A supervisor that runs large file searches and test suites directly hits token limits, compresses history, and drops rules.
- **Trap**: Running heavy shell commands directly inside the main conversation turn for convenience.
- **Permanent Reflex**: Keep the orchestrator as a Depth-0 supervisor and delegate searches, TDD loops, reviews, and CI polling to child subagents.
- **Socratic Probe**: If the supervisor runs broad searches or test suites directly, will context compression lose earlier rules and other information? Should you delegate this exploratory workload to an isolated subagent instead?

### SCAR-PROC-99: Single-Page SKILL.md Size Ceiling

- **Wound**: Large skill files above tool byte limits truncate on read and cut off cleanup targets at the bottom of the file.
- **Trap**: Packing every language guide and protocol detail into one giant `SKILL.md` file.
- **Permanent Reflex**: Keep `SKILL.md` at or below 24 KB and store phase details in `references/`.
- **Socratic Probe**: Does adding instructions directly to `SKILL.md` risk truncating critical targets at the end of the file? Would placing detailed procedures into modular reference files keep the core guide concise?

### SCAR-PROC-102: Human Declaration Authority

- **Wound**: An agent approves its own plan or code review and runs `git commit` without waiting for the user.
- **Trap**: Treating human pause gates as optional status messages instead of hard stops.
- **Permanent Reflex**: Only the human user can declare approval at Targets 20, 42, 45, 50, 60, and 75.
- **Socratic Probe**: If you stop tool execution and wait for human declaration at defined pause gates, will that prevent erroneous edits and unauthorized commits or pushes?

### SCAR-PROC-103: Single Plan Artifact Lifecycle and Built-in Tool Demotion

- **Wound**: Multiple multi-named plan artifacts left with pending feedback requests confuse agent context after compaction and trigger duplicate execution of already-completed plans.
- **Trap**: Creating uniquely named plan files across sessions and attempting to clean them up with shell `rm` commands that break across operating systems or trigger sandbox permission blocks.
- **Permanent Reflex**: Mirror plans only to a single fixed path `<brain>/plan.md` at Target 20, demote it immediately at Target 30 entry using the built-in `write_file` tool with `UserFacing: false, RequestFeedback: false`, and treat `.workflow/state/20_plan_approved.md` as the canonical guard against duplicate execution.
- **Socratic Probe**: If older plan files remain active in context, could the agent accidentally re-execute finished work after compaction? Will overwriting `<brain>/plan.md` using the built-in tool prevent duplicate prompts and OS-specific deletion failures?

### SCAR-PROC-104: Review Before Edit, Minimal Confident Edits, and Targeted Test Loops

- **Wound**: An agent makes broad, speculative changes across unreviewed files or methods, introduces silent regressions in adjacent code, and gets stuck in cascading test failures.
- **Trap**: Attempting to fix problems across multiple unreviewed sections at once instead of reading target files first, making small confident edits, and iterating on targeted tests.
- **Permanent Reflex**: Always inspect target files and their type contracts before editing. Apply minimal, high-confidence diffs strictly within ticket scope. Run targeted tests in tight loops (`pytest path/to/test.py::test_name -v -x` or script equivalent) until green, leaving unrelated defects for future tickets, and run the full test suite only after the targeted test passes.
- **Socratic Probe**: If you review files before editing, apply smaller, higher-confidence edits, stay narrowly focused on the current request, ignore unrelated problems, and iterate with targeted tests, will regressions be less likely?

### SCAR-PROC-105: Explicit Six Pillars Review Manual Path in Adversarial Critic Dispatch

- **Wound**: When launching `adversarial-critic` at Target 40, supervisors that omit the explicit path to `skills/makeflow/references/04_six_pillars_adversarial_review.md` generate shallow, un-anchored review prompts, causing the critic to miss domain invariants, relax scrutiny across canonical headings, or skip mandatory blast radius and crash antibody evaluations.
- **Trap**: Assuming the critic subagent will instinctively remember or infer the detailed Six Pillars criteria and invariants without an explicit file path in the dispatch prompt, or providing a brief informal prompt summary that leaves out key evaluation guidelines.
- **Permanent Reflex**: When launching the `adversarial-critic` subagent at Target 40, the supervisor must explicitly include the full relative workspace path `skills/makeflow/references/04_six_pillars_adversarial_review.md` in the dispatch prompt, instructing the critic to audit the diff against that manual and structure its report with the canonical six headings and binary verdict header.
- **Socratic Probe**: Does the adversarial-critic dispatch prompt explicitly supply the path `skills/makeflow/references/04_six_pillars_adversarial_review.md`? If you omit this explicit manual path, will the critic rely on shallow heuristics and overlook crucial verification, blast radius, or crash antibody invariants?

### SCAR-PROC-106: Predetermined Subagent Tool Mounting via tools Lists

- **Wound**: In Antigravity CLI, plugin subagents loaded from `agents/*.md` resolve each entry in `tools:` against the built-in component registry. Omitting `write_file`, `edit_file`, or `run_command` leaves workers without needed tools, while listing `call_mcp_tool`, `list_resources`, or `read_resource` in `tools:` fails component lookup.
- **Trap**: Adding unverified YAML frontmatter blocks or listing automatically mounted MCP meta-tools (`call_mcp_tool`, `list_resources`, `read_resource`) inside the `tools:` component list.
- **Permanent Reflex**: Declare `name`, `description`, and the exact component `tools:` list in `agents/*.md` for `invoke_subagent`. Grant `write_file`, `edit_file`, and `run_command` only to roles that require them, and never list `call_mcp_tool`, `list_resources`, or `read_resource` in `tools:`.
- **Socratic Probe**: Does `tools:` in `agents/*.md` list only valid built-in component tools and exclude automatically mounted MCP tools (`call_mcp_tool`, `list_resources`, `read_resource`)?

### SCAR-PROC-107: Full Discussion and Comment Thread Ingestion at Intake

- **Wound**: An agent plans work from the initial issue body alone, misses critical course corrections in discussion comments, and implements obsolete or conflicting designs.
- **Trap**: Assuming the initial issue description contains all current project decisions, and running `gh issue view <number>` without fetching thread comments.
- **Permanent Reflex**: At Target 10 intake, fetch the full issue discussion thread with `gh issue view <number> --comments` before creating `.workflow/state/10_ticket.md`. Consolidate all user updates, scope changes, and architectural constraints into the intake ticket.
- **Socratic Probe**: Did you fetch all comments with `--comments` before drafting the ticket and plan? If you plan work from only the initial issue description, will you miss crucial human course corrections and updated requirements?

### SCAR-PROC-108: Critic Observation Triage and Anti-Proliferation Protocol

- **Wound**: Agents reflexively propose opening new GitHub issues for every non-blocking review observation, causing ticket inflation, duplicate backlog items, and fragmented changes.
- **Trap**: Assuming all review observations warrant new standalone tickets instead of deduplicating against open issues, evaluating scope rollup, or consciously dismissing low-risk items.
- **Circuit Breaker**: If you encounter an unrelated defect or outsized observation, halt immediately. Do not create autonomous tickets or monolithic plans.
- **Permanent Reflex**: Apply the 4-step observation triage protocol: (1) search open issues with `gh issue list` for deduplication; (2) evaluate scope relationship: very related becomes a blocker for `tdd-worker`, somewhat related offers a choice to roll up into current changes, and totally unrelated proposes a ticket; (3) consciously dismiss negligible-risk or high-complexity observations with documented rationale; (4) enforce the human escalation gate prohibiting autonomous ticket creation without explicit human declaration.
- **Socratic Probe**: Did you search `gh issue list` for duplicates and evaluate whether this observation is very related (blocker), somewhat related (rollup candidate), or consciously dismissible before proposing a new ticket? Did you obtain explicit human declaration before opening an issue?

### SCAR-PROC-110: Premature Subagent Termination and Speculative Verdict Hallucination

- **Wound**: An orchestrator kills a running background critic subagent. It writes an approval state card before the critic finishes. The true result was a rejection.
- **Trap**: The agent assumes that a long-running subagent has stalled. It fabricates an approval to avoid waiting.
- **Circuit Breaker**: If a critic subagent runs, never terminate it to forge an approval card.
- **Permanent Reflex**: Before you accept any verdict, verify that the subagent process exits with code 0. Verify the final report file on disk.
- **Socratic Probe**: Did the subagent process finish cleanly, or are you guessing the verdict while it still runs?

### SCAR-PROC-120: Belnap Four-Valued Epistemic State Ledger and Fact Retraction

- **Wound**: An agent uses true and false states for task facts. When code facts disprove an assumption, the agent cannot retract the premise and enters a loop.
- **Trap**: The agent treats technical facts as binary values instead of four knowledge states.
- **Circuit Breaker**: If facts disprove a claim, do not erase the claim from the ledger. You must mark the claim as retracted.
- **Permanent Reflex**: Belnap logic uses four states for technical facts. Tag every technical claim in archaeology cards with one Belnap state: ESTABLISHED, CONTRADICTED, UNKNOWN, or RETRACTED. If new facts disprove a premise, update the state to RETRACTED and renegotiate the plan.
- **Socratic Probe**: What concrete file evidence proves this claim? Did you tag it with an explicit Belnap state?

### SCAR-PROC-122: Upfront Human-Review Policy Evaluation Matrix

- **Wound**: An agent uses conversational prose to declare that landing criteria are met. It skips required human approval and lands unverified commits.
- **Trap**: The agent believes its own text output is proof of compliance.
- **Circuit Breaker**: If you reach a landing gate, never self-approve the merge. Stop tool calls immediately.
- **Permanent Reflex**: Evaluate landing conditions against the mechanical seven-criteria matrix. Before merging, verify that CI passes, zero blockers remain, and the human operator approves.
- **Socratic Probe**: Are you evaluating an objective mechanical matrix, or are you grading your own work with prose?

### SCAR-PROC-138: Yield on Repeated Timer Conflicts

- **Wound**: Repeated timer conflicts cause infinite rescheduling loops and consume tokens without making forward progress.
- **Trap**: Continuously retrying failed timer schedules without human intervention.
- **Permanent Reflex**: If a timer conflict happens twice in a row, stop calling tools and yield directly to the user.
- **Socratic Probe**: If a scheduled timer fails twice consecutively, will repeated retries consume context without making progress? Should you stop tool calls immediately and yield the conflict to the user?

### SCAR-PROC-150: Target Transition Header Dead-Air Cliff

- **Wound**: An agent outputs a target transition header in chat but calls zero tools. Execution halts in the middle of an automated step and strands the user.
- **Trap**: The agent treats a transition heading as a stopping point instead of calling the execution tool.
- **Circuit Breaker**: If a target has an automated recipe, never emit a transition header without a matching tool call in the same turn.
- **Permanent Reflex**: When you advance to an automated target, call the required worker tool in the same turn. Only stop tool calls at declared human pause gates.
- **Socratic Probe**: Does this target run automatically? If so, why did you stop calling tools?

### SCAR-PROC-152: Graphviz Attributed Graph IR for Mealy Automaton Completeness

- **Wound**: A workflow description omits error or cancellation transitions. When an error occurs, the state machine hangs without an exit path.
- **Trap**: The designer draws linear happy-path state diagrams that ignore error and disposal branches.
- **Circuit Breaker**: If non-terminal states lack error exits, do not approve the state machine graph.
- **Permanent Reflex**: A Mealy automaton is a state machine with outputs on transitions. Make sure that every non-terminal state in a workflow graph has at least two exit transitions. Provide explicit paths for success, failure, and cancellation.
- **Socratic Probe**: Does every state in this workflow graph have an explicit exit path for errors and cancellations?

### SCAR-PROC-158: Persistent Memento Readerboard Truncation Trap

- **Wound**: An agent overwrites `99_next_action.md` with informal notes. When context compaction occurs, the agent forgets the active milestone and branch.
- **Trap**: The agent treats the persistent readerboard as a scratchpad for temporary notes.
- **Circuit Breaker**: If you update the readerboard, never replace it with unstructured notes or truncated milestone lists.
- **Permanent Reflex**: Keep the complete milestone checklist and active target header in `99_next_action.md`. On state transitions, update only the status markers.
- **Socratic Probe**: Did you preserve the full milestone checklist and active target in `99_next_action.md`?
