# RL0–RL1 gates (design v1.1)

| Gate | Required evidence | Implementation/check |
|---|---|---|
| Offline validity | 2–8 real SWE-smith/Pygments pairs; reverse patch receipts; independent new private inputs; reference pass/broken fail | inventory + isolated oracle; synthetic tests are insufficient |
| Safe intake | Traversal, links, forbidden edits, oversized payloads, partial/malformed output, false PASS cannot succeed | patch and inert-payload tests |
| Runtime durability | Crash/restore, ambiguous dispatch/create/release, uncertain occupancy, handoff, cancellation, late settlement | ledger/recovery fixtures; deployment expiry remains separate |
| Learning correctness | Six proposal slots, exact common packets, restricted history, scoped aliasing, ranking | schedule/packing/selection fixtures |
| Live feasibility | Verified endpoint/runner prices, enforced expiry, durable storage/auth, eight real smoke tasks within $25 | live bindings; no mock substitution |
| Final integrity | Frozen sampling/analysis, protected routes/references/caches, complete denominators, reconciled obligations | projection/report tests + real campaign audit |
| Scientific adequacy | Outcome-blind task grouping and justified sampling; 88-task supply or reviewed prospective revision | explicit intake/protocol receipts |

A passing unit suite does not close deployment or scientific gates. No live automatic retry is authorized. Future RL2–RL6 stages require separate protocols/budgets.
