"""Outputs produced at S6 and validated at S7 (CONTRACT.md section D).

Descriptions restate the meanings in data/definitions.md. They say what a field means,
never how to detect it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import Strict

EventIds = list[str]

_RACE = (
    "A Grand Prix race session (not a sprint) that started, including races that were "
    "red-flagged, held from 2018 on and before the as-of date."
)
_SC = (
    "True when the safety car ran during the race, including a start behind the safety car. "
    "A formation lap before a standing start does not count."
)
_VSC = "True when a virtual safety car was deployed during the race."
_RED = "True when the race was suspended with a red flag."
_EVIDENCE = "Ids of recorded tool_result events of this run that concern this race."


class TargetRef(Strict):
    year: int
    round: int


class Venue(Strict):
    location: str = Field(description="The Location field of the season schedule for the target event.")
    from_target: TargetRef = Field(description="The target event whose Location defines the venue.")
    evidence: EventIds = Field(description="Ids of recorded tool_result events of this run that establish the venue.")


class RaceRow(Strict):
    year: int
    round: int
    location: str = Field(description="The Location field of the season schedule for this event.")
    event_name: str
    sc: bool = Field(description=_SC)
    vsc: bool = Field(description=_VSC)
    red_flag: bool = Field(description=_RED)
    evidence: EventIds = Field(description=_EVIDENCE)


class Counts(Strict):
    n_races: int = Field(ge=0, description="The number of races counted for the venue.")
    n_sc: int = Field(ge=0, description="The number of those races with sc true.")
    n_vsc: int = Field(ge=0, description="The number of those races with vsc true.")
    n_any: int = Field(ge=0, description="The number of those races with sc or vsc true.")


class Rate(Strict):
    value: float = Field(description="k / n_races, rounded to 3 decimals.")
    interval: tuple[float, float] = Field(
        description="Wilson score interval [low, high] at 90 percent confidence "
        "(z = 1.6448536269514722), bounds rounded to 3 decimals."
    )
    method: Literal["wilson_90"] = "wilson_90"


class Rates(Strict):
    sc: Rate | None = Field(description="Rate of n_sc over n_races; null when n_races is 0.")
    vsc: Rate | None = Field(description="Rate of n_vsc over n_races; null when n_races is 0.")
    any: Rate | None = Field(description="Rate of n_any over n_races; null when n_races is 0.")


class VenueBrief(Strict):
    """Answer to a venue_brief case."""

    case_id: str
    venue: Venue
    races: list[RaceRow] = Field(
        description="Every race at the venue: " + _RACE + " A race counts when its schedule "
        "Location equals the target event's Location."
    )
    counts: Counts
    rates: Rates
    disposition: Literal["GO", "HOLD"] = Field(description="HOLD, with no rates, when n_races is 0. Otherwise GO.")
    thin_sample: bool = Field(description="True when n_races is below 5.")
    hold_reason: str | None = Field(default=None, description="Why the disposition is HOLD; null for GO.")
    open_objections: list[str] = Field(default_factory=list, description="Objections raised and not resolved.")


class AuditRace(Strict):
    year: int
    round: int
    event_name: str
    location: str = Field(description="The Location field of the season schedule for this event.")


class RaceAudit(Strict):
    """Answer to a race_audit case."""

    case_id: str
    race: AuditRace = Field(description="The target race: " + _RACE)
    sc: bool = Field(description=_SC)
    vsc: bool = Field(description=_VSC)
    red_flag: bool = Field(description=_RED)
    evidence: EventIds = Field(description=_EVIDENCE)
    open_objections: list[str] = Field(default_factory=list, description="Objections raised and not resolved.")


OUTPUT_MODELS = {"venue_brief": VenueBrief, "race_audit": RaceAudit}
