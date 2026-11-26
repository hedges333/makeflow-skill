# Vertical slicing, iterative cycles, and legacy characterization

- **Source Standards**: Adapted from `mattpocock/skills` (`tdd` skill), `tweag/agentic-coding-handbook`, `emilybache/DiamondKata`, and `emilybache/Racing-Car-Katas`.
- **License and Attribution**: `mattpocock/skills` (MIT License, Copyright Matt Pocock), `tweag/agentic-coding-handbook` (MIT License, Copyright Modus Labs), `emilybache/DiamondKata` (MIT License, Copyright Emily Bache), and `emilybache/Racing-Car-Katas` (MIT License, Copyright Emily Bache). Distilled and adapted for Makeflow agents.

This module explains how to calibrate TDD slice counts, execute vertical slices inside Target 30, recycle tests iteratively, and refactor legacy code safely.

## 1. Complexity-scaled slice and round sizing

Not every ticket needs multiple TDD cycles or multiple worker dispatches (`SCAR-TEST-46`).
Match the slice count to the true behavioral complexity of the task during Target 20 planning.

| Complexity tier | Example task | Planned TDD slices | `tdd-worker` dispatches |
| :--- | :--- | :--- | :--- |
| **Trivial (single behavior)** | Change one output string, fix one constant, or add one guard clause | **1 slice** (1 Red, 1 Green, quick Refactor check). Zero padding. | **1 dispatch** |
| **Moderate (multi-behavior)** | New validator rules, CLI flag with error paths, or small state machine | **2 to 5** ordered vertical slices inside one worker session. | **1 dispatch** |
| **Complex (multi-subsystem)** | Cross-layer refactor, polyglot feature, or high-risk subsystem | **5+** ordered vertical slices grouped into explicit milestones. | **1 dispatch** by default, or **2+** milestone dispatches when warranted |

### Anti-padding discipline

1. Execute 1 slice for a trivial single-behavior change instead of inventing synthetic sub-slices.
2. Never dump all tests for a multi-behavior feature upfront before writing the first slice.
3. If an unplanned edge case surfaces, add 1 to 2 scoped slices and note the reason.
4. If scope grows beyond the planned tier, halt and escalate with `RETRACTED_PREMISE`.
5. If a planned slice becomes redundant, collapse it and record the reason in your report.

## 2. Vertical slicing versus horizontal dumping

Horizontal test-first dumping writes ten failing tests across every edge case before any production code exists (`SCAR-TEST-46`).
Horizontal dumping forces guessing across unbuilt interfaces and produces speculative tests that break as soon as the first slice clarifies the design.

Vertical slicing writes one failing test for one concrete behavior, makes that test pass with minimal code, and refactors before moving to the next behavior.

### Tracer bullet first slice

Start multi-behavior features with a thin end-to-end tracer bullet slice.

1. Pick the simplest happy-path scenario that crosses the declared public seam.
2. Write one failing test for that path and witness Red.
3. Write the smallest working implementation to turn that slice green.
4. Add boundary, error, and edge-case slices one by one in ordered cycles.

## 3. Iterative test recycling versus incremental decomposition

Adapted from Emily Bache (`DiamondKata`), recognize two distinct ways to grow complex algorithms under TDD.

- **Iterative test recycling**: Start with a coarse case and generalize the algorithm step by step. Update earlier examples once the full contract stabilizes.
- **Incremental decomposition**: Build small orthogonal helper behaviors one slice at a time when sub-problems have independent contracts.

For agentic sessions with `SCAR-TEST-12` sealed receipts, prefer incremental additive slices so earlier regression tests remain intact and verifiable.
If an exploratory prototype test must be generalized, modify or clean it only during post-green Refactor after `verify-green` passes.

## 4. Characterization tests before legacy refactoring

Adapted from Emily Bache (`Racing-Car-Katas`), never refactor untested legacy code directly.
Legacy modules with tight coupling or SOLID violations can hide subtle production behaviors.

Follow four steps before refactoring legacy code:

1. Write characterization tests that pin current observable behavior at the outermost available seam.
2. Run the characterization suite and confirm every baseline test passes.
3. Introduce minimal constructor injection or interface seams to decouple hard-coded collaborators.
4. Refactor internals in small steps while keeping the characterization suite green after each edit.

## 5. Post-green Refactor timing under `SCAR-TEST-12`

Makeflow seals the test directory hash between `witness-red` and `verify-green`.
Editing any test file before `verify-green` completes triggers `EACCES_TEST_TAMPERING` (`SCAR-TEST-49`).

Execute each Target 30 slice in four strict phases.

1. **Red**: Add tests for the active slice only, verify meaningful failure, and run `witness-red`.
2. **Green**: Write the smallest production edit that passes the sealed command, keeping tests untouched.
3. **Verify Green**: Run `make -f .workflow/state/Makefile verify-green` to seal the passing slice.
4. **Refactor**: Clean duplicate logic in production code, polish test helpers, and re-run targeted tests.

### Multi-slice receipt continuity

When you start Slice 2 with `witness-red` after Slice 1 passed `verify-green`, `lib/tdd_guard.py` archives Slice 1 into `completed_cycles`.
On a single-slice trivial task, `completed_cycles` remains `[]` with zero extra commands.
Never skip post-green Refactor, and seal `.workflow/state/30_regression_receipt.json` with `verify-regression` once all slices finish.
