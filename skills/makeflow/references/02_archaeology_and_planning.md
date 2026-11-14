# 02. Archaeology and Planning (`Target 15` and `Target 20`)

This manual covers codebase archaeology, the Belnap fact table, plan file creation, and the human plan gate.

## 1. Target 15: Codebase Archaeology

Do not plan code edits from guesswork. Inspect the repository first to gather facts.

1. Fork an `archaeology-worker` subagent so the supervisor context stays small.
2. Scan the repository across all 27 supported languages to find every subproject root, manifest, runtime, and test runner.
3. Read sibling modules so that new code matches existing names, error handling, and return types.
4. Inspect `.workflow/config.json`, environment variables, and database manifests to identify live and test database names.
5. Tag each technical claim with one Belnap truth state (a four-state fact label):
   - `ESTABLISHED`: Proved true by reading source files or test output.
   - `CONTRADICTED`: Proved false by source files or test output.
   - `UNKNOWN`: Not yet checked against source files.
   - `RETRACTED`: Accepted earlier, then disproved by new facts.
6. If a project uses databases and live versus test database names remain `UNKNOWN`, ask the user and offer to save them in `.workflow/config.json`.
7. Convert user requirements into Given-When-Then scenarios that become unit tests in Target 30.
8. If the target subproject has no test runner or test directory, prepare a smoke test template.
9. Classify the underlying failure mechanism and evaluate testing feasibility and execution burden balance (< 100 ms execution budget, hermetic fixtures) (`SCAR-TEST-11`).
10. Discover repository-scoped hosting mode (`SCAR-GIT-30`). Check `git remote get-url origin`, `gh` binary, `gh auth status`, and `gh repo view`. If origin does not point to GitHub or CLI checks fail, record `HOSTING_MODE: PURE_GIT`.
11. Discover applicable Tier 1 core scars in `skills/makeflow/references/scars/` and Tier 2 project-local scars in `.workflow/scars/`.
12. Write `.workflow/state/15_archaeology_receipt.md` and advance to Target 20.

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

Do not draft implementation plans from passive checklists. Before writing `.workflow/state/20_plan.md`, interrogate your proposed solution with these four Socratic questions:

1. **Targeted Test Loops (`SCAR-PROC-104`, `SCAR-TEST-09`)**: Will running targeted test loops with fail-fast flags (`-v -x`) isolate failures before running full test suites?
2. **Hermetic Test Isolation (`SCAR-TEST-08`)**: Can tests run in memory, in temporary directories, or against mock fixtures without touching live databases or systems?
3. **Existing Test Modifications**: If an existing test must change, does the change reflect a true contract update rather than masking a bug?
4. **Comprehensive Coverage**: Do new tests reproduce the conditions and desired behavior addressed by the change request ticket?
5. **Minimal Scoped Edits (`SCAR-PROC-104`)**: If you keep changes narrow and leave unrelated defects for future tickets, will regressions be less likely?
6. **Two-Tier Scar Discovery**: Did you check Tier 1 core scars (`skills/makeflow/references/scars/`) and Tier 2 project-local scars (`.workflow/scars/`) for relevant failure reflexes?

### 2.2 Socratic Invariant Audit Table

Include this audit table in every `.workflow/state/20_plan.md`:

| Invariant / Boundary | Probing Socratic Inquiry | Pre-Implementation Verification / Proof |
| :--- | :--- | :--- |
| **Fail-Fast Targeted Loops** | Which exact subproject test command runs in tight iteration with fail-fast flags (`-v -x`) before full sweeps? | List the exact command with `-v -x` flags. |
| **Hermetic Test Isolation** | Can every test run in memory or against mock fixtures without live socket or database calls? | State the fixture strategy and temporary directory path. |
| **Existing Test Modification** | If any existing test is altered, why does this modification not mask a regression? | State the semantic reason, or note that zero existing tests change. |
| **Comprehensive Coverage** | Do new tests model the desired behavior of the new feature or bug fix? | State the ways that new tests will comprehensively cover the requested change. |
| **Minimal Scoped Edits** | What files will remain untouched, and how are unrelated defects quarantined? | List untouched modules and verify ticket scope boundaries. |

### 2.3 Required Plan Disclosures

Include these seven items in every plan:

1. Target subprojects and programming languages involved in the task.
2. Exact file paths to create or edit.
3. Targeted subproject test commands for Target 30 and full repository test commands for Target 50.
4. Smoke test files and directories to create if the subproject lacks tests.
5. Any existing tests that must change, paired with the technical reason for each change.
6. Database and file isolation rules proving that tests use `*_test` databases, in-memory stores, or mocks, never production data.
7. Testing Strategy and Burden-Balance Analysis (`SCAR-TEST-11`): Disclose testing depth against speed and dependency constraints. Prohibit surface-only testing when hermetic boundary testing is feasible.

## 3. Human Plan Approval Gate

Target 20 is a mandatory human pause gate.

1. Print the plan summary in chat with a `file://` link to `.workflow/state/20_plan.md`.
2. Set `.workflow/state/99_next_action.md` to `AWAITING_DECLARATION (human-plan-approval)`.
3. Stop execution with empty tool calls (`tool_calls: []`).
4. Wait for explicit human approval in chat before editing any source files.
5. After the user approves the plan, write `.workflow/state/20_plan_approved.md` and advance to Target 30 with `humanPlanApproved: true`.
