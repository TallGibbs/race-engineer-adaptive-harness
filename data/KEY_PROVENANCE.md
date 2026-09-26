# Answer key provenance

- Built: 2026-09-26
- Data library: fastf1 3.8.3
- Key file: `key.json`; its hash is in `key.sha256`, the SHA-256 of the canonical JSON form (sorted keys, compact separators, UTF-8).

## Sources

Each race was derived from two independent sources in the FastF1 Grand Prix race session:

- **Source A, track status:** `sc` is true if status 4 (safety car) appears, `vsc` if status 6 (virtual safety car) appears, and `red_flag` if status 5 (red flag) appears.
- **Source B, race-control messages:** the same three booleans, derived from the text of the race-control messages.

A race whose sources were empty would have been treated as a failed load and reported. No load failed.

## Venue rule

The venue of an event is the Location field of the FastF1 season schedule for that event. A venue brief counts only races whose schedule Location exactly equals the target's Location. Near-matches were reviewed by the operator and not counted.

## As-of rule

A race counts only if it is a Grand Prix race (not a sprint), held from 2018 on, with an event date before the as-of date, 2026-09-25.

## Source disagreements

None. Both sources agreed on all three booleans for every race in the key.

## Case changes

One optional case was removed before the key was frozen because its venue appears in the schedule under two different Location strings.

## Data stored

No timing data is stored in this repository. The key holds only the derived booleans, counts, and dispositions.
