# 09. Synthetic Scars Master Catalog and Routing Index

This manual serves as the Master Routing Index for all Synthetic Scars (engineering rules codified from past operational failures).

## 1. Hub-and-Spoke and Two-Tier Architecture

Synthetic Scars operate across a Two-Tier Architecture:

1. **Tier 1 (Core Built-In Scars)**: Universal engineering rules live in `skills/makeflow/references/scars/` and `skills/makeflow/references/polyglot/`.
2. **Tier 2 (Project-Local Scars)**: Workspace-specific domain rules live in `.workflow/scars/` (for example `.workflow/scars/local.md` or `.workflow/scars/*.md`).

To scale the synthetic scar catalog to hundreds of community contributions without hitting context limits (`SCAR-PROC-99`) or causing Git merge conflicts, scars are organized into a **Hub-and-Spoke** architecture:

```
references/
├── 09_synthetic_scars_catalog.md    <-- Master Routing Index Table (Hub)
├── scars/                           <-- Category Spokes (Universal Rules)
│   ├── process.md                   <-- SCAR-PROC-*
│   ├── testing.md                   <-- SCAR-TEST-*, SCAR-AST-*
│   ├── git_and_shell.md             <-- SCAR-GIT-*, SCAR-SH-*
│   ├── system_and_os.md             <-- SCAR-SYS-*, SCAR-ENV-*
│   ├── data_and_database.md         <-- SCAR-DATA-*, SCAR-DB-*
│   ├── security_and_auth.md         <-- SCAR-SEC-*, SCAR-AUTH-*
│   ├── tooling_and_hci.md           <-- SCAR-TOOL-*, SCAR-HCI-*, SCAR-DOC-*
│   └── architecture.md              <-- SCAR-ARCH-*
└── polyglot/                        <-- Language Spokes (Domain-Specific)
    ├── dart_flutter.md              <-- SCAR-LANG-DART-*
    ├── python.md                    <-- SCAR-LANG-PYTHON-*
    ├── typescript_node.md           <-- SCAR-LANG-TYPESCRIPT-*
    └── ... (27 languages)
```

### Numbering Rules
1. **Category-Scoped Sequencing**: Scars are ordered monotonically within each category spoke file.
2. **End-of-Sequence Appending**: When adding a new scar, assign the next sequential integer and append it to the end of the appropriate category file.
3. **Full Language Names**: Language-specific failure vaccines use full uppercase language names: `SCAR-LANG-<FULL_LANGUAGE>-<NN>`.
4. **Four-Part Anatomy & Optional Circuit Breaker**: Every scar in a spoke file must record:
   - **Wound**: The exact bug, data loss, or broken build that happened.
   - **Trap**: The false assumption or shortcut that caused the failure.
   - **Permanent Reflex**: The mandatory rule that prevents recurrence.
   - **Socratic Probe**: A concise heuristic inquiry that tests attention against the specific failure mode during execution.

   A scar may optionally include an explicit **Circuit Breaker** between the Trap and the Permanent Reflex:
   - **Circuit Breaker**: An explicit halt trigger giving the agent immediate permission to stop before executing the permanent reflex ("If X, stop immediately or never Y").

### Socratic Reflex Protocol

Rules alone can be mechanically recited without changing reasoning behavior. To prevent cognitive bypass, every synthetic scar includes a **Socratic Probe** (a focused diagnostic inquiry).

Agents must use Socratic Probes during:
1. **Target 15 & Target 20 (Archaeology & Planning)**: Interrogate design assumptions against catalog failure modes before writing code.
2. **Target 30 (Implementation Loops)**: Evaluate whether proposed edits introduce unnecessary abstractions, violate hermetic isolation, or alter unreviewed code.
3. **Target 40 (Adversarial Review)**: Scrutinize diffs using probe questions to uncover silent regressions and unhandled failure modes.

---

## 2. Master Routing Table

| Scar ID | Title | Category | Spoke File |
| :--- | :--- | :--- | :--- |
| `SCAR-PROC-45` | Subagent System Prompt Paraphrasing and Rubric Mutagenesis | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-53` | Prohibit Shell Sleep Polling | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-89` | Subagent IPC Back-Channel Conflation and Reactive Wakeup Airlock | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-95` | Depth-0 Supervisor and Subagent Isolation | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-99` | Single-Page SKILL.md Size Ceiling | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-102` | Human Declaration Authority | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-103` | Single Plan Artifact Lifecycle and Demotion | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-104` | Review Before Edit and Targeted Loops | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-105` | Explicit Critic Six Pillars Manual Path | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-106` | Predetermined Subagent Tool Mounting via tools Lists | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-107` | Full Discussion and Comment Thread Ingestion at Intake | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-108` | Critic Observation Triage and Anti-Proliferation Protocol | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-110` | Premature Subagent Termination and Speculative Verdict Hallucination | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-120` | Belnap Four-Valued Epistemic State Ledger and Fact Retraction | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-122` | Upfront Human-Review Policy Evaluation Matrix | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-124` | Review Process or Halt on Guard Denial | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-138` | Yield on Repeated Timer Conflicts | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-150` | Target Transition Header Dead-Air Cliff | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-152` | Graphviz Attributed Graph IR for Mealy Automaton Completeness | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-PROC-158` | Persistent Memento Readerboard Truncation Trap | Process | [`scars/process.md`](./scars/process.md) |
| `SCAR-TEST-05` | Isolated Test Environments | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-08` | Two-Tier Test Isolation | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-09` | Fail-Fast Targeted TDD Loop | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-10` | Pre-Flight Structural Static Analysis Gate | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-11` | Shift-Left Testing Burden Balance | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-12` | Cryptographically Anchored TDD Receipts | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-22` | Traceable Test Naming and Artifact Longevity | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-24` | Coverage-Driven Fallback Branch Deletion Trap | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-29` | Red-Green Marginal-Line Test Law | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-TEST-44` | Phase-Separated Flakiness Discrimination | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-AST-01` | Structural AST Anti-Shortcut Scrubbing Gate | Testing | [`scars/testing.md`](./scars/testing.md) |
| `SCAR-SH-01` | Prefer Temporary Script Files Over Complex Shell Quoting | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-SH-03` | Shell Redirection and Comment Stripping | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-17` | Unchecked Direct Push CI Bypass Trap | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-20` | Pre-Push Merge Check | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-22` | Pre-Commit Working Tree Cleanliness Deadlock | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-23` | Native Merkle Tree Lock and TOCTOU Drift Attribution | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-24` | Conventional Commits Type-Scope Decoupling | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-25` | Destructive Working-Tree and History Guards | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-GIT-30` | Pure Git Mode and Repository-Scoped Hosting Discovery | Git & Shell | [`scars/git_and_shell.md`](./scars/git_and_shell.md) |
| `SCAR-ENV-04` | Shared Workspace Umask Permissions | System & OS | [`scars/system_and_os.md`](./scars/system_and_os.md) |
| `SCAR-SYS-05` | Cross-User File Promotion | System & OS | [`scars/system_and_os.md`](./scars/system_and_os.md) |
| `SCAR-SYS-06` | Native Runtime Environment Path Pre-Flight | System & OS | [`scars/system_and_os.md`](./scars/system_and_os.md) |
| `SCAR-SYS-07` | POSIX Signal Trapping for Long-Running Workers | System & OS | [`scars/system_and_os.md`](./scars/system_and_os.md) |
| `SCAR-SYS-08` | Atomic Directory-Based Mutual Exclusion Locks | System & OS | [`scars/system_and_os.md`](./scars/system_and_os.md) |
| `SCAR-DB-09` | Pipeline Splitting and URI Userinfo Stripping | Data & Database | [`scars/data_and_database.md`](./scars/data_and_database.md) |
| `SCAR-SEC-01` | Untrusted External Text Airlock | Security & Auth | [`scars/security_and_auth.md`](./scars/security_and_auth.md) |
| `SCAR-AUTH-02` | Preserve Authorization on Same-Origin Redirects | Security & Auth | [`scars/security_and_auth.md`](./scars/security_and_auth.md) |
| `SCAR-TOOL-07` | Prefer Dart Over Python for Scratch Scripts | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-TOOL-13` | Single State Writer Isolation | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-TOOL-14` | Programmatic Tool Polling vs Subagent Scrape Loops | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-TOOL-15` | Anchored State Marker Detection vs Substring Matching | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-TOOL-16` | Prohibit Arbitrary Output Limits and Require Volume Triage Interview | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-HCI-08` | Multi-Choice Prompt Rule | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-DOC-16` | Concentric Documentation Rings | Tooling & HCI | [`scars/tooling_and_hci.md`](./scars/tooling_and_hci.md) |
| `SCAR-ARCH-05` | Testable Object-Oriented Script Template (BaseScript Pattern) | Architecture | [`scars/architecture.md`](./scars/architecture.md) |
| `SCAR-ARCH-06` | Just-in-Time Vector 4 Reflex Gates | Architecture | [`scars/architecture.md`](./scars/architecture.md) |
| `SCAR-ARCH-07` | Anti-Speculative Defenses and Caller Grounding | Architecture | [`scars/architecture.md`](./scars/architecture.md) |

### Language-Specific Failure Vaccines

| Scar ID | Title | Language | Spoke File |
| :--- | :--- | :--- | :--- |
| `SCAR-LANG-DART-01` | Async Timers (fakeAsync & Frame Pumping) | Dart & Flutter | [`polyglot/dart_flutter.md`](./polyglot/dart_flutter.md) |
| `SCAR-LANG-DART-02` | Completer Guard (!isCompleted) | Dart & Flutter | [`polyglot/dart_flutter.md`](./polyglot/dart_flutter.md) |
| `SCAR-LANG-DART-03` | Cascade AST Node Member Offset Inspection | Dart & Flutter | [`polyglot/dart_flutter.md`](./polyglot/dart_flutter.md) |
| `SCAR-LANG-DART-04` | Widget Lifecycle & Controller Disposal | Dart & Flutter | [`polyglot/dart_flutter.md`](./polyglot/dart_flutter.md) |

---

## 3. Scar Creation Lifecycle and Target 90 Rules

### 3.1 Ticket-Scoped Core Scars (Tier 1)

1. If a core scar is a ticket deliverable, scope it during archaeology at Target 15.
2. Plan the spoke file, routing row, and unit tests in `.workflow/state/20_plan.md` at Target 20.
3. Implement and test the scar with `tdd-worker` at Target 30 on a feature branch.
4. Audit the scar diff with `adversarial-critic` at Target 40 before merging.
5. Target 90 never creates or modifies core catalog files on `main`.
6. At Target 90, record only merged scar IDs in `.workflow/history.json` and `.workflow/history.md`.

### 3.2 Incidental Retrospective Lessons and Project-Local Scars (Tier 2)

1. Inside the `makeflow-skill` repository, record incidental session lessons in ticket notes as candidates for a future feature request ticket.
2. Inside external user projects, offer to record project-specific lessons in `.workflow/scars/local.md` (or `.workflow/scars/*.md`) during Target 90.
3. Format each project-local scar with the Wound, Trap, Permanent Reflex, and Socratic Probe fields.
4. Record earned project-local scar IDs in `.workflow/history.json` and `.workflow/history.md`.

---

## 4. Multi-Dimensional SQLite Registry and FTS5 Search

The bundled SQLite database (`skills/makeflow/data/scars_registry.db`) stores all scars with an FTS5 (full-text search) virtual index.
The `scars_fts` table indexes `id`, `original_id`, `keywords`, `languages`, `frameworks`, `diagnostic_codes`, and `workflow_phases` directly.

### 4.1 Multi-Dimensional Query Filters

Use `workflow_query_scars` or `scripts/sync_synthetic_scars.py` to filter scars by multiple dimensions:

1. `query` (`--query`): Search by exact scar ID (`SCAR-ARCH-05`), ID prefix (`SCAR-ARCH`), or technical keywords.
2. `category` (`--category`): Filter by category name or slug (for example `Testing` or `architecture`).
3. `language` (`--language`): Filter by normalized language (for example `dart`, `python`, `rust`, `go`, or `universal`).
4. `framework` (`--framework`): Filter by framework or library (for example `flutter`, `riverpod`, `bloc`, `pytest`, or `sqlite`).
5. `diagnosticCode` (`--diagnostic-code`): Filter by compiler, signal, or runtime code (for example `StateError`, `UNDEFINED_GETTER`, or `SIGTERM`).
6. `workflowPhase` (`--workflow-phase`): Filter by canonical target phase (for example `Target 20: plan` or `Target 30: tdd`).

### 4.2 Metadata Enrichment Command

If you import or synchronize new scars, run the metadata enrichment script to update the FTS5 index:

```bash
python3 scripts/enrich_scars_index.py --db-path skills/makeflow/data/scars_registry.db
```

You can also run `make enrich-scars` or `python3 scripts/sync_synthetic_scars.py --enrich`.
