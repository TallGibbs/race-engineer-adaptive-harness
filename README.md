# Race Engineer Adaptive Harness

## What this is

Race Engineer Adaptive Harness is a race engineer agent inside a self-improving harness.
It was built for the hackathon's Recursive Harnessing problem statement.

The claim it tests, in one sentence: a harness that records its own runs can find the
root causes of its defects in those records and change its own process so that it passes
more checks on a fixed evaluation it cannot edit, without getting worse elsewhere.

The agent's job is a race engineer's:

- `venue_brief`: how often Grand Prix races at a venue have been interrupted by a safety
  car (SC) or a virtual safety car (VSC), with counts, rates, and intervals.
- `race_audit`: whether a given race had an SC, a VSC, or a red flag.

The harness is the contribution. The race engineer agent is the work it improves.

## How it works

### Roles and the discussion

Three role agents work each case in one bounded discussion:

- race engineer: frames the question and writes the final brief;
- statistician: assesses the question and computes rates and intervals;
- data engineer: assesses the question and gathers the race data with tools.

The discussion runs through a fixed list of stages (`configs/v1.json`): frame, two
independent assessments run in parallel and blind to each other, a challenge round in
which each role responds once to the other two, gather, estimate, brief, and a code-only
validation stage. Each case run has fixed budgets for model calls, tool calls, and
wall-clock time. The role charters (`examples/neutralization_brief/charters/`) state
general professional norms only.

Each role, and the improvement agent, runs on a model set in `.env`: `MODEL_NAME` is the
default, and `MODEL_NAME_RACE_ENGINEER`, `MODEL_NAME_STATISTICIAN`,
`MODEL_NAME_DATA_ENGINEER`, and `MODEL_NAME_IMPROVEMENT` override it per role. One
provider and one key serve every role. The assignment is fixed for a run and recorded
with it. No sampling parameters or token limits are set.

### What MongoDB Atlas records

Every message, tool call, tool result, check, and decision is written to MongoDB Atlas,
in six collections:

| collection | holds |
|---|---|
| `runs` | one document per case run: harness version, configuration hash, memory snapshot, model assignment, FastF1 version, status, totals, and the output |
| `events` | every model call, tool call, tool result, message, decision, check, and error of a run, in order, with content hashes and the context each model call received |
| `harness_versions` | each configuration, its parent, the changes and their reasons, its validation, its status, which one is pinned, and its control plan |
| `lessons` | root causes found in the analyze phase, with their why-chains, source events, and an embedding |
| `evaluations` | the evaluator's verdict on each check of each run, with the evaluator and answer-key hashes |
| `experiments` | one document per improvement cycle: cases, arms, each phase's artifact and tollgate verdict, the acceptance results, and the decision |

### The improvement cycle

The harness improves its own process with DMAIC (define, measure, analyze, improve,
control), the cycle used in Six Sigma quality work. `SPECIFICATION.md` fixes what is
measured: eight checks per case run (E1 schema, E2 venue, E3 coverage, E4 indicators,
E5 arithmetic, E6 disposition, E7 evidence, E8 budget), defects and opportunities, and
the acceptance and control rules. The harness may change its process but never the
specification, the evaluator, the cases, the answer key, or the cycle's own code.

An improvement agent writes the text in each phase through a fixed prompt. Every
tollgate is decided by code. The cycle stops at the first tollgate that does not pass,
and a stop is recorded as an outcome.

Cases come in three sets: development, held-out, and control. Only development-case
evidence enters the define and analyze phases or any prompt the improvement agent sees.
Held-out and control results are used only by code, to verify and to control.

`data/cases.json` defines three default cases (D1, H1, P1, venue briefs) and three
optional race-audit cases (D2, H2, P2), all published before any harness code. The first
experiment, exp1, uses all six. The optional cases were included because the only default
development case, D1, has zero races at its venue and so cannot exhibit citation defects;
they were fixed in advance and were used, not added. Every pilot arm covers the same six
cases.

- Define: code builds a charter from the baseline's development-case evaluations
  (defects, opportunities, defects per opportunity, scope, goal). The agent writes a
  problem statement. Tollgate: at least one development defect.
- Measure: code confirms the answer-key and evaluator hashes, re-scores every baseline
  run in a fresh evaluator process and requires identical verdicts, and records defects
  per opportunity by check and by case set, a Pareto table by check, and cost per run.
  Tollgate: the measurement is valid.
- Analyze: for each development defect the agent returns a root cause: a cause category,
  a why-chain of one to five statements, the event where the defect originated, and the
  source events. Code verifies each one against the recorded run. Verified root causes
  are stored as lessons. Tollgate: at least one verified root cause in a category the
  harness can change (stage order, what each role receives, or in-harness checks).
- Improve: the agent proposes up to three changes to the configuration, each citing a
  verified root cause, with a rationale, expected effect, and risks. Code validates the
  paths, bounds, change cap, and citations; a valid proposal becomes a candidate
  version. A fresh pilot then runs the current version and the candidate on the same
  cases with the same memory. Tollgate: the acceptance rules below.
- Control: the accepted version is pinned by its configuration hash, and a control plan
  is written from the pilot. An optional confirmation run checks it. Tollgate: the plan
  is armed and every confirmation case meets its threshold.

### Lessons and retrieval

Verified root causes are stored as lessons with an embedding, and stay in memory after
the cycle. Roles retrieve the most relevant verified lessons with Atlas Vector Search
at their first stage. Retrieval filters by memory snapshot, so runs on memory from
before the cycle (M0) never see lessons written by it (M1). Unverified root causes are
never retrieved.

### The comparison and the acceptance rules

The candidate is compared with a memory-only control group: the current version run
with the same new lessons (snapshot M1). The difference between the arms is then the
configuration change, not the lessons. The fixed evaluator
(`python -m adaptive_harness.evaluate`) scores both arms. The candidate is accepted only
if all five rules hold; otherwise the current version is kept and the reasons are
recorded.

- R0 validity: no run ends as a measurement-system failure after one rerun.
- R1 no regression: every check that passes on a control case under the current version
  also passes under the candidate.
- R2 improvement: the candidate passes strictly more development-case checks; with
  repeated runs, its worst run must beat the current version's best.
- R3 held-out: the candidate passes at least as many held-out checks.
- R4 time: the candidate's total wall-clock time is at most 1.5 times the current
  version's.

### Control plan and rollback

The control plan sets a threshold for each case: the number of checks the accepted
version passed in the pilot. The evaluator checks every later run of the pinned version
against it. A run that passes fewer checks is a signal; the case is rerun once, and if
the signal repeats, the previous version is pinned again, the candidate is marked rolled
back, and a new cycle opens using development-case evidence only. A run under a
different model assignment or FastF1 version is flagged as outside the plan, not
compared.

### Reports

- `reports/exp1.md`: the report for the first experiment, with the baseline, each
  phase's artifact and tollgate verdict, the acceptance results, and the decision.
- `reports/maps.md`: Mermaid maps of the v1 process, the cycle as it ran, and v1
  against v2, generated from the stored configurations and records, not drawn by hand;
  SVG and PNG exports in `reports/maps/`.
- `reports/exp2.md` and `reports/maps_exp2.md` (exports in `reports/maps_exp2/`): the
  second cycle, with v1 against v3.

## Experiments

- [exp1](reports/exp1.md): rejected; the cycle stopped at improve on R2 (candidate v2,
  enabling `venue_match`, passed 11 development checks, the same as v1). v1 stays pinned.
- [exp2](reports/exp2.md): rejected; the cycle stopped at improve on R2 (candidate v3,
  enabling `source_agreement`, proposed after seeing v2's outcome, passed 11 development
  checks, the same as v1). v1 stays pinned.
- [exp3](reports/exp3.md): rejected; the cycle stopped at improve on R1 and R2 (candidate
  v4, enabling the operator-added `row_evidence` check, passed 11 development checks,
  the same as v1, and lost E2 and E3 on control case P1). v1 stays pinned.

Maps: [exp1](reports/maps.md), [exp2](reports/maps_exp2.md), [exp3](reports/maps_exp3.md).

## How to run

Requirements: Python 3.11 or later, a MongoDB Atlas cluster (Vector Search is used for
lessons), and an API key for a model provider (Anthropic, or an OpenAI-compatible
endpoint).

```bash
python -m venv .venv
pip install -e .
cp .env.example .env
pytest
```

Fill in `.env` from `.env.example`: `MONGODB_URI`, `MONGODB_DB`, `MODEL_PROVIDER`,
`MODEL_NAME` and any per-role overrides, `MODEL_BASE_URL` if needed, `MODEL_API_KEY`,
and `FASTF1_CACHE_DIR` (a folder outside the repository). Never commit `.env`. Every
command below reads `.env` itself.

Plain `pytest` uses the in-memory fakes and skips the tests that need a live MongoDB
Atlas cluster or a FastF1 cache. To run those too, load `.env` into the test process
(this needs python-dotenv's command-line extra, `pip install "python-dotenv[cli]"`):

```bash
python -m dotenv run -- pytest
```

Create the collections and indexes and register v1 as the pinned baseline:

```bash
python -m adaptive_harness init-db
```

Run the baseline arm (memory snapshot M0) on the six cases, under an experiment id.
`--config` takes a file path, a stored version id (`v1`, `v2`), or `pinned`:

```bash
python -m adaptive_harness run --config configs/v1.json --snapshot M0 --arm baseline --experiment exp1 --cases D1,D2,H1,H2,P1,P2
```

Score the runs with the evaluator, which runs as its own process:

```bash
python -m adaptive_harness.evaluate --experiment exp1
```

Run the cycle one phase at a time:

```bash
python -m adaptive_harness define --experiment exp1
```

```bash
python -m adaptive_harness measure --experiment exp1
```

```bash
python -m adaptive_harness analyze --experiment exp1 --snapshot M1
```

```bash
python -m adaptive_harness improve --experiment exp1 --from v1 --snapshot M1
```

Then run the pilot arms fresh on memory snapshot M1 and the same six cases: the current
version as `memory_only` and the candidate as `candidate`:

```bash
python -m adaptive_harness run --config v1 --snapshot M1 --arm memory_only --experiment exp1 --cases D1,D2,H1,H2,P1,P2
```

```bash
python -m adaptive_harness run --config v2 --snapshot M1 --arm candidate --experiment exp1 --cases D1,D2,H1,H2,P1,P2
```

Score them and apply the acceptance rules (tollgate I):

```bash
python -m adaptive_harness.evaluate --experiment exp1
```

```bash
python -m adaptive_harness improve --verify --experiment exp1
```

If tollgate I passes, pin the candidate and arm its control plan; `--confirm` also runs
the confirmation arm (leave it out to arm the plan without one):

```bash
python -m adaptive_harness control --experiment exp1 --confirm
```

Or run all five phases, including the pilot on the experiment's cases, in one command.
It stops at the first tollgate that does not pass (`--confirm` adds the confirmation arm,
`--repeat` a repeat of the development cases in measure):

```bash
python -m adaptive_harness cycle --experiment exp1
```

Write the markdown report (`reports/exp1.md`). Operator notes in
`reports/exp1.notes.json` are placed in the matching section:

```bash
python -m adaptive_harness report --experiment exp1
```

Write the Mermaid maps (`reports/maps.md`) for two versions and the experiment. When
Node.js is installed, the maps are also exported as SVG and PNG into `reports/maps/`
through `npx -y @mermaid-js/mermaid-cli`; `--no-export` skips that:

```bash
python -m adaptive_harness maps --versions v1,v2 --experiment exp1
```

`python -m adaptive_harness --help` lists every command, and each command takes `--help`.

## Inspecting the records

`show` reads the MongoDB records directly and prints them compactly (`--width` sets the
line width):

| command | prints |
|---|---|
| `python -m adaptive_harness show run <run_id>` | a run's events in order |
| `python -m adaptive_harness show experiment <experiment_id>` | phases and tollgates, decision, acceptance, versions, runs per arm |
| `python -m adaptive_harness show version <version_id>` | config hash, status, pinned, parent, each change |
| `python -m adaptive_harness show lesson <lesson_id>` | one root cause: categories, why-chain, lesson text |
| `python -m adaptive_harness show lessons --query "<text>" [--snapshot M1]` | Atlas Vector Search over the lessons |
| `python -m adaptive_harness show cost --experiment <experiment_id>` | model calls and tokens by arm, role, and model (no prices) |

[reports/explain_control_checks.md](reports/explain_control_checks.md) records the query
plan of the evaluator's control-history query before and after the `control_checks`
index.

## Data

Race data is fetched at runtime with the open-source FastF1 library into each user's own
cache (`FASTF1_CACHE_DIR`). No timing data is stored in this repository. `data/` holds
only the evaluation cases (`cases.json`), the definitions (`definitions.md`), and the
answer key (`key.json`, its hash in `key.sha256`, and how it was built in
`KEY_PROVENANCE.md`). Only the evaluator reads the answer key.

Retrieval may fail from cloud or CI machines; run it from a home or office connection.

This project is unofficial and is not associated with the Formula 1 companies.

## Provenance

1. The commit of `data/` was the first of the event, made before any harness code
   existed, so the fixed evaluation was public first.
2. `SPECIFICATION.md` came next, also before any harness code.
3. The shared contract (`CONTRACT.md`) followed, and the harness was then built in
   parallel lanes against it.
4. All code was written on 2026-09-26, with AI coding assistants.
5. The design notes and the instructions given to the coding assistants were written
   before the event, with small adjustments made on the morning of the event before
   hacking began, and coordination messages between the parallel build sessions during
   it; none are included.

## Limits

- Six fixed cases (two development, two held-out, two control; see
  `data/cases.json`), one run per case per arm, and no statistical claim.
- No sigma level or capability claim. The control plan uses specification thresholds
  instead of statistical control limits, which need about 20 runs.
- A hackathon prototype, not a production system.
