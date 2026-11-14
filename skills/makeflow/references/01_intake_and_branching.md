# 01. Intake, Feature Branching, and Readerboard (`Target 00` and `Target 10`)

This manual covers state restoration, ticket intake, Git branch setup, and live readerboard updates.

## 1. Target 00: Read State (`read-init-d`)

At the start of every turn, inspect `<workspace>/.workflow/state/`:

1. If `.workflow/state/` does not exist, start at Target 10.
2. If `.workflow/state/` exists, read all `.md` files in alphabetical order (`00_governance.md` through `99_next_action.md`).
3. Restore the active ticket ID, branch name, and active target.
4. If an active ticket is restored, update the terminal tab title to `{ticket_number} {project_name}: {issue_tag}`.
5. If the user only asks for status, print the readerboard in chat and stop.

## 2. Target 10: Ticket Intake and Git Branching

### Mode A: Active Ticket Provided

When the user provides a task description, issue number, or issue URL, run these steps:

1. Extract the ticket ID and scope from the request. When an issue number or URL is provided, fetch the full discussion thread with `gh issue view <number> --comments` (or API equivalent). Consolidate latest course corrections, design decisions, and scope changes into the ticket description (`SCAR-PROC-107`).
2. Make sure that `git status --short` is clean on the current branch. If uncommitted edits exist, stop and ask the user how to handle them.
3. Switch to the default branch (`main` or `master`) and pull the latest commits:
   ```bash
   git checkout main && git pull --ff-only
   ```
4. Exclude `.workflow/state/` and `.workflow/archive/` from Git tracking:
   ```bash
   git check-ignore -q .workflow/state/ || echo ".workflow/state/" >> .git/info/exclude
   git check-ignore -q .workflow/archive/ || echo ".workflow/archive/" >> .git/info/exclude
   ```
5. Run clean intake pre-flight check: If leftover state cards or `approved_tree.txt` from prior sessions exist in `.workflow/state/`, remove them (`unlink`, default) or move them to `.workflow/archive/<old-id>/` (`archive`) per `state.completedCardAction` before writing `10_ticket.md`.
6. Acquire the ticket lock to prevent collisions across multiple workspaces (`SCAR-SYS-08`). If another active workspace holds the ticket, stop intake immediately.
7. Create and switch to a feature branch that matches standard Git naming rules:
   - GitHub Bug (`#<number>` or `GH-<number>`): `fix/<number>-<slug>`, for example `fix/42-login-redirect`. Ticket ID is `GH-<number>`.
   - GitHub Feature (`#<number>` or `GH-<number>`): `feat/<number>-<slug>`, for example `feat/101-csv-export`. Ticket ID is `GH-<number>`.
   - General GitHub Issue (`#<number>` or `GH-<number>`): `ticket/<number>-<slug>`, for example `ticket/55-upgrade-deps`. Ticket ID is `GH-<number>`.
   - Jira or Linear Issue (`<PROJECT>-<NUMBER>`): `feat/<project>-<number>-<slug>` or `fix/<project>-<number>-<slug>`.
   - Ad-hoc Chat Task / Pure Git Session (no external tracker): Determine next local count `N` from the highest `LOCAL-<N>` in `.workflow/history.json`. Use `feat/local-<N+1>-<slug>` or `fix/local-<N+1>-<slug>`. Ticket ID is `LOCAL-<N+1>`. Never use bare integers for local branches.
8. Format `<slug>` with lowercase letters, digits, and single hyphens (`[a-z0-9-]`), up to 40 characters.
9. Write the consolidated ticket and comment details to `<workspace>/.workflow/state/10_ticket.md`.
10. Update the terminal title to `{ticket_number} {project_name}: {issue_tag}` by running `python3 <plugin_root>/scripts/terminal_title.py set`.
11. Advance to Target 15.

### Multi-Workspace Mutual Exclusion (`SCAR-SYS-08`)

A mutual exclusion lock prevents two workspaces from using one ticket.

Before you create a branch, acquire the ticket lock:

1. If you start intake, verify that no other workspace holds the ticket.
2. Compute the repository hash from the first Git commit.
3. Create the atomic lock directory at `~/.workflow/locks/<repo-hash>-<ticket>.lock`.
4. If the directory exists, read the recorded process ID and workspace path.
5. If the recorded workspace matches the current workspace, grant reentrant access.
6. If the session is active in another workspace, stop intake and report the collision.
7. If the session in the other workspace is dead or complete, purge the stale lock.
8. Record repository path, branch name, and process ID in `session.json`.
9. Register the lock with SignalTrap to clean it upon abnormal termination.
10. At Target 80 and Target 90, run `make -f .workflow/state/Makefile release-intake`.

### Operational Lock Commands

Use these commands to inspect or clear ticket locks:

1. To inspect lock status for a ticket, run:
   ```bash
   python3 scripts/workspace_mutex.py status --ticket <ticket-id>
   ```
2. To release an active lock directly, run:
   ```bash
   python3 scripts/workspace_mutex.py release --ticket <ticket-id>
   ```
3. To release the current ticket lock through make, run:
   ```bash
   make -f .workflow/state/Makefile release-intake TICKET=<ticket-id>
   ```

### Intake Blocker Detection and Operator Interview

During Target 10 intake, Makeflow checks for blocking GitHub issues.
A blocker is an unresolved upstream issue.
Makeflow inspects `issueDependenciesSummary` and `blockedBy` nodes through the GitHub GraphQL API.

If open blockers exist, intake halts with exit code 2 and emits reflex `INTAKE_DEPENDENCY_BLOCKED`.
Before you launch the archaeology worker, interview the human operator with two choices:

1. **Option 1: Stop Work (Default)**
   If you select Option 1, halt intake until engineers resolve the blocking issue.
   Release the ticket lock and wait.

2. **Option 2: Proceed with Archaeology Investigation**
   If you select Option 2, run intake with `ALLOW_BLOCKED=1` in the Makefile.
   Alternatively, pass `--allow-blocked` to `scripts/workflow_gates.py intake`.
   Launch the archaeology worker to evaluate dependency coupling against the blocker.

### Mode B: Open Backlog Discovery

When the user asks what to work on next, run these steps:

1. Query open issues from the issue tracker, for example `gh issue list`.
2. Exclude issues that already have an active branch or assignee.
3. Sort open items by priority label (`P0`, `P1`, `P2`, `P3`, then unlabeled).
4. Print the candidate list in chat.
5. If two or more candidates exist ($N \ge 2$), call `ask_question` so the user can pick one.

## 3. Live Readerboard (`99_next_action.md`)

Update `<workspace>/.workflow/state/99_next_action.md` at every target transition.

```markdown
# 99_next_action: Live Memento Readerboard

- Ticket: [<ID>: <Title>](<URL>) (In Progress)
- Branch: `feat/<ID>-<slug>`
- Active Target: Target 15 (`archaeology-verified`)
- Last Completed Step: Target 10 (`ticket-assigned`)
- Next Action: Fork archaeology-worker to scan subprojects and tests

## Milestone Progress
- [x] T00: read-init-d
- [x] T10: ticket-assigned
- [>] T15: archaeology-verified
- [ ] T20: approved-plan (Human Pause Gate)
- [ ] T30: implementation-diff (Red-Green TDD)
- [ ] T40: adversarial-review (Six Pillars)
- [ ] T45: approved-review (Human Pause Gate)
- [ ] T50: commit (Human Pause Gate)
- [ ] T60: push (Human Pause Gate)
- [ ] T70: bot-quiescence (180s CI poll)
- [ ] T75: ready-to-land (Human Pause Gate)
- [ ] T80: land (Merge and Cleanup)
- [ ] T90: finish (History Ledger)
```
