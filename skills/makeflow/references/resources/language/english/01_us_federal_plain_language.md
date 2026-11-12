# U.S. Federal Plain Language Guidelines

- **Source Standard**: U.S. Federal Plain Language Guidelines (`plainlanguage.gov`), Plain Language Action and Information Network (PLAIN).
- **License Status**: Public Domain (`17 U.S.C. § 105` as a work of the United States Government).

This module encapsulates the complete U.S. Federal Plain Language Guidelines (`plainlanguage.gov`) across eight structured parts for software documentation and agent instructions.

## 1. Audience and Direct Address

Identify your target audience and their primary tasks before you draft any specification or procedure.
Write directly to the single reader who executes the task.

### 1.1 Identify Audience Knowledge and Core Tasks

1. List the primary tasks that the reader needs to complete.
2. Match technical depth to the reader role, such as system operator, contributor, or subagent worker.
3. Omit background history that does not help the reader complete the task.

### 1.2 Address Separate Audiences Separately

When a document governs multiple distinct user roles, mixing instructions in one paragraph confuses every reader.
Separate audiences into distinct sections or headings so each role reads only applicable requirements.

- **Avoid**: `Operators and subagent workers must validate the environment and seal the test receipt before advancing.`
- **Use**: `If you are the supervisor agent, validate the environment at Target 00. If you are the TDD worker, seal the test receipt at Target 30.`

### 1.3 Address the Reader Directly as `you`

Use the second-person pronoun `you` to assign responsibility to the reader.
Define who `you` represents at the start of the document or section.
Name the software component or agency explicitly when the system performs the action.
Use singular nouns and verbs so requirements apply unambiguously to each individual reader.

- **Avoid**: `Applicants must submit their configuration manifests to the validation service.`
- **Use**: `You must submit your configuration manifest to the validation service.`

## 2. Document Organization, Headings, and Paragraph Structure

Organize documents in chronological task order so the reader follows steps without jumping backward or forward.

### 2.1 Write Informative Headings and Short Sections

Break long documents into short, focused sections under informative headings.
Use three heading formats to guide scanning:

1. Question headings anticipate the reader inquiry, such as `How do I verify the test receipt?`
2. Action headings start with an imperative verb, such as `Verify the Staged Merkle Tree`.
3. Statement headings summarize the section rule, such as `Mandatory Pre-Flight Static Checks`.

- **Avoid**: `Tree Validation`
- **Use**: `How to Validate the Staged Merkle Tree`

### 2.2 Start Every Paragraph with a Topic Sentence

Begin every paragraph with a topic sentence (a first sentence that states the core rule).
Limit each paragraph to 6 sentences or fewer.
Cover only one main idea in each paragraph.

### 2.3 Place the Main Idea Before Exceptions and Conditions

State the core rule or main idea before you introduce long exceptions or complex conditions.
If a condition is short (a few words) and prevents misreading, place the condition first followed by a comma.
If an exception or condition is long, state the main clause first.
When a sentence has multiple conditions, present those conditions in a vertical list.
Use `if` for conditions instead of `where`.
Use `when` for recurring events.

- **Avoid**: `Except when the configuration file overrides the timeout and the runner operates in batch mode, the daemon polls every 5 seconds.`
- **Use**: `The daemon polls every 5 seconds, unless the configuration file overrides the timeout during batch mode.`

### 2.4 Connect Ideas with Explicit Transition Words

Use transition words at the start of sentences to show how a statement relates to the prior sentence:

1. Use pointing words (`this`, `that`, `these`, `those`) with an explicit noun (`this configuration`) to link back cleanly.
2. Use echo links (repeated key noun phrases) to maintain continuity across paragraphs.
3. Use explicit connectives (`also`, `in addition`, `for example`, `therefore`, `as a result`, `however`) to show logical relationships.

## 3. Active Voice, Verb Tenses, Modal Ladder, and Hidden Verbs (Nominalizations)

Strong, direct verbs make technical specifications clear and unambiguous.

### 3.1 Write in the Active Voice and Simple Present Tense

In the active voice, the subject of the sentence performs the action (`actor -> verb -> object`).
Passive voice hides the actor and adds auxiliary verb tokens (`is`, `was`, `been`, `by`).
Use the simple present tense (`the hook validates`) instead of progressive (`is validating`) or future (`will validate`) forms.

1. State the actor (the user, the function, or the server) before the verb.
2. Address the human operator or caller directly as `you`.
3. Name the software component explicitly when the system performs the action.

- **Avoid**: `The configuration file is validated by the startup hook.`
- **Use**: `The startup hook validates the configuration file.`

### 3.2 Apply the Approved Modal Verb Ladder and Ban `shall`

Legalistic and ambiguous modals (`shall`, `should`, `would`, `might`, `could`) obscure requirement strength.
Never use `shall` to state a requirement because legal and technical readers interpret `shall` inconsistently.
Select modal verbs from this deterministic ladder:

1. Write `must` for mandatory requirements and invariants (`you must run static checks`).
2. Write `must not` or `never` for strict prohibitions (`you must not bypass the gate`).
3. Write `can` or `may` for capability or permission.
4. Write `will` only when predicting deterministic future system responses.
5. Never use inverted conditional modals (`Should the process fail`).
6. Write `If the process fails` for conditional clauses.

### 3.3 Agentic Override on Contractions

While `plainlanguage.gov` permits conversational contractions in general public correspondence, [`00_index_and_agentic_rationale.md`](./00_index_and_agentic_rationale.md) Rule 8 strictly prohibits contractions in technical specifications.
Subword tokenizers split contractions (`don't`, `won't`, `it's`) into detached fragments that dilute negation weights in self-attention layers.
Always write full uncontracted forms (`do not`, `will not`, `cannot`, `it is`) in prose and tables.

### 3.4 Eliminate Hidden Verbs (Nominalizations)

A nominalization (a noun formed from an action verb) weakens sentences by replacing direct verbs with abstract nouns.
Common nominalization endings include `-tion`, `-sion`, `-ment`, `-ance`, `-ence`, `-al`, and `-ing` paired with weak helper verbs.
Replace noun-plus-helper-verb phrases (`make`, `perform`, `conduct`, `provide`, `achieve`, `reach`, `carry out`, `undertake`) with one direct action verb:

| Hidden Verb Phrase (Avoid) | Direct Action Verb (Use) |
| :--- | :--- |
| `achieve a reduction in` | `reduce` |
| `carry out a review` | `review` |
| `conduct an analysis` | `analyze` |
| `conduct an assessment` | `assess` |
| `conduct an audit` | `audit` |
| `conduct an evaluation` | `evaluate` |
| `conduct an inspection` | `inspect` |
| `conduct an investigation` | `investigate` |
| `effect a recovery` | `recover` |
| `establish a connection` | `connect` |
| `gain an understanding` | `understand` |
| `give consideration to` | `consider` |
| `have an effect on` | `affect` |
| `make a comparison` | `compare` |
| `make a decision` | `decide` |
| `make a determination` | `determine` or `decide` |
| `make a measurement` | `measure` |
| `make a modification` | `modify` or `change` |
| `make a recommendation` | `recommend` |
| `make an adjustment` | `adjust` |
| `make an application` | `apply` |
| `make an assumption` | `assume` |
| `make the payment` | `pay` |
| `perform a calculation` | `calculate` |
| `perform a check on` | `check` |
| `perform a computation` | `compute` |
| `perform a migration` | `migrate` |
| `perform a transformation` | `transform` |
| `perform an allocation` | `allocate` or `assign` |
| `perform an initialization` | `initialize` |
| `perform configuration of` | `configure` |
| `perform deletion of` | `delete` |
| `perform deployment of` | `deploy` |
| `perform execution of` | `execute` or `run` |
| `perform implementation of` | `implement` |
| `perform installation of` | `install` |
| `perform notification` | `notify` |
| `perform registration` | `register` |
| `perform resolution of` | `resolve` |
| `perform transmission of` | `transmit` or `send` |
| `perform validation on` | `validate` |
| `perform verification of` | `verify` |
| `provide a description of` | `describe` |
| `provide an explanation` | `explain` |
| `provide assistance` | `help` |
| `provide documentation` | `document` |
| `provide protection` | `protect` |
| `reach a conclusion` | `conclude` or `decide` |
| `take action on` | `act` |
| `undertake the calculation` | `calculate` |

## 4. Word Choice, Jargon, Noun Strings, Abbreviations, Definitions, and Compound Prepositions

Choose familiar, concrete words and use each term consistently throughout a specification.

### 4.1 Ban Legalistic and Bureaucratic Jargon

Keep necessary technical nouns (`mutex`, `webhook`, `subagent`).
Strip out archaic legalisms (`above-mentioned`, `aforementioned`, `foregoing`, `henceforth`, `hereafter`, `hereby`, `heretofore`, `herewith`, `thereafter`, `thereof`, `therewith`, `whatsoever`, `whereat`, `wherein`, `whereof`) and business clichés (`value added`, `touch base`, `synergistic`).

- **Avoid**: `Apply sufficient torque to the assembly to ensure that loosening cannot occur hereafter.`
- **Use**: `Tighten the assembly securely so that it does not loosen.`

### 4.2 Break Up Stacked Noun Strings

A noun string (three or more consecutive nouns modifying a final noun) forces the reader to guess modifier boundaries.
Limit noun clusters to 3 words or fewer by inserting prepositions, hyphens, or verbs.

- **Avoid**: `Update the underground mine worker safety protection procedures development manual.`
- **Use**: `Update the manual for developing safety procedures that protect underground mine workers.`

### 4.3 Minimize Abbreviations and Formal Definitions

Undefined or single-use abbreviations force readers to flip back and forth through the text.
Follow these rules for abbreviations and definitions:

1. Spell out an abbreviation at first use unless it is universally known (`HTML`, `JSON`, `URL`, `CLI`, `API`).
2. Do not introduce an abbreviation if you use the term fewer than three times in a document.
3. Avoid standalone glossary sections at the start of a file.
4. Define every new technical term inline in under 10 words at first use.
5. Never define common words in a way that contradicts their ordinary dictionary meaning.

### 4.4 Place Modifiers Carefully and Use Terms Consistently

Place modifiers such as `only`, `always`, and `never` immediately before the exact word or phrase they modify.
Use the same word consistently for the same concept.
Never substitute synonyms just for stylistic variety.

- **Avoid**: `You are only required to provide the following fields when initializing the client.`
- **Use**: `You must provide only the following fields when you initialize the client.`

### 4.5 Compress Wordy Compound Prepositions

Replace multi-word prepositional phrases with direct single-word prepositions or conjunctions:

| Wordy Compound Preposition (Avoid) | Direct Replacement (Use) |
| :--- | :--- |
| `as a means of` | `to` |
| `as prescribed by` | `in` or `under` |
| `at the present time` | `now` |
| `by means of` | `by` or `with` |
| `due to the fact that` | `because` or `since` |
| `during the course of` | `during` |
| `during the period` | `during` |
| `for a period of` | `for` |
| `for the purpose of` | `to` or `for` |
| `in a manner similar to` | `like` |
| `in a timely manner` | `promptly` or `on time` |
| `in accordance with` | `by`, `under`, or `following` |
| `in addition to` | `besides` or `also` |
| `in an effort to` | `to` |
| `in close proximity to` | `near` |
| `in connection with` | `with` or `about` |
| `in excess of` | `more than` or `over` |
| `in favor of` | `for` |
| `in lieu of` | `instead of` |
| `in order that` | `for` or `so` |
| `in order to` | `to` |
| `in regard to` | `about` or `on` |
| `in relation to` | `about` or `with` |
| `in terms of` | `in` or `for` |
| `in the absence of` | `without` |
| `in the amount of` | `for` |
| `in the course of` | `during` or `in` |
| `in the event of` | `if` |
| `in the event that` | `if` |
| `in the near future` | `soon` |
| `in view of` | `since` or `because` |
| `on the basis of` | `by` or `from` |
| `pertaining to` | `about` or `on` |
| `prior to` | `before` |
| `pursuant to` | `under`, `by`, or `following` |
| `subsequent to` | `after` |
| `under the provisions of` | `under` |
| `until such time as` | `until` |
| `with reference to` | `about` |
| `with regard to` | `about` or `on` |
| `with respect to` | `about` or `for` |
| `with the exception of` | `except for` |

## 5. Concise Sentences, SVO Proximity, and Positive Language

Concise, affirmative sentences reduce cognitive load for human readers and preserve self-attention locality for AI agents.

### 5.1 Write Short Sentences and Keep Subject, Verb, and Object Close Together

Keep procedural sentences at 20 words or fewer.
Keep descriptive sentences at 25 words or fewer.
Maintain strict Subject-Verb-Object (SVO) proximity in every clause.
Do not insert modifying phrases between the subject and its verb or between the verb and its object.

- **Avoid**: `The supervisor agent, after inspecting all five state cards in the workflow directory and verifying the configuration file, dispatches the worker.`
- **Use**: `The supervisor agent dispatches the worker after inspecting all five state cards and verifying the configuration file.`

### 5.2 Use Positive Language and Eliminate Double Negatives

Write affirmative statements that state what is true or what action to take when a condition holds.
Eliminate double negatives (`not uncommon`, `not invalid`, `not insignificant`, `not fewer than`, `unless ... not`, `cannot ... without`) and nested exceptions.

| Negative or Double-Negative Phrase (Avoid) | Affirmative Replacement (Use) |
| :--- | :--- |
| `cannot proceed without` | `must have ... to proceed` |
| `does not become void unless` | `remains valid if` |
| `has not yet attained` | `is under` |
| `in the absence of express approval, no action is permitted` | `you must get express approval before acting` |
| `is not valid unless` | `is valid only if` |
| `may not run until` | `may run only when` |
| `no fewer than` | `at least` |
| `not insignificant` | `significant` or `large` |
| `not invalid` | `valid` |
| `not uncommon` | `common` or `frequent` |

- **Avoid**: `The test receipt is not valid unless the worker does not modify the test directory after sealing.`
- **Use**: `The test receipt is valid only if the worker leaves the test directory unmodified after sealing.`

## 6. Aids to Clarity: Lists, Decision Tables, Examples, Cross-References, and Web Structure

Visual structure and explicit navigation help readers locate rules without parsing dense paragraphs.

### 6.1 Use Vertical Lists and Ban Slash Constructions

1. Present three or more related conditions, options, or steps as a vertical list.
2. End the lead-in sentence with a colon.
3. Keep all list items parallel in grammatical form.
4. Start each list item with a capital letter.
5. End complete-sentence list items with a period.
6. Never use `and/or` or slash constructions (`client/server`, `read/write`) in prose.
7. Write `A or B, or both` instead of `A and/or B`.

### 6.2 Convert Branching Rules into Decision Tables

When a rule depends on multiple branching conditions (`if ... and ... then ...`), use a structured decision table:

| If the target gate is: | And the number of options is: | Then the agent must: |
| :--- | :--- | :--- |
| Target 20 (`plan`) | One approval action (`N = 1`) | Write `20_plan.md`, mirror to `<brain>/plan.md`, and stop tool calls |
| Target 42 (`triage`) | Two or more alternatives (`N >= 2`) | Print the full facts in chat and call `ask_question` |
| Target 50 (`commit`) | One approval action (`N = 1`) | Print the proposed commit message in chat and stop tool calls |

### 6.3 Use Concrete Examples and Highlight Critical Concepts

Follow abstract rules with concrete `Avoid -> Use` examples so the reader sees the exact transformation.
Use bold text sparingly to highlight key terms.
Never use all-caps in body prose outside standard alert labels (`WARNING`, `CAUTION`, `NOTE`).

### 6.4 Minimize Cross-References and Ban Vague Positional Pointers

Frequent cross-references force readers and agents to jump across files and lose context.
If the referenced rule is short, repeat the rule inline instead of cross-referencing.
When you reference another section or file, ban vague positional cross-references (`above`, `below`, `herein`, `aforementioned`, `the preceding section`).
Always cite the exact section heading or provide a clickable `file://` link with line numbers.

- **Avoid**: `Verify the receipt hash in accordance with the aforementioned rules in the preceding section above.`
- **Use**: `Verify the receipt hash using the steps in Section 3.2 or [tdd_guard.py](file:///path/to/scripts/tdd_guard.py#L1-L40).`

### 6.5 Write Self-Descriptive Links and Task-Oriented Web Structure

Write link text that names the exact destination file or topic so the reader knows where the link leads.
Never write `click here`, `read more`, or bare URLs as link text.
Organize documentation directly by user task instead of dumping unstructured Frequently Asked Questions (FAQ) lists or monolithic PDFs.

## 7. Testing and Verifying Specification Clarity

Plain language guidelines require testing specifications against real readers and automated validators before release.

### 7.1 Paraphrase Testing (Socratic Protocol Verification)

In paraphrase testing, a reader explains in their own words what a specification requires at specific stopping points.
In agent workflows, apply Socratic pre-planning inquiry before writing code.
Require the agent to state exact invariants, boundary conditions, and failure modes in a Socratic Invariant Audit table.

### 7.2 Scenario Usability Testing

In usability testing, participants receive a concrete task scenario and attempt to find and execute the required procedure.
Verify documentation and runbooks by executing the exact commands in a clean environment.

### 7.3 Controlled Comparative Studies and Automated Linting

Controlled comparative studies measure error rates and task completion across candidate specification drafts.
Enforce deterministic specification linting in continuous integration to check sentence limits, punctuation, and vocabulary tables on every change.

## 8. Public-Domain Simple-Word Replacement Dictionary

Replace ornate or archaic phrases with direct equivalents from the `plainlanguage.gov` simple-word replacement dictionary (contractions expanded per Section 3.3):

| Complex Word or Phrase (Avoid) | Simple Replacement (Use) |
| :--- | :--- |
| `a and/or b` | `a or b, or both` |
| `a number of` | `some` or `many` |
| `accompany` | `go with` |
| `accomplish` | `carry out`, `do`, or `finish` |
| `accordance` (`in accordance with`) | `by`, `following`, `per`, or `under` |
| `accorded` | `given` |
| `accordingly` | `so` |
| `accrue` | `add` or `gain` |
| `accurate` | `correct`, `exact`, or `right` |
| `additional` | `added`, `more`, `extra`, or `other` |
| `address` (as a verb meaning discuss) | `discuss` or `handle` |
| `addressees` | `you` |
| `addressees are requested` | `please` (or omit) |
| `adjacent to` | `next to` |
| `advantageous` | `helpful` |
| `adversely impact on` | `hurt` or `set back` |
| `advise` | `recommend` or `tell` |
| `afford an opportunity` | `allow` or `let` |
| `aircraft` | `plane` |
| `allocate` | `divide` or `assign` |
| `alternatively` | `or` |
| `ameliorate` | `improve` |
| `anticipate` | `expect` |
| `apparent` | `clear` or `plain` |
| `appreciable` | `many` or `large` |
| `appropriate` | `proper` or `right` (or omit) |
| `approximate` or `approximately` | `about` |
| `arrive onboard` | `arrive` |
| `as a means of` | `to` |
| `as prescribed by` | `in` or `under` |
| `ascertain` | `find out`, `learn`, or `verify` |
| `assist` or `assistance` | `aid` or `help` |
| `at the present time` | `at present` or `now` |
| `attain` | `meet` or `reach` |
| `attempt` | `try` |
| `be advised` | omit |
| `benefit` | `help` |
| `by means of` | `by` or `with` |
| `capability` | `ability` |
| `caveat` | `warning` or `limit` |
| `cease` | `stop` |
| `close proximity` | `near` |
| `combat environment` | `combat` |
| `combined` | `joint` |
| `commence` | `begin` or `start` |
| `comply with` | `follow` |
| `component` | `part` or `module` |
| `comprise` | `form`, `include`, or `make up` |
| `concerning` | `about` or `on` |
| `consequently` | `so` or `as a result` |
| `consolidate` | `combine`, `join`, or `merge` |
| `constitute` or `constitutes` | `is`, `form`, or `make up` |
| `contains` | `has` |
| `convene` | `meet` |
| `currently` | `now` (or omit) |
| `deem` | `believe`, `consider`, or `think` |
| `delete` | `cut` or `drop` |
| `demonstrate` | `prove` or `show` |
| `depart` | `leave` |
| `depict` | `show` |
| `designate` | `appoint`, `choose`, or `name` |
| `desire` | `want` or `wish` |
| `determine` | `decide`, `figure`, or `find` |
| `disclose` | `show` |
| `discontinue` | `drop` or `stop` |
| `dispatch` (general prose) | `send` |
| `disseminate` | `give`, `issue`, `pass`, or `send` |
| `due to the fact that` | `because`, `due to`, or `since` |
| `during the period` | `during` |
| `effect modifications` | `make changes` |
| `effectuate` | `carry out` |
| `elect` | `choose` or `pick` |
| `eliminate` | `cut`, `drop`, or `end` |
| `employ` | `use` |
| `encounter` | `meet` |
| `endeavor` | `try` |
| `ensure` | `make sure` |
| `enumerate` | `count` or `list` |
| `equipments` | `equipment` |
| `equitable` | `fair` |
| `establish` | `set up`, `prove`, or `show` |
| `evidenced` | `showed` |
| `evident` | `clear` |
| `evince` | `show` |
| `exclusively` | `only` |
| `exhibit` | `show` |
| `expedite` | `hasten` or `speed up` |
| `expeditious` | `fast` or `quick` |
| `expend` | `spend` |
| `expertise` | `ability` |
| `expiration` | `end` |
| `facilitate` | `ease` or `help` |
| `failed to` | `did not` |
| `feasible` | `can be done`, `possible`, or `workable` |
| `females` | `women` |
| `finalize` | `complete` or `finish` |
| `for a period of` | `for` |
| `for example, ... etc.` | `for example` or `such as` |
| `for the purpose of` | `to` or `for` |
| `forfeit` | `give up` or `lose` |
| `formulate` | `devise` or `write` |
| `forthwith` | `immediately` |
| `forward` | `send` |
| `frequently` | `often` |
| `function` | `act`, `role`, or `work` |
| `fundamental` | `basic` or `core` |
| `furnish` | `give` or `send` |
| `has a requirement for` | `needs` |
| `henceforth` | `from now on` |
| `herein` | `here` |
| `heretofore` | `until now` |
| `herewith` | `below` or `here` |
| `however` | `but` |
| `identical` | `same` |
| `identify` | `find`, `name`, or `show` |
| `immediately` | `at once` |
| `impact` (as a verb) or `impacted` | `affect`, `affected`, or `changed` |
| `implement` | `carry out` or `start` |
| `in a timely manner` | `on time` or `promptly` |
| `in accordance with` | `by`, `following`, `per`, or `under` |
| `in addition` | `also`, `besides`, or `too` |
| `in an effort to` | `to` |
| `in excess of` | `more than` |
| `in lieu of` | `instead` or `instead of` |
| `in order that` | `for` or `so` |
| `in order to` | `to` |
| `in regard to` | `about`, `concerning`, or `on` |
| `in relation to` | `about`, `with`, or `to` |
| `in the amount of` | `for` |
| `in the event of` or `in the event that` | `if` |
| `in the near future` | `shortly` or `soon` |
| `in the process of` | omit |
| `in view of` | `since` |
| `in view of the above` | `so` |
| `inasmuch as` | `because` or `since` |
| `inception` | `start` |
| `incumbent upon` | `must` |
| `indicate` | `show`, `state`, or `write down` |
| `indication` | `sign` |
| `initial` | `first` |
| `initiate` | `start` |
| `inter alia` | omit |
| `interface` | `meet` or `work with` |
| `interpose no objection` | `do not object` |
| `is applicable to` | `applies to` |
| `is authorized to` | `may` |
| `is in consonance with` | `agrees with` or `follows` |
| `is responsible for` | `handles` (or omit) |
| `it appears` | `seems` |
| `it is` | omit |
| `it is essential` | `must` or `need to` |
| `it is requested` | `please`, `we request`, or `I request` |
| `leverage` (as a verb) | `use` |
| `liaison` | `discussion` |
| `limited number` | `limits` |
| `magnitude` | `size` |
| `maintain` | `keep` or `support` |
| `manifest` | `clear` |
| `maximum` | `greatest`, `largest`, or `most` |
| `methodology` | `method` |
| `minimize` | `decrease` |
| `minimum` | `least` or `smallest` |
| `modify` | `change` |
| `monitor` | `check` or `watch` |
| `necessitate` | `cause`, `need`, or `require` |
| `nevertheless` | `even so` or `still` |
| `not later than 10 May` | `by 10 May` or `before 11 May` |
| `not later than 1600` | `by 1600` |
| `notwithstanding` | `despite`, `in spite of`, or `still` |
| `notify` | `let know` or `tell` |
| `numerous` | `many` |
| `objective` | `aim` or `goal` |
| `obligate` | `bind` or `compel` |
| `observe` | `see` |
| `obtain` | `get` |
| `on a regular basis` | `regularly` (or omit) |
| `operate` | `run`, `use`, or `work` |
| `optimum` | `best`, `greatest`, or `most` |
| `option` | `choice` or `way` |
| `parameters` (non-code prose) | `limits` |
| `participate` | `take part` |
| `perform` | `do` or `run` |
| `permit` | `allow` or `let` |
| `pertaining to` | `about`, `of`, or `on` |
| `portion` | `part` |
| `possess` | `have` or `own` |
| `practicable` | `practical` |
| `preclude` | `prevent` |
| `previous` | `earlier` |
| `previously` | `before` or `earlier` |
| `prior to` | `before` |
| `prioritize` | `rank` |
| `proceed` | `do`, `go ahead`, `continue`, or `try` |
| `procure` | `buy` or `get` (or omit) |
| `proficiency` | `skill` |
| `promulgate` | `issue` or `publish` |
| `provide` | `give`, `offer`, `say`, or `supply` |
| `provided that` | `if` |
| `provides guidance for` | `guides` |
| `purchase` | `buy` |
| `pursuant to` | `by`, `following`, `per`, or `under` |
| `reflect` | `say` or `show` |
| `regarding` | `about`, `of`, or `on` |
| `relative to` | `about` or `on` |
| `relocate` | `move` |
| `remain` | `stay` |
| `remainder` | `rest` |
| `remuneration` | `pay` or `payment` |
| `render` | `give` or `make` |
| `represents` | `is` |
| `request` (as a verb) | `ask` |
| `require` | `must` or `need` |
| `requirement` | `need` |
| `reside` | `live` |
| `retain` | `keep` |
| `said`, `some`, or `such` | `the`, `this`, or `that` |
| `selection` | `choice` |
| `set forth in` | `in` |
| `similar to` | `like` |
| `solicit` | `ask for` or `request` |
| `state-of-the-art` | `latest` or `modern` |
| `subject` (as a modifier) | `the`, `this`, or `your` |
| `submit` | `give` or `send` |
| `subsequent` | `later` or `next` |
| `subsequent to` | `after` |
| `subsequently` | `after`, `later`, or `then` |
| `substantial` | `large` or `much` |
| `successfully complete` | `complete` or `pass` |
| `sufficient` | `enough` |
| `take action to` | omit |
| `terminate` | `end` or `stop` |
| `the month of` | omit |
| `the undersigned` | `I` |
| `the use of` | omit |
| `there are` | omit |
| `therefore` | `so` or `as a result` |
| `therein` | `there` |
| `there is` | omit |
| `thereof` | `its` or `their` |
| `this activity` or `this command` | `us` or `we` |
| `time period` | `time` or `period` |
| `timely` | `prompt` |
| `transmit` | `send` |
| `transpire` | `happen` |
| `type` (as a filler noun) | omit |
| `ultimate` | `final` |
| `under the provisions of` | `under` |
| `until such time as` | `until` |
| `utilize` or `utilization` | `use` |
| `validate` (general prose) | `confirm` or `check` |
| `viable` | `practical` or `workable` |
| `vice` | `instead of` or `versus` |
| `warrant` (as a verb) | `call for`, `justify`, or `permit` |
| `whenever` | `when` |
| `whereas` | `because`, `since`, or `while` |
| `with reference to` | `about` |
| `with respect to` | `about` or `for` |
| `with the exception of` | `except for` |
| `witness` or `witnessed` | `see`, `observe`, or `saw` |
| `your office` | `you` |
| `/` (slash) | `and` or `or` |
