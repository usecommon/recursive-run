# WIP Research
# Recursive Learning Harness Pilot

Implementation of design v1.1, scoped to RL0–RL1. A valid negative or inconclusive screen is a successful research deliverable. This repository does not claim learned improvement or live readiness.

## Development

Python 3.12 and uv are required. Use the existing checkout; cloud tasks are already isolated.

```sh
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen --extra execution --extra cloud
UV_CACHE_DIR=/workspace/.cache/uv uv run --extra execution --extra cloud pytest
UV_CACHE_DIR=/workspace/.cache/uv uv run --extra execution --extra cloud ruff check .
uv run recursive-run validate configs/mock.json
```

`configs/mock.json` is deliberately offline-only. Provider, runner, prices, expiry, dataset supply, durable filesystem and operator authentication must be verified before live admission. Never put credentials in a manifest or source file. T

The authoritative extracted design and source hash are in `docs/`. The companion plan/pack were not provided; implementation requirements are traced to design v1.1, and missing bindings remain explicit blockers.

See `docs/acceptance.md` and `docs/progress.md` for gates and actual evidence. Tests using synthetic fixtures do not qualify as real offline pairs or the live smoke.

For the full development validation including the four real Docker probes, see `docs/development.md`. Private offline evidence is retained under `.runtime/`; it is not committed.
