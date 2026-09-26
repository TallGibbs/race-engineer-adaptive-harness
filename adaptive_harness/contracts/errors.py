"""Typed errors shared by all lanes."""

from __future__ import annotations


class HarnessError(Exception):
    """Base class. Errors that mark a run HARNESS subclass MeasurementSystemFailure."""


class MeasurementSystemFailure(HarnessError):
    """A failure of the measurement system: the run is HARNESS, never FAIL."""


# ---------------------------------------------------------------- tools (E)


class ToolError(HarnessError):
    pass


class AsOfViolation(ToolError):
    """The call asked for data dated on or after the task's as-of date."""


class DataUnavailable(ToolError):
    """The data does not exist (for example no such event). A normal, reportable result."""


class FetchError(ToolError, MeasurementSystemFailure):
    """The data could not be fetched. Marks the run HARNESS."""


# ---------------------------------------------------------------- model (F)


class ModelUnavailable(MeasurementSystemFailure):
    """The model API failed after retries. Marks the run HARNESS."""


class RunTimeout(MeasurementSystemFailure):
    """The run exceeded a timeout outside its budget accounting. Marks the run HARNESS."""


# ---------------------------------------------------------------- runner / store


class BudgetExceeded(HarnessError):
    """A per-run budget was exhausted. The run ends with status budget_exceeded (E8 FAIL)."""


class DuplicateKey(HarnessError):
    """An insert hit an existing _id (or (run_id, seq) for events)."""
