# Makefile Workflow Plugin Rules

These rules apply when the `makeflow` plugin is active.

## 1. Chat Presentation, Input, and Artifact Rules

The chat stream is the main presentation surface. The `ask_question` tool is an input prompt, not a display surface.

### 1.1 Target 20 Plan Artifact Lifecycle in Antigravity (SCAR-PROC-103)

Antigravity renders an interactive review panel for artifacts. Follow these rules for implementation plans:

1. Write the implementation plan to `.workflow/state/20_plan.md`.
2. Mirror the plan to `<brain>/plan.md` with the built-in `write_file` tool.
3. Set `Overwrite: true` and `ArtifactMetadata: { UserFacing: true, RequestFeedback: true }`.
4. Always use the single fixed filename `<brain>/plan.md`. Do not create multiple plan files with different names.
5. Print a concise summary in chat with a `file://` link to `.workflow/state/20_plan.md`, then stop tool calls.
6. Accept either `/artifact` approval or verbal chat approval at Target 20.
7. If the user approves the plan, demote the artifact immediately at Target 30 entry.
8. Use the built-in `write_file` tool to overwrite `<brain>/plan.md`.
9. Set `Overwrite: true` and `ArtifactMetadata: { UserFacing: false, RequestFeedback: false }`.
10. Write an approved tombstone body inside `<brain>/plan.md`.
11. Do not use shell `rm` commands to delete artifacts. Overwriting with the built-in tool updates markdown content and companion JSON attributes safely.
12. If `.workflow/state/20_plan_approved.md` exists, never re-execute Target 30 from old artifact notifications. Always verify progress from `.workflow/state/`.
13. Include Testing Strategy and Burden-Balance Analysis (`SCAR-TEST-11`): In Target 20 plans, disclose testing depth against speed and dependency constraints. Prohibit surface-only testing when hermetic boundary testing is feasible.
14. Socratic Pre-Planning Inquiry and Invariant Audit: Before writing `.workflow/state/20_plan.md`, interrogate the solution with Socratic pre-planning inquiries. Include the mandatory Socratic Invariant Audit table in every plan artifact.

### 1.2 Pause Gates, Question Prompts, and Declaration Authority (SCAR-PROC-102)

1. Enforce human declaration authority (`SCAR-PROC-102`): Stop and wait for explicit human approval at all six pause gates: Target 20 (plan), Target 42 (blocker triage), Target 45 (review), Target 50 (commit), Target 60 (push), and Target 75 (landing).
2. Explicitly prohibit agent self-approval: Agents must never self-approve, self-declare, or bypass any pause gate.
3. If the user must choose between two or more alternatives ($N \ge 2$), call `ask_question`.
4. If a gate has one approval action ($N = 1$), do not call `ask_question`. Print the full proposal in chat with `file://` links and stop tool calls. This rule covers Blocker Triage (Target 42), Review Approval (Target 45), Commit Approval (Target 50), Push Approval (Target 60), and Landing Approval (Target 75).
5. Before you ask for a decision, print the full facts in the chat message.
6. Add a `file://` markdown link for every file, diff, plan, or review card that you name.

### 1.3 Ticket Disambiguation and Commit Protocol

To prevent accidental GitHub issue closures, distinguish local session tasks from remote issues:

1. Label local session tasks with the prefix `LOCAL-<N>`.
2. Label GitHub issues with the prefix `GH-<N>`.
3. For local session tasks, create branches named `feat/local-<N>-<slug>` or `fix/local-<N>-<slug>`.
4. Never use bare integer branch names for local tasks.
5. On local session commits, write `Ticket: LOCAL-<N>`.
6. Never write `closes #<N>`, `Closes #<N>`, or bare `#<N>` in local commit messages.

### 1.4 Explicit Six Pillars Manual Path in Critic Dispatch (SCAR-PROC-105)

When launching the `adversarial-critic` subagent at Target 40:

1. Always include the explicit workspace path `skills/makeflow/references/04_six_pillars_adversarial_review.md` in the dispatch prompt.
2. Instruct the subagent to audit the diff against that manual and format findings under the canonical six headings.
3. Never substitute an informal prompt summary for the explicit manual reference.

### 1.5 Predetermined Subagent Tool Mounting (GH-83, GH-108)

In Antigravity CLI, `agents/*.md` files declare `name`, `description`, and `tools:` for `invoke_subagent`.

1. Declare component permissions in `agents/*.md` using only `name`, `description`, and a `tools:` list.
2. Grant `write_file`, `edit_file`, and `run_command` to `tdd-worker` in `tools:`.
3. Grant `run_command` without `write_file` or `edit_file` to `adversarial-critic` and `triage-worker` in `tools:`.
4. Omit `write_file`, `edit_file`, and `run_command` from `archaeology-worker` so it stays read-only.
5. Never list `call_mcp_tool`, `list_resources`, or `read_resource` in `tools:` because the runtime mounts MCP tools automatically.

### 1.6 Synthetic Scar Creation Lifecycle and Two-Tier Discovery

Synthetic Scars use a two-tier storage and discovery lifecycle:

1. Tier 1 core scars live in `skills/makeflow/references/scars/` and `skills/makeflow/references/polyglot/`.
2. Tier 2 project-local scars live in `.workflow/scars/` (for example `.workflow/scars/local.md`).
3. If a core scar is a ticket deliverable, scope it in Target 15 and plan it in Target 20.
4. Author and test core scars with `tdd-worker` at Target 30 on a feature branch, and review in Target 40.
5. Target 90 never creates or modifies core catalog files on `main`.
6. At Target 90, record only merged scar IDs in `.workflow/history.json` and `.workflow/history.md`.
7. Inside `makeflow-skill`, record incidental session lessons in ticket notes for a future feature request ticket.
8. Inside external user projects, offer to record project-specific lessons in `.workflow/scars/local.md` at Target 90.
9. During Target 15, Target 20, Target 30, and Target 40, inspect both `skills/makeflow/references/scars/` and `.workflow/scars/`.

### 1.7 Full Discussion and Comment Thread Ingestion at Intake (SCAR-PROC-107)

When ingesting an assigned issue at Target 10:

1. Fetch the full discussion thread with `gh issue view <number> --comments` (or platform API equivalent).
2. Review all thread comments for course corrections, design updates, and architectural constraints.
3. Consolidate discussion updates into `.workflow/state/10_ticket.md` before drafting the Target 20 plan.
4. If comments exist on the issue, do not rely on the initial issue description alone.

### 1.8 Critic Observation Triage and Anti-Proliferation Protocol (SCAR-PROC-108)

To prevent ticket backlog inflation from review observations:

1. Before proposing a new ticket, search open issues with `gh issue list`.
2. Link the observation to any existing issue to prevent duplicate tickets.
3. If an observation is very related to active ticket scope, treat it as a blocker for `tdd-worker`.
4. If an observation is somewhat related, offer the user a choice to roll up or file a ticket.
5. If an observation is totally unrelated, propose a new ticket after deduplication.
6. If an observation has negligible risk or outsized complexity, perform conscious dismissal and document technical rationale without filing an issue.
7. Wait for explicit human confirmation at the human escalation gate before creating any issue.
8. Autonomous ticket creation is strictly prohibited.

### 1.9 Target 40 Model Picker Configuration Bypass and Turn Pause Invariant (GH-81)

When entering Target 40 adversarial review:

1. If `.workflow/config.json` specifies models or "inherit", bypass the interactive model picker prompt.
2. When model selection is unconfigured in interactive IDEs, prompt the user to switch the model picker to a deep-reasoning model.
3. Stop tool calls (`tool_calls: []`) to pause before dispatching `adversarial-critic`. Never dispatch the critic subagent in the same turn as the model picker prompt.

### 1.10 Target 60 Pre-Push Rebase and Critic Re-Audit Invariant (GH-126)

When resolving pre-push rebase conflicts at Target 60:

1. Never rebase autonomously without explicit operator direction (`SCAR-GIT-20`, `SCAR-GIT-25`).
2. Rebase conflict resolution invalidates the staged Merkle tree lock (`SCAR-GIT-23`).
3. Re-stage all resolved changes and update the approved tree lock: `git add -A && git write-tree > .workflow/state/approved_tree.txt`.
4. Run pre-flight static checks (`make test-static`).
5. Re-dispatch `adversarial-critic` with explicit manual path `skills/makeflow/references/04_six_pillars_adversarial_review.md` (`SCAR-PROC-105`) before advancing to push.
6. Advance target from `T60` to `T40` in the Makefile DAG to complete the adversarial re-audit.

### 1.11 Prohibit Arbitrary Output Limits and Require Volume Triage Interview (SCAR-TOOL-16)

When inspecting or triaging information sources:

1. Never unilaterally append arbitrary `--limit`, `-n`, or truncation flags (`head -n`, `--limit 10`).
2. If high output volume threatens context limits, halt tool execution immediately.
3. Report the volume risk and interview the user to select one of four paths:
   - Revise the directive or filter criteria.
   - Impose explicit user-approved limits.
   - Count total rows first before reading detailed content.
   - Proceed without limits.

## 2. Production Data Protection Rules

Never connect tests or agent commands to live systems.

1. Do not connect to live production databases, cloud storage, or mutable production files.
2. Point tests to isolated test databases (for example `*_test` or configured `database.testDatabases`), in-memory databases, or mock fixtures. If live versus test database names are unclear, ask the user before running tests.
3. Run file operations during tests inside temporary directories or mocked file systems.
4. Partition tests into two tiers (`SCAR-TEST-08`): Run pure unit tests in memory with zero external socket or daemon dependencies. Isolate integration tests behind explicit markers, and never connect live sockets or database DDL inside global autouse session fixtures. Tests may use self-contained ephemeral loopback sockets (`127.0.0.1:0` or temporary Unix domain sockets) for protocol or daemon verification.
5. Use safe file promotion (`SCAR-SYS-05`): When copying files across system users or service accounts, use `shutil.copyfile` to stream content bytes. Do not use `shutil.copy2` or `copystat` across user boundaries. Create destination directories with caller permissions.
6. Native runtime environment path pre-flight (`SCAR-SYS-06`): Before launching processes that initialize native compiled extensions, verify that temporary directories (`TMPDIR`) exist and are writable using `validate_runtime_path`. If an environment path is missing or unwritable, sanitize the environment with a verified safe fallback path.
7. POSIX signal trapping for long-running workers (`SCAR-SYS-07`): Intercept `SIGTERM` and `SIGINT` in worker processes and test daemons using `SignalTrap`. Register cleanup callbacks in reverse order (LIFO) and unlink lockfiles automatically. Terminate registered child subprocesses with kill escalation, reap zombies non-blockingly, and force-exit immediately if repeated signals arrive during shutdown.
8. Atomic directory-based mutual exclusion locks (`SCAR-SYS-08`): Use atomic directory creation (`os.mkdir` / `mkdir`) for process mutual exclusion instead of non-atomic lockfiles. Catch `FileExistsError` to detect contention, and remove lock directories with `os.rmdir` upon process exit or signal trapping. Catch `FileNotFoundError` silently during cleanup to handle concurrent removal safely.

## 3. Code Edit and Architecture Rules (`SCAR-PROC-104`)

Keep each code change small and within ticket scope.

1. Review before edit (`SCAR-PROC-104`): Read target files and their type contracts before you edit them.
2. Apply minimal edits that solve the active ticket. Favor object composition over deep class inheritance.
3. Run pre-flight structural static analysis (`SCAR-TEST-10`): If you modify code, run fast structural checks before functional test suites. Check syntax errors and undefined symbols with `ruff`, `flake8`, or standard library AST parsers.
4. Run fail-fast targeted test loops (`SCAR-PROC-104`, `SCAR-TEST-09`): Execute targeted subproject test commands in tight loops with fail-fast flags (`pytest path/to/test.py::test_name -v -x` or script equivalent) until green before running full regression suites.
5. Use an ORM (a library that maps database rows to objects) by default. If large datasets slow down the ORM, encapsulate parameterized SQL or NoSQL queries behind repository methods.
6. If you find unrelated defects during a ticket, do not edit them. Record them in ticket notes for a separate task.
7. Anti-speculative defenses and caller grounding (`SCAR-ARCH-07`): Defend only against inputs and states reachable by verified, real callers. Do not add speculative defense layers, multi-source regex heuristics, or unrequested CLI flag-order handling for internal scripts invoked only by deterministic targets. Balance "the problem could occur" against "the problem will occur": if an edge case is certain to occur for actual callers, address it; if it is merely hypothetical problem-hunting without observed failures, defer defense until a bug is observed or reported.

### 3.1 Temporary Script and Automation Rules (`SCAR-SH-01`, `SCAR-TOOL-07`, `SCAR-ARCH-05`)

Apply these rules for scratch scripts and automation utilities:

1. For one-off scripts and automation tasks where the language is not specified, prefer Dart over Python if Dart is installed.
2. If Dart is not installed, use Python.
3. Never attempt to install Dart.
4. If shell commands require complex string escaping or multiple quoting layers, write the script to a file in a temporary directory. Pass the script path to the runtime interpreter command (`dart` or `python3`) under `SCAR-SH-01`.
5. Encapsulate script and entry-point logic in testable class objects or lifecycle runners that manage argument parsing, setup, execution, cleanup, and fatal exception trapping (the BaseScript / Template Method pattern, `SCAR-ARCH-05`). Keep executable CLI entry points to thin two-line wrappers (`sys.exit(ScriptClass().execute())`).

## 4. Simple English Documentation Rules

Apply the open technical English rules in `skills/makeflow/references/resources/language/english/` to documentation, plans, changelogs, and pull request descriptions:

1. Keep procedural sentences at 20 words or fewer, with one instruction per sentence.
2. Keep descriptive sentences at 25 words or fewer, in simple tenses and active voice.
3. Put the condition before the command with a comma: "If the build fails, read the log."
4. Define each technical concept term in under ten words at first use.
5. Delete filler words, contractions, semicolons, and em-dashes.
