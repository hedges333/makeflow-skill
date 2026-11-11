# NASA SP-7084 Technical Grammar and Punctuation

- **Source Standard**: `NASA SP-7084`, *Grammar, Punctuation, and Capitalization: A Handbook for Technical Writers and Editors*, by Mary K. McCaskill (National Aeronautics and Space Administration).
- **License Status**: Public Domain (`17 U.S.C. § 105` as a work of the United States Government).

This module distills technical grammar, noun modification, verbals, and punctuation rules from `NASA SP-7084`.

## 1. Break Up Stacked Noun Clusters

Technical writers often stack multiple nouns before a head noun (`the connection pool timeout configuration value`).
Long noun clusters obscure which word modifies which noun.

1. Limit consecutive noun clusters to a maximum of 3 words.
2. If a phrase contains 4 or more consecutive nouns, insert prepositions (`of`, `for`, `in`, `on`) to show the exact relationship.

| Stacked Noun Cluster (Avoid) | Restructured Phrase (Use) |
| :--- | :--- |
| `connection pool timeout configuration value` | `the timeout value for the connection pool` |
| `subagent tool permission validation check` | `the validation check for subagent tool permissions` |
| `environment variable fallback path resolution` | `fallback path resolution for environment variables` |

## 2. Hyphenate Unit Modifiers Before Nouns

A unit modifier (two or more words acting together as a single adjective before a noun) requires a hyphen to prevent misreading.

1. Hyphenate compound unit modifiers when they appear before a noun (`read-only mode`, `fail-fast loop`, `in-memory database`, `high-priority task`).
2. Do not hyphenate the compound when it follows the verb as a predicate (`The database is in memory.`).
3. Do not hyphenate adverbs ending in `-ly` (`newly created branch`, `strictly isolated test`).

## 3. Avoid Ambiguous Participles and Gerunds

Words ending in `-ing` can function as participles (verbal adjectives) or gerunds (verbal nouns).
Dangling or unattached `-ing` phrases create causal and temporal ambiguity.

1. Do not start procedural or descriptive sentences with a dangling `-ing` participle phrase.
2. Replace `-ing` clauses with explicit conditional (`if`, `when`) or causal (`because`) clauses that name the subject.
3. Use `-ing` words only as established technical nouns (`logging`, `caching`, `routing`) or hyphenated unit modifiers.

- **Avoid**: `Running the migration script, the lock table timed out.`
- **Use**: `When the worker ran the migration script, the lock table timed out.`

## 4. Retain Explicit Conjunctions (`that`)

Omitting the conjunction `that` after reporting or verification verbs forces the reader to backtrack when parsing the direct object vs. a new clause.

1. Keep the word `that` after verbs such as `verify`, `confirm`, `assert`, `check`, `state`, and `show`.
2. Keep articles (`the`, `a`, `an`) before countable nouns so nouns are never misread as imperative verbs.

- **Avoid**: `Verify the archive hash matches the receipt seal.`
- **Use**: `Verify that the archive hash matches the receipt seal.`

## 5. Clean Punctuation Without Semicolons or Em-Dashes

`NASA SP-7084` emphasizes that punctuation must clarify syntactic boundaries rather than splice loosely related thoughts.

1. Never use a semicolon in prose. Split the compound thought into two separate sentences with a period.
2. Never use an em-dash in prose. State the exact logical connector (`because`, `but`, `for example`, `namely`) or use parentheses for brief definitions.
3. Place a serial comma (Oxford comma) before the conjunction in a series of three or more items (`red, green, and refactor`).
4. Place a comma after every introductory conditional or temporal clause (`If the socket closes, retry the connection.`).
