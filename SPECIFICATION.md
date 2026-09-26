# Quality specification

This file was committed at the start of the event, before any harness code existed. The
harness is measured against it and may never change it. A corrected specification is a
new version, committed with its reason, and every run is scored again.

## Purpose

The harness improves itself through a DMAIC cycle (define, measure, analyze, improve,
control). This file fixes what the cycle measures and what counts as an improvement.

## Critical-to-quality characteristics (CTQs)

Eight checks per case run:

- E1 schema: the output is valid against its schema.
- E2 venue: the resolved venue matches the key, and no race from another venue is
  counted.
- E3 coverage: the set of races counted equals the key's set.
- E4 indicators: sc, vsc, and red_flag match the key for every race, or for the audited
  race.
- E5 arithmetic: counts, rates, and intervals match an independent recomputation,
  within 0.001.
- E6 disposition: HOLD with no rates when no race qualifies, and thin_sample correct.
- E7 evidence: every race row cites recorded tool results from the same run about that
  race.
- E8 budget: the run stays within its budgets.

## Units, opportunities, and defects

- The unit is one case run.
- Each applicable check on a case run is one opportunity, and each failed check is one
  defect.
- For a race audit, E2, E3, E5, and E6 do not apply and are not opportunities.
- A data-fetch, model-API, or timeout failure is a HARNESS result: a failure of the
  measurement system, neither a defect nor an opportunity. The run is repeated once.

## What is reported

- Reported: counts of defects and opportunities, defects per opportunity (DPO), and a
  Pareto table of defects by check.
- Not reported: a sigma level or a capability index, which need far more opportunities
  than this experiment has.

## Acceptance rules for an improvement

The candidate configuration is compared with the current one, both with the same memory
snapshot, over the same cases. All five rules must hold.

- R0 validity: no run ends HARNESS after one rerun, judged from run metadata.
- R1 no regression: every check that passes on a control case under the current
  configuration also passes under the candidate.
- R2 improvement: the candidate passes strictly more development-case checks; with
  repeated runs, its worst run must beat the current configuration's best.
- R3 held-out: the candidate passes at least as many held-out checks.
- R4 time: the candidate's total wall-clock time is at most 1.5 times the current
  configuration's.

## Control rule

- An accepted configuration is pinned by the hash of its configuration.
- Its pilot results set a threshold for each case: the number of checks it passed.
- A later run of the pinned configuration on that case signals when it passes fewer.
- Reaction: rerun that case once; if the signal repeats, return to the previously
  accepted configuration and open a new cycle using development-case evidence only.
- A run under a different model assignment (any role's model) or FastF1 version is
  outside the plan and is flagged, not compared.
- These are specification thresholds, not statistical control limits, which need about
  20 runs.

## Evidence rule

Only development cases may inform the define and analyze phases. Held-out and control
cases are used only to verify and to control.
