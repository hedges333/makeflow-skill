# Antigravity `/teamwork`, Native Swarms, and `makeflow` Plugin Compatibility Analysis

This document analyzes how the Antigravity (`agy`) `/teamwork` slash command and Native Swarm skill interact with the `makeflow` plugin.

---

## 1. Architectural Comparison

A **swarm** (a group of parallel agents on a shared board) and the **`makeflow` supervisor** (a single-root dependency graph engine) use different control loops.

| Dimension | Antigravity `/teamwork` and `swarm` (`native_swarm/SKILL.md`) | `makeflow` Plugin (`skills/makeflow/SKILL.md`, `08_subagent_orchestration.md`) |
| :--- | :--- | :--- |
| **Topology** | Peer-to-peer message board (`swarm`, `post_message`, `read_board`, `swarm_wait`) with `coordinator`, `author`, `verifier`, `monitor`, and `narrator` agents. | Hierarchical Depth-0 `make` supervisor that dispatches five `invoke_subagent` roles (`archaeology-worker`, `tdd-worker`, `adversarial-critic`, `triage-worker`, `synthetic-scar-importer`). |
| **Execution Flow** | Asynchronous parallel fan-out (splitting work across concurrent agents) with sequence-numbered posts (`#seq`) and `@mentions`. | Deterministic sequential **DAG** (a dependency graph without loops) from `Target 00` through `Target 90` using *Conversation for Action* speech acts. |
| **Workspace Model** | Parallel authors use `workspace: "share"` or `"branch"` and commit locally before the coordinator deletes their workspaces with `kill`. | Single shared workspace (`workspace: "inherit"`), a single ticket lock (`SCAR-SYS-08`), and one staged **Git Merkle tree** (a hash of staged files, `SCAR-GIT-23`). |
| **Human Interaction** | Only the `coordinator` speaks to the user, while workers post only to the shared board. | Mandatory human pause gates (`tool_calls: []`) at Targets `20`, `42`, `45`, `50`, `60`, and `75` (`SCAR-PROC-102`). |
| **Model Selection** | All workers inherit the coordinator model unless the user asks for another model or quota runs out (`native_swarm/SKILL.md`). | Role-specific model tiers come from `.workflow/config.json` (default `"inherit"`), or the supervisor pauses at Target 40 for model selection. |

---

## 2. Five Concrete Runtime and Governance Conflicts

### Conflict 1: Global `AGENTS.md` and `SessionStart` Bleed into Swarm Workers

* **Mechanism**:
  * Every swarm worker (`author`, `verifier`, `monitor`, `narrator`) starts as a separate agent conversation.
  * When the `makeflow` plugin is installed globally, `hooks.json` runs `scripts/session_start.py` at the start of every agent session.
  * Global user rules in `rules/AGENTS.md` also load into every swarm worker conversation.
* **Observed Behavior**:
  * If a swarm worker runs with `workspace: "inherit"` (such as a `verifier`, `monitor`, or `narrator`), `scripts/session_start.py` finds `.workflow/state/*.md` and injects:
    `"Makefile Workflow found N state card(s)... Read all state cards at Target 00 (read-init-d) before taking new action."`
  * If a swarm worker runs with `workspace: "share"` or `"branch"`, `.workflow/state/` is untracked because `.git/info/exclude` hides it. `scripts/session_start.py` then injects:
    `"Makefile Workflow: Workspace is clean and ready for Target 10 intake."`
* **Failure Mode**:
  * If a swarm worker obeys `SCAR-PROC-102` in `rules/AGENTS.md`, it stops tool calls to wait for human approval at Target 20, Target 45, or Target 50.
  * In a swarm, workers cannot talk to the user (`native_swarm/SKILL.md`: *"the user talks to you and to nobody else"*).
  * A worker that pauses for direct human chat input stalls silently on the message board.

### Conflict 2: `PreToolUse` Hardware Guard Blocks Swarm Worker Local Commits

* **Mechanism**:
  * Native Swarm instructs parallel authors in `workspace: "share"` or `"branch"` to commit their changes inside their own working copies (`native_swarm/SKILL.md`).
  * Because `swarm{action: "kill"}` deletes the `share` or `branch` workspace permanently, authors must commit or upload their changes before termination.
  * Makeflow's `PreToolUse` hook (`scripts/pre_tool_guard.py`) intercepts every `run_command` call that invokes `git commit`.
* **Observed Behavior**:
  * `scripts/pre_tool_guard.py` checks `resolve_state_dir(payload)` for `.workflow/state/45_review_approved.md`.
  * On every non-history `git commit`, `evaluate_git_command()` returns `"decision": "force_ask"` with the message:
    `"Target 50 Commit Gate: 45_review_approved.md is missing in .workflow/state/. Make sure that Target 45 review passed before you approve."`
* **Failure Mode**:
  * An isolated `share` or `branch` worktree does not have `.workflow/state/45_review_approved.md`.
  * Every local `git commit` inside a background swarm author triggers an interactive `force_ask` approval gate or fails if unattended.
  * This blocks autonomous local commits across parallel worker branches.

### Conflict 3: Coordinator Role Inversion and Stall Monitor Timer Interference

* **Mechanism**:
  * In `native_swarm/SKILL.md`, the coordinator must not run builds, tests, git commands, or code edits directly.
  * When `swarm_monitor` is in the skill list, the coordinator must spawn a `monitor` worker at swarm creation (`swarm_monitor/SKILL.md`).
  * The `monitor` schedules a recurring **cron job** (a periodic timer task) with `CronExpression: "*/3 * * * *"` and `IsDaemon: true`.
  * In `skills/makeflow/SKILL.md` and `08_subagent_orchestration.md`, the Depth-0 supervisor alone writes `.workflow/state/*.md` cards (`SCAR-TOOL-13`), executes `.workflow/state/Makefile` gate targets, and parks with `tool_calls: []` at all six human pause gates.
* **Failure Mode**:
  * If a swarm remains live while the Makeflow supervisor waits at a human pause gate (Target 20, 42, 45, 50, 60, or 75), the `monitor` wakes every three minutes.
  * When the `monitor` sees that the coordinator and all workers are in the `waiting` state while the swarm has not posted `[done]`, it diagnoses a stall and posts `@coordinator [stall]`.
  * That `@coordinator` mention wakes the supervisor out of its human pause gate before the human operator has responded.

### Conflict 4: Single-Tree Merkle Lock (`SCAR-GIT-23`) and Workspace Mutex (`SCAR-SYS-08`) vs. Multi-Worktree Fan-Out

* **Mechanism**:
  * Makeflow enforces a **workspace mutex** (an atomic directory lock for one workspace) at `~/.workflow/locks/<repo-hash>-<ticket>.lock` (`01_intake_and_branching.md`, `SCAR-SYS-08`).
  * Makeflow seals a **TDD receipt** (a JSON record of failing and passing test runs) in `.workflow/state/30_tdd_receipt.json` (`SCAR-TEST-12`).
  * At Target 40, Makeflow locks the staged Git Merkle tree hash in `.workflow/state/approved_tree.txt` (`git add -A && git write-tree > .workflow/state/approved_tree.txt`, `SCAR-GIT-23`).
* **Failure Mode**:
  * If a swarm worker in a `share` or `branch` workspace runs `make -f .workflow/state/Makefile check-intake` for the same ticket ID, `scripts/workspace_mutex.py` rejects it as a multi-workspace collision.
  * If multiple swarm authors edit code in separate `share` worktrees, their edits exist on separate branches and working trees outside `.workflow/state/30_tdd_receipt.json` and `.workflow/state/approved_tree.txt`.

### Conflict 5: Peer Verifier Loop vs. Isolated Six-Pillar Adversarial Review

* **Mechanism**:
  * Native Swarm uses peer `verifier` workers (one verifier for every two to four authors) that converse with authors on the board until slices pass (`native_swarm/SKILL.md`).
  * Makeflow requires an isolated `adversarial-critic` subagent at Target 40 that audits the complete staged diff against `skills/makeflow/references/04_six_pillars_adversarial_review.md` (`SCAR-PROC-105`), followed by Target 42 or Target 45 human review approval.
* **Failure Mode**:
  * Peer verifier approval on the swarm board does not create `.workflow/state/40_critic_review.md`, `.workflow/state/45_review_approved.md`, or `.workflow/state/approved_tree.txt`.
  * Without those state artifacts, Makeflow's Target 45 and Target 50 Makefile gates (`check-approved-tree`, `check-commit-msg`) and `pre_tool_guard.py` block the commit.

---

## 3. Reconciliation Patterns and Recommendations

### Pattern A: Complementary Mode Separation (Zero Code Changes)

Use each orchestration tool for its target workload:

1. **Use `makeflow` (`invoke_subagent`) for single-ticket engineering**:
   * Dispatch `archaeology-worker` (Target 15), `tdd-worker` (Target 30), `adversarial-critic` (Target 40), and `triage-worker` (Target 70).
   * Keep one shared workspace, deterministic TDD receipts, and all six human pause gates.
2. **Use `run_workflow` (`run-workflow/SKILL.md`) for parallel fan-out inside a Makeflow target**:
   * When Target 15 archaeology or Target 40 analysis splits into many independent read-only checks, run a Python `run_workflow` script using `parallel(items, fn)`.
   * Because `run_workflow` agents return structured JSON schema outputs directly to the caller and terminate when the script finishes, they do not leave a `swarm_monitor` cron running or require worker-local git commits.

### Pattern B: Bounded Swarm Inside Target 15 or Target 30

If a ticket requires multi-worker swarm collaboration across many independent files, bound the swarm strictly inside a single Makeflow phase:

1. Run Target 10 intake and Target 20 plan approval in the root Makeflow supervisor before starting any swarm.
2. At Target 30, spawn the swarm to implement non-overlapping file slices across `workspace: "share"` workers.
3. Have the coordinator cherry-pick or apply the worker diffs into the primary ticket workspace.
4. Post `[done] <summary>` on the swarm board and run `swarm{action: "kill"}` on all workers so the `monitor` timer stops before leaving Target 30.
5. In the primary workspace, run Makeflow's `witness-red` / `verify-green` checks, lock `approved_tree.txt` at Target 40, dispatch `adversarial-critic`, and proceed through Targets 45–90 normally.

### Pattern C: Make the `makeflow` Plugin Swarm-Aware (Recommended Plugin Enhancements)

To allow `/teamwork` and Native Swarms to run smoothly while the `makeflow` plugin is active, update three components in `makeflow-skill`:

1. **Scope `rules/AGENTS.md` and `skills/makeflow/SKILL.md` to the Depth-0 Root Supervisor**:
   * State explicitly that `.workflow/state/*.md` card ownership (`SCAR-TOOL-13`) and the six human pause gates (`SCAR-PROC-102`) apply only to the root supervisor session that talks to the human user.
   * Instruct child subagents (`invoke_subagent`) and swarm workers (`<swarm_worker>`) to follow their delegated brief and report back to their parent or `@coordinator` without running Target 00/10 intake or pausing for direct human chat input.
2. **Add Swarm / Ephemeral Worktree Detection to `scripts/session_start.py`**:
   * Detect when a session starts inside a swarm worker context or an ephemeral `share` / `branch` worktree.
   * Suppress the `"Workspace is clean and ready for Target 10 intake"` and `"Read all state cards at Target 00"` system message injections for swarm workers (`author`, `verifier`, `monitor`, `narrator`).
3. **Make `scripts/pre_tool_guard.py` Worktree-Aware for Local Swarm Commits**:
   * Keep all destructive Git blocks (`git reset --hard`, `git clean -f`, `git checkout --`, force-push) and production database blocks active across all swarm workers.
   * When a swarm author runs `git commit` on an ephemeral `share` or `branch` worktree (outside the primary workspace that holds `.workflow/state/`), allow local commits without requiring `.workflow/state/45_review_approved.md`, while preserving the `force_ask` Target 50 Commit Gate on the primary ticket workspace.
