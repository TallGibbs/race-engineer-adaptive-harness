# Contract

The single reference every lane builds against. It is frozen: no lane edits this file,
`adaptive_harness/contracts/`, `configs/`, `pyproject.toml`, `SPECIFICATION.md`, or
`data/`. If the contract blocks you, stop and tell the operator.

The code form of this contract is `adaptive_harness/contracts/` (pydantic models) and
the JSON Schemas exported from it in `adaptive_harness/contracts/schemas/`
(`python -m adaptive_harness.contracts.export`). Where this file and the models differ,
tell the operator; do not work around it.

`SPECIFICATION.md` fixes what is measured and what counts as an improvement. The rule
files `configs/acceptance.json` and `configs/cycle.json` restate it, and a contract test
checks that their statements appear in it. If they ever differ, the specification wins.

## The project in one paragraph

A race engineer agent briefs race interruptions: how often races at a venue have been
neutralized by a safety car (SC) or virtual safety car (VSC) (`venue_brief`), and whether
a given race had an SC, a VSC, or a red flag (`race_audit`). Three role agents (race
engineer, statistician, data engineer) hold one bounded discussion per case. MongoDB
Atlas records every message, tool call, result, and decision. The harness improves its
own process through a DMAIC cycle whose tollgates are decided by code, scored by a fixed
evaluator it cannot edit.

## Shared helpers (`adaptive_harness.contracts`)

- `canonical_json(obj)`: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False).encode("utf-8")`. Every hash in the project is
  `sha256(canonical_json(...))` (`canonical_sha256`).
- `load_config()`, `load_acceptance()`, `load_cycle()`; `REPO_ROOT`, `CONFIGS_DIR`,
  `DATA_DIR`.
- `HarnessConfig.config_hash()`: canonical SHA-256 of the whole configuration.
- `resolve_model(model_block, env)`, `stage_order_problems(stages, optional_catalog)`,
  `surface_of(path)`, `EDITABLE_PATHS`, `CHECK_BOUNDS`, `event_id(run_id, seq)`.
- Tasks: `examples.neutralization_brief.task.load_tasks(sets=None, only_default=False)`
  and `load_task(case_id)` return `Task {id, set, type, target {year, round}, as_of,
  question}` from `data/cases.json` only.

## A. Harness configuration (`configs/v1.json`, model `HarnessConfig`)

- `version_id` "v1", `parent_id` null, `task_family` "neutralization_brief".
- `model`: `provider`, `base_url`, `default`, `roles {race_engineer, statistician,
  data_engineer, improvement_agent}`. In v1 every value is an `"env:<VAR>"` reference:
  `MODEL_PROVIDER`, `MODEL_BASE_URL`, `MODEL_NAME` (the default), and
  `MODEL_NAME_RACE_ENGINEER`, `MODEL_NAME_STATISTICIAN`, `MODEL_NAME_DATA_ENGINEER`,
  `MODEL_NAME_IMPROVEMENT`. `resolve_model` maps every role to a concrete name: the role's
  variable when set and non-empty, else the default. The resolved assignment
  (`ResolvedModel {provider, base_url, assignment {role: model}}`) is what runs and control
  plans record. One provider and one key (`MODEL_API_KEY`, never stored) serve every role.
  No sampling parameters (temperature, top_p, top_k) and no token limits exist anywhere.
- `budgets` per case run: `max_model_calls` 40, `max_tool_calls` 60,
  `max_wall_seconds` 900. No token budget.
- `stages`: an ordered list of stage ids, v1 `["S1","S2a","S2b","S3","S4","S5","S6","S7"]`.
  Definitions live in the fixed `stage_catalog`:

  | id | name | roles | tools | notes |
  |---|---|---|---|---|
  | S1 | frame | race_engineer | | |
  | S2a | assess | statistician | | parallel group S2, blind to S2b |
  | S2b | assess | data_engineer | | parallel group S2, blind to S2a |
  | S3 | challenge | all three | | each role responds once to the other two |
  | S4 | gather | data_engineer | list_events, race_control_messages, track_status | |
  | S5 | estimate | statistician | interval | |
  | S6 | brief | race_engineer | | produces the output (section D) |
  | S7 | validate | (code) | | fixed, code only, `kind: "code"` |

  S7 repair round (operator decision, see section B): when any enabled catalog check
  (`venue_match`, `source_agreement`, `min_races_for_rate`, `row_evidence`) fails, the S6
  role gets the check reasons as a user message and replies once with a revised output,
  recorded as a new S6 message event with refs to the check events. S7 runs again on the
  revised output, which becomes the run's output; if checks still fail, the run is HOLD
  with the reasons. There is one round only, it counts against the run's budgets, and a
  schema failure never triggers it, so a configuration with no catalog check enabled runs
  S7 exactly as before.

  Consecutive stages sharing a `parallel_group` run in parallel; blindness comes from the
  context policy (neither sees `prior:` of the other).
- `optional_stage_catalog` (fixed): X1 identity_check (data_engineer, tools list_events,
  allowed before S1); X2 definitions (statistician, no tools, allowed before S4);
  X3 reconcile_sources (data_engineer, tools race_control_messages and track_status,
  allowed after S4). "Before S4" means anywhere earlier in the list.
- `context_policy`:
  - `roles[role][stage_id]`: ordered context items from the catalog `task`,
    `output_schema`, `tool_docs`, `prior:<stage id>`, `lessons`, `open_objections`.
    A stage with no entry for a role gets the runner default: `task`, `output_schema`,
    and `tool_docs` when the stage has tools. `output_schema` is the schema of that
    stage's reply (StageNote before S6, the section D output at S6).
  - v1: each role gets task and output_schema everywhere, tool_docs where it has tools,
    the prior outputs its stage needs, and `lessons` only at its first stage.
  - `lessons`: `k` 3, `filters {status: "verified", scope: "neutralization_brief"}`.
- `checks` (in-harness checks run by the runner, distinct from the evaluator):
  `schema {enabled: true, editable: false}`; `venue_match {enabled: false}`;
  `source_agreement {enabled: false, tolerance_laps: 1}`;
  `min_races_for_rate {enabled: false, value: 1}`.
  `row_evidence` (added to the catalog after v1, operator decision, see section B) is
  optional: absent means off, and it is omitted from the canonical serialization when
  absent, so every configuration stored before it keeps its hash. When enabled, S7
  checks that every race row cites at least one recorded tool_result of the same run
  whose arguments match that race (year and round); computed statistics (`interval`)
  do not count as race evidence.
- `change_cap` 3.

## B. Editable paths and bounds

The only things a proposal may change, as RFC 6902 JSON Patch operations. `surface_of`
maps a path to its surface (cause category) or None when fixed.

| path | surface | bounds |
|---|---|---|
| `/stages` | method | reorder, or insert X1 to X3 at their allowed positions. S1 to S7 may not be removed; S4 stays before S5; S6 then S7 stay last. |
| `/context_policy/...` | material | items from the catalog only; `lessons.k` 0 to 5; filters limited to `status` and `scope` |
| `/checks/venue_match` | measurement | enabled true or false |
| `/checks/source_agreement` | measurement | enabled; `tolerance_laps` 0, 1, or 2 |
| `/checks/min_races_for_rate` | measurement | enabled; `value` 1 to 10 |
| `/checks/row_evidence` | measurement | enabled true or false; absent means off (a JSON Patch `add` enables it) |

Operator decision (2026-09-26): the analyze phase, in two separate cycles, produced the
same verified lesson: before any GO, an in-harness check should confirm that every race
row in the brief cites at least one recorded tool_result from the same run whose
arguments match that race (year and round); computed statistics such as interval results
must not count as race evidence, and any row that fails should be sent back for repair or
set to HOLD. No check in the editable catalog did this, so the operator added
`row_evidence` to the catalog, off by default. The harness, not the operator, decides
whether to enable it, through the normal improve phase and acceptance rules. The
improvement agent's bounds text for `/checks` is generated from `CHECK_BOUNDS`.

Everything else is fixed: `model` (the default and every role), `budgets`, both stage
catalogs, `change_cap`, `/checks/schema`, tools, charters, the evaluator, the cases, and
the key. `HarnessConfig.model_validate` enforces the bounds and the stage-order rules;
the dmaic lane additionally enforces the path whitelist, `change_cap`, and the
root-cause citation rules (section J).

## C. MongoDB collections (database `MONGODB_DB`; models in `contracts/records.py`)

Every document's id is `_id`. Write with `Model(...).to_doc()`; read with
`Model.model_validate(doc)`. Timestamps are timezone-aware UTC datetimes.

- `runs` (`Run`): `_id` run_id, `harness_version`, `config_hash`, `memory_snapshot`
  (M0 or M1), `case_id`, `arm` (baseline, repeat, memory_only, candidate, confirmation),
  `experiment_id`, `model {provider, assignment {role: model}}`, `fastf1_version`,
  `started_at`, `ended_at`, `status` (completed, hold, budget_exceeded, harness_error;
  null while running), `totals {model_calls, tool_calls, input_tokens, output_tokens,
  cache_read_tokens, wall_seconds}`, `output` (the brief).
- `events` (`Event`): `_id` `"<run_id>:<seq 5 digits>"` (`event_id`), `run_id`, `seq`,
  `stage_id`, `role`, `type` (model_call, tool_call, tool_result, message, decision,
  check, error), `content`, `refs` (event ids), `context_manifest` (model_call only:
  `[{item, id, sha256, approx_tokens}]`), `usage`, `model` (model_call only: the model
  that answered), `stop_reason`, `content_sha256` (canonical SHA-256 of `content`),
  `created_at`. Unique index on (run_id, seq). Writes are idempotent: a duplicate key on
  retry counts as already written (`Store.append_event` returns False).
- `harness_versions` (`HarnessVersion`): `_id` version_id, `parent_id`, `config`,
  `config_hash`, `changes [{op (one JSON Patch op), lesson_id, why}]`, `rationale`,
  `expected_effect`, `risks` (what could get worse, and which acceptance rule would catch
  it), `validation {valid, reasons}`, `status` (baseline, candidate, accepted, rejected,
  rolled_back), `pinned` (exactly one version pinned at a time; v1 starts pinned),
  `control_plan {thresholds {case_id: checks passed in the pilot}, model (ResolvedModel),
  fastf1_version, reaction_plan, status (armed, in_control, signal, rolled_back),
  confirmation_run_ids, signals [{run_id, case_id, passed, threshold, repeated}]}`,
  `decision_reasons`, `created_at`.
- `lessons` (`Lesson`, the analyze phase's root causes): `_id`, `defect {run_id,
  case_id, check_id}`, `failure_category`, `cause_category` (method, material,
  measurement, machine, people, environment), `controllable` (true exactly for method,
  material, measurement), `why_chain` (1 to 5 statements), `origin_event_id`,
  `source_event_ids`, `text`, `status` (provisional, verified, rejected), `scope`,
  `snapshot`, `embedding`, `embedding_model`, `created_at`. Atlas Vector Search index
  `lessons_vector` on `embedding`, filter fields `scope`, `status`, `snapshot`.
- `evaluations` (`Evaluation`): `_id`, `run_id`, `case_id`, `arm`, `experiment_id`,
  `checks [{id E1..E8, result PASS|FAIL|HARNESS|NA, category}]`, `opportunities`
  (PASS + FAIL count), `defects` (FAIL count), `evaluator_sha256`, `key_sha256`,
  `created_at`.
- `experiments` (`Experiment`, one per improvement cycle): `_id`, `cases [{id, set}]`,
  `arms`, `dmaic {define, measure, analyze, improve, control: each {status (passed,
  stopped, not_reached), artifact, tollgate {passed, reasons}, completed_at}}`,
  `acceptance {R0..R4: {holds, detail}}`, `decision` (no_project, stopped, accepted,
  rejected), `candidate_version`, `report_path` (repo-relative), `created_at`.

Memory snapshots: M0 is memory before any cycle (no verified lessons); M1 is memory after
the analyze phase has written verified lessons (snapshot "M1"). Retrieval filters on
`snapshot` so a run on M0 never sees M1 lessons.

## D. Outputs (produced at S6, validated at S7; `contracts/outputs.py`)

Unknown fields are rejected. Field descriptions restate `data/definitions.md` (meanings
only, never how to detect them).

- `venue_brief` (`VenueBrief`): `case_id`; `venue {location, from_target {year, round},
  evidence [event ids]}`; `races [{year, round, location, event_name, sc, vsc, red_flag,
  evidence [event ids]}]`; `counts {n_races, n_sc, n_vsc, n_any}`; `rates {sc, vsc, any}`
  each `{value, interval [low, high], method "wilson_90"}` or null when n_races is 0;
  `disposition` GO or HOLD; `thin_sample`; `hold_reason`; `open_objections [text]`.
- `race_audit` (`RaceAudit`): `case_id`; `race {year, round, event_name, location}`;
  `sc`; `vsc`; `red_flag`; `evidence [event ids]`; `open_objections [text]`.

## E. Tools (`contracts.interfaces.Tool`)

A tool has `name`, `description`, `input_schema` (JSON Schema of args), and
`call(args, as_of) -> ToolResult {text (compact), payload (structured), sha256
(canonical SHA-256 of payload)}`; build with `ToolResult.build(text, payload)`.
Tool names: `list_events`, `race_control_messages`, `track_status`, `interval`.
Typed errors (`contracts.errors`): `AsOfViolation`, `DataUnavailable`, `FetchError`.
A `FetchError` marks the run HARNESS, never FAIL. The tools lane owns
`examples/neutralization_brief/tools.py`, which exposes `build_tools() -> dict[str, Tool]`.

## F. Model client (`contracts.interfaces.ModelClient`)

`complete(role, system, messages, json_schema=None) -> ModelResponse {text, parsed,
usage {input_tokens, output_tokens, cache_read_tokens}, stop_reason, latency_ms, model}`.
`messages` are `ChatMessage {role: user|assistant, content}` (or equivalent dicts). The
client resolves the role through the config's model block (`resolve_model`: role
override, else default) and records the model that answered. Timeouts; two retries on
rate limits, server errors, and network errors; a final failure raises
`ModelUnavailable`, which marks the run HARNESS. No sampling parameters, no token limits.
The runner lane owns the real client (`adaptive_harness/runner/`).

## G. Stage protocol

Every role reply is one JSON object (`parse_stage_reply`):

- tool stages: `{"action": "call_tool", "tool": ..., "args": {...}, "why": ...}` or
  `{"action": "finish", "output": {...}}`. The coordinator executes the tool, records a
  `tool_call` and a `tool_result` event, returns the result text with its event id to the
  role, and loops within budget.
- other stages: `{"action": "finish", "output": {...}}`.
- `output` is a `StageNote {summary, objections, data}` before S6 and the section D output
  at S6. S7 is code: it validates the S6 output and runs the enabled checks, with one
  repair round when a catalog check fails (section A, S7 repair round).

Run status: `completed` (GO output), `hold` (HOLD output), `budget_exceeded`,
`harness_error` (any `MeasurementSystemFailure`: `FetchError`, `ModelUnavailable`,
`RunTimeout`).

## H. Evaluator (`python -m adaptive_harness.evaluate`, lane evaluate)

The only code that reads `data/key.json`. Checks per case run: E1 schema; E2 venue
(location matches the key and no race from another venue); E3 coverage (the set of races
equals the key's); E4 indicators (sc, vsc, red_flag per race, or for an audit); E5
arithmetic (counts, rates, and intervals recomputed, within 0.001); E6 disposition (HOLD
with no rates when the key has no races; thin_sample correct); E7 evidence (every race row
cites recorded tool_result events of this run for that race); E8 budget. For a race
audit E2, E3, E5, E6 are NA. Each PASS or FAIL is one opportunity, each FAIL one defect;
HARNESS and NA are neither. Failure categories (the only evaluator output a proposal may
see): schema_invalid, venue_mismatch, coverage_mismatch, indicator_mismatch,
arithmetic_mismatch, disposition_wrong, evidence_missing, budget_exceeded. Each
evaluation records `evaluator_sha256` and `key_sha256` (the value in `data/key.sha256`).

## I. Acceptance rules (`configs/acceptance.json`, the improve tollgate)

Candidate (v2 on M1, arm candidate) against the memory-only arm (v1 on M1, arm
memory_only), same cases, same memory snapshot. Accept only if all five hold; otherwise
reject, keep v1, and record the reasons.

- R0 validity: no run ends HARNESS after one rerun, judged from run metadata.
- R1 no regression: every check that passes on a control case under v1 also passes under v2.
- R2 improvement: v2 passes strictly more development-case checks; with repeats, v2's
  worst run must beat v1's best.
- R3 held-out: v2 passes at least as many held-out checks.
- R4 time: v2's total wall-clock time is at most 1.5 times v1's.

## J. DMAIC cycle (`configs/cycle.json`; code in `adaptive_harness/dmaic/`)

The improvement agent is a fixed-prompt model call per phase on the `improvement_agent`
role's model; every tollgate is code. Each phase writes its artifact and tollgate verdict
into `experiments.dmaic.<phase>`. The cycle stops at the first tollgate that does not
pass; a stop is a recorded outcome, not an error.

- Define: from the baseline arm's development-case evaluations only, code builds the
  charter (CTQs with defects; baseline defects, opportunities, DPO; scope = the three
  editable surfaces; out of scope = model, tools, charters, data, evaluator,
  specification; goal copied from the acceptance rules). The agent writes a problem
  statement of at most three sentences. Tollgate D: at least one development defect,
  else stop "no project: the process meets its specification on the development cases".
- Measure: confirm key and evaluator hashes; re-score every baseline run in a fresh
  evaluator process with identical verdicts; no HARNESS after one rerun; record DPO by
  check and by case set, a Pareto table by check, cost per run. Optional repeat arm.
  Tollgate M: the measurement is valid.
- Analyze: per development defect the agent returns a root cause (defect, cause
  category, why-chain of 1 to 5, origin event, source events, lesson text). Surfaces:
  method -> `/stages`, material -> `/context_policy`, measurement -> `/checks`; machine,
  people, environment are fixed. Code verifies (events exist in that run, origin among
  them, the defect is a real FAIL in that run, category on the list). Verified causes
  become lessons (status verified, snapshot M1); the rest stay provisional and are never
  retrieved. Tollgate A: at least one verified controllable root cause, else stop "no
  controllable root cause".
- Improve: the agent gets the current config, section B, the charter, and the verified
  controllable root causes with source-event excerpts; returns changes (each citing its
  root cause), rationale, expected effect, risks. Code validates paths and bounds,
  `change_cap`, citation of a verified controllable root cause whose category matches the
  surface, and the schema. A valid proposal becomes candidate v2. Pilot: v1 on M1 (arm
  memory_only) and v2 on M1 (arm candidate), fresh, same cases. Tollgate I: section I,
  applied by the evaluator process.
- Control: pin v2 (`Store.pin_version`), write its control plan from the specification's
  control rule, optionally run a confirmation arm. The evaluator checks every later run
  of a pinned version against its plan; a run under a different model assignment or
  FastF1 version is flagged, not compared. On a signal rerun the case once; if it
  repeats, pin the parent, mark v2 rolled_back, and open a new cycle whose define phase
  sees development-case evidence only. Tollgate C: plan armed and every confirmation case
  meets its threshold.
- Verified root causes stay in memory as lessons learned.

Evidence rule: only development cases may inform define, analyze, and any prompt the
improvement agent sees. Held-out and control results are used only by code.

## Interfaces and test doubles

- `contracts.interfaces.Store`: `init`, `insert`, `get`, `find(filter, sort, limit)`,
  `update(set_fields)`, `append_event` (idempotent), `next_seq`, `pin_version`,
  `pinned_version`, `vector_search_lessons(vector, k, filters)`. The store lane
  implements it on MongoDB (`adaptive_harness/store/`), reading `MONGODB_URI` and
  `MONGODB_DB`.
- `adaptive_harness.testing`: `FakeStore` (in memory, same interface), `FakeModel`
  (scripted responses in order), `FakeTool` / `FakeTools` (canned tool results). All
  unit tests use these; no network in unit tests.

## CLI

`python -m adaptive_harness <command> [args]`. Commands and owners: `init-db` (store),
`run` (runner), `define`, `measure`, `analyze`, `improve`, `control`, `cycle` (dmaic),
`report`, `maps`, `show` (report). A lane implements a command by defining
`<command>(argv: list[str]) -> int` (hyphens to underscores, e.g. `init_db`) in
`adaptive_harness/<lane>/cli.py` and parsing its own arguments; `__main__.py` dispatches
to it and never needs editing. The evaluator is its own module:
`python -m adaptive_harness.evaluate` (lane evaluate owns `adaptive_harness/evaluate/`).

## Environment (`.env.example`)

`MONGODB_URI`, `MONGODB_DB=adaptive_harness`, `MODEL_PROVIDER`, `MODEL_NAME`,
`MODEL_NAME_RACE_ENGINEER`, `MODEL_NAME_STATISTICIAN`, `MODEL_NAME_DATA_ENGINEER`,
`MODEL_NAME_IMPROVEMENT`, `MODEL_BASE_URL`, `MODEL_API_KEY`, `FASTF1_CACHE_DIR`,
`FASTF1_OFFLINE=0`. Load with python-dotenv; never commit `.env`.
