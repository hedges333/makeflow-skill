# 04. Six Pillars Adversarial Architecture & Code Review (`Target 40`, `42`, and `45`)

This manual defines the Six Pillars code audit, the Progressive Three-Tier Critic Hierarchy, the Git Merkle tree lock, and the human review gates.

---

## 1. Overview and Core Invariants

An agent that writes code possesses authoring bias. The author rationalizes compromises and overlooks edge cases. To guarantee software reliability, an independent critic must inspect the code diff with cold detachment.

### 1.1 The Mandatory Un-Primed Context Gate
Every code change must pass an adversarial code review before commit. The critic must not share the builder's conversational history. An un-primed context prevents the critic from inheriting the builder's false assumptions.

### 1.2 The Dijkstra Blocker Barrier (`SCAR-REVIEW-05`, `SCAR-PROC-77`)
Adversarial review is an impassable boolean gate:
$$\text{CanAdvanceToTarget45} \iff \operatorname{Count}(\text{CriticBlockers}) = 0$$

If the critic identifies any blocker:
1. The workflow halts immediately at **Target 42 (`triaged-blockers`)**.
2. The agent must not draft commit messages or push code.
3. The agent must present the Blocker Digest to the human operator and stop tool calls.
4. Remediation proceeds only after the human operator gives direction.

---

## 2. The Progressive Three-Tier Critic Hierarchy

The review invariant is universal across all agent runtimes. The execution mechanism adapts dynamically to the capabilities of the host environment:

### Tier 3: Dedicated Subagent (Antigravity Orchestrator)
When running in an orchestrator that supports subagents, launch `adversarial-critic` with `Model: "pro"` (Asymmetric Cognitive Tiering).
- **Tool Permissions**: `agents/adversarial-critic.md` mounts `run_command` via `tools:` (prompt-restricted strictly to read-only Git commands: `git diff`, `git log`, `git status`). Mutating commands and file edits are strictly prohibited.
- **Tool Restrictions**: `write_file`, `edit_file`, and subagent creation tools remain unmounted.
- **Cognitive Tiering**: Always assign high-capacity reasoning compute (`Model: "pro"`) so the critic catches subtle concurrency and logic defects.

#### The Six Subagent Invariants:
1. **Sequential Read-Before-Define Barrier (`SCAR-PROC-114`)**:
   Never invoke or define the critic in the same turn that reads this manual. Wait for the file read tool to return in Turn $t$ before calling `invoke_subagent` in Turn $t+1$. Calling both in parallel causes prompt hallucination.
2. **Canonical Six-Pillar Headings & Blast Radius Canary**:
   The critic report and chat summaries must use the exact six canonical level-3 headings:
   - `### 1. Intent`
   - `### 2. Verification`
   - `### 3. Architecture`
   - `### 4. Blast Radius`
   - `### 5. Reviewer Defense`
   - `### 6. Immune Defenses & Crash Antibodies`
   `Blast Radius` serves as a mandatory domain canary. The critic must write multi-sentence prose under every heading. Never compress findings into a six-row summary table.
3. **Self-Contained Review Artifacts Across Rounds**:
   Save each review round permanently as `.workflow/state/40_critic_review_round<N>.md`. Keep `.workflow/state/40_critic_review.md` updated with the complete, self-contained winning review prose. Never reduce the report to a link stub.
4. **Exhaustive Round 1 Falsification & Anti-Single-Blocker Satisficing (`SCAR-PROC-115`)**:
   Finding an initial blocker does not excuse relaxing scrutiny on remaining paths. The critic must evaluate all added or modified state flags across all enclosing control structures. Never excuse a new defect because older code has similar defects.
5. **Post-Compaction Rubric Anchoring in `00_governance.md` (`SCAR-PROC-121`)**:
   Context compaction can evict earlier instructions. Pin the six canonical headings verbatim inside `.workflow/state/00_governance.md` at Target 00. Re-read this manual if context compaction occurs before a subsequent review round.
6. **Explicit Manual Reference in Dispatch Prompt (`SCAR-PROC-105`)**:
   When launching `adversarial-critic`, the supervisor must explicitly pass `skills/makeflow/references/04_six_pillars_adversarial_review.md` in the dispatch prompt. Omitting this explicit path leads to shallow prompts and superficial reviews.

### Tier 2: Ephemeral Generic Subagent (Baseline Multi-Agent Runtimes)
If the runtime supports subagents but lacks specialized agent definitions, launch a generic read-only worker (`Role: "Adversarial Code Reviewer"`, `TypeName: "research"`, `Model: "pro"`). Package the full Six Pillars rubric and the `$APPROVED_TREE` hash directly inside the task prompt.

### Tier 1: In-Session Role Switch (Single-Agent CLI Runtimes)
In single-agent environments without subagent tools (such as Claude Code or basic CLIs), execute an In-Session Role Switch:
1. Temporarily adopt the Adversarial Reviewer persona.
2. Read the diff directly against origin/main.
3. Audit the diff with cold detachment across all six pillars.
4. Write the evaluation to `.workflow/state/40_critic_review.md`.
5. If blockers exist, revert to author mode to write failing reproduction tests and fixes before asking to commit.

### Dynamic Degradation Fallback Protocol (`SCAR-PROC-51`)
If a Tier 3 or Tier 2 subagent fails due to transient network or transport errors:
1. Do not crash or dump stack traces.
2. Fall back seamlessly to **Tier 1 (In-Session Role Switch)**.
3. Inspect `git diff origin/main...HEAD` directly and generate `.workflow/state/40_critic_review.md`.
4. Ensure `VERDICT: APPROVED (0 BLOCKERS)` is verified before proceeding.

---

## 3. Target 40: Adversarial Review Protocol

### 3.1 Lock the Merkle Tree (`SCAR-GIT-23`, `SCAR-DIFF-05`)
Stage all workspace changes and record the Git Merkle tree hash:
```bash
git add -A && git write-tree > .workflow/state/approved_tree.txt
```
Verify that zero untracked files remain (`git status --porcelain` is clean). The tree hash provides an immutable cryptographic anchor.

### 3.2 Model Picker Configuration Bypass and Interactive Turn Pause (GH-81)
If `.workflow/config.json` specifies models or "inherit", bypass the model picker prompt. The workflow honors configured model tiers automatically.
When model selection is unconfigured in interactive IDE environments, prompt the human user to switch to a deep-reasoning model.
Stop tool calls (`tool_calls: []`) to pause the turn before critic dispatch. Never dispatch `adversarial-critic` in the same turn as the model picker prompt.

### 3.3 Compute Direct Merkle Tree Diff
The critic computes the diff directly from the Git object database:
```bash
APPROVED_TREE=$(cat .workflow/state/approved_tree.txt)
git diff $(git merge-base HEAD origin/main) "$APPROVED_TREE"
```
Do not serialize intermediate diff files to disk. Intermediate diff files risk truncated redirects and formatting errors.

### 3.4 Mandatory Machine-Readable Binary Verdict Header (`SCAR-PROC-89`)
The critic report must conclude with an exact binary verdict header:
```text
VERDICT: APPROVED (0 BLOCKERS)
```
or
```text
VERDICT: REJECTED (<N> BLOCKERS)
BLOCKERS:
  - ID: BLOCKER-1
    PILLAR: <Pillar Name>
    LOCATION: <File:Line>
    WOUND: <Failure mode description>
    TRAP: <Root cause defect>
    DIAGNOSTIC_INVARIANT: <Invariant that must be satisfied>
```
Soft tokens (such as `REQUEST_CHANGES` or `NEEDS_WORK`) are prohibited. The critic must not supply copy-paste implementation code blocks.

### 3.5 Single-Shot Subagent Lifecycle (`SCAR-PROC-89`)
Never negotiate with, argue with, or send updated diffs to an existing critic subagent. Each review round requires a fresh, un-primed subagent instance.

---

## 4. The Canonical Six Pillars (Polyglot Engineering Audit)

The critic evaluates the diff against six engineering pillars. Every pillar must contain thorough architectural prose.

### 1. Intent
- **Requirement Completeness**: Does the diff fulfill all issue requirements without scope creep, unneeded refactorings, or piggybacking?
- **Dual-Audience Documentation (`SCAR-DOC-16`)**: Does the change provide operational instructions for maintainers and clear guides for end consumers?
- **Changelog and Release Hygiene**: If public APIs, packages, or schemas changed, did the author update unreleased notes and bump package manifest constraints?
- **Socratic Probe**: Does this diff contain any edit that is not strictly required to resolve the ticket?

### 2. Verification
Test-Driven Development (TDD) is the core discipline of this workflow. The critic must rigorously verify that the builder followed TDD methodology and engineered comprehensive, hermetic tests rather than shallow happy-path checks:

- **Red-Green-Refactor Protocol with Failure Falsification**:
  - Did the builder author failing reproduction tests *before* modifying production code?
  - Was each test proven genuine through failure falsification? The critic must verify that tests cannot pass vacuously, tautologically, or by accident.
  - If a bug fix is claimed, does the test fail when the fix is removed or reverted?
- **Comprehensive Boundary and Input Domain Testing**:
  - Does the test suite probe the full operational envelope, rather than a single nominal input?
  - **Boundary Limits**: Are empty collections, zero values, negative values, maximum integer limits, single-item collections, and off-by-one indices explicitly exercised?
  - **Negative and Error Paths**: Are malformed inputs, schema violations, timeout expiries, missing required fields, and permission rejections verified?
  - **State Machine Invariants**: Does the test diff maintain a 1-to-1 bijection with all state transitions, entry conditions, exit invariants, and rollback steps?
- **Defensive and Fallback Branch Preservation (`SCAR-TEST-24`)**:
  - Are fallback branches (`else { super.method(); }`, default switch branches, and catch handlers) thoroughly exercised?
  - Prohibit deleting defensive branches or error-recovery logic simply to game coverage metrics.
- **Genuine Verification vs Counterfeit Mock-Only Coverage (`SCAR-ARCH-18`)**:
  - Does the test suite verify real domain logic, data transformation, and serialization contracts?
  - Reject counterfeit tests that merely assert that a mock was called while leaving real logic and boundary conditions unexercised.
  - Ensure mock fixtures conform strictly to real interface types and invariants.
- **Hermetic Test Isolation (`SCAR-TEST-08`, `SCAR-TEST-11`)**:
  - Do tests run completely in memory, in temporary directories, or against hermetic mock fixtures?
  - Zero live production databases, cloud APIs, or mutable host files may be accessed during tests.
  - For network protocols, background daemons, or sockets, do tests bind exclusively to self-contained ephemeral loopbacks (`127.0.0.1:0` or temporary Unix domain sockets)? Unmanaged host daemons are strictly prohibited.
- **Burden-Balance Analysis (`SCAR-TEST-11`)**:
  - Does the test plan achieve deep boundary confidence without placing an excessive burden on execution time or dependencies?
  - Surface-only testing is prohibited when hermetic boundary testing is feasible.
- **Socratic Probe**: Could any new or modified test pass even if the underlying defect remained active in production?

### 3. Architecture
- **Contract and Symmetry Compliance**: Does the diff follow established project patterns and type contracts?
- **API Diamond Invariant (`SCAR-API-06`)**: Does the design maintain a minimal, stable core spine with rich compositional periphery?
- **Anti-Speculative Defenses and Caller Grounding (`SCAR-ARCH-07`)**: Does the diff defend only against inputs and states reachable by verified, real callers? Prohibit speculative defense layers, multi-source prose regexes, or unrequested CLI flag-order permutation handling for internal scripts invoked only by deterministic targets. Balance "the problem could occur" against "the problem will occur": if an edge case is certain to occur for actual callers, it must be addressed; if it is merely hypothetical problem-hunting without observed failures, defer defense until a bug is observed or reported.
- **Design Pattern Discipline**: Does the code use suitable Gang of Four or Game Programming Patterns (`14_design_patterns_and_architecture.md`) without introducing unnecessary abstraction layers?
- **Persistence Boundaries**: Does the code use an ORM for standard entity relationships? If processing high-volume datasets, are parameterized queries cleanly encapsulated behind repository interfaces?
- **Structural Decoupling**: Are user interfaces, business logic controllers, and runtime entry points cleanly separated?
- **Socratic Probe**: Does this design create tight coupling or premature abstractions that will complicate future maintenance?

### 4. Blast Radius
- **Cross-Module Regressions**: What are the risks to sibling modules, callers, database schemas, and downstream consumers?
- **Caller In-Degree**: Did the diff account for all callers and mock implementations of modified interfaces?
- **Chesterton's Fence**: Did the author preserve historical bug guards found in `git log`? If removing a guard, is the historical context explained?
- **Dependency Bounds**: Are upstream and downstream dependency bounds updated appropriately?
- **Concurrent Ticket Collision**: Does this change conflict with concurrent in-flight branches or shared resources?
- **Socratic Probe**: If this change breaks production at 3 AM, what is the exact failure cascade?

### 5. Reviewer Defense
- **Intentional Non-Changes**: What trade-offs were made, and why were alternative approaches or untouched adjacent files intentionally left alone?
- **Anticipation of Questions**: What questions will external human maintainers or automated bots raise? Are proactive explanations included?
- **Documentation Code Snippet Parity (`SCAR-DOC-07`)**: Are documentation and README code snippets updated to match new API signatures and lifecycle invariants?
- **Essential vs Counterfeit Invariants (`SCAR-INV-03`)**: Does the code defend essential business invariants rather than brittle implementation details?
- **Simple English Compliance**: Do comments, plans, and commit messages adhere to Simple English (`skills/makeflow/references/resources/language/english/`)?
  - Keep procedural sentences at 20 words or fewer.
  - Keep descriptive sentences at 25 words or fewer.
  - Put the condition before the command with a comma.
  - Define technical terms simply at first use.
  - Delete contractions, semicolons, em-dashes, and filler words.
- **Socratic Probe**: What is the strongest objection an external reviewer could raise against this diff?

### 6. Immune Defenses & Crash Antibodies
- **Two-Tier Scar Audit**: Audit the diff against Tier 1 core scars in `skills/makeflow/references/scars/` and Tier 2 project-local scars in `.workflow/scars/`.
- **Sensitive Runtime Boundaries**: Did the diff touch asynchronous streams, event loops, lifecycles, memory, or cache storage?
- **Asynchronous 4-Tuple Contract**:
  1. Re-check mounted or open state after asynchronous suspensions.
  2. Re-throw unexpected errors in intermediate catch blocks.
  3. Release acquired locks in guaranteed `finally` blocks.
  4. Cancel pending timers or background subscriptions on teardown.
- **Strict Exception Isolation (`SCAR-ERR-02`, `SCAR-ERR-03`)**:
  Do boundary observers and catch blocks catch specific recoverable exception types (e.g. `on Exception catch`)? Broad, silent error-swallowing blocks (such as bare `catch` or catching root `Object`) are strictly prohibited.
- **Defensive Validation & Range Clamping**:
  - Handle null pointers and empty collections explicitly.
  - Use `NULL` rather than sentinel dummy numbers (`-1`, `0.0`).
  - Clamp numerical inputs to safe operational envelopes before external calls.
  - Return unmodifiable snapshots of internal collections.
- **Socratic Probe**: What unexpected runtime exception or resource leak could crash this process under extreme load?

---

## 5. Target 42: Blocker Triage Gate (`BLOCKERS > 0`)

If Target 40 yields `VERDICT: REJECTED (<N> BLOCKERS)`:

### 5.1 The Zero-Tool Blocker Airlock (`SCAR-PROC-89`)
The agent is strictly prohibited from immediately modifying code, running tests, or arguing with the critic. The turn following a critic blocker wakeup must contain **zero tool calls (`tool_calls: []`)**.

### 5.2 The Blocker Digest
Present a structured Blocker Digest directly in chat:
1. **Blocker Manifest**: List every blocker ID, pillar, file location, wound, and trap.
2. **Remediation Strategy**: Propose a failing reproduction test and surgical fix for each blocker.
3. **Documentation Impact**: Note any documentation or changelog updates required.
4. **Non-Blocking Observations (NBO) Summary**: If the critic identifies NBOs during a rejected review round, present an NBO summary table in chat applying the 4-step observation triage protocol (`SCAR-PROC-108`). Include each observation's full technical description, `file:line` location, impact and rationale, scope relationship evaluation, and proposed disposition (`Apply` in the upcoming Target 30 remediation pass, `Skip` with justification, or `Defer to issue`).

### 5.3 Sovereign Human Direction
Halt and wait for explicit human steering:
- *"Proceed with remediation."*
- *"Update documentation per the proposed strategy."*
- *"Reject blocker as counterfeit per SCAR-INV-03."*
- Confirm or adjust proposed NBO dispositions (`Apply`, `Skip`, or `Defer to issue`).

After human authorization, return to Target 30 to implement reproduction tests and fixes, then dispatch a fresh critic for Round $N+1$.

---

## 6. Target 45: Review Sign-Off Gate (`0 BLOCKERS`)

If Target 40 yields `VERDICT: APPROVED (0 BLOCKERS)`:

### 6.1 Merkle Tree Verification
Verify that the working tree still matches the recorded Merkle tree:
```bash
test "$(git write-tree)" = "$(cat .workflow/state/approved_tree.txt)"
```

### 6.2 Canonical Prose Presentation
Present the review in chat using the exact six canonical headings. Never compress findings into a summary table.

### 6.3 Non-Blocking Observations (NBO) Matrix and 4-Step Triage Protocol (`SCAR-REVIEW-12`, `SCAR-REVIEW-28`, `SCAR-PROC-108`)
If the critic identifies non-blocking observations, present a structured matrix:
| # | Observation | Location | Scope Relationship | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Brief summary | `file:line` | `Very related` / `Somewhat related` / `Totally unrelated` | `Apply` / `Skip` / `Defer to issue` |

Do not label observations as `"Acknowledged"`. Evaluate and triage every observation using the **4-Step Observation Triage Protocol**:

1. **Existing Issue Deduplication**: Before proposing a new ticket, search open issues with `gh issue list` (or tracker API) to avoid duplicate backlog entries. If an existing issue covers the finding, link to it instead of proposing a duplicate.
2. **Scope Relationship and Rollup**: Interrogate the observation's relationship to current ticket scope:
   - **Very related**: Triage as a blocker for `tdd-worker`. Return to Target 30 for immediate resolution.
   - **Somewhat related**: Offer the human operator a choice to roll up the fix into the current change or file a new ticket.
   - **Totally unrelated**: Propose filing a new ticket (subject to human escalation).
3. **Conscious Dismissal**: If an observation presents negligible risk or outsized complexity, perform a conscious dismissal. Document the technical rationale to skip the observation without filing an issue.
4. **Human Escalation Gate**: Prohibit autonomous ticket creation. Require explicit human declaration before opening any new tickets.

### 6.4 Single-Target Turn Airlock (`SCAR-PROC-84`, `SCAR-PROC-123`)
Target 45 is an absolute turn-ending airlock:
1. Confirm that all accepted changes are tested and staged in the working tree.
2. Prohibit looking ahead to commit messages or pull request drafting.
3. Stop calling tools and wait for human review approval.
4. After approval, write `.workflow/state/45_review_approved.md` and advance to Target 50 (`commit`).
