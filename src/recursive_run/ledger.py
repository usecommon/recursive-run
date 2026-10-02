"""Single-writer durable state; outcome and obligation settlement remain independent.

DELETE journal + FULL sync are pinned. This does not certify the deployment filesystem.
Only one process may open a writable Ledger; crashes release the OS writer lock.
"""

import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from recursive_run.contracts import AdmittedAction, State, TRANSITIONS, OracleResult


class Blocked(RuntimeError):
    pass


class Ledger:
    def __init__(
        self,
        path: Path,
        cap: int = 200_000_000,
        final_protected: int = 0,
        category_caps: dict[str, int] | None = None,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.writer_lock = open(str(path) + ".writer", "a+b")
        try:
            fcntl.flock(self.writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.writer_lock.close()
            raise Blocked("another controller writer is active") from error
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(
          id TEXT PRIMARY KEY, task TEXT NOT NULL, arm TEXT NOT NULL, phase TEXT NOT NULL,
          state TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
          generation INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL,
          steps INTEGER NOT NULL DEFAULT 0, policy TEXT NOT NULL, experiment TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(
          key TEXT PRIMARY KEY, kind TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          sequence INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1,
          attempt TEXT, kind TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS obligations(
          id TEXT PRIMARY KEY, attempt TEXT REFERENCES attempts(id), category TEXT NOT NULL,
          maximum INTEGER NOT NULL CHECK(maximum >= 0), actual INTEGER,
          status TEXT NOT NULL, operation_id TEXT);
        CREATE TABLE IF NOT EXISTS actions(
          key TEXT PRIMARY KEY, attempt TEXT NOT NULL REFERENCES attempts(id),
          envelope TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS leases(
          id TEXT PRIMARY KEY, attempt TEXT NOT NULL REFERENCES attempts(id),
          purpose TEXT NOT NULL, generation INTEGER NOT NULL, epoch INTEGER NOT NULL,
          expiry REAL NOT NULL, image TEXT NOT NULL, status TEXT NOT NULL,
          provider_id TEXT, obligation TEXT NOT NULL REFERENCES obligations(id));
        CREATE TABLE IF NOT EXISTS oracle_results(
          attempt TEXT PRIMARY KEY REFERENCES attempts(id), result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifact_refs(
          hash TEXT PRIMARY KEY, visibility TEXT NOT NULL, attempt TEXT REFERENCES attempts(id));
        CREATE TABLE IF NOT EXISTS artifact_occurrences(
          id INTEGER PRIMARY KEY, hash TEXT NOT NULL REFERENCES artifact_refs(hash),
          attempt TEXT REFERENCES attempts(id));
        """)
        self.cap = cap
        self.final_protected = final_protected
        self.category_caps = category_caps or {
            "coding_model": 140_000_000,
            "proposal": 12_000_000,
            "infrastructure": 20_000_000,
        }
        with self.transaction():
            frozen = self._json(
                {
                    "cap": cap,
                    "final_protected": final_protected,
                    "category_caps": self.category_caps,
                }
            )
            existing = self.db.execute("SELECT value FROM settings WHERE key='budget'").fetchone()
            if existing and existing[0] != frozen:
                raise Blocked("budget policy differs from durable policy")
            self.db.execute("INSERT OR IGNORE INTO settings VALUES('budget', ?)", (frozen,))

    def close(self):
        self.db.close()
        self.writer_lock.close()

    @staticmethod
    def _json(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _event(self, attempt, kind, body):
        self.db.execute(
            "INSERT INTO events(attempt,kind,body,created) VALUES(?,?,?,?)",
            (attempt, kind, self._json(body), time.time()),
        )

    def _receipt(self, key, kind, request):
        row = self.db.execute("SELECT * FROM receipts WHERE key=?", (key,)).fetchone()
        if row:
            if row["kind"] != kind or row["request"] != self._json(request):
                raise Blocked("idempotency key reused with different request")
            return json.loads(row["result"])
        return None

    def _save_receipt(self, key, kind, request, result):
        self.db.execute(
            "INSERT INTO receipts VALUES(?,?,?,?)",
            (key, kind, self._json(request), self._json(result)),
        )
        return result

    def attempt(self, attempt_id):
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            raise Blocked("unknown attempt")
        return dict(row)

    def create_attempt(self, attempt_id, task, arm, phase, deadline, policy, experiment):
        if arm not in {"S", "B", "R"} or phase not in {"smoke", "discovery", "selection", "final"}:
            raise ValueError("invalid arm/phase")
        with self.transaction():
            if self.db.execute(
                "SELECT 1 FROM settings WHERE key=?", ("cancelled:" + experiment,)
            ).fetchone():
                raise Blocked("experiment cancelled; no new attempts")
            self.db.execute(
                """INSERT INTO attempts(id,task,arm,phase,state,deadline,policy,experiment)
                               VALUES(?,?,?,?,'QUEUED',?,?,?)""",
                (attempt_id, task, arm, phase, deadline, policy, experiment),
            )
            self._event(attempt_id, "created", {"task": task, "arm": arm, "phase": phase})

    def transition(
        self, attempt_id, target: State, receipt_key, oracle: OracleResult | None = None
    ):
        request = {
            "attempt": attempt_id,
            "target": target.value,
            "oracle": oracle.model_dump(mode="json") if oracle else None,
        }
        with self.transaction():
            existing = self._receipt(receipt_key, "transition", request)
            if existing is not None:
                return existing
            row = self.attempt(attempt_id)
            if target not in TRANSITIONS.get(State(row["state"]), set()):
                raise Blocked("illegal outcome transition")
            if target == State.SUCCEEDED:
                if oracle is None or not oracle.success:
                    raise Blocked("complete trusted scoring required")
            if oracle:
                self.db.execute(
                    "INSERT INTO oracle_results VALUES(?,?)", (attempt_id, oracle.model_dump_json())
                )
            self.db.execute("UPDATE attempts SET state=? WHERE id=?", (target.value, attempt_id))
            self._event(attempt_id, "transition", request)
            return self._save_receipt(receipt_key, "transition", request, {"state": target.value})

    def cancel(self, attempt_id, receipt_key):
        request = {"attempt": attempt_id}
        with self.transaction():
            existing = self._receipt(receipt_key, "cancel", request)
            if existing is not None:
                return existing
            row = self.attempt(attempt_id)
            epoch = row["epoch"] + 1
            target = (
                State.CANCELLED.value
                if State.CANCELLED in TRANSITIONS.get(State(row["state"]), set())
                else row["state"]
            )
            self.db.execute(
                "UPDATE attempts SET epoch=?,state=? WHERE id=?", (epoch, target, attempt_id)
            )
            self._event(attempt_id, "cancel", {"epoch": epoch})
            return self._save_receipt(receipt_key, "cancel", request, {"epoch": epoch})

    def cancel_experiment(self, experiment_id, receipt_key):
        request = {"experiment": experiment_id}
        with self.transaction():
            prior = self._receipt(receipt_key, "experiment_cancel", request)
            if prior is not None:
                return prior
            row = self.db.execute(
                "SELECT value FROM settings WHERE key=?", ("cancelled:" + experiment_id,)
            ).fetchone()
            epoch = int(row[0]) + 1 if row else 1
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                ("cancelled:" + experiment_id, str(epoch)),
            )
            rows = self.db.execute(
                "SELECT * FROM attempts WHERE experiment=?", (experiment_id,)
            ).fetchall()
            for attempt in rows:
                target = (
                    State.CANCELLED.value
                    if State.CANCELLED in TRANSITIONS.get(State(attempt["state"]), set())
                    else attempt["state"]
                )
                self.db.execute(
                    "UPDATE attempts SET epoch=epoch+1,state=? WHERE id=?", (target, attempt["id"])
                )
            self._event(None, "experiment_cancel", {"experiment": experiment_id, "epoch": epoch})
            return self._save_receipt(receipt_key, "experiment_cancel", request, {"epoch": epoch})

    def recover(self):
        """Fence interrupted work. Reconciliation never assumes an unknown worker is absent."""
        with self.transaction():
            rows = self.db.execute(
                "SELECT * FROM attempts WHERE state IN ('RUNNING','EVALUATING')"
            ).fetchall()
            for row in rows:
                self.db.execute(
                    "UPDATE attempts SET generation=generation+1,state='INTERRUPTED' WHERE id=?",
                    (row["id"],),
                )
                self._event(row["id"], "recovery_fence", {"generation": row["generation"] + 1})
        return self.unsettled()

    def money(self):
        rows = self.db.execute("SELECT * FROM obligations").fetchall()
        return {
            "settled": sum(r["actual"] for r in rows if r["actual"] is not None),
            "reserved": sum(r["maximum"] for r in rows if r["actual"] is None),
        }

    def _reserve(self, obligation_id, attempt_id, category, maximum):
        if self.db.execute("SELECT 1 FROM settings WHERE key='invalid_cost_bound'").fetchone():
            raise Blocked("cost upper bound violated; revise protocol before new admission")
        if type(maximum) is not int or maximum < 0 or category not in self.category_caps:
            raise ValueError("invalid maximum cost/category")
        row = self.attempt(attempt_id) if attempt_id is not None else {"phase": "learning"}
        money = self.money()
        protection = 0 if row["phase"] == "final" else self.final_protected
        if money["settled"] + money["reserved"] + maximum + protection > self.cap:
            raise Blocked("external cash or protected final allocation exhausted")
        used = self.db.execute(
            "SELECT COALESCE(SUM(COALESCE(actual,maximum)),0) FROM obligations WHERE category=?",
            (category,),
        ).fetchone()[0]
        if used + maximum > self.category_caps[category]:
            raise Blocked("category cap exhausted")
        if row["phase"] == "smoke":
            used = self.db.execute("""SELECT COALESCE(SUM(COALESCE(o.actual,o.maximum)),0)
              FROM obligations o JOIN attempts a ON a.id=o.attempt WHERE a.phase='smoke' """).fetchone()[
                0
            ]
            if used + maximum > 25_000_000:
                raise Blocked("smoke stop limit exhausted")
        if category == "coding_model":
            used = self.db.execute(
                """SELECT COALESCE(SUM(COALESCE(actual,maximum)),0)
              FROM obligations WHERE attempt=? AND category='coding_model' """,
                (attempt_id,),
            ).fetchone()[0]
            if used + maximum > 500_000:
                raise Blocked("rollout model ceiling exhausted")
        if category == "proposal" and maximum > 2_000_000:
            raise Blocked("proposal ceiling exceeded")
        self.db.execute(
            "INSERT INTO obligations VALUES(?,?,?,?,NULL,'RESERVED',NULL)",
            (obligation_id, attempt_id, category, maximum),
        )

    def admit(
        self,
        attempt_id,
        operation_key,
        action_digest,
        policy_hash,
        epoch,
        generation,
        kind,
        maximum_cost,
        now=None,
    ):
        now = time.time() if now is None else now
        with self.transaction():
            row = self.attempt(attempt_id)
            prior = self.db.execute(
                "SELECT envelope FROM actions WHERE key=?", (operation_key,)
            ).fetchone()
            if prior:
                # Returning a receipt grants no permission to dispatch it a second time.
                raise Blocked("operation already admitted; reconcile, do not redispatch")
            expected_state = "EVALUATING" if kind == "score" else "RUNNING"
            if (
                row["state"] != expected_state
                or row["deadline"] <= now
                or row["epoch"] != epoch
                or row["generation"] != generation
                or row["policy"] != policy_hash
            ):
                raise Blocked("stale, cancelled, expired or policy-mismatched action")
            if kind == "model" and row["steps"] >= 20:
                raise Blocked("model step ceiling exhausted")
            purpose = "scoring" if kind == "score" else "coding"
            lease = self.db.execute(
                "SELECT * FROM leases WHERE attempt=? AND purpose=? AND status='ACTIVE' AND generation=? AND epoch=? AND expiry>?",
                (attempt_id, purpose, generation, epoch, now),
            ).fetchone()
            if not lease:
                raise Blocked("current active worker lease required")
            obligation = "action:" + operation_key
            category = "coding_model" if kind == "model" else "infrastructure"
            self._reserve(obligation, attempt_id, category, maximum_cost)
            envelope = AdmittedAction(
                experiment_id=row["experiment"],
                attempt_id=attempt_id,
                operation_key=operation_key,
                action_digest=action_digest,
                policy_hash=policy_hash,
                cancellation_epoch=epoch,
                generation=generation,
                deadline=row["deadline"],
                reservation_id=obligation,
                lease_id=lease["id"],
                maximum_cost=maximum_cost,
                kind=kind,
            )
            self.db.execute(
                "INSERT INTO actions VALUES(?,?,?,'INTENT')",
                (operation_key, attempt_id, envelope.model_dump_json()),
            )
            if kind == "model":
                self.db.execute("UPDATE attempts SET steps=steps+1 WHERE id=?", (attempt_id,))
            self._event(attempt_id, "admitted", envelope.model_dump(mode="json"))
            return envelope

    def before_dispatch(self, envelope: AdmittedAction, now=None):
        """Consume a durable intent once. A crash after this point is an uncertain dispatch."""
        now = time.time() if now is None else now
        with self.transaction():
            row = self.attempt(envelope.attempt_id)
            action = self.db.execute(
                "SELECT * FROM actions WHERE key=?", (envelope.operation_key,)
            ).fetchone()
            lease = self.db.execute(
                "SELECT * FROM leases WHERE id=?", (envelope.lease_id,)
            ).fetchone()
            if not lease or lease["status"] != "ACTIVE" or lease["expiry"] <= now:
                raise Blocked("worker lease no longer active")
            expected = "EVALUATING" if envelope.kind == "score" else "RUNNING"
            if (
                not action
                or action["envelope"] != envelope.model_dump_json()
                or action["status"] != "INTENT"
                or row["state"] != expected
                or row["epoch"] != envelope.cancellation_epoch
                or row["generation"] != envelope.generation
                or now >= envelope.deadline
            ):
                raise Blocked("dispatch fenced or already consumed")
            self.db.execute(
                "UPDATE actions SET status='DISPATCHED' WHERE key=?", (envelope.operation_key,)
            )
            self._event(envelope.attempt_id, "dispatch", {"key": envelope.operation_key})

    def settle(self, obligation_id, actual, operation_id, receipt_key):
        if type(actual) is not int or actual < 0 or not operation_id:
            raise ValueError("invalid charge/operation receipt")
        request = {"obligation": obligation_id, "actual": actual, "operation_id": operation_id}
        with self.transaction():
            prior = self._receipt(receipt_key, "settlement", request)
            if prior is not None:
                return prior
            row = self.db.execute(
                "SELECT * FROM obligations WHERE id=?", (obligation_id,)
            ).fetchone()
            if not row or row["actual"] is not None:
                raise Blocked("unknown or already settled obligation")
            self.db.execute(
                "UPDATE obligations SET actual=?,status='SETTLED',operation_id=? WHERE id=?",
                (actual, operation_id, obligation_id),
            )
            self._event(
                row["attempt"], "settlement", {**request, "over_bound": actual > row["maximum"]}
            )
            # Over-bound receipts remain in cash accounting and block advancement, never discarded.
            if actual > row["maximum"]:
                self.db.execute(
                    "INSERT OR REPLACE INTO settings VALUES('invalid_cost_bound','true')"
                )
            return self._save_receipt(receipt_key, "settlement", request, {"settled": True})

    def occupied_workers(self):
        return self.db.execute("SELECT COUNT(*) FROM leases WHERE status != 'ABSENT'").fetchone()[0]

    def create_lease(self, lease_id, attempt_id, purpose, image, expiry, maximum_cost, now=None):
        now = time.time() if now is None else now
        if purpose not in {"preparation", "coding", "scoring"} or expiry <= now:
            raise ValueError("invalid purpose/expiry")
        with self.transaction():
            row = self.attempt(attempt_id)
            required = "EVALUATING" if purpose == "scoring" else "RUNNING"
            if row["state"] != required or now >= row["deadline"]:
                raise Blocked("lease admission fenced")
            if expiry > row["deadline"]:
                raise Blocked("lease expiry exceeds attempt deadline")
            if self.occupied_workers() >= 2:
                raise Blocked("two-worker capacity includes uncertain creation/release")
            if (
                purpose == "scoring"
                and self.db.execute(
                    "SELECT 1 FROM leases WHERE attempt=? AND purpose='coding' AND status!='ABSENT'",
                    (attempt_id,),
                ).fetchone()
            ):
                raise Blocked("release coding worker before scoring")
            obligation = "lease:" + lease_id
            self._reserve(obligation, attempt_id, "infrastructure", maximum_cost)
            self.db.execute(
                "INSERT INTO leases VALUES(?,?,?,?,?,?,?,'UNKNOWN',NULL,?)",
                (
                    lease_id,
                    attempt_id,
                    purpose,
                    row["generation"],
                    row["epoch"],
                    expiry,
                    image,
                    obligation,
                ),
            )
            self._event(attempt_id, "lease_intent", {"lease": lease_id, "expiry": expiry})

    def observe_lease(self, lease_id, status, provider_id, receipt_key):
        if status not in {"UNKNOWN", "ACTIVE", "RELEASE_PENDING", "ABSENT"}:
            raise ValueError("invalid provider observation")
        request = {"lease": lease_id, "status": status, "provider_id": provider_id}
        with self.transaction():
            prior = self._receipt(receipt_key, "lease_observation", request)
            if prior is not None:
                return prior
            row = self.db.execute("SELECT * FROM leases WHERE id=?", (lease_id,)).fetchone()
            if not row or row["status"] == "ABSENT":
                raise Blocked("unknown or already absent lease")
            if row["provider_id"] and provider_id != row["provider_id"]:
                raise Blocked("provider identity changed")
            self.db.execute(
                "UPDATE leases SET status=?,provider_id=? WHERE id=?",
                (status, provider_id, lease_id),
            )
            self._event(row["attempt"], "lease_observation", request)
            return self._save_receipt(receipt_key, "lease_observation", request, {"status": status})

    def unsettled(self):
        return {
            "leases": [
                dict(r) for r in self.db.execute("SELECT * FROM leases WHERE status!='ABSENT'")
            ],
            "obligations": [
                dict(r) for r in self.db.execute("SELECT * FROM obligations WHERE actual IS NULL")
            ],
        }

    def register_artifact(self, key, visibility, attempt=None):
        if visibility not in {"public", "development", "private", "final"}:
            raise ValueError("invalid projection")
        with self.transaction():
            existing = self.db.execute(
                "SELECT visibility FROM artifact_refs WHERE hash=?", (key,)
            ).fetchone()
            if existing and existing[0] != visibility:
                raise Blocked("artifact projection cannot be silently reclassified")
            self.db.execute(
                "INSERT OR IGNORE INTO artifact_refs VALUES(?,?,?)", (key, visibility, attempt)
            )
            self.db.execute(
                "INSERT INTO artifact_occurrences(hash,attempt) VALUES(?,?)", (key, attempt)
            )

    def backup(self, destination: Path, artifact_store):
        """Writer is quiescent here; caller must retain both files and DB as one snapshot."""
        if destination.exists():
            raise ValueError("backup destination exists")
        destination.mkdir()
        target = sqlite3.connect(destination / "ledger.sqlite")
        try:
            self.db.backup(target)
        finally:
            target.close()
        from recursive_run.artifacts import ArtifactStore

        copied = ArtifactStore(destination / "artifacts")
        for row in self.db.execute("SELECT hash FROM artifact_refs"):
            copied.put(artifact_store.get(row["hash"]))
        self.verify_backup(destination)

    @staticmethod
    def verify_backup(destination: Path):
        from recursive_run.artifacts import ArtifactStore

        with sqlite3.connect(f"file:{destination / 'ledger.sqlite'}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("corrupt database")
            store = ArtifactStore(destination / "artifacts")
            for row in db.execute("SELECT hash FROM artifact_refs"):
                store.get(row[0])
