# 06. Clean Commit and Pull Request Creation (`Target 60`)

This manual covers the pre-push merge check, the human push gate, and pull request creation.

## 1. Pre-Push Merge Check (`SCAR-GIT-20`)

Never push a branch that conflicts with the remote default branch.

1. Fetch the latest default branch from `origin`:
   ```bash
   git fetch origin main
   ```
2. Test a clean merge in memory with `git merge-tree`:
   ```bash
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
9. Advance target from `T60` to `T40` to complete the fresh review.

## 2. Human Push Approval Gate

Target 60 is a mandatory human pause gate.

1. Print the branch name, remote repository name, and local `HEAD` commit hash in chat.
2. Set `.workflow/state/99_next_action.md` to `AWAITING_DECLARATION (human-push-approval)`.
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
3. If in Pure Git Mode (`SCAR-GIT-30`), record `HOSTING_MODE: PURE_GIT` in `.workflow/state/99_next_action.md` and skip `gh pr create`.
4. Advance to Target 70 with `humanPushApproved: true`.
