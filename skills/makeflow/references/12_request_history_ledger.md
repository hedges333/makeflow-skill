# 12. Request History Ledger

This manual describes the version-controlled history ledger that records completed tickets over the life of a repository.

## 1. Purpose of the Ledger

Chat sessions end, but the repository remains. The Request History Ledger records four things:

1. Every ticket completed through the Makefile Workflow.
2. Branch names, commit hashes, pull request links, and added test counts.
3. Synthetic Scars (process rules learned from past failures) earned during each task. The `scars_earned` list records merged core scar IDs and project-local `.workflow/scars/` entries. Never make unreviewed core catalog edits on `main` at Target 90.
4. Next local task numbers when creating branches for ad-hoc chat requests.

## 2. File Locations and Formats

Store both ledger files inside `.workflow/` at the repository root:

- `.workflow/history.json`: Machine-readable JSON array of completed ticket records.
- `.workflow/history.md`: Human-readable markdown summary table.

### JSON Schema (`history.json`)

```json
[
  {
    "id": "GH-42",
    "issue_url": "https://github.com/owner/repo/issues/42",
    "title": "Fix login redirect timeout on HTTPS",
    "request": "Handle 301 and 308 redirects without dropping authorization header",
    "branch": "fix/42-login-redirect",
    "subprojects": ["backend"],
    "started_at": "2026-10-29T10:00:00Z",
    "completed_at": "2026-10-29T10:45:00Z",
    "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
    "pr_number": 45,
    "pr_url": "https://github.com/owner/repo/pull/45",
    "closes_issue": true,
    "tests_added": 3,
    "scars_earned": [
      "SCAR-AUTH-02: Keep Authorization header on same-origin 308 redirects"
    ]
  },
  {
    "id": "LOCAL-1",
    "issue_url": null,
    "title": "Add health endpoint",
    "request": "Add /api/health endpoint returning uptime and database ping",
    "branch": "feat/local-1-health-endpoint",
    "subprojects": ["backend"],
    "started_at": "2026-10-29T11:00:00Z",
    "completed_at": "2026-10-29T11:30:00Z",
    "commit_sha": "f1e2d3c4b5a6789012345678901234567890ef01",
    "pr_number": 46,
    "pr_url": "https://github.com/owner/repo/pull/46",
    "closes_issue": false,
    "tests_added": 2,
    "scars_earned": []
  }
]
```

### Markdown Table Format (`history.md`)

```markdown
# Repository Request History Log

| Date | Ticket | Summary | Branch | PR | Tests | Scars Earned |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-10-29 | [GH-42](https://github.com/owner/repo/issues/42) | Fix login redirect timeout on HTTPS | `fix/42-login-redirect` | [#45](https://github.com/owner/repo/pull/45) | +3 | `SCAR-AUTH-02` |
| 2026-10-29 | LOCAL-1 | Add health endpoint | `feat/local-1-health-endpoint` | [#46](https://github.com/owner/repo/pull/46) | +2 | - |
```

## 3. Git Lifecycle by Repository Mode

Select the Git lifecycle that matches your branch protection settings:

### Mode 1: Pull Request Mode with Branch Protection

If branch protection forbids direct pushes to `main`, follow this lifecycle:
1. Update `.workflow/history.json` and `.workflow/history.md` during Target 75 on the feature branch.
2. Commit the ledger update to the feature branch with `git commit -m "docs(history): record <id>"`.
3. Push the feature branch to update the pull request. Wait for required status checks to pass before requesting landing approval.
4. When `gh pr merge --squash` runs at Target 80, the pull request merges the ledger record into `main`.
5. At Target 90, pull `main` to verify the ledger commit is present.

### Mode 2: Pull Request Mode with Direct Push Allowed

If direct pushes to `main` are permitted for automated workflows, follow this lifecycle:
1. Complete the pull request squash merge and branch deletion at Target 80.
2. Switch to `main` and pull the latest commits with `git checkout main && git pull --ff-only`.
3. Update `.workflow/history.json` and `.workflow/history.md` at Target 90.
4. Commit the ledger update with `git commit -m "docs(history): record <id>"`.
5. Push the ledger commit directly to `main` with `git push origin main`.

### Mode 3: Pure Git Mode

If the repository runs without pull requests, follow this lifecycle:
1. At Target 80, squash-merge the feature branch into `main` and delete the feature branch.
2. At Target 90, update `.workflow/history.json` and `.workflow/history.md`.
3. Commit the ledger files with `git commit -m "docs(history): record <id>"`.
4. Push `main` to the remote repository with `git push origin main`.

## 4. Update Steps at Target 90

Run these steps during Target 90 after a pull request merges:

1. Create `.workflow/` if the directory does not exist.
2. Read `.workflow/history.json`, or start with `[]` when the file is new.
3. Append the completed ticket object to `.workflow/history.json`.
4. Add a matching summary row to `.workflow/history.md`.
5. Commit the updated ledger files when the project tracks `.workflow/history.*` in Git: `git commit -m "docs(history): record <id>"`.
6. If direct push to `main` is permitted, push the ledger commit with `git push origin main`.
7. Archive or clean up completed state cards and `approved_tree.txt` per `state.completedCardAction`:
   - If `state.completedCardAction` is `unlink` (default), remove intermediate cards `10_` through `80_` and `approved_tree.txt` from `.workflow/state/`.
   - If `state.completedCardAction` is `archive`, move intermediate cards `10_` through `80_` and `approved_tree.txt` to `.workflow/archive/<ticket-id>/`.
8. Release the ticket lock by running `make -f .workflow/state/Makefile release-intake` (`SCAR-SYS-08`).
9. Mark `.workflow/state/99_next_action.md` as `COMPLETE (make finish satisfied)` and print the closing summary in chat.
