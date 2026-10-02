# Reproduce current development checks

Use the existing `/workspace/recursive-run` checkout. Cloud tasks are isolated; do not create a worktree unless explicitly requested.

```sh
export UV_CACHE_DIR=/workspace/.cache/uv
uv sync --frozen --extra execution --extra cloud
uv run --extra execution --extra cloud ruff check .
uv run --extra execution --extra cloud pytest
```

For isolated Docker probes, first verify the managed daemon, then pull the pinned upstream image with TLS/checksum verification intact:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock info
docker --host=unix:///var/run/docker.sock pull ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58
RL_OFFLINE_IMAGE=ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 uv run --extra execution --extra cloud pytest
```

The test suite without `RL_OFFLINE_IMAGE` explicitly skips four Docker tests. `uv run recursive-run validate configs/mock.json` validates the offline schema; `--operation smoke` must exit 2 until live bindings are supplied. Do not convert mock configurations into live authorization by filling arbitrary strings.

A mock campaign requires a fresh directory. It creates synthetic artifacts/results only:

```sh
uv run --extra execution --extra cloud python scripts/mock_campaign.py --output .runtime/mock-campaign-NEW
```

To reproduce the existing real-source offline checks with retained private specifications:

```sh
uv run --extra execution --extra cloud python scripts/validate_offline_pairs.py --source /workspace/scratch/pygments-mirror --inputs .runtime/offline-intake-inputs.json --cases 8 --output .runtime/offline-validation-NEW
uv run --extra execution --extra cloud python scripts/validate_overfit_probe.py --source /workspace/scratch/pygments-mirror --inputs .runtime/offline-intake-inputs.json --output .runtime/overfit-validation-NEW
```

These commands use fresh disposable workers without networking, sockets or host mounts. They do not run a live coding model and do not certify provider-enforced expiry. All private inputs/outputs and raw task evidence stay outside learner snapshots. The source snapshot includes only regular Pygments package files plus LICENSE/AUTHORS. Archive intake is unsupported; patches are validated before reconstruction inside workers.

The mini-swe-agent bridge uses only controller model/environment ports. Never replace it with upstream LocalEnvironment or vendor models, which would expose controller execution or provider routes outside admission. Do not enable SDK retries for ambiguous operations.

SQLite is single-writer, DELETE journal, FULL sync. The writer lock and process-crash fixtures do not certify a generic cloud volume's durability. Deployment requires a verified same-host durable filesystem. Backup database and registered artifacts together and run `Ledger.verify_backup` before considering a restore usable.
