# Test-driven development resources: index and agentic synthesis

This directory defines the distilled test-driven development (TDD, writing tests before production code) curriculum for Makeflow agents.
The supervisor consults this curriculum at Target 20 planning, and `tdd-worker` applies it at Target 30 execution.

## 1. Six core TDD principles

Every implementation task follows six engineering rules across planning and execution.

1. Enforce the test-first iron law and verify meaningful Red failures before editing production code.
2. Scale vertical slice counts to task complexity instead of dumping tests upfront or padding trivial fixes.
3. Declare public test seams in `.workflow/state/20_plan.md` and test observable behavior at those seams.
4. Assert independent literal expectations and mock only external system boundaries through injected interfaces.
5. Keep test files frozen until `verify-green` passes, then refactor production and test code deliberately.
6. Reject agent shortcuts that excuse untested code or test-after rationalization.

## 2. Complexity-scaled TDD slice and worker round budget

The supervisor estimates task complexity during Target 20 planning.
The supervisor writes an explicit slice count and worker round estimate into `.workflow/state/20_plan.md`.
The supervisor then passes that budget inside the Target 30 `tdd-worker` dispatch prompt.

| Complexity tier | Example task | Planned TDD slices (Red-Green-Refactor cycles) | `tdd-worker` dispatches (rounds) |
| :--- | :--- | :--- | :--- |
| **Trivial (single behavior)** | Change one constant, swap one output string, or add one guard clause | **1 slice** (1 Red, 1 Green, quick Refactor check, `verify-regression`). Zero artificial padding. | **1 dispatch** |
| **Moderate (multi-behavior)** | New validation rules, CLI flag with error paths, or small state machine | **2 to 5** ordered vertical slices run sequentially inside one worker session. | **1 dispatch** |
| **Complex (multi-subsystem)** | Cross-layer refactor, polyglot feature, or large subsystem under context pressure | **5+** ordered vertical slices grouped into explicit milestone chunks. | **1 dispatch** by default, or **2+** milestone dispatches when context or subproject boundaries require separate rounds |

### Anti-padding and scope adaptation rules

1. Treat the supervisor slice budget as the target scope for Target 30.
2. Never add synthetic extra cycles or artificial padding to a trivial single-behavior fix.
3. If an unplanned edge case emerges, add 1 to 2 tightly scoped slices and report the reason.
4. If scope expands materially beyond the plan, stop and escalate with `RETRACTED_PREMISE`.
5. If a planned slice proves redundant during execution, collapse it and document the reason.

### Forensic cycle history in `lib/tdd_guard.py`

On a 1 slice trivial task, `.workflow/state/30_tdd_receipt.json` records `completed_cycles: []` with zero extra commands.
On a multi-slice task, running `witness-red` for the next slice archives the previous verified Green cycle into `completed_cycles`.
Each archived entry records `slice_index`, `test_cmd`, `red_exit`, `green_exit`, `witnessed_at`, and `verified_at`.
This trail gives `adversarial-critic` direct proof of every verified cycle without altering sealed test files.

## 3. Role routing across workflow phases

Use the table below to route each agent role to the active TDD resource modules.

| Workflow phase and agent | Required resource modules | Primary engineering deliverable |
| :--- | :--- | :--- |
| Target 20 (`supervisor`) | [`00_index_and_tdd_synthesis.md`](./00_index_and_tdd_synthesis.md), [`02_seams_behavior_and_mocking_boundaries.md`](./02_seams_behavior_and_mocking_boundaries.md), [`03_vertical_slicing_and_iterative_cycles.md`](./03_vertical_slicing_and_iterative_cycles.md) | Complexity tier, planned slice budget, worker round estimate, and pre-agreed public seams in `20_plan.md` |
| Target 30 (`tdd-worker`) | [`00_index_and_tdd_synthesis.md`](./00_index_and_tdd_synthesis.md), [`01_red_green_iron_law_and_rationalizations.md`](./01_red_green_iron_law_and_rationalizations.md), [`02_seams_behavior_and_mocking_boundaries.md`](./02_seams_behavior_and_mocking_boundaries.md), [`03_vertical_slicing_and_iterative_cycles.md`](./03_vertical_slicing_and_iterative_cycles.md) | Ordered Red-Green-Refactor execution honoring slice budgets, seam tests, and post-green cleanup |
| Target 40 (`adversarial-critic`) | [`01_red_green_iron_law_and_rationalizations.md`](./01_red_green_iron_law_and_rationalizations.md), [`02_seams_behavior_and_mocking_boundaries.md`](./02_seams_behavior_and_mocking_boundaries.md) | Audit of `completed_cycles`, boundary mocking, anti-tautology, and Pillar 2 test falsification |

## 4. Curriculum modules and precedence hierarchy

This curriculum distills seven license-verified open-source TDD resources into four modular references.

| Module file | Core focus | Precedence rank |
| :--- | :--- | :--- |
| [`00_index_and_tdd_synthesis.md`](./00_index_and_tdd_synthesis.md) | Constitutional TDD rules, complexity budget, and role routing | Rank 1 (overrides modules `01` through `03` on Makeflow gates) |
| [`01_red_green_iron_law_and_rationalizations.md`](./01_red_green_iron_law_and_rationalizations.md) | Iron law, meaningful Red, FIRST, Arrange-Act-Assert, and rationalization counters | Rank 2 (test-first execution discipline) |
| [`02_seams_behavior_and_mocking_boundaries.md`](./02_seams_behavior_and_mocking_boundaries.md) | Public seams, permanent contracts, anti-tautology, and boundary mocking | Rank 3 (test design and architecture quality) |
| [`03_vertical_slicing_and_iterative_cycles.md`](./03_vertical_slicing_and_iterative_cycles.md) | Slice sizing, tracer bullets, iterative recycling, characterization tests, and Target 30 protocol | Rank 4 (iteration strategy and legacy workflows) |

## 5. Upstream source attribution and license audit

Every adapted concept in this directory derives from seven open-source repositories verified for compatible permissive or attribution licenses.
You can redistribute and adapt these distilled guides under the repository MIT license while preserving upstream attribution notices.

| Upstream repository | URL | Verified license | Copyright or author | Distilled contribution |
| :--- | :--- | :--- | :--- | :--- |
| `obra/superpowers` | `https://github.com/obra/superpowers` | MIT | Jesse Vincent | Test-first iron law, meaningful Red verification, good test qualities, and rationalization counters |
| `mattpocock/skills` | `https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd` | MIT | Matt Pocock | Public seams, vertical slicing, tracer bullets, boundary-only mocking, and anti-tautology |
| `tweag/agentic-coding-handbook` | `https://github.com/tweag/agentic-coding-handbook` | MIT | Modus Labs | Tests-as-prompts, incremental TDD checklists, and tight agent scope control |
| `AsiaOstrich/universal-dev-standards` | `https://github.com/AsiaOstrich/universal-dev-standards` | CC BY 4.0 (docs) / MIT (CLI) | Universal Development Standards Contributors | FIRST test principles, Arrange-Act-Assert structure, and acceptance test alignment |
| `fheikens/stdd` | `https://github.com/fheikens/stdd` | CC BY 4.0 | Frank Heikens | Behavioral specifications as permanent contracts over disposable implementations |
| `emilybache/DiamondKata` | `https://github.com/emilybache/DiamondKata` | MIT | Emily Bache | Iterative test recycling versus incremental decomposition trade-offs |
| `emilybache/Racing-Car-Katas` | `https://github.com/emilybache/Racing-Car-Katas` | MIT | Emily Bache | Characterization tests before refactoring legacy SOLID violations |

### Transparent license exclusion note

We audited `mfranzon/tdd` (`https://github.com/mfranzon/tdd`) during GH-273 archaeology and omitted it because the repository has no `LICENSE` file and no license declaration.
Under default copyright law, unlicensed source text stays all-rights-reserved and cannot be adapted into Makeflow.
Standard increment-ordering and stack practices are covered by the seven verified sources above and `skills/makeflow/references/polyglot/`.
