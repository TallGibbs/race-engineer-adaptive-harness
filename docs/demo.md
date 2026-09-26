# Demo: Race Engineer Adaptive Harness

Materials for the 3-minute live demo and the questions after it. Theme: Statement One,
Recursive Harnessing. The harness proposes changes to its own configuration, a fixed
evaluator decides, and lessons in MongoDB Atlas carry learning across cycles.

Every number below comes from a named file in this repository: `reports/exp1.md`,
`reports/exp2.md`, `reports/exp3.md`, `reports/explain_control_checks.md`, `README.md`,
`CONTRACT.md`, or `docs/index.html`. The `show` output quoted here was captured read-only
from the Atlas records on 2026-09-26.

## 1. Runbook

Before going on stage: open a terminal in the repository with the virtual environment
active and `.env` filled in, widen the window to about 110 columns, and open
`docs/index.html` and `reports/exp3.md` in browser tabs as fallbacks. Every command
below only reads. None runs a cycle or writes to the database.

Syntax is checked against `README.md` ("Inspecting the records") and
`adaptive_harness/report/cli.py` (`_show_parser`); `show run` is delegated to
`adaptive_harness/store/cli.py`.

| # | command | expected output (one line) | fallback if Atlas or the network fails |
|---|---|---|---|
| 1 | `python -m adaptive_harness show run D2-20260926T173124-912dd0` | v1 baseline run on D2: 13 model calls, 4 tool calls (list_events 2025; race_control_messages and track_status 2025 round 13; interval k 7 n 14), 00030 schema check passed, 00031 GO | `reports/exp3.md`, "Root cause `lesson:exp3:D2-20260926T173124-912dd0:E7`" (why-chain cites 00017, 00020, 00025, 00029, 00030, 00031) |
| 2 | `python -m adaptive_harness show lessons --query "race row cites same-run tool results" --snapshot M1 --k 3` | `$vectorSearch lessons_vector`, three verified measurement lessons, one per cycle (exp2 0.8888, exp1 0.8830, exp3 0.8773) | `README.md`, "Lessons and retrieval"; `docs/index.html#learned` ("The same verified lesson across cycles"); lesson texts in the Analyze section of each report |
| 3 | `python -m adaptive_harness show version v4` | v4, status rejected, parent v1, one change: add `/checks/row_evidence` = `{"enabled": true}`, citing `lesson:exp3:D2-20260926T173124-912dd0:E7`; decision reasons R1 and R2 | `reports/exp3.md`, "Improve" then "Proposal" |
| 4 | `python -m adaptive_harness show experiment exp3` | decision rejected, pinned v1; R0 holds, R1 fails (P1: E2, E3), R2 fails (11 vs 11), R3 holds (10 vs 10), R4 holds (1181.9 s vs 993.0 s, limit 1.5x); runs per arm by aggregation | `reports/exp3.md`, "Acceptance rules"; `docs/index.html#experiments` |
| 5 | `python -m adaptive_harness show run D2-20260926T175842-ce993b --width 200` | v4 candidate run on D2: 00036 schema passed, 00037 row_evidence passed with no reasons, 00038 GO (E7 still failed per the report) | `reports/exp3.md`, "Checks by case and arm" (D2 E7 FAIL under candidate); `docs/index.html#learned` |
| 6 | `python -m adaptive_harness show cost --experiment exp3` | model calls and tokens by arm, role, and model from an aggregation over `events` ($match, $lookup, $group), 445 calls in total | `reports/exp3.md`, "Cost per arm" |
| 7 (Q&A only) | `python -m adaptive_harness show experiment exp1` | rejected on R2 (11 vs 11); R4 1134.9 s vs 1183.6 s | `reports/exp1.md`, "Acceptance rules" |

Notes:

- For command 5, `--width 200` keeps the row_evidence line from being cut. At the default
  width the line is truncated after `{"check": "row_evidenc...`.
- `show cost` counts every run recorded in an arm, including the two baseline runs that
  ended HARNESS on a provider credit error and were rerun (`reports/exp3.md`, "HARNESS
  results"), so its baseline row is larger than the 6-run table in the report.
- For the index story, there is no live command: show `reports/explain_control_checks.md`
  (COLLSCAN examining 1,952 documents before, IXSCAN on `control_checks` examining 0
  after).
- If the terminal fails entirely, run the whole demo from `docs/index.html`: `#how`,
  `#mongodb`, `#experiments`, `#learned`, `#links`.

## 2. Live script (3:00)

Screens are named in brackets. Say the sentences; do not read the brackets.

**0:00 to 0:20. The problem.** [Screen: `docs/index.html#what`]

"This is a race engineer agent. Given a Grand Prix, it audits whether the race had a safety
car, a virtual safety car, or a red flag, and for a venue it reports how often races get
neutralized. It pulls real timing data with FastF1. The hard part is not the answer; it is
proving each row came from the data. And we did not hand-tune the agent. We built a harness
that tries to improve its own process, and a fixed evaluator it cannot edit."

**0:20 to 1:00. The harness, and MongoDB as its memory.** [Screen: terminal, command 1]

"Three role agents, a race engineer, a statistician and a data engineer, work each case
through fixed stages. Every model call, tool call, check and decision lands in MongoDB
Atlas. Here is one run, straight from the events collection: four tool calls, list events,
race control messages, track status, an interval. Then event 30, a schema check, passed.
Event 31, GO. But the evaluator failed it on E7, evidence."

[Screen: terminal, command 2]

"When a run fails, the analyze phase writes a root cause with a why-chain pointing at event
ids, code verifies it against the record, and it becomes a lesson with a 384-dimension
embedding. Roles retrieve lessons through Atlas Vector Search. This query returns three
lessons, one from each cycle, all saying the same thing: nothing checks that each race row
cites tool results about that race."

**1:00 to 2:15. Three cycles, and the refusal of v4.** [Screen: `docs/index.html#experiments`]

"We ran three full DMAIC cycles on the same six cases: two development, two held-out, two
control. Each time the only defect was E7 on case D2. Cycle one, the harness proposed v2,
enabling a venue check. The evaluator: 11 development checks against 11. Rejected. Cycle
two, it was shown v2's outcome and proposed v3, a source-agreement check. 11 against 11.
Rejected."

[Screen: terminal, command 3]

"Between cycles, we added the check its lessons kept asking for, row evidence, to the
catalog, switched off. We did not turn it on. In cycle three the improvement agent saw v2
and v3, and proposed v4: enable row evidence, citing its own lesson."

[Screen: terminal, command 4]

"And the evaluator refused it. R1: control case P1 lost two checks. R2: still 11 against 11.
Time and held-out held. v1 stays pinned."

[Screen: terminal, command 5]

"Why? Here is v4's own run. Event 37: row evidence, passed. Event 38: GO. E7 still failed.
The check accepts a row once any citation matches the race. The evaluator fails the row if
any citation does not. The harness built a guardrail looser than the measurement, and the
fixed evaluator caught it."

**2:15 to 2:45. What this proves about recursive harnessing.** [Screen: `docs/index.html#learned`]

"A self-improving harness is only as honest as what judges it. Ours changed its own checks
three times, each change traced to a verified lesson in MongoDB, and each time a fixed
evaluator it cannot touch said no. Nothing was accepted because nothing was proven. The
memory is working: three cycles converged on one root cause and one check. What it has
not yet learned is to make that check as strict as the evaluator, and the records show
exactly where."

**2:45 to 3:00. Close.** [Screen: `docs/index.html#links`]

"Everything is in the repo: the reports, the Mermaid maps, and the show commands you just
saw, all generated from the Atlas records. TallGibbs slash race-engineer-adaptive-harness.
Thank you."

## 3. Likely questions

**Why didn't it improve?**
Because no candidate beat v1 on the fixed rules. v2 and v3 each passed 11 development
checks against 11 (`reports/exp1.md`, `reports/exp2.md`, "Acceptance rules"). v4 enabled
the right kind of check, but the check was looser than the evaluator: row_evidence passed
in all six candidate runs, so the one repair round never fired (`docs/index.html#learned`).
A rejected cycle is a recorded outcome, not a crash.

**Isn't the evaluator just stricter?**
Yes, and that is the point. The evaluator (`adaptive_harness/evaluate/checks.py`,
`_cites_race`) requires every citation in a row to be a same-run tool result for that year
and round. The in-harness check (`adaptive_harness/runner/checks.py`, `row_evidence`)
accepts a row once one citation matches. The evaluator was fixed before any harness code
and the harness may not edit it (`CLAUDE.md`, `README.md` "Provenance"). The gap between
the two is the thing the next cycle has to close.

**Did v4 really cause the P1 regression?**
Not provably. Under the same v1 configuration, P1 lost E2 and E3 in exp1's memory_only arm
too (`reports/exp1.md`, "Checks by case and arm", control 10, 8, 10). Scores vary between
identical runs, which is why R1 and R2 compare against the current version run fresh, and
why we make no statistical claim. v4 would still have failed R2 on its own.

**What does MongoDB actually do here?**
It is the harness's memory and its audit trail. Six collections: runs, events, lessons,
evaluations, experiments, harness_versions (`README.md`, "What MongoDB Atlas records").
Lessons are retrieved with Atlas Vector Search over 384-dimension bge-small embeddings,
pre-filtered by memory snapshot so older runs never see newer lessons. `show cost` is an
aggregation over events. A partial index, `control_checks`, moved the control-history query
from a COLLSCAN examining 1,952 documents to an IXSCAN examining 0
(`reports/explain_control_checks.md`).

**What can the harness change about itself, and what can't it?**
It can change stage order, what each role receives, and which in-harness checks are
enabled: `/stages`, `/context_policy`, `/checks` (`reports/exp3.md`, "Charter", scope). It
cannot change the model, tools, charters, data, evaluator, specification, acceptance
rules, or the DMAIC code (`CLAUDE.md`).

**Did you, the operator, steer it?**
Four decisions, all disclosed. We used the optional cases D2, H2 and P2, published before
any code, because the only default development case, D1, has zero races at its venue.
We set budgets of 40 model calls, 60 tool calls and 900 s before the baseline. We chose
models per role. And after two cycles we added row_evidence to the catalog, off by
default, with one repair round; the harness chose to enable it (`docs/index.html#provenance`).

**How do you know the lessons aren't leaking the answer key?**
Only the evaluator reads the answer key, and its hash is confirmed each cycle
(`reports/exp3.md`, "Measurement checks"). Only development-case evidence enters define,
analyze, or any prompt the improvement agent sees; held-out and control results are used
only by code (`README.md`, "The improvement cycle").

**Is the memory doing anything if results didn't move?**
Retrieval works and lessons converged: three cycles, three verified lessons on the same
root cause (command 2). But the memory_only arm, v1 plus the new lessons, passed 11
development checks in all three cycles, the same as without them (`docs/index.html#learned`).
Memory alone did not fix E7; that is why the cycle proposes configuration changes.

**How much did it cost and how long did it take?**
In exp3 the candidate arm took 1181.9 s wall-clock against 993.0 s for memory_only, within
the 1.5x limit (`reports/exp3.md`, "Acceptance rules"). Token counts by arm and model are in
"Cost per arm"; no prices were supplied, so no dollar figure is reported.

**What would you do next?**
The records already hold the evidence a next cycle would need: in v4's D2 run,
row_evidence passed at event 00037 and the run went to GO at 00038, yet E7 still failed.
Whether the harness can find and close that gap within its editable surfaces, without the
operator tightening the check by hand, is what a fourth cycle would test.
