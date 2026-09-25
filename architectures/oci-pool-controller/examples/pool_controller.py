#!/usr/bin/env python3
"""Reusable, caller-driven OCI pool controller adapter; never drains jobs.

The SQLite outbox is a single-host reference implementation, not a distributed
scheduler. Your platform should put these immutable records and generation allocation
in its existing transactional control-plane database. A timer must call tick()
even after a desired-count request completed. Reading status does not reconcile.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class Reply:
    status: int
    body: Mapping[str, Any]

    @property
    def superseded(self) -> bool:
        return self.body.get("request_state") == "superseded" or self.body.get("result") == "superseded"

    @property
    def retryable(self) -> bool:
        # Business policy beats HTTP heuristics: a 409 may be a permanent
        # safety rejection, and a 502 may be a permanent IAM failure.
        if "retryable" in self.body:
            return self.body["retryable"] is True
        return self.status == 202 and self.body.get("action") == "reconcile_pool"


class ControllerRejected(Exception):
    """A sanitized nonretryable response for the scheduler/operator to handle."""

    def __init__(self, reply: Reply):
        self.reply = reply
        super().__init__(f"Controller rejected request (status {reply.status})")


class OCITransport:
    """Synchronous signed Function invocation, without SDK-level hidden retry.

    OCI's IAM front door verifies the signature. This requires the controller's
    CONTROLLER_ONLY=true, AUTH_MODE=oci_iam deployment, not the demo gateway.
    Business status is carried in its {status_code, body} JSON envelope.
    """

    def __init__(self, sdk_client: Any, function_id: str, no_retry: Any = None):
        self.sdk_client = sdk_client
        self.function_id = function_id
        self.no_retry = no_retry

    @classmethod
    def from_config(cls, *, function_id: str, endpoint: str, config_file: str, profile: str) -> "OCITransport":
        import oci

        config = oci.config.from_file(config_file, profile)
        no_retry = oci.retry.NoneRetryStrategy()
        client = oci.functions.FunctionsInvokeClient(
            config, service_endpoint=endpoint, timeout=(10, 65), retry_strategy=no_retry,
        )
        return cls(client, function_id, no_retry)

    def __call__(self, payload: Mapping[str, Any]) -> Reply:
        # Credentials stay in the SDK signer. They are never in a payload,
        # request journal, CLI output, or a custom gateway/session token.
        try:
            response = self.sdk_client.invoke_function(
                self.function_id,
                invoke_function_body=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                fn_invoke_type="sync",
                retry_strategy=self.no_retry,
            )
            stream = response.data
            raw = stream.content if hasattr(stream, "content") else stream.read()
        except Exception as error:
            status = getattr(error, "status", 0)
            transient = status in {408, 429, 500, 502, 503, 504} or any(
                word in type(error).__name__.lower() for word in ("timeout", "connection")
            )
            # Never retain SDK exception messages: they may contain signed
            # headers, endpoints, or request bodies when debug logging is on.
            return Reply(int(status or 502), {
                "result": "error", "reason": "function_invocation_failed", "retryable": transient,
            })
        try:
            envelope = json.loads(raw)
            status, body = envelope["status_code"], envelope["body"]
            if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
                raise ValueError("invalid status")
            if not isinstance(body, dict):
                raise ValueError("invalid body")
            return Reply(status, body)
        except (KeyError, TypeError, ValueError, AttributeError):
            return Reply(502, {"result": "error", "reason": "invalid_controller_envelope", "retryable": False})


class Outbox:
    """Persist intent before sending it. No credentials are stored here.

    One database must have one Function/pool-registry owner. For multiple
    scheduler hosts, replace this adapter with the shared scheduler database;
    copying a SQLite file is not a coordination mechanism.
    """

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, pool_key TEXT NOT NULL,
                kind TEXT NOT NULL, generation INTEGER,
                payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                response TEXT, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS demand_generation
                ON requests(pool_key, generation) WHERE kind='demand';
        """)

    def close(self) -> None:
        self.db.close()

    def latest(self, pool_key: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT payload FROM requests WHERE pool_key=? AND kind='demand' ORDER BY generation DESC LIMIT 1",
            (pool_key,),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def demand(self, pool_key: str, desired_size: int, generation: int) -> dict[str, Any]:
        if isinstance(desired_size, bool) or not isinstance(desired_size, int) or desired_size < 0:
            raise ValueError("desired_size must be a nonnegative integer")
        if isinstance(generation, bool) or not isinstance(generation, int) or not 1 <= generation <= 9_007_199_254_740_991:
            raise ValueError("generation must be a positive JavaScript-safe integer")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            latest = self.latest(pool_key)
            if latest and generation <= latest["desired_generation"]:
                if generation == latest["desired_generation"] and desired_size == latest["desired_size"]:
                    self.db.commit()
                    return latest
                raise ValueError("new demand must advance the persisted per-pool generation")
            payload = {"action": "reconcile_pool", "request_id": str(uuid.uuid4()), "pool_key": pool_key,
                       "desired_size": desired_size, "desired_generation": generation}
            self._insert(payload, "demand", generation)
            self.db.commit()
            return payload
        except Exception:
            self.db.rollback()
            raise

    def retire(self, pool_key: str, instance_id: str) -> dict[str, Any]:
        # The scheduler must first stop dispatch and verify the job is finished.
        # There is deliberately no 'cancel retirement' or guessed count-minus-one.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for row in self.db.execute("SELECT payload FROM requests WHERE pool_key=? AND kind='retirement'", (pool_key,)):
                payload = json.loads(row["payload"])
                if payload["instance_id"] == instance_id:
                    self.db.commit()
                    return payload
            payload = {"action": "set_pool_protection", "request_id": str(uuid.uuid4()), "pool_key": pool_key,
                       "instance_id": instance_id, "tag_value": "0"}
            self._insert(payload, "retirement", None)
            self.db.commit()
            return payload
        except Exception:
            self.db.rollback()
            raise

    def _insert(self, payload: Mapping[str, Any], kind: str, generation: int | None) -> None:
        self.db.execute("INSERT INTO requests(request_id,pool_key,kind,generation,payload) VALUES(?,?,?,?,?)",
                        (payload["request_id"], payload["pool_key"], kind, generation, json.dumps(payload, sort_keys=True)))

    def record(self, payload: Mapping[str, Any], reply: Reply) -> None:
        if reply.superseded:
            state = "superseded"
        elif reply.body.get("intervention_required") is True:
            # A held capacity reservation is NOT a retry loop. Stop automatic
            # maintenance and alert the operator; never reset the server ledger.
            state = "failed"
        elif reply.retryable:
            state = "pending"
        elif reply.status >= 400 or reply.body.get("request_state") == "failed" or reply.body.get("result") in {"failed", "error", "rejected"}:
            state = "failed"
        elif payload["action"] == "set_pool_protection" and reply.body.get("retirement_committed") is not True:
            state = "failed"  # A dry-run response is not a committed retirement.
        elif payload["action"] == "reconcile_pool" and reply.body.get("request_state") != "completed":
            state = "pending"
        else:
            state = "completed"
        with self.db:
            self.db.execute("UPDATE requests SET state=?,response=?,attempts=attempts+1 WHERE request_id=?",
                            (state, json.dumps({"status": reply.status, "body": reply.body}), payload["request_id"]))

    def state(self, request_id: str) -> str:
        row = self.db.execute("SELECT state FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("request is not in the durable outbox")
        return str(row["state"])

    def pending_retirements(self, pool_key: str) -> list[dict[str, Any]]:
        return [json.loads(row["payload"]) for row in self.db.execute(
            "SELECT payload FROM requests WHERE pool_key=? AND kind='retirement' AND state='pending' ORDER BY rowid",
            (pool_key,),
        )]


class Controller:
    def __init__(self, transport: Callable[[Mapping[str, Any]], Reply], outbox: Outbox,
                 *, sleep: Callable[[float], None] = time.sleep, jitter: Callable[[float, float], float] = random.uniform):
        self.transport, self.outbox, self.sleep, self.jitter = transport, outbox, sleep, jitter

    def send(self, payload: Mapping[str, Any], *, max_attempts: int = 1) -> Reply:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 8:
            raise ValueError("max_attempts must be between 1 and 8")
        # Use the stored immutable payload, never caller-mutated fields.
        row = self.outbox.db.execute("SELECT payload FROM requests WHERE request_id=?", (payload["request_id"],)).fetchone()
        if row is None or json.loads(row["payload"]) != dict(payload):
            raise ValueError("send requires the original persisted request payload")
        for attempt in range(max_attempts):
            if payload["action"] == "reconcile_pool":
                latest = self.outbox.latest(str(payload["pool_key"]))
                if latest is None or latest["request_id"] != payload["request_id"]:
                    return Reply(200, {"result": "superseded", "request_id": payload["request_id"], "retryable": False})
            reply = self.transport(dict(payload))
            if reply.body.get("request_id", payload["request_id"]) != payload["request_id"]:
                reply = Reply(502, {"result": "error", "reason": "response_request_id_mismatch", "retryable": False})
            self.outbox.record(payload, reply)
            if not reply.retryable:
                if self.outbox.state(str(payload["request_id"])) == "failed":
                    raise ControllerRejected(reply)
                return reply
            if attempt + 1 < max_attempts:
                cap = min(30.0, 2.0 ** (attempt + 1))
                self.sleep(self.jitter(cap / 2, cap))
        return reply  # Pending remains durable; the caller owns its next tick.

    def tick(self, pool_key: str) -> list[Reply]:
        """One bounded timer tick, not an autonomous worker/queue consumer.

        Latest completed demand is intentionally replayed: retirement or drift
        may have changed capacity. Failed/superseded demand needs a fresh,
        scheduler-authorized generation after diagnosis, not automatic retry.
        """
        replies = [self.send(payload) for payload in self.outbox.pending_retirements(pool_key)]
        latest = self.outbox.latest(pool_key)
        if latest and self.outbox.state(latest["request_id"]) not in {"failed", "superseded"}:
            replies.append(self.send(latest))
        return replies

    def request_status(self, request_id: str) -> Reply:
        return self.transport({"action": "request_status", "request_id": request_id})

    def pool_status(self) -> Reply:
        # Pool and bootstrap observations, NOT actual platform registration.
        return self.transport({"action": "pool_status"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function-id", required=True)
    parser.add_argument("--endpoint", required=True, help="Functions invoke base endpoint from deployment outputs")
    parser.add_argument("--config-file", default=str(Path.home() / ".oci" / "config"))
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--outbox", default="controller-outbox.sqlite", help="Durable single-host state; back it up, do not reset between runs")
    sub = parser.add_subparsers(dest="command", required=True)
    demand = sub.add_parser("demand")
    demand.add_argument("--pool", required=True)
    demand.add_argument("--target", type=int, required=True)
    demand.add_argument("--generation", type=int, required=True)
    retire = sub.add_parser("retire")
    retire.add_argument("--pool", required=True)
    retire.add_argument("--instance-id", required=True)
    retire.add_argument("--confirmed-idle-and-dispatch-disabled", action="store_true", required=True)
    tick = sub.add_parser("tick")
    tick.add_argument("--pool", required=True)
    status = sub.add_parser("status")
    status.add_argument("--request-id", required=True)
    sub.add_parser("pools")
    args = parser.parse_args()
    outbox = Outbox(args.outbox)
    try:
        controller = Controller(OCITransport.from_config(function_id=args.function_id, endpoint=args.endpoint,
                                                        config_file=args.config_file, profile=args.profile), outbox)
        if args.command == "demand":
            replies = [controller.send(outbox.demand(args.pool, args.target, args.generation))]
        elif args.command == "retire":
            replies = [controller.send(outbox.retire(args.pool, args.instance_id))]
        elif args.command == "tick":
            replies = controller.tick(args.pool)
        elif args.command == "status":
            replies = [controller.request_status(args.request_id)]
        else:
            replies = [controller.pool_status()]
        print(json.dumps([{"status_code": reply.status, "body": reply.body} for reply in replies], indent=2))
        return 0 if all(reply.status < 400 or reply.retryable for reply in replies) else 2
    except ControllerRejected as error:
        print(json.dumps({"status_code": error.reply.status, "body": error.reply.body}))
        return 2
    except Exception:
        # Configuration errors must not dump credential paths or signer state.
        print(json.dumps({"result": "error", "reason": "client_configuration_or_outbox_error"}))
        return 2
    finally:
        outbox.close()


if __name__ == "__main__":
    raise SystemExit(main())
