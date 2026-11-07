# Makefile Workflow Plugin Management

MKFILE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SCRIPTS_DIR := $(MKFILE_DIR)/scripts
PLUGIN_NAME = makeflow
INSTALL_DIR = $(HOME)/.gemini/config/plugins/$(PLUGIN_NAME)
PYTHON ?= python3
REPO_ROOT ?= $(MKFILE_DIR)

.PHONY: all validate install uninstall test test-static ast-lint test-ast-scrub test-safe-copy test-env-preflight test-signal-trap test-mcp test-compat test-compat-cwd test-base-script test-terminal-title test-hooks test-reflex test-tdd-guard check-plugin-files check-skill-budget help

all: validate test

help:
	@echo "Available commands:"
	@echo "  make validate           - Check plugin manifest, skills, agents, and rules with agy"
	@echo "  make test               - Run all automated test suites and plugin structure gates"
	@echo "  make test-static        - Run pre-flight structural static analysis"
	@echo "  make test-safe-copy     - Test safe cross-user file promotion (SCAR-SYS-05)"
	@echo "  make test-env-preflight - Test native runtime environment path pre-flight (SCAR-SYS-06)"
	@echo "  make test-signal-trap   - Test POSIX signal trapping and worker cleanup (SCAR-SYS-07)"
	@echo "  make test-mcp           - Test Model Context Protocol (MCP) server over stdio"
	@echo "  make test-compat        - Check Claude, Cursor, Copilot, and ChatGPT adapters"
	@echo "  make test-compat-cwd    - Test arbitrary CWD subprocess timeout (GH-62)"
	@echo "  make test-base-script   - Test BaseScript execution lifecycle (SCAR-ARCH-05)"
	@echo "  make test-hooks         - Test SessionStart and PreToolUse hook scripts"
	@echo "  make test-reflex        - Test Vector 4 Reflex emitter and workflow gates (SCAR-ARCH-06)"
	@echo "  make test-tdd-guard     - Test cryptographically anchored TDD receipts (SCAR-TEST-12)"
	@echo "  make check-plugin-files - Check required plugin manifests and core skill files"
	@echo "  make check-skill-budget - Check SKILL.md context size ceiling (SCAR-PROC-99)"
	@echo "  make install            - Install plugin into ~/.gemini/config/plugins/$(PLUGIN_NAME)"
	@echo "  make uninstall          - Remove plugin from ~/.gemini/config/plugins/$(PLUGIN_NAME)"

validate:
	@if command -v agy >/dev/null 2>&1; then \
		echo "Checking plugin with agy..."; \
		(cd "$(MKFILE_DIR)" && agy plugin validate . --strict) || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
			--scar-id DOC-16 \
			--code PLUGIN_MANIFEST_VALIDATION_FAILED \
			--trigger "agy plugin validate . --strict failed." \
			--rejected "Shipping invalid plugin manifests, agent frontmatter, or skill metadata." \
			--reflex "Inspect agy validation errors above, fix manifest or frontmatter fields, and re-run make validate."; \
	else \
		echo "agy CLI not found in PATH. Skipping CLI check."; \
	fi

check-plugin-files:
	@$(PYTHON) "$(SCRIPTS_DIR)/workflow_gates.py" --repo-root "$(REPO_ROOT)" plugin-files

check-skill-budget:
	@$(PYTHON) "$(SCRIPTS_DIR)/workflow_gates.py" --repo-root "$(REPO_ROOT)" skill-budget

ast-lint:
	@echo "Running structural AST scrubber..."
	@$(PYTHON) "$(SCRIPTS_DIR)/ast_scrub.py" --src-dir scripts --strict

test-static: ast-lint
	@echo "Running pre-flight structural static analysis..."
	@$(PYTHON) "$(SCRIPTS_DIR)/static_check.py" || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id TEST-10 \
		--code STATIC_CHECK_FAILED \
		--trigger "scripts/static_check.py reported Python syntax or undefined-symbol errors." \
		--rejected "Running functional suites before structural static checks pass." \
		--reflex "Fix reported syntax or symbol errors and re-run make test-static."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_static_analysis.py" || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id TEST-10 \
		--code STATIC_ANALYSIS_SUITE_FAILED \
		--trigger "scripts/test_static_analysis.py failed." \
		--rejected "Proceeding when pre-flight static analysis tests fail." \
		--reflex "Run $(PYTHON) scripts/test_static_analysis.py directly, fix the regression, and re-run make test-static."

test-safe-copy:
	@echo "Testing safe cross-user file promotion..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_safe_copy.py" || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id SYS-05 \
		--code SAFE_COPY_SUITE_FAILED \
		--trigger "scripts/test_safe_copy.py failed." \
		--rejected "Using metadata-copying primitives across user boundaries." \
		--reflex "Inspect safe_copy.py stream-copy semantics with shutil.copyfile and re-run make test-safe-copy."

test-env-preflight:
	@echo "Testing native runtime environment path pre-flight..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_env_preflight.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id SYS-06 \
		--code ENV_PREFLIGHT_SUITE_FAILED \
		--trigger "scripts/test_env_preflight.py failed." \
		--rejected "Launching runtimes without writable TMPDIR fallback checks." \
		--reflex "Inspect env_preflight.py validate_runtime_path logic and re-run make test-env-preflight."

test-signal-trap:
	@echo "Testing POSIX signal trapping and worker cleanup..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_signal_trap.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id SYS-07 \
		--code SIGNAL_TRAP_SUITE_FAILED \
		--trigger "scripts/test_signal_trap.py failed." \
		--rejected "Leaving worker lockfiles or signal handlers unrestored on exit." \
		--reflex "Inspect SignalTrap LIFO cleanup and lockfile unlinking in signal_trap.py and re-run make test-signal-trap."

test-mcp:
	@echo "Testing MCP server JSON-RPC protocol..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_mcp_server.py" || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id GIT-25 \
		--code MCP_SERVER_SUITE_FAILED \
		--trigger "scripts/test_mcp_server.py failed." \
		--rejected "Breaking MCP state transition, tree check, or safety guard contracts." \
		--reflex "Run $(PYTHON) scripts/test_mcp_server.py directly, inspect failing JSON-RPC case, and fix mcp/server.py."

test-compat:
	@echo "Testing cross-agent compatibility..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_cross_agent_compatibility.py" || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id DOC-16 \
		--code CROSS_AGENT_COMPAT_FAILED \
		--trigger "scripts/test_cross_agent_compatibility.py reported contract drift." \
		--rejected "Breaking adapter, scar routing, pause gate, or manifest invariants." \
		--reflex "Read the reported compatibility errors above, align the affected file, and re-run make test-compat."

test-compat-cwd:
	@echo "Testing arbitrary CWD subprocess timeout..."
	@$(PYTHON) -m unittest "$(SCRIPTS_DIR)/test_compat_cwd.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id TEST-08 \
		--code COMPAT_CWD_SUITE_FAILED \
		--trigger "scripts/test_compat_cwd.py failed." \
		--rejected "Failing arbitrary CWD timeout or exception handling unit tests." \
		--reflex "Run $(PYTHON) -m unittest -v scripts/test_compat_cwd.py, fix failing tests, and re-run make test-compat-cwd."

test-base-script:
	@echo "Testing BaseScript execution lifecycle..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_base_script.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id ARCH-05 \
		--code BASE_SCRIPT_SUITE_FAILED \
		--trigger "scripts/test_base_script.py failed." \
		--rejected "Calling process-terminating sys.exit inside script business logic." \
		--reflex "Preserve BaseScript execute/setup/run/cleanup stream isolation and re-run make test-base-script."

test-terminal-title:
	@echo "Testing terminal title detection and setting..."
	@$(PYTHON) -m unittest "$(SCRIPTS_DIR)/test_terminal_title.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id ARCH-05 \
		--code TERMINAL_TITLE_SUITE_FAILED \
		--trigger "scripts/test_terminal_title.py failed." \
		--rejected "Failing terminal capability detection or title escape formatting." \
		--reflex "Run $(PYTHON) -m unittest -v scripts/test_terminal_title.py, fix failing tests, and re-run make test-terminal-title."

test-hooks:
	@echo "Testing SessionStart and PreToolUse hooks..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_hooks.py" || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id GIT-25 \
		--code HOOK_GUARD_SUITE_FAILED \
		--trigger "scripts/test_hooks.py failed." \
		--rejected "Weakening PreToolUse command guards or SessionStart state restoration." \
		--reflex "Run $(PYTHON) scripts/test_hooks.py directly, inspect the failing hook assertion, and re-run make test-hooks."

test-reflex:
	@echo "Testing Vector 4 Reflex emitter and workflow gates..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_emit_reflex.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id ARCH-06 \
		--code REFLEX_GATE_SUITE_FAILED \
		--trigger "scripts/test_emit_reflex.py failed." \
		--rejected "Breaking Vector 4 Reflex output contracts, gate exit codes, or SKILL.md budget." \
		--reflex "Run $(PYTHON) -m unittest -v -f scripts/test_emit_reflex.py, fix the failing gate or SKILL.md size, and re-test."

test-tdd-guard:
	@echo "Testing cryptographically anchored TDD receipts..."
	@$(PYTHON) "$(SCRIPTS_DIR)/test_tdd_guard.py" -v || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id TEST-12 \
		--code TDD_GUARD_SUITE_FAILED \
		--trigger "scripts/test_tdd_guard.py failed." \
		--rejected "Weakening witness-red sealing, verify-green replay, or forensic collision logging." \
		--reflex "Run $(PYTHON) -m unittest -v -f scripts/test_tdd_guard.py, fix the failing guard check, and re-run make test-tdd-guard."

test-ast-scrub:
	@echo "Testing structural AST scrubber..."
	@$(PYTHON) -m unittest -v scripts.test_ast_scrub || $(PYTHON) "$(SCRIPTS_DIR)/emit_reflex.py" \
		--scar-id AST-01 \
		--code AST_SCRUB_SUITE_FAILED \
		--trigger "scripts.test_ast_scrub failed." \
		--rejected "Weakening structural AST scrubber detection rules or execution speed." \
		--reflex "Run $(PYTHON) -m unittest -v scripts.test_ast_scrub directly, fix the regression, and re-run make test-ast-scrub."

test: test-static test-safe-copy test-env-preflight test-signal-trap test-compat test-compat-cwd test-base-script test-terminal-title test-mcp test-hooks test-reflex test-tdd-guard test-ast-scrub
	@echo "Testing file structure and skill context budget..."
	@$(PYTHON) "$(SCRIPTS_DIR)/workflow_gates.py" --repo-root "$(REPO_ROOT)" plugin-files
	@$(PYTHON) "$(SCRIPTS_DIR)/workflow_gates.py" --repo-root "$(REPO_ROOT)" skill-budget
	@echo "All tests passed."

install: validate test
	@if command -v agy >/dev/null 2>&1; then \
		echo "Installing with agy plugin install . ..."; \
		(cd "$(MKFILE_DIR)" && agy plugin install .); \
	else \
		echo "Copying plugin to $(INSTALL_DIR)..."; \
		rm -rf "$(INSTALL_DIR)"; \
		mkdir -p "$(INSTALL_DIR)"; \
		tar -C "$(MKFILE_DIR)" --exclude='./.git' --exclude='./.tmp' --exclude='./.venv' --exclude='./.workflow/state' --exclude='./.workflow/archive' --exclude='./.workflow/config.json' --exclude='./.workflow/plan.md' --exclude='./.workflow/commit_msg.txt' -cf - . | tar -xf - -C "$(INSTALL_DIR)"; \
		echo "Installed to $(INSTALL_DIR)"; \
	fi
	@rm -rf "$(INSTALL_DIR)/.workflow/state"
	@rm -rf "$(INSTALL_DIR)/.workflow/archive"

uninstall:
	@if command -v agy >/dev/null 2>&1; then \
		agy plugin uninstall $(PLUGIN_NAME) || true; \
	fi
	@rm -rf "$(INSTALL_DIR)"
	@echo "Uninstalled $(PLUGIN_NAME)."
