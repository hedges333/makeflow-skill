# GitHub Copilot Instructions: Makefile Workflow

Follow the Makefile Workflow lifecycle when assisting in this repository:

1. Read target files and their existing tests before you propose changes.
2. Do all work on a dedicated branch, such as `feat/<id>-<slug>` or `fix/<id>-<slug>` (use `feat/local-<n>-<slug>` for local tasks).
3. Write failing tests that reproduce the bug or specify the feature before you write production code.
4. Point tests to `*_test` databases, configured `database.testDatabases`, in-memory databases, or mock fixtures. Never connect to production databases.
5. Keep edits small and favor object composition. If large datasets outgrow an ORM, encapsulate parameterized SQL or NoSQL queries behind repository methods.
6. Audit every diff against the Six Pillars: Intent, Verification (TDD, failure falsification, boundary limits), Architecture, Blast Radius (mandatory canary), Reviewer Defense (intentional non-changes, Simple English), and Immune Defenses & Crash Antibodies.
7. In multi-language repositories, run tests in the affected subproject first, then run the full suite before commit.
8. Stop and wait for explicit user approval at all six pause gates: Target 20 (plan), Target 42 (blocker triage), Target 45 (review), Target 50 (commit), Target 60 (push), and Target 75 (landing). Never declare or approve gates yourself under SCAR-PROC-102. Forbid agent self-approval.
9. Write commit messages, documentation, and pull request summaries in Simple English: short sentences, active voice, and clear terms. Forbid `closes #` on local session commits.
10. If `.workflow/config.json` or `~/.gemini/config/makeflow.json` exists, read it to load user configuration.
11. Intercept POSIX signals (`SIGTERM` and `SIGINT`) in long-running workers and clean up lockfiles under `SCAR-SYS-07`.
12. If shell commands require complex string escaping or multiple quoting layers, write the script to a file in a temporary directory. Pass the path to the runtime interpreter under `SCAR-SH-01`.
13. Encapsulate script logic in testable classes using the BaseScript Template Method pattern under `SCAR-ARCH-05`. Separate library modules in `lib/` without shebangs, thin CLI wrappers in `scripts/`, and unit tests in `tests/`. Enforce polyglot layer separation (`entrypoints -> library`) and Target 20 structural debt triage under `SCAR-ARCH-08`.
14. Use atomic directory creation (`mkdir`) for mutual exclusion locks under `SCAR-SYS-08`.
15. Never arbitrarily impose `--limit` or truncation flags before assessing the full dataset. If output volume threatens context limits, halt tool execution immediately. Interview the user to select a triage path under `SCAR-TOOL-16`.

## Interactive Model Switch Reminder

If `.workflow/config.json` specifies models or "inherit", bypass the model picker prompt. When unconfigured, prompt the user in Copilot Chat at review boundaries:

1. At Target 40, prompt the user: "Select a deep-reasoning model in Copilot Chat for the Six Pillars review." Pause (stop tool calls) before dispatching the review.
2. At Target 45, tell the user: "Review passed. You can switch back to your default coding model."
