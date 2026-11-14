# 13. Configuration and User Preferences

This manual explains how users assign custom AI models and review limits in their local environments without editing shared plugin files.

## 1. Portability Design

Different teams use different AI providers, API keys, and cost budgets. For that reason, `./agents/*.md` files omit hardcoded `model:` fields. Each user sets local preferences through JSON configuration files or environment variables.

## 2. Configuration Precedence Order

When a session starts or `workflow_get_config` runs, the plugin resolves configuration in this order (later environment variables override JSON values):

1. Workspace file: `<workspace>/.workflow/config.json` (git-ignored).
2. Global user file: `~/.gemini/config/makeflow.json`.
3. Environment variables:
   - `MAKEFILE_WORKFLOW_CRITIC_MODEL` for `adversarial-critic`.
   - `MAKEFILE_WORKFLOW_WORKER_MODEL` or `MAKEFILE_WORKFLOW_ARCHAEOLOGY_MODEL` for `archaeology-worker`.
   - `MAKEFILE_WORKFLOW_TDD_MODEL` for `tdd-worker`.
   - `MAKEFILE_WORKFLOW_TRIAGE_MODEL` for `triage-worker`.
   - `MAKEFILE_WORKFLOW_SCAR_IMPORTER_MODEL` for `synthetic-scar-importer`.
   - `MAKEFILE_WORKFLOW_PROD_DBS` for comma-separated live database names to block.
   - `MAKEFILE_WORKFLOW_TEST_DBS` for comma-separated safe test database names to allow.
   - `MAKEFILE_WORKFLOW_PROD_HOSTS` for comma-separated live database hosts to block.
   - `MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS` for continuous integration quiet window in seconds (default 180).
   - `MAKEFILE_WORKFLOW_CI_POLL_INTERVAL` for continuous integration polling interval in seconds (default 30).
   - `MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION` for completed state card action (`unlink` or `archive`, default `unlink`).
   - `MAKEFLOW_DISABLE_TERMINAL_TITLE` set to `1` or `true` to disable automatic terminal tab title updates.
4. Canonical defaults in `./default_config.json` (packaged at repository root). Single-item user overrides in `.workflow/config.json` deep-merge recursively into defaults without dropping unset default fields:
   - `adversarial-critic`: `inherit`
   - `archaeology-worker`: `inherit`
   - `tdd-worker`: `inherit`
   - `triage-worker`: `inherit`
   - `synthetic-scar-importer`: `inherit`
   - `ci.quiescenceSeconds`: `180`
   - `ci.pollIntervalSeconds`: `30`
   - `state.completedCardAction`: `unlink` (removes intermediate cards `10_` through `80_` and `approved_tree.txt` at Target 90; set to `archive` to preserve them in `.workflow/archive/<ticket-id>/`).
   - Database guard: blocks standalone `prod` and `production` names automatically.

## 3. Configuration File Schema

Copy [`.workflow/config.json.example`](../../../.workflow/config.json.example) to `<workspace>/.workflow/config.json` or `~/.gemini/config/makeflow.json`:

```json
{
  "models": {
    "adversarial-critic": "inherit",
    "archaeology-worker": "inherit",
    "tdd-worker": "inherit",
    "triage-worker": "inherit",
    "synthetic-scar-importer": "inherit"
  },
  "review": {
    "maxReviewRounds": 3,
    "strictSixPillars": true
  },
  "ci": {
    "pollIntervalSeconds": 30,
    "quiescenceSeconds": 180
  },
  "database": {
    "prodDatabases": [],
    "testDatabases": [],
    "prodHosts": []
  },
  "state": {
    "completedCardAction": "unlink"
  }
}
```

### Hybrid Database Isolation Rules

Both `scripts/pre_tool_guard.py` and `workflow_safety_check` read the `database` block and `MAKEFILE_WORKFLOW_*` variables:

1. Any command targeting an entry in `prodHosts` or `prodDatabases` is denied at once, even if its name does not include `prod`.
2. Any command targeting an entry in `testDatabases` on a non-production host is allowed, so SQL queries selecting a column named `prod` do not trigger false alarms.
3. If no custom list matches, the built-in `prod` and `production` guard remains active.
4. When a repository uses databases whose live or test names are not clear from manifests or `.workflow/config.json`, the agent asks the user at Target 15 or Target 20.

### Model Tiers in Antigravity (`agy`)

Subagent model tiers are specific to Google Antigravity (`agy`). Other platforms run as single chat sessions using the active session model.

Antigravity `invoke_subagent` accepts four model tier names:

- `inherit`: Uses the active model of the parent session and inherits the session reasoning effort (`/effort`).
- `pro`: High-capacity reasoning models such as Gemini 4 Argon or Claude Opus.
- `flash`: Fast workhorse models such as Gemini 3.8 Flash.
- `flash_lite`: Lightweight low-cost models.

When set to `inherit`, subagents inherit both the model and the chat session reasoning effort (`/effort`). When set to an abstract tier (`pro`, `flash`, `flash_lite`), Antigravity uses backend tier presets with standard effort.

If you put a concrete model ID such as `gemini-4-argon-high` in `models`, `workflow_get_config` normalizes it under `agyModelTiers`. Each role maps to `pro`, `flash`, `flash_lite`, or `inherit`.

## 4. Agent File Shadowing in Antigravity

Antigravity loads agent definitions in priority order from three paths:

1. Workspace path: `<workspace>/.agents/agents/<name>.md`
2. Global user path: `~/.gemini/config/agents/<name>.md`
3. Plugin path: `~/.gemini/config/plugins/makeflow/agents/<name>.md`

If you want custom prompt rules for `adversarial-critic`, create `~/.gemini/config/agents/adversarial-critic.md`. Antigravity loads your file instead of the plugin copy, and your file survives plugin upgrades.

## 5. Interactive IDE Prompts (Cursor, Copilot, ChatGPT)

Interactive chat IDEs cannot switch models through subagent API calls.

If `.workflow/config.json` specifies models or 'inherit', bypass the model picker prompt. The workflow respects your pre-configured model tier without user interruption.

When model selection is unconfigured, the agent prints reminders in chat at review boundaries:

1. At Target 40, prompt the user: "Switch the model picker to a deep-reasoning model for the Six-Pillar review." Stop tool calls (`tool_calls: []`) to pause for model selection before critic dispatch.
2. At Target 45 (Review Sign-Off), prompt the user: "Review passed. You can switch back to your fast coding model for the remaining steps."
