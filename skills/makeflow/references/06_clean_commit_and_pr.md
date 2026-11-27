# 06. Clean Commit and Pull Request Creation (`Target 60`)

This manual covers the pre-push merge check, the human push gate, and pull request creation.

## 1. Pre-Push Merge Check (`SCAR-GIT-20`)

Never push a branch that conflicts with the remote default branch.

1. Run the deterministic upstream sync gate from `.workflow/state/Makefile`:
   ```bash
   make -f .workflow/state/Makefile check-main-sync
   ```
2. This gate fetches `origin/main` and tests a 3-way merge in memory with `git merge-tree`:
   ```bash
   git fetch origin main
   git merge-tree $(git merge-base HEAD origin/main) HEAD origin/main
   ```
3. If `git merge-tree` reports conflict markers, stop tool calls immediately without altering Git state (`SCAR-GIT-25`). Print the conflicting files and upstream conflict details in chat. Pause and await explicit operator direction before any rebase or merge action.
4. If the human operator directs a rebase, resolve conflicts with the operator.
5. Rebase conflict resolution invalidates the staged Merkle tree.
6. Re-stage all resolved files and update the approved tree lock:
   ```bash
   git add -A && git write-tree > .workflow/state/approved_tree.txt
   ```
7. Run pre-flight static checks:
   ```bash
   make test-static
   ```
8. Re-dispatch `adversarial-critic` with explicit manual path `skills/makeflow/references/04_six_pillars_adversarial_review.md` (`SCAR-PROC-105`) before push.
9. Run `make -f .workflow/state/Makefile adversarial-review FROM=T60` to advance from `T60` to `T40` and complete the fresh review.

### 1.1 Early Upstream Sync Across Target Transitions

Both `.workflow/state/Makefile` targets and the `workflow_advance_target` MCP tool check `origin/main` at each target transition:

1. From Target 10 through Target 40, if `origin/main` advances without conflicts, offer an operator-approved `git rebase origin/main` (`SCAR-GIT-25`).
2. At Target 45 and Target 50, preserve `.workflow/state/approved_tree.txt` (`SCAR-GIT-23`) when `origin/main` has zero conflicts.
3. If `origin/main` introduces merge conflicts at any transition, stop tool calls immediately without altering Git state (`SCAR-GIT-25`).
4. Report the conflicting files in chat and wait for explicit operator direction.
5. At Target 70 and Target 75, never rebase an open pull request (`SCAR-GIT-18`, `SCAR-GIT-25`). If conflicts arise on an open pull request, request operator approval to merge `origin/main` and return to Target 30.

## 2. Human Push Approval Gate

Target 60 is a mandatory human pause gate.
If zero Git remotes exist in Pure Git Mode (`SCAR-GIT-30`), skip Target 60 via `T50 -> T70`.
If any Git remote exists, enter Target 60 even when `HOSTING_MODE: PURE_GIT` is active.

1. Run `make -f .workflow/state/Makefile push APPROVED=1` to run `check-main-sync` and render `AWAITING_DECLARATION (human-push-approval)` in `.workflow/state/99_next_action.md`.
2. Print the branch name, remote repository name, and local `HEAD` commit hash in chat.
3. Stop tool calls (`tool_calls: []`) and wait for explicit user approval before running `git push`.

## 3. Push Branch and Open Pull Request

After the user approves the push, run these steps:

1. Push the feature branch to `origin`:
   ```bash
   git push -u origin <branch-name>
   ```
2. If hosting discovery detected GitHub mode (`SCAR-GIT-30`), open a pull request with `gh pr create`. Include the Six Pillars summary in the description:
   - Intent and scope summary.
   - Verification commands and passing test counts.
   - Architecture and API symmetry notes.
   - Blast radius review.
   - Readability notes.
   - Error handling and edge cases covered.
3. If a non-GitHub remote uses Pure Git Mode (`SCAR-GIT-30`), push the branch and skip `gh pr create`. Record `HOSTING_MODE: PURE_GIT` in `.workflow/state/15_archaeology_receipt.md`.
4. Run `make -f .workflow/state/Makefile bot-quiescence APPROVED=1` (or `humanPushApproved: true`) to advance to Target 70. Run `git push`, `gh pr create`, and `make -f .workflow/state/Makefile bot-quiescence APPROVED=1` in separate shell calls without `&&`.
