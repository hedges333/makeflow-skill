# 02. Archaeology and Planning (`Target 15` and `Target 20`)

This manual covers codebase archaeology, the Belnap fact table, plan file creation, and the human plan gate.

## 1. Target 15: Codebase Archaeology

Do not plan code edits from guesswork. Inspect the repository first to gather facts.

1. Capture the repository Git file tree index before `invoke_subagent` with `make -f .workflow/state/Makefile repo-tree` (or `git ls-files --cached --others --exclude-standard`).
2. If `repo-tree` exits `2` (`SCAR-TOOL-16`), report the file counts and interview the user before selecting `--prefix`, `--approved-limit`, `--count-only`, or `--no-limit`.
3. Include the captured file tree index directly in the `archaeology-worker` dispatch prompt. If the repository is empty (`0 of 0 files`), pass that index so the worker avoids blind path probing.
4. Fork `archaeology-worker` so the supervisor context stays small. If the dispatch omits an index, the worker calls the `workflow_repo_tree` MCP tool first.
5. Enforce Immediate Supervisory Halt (`tool_calls: []`) right after `invoke_subagent` (`SCAR-PROC-95`). Do not run concurrent supervisor file reads or shell commands while the worker runs.
6. Never run live CLI or shell bug-reproduction experiments in the Depth-0 supervisor during Target 15. Direct live bug reproduction to `tdd-worker` at Target 30.
7. Scan the repository across all 27 supported languages to find every subproject root, manifest, runtime, and test runner.
8. Read sibling modules so that new code matches existing names, error handling, and return types. Record **Module Layout and Layer Separation** against `skills/makeflow/references/polyglot/<lang>.md` (`SCAR-ARCH-08`), and flag structural anti-patterns with Belnap `ESTABLISHED` file evidence.
9. Inspect `.workflow/config.json`, environment variables, and database manifests to identify live and test database names.
10. Tag each technical claim with one Belnap truth state (a four-state fact label):
    - `ESTABLISHED`: Proved true by reading source files or test output.
    - `CONTRADICTED`: Proved false by source files or test output.
    - `UNKNOWN`: Not yet checked against source files.
    - `RETRACTED`: Accepted earlier, then disproved by new facts.
11. If a project uses databases and live versus test database names remain `UNKNOWN`, ask the user and offer to save them in `.workflow/config.json`.
12. Convert user requirements into Given-When-Then scenarios that become unit tests in Target 30.
13. If the target subproject has no test runner or test directory, prepare a smoke test template.
14. Classify the underlying failure mechanism and evaluate testing feasibility and execution burden balance (< 100 ms execution budget, hermetic fixtures) (`SCAR-TEST-11`).
15. Discover repository-scoped hosting mode (`SCAR-GIT-30`). Check `git remote get-url origin`, `gh` binary, `gh auth status`, and `gh repo view`. If origin does not point to GitHub or CLI checks fail, record `HOSTING_MODE: PURE_GIT`.
16. Discover applicable Tier 1 core scars in `skills/makeflow/references/scars/` and Tier 2 project-local scars in `.workflow/scars/`, and call `workflow_query_scars` (`workflowPhase: "Target 15: archaeology"`) for detected languages and frameworks.
17. Inspect the subproject for existing doc comment styles and documentation generator configs (`docs/conf.py`, `mkdocs.yml`, `typedoc.json`, `Doxyfile`, `Cargo.toml`) (`SCAR-PROC-159`). Adapt to existing conventions, or fall back to `skills/makeflow/references/polyglot/<lang>.md` defaults.
18. On worker wakeup, write `.workflow/state/15_archaeology_receipt.md` directly from the worker receipt without re-reading scanned files. If a concrete contradiction appears, re-read only that source file before drafting `.workflow/state/20_plan.md`.

### 1.1 Post-Archaeology Dependency Coupling Evaluation

If intake proceeded under `ALLOW_BLOCKED=1`, evaluate dependency coupling in Target 15.
Coupling means one module depends directly on another module.
Determine whether active code paths intersect the upstream blocker:

1. If archaeology proves an active technical blocker exists, recommend halting work or switching tickets.
2. If archaeology proves the code paths are decoupled, document the proof in `15_archaeology_receipt.md`.
3. Proceed to Target 20 planning only if the implementation can complete safely without the upstream fix.

## 2. Target 20: Plan File Creation (`20_plan.md`)

Write the implementation plan to `.workflow/state/20_plan.md`.

### 2.1 Socratic Pre-Planning Deliberation

Do not draft implementation plans from passive checklists. Before writing `.workflow/state/20_plan.md`, interrogate your proposed solution with these nine Socratic questions:

1. **Targeted Test Loops (`SCAR-PROC-104`, `SCAR-TEST-09`)**: Will running targeted test loops with fail-fast flags (`-v -x`) isolate failures before running full test suites?
2. **Hermetic Test Isolation (`SCAR-TEST-08`)**: Can tests run in memory, in temporary directories, or against mock fixtures without touching live databases or systems?
3. **Existing Test Modifications**: If an existing test must change, does the change reflect a true contract update rather than masking a bug?
4. **Comprehensive Coverage**: Do new tests reproduce the conditions and desired behavior addressed by the change request ticket?
5. **Minimal Scoped Edits (`SCAR-PROC-104`)**: If you keep changes narrow and leave unrelated defects for future tickets, will regressions be less likely?
6. **Two-Tier Scar Discovery**: Did you check Tier 1 core scars (`skills/makeflow/references/scars/`), Tier 2 project-local scars (`.workflow/scars/`), and stacked SQLite registries via `workflow_query_scars` (`workflowPhase: "Target 20: plan"`) for relevant failure reflexes?
7. **Code Structure and Layer Separation (`SCAR-ARCH-08`)**: Does this change place reusable logic, thin entrypoints, and tests in idiomatic language layers without cross-entrypoint imports or unapproved reorganization?
8. **Upstream Root-Cause Remediation (`SCAR-ARCH-09`)**: Would fixing the root cause upstream keep code simplest without harming other subsystems? Can you remove downstream workarounds instead of patching symptoms further down the control flow?
9. **Preserve Structured Data (`SCAR-ARCH-10`)**: Does this module scrape prose with regexes for data that already exists as structured JSON upstream? Can consumers read one shared structured store instead?

### 2.2 Socratic Invariant Audit Table

Include this audit table in every `.workflow/state/20_plan.md`:

| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |
| :--- | :--- | :--- |
| **Fail-Fast Targeted Loops** | Which exact subproject test command runs in tight iteration with fail-fast flags (`-v -x`) before full sweeps? | List the exact command with `-v -x` flags. |
| **Hermetic Test Isolation** | Can every test run in memory or against mock fixtures without live socket or database calls? | State the fixture strategy and temporary directory path. |
| **Existing Test Modification** | If any existing test is altered, why does this modification not mask a regression? | State the semantic reason, or note that zero existing tests change. |
| **Comprehensive Coverage** | Do new tests model the desired behavior of the new feature or bug fix? | State the ways that new tests will comprehensively cover the requested change. |
| **Minimal Scoped Edits** | What files will remain untouched, and how are unrelated defects quarantined? | List untouched modules and verify ticket scope boundaries. |
| **Code Structure and Layer Separation** | Does this change place reusable logic, thin entrypoints, and tests in idiomatic language layers without cross-entrypoint imports or unapproved reorganization? | State the layer role of each file and the structural posture (`SCAR-ARCH-08`). |

### 2.3 Required Plan Disclosures

Include these seven items in every plan:

1. Target subprojects and programming languages involved in the task.
2. Exact file paths to create or edit, the layer role of each planned file (library module, thin entrypoint, or test), and the three-tier structural posture (`SCAR-ARCH-08`):
   - **Adapt to clean existing layout**: Place files in the subproject's established layers.
   - **Enforce canonical layout for new subprojects or subsystems**: Follow `skills/makeflow/references/polyglot/<lang>.md` and `entrypoints -> library` one-way dependencies.
   - **Flag messy existing layout at Target 15 and Target 20**: Offer a user choice at Target 20. Either keep in-ticket edits minimal with a follow-up reorganization ticket, or include scoped reorganization with explicit human approval.
3. Targeted subproject test commands for Target 30 and full repository test commands for Target 50.
4. Smoke test files and directories to create if the subproject lacks tests.
5. Any existing tests that must change, paired with the technical reason for each change.
6. Database and file isolation rules proving that tests use `*_test` databases, in-memory stores, or mocks, never production data.
7. Testing Strategy and Burden-Balance Analysis (`SCAR-TEST-11`): Disclose testing depth against speed and dependency constraints. Prohibit surface-only testing when hermetic boundary testing is feasible.
8. Active Project Documentation Standard and Tree Assembly Command (`SCAR-PROC-159`): Disclose the active doc comment standard and tree assembly command in `20_plan.md` from project conventions or `skills/makeflow/references/polyglot/<lang>.md`.

## 3. Human Plan Approval Gate

Target 20 is a mandatory human pause gate.

1. Run `make -f .workflow/state/Makefile approved-plan` to record Target 20 and render `AWAITING_DECLARATION (human-plan-approval)` in `.workflow/state/99_next_action.md`.
2. Print the plan summary in chat with a `file://` link to `.workflow/state/20_plan.md`.
3. Stop execution with empty tool calls (`tool_calls: []`).
4. Wait for explicit human approval in chat before editing any source files.
5. After the user approves the plan, write `.workflow/state/20_plan_approved.md` and run `make -f .workflow/state/Makefile implementation-diff APPROVED=1` (or `humanPlanApproved: true`).
