# NASA SP-7084 Technical Grammar, Sentence Style, Punctuation, and Capitalization

- **Source Standard**: `NASA SP-7084`, *Grammar, Punctuation, and Capitalization: A Handbook for Technical Writers and Editors*, by Mary K. McCaskill (National Aeronautics and Space Administration, Langley Research Center, 1990, NTRS `19900017394`).
- **License Status**: Public Domain (`17 U.S.C. § 105` as a work of the United States Government).

This module encapsulates all four chapters of `NASA SP-7084` for technical documentation and software engineering specifications.
Where agentic attention rules in [`00_index_and_agentic_rationale.md`](./00_index_and_agentic_rationale.md) restrict punctuation, this guide states the explicit Agentic Override.

---

## Chapter 1: Technical Grammar (Parts of Speech and Syntactic Structure)

Grammar (the study of word classes, inflections, and sentence functions) gives technical prose a deterministic structure.
In `NASA SP-7084` Chapter 1, every grammatical rule serves functional clarity so readers parse one meaning on the first pass.

### 1.1 Nouns: Stacked Noun Clusters, Possessive Case, and Abstract Noun Avoidance

A noun (a word that names a person, place, concept, or component) changes form to show number and possessive case.
Technical writers often stack multiple nouns before a head noun or overuse abstract nouns ending in `-tion`, `-ment`, or `-ance`.

1. Limit consecutive noun clusters to a maximum of 3 words.
2. If a phrase contains 4 or more consecutive nouns, insert prepositions (`of`, `for`, `in`, `on`, `from`) to show the exact relationship.
3. Form the possessive of a singular or plural noun not ending in `s` by adding `'s` (`the operator's token`, `men's roles`).
4. Form the possessive of a singular or plural noun ending in `s` by adding an apostrophe or `'s` consistently (`processes' locks`, `Jones' report`).
5. For inanimate technical objects, prefer noun adjuncts or prepositional phrases (`the strength of the laminate` or `laminate strength`) over `'s` possessives (`the table's column`).
6. Replace vague abstract noun constructions (`the existence of a race condition`) with concrete subjects and active verbs.

| Stacked Noun Cluster or Inanimate Possessive (Avoid) | Restructured Technical Phrase (Use) |
| :--- | :--- |
| `connection pool timeout configuration value` | `the timeout value for the connection pool` |
| `subagent tool permission validation check` | `the validation check for subagent tool permissions` |
| `environment variable fallback path resolution` | `fallback path resolution for environment variables` |
| `shock wave boundary layer interaction` | `interaction between the shock wave and boundary layer` |
| `separated flow wing heating rate values` | `separated-flow heating rates on the wing` |
| `the database table's primary index` | `the primary index of the database table` |

### 1.2 Pronouns: Antecedent Agreement, Case, Personal Pronouns, and Relative Pronouns (`that` vs. `, which`)

A pronoun (a word that replaces a noun) must match its antecedent (the specific noun it replaces) in number, gender, and person.
Ambiguous pronouns and broad references (`this`, `which`, or `it` referring to an entire preceding clause) force readers and language models to guess the referent.

1. Give every pronoun one unmistakable noun antecedent in the same sentence or the immediately preceding clause.
2. Never use bare `this`, `that`, `which`, or `it` to refer broadly to the general idea of a previous sentence.
3. Attach a concrete summary noun immediately after `this` or `these` (`this emphasis`, `this timeout`, `these measurements`).
4. Never use an apostrophe in possessive case personal or relative pronouns (`its`, `theirs`, `ours`, `yours`, `whose`).
5. Address the operator directly as `you` in procedures, and use `we` only when referring to a defined author group.
6. Use plural nouns (`Editors must follow their guidelines`) or article rewrites (`the human ear`) to avoid gender-specific singular pronouns.
7. Use `that` without a preceding comma to introduce a restrictive clause (a clause that defines or filters the noun).
8. Use `, which` enclosed by commas to introduce a nonrestrictive clause (a clause that adds supplemental facts without filtering the noun).
9. Use `which` after a preposition (`the bucket in which keys reside`) or after demonstrative `that` (`that which fails`).
10. Use `who` for nominative subjects, `whom` for objective targets of verbs or prepositions, and `whose` for possession (including inanimate nouns when `of which` is awkward).

- **Avoid (Broad Reference)**: `The boundary condition becomes a source term, which permits use of the Green function.`
- **Use (Explicit Cause)**: `Because the boundary condition becomes a source term, the solver can use the Green function.`
- **Avoid (Ambiguous `this`)**: `Mass flow rate increased in the cove. This resulted in higher gas temperature.`
- **Use (Explicit Subject)**: `The increasing mass flow rate in the cove raised the gas temperature.`
- **Avoid (Restrictive `which`)**: `Stop any worker process which exceeds the memory limit.`
- **Use (Restrictive `that` vs. Nonrestrictive `, which`)**: `Stop any worker process that exceeds the memory limit. The primary daemon, which runs on port 8080, monitors memory limits.`

### 1.3 Verbs: Voice, Tense, Mood, and Subject-Verb Agreement

A verb (a word that expresses an action or state of being) changes form to show voice, tense, mood, person, and number.

#### 1.3.1 Active vs. Passive Voice, Tense Consistency, and Mood

1. Prefer active voice (where the subject performs the action) over passive voice (where the subject receives the action).
2. Reserve passive voice for cases where the actor is unknown, unimportant, or less important than the receiver of the action.
3. Maintain consistent verb tense within each element of discourse.
4. Use simple present tense for permanent system facts, universal equations, exposition, and reference descriptions (`Figure 2 shows`).
5. Use simple past tense for narration of completed historical tests, incidents, or specific experimental runs (`The specimen failed`).
6. Keep subordinate verbs aligned with the sequence of tenses relative to the principal verb.
7. Use indicative mood for statements of fact and imperative mood for procedural commands (`Run the linter.`).
8. Reserve subjunctive mood for requirements (`The operator asked that the test be repeated`) or counterfactual conditions (`If the matrix were singular`).

#### 1.3.2 Subject-Verb Agreement

A verb must agree in number with its subject, regardless of phrases that intervene between them.

1. Ignore intervening phrases (`as well as`, `along with`, `plus`, `in addition to`) when matching verb number.
2. Use a plural verb when two or more subjects are joined by `and` (`Damping ratio and frequency agree`).
3. Use a singular verb when singular subjects are joined by `or` or `nor` (`Neither the socket nor the pipe is open`).
4. If `or` or `nor` joins singular and plural subjects, match the verb to the nearer subject (`Either the supervisor or the workers are responsible`).
5. Never join subjects with `and/or`. Write `A or B, or both` so verb number remains deterministic.
6. When a number and plural unit name a single measurement, quantity, or duration, use a singular verb (`15 seconds is the timeout`, `20 liters of fuel has passed`).
7. Treat `each`, `every`, `either`, and `neither` as singular when they act as pronouns or modify singular subjects.
8. Distinguish `the number of` (which takes a singular verb) from `a number of` (which takes a plural verb), and apply the same rule to `the total` vs. `a total of`.
9. Match fractional or quantifier subjects (`all`, `most`, `some`, `half`, `part`, `percent`, `none`) to the number of the noun in the following `of` phrase (`Most of the measurements contain this error`, `Most of the disagreement is minor`).
10. Treat `data`, `criteria`, and `phenomena` as plural nouns in formal technical reports (`The data indicate`), or rewrite with concrete singular nouns (`the dataset`, `the payload`).

| Subject-Verb Agreement Category | Example Subject Construction | Required Verb Form and Example |
| :--- | :--- | :--- |
| Compound subject with `and` | `The parser and the linter` | Plural (`The parser and the linter validate the tree.`) |
| Singular subjects with `or` / `nor` | `Neither the client nor the daemon` | Singular (`Neither the client nor the daemon holds the lock.`) |
| Mixed-number subjects with `or` / `nor` | `Either the main thread or the worker threads` | Matches nearer subject (`Either the main thread or the worker threads close the socket.`) |
| Intervening phrase (`as well as`, `plus`) | `Damping ratio, as well as frequencies,` | Matches head subject (`Damping ratio, as well as frequencies, agrees with the model.`) |
| Single measurement or duration unit | `15 seconds`, `64 megabytes` | Singular (`15 seconds is the maximum quiescence timeout.`) |
| Distributive determiners (`each`, `every`) | `Each subagent`, `Every test case` | Singular (`Each subagent runs in an isolated context.`) |
| `The number of` vs. `A number of` | `The number of retries` / `A number of retries` | Singular for `The number` (`The number of retries is three.`) / Plural for `A number` (`A number of retries were logged.`) |
| `The total` vs. `A total of` | `The total of the weights` / `A total of 12 nodes` | Singular for `The total` (`The total of the weights equals one.`) / Plural for `A total of` with plural noun (`A total of 12 nodes are active.`) |
| `None` | `None of the configuration` / `None of the tests` | Singular for mass noun or "not one" (`None of the configuration is mutable.`) / Plural for countable plural (`None of the tests fail.`) |
| `Data`, `criteria`, `phenomena` | `The experimental data`, `These criteria` | Plural in formal reports (`The data indicate that lift increases.`) |
| Uncountable technical nouns | `equipment`, `hardware`, `apparatus`, `aeronautics` | Singular (`The test hardware is calibrated.`) |

### 1.4 Adjectives and Adverbs: Unit Modifiers, `-ly` Adverbs, Degree, and Placement of `only`

An adjective modifies a noun or pronoun, whereas an adverb modifies a verb, an adjective, or another adverb.

1. Use articles `a` before consonant sounds (`a NASA airfoil`, `a user`) and `an` before vowel sounds (`an NACA airfoil`, `an hour`, `an AST check`).
2. When coordinate adjectives modify a singular noun and refer to separate items, repeat the article (`the transverse and the shear strain`, or write `the transverse and shear strains`).
3. Hyphenate a unit modifier (two or more words acting together as one adjective) when it precedes a noun (`high-pressure combustor`, `flight-tested model`, `read-only file`).
4. Do not hyphenate a temporary compound when it follows the verb as a predicate adjective (`The configuration was flight tested`, `The cache is in memory`).
5. Do not hyphenate a unit modifier when the first word is an adverb ending in `-ly` (`relatively accurate prediction`, `strictly isolated test`).
6. Do not hyphenate a unit modifier when the first word is a comparative or superlative (`higher order calculation`, `lowest latency path`).
7. Do not hyphenate proper names (`North Carolina coast`), chemical compounds (`nitric oxide formation`), or letter/number designations (`material 3 properties`, `Type A connector`) used as modifiers.
8. Never use an adjective to modify a verb (`mounted internal to the model`). Use an adverb (`internally mounted`) or a prepositional phrase (`mounted inside the model`).
9. Place limiting adverbs (`only`, `almost`, `nearly`, `merely`, `also`) immediately before the exact word or phrase they limit.
10. Eliminate squinting adverbs (adverbs placed between two verbs or clauses where either could be modified).
11. Split an infinitive (`to immediately recalibrate`) only when placing the adverb outside the infinitive creates squinting ambiguity or awkward phrasing.

- **Avoid (Misplaced `only`)**: `The approximation is only valid for u = 0.`
- **Use (Exact `only` Placement)**: `The approximation is valid only for u = 0.`
- **Avoid (Misplaced `only`)**: `It is only necessary to apply equations (6) to (12).`
- **Use (Exact `only` Placement)**: `It is necessary to apply only equations (6) to (12).`

### 1.5 Prepositions and Conjunctions: Idioms, Retaining `that`, and Correlative Parallelism

A preposition connects a noun phrase to a sentence, and a conjunction joins words, phrases, or clauses.

1. Select prepositions according to established technical idiom (`different from`, `analogous to`, `coincident with`, `independent of`, `similar to`).
2. Repeat prepositions across coordinate phrases whenever omitting the preposition creates ambiguity (`in nitrogen and in oxygen`) or breaks correlative parallelism.
3. Retain the subordinating conjunction `that` after reporting and verification verbs (`verify`, `confirm`, `assert`, `check`, `state`, `show`, `believe`, `indicate`, `conclude`, `ensure`).
4. Never repeat `that` after an intervening adverbial clause (`He concluded that because the diff was clean, the build passed`).
5. Use `if` for conditional clauses (`If the socket closes, retry`) and `whether` for alternative clauses (`Determine whether the socket is open`).
6. Use `where` only for physical or topological location. Replace loose `where` with `in which`, `by which`, or `when` for non-spatial concepts.
7. Follow both members of every correlative conjunction pair (`both ... and`, `either ... or`, `neither ... nor`, `not only ... but also`) with the exact same part of speech.

| Term | Required Prepositional Idiom | Common Error to Avoid |
| :--- | :--- | :--- |
| `analogous` | `analogous to` | `analogous with` |
| `coincident` | `coincident with` | `coincident to` |
| `compare` (likeness) | `compare to` (likening two different classes) | Mixing `to` and `with` |
| `compare` (examination) | `compare with` (side-by-side technical analysis) | `compared to the baseline` |
| `consist` | `consist of` (parts) / `consist in` (definition) | `consist with` |
| `correlate` / `correlation` | `correlate with` / `correlation with` (things) or `between` | `correlation to` |
| `different` | `different from` | `different than` (before a noun) |
| `independent` | `independent of` | `independent from` |
| `implicit` | `implicit in` | `implicit to` |
| `variance` | `at variance with` | `at variance to` |

- **Avoid (Omitted `that`)**: `Verify the archive hash matches the receipt seal.`
- **Use (Explicit `that`)**: `Verify that the archive hash matches the receipt seal.`
- **Avoid (Unbalanced Correlatives)**: `The microprocessor provides both radiometer control functions and formats the data.`
- **Use (Parallel Correlatives)**: `The microprocessor both controls the radiometer and formats the data.`
- **Avoid (Broken Prepositional Parallelism)**: `Tests were conducted not only in nitrogen but also oxygen.`
- **Use (Repeated Preposition)**: `Tests were conducted not only in nitrogen but also in oxygen.`

### 1.6 Verbals: Participles, Gerunds, Infinitives, and Dangling Modifiers

Verbals include participles (adjectives ending in `-ing` or `-ed`), gerunds (nouns ending in `-ing`), and infinitives (`to` plus verb).

1. Treat a gerund subject or coordinate gerunds representing a single process as singular (`Substituting the expression into equation (2) yields`).
2. Keep `by` before a method gerund (`obtained by using least squares`) to prevent a dangling participle (`obtained using`).
3. Ensure every introductory participle, gerund, or infinitive phrase modifies the grammatical subject of the main clause.
4. Eliminate dangling participles by rewriting the introductory phrase as an explicit adverbial clause (`when`, `because`, `after`) with its own subject.
5. Replace trailing participial result clauses (`, yielding a first-order solution`) with infinitive purpose phrases (`to yield a first-order solution`) or coordinate verbs (`and yields`).

- **Avoid (Dangling Verbal)**: `When using a nonaligning pitot tube, total velocity cannot be measured by the investigator.`
- **Use (Matched Subject)**: `When using a nonaligning pitot tube, the investigator cannot measure total velocity.`
- **Avoid (Dangling Participle)**: `Running the migration script, the lock table timed out.`
- **Use (Explicit Clause)**: `When the worker ran the migration script, the lock table timed out.`

### 1.7 Sentence Structure, Parallelism, and Comparisons

Parallelism (writing logically equal ideas in the same grammatical structure) binds lists, coordinate clauses, and comparisons together.

1. Express coordinate elements joined by `and`, `but`, `or`, or `nor` in identical grammatical forms (all nouns, all prepositional phrases, or all active clauses).
2. Make every list item read logically from the lead-in clause and share the same grammatical form.
3. Use comparative degree (`-er`, `more`, `less`) for two items, and add `other` when comparing within one class (`higher at orifice 7 than at the other 47 orifices`).
4. Use superlative degree (`-est`, `most`, `least`) with `all` (never `any`) when comparing three or more items (`highest of all 48 orifices`).
5. Eliminate incomplete comparisons by stating both compared attributes or inserting demonstrative pronouns (`those on`, `that of`).
6. State the explicit standard of comparison (`stronger than ferritic steels` instead of bare `higher strength steels`).
7. Use `compare with` for side-by-side technical analysis, and use `compare to` only for figurative likeness.
8. Keep both `as` tokens in `as ... as` comparisons (`as hard as, if not harder than, before`).

- **Avoid (Non-Parallel Series)**: `Pressures at the bulkhead, the seal, and in the cove are shown.`
- **Use (Parallel Series)**: `Pressures at the bulkhead, at the seal, and in the cove are shown.`
- **Avoid (Incomplete Comparison)**: `The pressures on the left dry-power nozzle are lower than the right afterburning nozzle.`
- **Use (Complete Comparison)**: `The pressures on the left dry-power nozzle are lower than those on the right afterburning nozzle.`

---

## Chapter 2: Technical Sentence Style

`NASA SP-7084` Chapter 2 governs how subjects, verbs, modifiers, conciseness, and emphasis work together to convey technical ideas without friction.

### 2.1 Clarifying the Subject, Invigorating the Verb, and Subject-Verb Proximity

The subject and verb are the two most important positions in any sentence.
Wasting the subject position on an abstract noun or an expletive filler (`there is`, `there are`, `it is ... that`) buries the real action.

1. Place the concrete actor or technical component in the grammatical subject position.
2. Express the core action in the main verb rather than in a nominalization (`Lift coefficients are compared` or `We compare lift coefficients` instead of `A comparison of lift coefficients is presented`).
3. Eliminate expletive constructions (`there is`, `there are`, `it is apparent that`, `it is believed that`).
4. Keep the subject and main verb close together so intervening modifiers do not sever sentence continuity.
5. If a long clause separates the subject and verb, shorten the clause or move it after the predicate.
6. Avoid hedging verbs (`tends to`, `seems to`, `appears to`, `might`) when stating deterministic system behavior or measured facts.

- **Avoid (Expletive `there is` + Participle)**: `At NPR < 4, there is a large reduction in discharge coefficient, indicating a decrease in throat area.`
- **Use (Concrete Subject + Vigorous Verb)**: `At NPR < 4, a large reduction in discharge coefficient indicates a decrease in throat area.`
- **Avoid (Separated Subject and Verb)**: `Pressures that were sensed at discrete locations behind the seal, at the bulkhead, and at the base of the ramp are also given.`
- **Use (Adjacent Subject and Verb)**: `Table 2 also gives pressures sensed at discrete locations behind the seal, at the bulkhead, and at the base of the ramp.`

### 2.2 Brevity, Conciseness, and Eliminating Wordiness

Conciseness means expressing complete technical facts in the fewest necessary words without losing precision.
`NASA SP-7084` Section 2.4 classifies wordiness into tautologies (redundant modifiers), dilute verbs, superfluous prepositions, circumlocutions (roundabout constructions), hedges, pointless lead-ins, and false elegance.

1. Delete tautologies where a modifier repeats the meaning already contained in the noun (`AC current`, `20 seconds in duration`, `close proximity`).
2. Replace circumlocutions and roundabout phrases (`due to the fact that`, `in the event that`, `on a regular basis`) with single-word connectors (`because`, `if`, `regularly`).
3. Delete pointless introductory meta-phrases (`It is interesting to note that`, `It might be stated that`, `It should be remembered that`).
4. Replace absolute modifiers that have been improperly qualified (`very unique`, `quite impossible`, `more dominant`) with exact adjectives (`unique`, `impossible`, `dominant`).
5. Shorten document and section titles by dropping empty reporting prefixes (`An Investigation of`, `A Study of`, `An Analysis of`).

| Wordiness Category (`NASA SP-7084` § 2.4.1) | Wordy, Redundant, or Circumlocutory Phrase (Avoid) | Concise Technical Replacement (Use) |
| :--- | :--- | :--- |
| Tautology (redundant modifier) | `AC current` / `ATM machine` / `PIN number` | `AC` (or `alternating current`) / `ATM` / `PIN` |
| Tautology (redundant modifier) | `20 seconds in duration` | `20 seconds` |
| Tautology (redundant modifier) | `in close proximity to` | `near` |
| Tautology (redundant modifier) | `in the range of 1 to 10` | `from 1 to 10` |
| Tautology (redundant modifier) | `two different test runs` (when merely counting) | `two test runs` |
| Dilute verb | `are found to be in agreement` | `agree` |
| Dilute verb | `make adjustments to` / `perform an analysis of` | `adjust` / `analyze` |
| Dilute verb | `give consideration to` / `take measurements of` | `consider` / `measure` |
| Superfluous preposition ("hiccup") | `of from 10 to 20` / `inside of the container` | `from 10 to 20` / `inside the container` |
| Superfluous preposition ("hiccup") | `enter into the phase` / `in between the stages` | `enter the phase` / `between the stages` |
| Circumlocution / False elegance | `a majority of` / `a sufficient number of` | `most` / `enough` |
| Circumlocution / False elegance | `with the exception of` / `in the case when` | `except` / `when` |
| Pointless introductory phrase | `It is interesting to note that the cache missed.` | `The cache missed.` |
| Improperly qualified absolute | `more dominant` / `quite impossible` / `very unique` | `dominant` / `impossible` / `unique` |

### 2.3 Sentence Emphasis: End-Focus and Front-Condition Placement

In technical English, the positions of natural emphasis are the beginning and the end of a sentence (`NASA SP-7084` Section 2.6).
Relying on italics or exclamation marks for emphasis indicates weak sentence structure.

1. Place the condition, scope, or qualifying circumstance at the front of the sentence followed by a comma (`At zero leakage, cove heating rates decreased`).
2. Place the primary technical conclusion, command, or newly introduced term at the end of the sentence (end-focus).
3. Use grammatical parallelism or numbered vertical lists to emphasize coordinate findings.
4. Repeat key articles, prepositions, or conjunctions (`and that`) deliberately when each parallel branch requires distinct stress.

- **Avoid (Buried Condition)**: `Retry the connection with exponential backoff if the upstream socket closes.`
- **Use (Front-Condition + End-Focus)**: `If the upstream socket closes, retry the connection with exponential backoff.`

---

## Chapter 3: Technical Punctuation and Agentic Overrides

Punctuation marks perform four syntactic functions in `NASA SP-7084` Chapter 3: they separate, group or enclose, connect, and impart meaning.
Follow an open punctuation style: apply punctuation to prevent misreading and clarify grammatical boundaries rather than inserting every optional mark.

### 3.1 Mark-by-Mark Punctuation Rules

#### 3.1.1 Apostrophe (`'`)

1. Use an apostrophe to form the possessive case of nouns (Section 1.1) and never with possessive pronouns (`its`, `whose`, `theirs`).
2. Do not use contractions (`don't`, `it's`, `can't`) in technical documentation. Write `do not`, `it is`, and `cannot`.
3. Pluralize multi-letter acronyms and numbers with `s` (`APIs`, `1990s`, `4s`), and use `'s` only for single letters (`x's`, `a's`).

#### 3.1.2 Brackets (`[ ]`)

1. Use square brackets in prose to enclose editorial insertions, clarifications, or substitutions inside direct quotations (`"These instruments [the radiometer and scatterometer] operated aboard the satellite."`).
2. Preserve brackets byte-for-byte in Markdown link syntax, array indexing, and mathematical interval notation.

#### 3.1.3 Colon (`:`)

A colon has strong separating force and introduces lists, amplifications, equations, or formal quotations.

1. Place an introductory colon only after a complete independent clause (or a lead-in containing `as follows` or `the following`).
2. Never place a colon directly between a verb or preposition and its direct objects (`The components are: A, B, and C` or `separated into: A and B`).
3. Never place a colon after `such as`, `for example`, `namely`, or `that is`.
4. Use a colon between two independent clauses when the second clause directly amplifies, explains, or restates the first clause.
5. Place colons outside closing quotation marks and closing parentheses.

- **Avoid (Colon After Verb or `such as`)**: `The scatterometer is separated into: a gimbal and electronics, such as: power supplies.`
- **Use (Clean Verb Object or Complete Lead-In)**: `The scatterometer is separated into three assemblies: a gimbal, a transceiver, and power supplies.`

#### 3.1.4 Comma (`,`)

Commas either separate elements (standing alone) or enclose elements (requiring a partner comma or terminal mark).

1. **Serial (Oxford) Comma**: Always place a comma before the coordinating conjunction (`and`, `or`, `nor`) in a series of three or more elements (`red, green, and refactor`).
2. **Introductory Clauses and Phrases**: Place a comma after every introductory conditional, temporal, participial, gerund, or infinitive clause or phrase (`If the variable t represents time, then a represents frequency.`).
3. **Compound Sentences**: Place a comma before a coordinating conjunction (`and`, `but`, `or`, `nor`, `for`, `so`, `yet`) that joins two independent clauses.
4. **Comma Splice Prohibition**: Never join independent clauses with a bare comma (`The latency rose, however, throughput stayed flat`). Use a period instead (`The latency rose. However, throughput stayed flat.`).
5. **Compound Predicates**: Do not place a comma between two verbs that share a single subject (`The worker validates the receipt and updates the state file`).
6. **Nonrestrictive vs. Restrictive Modifiers**: Enclose nonrestrictive clauses, appositives, and interrupting phrases (`however`, `for example`) in a pair of commas. Never enclose restrictive modifiers (`that` clauses or defining appositives like `the noble gas argon`) in commas.
7. **Coordinate Adjectives**: Separate coordinate adjectives with a comma only if they independently modify the noun (test by inserting `and` or reversing their order: `a long, controversial history` vs. `a linear shift-invariant system`).
8. **Numbers**: Do not use commas inside 4-digit numbers (`2500`) or technical literals where commas conflict with argument separators.

#### 3.1.5 Hyphen (`-`), Prefix Hyphenation, and Suspended Hyphens

Hyphens connect compound unit modifiers before nouns (Section 1.4) and attach specific prefixes.
Modern technical usage prefers solid (unhyphenated) words for established prefixes unless a hyphen is required to prevent misreading or homograph collision.

1. Always hyphenate the prefixes `all-`, `ex-`, `half-`, `quasi-`, and `self-` (`self-attention`, `self-contained`, `quasi-steady`).
2. Always hyphenate any prefix attached to a capitalized proper noun (`un-American`, `non-POSIX`, `pre- Darwinian`) or a number (`pre-1990`).
3. Always hyphenate a prefix attached to an already hyphenated compound (`non-civil-service`, `pseudo-steady-state`).
4. Hyphenate a prefix when omitting the hyphen creates a confusing homograph (`re-cover` vs. `recover`, `re-create` vs. `recreate`, `un-ionized` vs. `unionized`, `co-op` vs. `coop`, `multi-ply` vs. `multiply`) or an awkward letter collision (`anti-inflation`, `sub-subcommittee`, `post-stall`).
5. Write standard prefixed words solid (`subproject`, `idempotent`, `precondition`, `untracked`, `nonlinear`, `reinitialize`, `multiprocess`, `postcondition`, `antipattern`) unless hyphenated in the table below.
6. **Suspended Hyphens**: Never write suspended hyphens (`32- and 64-bit`). Write both hyphenated modifiers in full (`32-bit and 64-bit`).
7. Do not use a hyphen or en-dash inside `from ... to ...` or `between ... and ...` spans (`Write from 1975 to 1978, not from 1975-1978`).

| Prefix or Construct | Solid Word Forms (Use Without Hyphen) | Hyphenated Forms (Use With Hyphen and Rationale) |
| :--- | :--- | :--- |
| `non-` | `nonlinear`, `nonrestrictive`, `nonempty`, `nonnegative`, `nonvolatile` | `non-blocking`, `non-ASCII` (proper/acronym), `non-civil-service` (hyphenated base) |
| `pre-` | `precondition`, `preexisting`, `preallocate`, `precompile`, `preempt` | `pre-flight`, `pre-push`, `pre-1990` (numeral) |
| `re-` | `reinitialize`, `recompute`, `redirect`, `revalidate`, `retrying` | `re-audit`, `re-execute` (vowel clarity), `re-cover` (cover again vs. `recover`), `re-create` |
| `self-` | `selfless`, `selfsame` (rare in technical prose) | `self-attention`, `self-contained`, `self-approval`, `self-referential` (always hyphenate `self-` prefix) |
| `sub-` | `subproject`, `subagent`, `subprocess`, `subquery`, `subordinate`, `subset` | `sub-subcommittee`, `sub-millisecond` (avoid awkward syllable collision) |
| `co-` | `cooperate`, `coordinate`, `coexist`, `collocate`, `coprocessor` | `co-author`, `co-worker`, `co-op` (homograph vs. `coop`) |
| `multi-` | `multiprocess`, `multithreaded`, `multilingual`, `multiplexer` | `multi-turn`, `multi-agent`, `multi-ply` (homograph vs. `multiply`) |
| `post-` | `postcondition`, `postmortem`, `postprocess`, `postpone` | `post-stall`, `post-deployment` (clarity before long compounds) |
| `anti-` | `antipattern`, `antivirus`, `antithesis`, `antifreeze` | `anti-inflation` (doubled `i`), `anti-shortcut`, `anti-speculative` |
| `un-` / `in-` / `id-` | `untracked`, `uncommitted`, `unmodified`, `idempotent`, `invariant` | `un-ionized` (homograph vs. `unionized`), `un-American` (proper noun) |
| Suspended hyphen | N/A | Avoid `32- and 64-bit`. Write `32-bit and 64-bit` in full. |

#### 3.1.6 Italics, Boldface, and Inline Code Spans

1. Wrap file paths, CLI flags, identifiers, literal tokens, and words used as words (`the word` `` `that` ``) in inline code backticks (`` `...` ``).
2. Use italics (`*...*`) for titles of standalone books and handbooks (*Grammar, Punctuation, and Capitalization*), mathematical scalar variables ($x$, $n$), and biological species names.
3. Keep chemical symbols (`He`, `NaCl`), SI units (`ms`, `kB`), and acronyms (`NASA`, `AST`) in roman (non-italic) type.
4. Do not use italics to emphasize entire sentences. Achieve emphasis through sentence structure (Section 2.3).

#### 3.1.7 Parentheses (`( )`)

1. Use parentheses to enclose brief definitions at first use (`an invariant (a condition that remains true across execution)`), citation references, or inline enumerations (`(1) separate, (2) enclose, and (3) connect`).
2. Never insert an unrelated tangential sentence in parentheses.
3. When a parenthetical phrase falls inside a sentence, place the sentence punctuation outside the closing parenthesis (`... across execution).`).
4. If a parenthetical sentence stands alone, capitalize its first word and place the period inside the closing parenthesis.

#### 3.1.8 Period (`.`)

1. End every declarative and imperative sentence with a period.
2. Omit trailing periods on Markdown section headings (`## Heading`) and table titles.
3. End vertical list items with a period when they are complete sentences or complete the lead-in clause.
4. Do not place periods after SI unit symbols (`m`, `s`, `kg`, `Hz`) or acronyms (`NASA`, `POSIX`).

#### 3.1.9 Question Mark (`?`)

1. Use a question mark only at the end of a direct question (`Which subproject test command runs in tight iteration?`).
2. Never place a question mark at the end of an indirect question (`The audit determines which test command runs in tight iteration.`).

#### 3.1.10 Quotation Marks (`" "`)

1. Use double quotation marks (`" "`) for direct verbatim quotations and single quotation marks (`' '`) only for quotes nested inside double quotes.
2. Do not enclose standard technical terms in "scare quotes" (`The worker "serializes" the state`).
3. Place periods and commas inside closing quotation marks, and place colons and non-quoted question marks outside.
4. When quoting literal programming strings or CLI tokens, use inline code backticks (`` `"value"` ``) so trailing punctuation never contaminates the literal string.

#### 3.1.11 Slash (`/`)

1. Use the slash (`/`) only in file paths (`skills/makeflow/SKILL.md`), mathematical fractions (`x/y`), or unit rates (`m/s`, `requests/s`).
2. Never use `and/or` or slash compounds (`client/server`, `read/write`, `molecular/atomic`) in prose.
3. Replace slash compounds with a hyphen (`client-server`), `and` (`read and write`), `or` (`molecular or atomic`), or `A or B, or both`.

---

### 3.2 Agentic Override: Semicolon (`;`) and Em-Dash (`—`) Prohibition

`NASA SP-7084` Sections 3.6 and 3.15 permit the em-dash (`` `—` ``) and the semicolon (`` `;` ``) in traditional human-edited reports.
However, [`00_index_and_agentic_rationale.md`](./00_index_and_agentic_rationale.md) **Rule 9** establishes an explicit **Agentic Override**: **Never use semicolons or em-dashes in prose.**

#### Why Rule 9 Bans Semicolons and Em-Dashes in Agentic Prose

1. **Semicolon Attention Dilution**: A semicolon (`` `;` ``) joins clauses without stating causal (`because`), contrastive (`but`), or conditional (`if`) logic. Use a period or explicit conjunction instead.
2. **Em-Dash Structural Ambiguity**: Writers overload the em-dash (`` `—` ``) for appositives, interruptions, explanations, and lists. Use parentheses, commas, or a colon instead.

Use the following deterministic conversion table to rewrite every `NASA SP-7084` semicolon or em-dash construction:

| `NASA SP-7084` Section and Construct | Traditional `NASA SP-7084` Pattern (Forbidden in Prose by Rule 9) | Agentic Override Replacement (Use in All Prose) |
| :--- | :--- | :--- |
| § 3.15.1 Coordinate clauses without conjunction | `The first two runs were practice; only the last four runs were analyzed.` | **Period (`.`):** `The first two runs were practice. Only the last four runs were analyzed.` |
| § 3.15.1 Coordinate clauses with conjunctive adverb (`however`, `therefore`, `thus`) | `The differences averaged 11 percent; however, larger errors occurred at Mach 2.` | **Period + Adverb (`However,`) or `, but`:** `The differences averaged 11 percent. However, larger errors occurred at Mach 2.` |
| § 3.15.1 Long coordinate clauses with internal commas | `The pilots preferred the steady horizon, runway image, and pitch grid; and they reported less distraction.` | **Period (`.`):** `The pilots preferred the steady horizon, runway image, and pitch grid. They also reported less distraction.` |
| § 3.15.2 Complex series with internal commas | `Members were H. Melfi, Goddard, Maryland; A. Carswell, York, Canada; and E. Browell, Langley, Virginia.` | **Vertical Bulleted List or Parentheses:** `Members were H. Melfi (Goddard, Maryland), A. Carswell (York, Canada), and E. Browell (Langley, Virginia).` |
| § 3.15.3 Explanatory clause (`that is`, `namely`, `for example`) | `Some processes are time-invariant; that is, measurements at different times share the same mean.` | **Period + `In other words,` or Colon (`:`):** `Some processes are time-invariant: measurements at different times share the same mean.` |
| § 3.15.4 Elliptical clauses with omitted verb | `Wind speed comes from brightness temperature; rain rate, from temperature difference.` | **Repeat Verb + Conjunction or Vertical List:** `Wind speed comes from brightness temperature, and rain rate comes from temperature difference.` |
| § 3.6.1 Enclosing em-dashes around appositive or list | `Of the noble gases studied—argon, xenon, and neon—argon offered the highest power.` | **Parentheses `( )`:** `Of the noble gases studied (argon, xenon, and neon), argon offered the highest power.` |
| § 3.6.1 Enclosing em-dashes around interrupting sentence | `The one-sided spectrum—engineers call it simply "spectrum"—is the analyzer output.` | **Parentheses `( )` or Relative Clause:** `The one-sided spectrum (which engineers call simply "spectrum") is the analyzer output.` |
| § 3.6.2 Separating em-dash before restatement or amplification | `The toughness of steel degrades at cryogenic temperatures—at 77 K, impact energy drops.` | **Colon (`:`) or Causal Clause (`because`):** `The toughness of steel degrades at cryogenic temperatures: at 77 K, impact energy drops.` |
| § 3.6.2 Separating em-dash in displayed list item (`Item—Description`) | `1. Air—The 600-psi system delivers 300 lb/s.` | **Bold Lead-In + Colon (`:`):** `1. **Air**: The 600-psi system delivers 300 lb/s.` |

---

## Chapter 4: Capitalization, Numerals, and Units of Measure

`NASA SP-7084` Chapter 4 applies a "down style" of capitalization: capitalize only proper nouns, sentence starts, and principal title words.

### 4.1 Proper Nouns vs. Common Engineering Nouns

A proper noun is the official name of a specific person, place, organization, or uniquely designated artifact.
Common engineering nouns (`the database`, `the supervisor`, `the subagent`, `the repository`, `the server`) remain lowercase in prose.

1. Keep common architecture and system nouns lowercase (`the database`, `the supervisor`, `the critic`, `the state file`, `the lock directory`).
2. Capitalize proper names of people, organizations, programming languages, and official protocols (`Mary K. McCaskill`, `NASA`, `Python`, `POSIX`, `Merkle tree`, `Belnap`).
3. Capitalize proper adjectives derived from proper names when used in their proper sense (`Newtonian fluid`, `Gaussian distribution`, `Boolean algebra`, `Cartesian coordinates`).
4. Keep unit eponyms and common derivatives lowercase (`20 pascals`, `10 newtons`, `watt`, `kelvin`, `venturi tube`, `roman type`), except `Celsius`, `Fahrenheit`, and `Rankine`.
5. Capitalize formal workflow targets (`Target 20`, `Target 40`, `Ring 3`), and keep generic cross-references lowercase (`section 3.2`, `figure 1`, `table 2`, `chapter 4`).

| Engineering or Technical Term | Lowercase Common Form (Use in Prose) | Capitalized Proper Form (Use Only for Official Names) |
| :--- | :--- | :--- |
| System roles and components | `the database`, `the supervisor`, `the worker`, `the server` | `PostgreSQL`, `SQLite`, `Target 20`, `Target 40` |
| Facilities and environments | `the wind tunnel`, `the test environment`, `the cluster` | `Langley 16-Foot Transonic Tunnel`, `GitHub Actions` |
| Scientific laws vs. units | `coulomb`, `newton`, `pascal`, `watt`, `curie`, `kelvin` | `Coulomb's law`, `Newton's method`, `Gauss' equation`, `degree Celsius` |
| Document cross-references | `see section 1.4`, `shown in figure 2`, `listed in table 1` | `Chapter 1: Technical Grammar` (in heading or formal title) |

### 4.2 Sentence Style vs. Headline Style Capitalization

1. **Sentence Style Capitalization**: Capitalize only the first word and proper nouns in sentences, figure captions (`Figure 1. Computing scheme.`), table cells, and run-in headings.
2. **Headline Style Capitalization**: In document titles and major headings (`#` and `##`), capitalize the first word, the last word, and all principal words.
3. In Headline Style, keep articles (`a`, `an`, `the`), conjunctions (`and`, `but`, `or`, `nor`), and short prepositions (`at`, `by`, `for`, `in`, `of`, `on`, `to`, `up`) lowercase internally.
4. In Headline Style, capitalize both parts of hyphenated compounds (`High-Resolution`, `Read-Only`), except compound numerals (`Twenty-one`) or unit symbols (`0.5-cm-Thick`).
5. Begin every vertical list item with a capital letter.

### 4.3 Acronyms and Abbreviations

An acronym is a word formed from the initial letters of a multi-word term (`NASA`, `AST`, `JSON`, `CLI`) without periods.
An abbreviation is a shortened form of a word (`fig.`, `vol.`, `no.`).

1. Write acronyms in all capital letters (`NASA`, `WYSIWYG`, `POSIX`, `FIFO`), except words that have entered standard lowercase vocabulary (`laser`, `radar`, `sonar`).
2. Keep acronym expansions lowercase unless they name a proper noun (`what you see is what you get (WYSIWYG)` vs. `National Aeronautics and Space Administration (NASA)`).
3. Never add periods inside acronyms.

### 4.4 Numerals, Numbers, and SI Units of Measure

1. Use numerals (`0`, `1`, `2`, `15`, `100`) for all measured quantities, technical limits, ages, time durations, percentages, bit widths, and coordinates (`15 seconds`, `24 KB`, `3 words`, `0.8 percent`).
2. Spell out `one` through `nine` for non-measurement narrative counts (`three chapters`, `two alternatives`), and use numerals for `10` and above (`16 test targets`).
3. Never begin a sentence with a bare numeral. Spell out the number or move the numeral inside the clause (`Twenty liters of fuel passed` or `The fuel system passed 20 L`).
4. Place a leading zero before every decimal point for values between `-1` and `1` (`0.2`, `0.05`, never `.2`).
5. Separate numerals from International System of Units (SI) symbols with a space (`15 ms`, `24 KB`, `300 m`, `77 K`), except plane angles (`35°`).
6. Keep SI unit symbols lowercase (`m`, `s`, `g`, `mol`, `rad`) unless the unit derives from a person's proper name (`Hz`, `K`, `N`, `Pa`, `W`, `V`, `A`, `J`, `C`).
7. Never pluralize SI unit symbols (`50 ms` and `10 kg`, never `50 mss`), and omit periods after SI symbols inside a sentence.
