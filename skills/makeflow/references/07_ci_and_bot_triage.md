# 07. CI Quiescence, Bot Review Triage, and Landing (`Target 70`, `75`, and `80`)

This manual covers remote CI checks, review bot triage, the human landing gate, and branch cleanup.

---

## 1. Target 70: CI and Review Bot Quiescence

After opening a pull request, do not merge right away. Wait for remote continuous integration (CI) workflows and review bots to finish.

### 1.1 Asynchronous Subagent Delegation (`SCAR-PROC-95`, `SCAR-PROC-53`)

In multi-agent environments, the supervisor must remain at Depth 0:
1. **Never Sleep-Poll in the Supervisor**:
   Do not run blocking `time.sleep()` loops or long-running MCP tool calls directly in the supervisor context. Hanging RPC sockets block conversation turns and risk client timeouts.
2. **Fork `triage-worker`**:
   Launch `triage-worker` via `invoke_subagent` with `Model` set to `agyModelTiers["triage-worker"]` (default `inherit`).
3. **Immediate Supervisory Halt**:
   Stop tool calls (`tool_calls: []`) immediately after launching the subagent.
4. **Isolated Monitoring**:
   `triage-worker` monitors CI completion and review bot comments in its isolated worker session using `workflow_poll_ci` or `gh pr view`.
5. **Reactive Wakeup**:
   When CI is green and quiescence is satisfied (or if bot comments need triage), `triage-worker` classifies findings and notifies the supervisor via `send_message`. The platform delivers a reactive wakeup to resume the supervisor turn cleanly.
6. **Card Persistence**:
   The supervisor writes `.workflow/state/70_bot_triage.md` from the worker receipt and advances to Target 75.

### 1.2 Single-Agent CLI Fallback & Single-Probe Inspection

In single-agent environments without subagent tools (such as basic command-line interfaces):
1. **Single-Probe Non-Blocking Query**:
   Call `workflow_poll_ci` with `maxWaitSeconds: 1` to query current CI status instantly without sleeping.
2. **Timer-Based Wakeup**:
   If CI is pending, schedule a one-shot timer or pause between probes rather than running continuous shell sleep loops.

### 1.3 Review Bot Triage Actions

When review bots (such as Copilot, Gemini Code Assist, CodeRabbit, or SonarCloud) post comments, `triage-worker` classifies each finding into one of four actions:
- **`Accept`**: Real defect or security vulnerability that requires a code fix.
- **`Defend`**: Incorrect bot claim, paired with a clear technical rebuttal.
- **`Defer`**: Valid suggestion outside active ticket scope, logged for a future ticket.
- **`Acknowledge`**: Informational note that requires zero code changes.

### 1.4 Fixing Accepted Findings

If any accepted finding requires code modifications:
1. Return to Target 30, write a failing reproduction test, and implement the fix.
2. Run Target 40 adversarial review and secure Target 45 approval.
3. Run Target 50 commit and Target 60 push.
4. Re-enter Target 70 and dispatch `triage-worker` for a fresh quiescence cycle.
5. When CI is green and zero open comments remain, advance to Target 75.

---

## 2. Target 75: Ready to Land Gate

Target 75 is a mandatory human pause gate.

1. In GitHub mode, verify that `gh pr view` reports `mergeable: "MERGEABLE"` and `mergeStateStatus: "CLEAN"`. In Pure Git Mode (`SCAR-GIT-30`), verify that local test suites pass with `make all`.
2. Print the CI status summary (or local test sweep evidence in Pure Git Mode) and resolved bot comment count in chat.
3. Print the 7-criteria review policy table:
   - Security impact.
   - Breaking API changes.
   - Database schema changes.
   - Performance impact.
   - Documentation updates.
   - Dependency changes.
   - Test coverage.
4. Stop tool calls (`tool_calls: []`) and wait for `human-landing-approval`.
5. If branch protection forbids direct push, commit ledger updates on the feature branch before requesting landing approval.

---

## 3. Target 80: Merge and Branch Cleanup

After the user approves landing, merge the pull request and clean up local Git state:

1. In GitHub mode, squash-merge the pull request and delete the remote branch:
   ```bash
   gh pr merge <PR_NUMBER> --squash --delete-branch
   ```
   Verify that the linked GitHub issue is closed.
2. In Pure Git Mode (`SCAR-GIT-30`), squash-merge locally on `main`:
   ```bash
   git checkout main && git merge --squash <branch>
   git commit -F .workflow/state/50_commit_msg.txt
   git remote | grep -q "^origin$" && git push origin main || true
   ```
3. If an origin remote exists, pull the merged commit on `main`:
   ```bash
   git checkout main && (git remote | grep -q "^origin$" && git pull --ff-only || true)
   ```
4. Delete the local feature branch:
   ```bash
   git branch -d <branch>
   ```
5. Release the intake lock by running `make -f .workflow/state/Makefile release-intake` (`SCAR-SYS-08`).
6. Advance to Target 90 (`finish`).
