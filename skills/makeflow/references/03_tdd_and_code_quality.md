# 03. TDD and Code Quality Protocol (`Target 30`)

This manual covers test-driven development (TDD, writing tests before production code), small edits, and documentation updates.

## 1. Review Before Edit (`SCAR-PROC-104`)

Follow four rules before you modify any source file:

1. Read the file, its imports, its type signatures, and its comments first (`SCAR-PROC-104`).
2. Make minimal, confident edits that solve the ticket. Do not rewrite an entire module when a three-line fix works.
3. Leave unrelated defects or style issues untouched, and note them for a separate ticket.
4. Read and enforce applicable Tier 1 core scars in `skills/makeflow/references/scars/` and Tier 2 project-local scars in `.workflow/scars/`.

## 2. Red-Green-Refactor TDD Loop

Execute every code change in three ordered phases.

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

The `witness-red` gate writes `.workflow/state/30_tdd_receipt.json` with a cryptographic seal (a tamper-detection hash) over Git `HEAD`, `test_cmd`, all test files, and core governance scripts (`scripts/tdd_guard.py`, `scripts/workflow_gates.py`, `scripts/base_script.py`, `scripts/emit_reflex.py`). The tree hash covers untracked fixtures such as `conftest.py` and ignores cache folders such as `__pycache__` and `.pytest_cache`. The `verify-green` gate compares the receipt seal, Git `HEAD`, test tree hash, and governance toolchain integrity before it replays the sealed command. It also verifies that `.workflow/state/Makefile` matches the canonical template. Tampering attempts return a Vector 4 Reflex (a runtime recovery directive) with exit code `2` and append to `forensic_log`.

The `verify-citation` gate reads cited specification lines directly from immutable Git blobs at witnessed `HEAD`. It also verifies governance toolchain integrity. If a cited file does not exist in the witnessed `HEAD` commit, the gate rejects it with `UNCOMMITTED_WITNESS_FILE`. If a citation points to a blank line or comment line, the gate rejects it with `TRIVIAL_WITNESS_LINE`. Both violations return exit code `2` and roll back Green verification.

The `pre_tool_guard.py` hook blocks shell commands attempting output redirection (`>`, `>>`), truncation (`truncate`), in-place editing (`sed -i`), or deletion (`rm`, `unlink`) targeting makeflow governance files or `.workflow/state/Makefile`.

### Phase 3: Refactor

1. Remove duplicate logic and keep function interfaces clear.
2. Run the code formatter and linter on the modified files.
3. Re-run the targeted test command to make sure that tests stay green.

## 3. Targeted Subproject Test Loops and Pre-Flight Checks (`SCAR-PROC-104`, `SCAR-TEST-09`, `SCAR-TEST-10`)

In repositories with multiple subprojects (for example `backend/`, `frontend/`, and `scripts/`), keep inner loops fast:

1. Run fast pre-flight structural static analysis before you execute functional tests (`SCAR-TEST-10`). Catch syntax errors and undefined symbols early using `ruff`, `flake8`, or standard library AST parsers.
2. Run tests only inside the modified subproject directory during Target 30 (`SCAR-PROC-104`).
3. Run targeted tests in tight loops with fail-fast flags (`pytest path/to/test.py::test_name -v -x` or script equivalent) until green (`SCAR-TEST-09`).
4. Save the full repository test sweep for Target 50 before commit.

## 4. Concentric Documentation Rings (`SCAR-DOC-16`)

Update documentation alongside code across four rings:

- Ring 1 (Inline Code): Update docstrings, parameter descriptions, and return types in modified files.
- Ring 2 (Subproject README): Update module README files when public contracts change.
- Ring 3 (Guides and Examples): Update usage examples when flags or APIs change.
- Ring 4 (Changelogs): Add an entry under the unreleased section of `CHANGELOG.md` when the project maintains one.

When targeted tests and docs are complete, update `.workflow/state/99_next_action.md` and advance to Target 40.
