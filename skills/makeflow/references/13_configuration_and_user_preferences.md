# 13. Configuration and User Preferences

This manual explains how users assign custom AI models and review limits in their local environments without editing shared plugin files.

## 1. Portability Design

Different teams use different AI providers, API keys, and cost budgets. For that reason, `./agents/*.md` files omit hardcoded `model:` fields. Each user sets local preferences through JSON configuration files or environment variables.

## 2. Configuration Precedence Order

When a session starts or `workflow_get_config` runs, Makeflow cascades configuration from lowest to highest precedence:

1. Canonical defaults in `./default_config.json` (packaged at repository root).
2. Legacy global file: `~/.gemini/config/makeflow.json` (preserved for backward compatibility).
3. Active agent user-global file: `<agent_plugin_data>/config.json` (selected by `MAKEFLOW_AGENT`, defaulting to `gemini`).
4. Project-local workspace file: `<workspace>/.workflow/config.json` (git-ignored).
5. Environment variable overrides:
   - `MAKEFILE_WORKFLOW_CRITIC_MODEL` for `adversarial-critic`.
   - `MAKEFILE_WORKFLOW_WORKER_MODEL` or `MAKEFILE_WORKFLOW_ARCHAEOLOGY_MODEL` for `archaeology-worker`.
   - `MAKEFILE_WORKFLOW_TDD_MODEL` for `tdd-worker`.
   - `MAKEFILE_WORKFLOW_TRIAGE_MODEL` for `triage-worker`.
   - `MAKEFILE_WORKFLOW_SCAR_IMPORTER_MODEL` for `synthetic-scar-importer`.
   - `MAKEFILE_WORKFLOW_PROD_DBS` for comma-separated live database names to block.
   - `MAKEFILE_WORKFLOW_TEST_DBS` for comma-separated safe test database names to allow.
   - `MAKEFILE_WORKFLOW_PROD_HOSTS` for comma-separated live database hosts to block.
   - `MAKEFILE_WORKFLOW_SCAR_DBS` for comma-separated SQLite synthetic scar database paths inserted before the bundled database.
   - `MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS` for continuous integration quiet window in seconds (default 180). Set this variable to `0` to disable the post-CI quiescence wait.
   - `MAKEFILE_WORKFLOW_CI_POLL_INTERVAL` for continuous integration polling interval in seconds (default 30). Use an integer of `1` or higher.
   - `MAKEFILE_WORKFLOW_CI_ENABLE_PEER_REVIEWS` set to `true` or `1` to enforce PR peer review approvals, CODEOWNERS, and unresolved review threads at Target 70 (default `false`).
   - `MAKEFILE_WORKFLOW_CI_MIN_APPROVALS` for local minimum `+1` approvals floor when peer reviews are enabled (default `0`).
   - `MAKEFILE_WORKFLOW_CI_REQUIRE_CODEOWNER_REVIEWS` set to `true` or `1` to require pending CODEOWNERS team reviews when peer reviews are enabled (default `false`).
   - `MAKEFILE_WORKFLOW_CI_ENABLE_SESSION_PARKING` set to `true` or `1` to park Target 70 sessions cleanly while awaiting peer reviews or slow CI (default `false`).
   - `MAKEFILE_WORKFLOW_CI_PARKING_CRON` for optional background polling cron expression while parked (default `""`).
   - `MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION` for completed state card action (`unlink` or `archive`, default `unlink`).
   - `MAKEFILE_WORKFLOW_PLANNING_BDD_MODE` for Target 15/20 BDD planning mode. Concrete modes (`adaptive`, `full_gherkin`, `planning_only`) run directly without prompting, while `ask` or unset `""` prompts in Target 15.
   - `MAKEFLOW_AGENT` for selecting the active agent path profile (`gemini`, `claude`, `cursor`, `copilot`, `codex`, `chatgpt`).
   - `MAKEFLOW_DISABLE_TERMINAL_TITLE` set to `1` or `true` to disable automatic terminal tab title updates.

Scalar and nested dictionary keys override earlier layers recursively. Setting `scars.databases` overrides the ordered SQLite scar database list verbatim. `MAKEFILE_WORKFLOW_SCAR_DBS` inserts environment paths before the bundled database, or appends them if the bundled database is omitted. Database protection lists combine all layers safely. `workflow_get_config` returns `configSource` (highest-precedence loaded file) and `configSources` (full loaded cascade).

Canonical defaults in `./default_config.json`:
   - `adversarial-critic`: `inherit`
   - `archaeology-worker`: `inherit`
   - `tdd-worker`: `inherit`
   - `triage-worker`: `inherit`
   - `synthetic-scar-importer`: `inherit`
   - `review.allowCriticHermeticTests`: `false`. Set to `true` only to allow read-only critic hermetic tests when no automated `T30 -> T40` gate exists.
   - `planning.bddMode`: `""` (unset default). Concrete auto-run modes (`adaptive`, `full_gherkin`, `planning_only`) execute directly without prompting. Prompt-triggering values (`""` and `ask`) trigger the split two-question Target 15 prompt. Invalid values fall back safely to `""`.
   - `testing.regressionCommand`: `""`. Set an explicit full subproject regression command for `verify-regression` when no `Makefile` `test:` target exists.
   - `testing.regressionTimeoutSeconds`: `300`. Positive timeout in seconds for `verify-regression` execution.
   - `ci.quiescenceSeconds`: `180`. Set to `0` to disable the post-CI quiescence wait.
   - `ci.pollIntervalSeconds`: `30`. Use `1` or higher.
   - `ci.enablePeerReviews`: `false` (sole-developer default). Set to `true` to enforce GitHub branch protection approvals, stale review dismissal, CODEOWNERS, `CHANGES_REQUESTED`, and unresolved review threads.
   - `ci.minApprovals`: `0`. Optional local floor (`>= 0`) for required `+1` approvals when `ci.enablePeerReviews` is `true`.
   - `ci.requireCodeOwnerReviews`: `false`. Optional local toggle to require pending CODEOWNERS reviews when `ci.enablePeerReviews` is `true`.
   - `ci.enableSessionParking`: `false`. Set to `true` to park Target 70 sessions cleanly while waiting on peer reviews or long-running CI.
   - `ci.parkingCronExpression`: `""`. Optional background polling cron schedule while parked (for example `"*/30 * * * *"`).
   - `state.completedCardAction`: `unlink` (removes intermediate cards `10_` through `80_` and `approved_tree.txt` at Target 90; set to `archive` to preserve them in `.workflow/archive/<ticket-id>/`).
   - `scars.databases`: `[".workflow/scars/scars_registry.db", "{agent_data}/scars_registry.db", "{plugin_root}/skills/makeflow/data/scars_registry.db"]`. Ordered SQLite scar database cascade supporting `{workspace}`, `{agent_data}`, `{plugin_root}`, and `~` tokens. Higher-precedence databases override matching scar IDs in lower tiers.
   - Database guard: blocks standalone `prod` and `production` names automatically.

### Cross-Agent User-Global Paths

| Agent | User-Global Config | User-Wide SQLite Registry |
| :--- | :--- | :--- |
| `gemini` | `~/.gemini/antigravity-cli/plugin_data/makeflow/config.json` | `~/.gemini/antigravity-cli/plugin_data/makeflow/scars_registry.db` |
| `claude` | `~/.claude/plugin_data/makeflow/config.json` | `~/.claude/plugin_data/makeflow/scars_registry.db` |
| `cursor` | `~/.cursor/plugin_data/makeflow/config.json` | `~/.cursor/plugin_data/makeflow/scars_registry.db` |
| `copilot` | `~/.copilot/plugin_data/makeflow/config.json` | `~/.copilot/plugin_data/makeflow/scars_registry.db` |
| `codex` | `~/.codex/plugin_data/makeflow/config.json` | `~/.codex/plugin_data/makeflow/scars_registry.db` |
| `chatgpt` | `~/.chatgpt/plugin_data/makeflow/config.json` | `~/.chatgpt/plugin_data/makeflow/scars_registry.db` |

## 3. Configuration File Schema

Copy [`.workflow/config.json.example`](../../../.workflow/config.json.example) to `<workspace>/.workflow/config.json` or your agent user-global config path:

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
    "strictSixPillars": true,
    "allowCriticHermeticTests": false
  },
  "planning": {
    "bddMode": ""
  },
  "testing": {
    "regressionCommand": "",
    "regressionTimeoutSeconds": 300
  },
  "ci": {
    "pollIntervalSeconds": 30,
    "quiescenceSeconds": 180,
    "enablePeerReviews": false,
    "minApprovals": 0,
    "requireCodeOwnerReviews": false,
    "enableSessionParking": false,
    "parkingCronExpression": ""
  },
  "database": {
    "prodDatabases": [],
    "testDatabases": [],
    "prodHosts": []
  },
  "state": {
    "completedCardAction": "unlink"
  },
  "scars": {
    "databases": [
      ".workflow/scars/scars_registry.db",
      "{agent_data}/scars_registry.db",
      "{plugin_root}/skills/makeflow/data/scars_registry.db"
    ]
  }
}
```

### Upgrading from Sole Developer to a Multi-Contributor Team

Sole developers keep default values (`enablePeerReviews: false`, `enableSessionParking: false`) for zero-wait landing after CI and bot triage.
When your team adds GitHub branch protection or `.github/CODEOWNERS`, opt in inside `<workspace>/.workflow/config.json`:

```json
{
  "ci": {
    "enablePeerReviews": true,
    "minApprovals": 1,
    "requireCodeOwnerReviews": true,
    "enableSessionParking": true,
    "parkingCronExpression": "*/30 * * * *"
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
