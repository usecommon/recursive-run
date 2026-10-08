# Pilot progress

This is implementation and offline validation evidence, not a completed pilot or evidence of learned improvement. Design v1.1 governs; the companion plan/pack were not supplied.

## Evidence obtained

- Locked Python 3.12 development environment and mini-swe-agent 2.4.6 execution extra.
- **49 tests passed, zero skipped** with the pinned offline Docker image: intake, inert output handling, complete scoring, state transitions, dispatch fencing, cancellation, reservation/settlement, worker occupancy/handoff, process crash, artifact restore, learning packets/aliases/ranking, sealed projections, frozen assignment membership, screening analysis and mini-swe-agent integration.
- Real isolated Docker probes: network disabled, no workspace/controller mount or host socket, timeouts/output floods cleaned up, false-PASS output rejected.
- Official SWE-smith mirror at `https://github.com/swesmith/pygments__pygments.27649ebb.git`: 1,430 mutation branches inventoried; 32 branches inspected, 16 provisional production candidates. These are not 1,430 eligible independent families.
- Eight real source mutations in distinct lexer files passed exact forward/reverse reconstruction, deterministic reference/broken checks and restored-code scoring. Sixteen newly authored private inputs remain in ignored controller storage. Dataset membership and forward bug-patch linkage are now verified against all 11 checksum-verified shards at revision 77cab9055d42ab4a5c25c89a8f937096db13558e. Origin/licensing receipts, relationship grouping and final acceptance remain unverified.
- One real-source fixture repair passed its visible example and failed both private checks. This validates detection of that overfit fixture, not all possible wrong repairs.
- Separate **synthetic mock campaign** completed the maximum 280 coding rollouts and six proposal slots, sealed all 120 final assignments, reconciled all mock obligations, and verified a coordinated database/artifact backup. Its fake charges/results are not live research costs or scientific outcomes.
- The local SQLite process-crash/restore tests passed on `/workspace` (overlayfs). Provider expiry, power-loss durability and durable deployment storage remain unverified.
- Three public benchmark sources are pinned with shard checksums and partition roles (`docs/benchmark-sources.md`): SWE-smith-py for development supply, SWE-rebench as a final-only fresh-holdout candidate (2,772 unique rows created after 2024-06-01) and SWE-bench Verified for external reference only. All shards passed checksum verification. No row from them is admitted.
- Family supply checkpoint (`docs/family-supply.md`): under a provisional outcome-blind grouping rule, pinned Pygments supports at most **82** independent families (73 including combined mutations) against the 88 required, before oracle-eligibility rejections. The default 8/24/16/40 protocol cannot be filled from Pygments alone; a protocol revision is required before curation. Effort receipts for the first eight families were not recorded, so the checkpoint itself remains BLOCKED.
- The managed proxy documentation lists gRPC / HTTP/2-only APIs as unsupported, so Modal's gRPC control plane cannot run from these cloud sessions regardless of credentials (see `docs/modal-transport-resolution.md`).
- Second repository: sqlglot is admitted as a task source alongside Pygments (`src/recursive_run/repositories.py`). It adds 28 independent families, for a structural supply of 110 against 88. A text-in/text-out `CallContract` grader runs reviewed sqlglot pipelines in the isolated worker (`docs/sqlglot-grader.md`). A non-isolated development probe separated reference from broken code on 4 of 4 real sqlglot mutations. Docker/live validation and intake receipts are pending.
- E2B runner (`docs/e2b-runner.md`): REST + envd adapter with durable leases, metadata reconciliation, verified-absence release and the $2 smoke infrastructure cap (13 mocked tests). Live probe passed: commands run through the managed proxy, egress carries no data (control with internet allowed did), and E2B removed the sandbox 61.9 s after a 60 s lifetime without a kill. Total live spend at most $0.008. A pinned template and the coding bridge remain before the smoke trial.
- **Live smoke completed** (`docs/smoke-report.md`): 8 sqlglot tasks accepted at intake in E2B. Baseline S verified 2/8. 3 attempts hit the 20-step limit and 2 ended on the one-strike format-error rule. One harness bug (missing oracle on a SUCCEEDED transition) interrupted an attempt; it was fixed and the task re-attempted. Total spend ≈ $0.13 of the $25 smoke authorization. Durable storage remains open, so smoke ran under a recorded smoke-only waiver.

## Step status

| Step | Status |
|---|---|
| 1 Implementation baseline | Complete for the supplied design; companion-pack reconciliation pending if supplied |
| 2 Dataset inventory | Pinned dataset acquired and checksum-verified: 1,430 Pygments rows; 708 structural candidates across 91 files. Oracle eligibility and independent-family supply remain unverified |
| 3 Two real source pairs | Executed successfully; dataset linkage verified; formal intake acceptance pending provenance/grouping review |
| 4 Private oracle | Implemented and exercised offline; per-task final OracleSpecs remain unfrozen |
| 5 Isolated preparation/scoring | Offline runner implemented; live runner and expiry binding pending |
| 6 Intake/oracle probes | Covered by tests and real overfit probe; affected deployment gate remains open |
| 7 Eight-family feasibility | Eight distinct-file mutations validated; family independence, effort receipts and 88-task supply not established |
| 8 Durable controller/evidence | Single-writer SQLite ledger, artifacts, frozen manifests and backup validation implemented; deployment certification pending |
| 9 Admission/accounting | Implemented and tested with synthetic obligations; live upper bounds/prices pending |
| 10 Provider/runner integration | Controlled interfaces and pinned mini-swe bridge tested with mocks; OpenAI snapshot adapter tested with a mock HTTP transport; Modal SDK 1.6.0 inspected. Credentials injected; OpenAI authentication verified; Modal gRPC transport and runner validation pending |
| 11 Recovery fixtures | Process-crash, ambiguous obligations, capacity/handoff, cancellation and late settlement tested; provider expiry still unverified |
| 12 Live bindings | OpenAI + Modal selected; $25 smoke-only authorized. OpenAI access verified; Modal transport, runner expiry/recovery and durable deployment remain unverified |
| 13 Live smoke | Completed 2026-10-08 on sqlglot (2/8 verified, ≈ $0.13); storage waiver recorded |
| 14 Full task curation/freeze | Unrun; waits for smoke and verified supply |
| 15 RL1 protocol/baseline freeze | Contracts implemented; actual experimental artifacts unfrozen |
| 16 Learning implementation | Packet/slot/alias/ranking mechanics implemented and tested; live campaign unrun |
| 17 Operator application | Deferred until live smoke passes, as required by the design |
| 18–19 Observations/learning | Unrun |
| 20 Final freeze | Unrun |
| 21 Final comparison | Unrun |
| 22 Reconcile/release | Mechanism tested with mocks; live results absent |
| 23 Screening report/handoff | Analysis code and mock report exist; scientific report absent |

## Current authorization and blockers

The user selected OpenAI + E2B (replacing Modal, whose gRPC control plane cannot pass the managed proxy) and authorized **up to $25 for smoke testing only, of which at most $2 for E2B sandboxes**. See `configs/smoke-authorization.json`. No learning or final evaluation spending is authorized. No paid API or worker execution has occurred.

Dataset access is restored. All 11 immutable SWE-smith-py shards passed their published LFS SHA-256 checksums. The dataset contains 50,908 rows, including 1,430 Pygments tasks. Eight offline mutations have verified dataset membership and matching forward bug patches. Structural screening found 708 candidate rows across 91 lexer files, rejecting 722 rows. These counts do not establish independent family supply or oracle adequacy.

Credential requirements saved in the environment draft:

- `RL_MODEL_API_KEY`: OpenAI project API key, scoped to `api.openai.com`. `OPENAI_API_KEY` is a platform-reserved secret-binding name; the adapter reads the custom variable explicitly.
- `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`: Modal account/workspace token pair, scoped to `api.modal.com` and `api.modal2.com`. Modal SDK 1.6.0 supports the inherited proxy and sends these values in gRPC request metadata. All three bindings are observed ready in runtime revision 7. OpenAI authentication and snapshot availability passed a read-only API check. Modal authentication remains unconfirmed because the proxied gRPC response lacks status trailers.

Values have been supplied securely. No replacement keys are currently requested.

The candidate coding endpoint is `gpt-4.1-mini-2025-04-14`, documented at https://developers.openai.com/api/docs/models/gpt-4.1-mini. Its published standard text rates are $0.40/M input, $0.10/M cached input and $1.60/M output. The no-retry adapter is tested with a mock HTTP transport; the actual account passed a read-only snapshot lookup. With a conservative 32,768-input/2,048-output ceiling it reserves $0.016384 per call; 20 calls fit the $0.50 rollout cap. Snapshot availability is verified; account-specific billing still requires verification.

Modal's sandbox pricing differs from its function pricing: https://modal.com/pricing currently lists $0.00003942 per physical CPU-core second and $0.00000667 per GiB-second for Sandboxes/Notebooks. Exact resource caps, startup/build/teardown/storage fees and billing reconciliation must be bound before admission. The SDK supports network-blocked sandboxes and a lifetime timeout, but the absolute-expiry/recovery contract has not been verified in live execution. No unverified retry behavior may be admitted.

Next: resolve the Modal gRPC proxy transport, bind the Modal runner and durable same-host controller storage/authentication, close the smoke task intake receipts, then run at most eight live smoke tasks under the $25 total stop. If expiry, storage or accounting requirements cannot be established, retain the blocker instead of weakening the design.

Existing cloud-instance offline compute and human effort have not been priced. Offline zero-cost placeholders and synthetic charges are not a complete research cost account.

## Retained evidence

Private specifications, ledger databases, worker diagnostics, receipts and mock reports live under `.runtime/` and must not be committed or exposed to learners. The source mirror and upstream tooling inspection checkouts are under `/workspace/scratch/`. Future final-task families must exclude the smoke/exposed entities after the complete relationship audit.

The user-facing application and scientific gates remain open. This environment has not been published or validated in a new task.

## Credential follow-up and transport diagnosis

OpenAI authenticated and returned the selected model snapshot without paid generation. Modal SDK 1.6.0 now includes its supported `api-proxy-support` dependency. A version-pinned adapter uses the system CA bundle, TLS verification and inherited proxy routing; the default SDK certifi bundle omitted the managed proxy CA.

A read-only Modal ClientHello request receives HTTP/2 200 and a valid protobuf response body. The observed HTTP/2 events are response headers, data and stream end, with no gRPC status trailers; the SDK call times out. This does not establish successful Modal authentication. The managed HTTPS proxy must preserve gRPC status trailers before this SDK path can be admitted. No direct-route, TLS-verification or accounting bypass is used. No model generations or Modal workers were launched.

The Modal transport safety tests and OpenAI adapter tests passed (5 tests). These validate local adapter behavior, not live sandbox readiness.

## Deeper Modal transport research

See [the resolution research](modal-transport-resolution.md) and `scripts/diagnose_modal_transport.py`. Both hosts fail the successful-RPC trailer path; early status-12 errors survive in headers. HTTP-version changes and gRPC-Web request switches failed. Official proxy support is already installed. Trailer loss is established at the client; its exact upstream location remains unproven. A platform trace/fix or validated controller migration is required; no exposed draft setting changes trailer handling. The read-only diagnostic exits 2 while this blocker persists.
