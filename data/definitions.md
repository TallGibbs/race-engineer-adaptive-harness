# Definitions

These definitions fix the meaning of every field in `cases.json` and `key.json`.

## Race

A race is a Grand Prix race session (not a sprint) that started, including races that were red-flagged. Only races held from 2018 on and before the as-of date, 2026-09-25, count.

## Venue

The venue of an event is the Location field of the season schedule for that event. A venue brief counts the races whose schedule Location equals the target event's Location.

## Race flags

- `sc` is true when the safety car ran during the race, including a start behind the safety car. A formation lap before a standing start does not count.
- `vsc` is true when a virtual safety car was deployed during the race.
- `red_flag` is true when the race was suspended with a red flag.

## Counts

- `n_races`: the number of races counted for the venue.
- `n_sc`: the number of those races with `sc` true.
- `n_vsc`: the number of those races with `vsc` true.
- `n_any`: the number of those races with `sc` or `vsc` true.

## Rates

Each rate is k / n, where k is one of the counts above and n is `n_races`. Each rate has a Wilson score interval at 90 percent confidence (z = 1.6448536269514722). Rates and interval bounds are rounded to 3 decimals.

## Disposition

- The disposition is HOLD, with no rates, when `n_races` is 0. Otherwise it is GO.
- `thin_sample` is true when `n_races` is below 5.
