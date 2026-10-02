"""One projection policy for every query/download path; final release is durable."""

import json
from recursive_run.contracts import TERMINAL, State
from recursive_run.ledger import Blocked


class Projection:
    def __init__(self, ledger):
        self.ledger = ledger

    @property
    def released(self):
        row = self.ledger.db.execute(
            "SELECT value FROM settings WHERE key='final_release'"
        ).fetchone()
        return bool(row and json.loads(row[0])["released"])

    def runs(self):
        rows = [dict(row) for row in self.ledger.db.execute("SELECT * FROM attempts ORDER BY id")]
        for row in rows:
            if row["phase"] == "final" and not self.released:
                row["state"] = "TERMINAL" if State(row["state"]) in TERMINAL else "IN_PROGRESS"
                # Internal task/action details may carry outcomes; neutral fields only.
                row_keys = {"id", "phase", "state"}
                for key in list(row):
                    if key not in row_keys:
                        del row[key]
        return rows

    def events(self):
        result = []
        for row in self.ledger.db.execute("SELECT * FROM events ORDER BY sequence"):
            if row["attempt"]:
                attempt = self.ledger.attempt(row["attempt"])
                if attempt["phase"] == "final" and not self.released:
                    continue  # even an error event can leak outcomes
            result.append(dict(row))
        return result

    def artifact_allowed(self, key, learner=False):
        row = self.ledger.db.execute("SELECT * FROM artifact_refs WHERE hash=?", (key,)).fetchone()
        if not row:
            return False
        if row["visibility"] == "private":
            return False
        if learner:
            return row["visibility"] in {"public", "development"}
        return row["visibility"] != "final" or self.released

    def freeze_schedule(self, assignments: list[dict[str, str]]):
        if len(assignments) != 120 or len({x["id"] for x in assignments}) != 120:
            raise ValueError("default final schedule requires 120 unique attempts")
        tasks = {x["task"] for x in assignments}
        if len(tasks) != 40 or any(
            {x["arm"] for x in assignments if x["task"] == task} != {"S", "B", "R"}
            for task in tasks
        ):
            raise ValueError("40 complete S/B/R task assignments required")
        with self.ledger.transaction():
            if self.ledger.db.execute("SELECT 1 FROM attempts WHERE phase='final'").fetchone():
                raise Blocked("freeze schedule before final attempt creation")
            self.ledger.db.execute(
                "INSERT INTO settings VALUES('final_schedule',?)", (json.dumps(assignments),)
            )

    def release(self, assigned_attempts: list[str], receipt_key: str, incomplete=False):
        # Authentication/authorization belongs at the endpoint, never inferred from this call.
        request = {"assigned": assigned_attempts, "incomplete": incomplete}
        with self.ledger.transaction():
            prior = self.ledger._receipt(receipt_key, "final_release", request)
            if prior is not None:
                return prior
            frozen = self.ledger.db.execute(
                "SELECT value FROM settings WHERE key='final_schedule'"
            ).fetchone()
            if not frozen:
                raise Blocked("final schedule was not frozen")
            planned = json.loads(frozen[0])
            if sorted(assigned_attempts) != sorted(x["id"] for x in planned):
                raise Blocked("release cannot omit frozen assigned attempts")
            for item in planned:
                row = self.ledger.attempt(item["id"])
                if (
                    row["task"] != item["task"]
                    or row["arm"] != item["arm"]
                    or row["phase"] != "final"
                ):
                    raise Blocked("attempt does not match frozen assignment")
            actual = [
                row["id"]
                for row in self.ledger.db.execute(
                    "SELECT id FROM attempts WHERE phase='final' ORDER BY id"
                )
            ]
            if not assigned_attempts or sorted(assigned_attempts) != actual:
                raise Blocked("complete assigned attempt membership required")
            if any(
                State(self.ledger.attempt(key)["state"]) not in TERMINAL
                for key in assigned_attempts
            ):
                raise Blocked("all scheduled final attempts must be terminal")
            if self.ledger.unsettled()["leases"] or self.ledger.unsettled()["obligations"]:
                raise Blocked("worker and cost reconciliation required before release")
            previous = self.ledger.db.execute(
                "SELECT value FROM settings WHERE key='final_release'"
            ).fetchone()
            if previous:
                raise Blocked("release already frozen")
            result = {
                "released": True,
                "incomplete": incomplete,
                "promotion_allowed": not incomplete,
            }
            self.ledger.db.execute(
                "INSERT INTO settings VALUES('final_release',?)", (json.dumps(result),)
            )
            self.ledger._event(None, "final_release", result)
            return self.ledger._save_receipt(receipt_key, "final_release", request, result)
