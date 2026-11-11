# MIL-STD-38784B Technical Manuals Standard Practice

- **Source Standard**: `MIL-STD-38784B`, *Department of Defense Standard Practice for General Style and Format of Technical Manuals*.
- **License Status**: Public Domain (`17 U.S.C. § 105` as a work of the United States Government).

This module distills procedural writing rules, imperative step structure, and safety alert hierarchy from `MIL-STD-38784B`.

## 1. Write Imperative Procedural Steps

Procedural text instructs the operator or agent on how to execute a task step by step.

1. Keep every procedural sentence at 20 words or fewer.
2. Start each procedural command with a direct imperative verb (`Run`, `Open`, `Verify`, `Delete`).
3. Include only one instruction per sentence unless two actions must occur at the same instant.
4. Number sequential procedural steps with integers (`1.`, `2.`, `3.`).
5. If a step depends on a precondition, write the condition before the imperative command with a comma.

- **Avoid**: `Run the database migration script if the schema version table is outdated.`
- **Use**: `If the schema version table is outdated, run the database migration script.`

## 2. Safety Alert Hierarchy: `WARNING`, `CAUTION`, and `NOTE`

`MIL-STD-38784B` establishes a strict three-tier hierarchy for alerts so operators never confuse informational notes with destructive hazards.

| Alert Level | Hazard Scope | Mandatory Placement and Rule |
| :--- | :--- | :--- |
| `WARNING` | Personal injury, loss of life, or physical hardware hazard | Place immediately before the step that creates the physical hazard. |
| `CAUTION` | Equipment damage, software crash, security breach, or data loss | Place immediately before the step that risks system or data damage. |
| `NOTE` | Supplemental factual context or descriptive explanation | Contains zero commands. The procedure must work if all notes are removed. |

### Formatting Rules for Alerts

1. Place `WARNING` and `CAUTION` blocks before the procedural step to which they apply.
2. State the prohibited action or hazard condition first inside a `WARNING` or `CAUTION`.
3. State the concrete consequence second (`The command deletes all unindexed rows without confirmation.`).
4. Never hide an imperative command, operational limit, or safety requirement inside a `NOTE`.

Example of a compliant `CAUTION` block:

> [!CAUTION]
> Do not pass the `--force` flag on production database hosts.
> The command drops active tables without confirmation.

Example of a compliant `NOTE` block:

> [!NOTE]
> The test runner creates an isolated SQLite file inside the system temporary directory.

## 3. Maintain Consistent Component Terminology

Readers and AI agents rely on exact lexical matching to track files, services, and state variables across a procedure.

1. Assign one exact name to each component, variable, or concept throughout the document.
2. Never alternate between synonyms (`config`, `settings`, `options`, `preferences`) for the same artifact.
3. Use `configuration` consistently across all sections.
4. Do not convert domain nouns into improvised verbs (`Do not webhook the payload` becomes `Send the payload to the webhook`).
