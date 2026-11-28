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
   `triage-worker` submits JSON via MCP `workflow_submit_report` (`reportType: "bot_triage"`). The server writes `70_bot_triage.json` and renders `70_bot_triage.md` atomically. In non-MCP runtimes, the supervisor writes `.workflow/state/70_bot_triage.md` from the worker receipt. Then run `make -f .workflow/state/Makefile ready-to-land` to advance to Target 75.

### 1.2 Single-Agent CLI Fallback & Single-Probe Inspection

In single-agent environments without subagent tools (such as basic command-line interfaces):
1. **Single-Probe Non-Blocking Query**:
   Call `workflow_poll_ci` with `maxWaitSeconds: 1` to query current CI status instantly without sleeping.
2. **Timer-Based Wakeup**:
   If CI is pending, schedule a one-shot timer or pause between probes rather than running continuous shell sleep loops.

### 1.3 Review Bot Triage Actions

When review bots (such as Copilot, Gemini Code Assist, CodeRabbit, or SonarCloud) post comments or CI checks fail, `triage-worker` queries `workflow_query_scars` (`workflowPhase: "Target 70: ci-triage"`) for matching diagnostic codes and classifies each finding into one of four actions:
- **`Accept`**: Real defect or security vulnerability that requires a code fix.
- **`Defend`**: Incorrect bot claim, paired with a clear technical rebuttal.
- **`Defer`**: Valid suggestion outside active ticket scope, logged for a future ticket.
- **`Acknowledge`**: Informational note that requires zero code changes.

### 1.4 Fixing Accepted Findings

If any accepted finding requires code modifications:
1. Run `make -f .workflow/state/Makefile implementation-diff FROM=T70`, write a failing reproduction test, and implement the fix.
2. Run Target 40 adversarial review and secure Target 45 approval.
3. Run Target 50 commit and Target 60 push.
4. Re-enter Target 70 and dispatch `triage-worker` for a fresh quiescence cycle.
5. When CI is green and zero open comments remain, run `make -f .workflow/state/Makefile ready-to-land`.

### 1.5 Multi-Contributor PR-Branch Firewalling, Peer Reviews, and Session Parking

Makeflow preserves zero-wait sole-developer behavior by default while supporting growing teams through opt-in `ci.*` configuration keys.

#### Always-On PR-Branch Firewalling
1. `workflow_poll_ci` queries a single pull request and its HEAD commit (`commits(last: 1)`) using `gh api graphql`.
2. This PR scope firewalls `CheckRun`, legacy `StatusContext`, comments, reviews, and threads inside the active branch (`SCAR-PROC-107`).
3. If PR `headRefName` does not match `expectedBranch` (or the local non-default Git branch), polling returns `ciStatus: "ERROR"` and `branchMismatch: true`.

#### Sole Developer Default vs Team Opt-In
1. When `ci.enablePeerReviews` is `false` (default), Target 70 checks PR-scoped CI, bot quiescence, and bot comments. It never blocks on human `+1` approvals or CODEOWNERS, and it returns review fields as passive metadata.
2. When `ci.enablePeerReviews` is `true`, Target 70 enforces GitHub branch protection rules (`requiredApprovingReviewCount`, `dismissesStaleReviews`, `requiresCodeOwnerReviews`). It also enforces `ci.minApprovals`, `ci.requireCodeOwnerReviews`, `CHANGES_REQUESTED`, and unresolved review threads.
3. If a non-admin token receives HTTP `403` or `404` from REST branch protection fallback, Makeflow degrades cleanly to `reviewDecision`, `mergeStateStatus`, `reviewRequests`, and local config floors.
4. If `CHANGES_REQUESTED`, CI `FAILURE`, or `CONFLICTING` occurs, Target 70 classifies `sessionStatus: "REMEDIATION_REQUIRED"` and routes back to Target 30 (`T70 -> T30`).

#### Session Parking and Resumption
1. When `ci.enableSessionParking` is `true` and a PR waits on peer approvals, CODEOWNERS, unresolved threads, or slow CI (`PENDING` or `TIMEOUT`), Target 70 recommends `PARKED`.
2. Run `make -f .workflow/state/Makefile park-session PR=<num> BRANCH=<branch> REASON="<reason>"` to persist `.workflow/state/70_parking_state.json`, update `70_bot_triage.md` and `99_next_action.md`, and print a Parking Receipt.
3. Stop tool calls after printing the Parking Receipt.
4. On demand at Target 00, run `make -f .workflow/state/Makefile resume-parked` to probe the PR once (`max_wait_sec=1`):
   - If ready, Makeflow clears `70_parking_state.json` and advances `T70 -> T75` (preserving mandatory `human-landing-approval` under `SCAR-PROC-102`).
   - If remediation is required, Makeflow clears parking and prints Target 30 recovery steps (`T70 -> T30`).
   - If still waiting, Makeflow refreshes `70_parking_state.json`, `70_bot_triage.md`, and `99_next_action.md`, then prints an updated Parking Receipt.
5. Optional cron polling: if `ci.parkingCronExpression` is set (for example `"*/30 * * * *"`), you may schedule background checks with `schedule(CronExpression=...)`. If repeated timer conflicts occur, yield immediately (`SCAR-PROC-138`) and rely on Target 00 on-demand resumption.

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
4. Stop tool calls (`tool_calls: []`) and wait for `human-landing-approval`. After approval, run `make -f .workflow/state/Makefile land APPROVED=1` (or `humanLandingApproved: true`) to advance to Target 80. Run `make -f .workflow/state/Makefile land APPROVED=1` in its own shell call without `&&`.
5. If branch protection forbids direct push, commit ledger and `CHANGELOG.md` updates on the feature branch before requesting landing approval.
6. If the pull request is `BEHIND_CLEAN`, the `ready-to-land` and `land` targets run a trial-merge of `HEAD` and `origin/main` (`SCAR-GIT-18`). You can also run `make -f .workflow/state/Makefile verify-trial-merge`. The gate uses a temporary detached `git worktree` and removes it when done. It seals `.workflow/state/75_trial_merge_receipt.json` for the pair of commit IDs. If `origin/main` moves, the receipt is stale and the gate runs again.
7. The trial-merge needs a regression command. If none is set, the gate stops with `MISSING_REGRESSION_COMMAND`. If the tests fail, the gate stops with `TRIAL_MERGE_SEMANTIC_CONFLICT`. In both cases, ask the operator before you merge `origin/main`, then return to Target 30 and Target 40.

---

## 3. Target 80: Merge and Branch Cleanup

After the user approves landing, merge the changes and clean up local Git state:

1. In GitHub mode, squash-merge the pull request and delete the remote branch:
   ```bash
   gh pr merge <PR_NUMBER> --squash --delete-branch
   ```
   Verify that the linked GitHub issue is closed.
2. In Pure Git Mode (`SCAR-GIT-30`), switch to `main` and squash-merge the ticket branch:
   ```bash
   git checkout main && git merge --squash <branch>
   git commit -F .workflow/state/50_commit_msg.txt
   ```
3. If an `origin` remote exists, push `main` and pull the updated head:
   ```bash
   git push origin main && git pull --ff-only
   ```
   If zero remotes exist, skip push and pull steps.
4. Verify the squashed commit on `main`. Because `git merge --squash` creates one new commit without merge parents, `git branch -d` fails (`not fully merged`). Request operator confirmation under `SCAR-GIT-25`, then force-delete the local ticket branch:
   ```bash
   git branch -D <branch>
   ```
5. Release the intake lock by running `make -f .workflow/state/Makefile release-intake` (`SCAR-SYS-08`).
6. Run `make -f .workflow/state/Makefile codified-scars-consolidated` to enter Target 90. Run `make -f .workflow/state/Makefile land APPROVED=1`, `gh pr merge`, and `make -f .workflow/state/Makefile codified-scars-consolidated` in separate shell calls without `&&`.
