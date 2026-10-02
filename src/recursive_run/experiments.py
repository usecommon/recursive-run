"""Durable frozen manifest authority; hash/review receipts remain externally verifiable."""

import json
from recursive_run.artifacts import digest
from recursive_run.contracts import Manifest
from recursive_run.ledger import Blocked


class ExperimentStore:
    def __init__(self, ledger):
        self.ledger = ledger
        ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS experiments(
          id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
          manifest TEXT NOT NULL, hash TEXT NOT NULL,
          state TEXT NOT NULL, operation TEXT);
        """)

    def freeze(self, manifest: Manifest):
        serialized = manifest.model_dump_json()
        key = digest(serialized.encode())
        with self.ledger.transaction():
            self.ledger.db.execute(
                "INSERT INTO experiments VALUES(?,?,?, ?,'FROZEN',NULL)",
                (manifest.experiment_id, manifest.schema_version, serialized, key),
            )
            self.ledger._event(
                None, "manifest_frozen", {"experiment": manifest.experiment_id, "hash": key}
            )
        return key

    def get(self, experiment_id):
        row = self.ledger.db.execute(
            "SELECT * FROM experiments WHERE id=?", (experiment_id,)
        ).fetchone()
        if not row:
            raise Blocked("unknown experiment")
        if digest(row["manifest"].encode()) != row["hash"]:
            raise Blocked("corrupt frozen manifest")
        return Manifest.model_validate_json(row["manifest"])

    def start(self, experiment_id, operation, receipt_key):
        request = {"experiment": experiment_id, "operation": operation}
        with self.ledger.transaction():
            prior = self.ledger._receipt(receipt_key, "experiment_start", request)
            if prior is not None:
                return prior
            manifest = self.get(experiment_id)
            if self.ledger.db.execute(
                "SELECT 1 FROM settings WHERE key=?", ("cancelled:" + experiment_id,)
            ).fetchone():
                raise Blocked("experiment cancelled")
            blockers = manifest.blockers(operation)
            if blockers:
                raise Blocked("; ".join(blockers))
            row = self.ledger.db.execute(
                "SELECT state FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            if row[0] != "FROZEN":
                raise Blocked("experiment already started/cancelled")
            self.ledger.db.execute(
                "UPDATE experiments SET state='STARTED',operation=? WHERE id=?",
                (operation, experiment_id),
            )
            self.ledger._event(None, "experiment_start", request)
            return self.ledger._save_receipt(
                receipt_key, "experiment_start", request, {"started": True}
            )

    def snapshot_validation(self, experiment_id):
        manifest = self.get(experiment_id)
        return json.loads(manifest.model_dump_json()), {
            op: manifest.blockers(op) for op in ["offline", "smoke", "learning", "final"]
        }
