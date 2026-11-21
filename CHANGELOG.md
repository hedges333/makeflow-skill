# Changelog

All notable changes to this project appear in this file.
The format follows Keep a Changelog and Semantic Versioning.
Historical ticket records before the first tagged release live in `.workflow/history.md`.

## [Unreleased]

### Added

- GH-219: Add `lib/state_machine.py` to execute 15 Makefile workflow targets and auto-render `.workflow/state/99_next_action.md`.
- GH-211: Add `CHANGELOG.md` and the two-stage Target 90 release workflow.
- GH-211: Add `lib/target90_guard.py` for shared hook and MCP Target 90 gating.
- GH-211: Add interactive Target 90 release prompting for SemVer patch, minor, or major cuts.
- GH-211: Add Target 90 ticket resolution comments for GitHub issues and local tickets.
- GH-211: Add 21 polyglot TDD guides, code documentation standards, and tree verification checks.
- GH-211: Add stacked SQLite Synthetic Scars search (`workflow_query_scars`) and local scar capture.
- GH-211: Add AST scrubbing, cryptographically sealed TDD receipts (`witness-red`, `verify-green`), and turn boundary guards.
- GH-228: Enforce ticket-ID prefixes on Target 90 changelog bullets with extensible Strategy and Registry support.
- GH-232: Add SCAR-ARCH-09 upstream root-cause scar, SCAR-ARCH-0009 SQLite row, and planning, TDD, and review directives.
- GH-220: Enforce deterministic SCAR-PROC-95 supervisory halt airlock in ./hooks.json, lib/turn_boundary.py, scripts/pre_tool_guard.py, and workflow manuals while phase subagents run.
- GH-233: Add `lib/regression_gate.py` and `verify-regression` to enforce full active subproject regression sweeps before Target 40.
- GH-246: Add lib/repo_tree.py, repo-tree Makefile target, workflow_repo_tree MCP tool, and Target 15 file tree dispatch contracts.
- GH-215: Add SCAR-TEST-45 ephemeral temp isolation, SCAR-TEST-0045 registry row, UNSAFE_TEMP_FIXTURE AST lint rule, and TemporaryDirectory fixture migrations.

### Changed

- GH-219: Configure `.git/info/exclude` during `init-makefile` in `scripts/workflow_gates.py` and refresh `.workflow/state/Makefile` in `scripts/session_start.py`.
- GH-219: Route `workflow_advance_target` in `mcp/server.py` through GNU Make and enforce `lib/turn_boundary.py` across shell and Make transitions.
- GH-211: Load `serverInfo.version` dynamically from `./plugin.json` in `mcp/server.py`.
- GH-211: Set author metadata in `./plugin.json` to Mark Hedges.
- GH-211: Align hook and MCP commit gates on `docs(changelog):`, `docs(release):`, `chore(release):`, and `docs(scars):`.
- GH-224: Change terminal titles to start with the bare directory name and trailing slash instead of the project name.
- GH-212: Clarify optional query description when callers provide filters in workflow_query_scars MCP schema.

### Fixed

- GH-211: Route feature commits touching `CHANGELOG.md` to Target 50 instead of Target 90.
- GH-217: Allow `ci.quiescenceSeconds=0` and `MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS=0` across hooks, configuration loaders, and `workflow_poll_ci`.
- GH-216: Run MCP server tests through the `unittest` runner in `make test-mcp` and CI workflows, and split `TestMcpServer` into discrete methods.
- GH-237: Align .claude-plugin/plugin.json, .codex-plugin/plugin.json, and MCP fallback version to 0.0.1.
- GH-235: Resolve outer ancestor PTY and emulator hints before TERM=dumb, simplify title formats without backticks, and reset titles on finish.
- GH-248: Bypass Target 60 push pause in Pure Git Mode and align Target 80 squash-merge `git branch -D` cleanup with SCAR-GIT-25.
- GH-243: Preserve pre-subcommand `--repo-root` and `--lock-dir` flags and default to `os.getcwd()` across `local_tickets`, `workspace_mutex`, and `terminal_title` CLI runners.
- GH-244: Stage and validate only the 13-item runtime plugin allowlist in Makefile and install.sh, excluding repository artifacts and Python bytecode.
- GH-260: Enforce Simple English rules and exit code 2 in `lib/changelog.py` without legacy exemptions, and require `make test` before Target 90 commits.
- GH-258: Sanitize MAKEFLAGS, GNU Make recursion flags, and Makeflow exports in child regression, TDD, and Make advance subprocesses.
- GH-257: Anchor receipt hosting mode detection in lib/commit_msg.py and lib/workflow_gates.py to ignore prose quotes.
- GH-240: Persist structured ticket JSON in lib/ticket_state.py, remove Markdown regex scraping, deduplicate terminal titles, and codify SCAR-ARCH-10.
