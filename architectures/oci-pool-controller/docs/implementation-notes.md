# OCI Pool Controller Reference Implementation: implementation notes

This guide describes the platform integration path in the accompanying
[Function source](../function/func.py). It is **unsupported sample code for
engineering review and staging**, not a production-qualified service.
Read the [disclaimer](../DISCLAIMER.md) and [release notice](../NOTICE.md)
before use.

## 1. Integration approach

Keep your scheduler, demand calculation and worker runtime. For each migrated
pool, replace the previous capacity writer with a signed call to the controller
and run a durable periodic maintenance loop. Your platform owns worker assignment, job
completion, draining, readiness and the decision to retire a worker.

The controller reads OCI's actual pool state, retains desired state and
retirement commitments in Object Storage, and advances one reconciliation
attempt per call. There is no autonomous background consumer: your platform must
continue retries and periodic maintenance calls.

Use [reference Terraform](../deploy/reference/README.md) and the
[signed client example](../examples/README.md). The client is an illustrative
single-host SQLite outbox, not a distributed scheduler.

## 2. Controller API surface

Reference deployment uses `CONTROLLER_ONLY=true`, `FUNCTION_ROLE=control`
and `AUTH_MODE=oci_iam`. Calls use the OCI Functions signed invocation endpoint,
not the demonstration browser gateway.

| Action | Purpose |
| --- | --- |
| `reconcile_pool` | Publish or replay an absolute desired capacity request and advance reconciliation. |
| `set_pool_protection` | Commit retirement of an exact worker with tag value `"0"`; protection `"1"` is allowed only before retirement commitment. |
| `request_status` | Read a stored reconciliation result without advancing work. |
| `pool_status` | Alias for `scale_test_status`; read registered pool, membership and diagnostic status. |

The shared source also contains legacy demo actions and worker callback roles.
They are not part of this controller API and are rejected in controller-only mode.
Invocation permission authorizes all operator actions across the controller's
registered pools; restrict that permission to approved control-plane identities.

## 3. Desired capacity and caller state

`desired_size` is the absolute number of **non-retiring workers required**,
including provisioning capacity. It is not an increment, current physical
membership, or the number of workers already ready to accept jobs.

Your platform must persist:

- The latest desired target for each stable `pool_key`.
- A strictly increasing `desired_generation` for each new demand decision.
- A new UUID `request_id` and the complete immutable payload for that decision.
- Pending retirement operations, retry/progress state and ownership of replay.
- Job/worker assignment and readiness state, including the permanent ban on
  assigning work to committed retirees.

Operator demand requests require `desired_generation`. Allocate generations
and write the outbox atomically in your platform's shared transactional state.
Independent local counters on multiple replicas are not sufficient.

A retry uses the **same UUID and payload**, including target, generation and
exclusions. A changed decision needs a **new UUID and higher generation**;
reusing a UUID with changed arguments is rejected. Seed migration generations
above the controller's observed generation rather than assuming zero.

The controller discovers physical pool size and membership; your platform does not
need to send a manually calculated OCI `current_count - 1`. That does not make
the caller stateless or eliminate its retry and job-safety responsibilities.

## 4. Scale-out workflow

1. Your platform calculates the desired non-retiring capacity and persists the request.
2. The controller validates the request, registered pool identity and latest
   generation, using its durable request ledger.
3. If retirements are pending, it finishes those first. Otherwise, when safe
   to grow, it checks the pool is `RUNNING`, the instance configuration matches,
   no OCI autoscaling configuration is attached, and configured budgets permit growth.
4. Under coordination leases and fresh state checks, it requests a larger
   absolute pool size through `UpdateInstancePool`.
5. Your platform replays the request with bounded backoff until convergence or an
   actionable failure. It separately verifies real platform registration and
   application dispatchability before assigning jobs.

Example new demand:

```json
{
  "action": "reconcile_pool",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "pool_key": "intel-small",
  "desired_size": 3,
  "desired_generation": 101,
  "exclude_instance_ids": []
}
```

Use a registered pool key and a target within its configured ceiling. The UUID
and generation above are illustrative; allocate actual values durably.

A newer request can be retained while the pool is `SCALING`; caller replay
advances it when safe. Newer generations supersede obsolete demand rather than
forming a FIFO of every intermediate target. This does not guarantee exactly
one OCI mutation for a burst or eliminate API throttling. Bound fleet-wide
caller concurrency and validate throughput in staging.

Ordinary growth without pending retirements does not require termination to be
enabled. With retire-first replacement, disabling termination can prevent
retirements from finishing and therefore block replacement launches.

## 5. Exact worker retirement

Your platform must stop new assignment, finish the job, make results durable and
complete required cleanup **before** committing a worker. There is no
controller-enforced grace interval after marking it.

```json
{
  "action": "set_pool_protection",
  "request_id": "438fdf12-2608-47b5-9417-4fc29b160231",
  "pool_key": "intel-small",
  "instance_id": "ocid1.instance.REPLACE_WITH_SELECTED_WORKER",
  "tag_value": "0"
}
```

Replace the instance placeholder with the selected worker's exact OCID.

The controller writes a durable retirement commitment before updating the tag.
A timeout or failed tag confirmation does not make reuse safe. Marking retirement
does not itself resize or terminate; replaying the latest demand advances it.

Managed detach candidates must be attached, `RUNNING`, outside the request's
exclusions, and have matching `HarnessId` and `ScaleTestProfile` enrollment
tags plus exact `InstanceTerminationProtectionEnabled="0"`. Missing or malformed
values, numeric zero and `"false"` are not equivalent. New workers start with
`"1"`. The legacy `OriginPoolId`, `DrainOperationId` and `DrainRequestedAt`
tags are not required by this managed retirement path.

Fresh identity, membership, eligibility and coordination checks precede exact
detach. The call specifies `is_decrement_size=true` and
`is_auto_terminate=true`, using the durable per-worker retry token.
Acceptance is asynchronous: detached is not proof of `TERMINATED`.

All committed retirements must finish, even if demand increases or their count
exceeds the desired reduction. An out-of-band protection change or exclusion
can block detach but cannot cancel retirement. Your platform must not return that
worker to dispatch.

The replacement policy is **retire-first, no surge**. For A/B/C retiring while
demand returns to three, the controller finishes A/B/C, then launches different
workers toward three. This can create a temporary capacity gap; it does not
provide zero-downtime replacement. Lower the desired target only if demand
actually falls. Otherwise, retain the target and continue maintenance replay.

## 6. Responses, retries and completion

Signed invocation returns an application envelope:

```json
{
  "status_code": 202,
  "body": {
    "result": "queued",
    "retryable": true
  }
}
```

This is an abbreviated example, not the complete response schema. Transport
HTTP success alone does not mean that the operation succeeded.

| Observed outcome | Caller action |
| --- | --- |
| Pending/queued/submitted and retryable | Retain the original payload and UUID; replay with bounded exponential backoff and jitter. |
| Timeout or unknown transport outcome | Do not assume failure; replay the same logical request and inspect authoritative state. |
| `completed` | Capacity converged when observed; verify real worker readiness separately and continue periodic latest-demand replay. |
| `superseded` | Stop replaying that obsolete demand; allow the latest request to drive progress. |
| Nonretryable failure | Stop automatic replay, alert and resolve the cause before authorizing a new decision. |

Do not retry every `409`: some conflicts are permanent safety rejections.
Use the response's `retryable`, `result`, `outcome` and `request_state`.
A successful `request_status` read returns application status 200 even when its
stored request is pending or failed; inspect the body. Status reads never
advance reconciliation.

The latest completed demand can reopen on replay after later retirement or
drift. Failed or superseded requests do not automatically reopen. Retain
periodic latest-demand replay even when the desired count has not changed.

## 7. Safety controls and current boundaries

- `DRY_RUN=true` prevents Compute/tag mutation but may create or update ledger
  records. Use a new UUID/generation for the reviewed live decision.
- `ENABLE_TERMINATION=false` blocks auto-terminating detach. It does not block
  ordinary scale-out or prevent retirement commitments when dry-run is off.
- Neither setting cancels OCI operations already accepted.
- Operator scope checks, protected launch tags, durable state, leases, generation
  checks and configured capacity guards remain required. Do not disable them
  to force progress. An attached OCI autoscaling configuration is rejected
  even when disabled; use one capacity writer per migrated pool.
- The controller does not cancel service-limit-stalled launches. A pool stuck
  `SCALING` can also block drain; escalate through the runbook.
- Managed `STOPPED`-worker cleanup and lifecycle-alert retirement are not
  implemented. Review your platform's OS-shutdown sequence before integration.
- Bootstrap markers are diagnostic, not proof of your platform readiness.
- Shape validation caps, the 100-instance exclusion limit, inline registry size,
  ledger growth, and shared budget coordination constrain this sample.
  Raising configured ceilings does not prove fleet-scale throughput or capacity.

See the [architecture limits](../PRODUCT_ARCHITECTURE.md#7-limits-and-failure-boundaries)
and [operations runbook](RUNBOOK.md) for details and recovery procedures.

## 8. Before production adoption

Run the [ordered staging acceptance checks](RUNBOOK.md#staging-acceptance-in-order)
with the actual operator image, IAM, pools and worker runtime. Prove overlapping
targets, busy-worker protection, irreversible retirement with returning demand,
retry/restart behavior, real worker readiness and exact cleanup.

Assign owners for shared caller state, retries, rate limits, credentials,
monitoring, incident recovery, ledger retention and rollback. Accept or address
the documented gaps before production approval. Historical lab tests and source
packaging do not qualify the reference deployment or establish a latency SLA.
