# Claude Code Guidelines: Makefile Workflow

This repository uses the Makefile Workflow. Follow these rules for every coding task.

## Core Rules

1. If `.workflow/state/` exists, read all `.md` files in alphabetical order (`cat .workflow/state/*.md`) to restore the active target.
2. Before you write code, make sure that `git status --short` is clean on `main`, then create a ticket branch.
3. Read target files before you edit them, apply small edits, and favor object composition. If large datasets outgrow an ORM, encapsulate parameterized SQL or NoSQL queries behind repository methods.
4. Point tests to isolated `*_test` databases, configured `database.testDatabases`, in-memory databases, or mock fixtures. If live versus test database names are unclear, ask the user first.
5. Write failing unit tests before you write feature code, and run tests in the active subproject directory.
6. Stop and wait for explicit user approval at all six pause gates (`SCAR-PROC-102`):
   - Target 20: Plan approval.
   - Target 42: Blocker triage, when review finds blockers.
   - Target 45: Review sign-off.
   - Target 50: Commit approval.
   - Target 60: Push approval.
   - Target 75: Pull request merge approval.
   Agents must never declare or approve gates themselves. Forbid agent self-approval.
7. Run targeted subproject tests at Target 30, and run the full repository test suite at Target 50 and Target 75.
8. Write documentation, commit messages, and pull request summaries in Simple English with short sentences and active voice.
9. Intercept POSIX signals (`SIGTERM` and `SIGINT`) in long-running workers and clean up lockfiles under `SCAR-SYS-07`.
10. If shell commands require complex string escaping or multiple quoting layers, write the script to a file in a temporary directory. Pass the path to the runtime interpreter under `SCAR-SH-01`.
11. Encapsulate script logic in testable classes using the BaseScript Template Method pattern under `SCAR-ARCH-05`. Separate library modules in `lib/` without shebangs, thin CLI wrappers in `scripts/`, and unit tests in `tests/`. Enforce polyglot layer separation (`entrypoints -> library`) and Target 20 structural debt triage under `SCAR-ARCH-08`.
12. Use atomic directory creation (`mkdir`) for mutual exclusion locks under `SCAR-SYS-08`.
13. Never arbitrarily impose `--limit` or truncation flags before assessing the full dataset. If output volume threatens context limits, halt tool execution immediately. Interview the user to select a triage path under `SCAR-TOOL-16`.

## Model Selection and Configuration

Read `.workflow/config.json` or `~/.gemini/config/makeflow.json` when present to load user model preferences.
If `.workflow/config.json` specifies models or "inherit", bypass the model switch prompt.
Use your session model for standard implementation tasks.
When unconfigured in interactive sessions, prompt the user to switch models and pause before review. At Target 40, run the Six-Pillar review with a high-reasoning model:

```bash
claude -p --model claude-3-opus-20240229 "Run Six-Pillar review on $(git merge-base HEAD origin/main)..$(cat .workflow/state/approved_tree.txt)"
```

## Target Summary

- `T00`: Read `.workflow/state/*.md` state cards.
- `T10`: Record ticket scope and create branch `feat/<id>-<slug>`, `fix/<id>-<slug>`, or `feat/local-<n>-<slug>` for local tasks.
- `T15`: Scan subprojects, detect languages, and build the Belnap fact table.
- `T20`: Write `.workflow/state/20_plan.md`, state test changes, and wait for Target 20 plan approval.
- `T30`: Run Red-Green-Refactor test-driven development and targeted subproject tests.
- `T40`: Run the Six Pillars code review.
- `T42`: Present blockers and wait for triage approval.
- `T45`: Present zero-blocker review and wait for sign-off.
- `T50`: Format, lint, run full tests, wait for Target 50 commit approval, and commit (forbid `closes #` on local commits).
- `T60`: Check clean merge with `git merge-tree`, wait for push approval, push, and open a pull request.
- `T70`: Poll CI checks and wait for review bots to finish (quiescence window).
- `T75`: Present policy checks and wait for merge approval.
- `T80`: Squash-merge the pull request, delete the ticket branch, and update `main`.
- `T90`: Record lessons and append the task to `.workflow/history.json` and `.workflow/history.md`.
