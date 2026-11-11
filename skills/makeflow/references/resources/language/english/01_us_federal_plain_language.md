# U.S. Federal Plain Language Guidelines

- **Source Standard**: U.S. Federal Plain Language Guidelines (`plainlanguage.gov`), Plain Language Action and Information Network (PLAIN).
- **License Status**: Public Domain (`17 U.S.C. § 105` as a work of the United States Government).

This module distills rules for active voice, action verbs, and simple vocabulary from `plainlanguage.gov`.

## 1. Write in the Active Voice

In the active voice, the subject of the sentence performs the action.
Passive voice hides the actor and adds auxiliary verb tokens (`is`, `was`, `been`, `by`).

1. State the actor (the user, the function, or the server) before the verb.
2. Address the human operator or caller directly as `you`.
3. Name the software component explicitly when the system performs the action.

- **Avoid (Passive)**: `The configuration file is validated by the startup hook.`
- **Use (Active)**: `The startup hook validates the configuration file.`

## 2. Eliminate Hidden Verbs (Nominalizations)

A nominalization (a noun formed from a verb) weakens sentences by replacing direct action verbs with abstract nouns.
Common nominalization endings include `-tion`, `-ment`, `-ance`, `-ence`, and `-al`.

1. Replace noun-plus-helper-verb phrases (`make a determination`, `perform an assessment`) with a single action verb.
2. Use simple present, simple past, or simple future verb forms.
3. Avoid progressive (`is running`) and perfect (`has completed`) verb constructions when a simple tense states the fact.

| Hidden Verb Phrase (Avoid) | Direct Action Verb (Use) |
| :--- | :--- |
| `make a determination` | `determine` or `decide` |
| `perform an initialization` | `initialize` |
| `conduct an investigation` | `investigate` |
| `provide a description of` | `describe` |
| `perform validation on` | `validate` |
| `achieve a reduction in` | `reduce` |

## 3. Apply the Approved Modal Verb Ladder

Ambiguous modal verbs (`should`, `would`, `might`, `could`) leave requirement strength unclear.
Select modal verbs from this deterministic ladder:

1. Write `must` for mandatory requirements and invariants.
2. Write `must not` or `never` for strict prohibitions.
3. Write `can` for capability or permission.
4. Write `will` for deterministic future system behavior.
5. Never use inverted conditional modals (`Should the process fail`). Write `If the process fails` instead.

## 4. Use Vertical Lists and Eliminate Slash Constructions

1. Present three or more related conditions, options, or steps as a vertical list.
2. End the lead-in sentence with a colon.
3. Start each list item with a capital letter, and end complete-sentence items with a period.
4. Do not use `and/or` or slash constructions (`client/server`) in prose.
5. Write `A or B, or both` instead of `A and/or B`.

## 5. Public-Domain Simple-Word Replacement Dictionary

Replace ornate or multi-syllable words with direct one-meaning equivalents from the `plainlanguage.gov` replacement list:

| Complex Word or Phrase (Avoid) | Simple Replacement (Use) |
| :--- | :--- |
| `accomplish` | `do` or `finish` |
| `accordance` (`in accordance with`) | `under` or `by` |
| `additional` | `more` or `extra` |
| `address` (as a verb meaning discuss) | `discuss` or `handle` |
| `allocate` | `divide` or `assign` |
| `alternatively` | `or` |
| `ameliorate` | `improve` |
| `anticipate` | `expect` |
| `apparent` | `clear` |
| `apprise` | `inform` or `tell` |
| `approximately` | `about` |
| `ascertain` | `find out` or `verify` |
| `assist` or `assistance` | `help` |
| `attempt` | `try` |
| `caveat` | `warning` or `limit` |
| `cease` | `stop` |
| `commence` | `start` or `begin` |
| `Consequently` | `As a result` |
| `comply with` | `follow` |
| `component` | `part` or `module` |
| `constitute` | `make up` or `form` |
| `deem` | `consider` |
| `demonstrate` | `show` or `prove` |
| `depict` | `show` |
| `designate` | `choose` or `name` |
| `discontinue` | `stop` |
| `dispatch` (general prose) | `send` |
| `due to the fact that` | `because` |
| `effectuate` | `carry out` |
| `endeavor` | `try` |
| `enumerate` | `count` or `list` |
| `equitable` | `fair` |
| `evince` | `show` |
| `exclusively` | `only` |
| `expedite` | `speed up` |
| `facilitate` | `help` |
| `feasible` | `workable` or `possible` |
| `for the purpose of` | `to` or `for` |
| `forfeit` | `lose` or `give up` |
| `formulate` | `devise` or `write` |
| `forthwith` | `immediately` |
| `frequently` | `often` |
| `fundamental` | `basic` or `core` |
| `henceforth` | `from now on` |
| `however` | `but` |
| `identical` | `same` |
| `impact` (as a verb) | `affect` |
| `implement` (when meaning start) | `start` or `carry out` |
| `in addition` | `also` |
| `in excess of` | `more than` |
| `in lieu of` | `instead of` |
| `in order to` | `to` |
| `in the event that` | `if` |
| `inasmuch as` | `because` |
| `indicate` | `show` or `state` |
| `initiate` | `start` |
| `leverage` (as a verb) | `use` |
| `magnitude` | `size` |
| `maintain` (when meaning keep) | `keep` |
| `manifest` | `clear` |
| `methodology` | `method` |
| `modify` | `change` |
| `monitor` | `watch` or `check` |
| `necessitate` | `require` |
| `nevertheless` | `Even so` or `Still` |
| `notwithstanding` | `despite` |
| `numerous` | `many` |
| `objective` (as a noun) | `goal` |
| `obtain` | `get` |
| `on a regular basis` | `regularly` |
| `optimum` | `best` |
| `parameters` (non-code prose) | `limits` |
| `perform` | `do` or `run` |
| `permit` | `allow` or `let` |
| `pertaining to` | `about` |
| `portion` | `part` |
| `possess` | `have` |
| `practicable` | `practical` |
| `preclude` | `prevent` |
| `previously` | `earlier` or `before` |
| `prior to` | `before` |
| `prioritize` | `rank` |
| `proceed` | `go ahead` or `continue` |
| `procure` | `buy` or `get` |
| `proficiency` | `skill` |
| `promulgate` | `issue` or `publish` |
| `provide` | `give` or `supply` |
| `pursuant to` | `under` or `by` |
| `regarding` | `about` |
| `relocate` | `move` |
| `remainder` | `rest` |
| `remuneration` | `pay` |
| `render` (when meaning make) | `make` |
| `request` (as a verb) | `ask` |
| `require` | `need` or `must` |
| `retain` | `keep` |
| `selection` | `choice` |
| `similar to` | `like` |
| `solicit` | `ask for` |
| `state-of-the-art` | `modern` or `latest` |
| `subsequent` | `next` or `later` |
| `subsequent to` | `after` |
| `substantial` | `large` |
| `sufficient` | `enough` |
| `terminate` | `stop` or `end` |
| `therefore` | `as a result` |
| `transmit` | `send` |
| `transpire` | `happen` |
| `ultimate` | `final` |
| `utilize` or `utilization` | `use` |
| `validate` (general prose) | `confirm` or `check` |
| `viable` | `workable` |
| `warrant` (as a verb) | `justify` or `call for` |
| `whenever` | `when` |
| `whereas` | `while` or `because` |
| `with respect to` | `about` or `for` |
| `witness` (as a verb in general prose) | `see` or `observe` |
