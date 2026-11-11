# Google Developer Documentation Style Guide

- **Source Standard**: *Google Developer Documentation Style Guide* (`developers.google.com/style`).
- **License and Attribution**: Licensed under the Creative Commons Attribution 4.0 International License (`CC-BY-4.0`), Copyright Google LLC. Adapted and distilled for software engineering agent workflows.

This module distills software documentation conventions, clause ordering, code formatting, and abbreviation rules from the *Google Developer Documentation Style Guide*.

## 1. Place the Condition Before the Instruction

When a procedural sentence states the instruction first and the condition second, a human or AI agent can execute the command before reading the guard condition.

1. State the condition, circumstance, or target environment before the instruction.
2. Separate the condition clause from the imperative command with a comma.

- **Avoid**: `Delete the temporary state directory if the ticket status is complete.`
- **Use**: `If the ticket status is complete, delete the temporary state directory.`

## 2. Use Second Person and Simple Present Tense

1. Address the reader or calling agent in the second person (`you`).
2. Avoid first-person plural pronouns (`we`, `let us`) because `we` leaves it unclear whether the user or the software performs the action.
3. Describe deterministic software behavior in the simple present tense (`The gate returns exit code 2.`).
4. Use `will` only when distinguishing a future event from the current state.

## 3. Format Code Spans, CLI Flags, and File Paths

Clear typographic boundaries separate natural-language prose from literal machine tokens.

1. Wrap inline code symbols, function names, class names, and method calls in backticks (`` `validate()` ``).
2. Wrap CLI commands, CLI flag names (`` `--repo-root` ``), and environment variables (`` `TMPDIR` ``) in backticks.
3. Wrap file paths (`` `skills/makeflow/SKILL.md` ``), HTTP verbs (`GET`), and status codes (`` `404` ``) in backticks.
4. Never inflect, pluralize, or add possessive apostrophes inside a code span. Write ``the `Config` objects`` instead of ``the `Configs` ``.

## 4. Replace Latin Abbreviations with Plain English

Latin abbreviations slow down non-native readers and create tokenization ambiguity in sentence boundary detectors.

1. Do not use `e.g.`, `i.e.`, `etc.`, `et al.`, or `via` in prose.
2. Replace each Latin term with its exact English phrase:

| Latin Abbreviation (Avoid) | Plain English Replacement (Use) |
| :--- | :--- |
| `e.g.` | `for example` or `such as` |
| `i.e.` | `that is` or `namely` |
| `etc.` | Delete `etc.` and list the specific items, or write `and similar items` |
| `via` | `through`, `by`, or `using` |
| `cf.` | `compare` or `see` |
| `viz.` | `namely` |

## 5. Use Precise, Inclusive, and Concrete Terminology

1. Replace vague anthropomorphic or culturally bound metaphors with concrete engineering terms.
2. Write `primary` and `replica` (or `main` and `worker`) when describing distributed nodes.
3. Write `allowlist` and `denylist` when describing access-control filters.
4. Delete conversational filler, marketing adjectives (`seamless`, `robust`, `cutting-edge`), and AI slop phrases (`it is worth noting`, `delve into`).
