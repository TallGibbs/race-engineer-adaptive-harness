# Index for the control-history query

The evaluator's control step (`adaptive_harness/evaluate/control.py`, `_control_events`)
reads the recorded control outcomes of a pinned version each time it checks a run. This
note records the query plan on the project's Atlas cluster (MongoDB 8.0.32) before and
after the `control_checks` index was added. Both plans come from
`explain` with verbosity `executionStats`, run read-only against the live `events`
collection while another experiment was writing to it, so the collection grew between the
two captures. Only counts are shown, no document contents.

## The query

```js
db.events.find({
  "type": "check",
  "content.control": { "$exists": true },
  "content.version": "v1"
})
```

`v1` is the version pinned at the time of capture. No control outcome had been recorded
yet, so the query returns no documents in either plan; what changes is how much of the
collection the server reads to find that out.

## The index

Defined in `adaptive_harness/store/mongo.py` (`INDEXES["events"]`) and created by
`python -m adaptive_harness init-db`, which is idempotent and added only this index:

```js
db.events.createIndex(
  { "content.version": 1, "type": 1 },
  { name: "control_checks",
    partialFilterExpression: { "content.control": { "$exists": true } } }
)
```

Both key fields are equality predicates of the query. The partial filter is a predicate the
query carries verbatim, so the planner can use the index, and the index holds only control
outcome events (a handful per pinned version) instead of an entry for every one of the
thousands of message, model-call, and tool events.

## Before and after

| | before | after |
|---|---|---|
| indexes on `events` | `_id_`, `run_seq`, `run_type` | `_id_`, `run_seq`, `run_type`, `control_checks` |
| documents in `events` | 1,952 | 2,157 |
| winning plan | `COLLSCAN` | `FETCH` <- `IXSCAN (control_checks)` |
| rejected plans | 0 | 0 |
| documents returned | 0 | 0 |
| keys examined | 0 | 0 |
| documents examined | 1,952 | 0 |
| execution time | 2 ms | 1 ms |

Before the index, every control check scanned the whole collection, and the cost grows with
every run recorded. After it, the server reads only index entries for control outcomes of
the requested version: keys and documents examined equal the number of matching outcomes,
independent of how many other events exist. The time difference is small at this size; the
documents-examined count is the number that scales.
