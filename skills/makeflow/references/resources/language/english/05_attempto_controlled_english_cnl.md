# Attempto Controlled English (ACE) Disambiguation Rules

- **Source Standard**: *Attempto Controlled English (ACE)* Construction and Interpretation Rules (Attempto Project, Department of Informatics, University of Zurich).
- **Attribution**: Adapted from the open Attempto Controlled English specification developed by Norbert E. Fuchs, Kaarel Kaljurand, and Tobias Kuhn at the University of Zurich.

Attempto Controlled English is a formal Controlled Natural Language (a subset of English with deterministic first-order logic semantics).
This module distills ACE disambiguation rules so humans and AI reasoning models parse specifications without syntactic or referential ambiguity.

## 1. Resolve Anaphora and Pronoun Ambiguity

In natural English, pronouns (`it`, `they`, `them`, `this`, `that`, `these`, `those`) create anaphora ambiguity (uncertainty about which earlier noun a pronoun refers to).

1. Never use a bare pronoun (`it`, `they`) when two or more nouns precede it in the current or previous sentence.
2. Repeat the exact noun (`the lock directory`, `the subagent`, `the receipt file`) instead of using a pronoun.
3. Never start a sentence with a bare demonstrative pronoun (`This prevents drift.`). Always attach the noun (`This check prevents drift.`).

- **Avoid**: `When the hook reads the config file, it validates its schema before it exits.`
- **Use**: `When the hook reads the configuration file, the hook validates the configuration schema before the hook exits.`

## 2. State Explicit Quantifier Scope

Universal quantifiers (`all`, `every`, `each`) and negation (`no`, `not`) produce multiple logical readings when combined in a single clause (`All workers do not hold a lock.`).

1. To express universal negation, start the noun phrase with `No` (`No worker holds a production database lock.`) instead of `All ... do not`.
2. Use `each` when a rule applies individually to every member of a set (`Verify each staged file before commit.`).
3. Place `only` immediately before the exact phrase it restricts (`Write temporary files only inside the scratch directory.`).

## 3. Fix Prepositional Phrase Attachment

A prepositional phrase (`with ...`, `in ...`, `from ...`) at the end of a sentence can attach either to the verb or to the nearest noun.

1. Place a prepositional phrase immediately after the noun it modifies, or convert the phrase into a relative clause (`that ...`).
2. If the prepositional phrase describes how to perform the action, place it near the verb or use `using`.

- **Avoid**: `The critic audits the diff from the feature branch with the Six Pillars manual.`
- **Use**: `Using the Six Pillars manual, the critic audits the diff from the feature branch.`

## 4. Write Deterministic `if ... then` Rules

ACE maps conditional specifications directly to logical implications ($P \implies Q$).

1. Write conditional rules in the explicit form `If <condition>, then <consequence>` or `If <condition>, <imperative command>`.
2. When a condition combines `and` and `or`, group the clauses explicitly with a vertical list so operator precedence is never ambiguous.
3. Avoid `unless` in complex conditions. Rewrite `Do not run A unless B and C` as `If B or C is false, do not run A`.

Example of an unambiguous compound conditional rule:

If any of the following conditions holds, halt the pre-push gate immediately:
1. The staged tree hash does not match `.workflow/state/approved_tree.txt`.
2. Pre-flight static analysis (`make test-static`) returns a non-zero exit code.
