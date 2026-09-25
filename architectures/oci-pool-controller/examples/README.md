# Control-plane adapter example

This is a staging integration example, not a production scheduler. Keep your
existing scheduler and invoke the OCI controller directly using the OCI SDK.
The SDK signs requests; OCI Functions verifies IAM authorization before
execution. No demo UI, browser token or API Gateway is required. The caller
may run in any environment with approved connectivity and an OCI signer.

Use the **controller-only** Terraform deployment with `AUTH_MODE=oci_iam`.
Only approved machine principals should have invoke permission for its dedicated
Function/application. The handler's response envelope is
`{"status_code": <business HTTP status>, "body": <controller response>}`;
an HTTP 200 invocation alone is not proof that the operation succeeded.

## Install and configure

```sh
python3 -m venv .venv-client
.venv-client/bin/python -m pip install -r examples/requirements.txt
```

Configure an operator-owned OCI machine identity/profile using your approved
credential distribution and rotation mechanism. Restrict config/private-key
permissions; never commit credentials or enable SDK HTTP debug logging in
production. The CLI reads the standard OCI config/profile. `OCITransport` also
accepts a preconfigured `FunctionsInvokeClient` for an approved alternate signer.

Use reference deployment outputs for `--function-id` and the **Functions invoke
base endpoint**, not the UI/gateway URL. These commands contain placeholders;
replace them before execution. Keep one persistent outbox per environment and
Function registry in an access-controlled directory. Do not reset it on restart.

```sh
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<controller-function-ocid>' \
  --endpoint 'https://<application-endpoint>.functions.oci.oraclecloud.com' \
  --profile POOL_STAGING --outbox /secure/state/controller-outbox.sqlite \
  pools
```

All commands below use those same connection/outbox arguments before the command.

## Scheduler integration

1. Retain your scheduler's queue/job accounting. Calculate an **absolute non-retiring
   target**, and atomically allocate a strictly increasing per-pool generation
   in the authoritative scheduler database. Publish:

   ```text
   demand --pool intel-small --target 3 --generation 101
   ```

   `Outbox.demand()` stores a UUID and the complete immutable payload before the
   first send. Repeating the same local generation and target returns that UUID.
   A new decision gets a new UUID and generation. Never increment the generation
   just because a request timed out. At migration, seed the counter above the
   controller's observed `latestDesiredGeneration`; do not assume it is zero.

2. When a worker is permanently retiring, stop dispatch to it and verify its
   job has completed/results are durable. Commit retirement of that exact OCID:

   ```text
   retire --pool intel-small --instance-id <worker-ocid> --confirmed-idle-and-dispatch-disabled
   ```

   This writes the exact string `"0"` through `set_pool_protection`; it never
   cancels retirement and does not change demand. Publish a lower absolute target
   only if scheduler demand actually decreased. If demand is unchanged, keep the
   current target; maintenance will finish retirement and request replacements.
   A CLI confirmation flag is an operator assertion, not proof a job is idle.

3. On your periodic per-pool maintenance loop, run:

   ```text
   tick --pool intel-small
   ```

   A tick advances pending retirement writes and replays the latest desired
   request **even if it previously completed**. Each tick makes at most one
   attempt per pending record. It does not run a background loop. The caller owns
   continued ticks, timeouts, monitoring, and alerts for stuck progress.
   Different pools can have independent scheduler loops.

4. Inspect without advancing reconciliation:

   ```text
   status --request-id <persisted-request-uuid>
   pools
   ```

   Status polling alone will not finish a queued/submitted request. Pool
   completion means controller capacity convergence, not job dispatchability.
   `bootstrapReady` is the demo bootstrap marker, **not your platform registration**.
   Use your actual worker registration and application dispatch checks.

## Retry and persistence contract

- SDK automatic retries are disabled. `Controller.send(..., max_attempts=4)` is
  available for bounded in-process retry; the CLI defaults to one attempt, with
  subsequent attempts owned by the scheduler timer. Backoff uses exponential
  jitter capped at 30 seconds (maximum eight attempts per call). A pending
  response remains in the outbox when that bound is reached.
- Honor the controller's `retryable` field. In particular, **do not retry every
  409**: retirement/provenance/safety rejection can be permanent. Transport
  timeouts, 429, and transient 5xx replay the same UUID and entire payload.
- `superseded` is terminal for that request; it cannot regain authority. Do not
  automatically invent a higher generation to override another writer.
- Nonretryable failed records stop automatic maintenance for that demand. Alert,
  diagnose, and authorize a new generation only after the cause is resolved.
  A retirement write with an uncertain timeout followed by `instance_not_member`
  requires inspecting authoritative retirement/instance state; do not assume
  failure, re-protect the worker, or return it to scheduling.
- Interrupted processes resume from the outbox. Persist it before acknowledging
  a scheduler event. The Function has its own durable request/retirement ledgers;
  the caller outbox retains the exact instructions required to replay them.
- Only the scheduler knows the right target. This adapter never derives it from
  observed pool size or guesses `count - 1` after a worker notification.

The SQLite outbox is deliberately a **single-host example**. Before production,
implement its operations in your platform's existing shared transactional database:
one ordered desired-state writer per pool, atomic generation allocation and
outbox insertion, durable job-to-retirement commitment, replay ownership, and
bounded fleet-wide API concurrency. Multiple hosts with independent SQLite
copies can issue conflicting generations; this example does not solve that.
Maintain a backlog/progress deadline and operator escalation; an endless series
of bounded timer ticks is not a service-limit recovery mechanism. Retain failed
retirement records for review instead of treating them as reclaimed workers.

## Verification before integration

The development repository retains the offline regression tests; they are not
included in this installation archive. Adapt their coverage to your platform's CI
when replacing the illustrative outbox: stable retry payloads, persisted
generations across restart, supersession, completed-demand maintenance replay,
fail-closed errors, irreversible retirement and IAM response envelopes.

Run the operator-owned [staging acceptance checks](../docs/RUNBOOK.md) with the
reviewed image and credentials. Source tests do not establish real invocation,
Your platform registration, or application job readiness.

The transport follows Oracle's documented
[FunctionsInvokeClient](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/functions/client/oci.functions.FunctionsInvokeClient.html)
and [signed Function invocation](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsinvokingfunctions.htm).




DNAnexus needs to maintain a **shared, transactional control-plane record per pool**. The SQLite outbox in the example is only a single-host reference; production DNAnexus should implement the equivalent in its existing database.

- **Stable pool identity**
  - `pool_key`
  - OCI pool OCID
  - Controller scope/`HarnessId`
  - Profile/worker type
  - Region and compartment
  - Configured maximum capacity and OCPU/VM limits

- **Latest desired capacity**
  - Absolute desired count of **non-retiring** workers
  - Strictly increasing `desired_generation`
  - The complete immutable request payload
  - A new `request_id` for every changed demand decision
  - Any worker exclusions

- **Request/outbox state**
  - Whether each request is pending, completed, failed, or superseded
  - Last controller response and business status
  - Retry count and next retry time
  - Whether an intervention/ operator review is required
  - OCI work-request IDs and correlation IDs where available

- **Retry and replay ownership**
  - Which DNAnexus replica owns the next replay/tick
  - Per-pool lease or fencing information
  - Backoff/deadline state
  - The rule that retries reuse the **same request ID, generation, and payload**
  - Failed or superseded requests must not be automatically regenerated

- **Worker lifecycle and readiness**
  - Worker OCID and pool membership
  - DNAnexus registration/heartbeat state
  - Dispatchable vs booting, unhealthy, draining, or retired
  - Job assignments and drain status
  - Confirmation that results and cleanup are complete before retirement

- **Retirement commitments**
  - Exact worker OCID selected for retirement
  - Durable retirement request ID
  - Whether the worker is draining, protection has been committed, detached, terminating, or terminated
  - A permanent “do not dispatch/reuse” marker once retirement is committed
  - Retirement retry/progress state

- **Capacity accounting**
  - Attached workers
  - Retiring/detached-but-not-terminated workers
  - Pending launch reservations
  - OCPU and VM budget consumption
  - Cross-pool aggregate budget/lease state

- **Operational and migration state**
  - Last successful reconciliation and last observed pool state
  - Current controller image/digest and ledger ownership
  - Alerts/escalations for stuck `SCALING`, failed launches, missing workers, or unavailable ledger state
  - Audit history sufficient to support rollback and incident review

The critical invariants are:

1. **Persist the request before sending it.**
2. **Allocate the generation and insert the outbox record atomically.**
3. **Never change a payload when retrying.**
4. **Never create competing generation counters on separate replicas.**
5. **Never dispatch work to a committed retiree.**
6. **Do not treat OCI pool convergence as DNAnexus worker readiness.**
7. **Continue periodic replay even after a request reports completed**, because later retirement or drift may require reconciliation.

The project separates this from the controller’s own OCI Object Storage ledger:

| State | DNAnexus responsibility | Controller responsibility |
|---|---|---|
| Desired demand | Persist target, generation, request UUID | Validate and retain controller-side request state |
| Job/worker readiness | Own completely | Does not prove application readiness |
| Drain decision | Own completely | Enforces retirement commitment once requested |
| Retry scheduling | Own periodic ticks and replay | Processes one reconciliation attempt per call |
| OCI capacity/mutation coordination | Track integration state and budget decisions | Persist leases, reservations, retirement records, and pool reconciliation state in Object Storage |

Primary references:

- [`docs/implementation-notes.md`](https://github.com/Perseus1237/oci-pool-controller/blob/main/docs/implementation-notes.md#L43-L69)
- [`examples/pool_controller.py`](https://github.com/Perseus1237/oci-pool-controller/blob/main/examples/pool_controller.py#L107-L215)
- [`README.md`](https://github.com/Perseus1237/oci-pool-controller/blob/main/README.md#L87-L115)
- [`PRODUCT_ARCHITECTURE.md`](https://github.com/Perseus1237/oci-pool-controller/blob/main/PRODUCT_ARCHITECTURE.md#L88-L110)
