# 05. Pre-Commit Hygiene and Git Commit Protocol (`Target 50`)

This manual covers pre-commit formatting, full test sweeps, tree drift checks, and atomic Git commits.

## 1. Pre-Commit Verification Steps

Run these four checks under `set -euo pipefail` before you propose a commit:

1. Format check: Run the project formatter in check mode, for example `prettier --check .`, `ruff format --check .`, `cargo fmt -- --check`, or `gofmt -l .`.
2. Static analysis: Run the project linter with warnings treated as errors, for example `eslint .`, `ruff check .`, `cargo clippy -- -D warnings`, or `golangci-lint run`.
3. Full repository test sweep: Confirm `.workflow/state/30_regression_receipt.json` passed in Target 30. Run the full test suite across every subproject before commit.
4. Two-layer tree drift check: Make sure that no unreviewed edits entered the workspace after Target 40:
   ```bash
   git diff --quiet
   test -z "$(git ls-files --others --exclude-standard)"
   test "$(git write-tree)" = "$(cat .workflow/state/approved_tree.txt)"
   ```

## 2. Commit Message Format

Write an atomic commit message to `.workflow/state/50_commit_msg.txt` using Conventional Commits (v1.0.0).

Conventional Commits defines structured commit titles. Use one of the 11 canonical types:
- `feat`: A new feature for the user
- `fix`: A bug fix for the user
- `docs`: Documentation changes only
- `style`: Formatting changes that do not alter code logic
- `refactor`: Code changes that neither fix a bug nor add a feature
- `perf`: Performance improvements
- `test`: Adding or correcting tests
- `build`: Changes to build systems or external dependencies
- `ci`: Changes to CI configuration files and scripts
- `chore`: Maintenance tasks and tool changes
- `revert`: Reverting a previous commit

Subject line rules:
- Format: `<type>(<scope>): <description>` or `<type>: <description>`.
- Use lowercase for type names.
- For breaking changes, add an exclamation mark `!` before the colon.
- Do not put a trailing period at the end of the description.

### 2.1 Mode A: Tracked Remote Issue (GitHub / GitLab)

```text
<type>(<scope>): <short summary> (closes #<ISSUE_ID>)

<plain English explanation of what changed and why>

Files changed:
- <path>:
  <1-line description of changes to the file>
  <1-line description of how it related to the current request>

Issue: #<ISSUE_ID>
Reviewed-by: adversarial-critic
```

On GitHub and GitLab, `closes #<ISSUE_ID>` links the pull request and closes the issue automatically upon merge.
Do not use `Ticket: GH-<N>` or `Ticket: LOCAL-<N>` on remote issues.

### 2.2 Mode B: Local Session Task (Pure Git Mode / Ad-Hoc Task)

```text
<type>(<scope>): <short summary>

<plain English explanation of what changed and why>

Files changed:
- <path>:
  <1-line description of changes to the file>
  <1-line description of how it related to the current request>

Ticket: LOCAL-<N>
Reviewed-by: adversarial-critic
```

> [!WARNING]
> Never use `closes #<N>`, `Closes #<N>`, `fixes #<N>`, or bare `#<N>` on local session commits. Remote git hosting services treat bare `#<N>` as an issue reference. Pushing a local commit with `closes #<N>` will trigger webhooks that accidentally close unrelated remote issues. Explicitly forbid auto-closing syntax on all local session commits.

### 2.3 Two-Line Changed Files Structure

If a commit message includes a `Files changed:` block, format every file entry with two indented lines:
- Line 1: Write a one-line description of changes to the file.
- Line 2: Write a one-line description of how the changes relate to the request.

Indent both lines under the file bullet. Do not use one line only. Do not add extra lines.

### 2.4 Helper Script

Use `scripts/commit_msg.py` to format and validate commit messages:

1. Validate a message file:
   ```bash
   python3 scripts/commit_msg.py validate --file .workflow/state/50_commit_msg.txt
   ```
2. Format a message file:
   ```bash
   python3 scripts/commit_msg.py format \
     --type feat \
     --scope gates \
     --desc "add commit helper script" \
     --issue 112 \
     --file-change scripts/commit_msg.py "Add helper script." "Fulfills ticket requirement." \
     --out .workflow/state/50_commit_msg.txt
   ```

## 3. Human Commit Approval Gate and Runtime Enforcement

Target 50 is a mandatory human pause gate.

1. Run `make -f .workflow/state/Makefile commit APPROVED=1` to verify `check-approved-tree`, validate `check-commit-msg`, and render `AWAITING_DECLARATION (human-commit-approval)` in `.workflow/state/99_next_action.md`.
2. Print `git status --short`, the diff stat, and the full text of `.workflow/state/50_commit_msg.txt` in chat.
3. Stop tool calls (`tool_calls: []`) and wait for explicit user approval in chat.
4. After user approval, commit the staged tree from the saved message file:
   ```bash
   git commit -F .workflow/state/50_commit_msg.txt
   ```
5. The `PreToolUse` hook validates every active Target 50 `git commit` command before execution.
   - Inline `-m` / `--message` values (joined with blank lines) and `-F <file>` / `--file=<file>` contents must pass Conventional Commits 1.0.0 and trailer rules.
   - The hook denies stdin commits (`-F -`, `--file=-`), missing files, mixed `-m` and `-F` flags, and bare commits with `SCAR-GIT-20`.
   - The hook denies alternate message flags (`-C`, `-c`, `--reuse-message`, `--reedit-message`, `--fixup`, `--squash`, `-t`, `--template`) and `--edit` / `-e` overrides.
   - `git commit --amend --no-edit` validates the current `HEAD` message fail-closed in the target repository directory.
   - Target 90 history commits, Target 80 landing commits, and inactive non-Makeflow workspaces stay unaffected.
   - `SCAR-PROC-102` pause-gate bundling and same-turn checks always run before commit message validation.
6. Run the standalone post-commit gate to verify the committed `HEAD` message and tree lock:
   ```bash
   make -f .workflow/state/Makefile check-post-commit
   ```
   This gate checks three invariants:
   - `git log -1 --pretty=%B HEAD` passes Conventional Commits 1.0.0 and trailer validation (`SCAR-GIT-20`).
   - The normalized `HEAD` message matches normalized `.workflow/state/50_commit_msg.txt` (`SCAR-GIT-20`).
   - `git rev-parse HEAD^{tree}` equals `.workflow/state/approved_tree.txt` (`SCAR-GIT-23`, `SCAR-GIT-25`).
7. If a Git remote exists, run `make -f .workflow/state/Makefile push APPROVED=1` (or set `humanCommitApproved: true`). If zero remotes exist in Pure Git Mode (`SCAR-GIT-30`), run `make -f .workflow/state/Makefile bot-quiescence APPROVED=1`. Both Target 50 exit transitions (`T50 -> T60` and `T50 -> T70`) run post-commit `HEAD` revalidation automatically before advancing.
