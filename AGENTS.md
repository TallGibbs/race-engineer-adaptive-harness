# Agent ground rules

Race Engineer Adaptive Harness. Read `CONTRACT.md` before writing code; it is the single
reference every lane builds against. `SPECIFICATION.md` fixes what is measured.

## Ground rules

- Stay inside your lane's folders. Do not edit `CONTRACT.md`, `adaptive_harness/contracts/`,
  `configs/`, `pyproject.toml`, `SPECIFICATION.md`, or `data/`. If the contract blocks
  you, stop and tell the operator.
- Only the evaluator reads `data/key.json`. No other code, prompt, context, or test
  fixture may read it or copy values from it. (The one sanctioned exception is the
  contract test that hashes it against `data/key.sha256`.)
- Do not write domain-specific hints about data pitfalls into role charters, prompts,
  tools, context, or code. The harness must learn such things from its own recorded runs.
  Role charters state general professional norms only.
- The harness may change its process, never its measurement or its improvement method:
  no proposal, lesson, or phase may change the evaluator, `data/`, `SPECIFICATION.md`,
  `configs/acceptance.json`, `configs/cycle.json`, or the DMAIC code.
- Only development-case evidence may enter the define and analyze phases or any prompt
  the improvement agent sees. Held-out and control results are used only by code, to
  verify and to control.
- Never commit secrets, `.env`, FastF1 cache files, database dumps, or absolute local
  paths.
- No Streamlit app, and no dashboard as the main feature. Outputs are the CLI, MongoDB
  records, a markdown report, and Mermaid maps.
- Tests use the fakes (`adaptive_harness.testing`) and synthetic fixtures; no network in
  unit tests.
- Commit small and often on your lane branch, with clear messages.

## Folder ownership

| Lane | Owns | Tests |
|---|---|---|
| store | `adaptive_harness/store/` | `tests/store/` |
| tools | `examples/neutralization_brief/tools.py` | `tests/tools/` |
| runner | `adaptive_harness/runner/`, `examples/neutralization_brief/charters/` | `tests/runner/` |
| evaluate | `adaptive_harness/evaluate/` | `tests/evaluate/` |
| dmaic | `adaptive_harness/dmaic/` | `tests/dmaic/` |
| report | `adaptive_harness/report/`, `reports/` | `tests/report/` |
| contract (frozen) | `CONTRACT.md`, `adaptive_harness/contracts/`, `adaptive_harness/testing/`, `adaptive_harness/__main__.py`, `configs/`, `pyproject.toml`, `examples/neutralization_brief/task.py`, `tests/contract/`, `AGENTS.md`, `CLAUDE.md`, `.env.example` | `tests/contract/` |
| operator only | `SPECIFICATION.md`, `data/` | |

A lane adds CLI subcommands by defining `<command>(argv) -> int` in
`adaptive_harness/<lane>/cli.py` (see CONTRACT.md, CLI).

## Setup

```
python -m venv .venv
pip install -e .
cp .env.example .env   # then fill in values; never commit .env
pytest
python -m adaptive_harness --help
```
