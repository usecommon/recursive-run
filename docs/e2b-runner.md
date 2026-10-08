# E2B runner

`src/recursive_run/providers/e2b.py` replaces Modal as the worker provider, because the managed proxy does not carry gRPC. It uses no E2B SDK and has no implicit retries:

- **Control plane:** E2B REST (`api.e2b.app`). Calls create (`POST /v2/sandboxes`), get, list-by-metadata and kill.
- **Data plane:** the sandbox's envd service on port 49983. Files go over REST `/files`; commands use the Connect protocol (`process.Process/Start`, JSON envelopes over HTTP/1.1). Connect reports stream errors in the body, so it never needs HTTP trailers.

## Lease rules

| Situation | Behavior |
|---|---|
| Before create | Durable lease with absolute expiry and a priced maximum reserved in the `infrastructure` category. During the smoke trial that category is capped at $2. |
| Create | Lifetime = lease lifetime, `autoPause: false`, `allow_internet_access: false` plus `network.denyOut: ["0.0.0.0/0"]`, metadata `rl_lease`/`rl_attempt`. |
| Create rejected (400/401/403/404/429) | Definite non-creation: lease absent, $0 settled. |
| Create reply lost or 5xx | Lease stays UNKNOWN and keeps its worker slot. Never resent. `reconcile` finds the sandbox by `rl_lease` metadata, and two matches block. Absence is concluded only once the lease is unlisted *after* its expiry, and then the full reservation is settled. |
| Template larger than priced | Killed at once and the run blocked. |
| Release | Kill, then a separate GET must return 404. Otherwise RELEASE_PENDING, which still occupies a slot. |
| Charge | E2B has no per-sandbox billing receipt, so the priced cost of the observed lifetime is settled, never above the reservation. |
| Commands | Wall time and output are bounded. On timeout or overflow, SIGKILL is sent; the sandbox lifetime bounds anything left over. |

Scoring runs the same reviewed `workers.WORKER_CODE` as the offline Docker runner. It is uploaded to `/home/user/rl` with one inert request per process, outside the task tree. Expected outputs never enter the sandbox.

## Live verification (8 October 2026)

`uv run python scripts/e2b_live_probe.py` creates one 60-second sandbox under a private ledger capped at $0.01. The receipt is written to `.runtime/e2b-live-probe.json`.

| Check | Result |
|---|---|
| Command through the managed proxy | OK (`echo` plus Python 3.11.6 in the `base` template) |
| Egress blocked | With the deny settings, the TCP handshake to 1.1.1.1:80 is accepted but **0 bytes** come back. A control sandbox with internet allowed received 64 bytes from the same check. The first probe, with only `allow_internet_access: false`, showed the handshake succeeding, so the runner now also sends an explicit `denyOut`. |
| Provider-enforced expiry | Sandbox absent 61.9 s after a 60 s lifetime; the probe never sent a kill. |
| Leftovers | 0 running sandboxes afterwards. |
| Spend | 4 short sandboxes, ≤ $0.008 upper bound. |

## Still open before the smoke trial

- **Pinned template.** The probe used E2B's public `base` template. Tasks need our own template, built from a digest-pinned image with the repository's Python and packages, and identified by build ID (`RL_E2B_TEMPLATE`).
- **Coding bridge.** mini-swe-agent actions must be routed to `E2BRunner.run` through controller admission.
- **Recovery after restart** reconciles and releases; it does not resume. envd tokens are held in memory only.
