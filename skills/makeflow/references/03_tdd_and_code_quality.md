# 03. TDD and Code Quality Protocol (`Target 30`)

This manual covers test-driven development (TDD, writing tests before production code), small edits, and documentation updates.

## 1. Review Before Edit (`SCAR-PROC-104`)

Follow six rules before you modify any source file:

1. Read the file, its imports, its type signatures, and its comments first (`SCAR-PROC-104`).
2. Make minimal, confident edits that solve the ticket. Do not rewrite an entire module when a three-line fix works.
3. Leave unrelated defects or style issues untouched, and note them for a separate ticket.
4. Read applicable Tier 1 core scars (`09_synthetic_scars_catalog.md`, `skills/makeflow/references/scars/`) and Tier 2 project-local scars (`.workflow/scars/`). Run two `workflow_query_scars` calls (`workflowPhase: "Target 30: tdd"`, `limit: 5`, `refreshLoaded: true`). Query active languages, frameworks, and diagnostic codes first, then query `language: "universal"` with `excludeCategories: ["WORKFLOW"]`.
5. Enforce idiomatic layer separation from `skills/makeflow/references/polyglot/<lang>.md` and one-way dependencies (`entrypoints -> library`) under `SCAR-ARCH-08` and `SCAR-ARCH-05`.
6. Read `.workflow/state/20_plan.md`, `04_testing_isolation.md`, `10_security_and_data_isolation.md`, and `14_design_patterns_and_architecture.md` before writing Red tests. Follow planned OO design patterns, module cohesion boundaries, and carried-forward scar guardrails.

## 2. Red-Green-Refactor TDD Loop

In multi-agent runtimes, fork `tdd-worker` via `invoke_subagent` and enforce Immediate Supervisory Halt (`tool_calls: []`) until `<SYSTEM_MESSAGE>` wakeup (`SCAR-PROC-95`). Execute every code change in three ordered phases.

### Phase 1: Red (Failing Test First)

1. Write unit tests from the Given-When-Then scenarios created in Target 15.
2. If the subproject has no test files, create the smoke test approved in Target 20 first.
3. Run the targeted subproject test command and make sure that the new test fails (`red_exit != 0`).
4. Never write feature code before watching the test fail. A test that passes before code changes proves nothing.
5. If tests live in a dedicated test directory, run `make -f .workflow/state/Makefile witness-red TEST_DIR=<dir> TEST_CMD="<cmd>"` (`SCAR-TEST-12`).

### Phase 2: Green (Minimal Production Code)

1. Write the smallest production code change that makes the failing test pass.
2. Follow [`14_design_patterns_and_architecture.md`](./14_design_patterns_and_architecture.md): favor object composition over deep inheritance, and apply Game Programming Patterns in game subprojects.
3. Balance encapsulation with speed: use an ORM (a library that maps database rows to objects) by default, and place parameterized SQL or NoSQL queries inside repositories for large datasets.
4. Run the targeted subproject test command and make sure that all targeted tests pass (`green_exit == 0`).
5. If `.workflow/state/30_tdd_receipt.json` exists, run `make -f .workflow/state/Makefile verify-green` (`SCAR-TEST-12`). Keep sealed test files, commands, and commits unchanged.

### Anchored TDD Receipts (`SCAR-TEST-12`)

The `witness-red` gate writes `.workflow/state/30_tdd_receipt.json` with a cryptographic seal (a tamper-detection hash) over Git `HEAD`, `test_cmd`, and test files. The seal also hashes these core governance files:

- `scripts/tdd_guard.py`
- `scripts/workflow_gates.py`
- `scripts/emit_reflex.py`
- `lib/base_script.py`
- `lib/commit_gates.py`
- `lib/commit_msg.py`
- `lib/config.py`
- `lib/git_index.py`
- `lib/intake_gates.py`
- `lib/main_sync.py`
- `lib/parking_gates.py`
- `lib/plugin_gates.py`
- `lib/reflex.py`
- `lib/regression_gate.py`
- `lib/repo_tree.py`
- `lib/shell_safety.py`
- `lib/state_makefile.py`
- `lib/tdd_guard.py`
- `lib/ticket_state.py`
- `lib/workflow_gates.py`
- `lib/workflow_gates_cli.py`

The tree hash covers untracked fixtures such as `conftest.py` and ignores cache folders such as `__pycache__` and `.pytest_cache`. The `verify-green` gate verifies the receipt seal, Git `HEAD`, test tree hash, and governance toolchain before replaying the command. It also verifies that `.workflow/state/Makefile` matches the canonical template. Tampering returns a Vector 4 Reflex (a runtime recovery directive) with exit code `2` and appends to `forensic_log`.

The `verify-regression` gate in `lib/regression_gate.py` runs the full active subproject regression suite. It seals `.workflow/state/30_regression_receipt.json` over the live working-tree hash, Git `HEAD`, command, toolchain hash, and Makefile digest. If tests fail or mutate workspace files, `verify-regression` exits with code `2` and removes stale receipts. Before Target 40, `check-regression` verifies the sealed receipt against live ground truth and `.workflow/state/approved_tree.txt`.

The `verify-citation` gate reads cited specification lines directly from immutable Git blobs at witnessed `HEAD`. It also verifies governance toolchain integrity. If a cited file does not exist in the witnessed `HEAD` commit, the gate rejects it with `UNCOMMITTED_WITNESS_FILE`. If a citation points to a blank line or comment line, the gate rejects it with `TRIVIAL_WITNESS_LINE`. Both violations return exit code `2` and roll back Green verification.

The `scripts/pre_tool_guard.py` hook blocks direct writes to protected governance files. It also blocks shell redirection (`>`, `>>`), `truncate`, `sed -i`, `rm`, and `unlink` on them. Protected targets include `lib/regression_gate.py`, `lib/state_machine.py`, `.workflow/state/30_regression_receipt.json`, `.workflow/state/active_target.json`, `.workflow/state/active_subagent.json`, and `.workflow/state/Makefile`.

### Phase 3: Refactor

1. Remove duplicate logic, keep function interfaces clear, and verify one-way layer boundaries (`entrypoints -> library`, `SCAR-ARCH-08`).
2. Run the code formatter and linter on the modified files.
3. Re-run the targeted test command to make sure that tests stay green.
4. Run `make -f .workflow/state/Makefile verify-regression` to execute the full active subproject regression suite and seal `.workflow/state/30_regression_receipt.json`.

## 3. Targeted Subproject Test Loops and Pre-Flight Checks (`SCAR-PROC-104`, `SCAR-TEST-09`, `SCAR-TEST-10`)

In repositories with multiple subprojects (for example `backend/`, `frontend/`, and `scripts/`), keep inner loops fast:

1. Run fast pre-flight structural static analysis before you execute functional tests (`SCAR-TEST-10`). Catch syntax errors and undefined symbols early using `ruff`, `flake8`, or standard library AST parsers.
2. Run inner Red-Green loops only inside the modified subproject directory during Target 30 (`SCAR-PROC-104`).
3. Run targeted tests in tight loops with fail-fast flags (`pytest path/to/test.py::test_name -v -x` or script equivalent) until green (`SCAR-TEST-09`).
4. Before Target 40, run `make -f .workflow/state/Makefile verify-regression` to catch regressions inside the active subproject.
5. Save cross-subproject full repository test sweeps for Target 50 before commit.

## 4. Documentation Tree (Progressive Disclosure) (`SCAR-DOC-16`)

Update documentation alongside code across four progressive disclosure tree levels (`SCAR-DOC-16`, `SCAR-PROC-159`):

- **Leaf Inline Code Docstrings**: Update doc comments, parameter descriptions, return types, and exceptions in modified files.
- **Branch Subproject READMEs**: Update module README files when public contracts change.
- **Branch Guides and Examples**: Update usage examples and tutorials when flags or APIs change.
- **Root Overview and Changelogs**: If behavior changes, update top-level overview indexes and add ticket-prefixed entries (`- GH-1234:` or `- LOCAL-1234:`) under `## [Unreleased]` in `CHANGELOG.md` (`SCAR-DOC-16`, `SCAR-PROC-159`).

When full subproject regression tests pass and `.workflow/state/30_regression_receipt.json` is sealed, lock `.workflow/state/approved_tree.txt` and run `make -f .workflow/state/Makefile adversarial-review` to enter Target 40.
