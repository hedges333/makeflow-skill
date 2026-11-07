# Git and Shell Execution Scars

This reference manual documents Git operations and shell execution scars (`SCAR-GIT-*` and `SCAR-SH-*`). These rules protect repository trees, history integrity, and shell command tokenization.

## Scars in this Category

| ID | Title | Scope |
| :--- | :--- | :--- |
| `SCAR-SH-01` | Prefer Temporary Script Files Over Complex Shell Quoting | Command Quoting & Script Isolation |
| `SCAR-SH-03` | Shell Redirection and Comment Stripping | Pre-Tool Guard Parsing |
| `SCAR-GIT-17` | Unchecked Direct Push CI Bypass Trap | Push Verification & CI Safety |
| `SCAR-GIT-20` | Pre-Push Merge Check | Branch Conflict Prevention |
| `SCAR-GIT-22` | Pre-Commit Working Tree Cleanliness Deadlock | Review Gate Staging & Cleanliness |
| `SCAR-GIT-23` | Native Merkle Tree Lock and TOCTOU Drift Attribution | Tree Hash Verification & Drift Prevention |
| `SCAR-GIT-24` | Conventional Commits Type-Scope Decoupling | Commit Message Classification |
| `SCAR-GIT-25` | Destructive Git Command Protection | Working Tree & Branch Safety |
| `SCAR-GIT-30` | Pure Git Mode and Repository-Scoped Hosting Discovery | Non-GitHub / Offline Workflows |

---

### SCAR-SH-01: Prefer Temporary Script Files Over Complex Shell Quoting

- **Wound**: An agent attempts complex multiline or quoted shell invocations (`bash -c "..."` or inline pipes), leading to syntax errors, unescaped variable expansion, or corrupted arguments.
- **Trap**: Trying to escape quote layers inline inside shell command strings, or relying on `chmod +x` file execution permissions on `noexec` mounts.
- **Permanent Reflex**: If shell commands require complex string escaping or multiple quoting layers, write the script to a file in a temporary directory. Pass the script path to the runtime interpreter command (`dart /path/to/script.dart` or `python3 /path/to/script.py`). Prefer Dart when available, falling back to Python. Explicit interpreter invocation bypasses `noexec` mount restrictions and eliminates reliance on `chmod +x` executable file permissions.
- **Socratic Probe**: Does this command involve fragile nested quotes that need a temporary script file? Will invoking the runtime interpreter bypass `noexec` restrictions and avoid `chmod +x` permission issues?

### SCAR-SH-03: Shell Redirection and Comment Stripping

- **Wound**: Complex shell commands containing redirections (`> /dev/null`) or inline comments fail parsing checks or bypass subcommand inspection guards.
- **Trap**: Naively tokenizing raw shell strings without stripping trailing redirections and comments before checking subcommands.
- **Permanent Reflex**: Strip shell redirections and unquoted comments before tokenizing command lines for Git and database safety checks.
- **Socratic Probe**: If you tokenize a raw shell string without stripping inline comments or redirections, could command safety checks be bypassed or fail? Will stripping unquoted comments and redirects reveal the true commands being executed?

### SCAR-GIT-17: Unchecked Direct Push CI Bypass Trap

- **Wound**: An agent pushes documentation edits directly to the default branch without running CI. A broken link or syntax error slips into production.
- **Trap**: Assuming that an edit is too trivial to require CI verification.
- **Circuit Breaker**: If you push to the default branch, never bypass automated CI verification.
- **Permanent Reflex**: Before you push, make sure that all commits pass local static analysis. If branch protection allows direct pushes, verify that push triggers run on CI.
- **Socratic Probe**: Is this commit verified locally, or are you bypassing checks because the edit seems trivial?

### SCAR-GIT-20: Pre-Push Merge Check

- **Wound**: An agent pushes a branch that conflicts with `main`, breaks CI, and requires history rewrites.
- **Trap**: Assuming that passing local unit tests means a clean merge with `origin/main`, or attempting an autonomous `git rebase` without human approval when conflicts appear.
- **Permanent Reflex**: Run `git fetch origin main` and `git merge-tree $(git merge-base HEAD origin/main) HEAD origin/main` before every push as an in-memory conflict detection check. Never rebase autonomously. If conflicts exist, stop immediately without altering Git state. Require explicit human operator approval under `SCAR-GIT-25` before any rebase or merge action.
- **Socratic Probe**: Even if local tests pass, has the remote target branch moved ahead since you branched? Will running `git merge-tree` detect upstream conflicts in memory without altering Git state, and do you require operator approval under `SCAR-GIT-25` before rebasing?

### SCAR-GIT-22: Pre-Commit Working Tree Cleanliness Deadlock

- **Wound**: A Makefile rule requires a clean Git status before review. Review requires uncommitted changes, so neither step can run.
- **Trap**: Demanding working tree cleanliness before code review completes.
- **Circuit Breaker**: If you evaluate pre-commit review gates, never require a clean working tree.
- **Permanent Reflex**: Separate pre-commit review from post-commit cleanliness. Make sure that review gates inspect the staged diff. Run cleanliness checks only after the commit completes.
- **Socratic Probe**: Are you checking diff correctness, or are you deadlocking on an uncommitted working tree?

### SCAR-GIT-23: Native Merkle Tree Lock and TOCTOU Drift Attribution

- **Wound**: An agent reviews code changes as text. A background formatter edits files before commit, so the committed code differs from the reviewed code.
- **Trap**: Relying on serialized text diffs instead of Git native object hashes.
- **Circuit Breaker**: If the index Merkle tree hash changes after review approval, halt immediately.
- **Permanent Reflex**: A TOCTOU race condition occurs between check and use. Capture the Git tree hash with `git write-tree` at Target 40. Make sure that the committed tree hash at Target 50 matches the approved tree hash exactly.
- **Socratic Probe**: Does the tree hash of this commit match the tree hash that the critic approved?

### SCAR-GIT-24: Conventional Commits Type-Scope Decoupling

- **Wound**: An agent labels an internal refactoring commit as `feat:`. Automated release tools publish a false feature in customer release notes.
- **Trap**: Treating any completed ticket as a user-facing feature.
- **Circuit Breaker**: If a commit contains internal plumbing or refactoring, never use the `feat:` prefix.
- **Permanent Reflex**: Use `feat:` only when the change adds user-facing capability. Use `refactor:`, `chore:`, or `fix:` for internal changes.
- **Socratic Probe**: Does this change add a visible feature for end users, or is it internal maintenance?

### SCAR-GIT-25: Destructive Working-Tree and History Guards

- **Wound**: An agent runs `git reset --hard`, `git clean -fd`, or `git checkout --` after a test failure and destroys uncommitted work.
- **Trap**: Blocking only `git push --force` to `main` while trusting prompt rules to stop other destructive Git commands.
- **Permanent Reflex**: Block work-discarding and force-push Git commands in hooks and MCP. Require explicit human approval before `git rebase` and `git branch -D`.
- **Socratic Probe**: If you run destructive Git commands to reset state, could you wipe out uncommitted user work? Would it be safer to isolate changes non-destructively and require human approval before altering history?

### SCAR-GIT-30: Pure Git Mode and Repository-Scoped Hosting Discovery

- **Wound**: An agent checks global `gh auth status` on a machine where GitHub CLI is logged in. The agent treats a local checkout or non-GitHub repository as a GitHub repo. The agent then stalls at Target 60 or Target 70 on failing `gh pr create` calls.
- **Trap**: Relying on global `gh auth status` rather than verifying repository remote origin and reachability.
- **Permanent Reflex**: At Target 15, discover hosting mode for the current repository. Verify that `git remote get-url origin` points to GitHub. Check that `gh auth status` succeeds. Verify repository reachability with `gh repo view`. If any check fails, record `HOSTING_MODE: PURE_GIT`. Skip remote PR creation at Target 60. Run full local test sweeps at Target 70. Present local test results at Target 75. Squash-merge locally on `main` at Target 80.
- **Socratic Probe**: Does this repository have a remote pointing to GitHub with working `gh repo view` access? If `gh` is globally logged in, will checking repository remote origin prevent failing downstream GitHub CLI calls?
