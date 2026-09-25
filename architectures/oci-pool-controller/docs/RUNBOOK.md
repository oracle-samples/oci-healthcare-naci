# OCI Pool Controller staging and operations runbook

For the opt-in bounded-growth preview, use the additional
[acceptance and recovery gates](BOUNDED_GROWTH_ACCEPTANCE.md). The retire-first
sections below describe the default policy, not the preview. New offline
controller acceptance tests are bundled. The separate
[2026-09-17 live report](LIVE_ACCEPTANCE_20260917.md) records the isolated
bounded-growth results and remaining gaps; it does not certify this whole
runbook or the external handoff environment.

## Safety boundary

Release `0.12.0-rc.1` supports review and bounded staging. Start with the package
[README](../README.md) and [request contract](../PRODUCT_ARCHITECTURE.md).
This runbook does not authorize a live deployment, tag change, termination,
quota increase or rollback. Obtain the
operator change owner's approval for the exact environment and actions.

This is unsupported sample code, not an Oracle-supported product or service.
Read the [sample-code disclaimer](../DISCLAIMER.md) and [release notice](../NOTICE.md).
Sample packaging and test results do not replace
operator security, operational, or legal review.

Retirement is permanent. Never assign work to a committed worker, re-protect
it to cancel retirement, delete its registry entry, or lower the OCI pool size
generically as a cleanup shortcut. There is no post-tag grace period: your platform
must finish draining before committing `"0"`.

## 1. Pre-deployment checklist

Use the [reference deployment guide](../deploy/reference/README.md).

- Approve the source/image version and immutable digest. Have the release owner
  and appropriate legal approver confirm provenance, licensing/distribution
  authority, Oracle copyright attribution and 2024 year, and third-party notices.
- Verify archive/manifest integrity, approved recipients and source/dependency
  security review. Obtain matching development-repository regression and fault
  test results; tests and historical evidence are not bundled in this archive.
- Inventory one staging pool first: region, pool ID, compartment, shape,
  instance configuration, launch tags, members, target, current size and state.
- Record the configured controller scope and stable pool keys. Preserve their
  mapping with the ledger; renaming/reusing keys is a migration, not cosmetic.
- Approve operational pool and aggregate OCPU limits and maximum test cost.
  Verify OCI service limits, quotas and available capacity separately.
- Approve the existing Function subnet, OCI-service egress, registry access and
  the signed invoke path from the caller's environment. This module does not
  establish private caller-to-OCI networking or create worker pools.
- Review IAM. Signed invocation grants the controller's full allowlisted pool
  authority. Inspect optional IAM creation and the exact Function dynamic-group
  rule; do not reuse a broad demo principal.
- Review pool/configuration/launch/worker scope tags and free-form tag headroom.
  Preserve unrelated tags. New instances must start protected.
- Store Terraform state securely with access controls and locking. Plans,
  populated variables and state are not release artifacts.
- Confirm logging, incident owner, retry/deadline policy, durable caller state,
  and real platform registration/dispatchability checks.
- Agree on exact tag `"0"` versus any existing `"false"` convention, the
  stop-assignment barrier and durable job results/runtime teardown before retirement.
  Review the guest OS shutdown sequence: managed retirement requires RUNNING
  workers and does not implement STOPPED-worker cleanup.
- Accept retire-first/no surge and its possible capacity gap. Set operator
  latency and throughput acceptance targets, including the retirement wait.

Check the [shape, exclusion and registry limits](../PRODUCT_ARCHITECTURE.md#7-limits-and-failure-boundaries)
against the intended fleet. Separate deployments need disjoint pool ownership
and explicit budget allocation; they do not share an aggregate fleet guard.

## 2. Safe canary cutover

1. Begin with `dry_run=true`, `enable_termination=false`, and a reviewed
   Terraform plan. Dry-run checks are not evidence of real retirement writes.
2. Enroll the staging resources according to the deployment guide. Replace
   immutable instance configurations through the operator-owned change process.
3. Verify signed `pool_status`, request validation and application envelope
   handling. Confirm unauthorized/wrong-pool requests cannot mutate.
4. Quiesce your platform's policy updates for this pool. Record latest real demand.
   Wait for existing OCI work to settle and inventory actual membership.
5. Export the old autoscaling configuration for reference, remove it only with
   approval, and verify it is absent. Merely disabling it is insufficient:
   the controller intentionally rejects any attached configuration.
6. Establish one durable your platform generation publisher/retry owner. Seed the
   authoritative desired non-retiring target using a new UUID/generation.
7. With canary approval, turn off dry-run while retaining termination-disabled
   and test small protected scale-out. For the later retirement tests, enable
   termination in both Function configuration and IAM through a reviewed change.
   Dry-run completion is not live validation; use a new UUID/generation for
   the live decision.
8. Complete the acceptance tests below. Record exact worker identities and
   outcomes; inspect unexpected work before proceeding.

Never leave OCI autoscaling and this controller writing the same pool.

### Staging acceptance, in order

Record these details in an operator-owned results artifact or change record:

```text
Release/commit, package manifest/checksum, operator image digest:
Region, exact pool IDs, worker image/runtime/registration version:
Test window, capacity/OCPU/cost caps, deadline and cleanup owner:
Scheduler, runtime, security/platform and operations reviewers:
Evidence location/access classification and change approval:
Per-test result/evidence, unresolved issues and assigned owners:
Staging acceptance sign-off/date; separate production decision/date:
```

An unchecked or incomplete test is not a pass. Each test applies to the recorded
environment; obtain an approved second pool before the two-pool test. Run
fault/restart cases against fake services in the development repository before
bounded live experiments. Never exhaust shared production quotas to test a
failure path.

| Test | Required evidence |
| --- | --- |
| Read-only/auth and dry-run | Correct pool inventory; unauthorized, wrong-scope and legacy/demo requests rejected without Compute/tag mutation; application errors inside HTTP success decoded. Dry-run may write coordination/request records. |
| Small scale-out | Exact desired membership, protected new workers, real platform registration and successful disposable application dispatch. Verify mutation controls before retirement tests. |
| True overlapping demand | Submit target three while target two is actually `SCALING`; same-ID replay remains accepted/queued and converges to exactly three. |
| Rapid `2 → 3 → 5 → 4` | Highest generation is authoritative and old requests stay superseded. If five workers materialize, explicitly drain safe excess workers; protected workers are not deleted to satisfy four. |
| Two pools together | Independent progress and generations, correct identities, bounded aggregate budget and no cross-pool mutation. |
| Busy-worker protection | Two busy workers and their job results survive; only the exact drained third worker terminates. |
| Permanent retirement with returning demand | Commit A/B/C, request three while retirement is pending, confirm A/B/C `TERMINATED` and distinct D/E/F registered. No cancellation, reuse, duplicates or nonterminated orphans. |
| Completed-demand and stale replay | Commit a worker after demand completes; replay that same latest demand UUID to finish retirement and replenish. An older target-zero request remains superseded and cannot remove replacements. |
| Fail-closed checks | Missing/malformed protection, wrong provenance, retirement exclusions, re-protection, unreadable ledger and STOPPED workers cannot cause unsafe deletion. Confirm unknown state never restores dispatch eligibility. |
| Unknown outcome and retries | Safely injected 409/412/429, timeout and 5xx preserve UUID/payload/generation, respect retryability and bounded jitter/backoff, and recover without duplicate mutation. Permanent errors alert and stop automatic replay. |
| Caller/controller restart | Restart during active work; outbox, latest generation and retirement commitments survive. Resume latest replay with one owner and no stale writer. |
| Capacity failure | Limit/capacity error and deadline alert are visible, intent remains durable and the runbook is followed. Do not infer stalled `SCALING` was canceled. |
| Exact cleanup | Follow section 7. Approved workers are drained and terminated; intended target/current/membership and pending retirements reach zero for a disposable pool, with no nonterminated orphans. Record remaining resources/costs. |

For capacity tests, record UTC times for demand decision, controller receipt,
OCI mutation acceptance, attached, RUNNING, bootstrap, your platform registration
and dispatchability. Distinguish demand-to-ready from launch-acceptance-to-ready;
API completion or synthetic bootstrap is not real worker readiness.

On failure, preserve evidence, pause dependent tests, inspect membership and
costs, and follow the approved cleanup or incident plan. Staging success is
not automatic production promotion; apply the gates in section 9.

## 3. Normal operation

The platform minute loop or equivalent durable worker:

1. Delivers selected, drained-worker retirement requests using stable IDs.
2. Publishes a new absolute target only for a new demand decision; allocates a
   new UUID and strictly higher generation in shared transactional state.
3. Replays identical pending requests with bounded backoff/jitter.
4. Periodically replays the latest demand after completion to observe later
   retirement/drift. A superseded request is never revived.
5. Reads status for evidence and alerts on age/shortfall/errors. Status is not
   a consumer and does not advance work.

The [example client's](../examples/README.md) `tick` supplies single-host
SQLite mechanics. Production replicas need shared transactional state for
atomic generations/outbox writes and replay ownership; independent host-local
copies can conflict. Your platform owns scheduling, supervision, retry budgets and alerts.
Transport HTTP 200 can contain an application error: unwrap `status_code` and
`body` before deciding whether the operation succeeded.

Do not remove retiring workers from your platform's audit/ownership records until
their terminal state is reconciled. Marking `"0"` commits before the OCI tag
write; a timeout or failed tag confirmation does not make reuse safe.

## 4. First evidence to collect for an incident

Use read-only checks first; do not click retry/drain repeatedly while diagnosing.

- Application request UUID, desired generation, immutable payload, result,
  outcome, retryability, attempt timestamps, request state and lease metadata.
- Latest authoritative per-pool desired state and any newer publisher.
- OCI pool target/current/membership and lifecycle; work-request errors and
  request IDs; each relevant instance's lifecycle and current scope/protection.
- Retirement registry entries, including detached-but-terminating instances.
- Function logs/Audit correlation, actual image digest and configuration.
- your platform busy/idle/assignment state and real worker heartbeat/registration.
- Cost guard headroom, service limits, quotas and capacity evidence.

Keep credentials, patient/genomic data and job payloads out of support exports.
Ledger records, resource IDs and logs still require operator access controls.
Do not clear a lease or edit the ledger manually to force progress.

## 5. Symptom-to-action guide

| Symptom | Meaning/check | Safe action |
| --- | --- | --- |
| HTTP success but no change | Direct invoke envelope may contain a business error, dry-run, submitted or queued state. | Inspect application status/body; verify dry-run and action; do not infer readiness. |
| `pool_busy` / long SCALING | Inspect `pending_reason`: bounded growth allows SCALING, but exact detach requires RUNNING; other lifecycle states and OCI IncorrectState can still block. Legacy/default mode retains its RUNNING gate. | Preserve latest demand; use bounded replay, inspect work requests and alert on age. |
| `launch_pending` | A reserved target is not yet observed complete. Only positively acknowledged launches can be extended upward; unknown/legacy outcomes cannot. | Replay latest demand; never clear reservations or manually set their `accepted` marker. |
| `pool_state_changed` | Target and membership observations disagree, including transient stale membership after detach. | Replay the same demand after backoff. Do not infer free capacity or clear commitments; alert if inconsistency persists. |
| `awaiting_eligible_instances` | Desired reduction lacks safe RUNNING, in-scope, non-excluded tag-0 workers. | your platform reviews drain decisions. Do not select a busy/protected replacement victim. |
| `retirement_pending` | A commitment remains unfinished; detached does not mean TERMINATED. | Inspect exact identities/lifecycles, scope/protection and exclusions. Resume latest demand after resolving cause. |
| `retirement_committed` | An attempt tried to re-protect a permanent retirement. | Reject reuse in the scheduler; provision different workers if needed. |
| Committed worker now tagged `1` | Out-of-band conflict blocks safe detach but does not cancel retirement. | Keep worker out of dispatch; investigate tag writer. Restore canonical eligibility only after approved safety verification. |
| Retiring worker STOPPED | Current managed candidate path requires RUNNING. | Escalate for a reviewed exact-worker recovery; do not automatically restart, generic-shrink or direct-delete it. STOPPED cleanup needs separate implementation. |
| Retirement instance unreadable/404 | Absence may be authorization/visibility, not verified termination. | Check exact scope/IAM and authoritative lifecycle evidence. Do not treat every 404 as successful deletion. |
| `autoscaling_configuration_attached` | Another scaling authority may exist, even if disabled. | Stop competing policy writes and complete approved cutover; never bypass the guard. |
| `oci_authorization_failed` | Exact detach permissions may be missing. | Review required delete/update permissions and scope; no blind retry loop or broad emergency grants. |
| Operational budget/configuration rejection | Operator limits, shape or enrollment do not permit request. | Review requested capacity and config with the owner; do not disable scope or budget controls. |
| UUID payload/generation conflict | Caller replay contract is broken or publisher is stale. | Restore immutable outbox record; allocate a new generation only for a genuinely new decision. |
| `superseded` | A newer desired generation is authoritative. | Stop replaying old demand; let the latest request continue committed retirements. |
| Ledger invalid/unavailable | Authoritative desired/retirement or coordination cannot be trusted. | Fail closed, preserve evidence, restore access/consistent state under a reviewed recovery plan. |

### Service-limit-stalled scale-out and blocked drain

If target ten launched only four and OCI remains SCALING with `LimitExceeded`,
the latest desired zero may be safely retained while detach is blocked by pool
state. This release does **not** cancel OCI's outstanding launch attempts.

1. Verify the exact work-request error; distinguish service limit, compartment
   quota, regional capacity, permission and transient throttle.
2. Suppress hot retries/duplicate requests. Keep the immutable desired state
   and use the agreed backoff/deadline/alert policy.
3. Notify the operator capacity owner/SRE with current target, real membership,
   pending retirement IDs and OCI evidence.
4. Obtain approval for any quota/capacity change or OCI-supported recovery.
   A limit increase may permit launches and spending; it is not read-only.
5. Do not force a smaller generic pool size, delete the pool/ledger, or switch
   to the legacy terminator. Those are not supported automatic recovery paths.
6. Once the underlying condition is resolved and OCI permits mutation, replay
   the latest desired state and verify exact cleanup and no new orphans.

## 6. Pause and emergency controls

Stop all client publishers/ticks first. Changing Function settings does not
cancel already accepted OCI work and may not stop an invocation already running.
Inspect in-flight work and preserve the ledger.

- `dry_run=true` prevents new mutation attempts from invocations using that
  configuration; it is not a rollback of accepted work.
- `enable_termination=false` blocks exact auto-terminating detach in the
  controller. It does not cancel retirement commitments or disable scale-out.
  A tag-0 call can still commit retirement when dry-run is off.
- For a full mutation stop, use an approved caller/IAM/configuration isolation
  procedure and verify no remaining execution authority. Keep read-only evidence
  accessible through a separate operator identity.

Do not use termination-off alone as a full scaling kill switch. Verify applied
configuration and observe outstanding OCI operations before declaring a pause.
After resolving a terminal kill-switch rejection, submit a reviewed new demand
generation; do not assume the failed request will reopen automatically.

## 7. Targeted test cleanup

Only clean up workers explicitly placed in the approved disposable test scope.

1. Disable job assignment and confirm each exact worker is fully drained.
2. Commit exact tag `"0"` through the controller; retain acknowledgements and
   inspect uncertain responses without canceling intent.
3. Publish the approved absolute target (zero only for a wholly disposable pool)
   with a new UUID/generation. Ensure no other publisher is changing demand.
4. Replay that latest request until safe pool convergence and completed
   retirements are confirmed. Inspect late-arriving instances before draining.
5. Independently check membership and nonterminated instances for the exact
   approved pool/scope. Detached/terminating workers are not a clean result.
6. Record what was terminated, remaining resources/costs and retained evidence.
   Terminated VMs cannot be restarted; do not imply cleanup is merely a stop.

Do not use `terraform destroy` as a pool-drain procedure. Existing pools are not
owned by the controller stack, and destruction of ledger state while
retirement is active is unsafe.

## 8. Upgrade and rollback

Pin and record image digests. Review the exact Terraform plan and request/queue/
retirement schema compatibility before applying. Back up state using a reviewed
consistent procedure and test restore without resetting generation authority.

For rollback, quiesce callers, inventory in-flight work and protect durable
state. **An older image that ignores irreversible retirement is not a safe
rollback target.** Never delete retirement entries or re-protect workers to
make an older release appear compatible.

If returning to legacy autoscaling is required, obtain a separate cutover plan:
resolve accepted retirements and OCI work first, prove the new controller cannot
mutate, reconcile operator demand and membership, then approve restoration of
one legacy writer. A paused system with retained intent is safer than two
controllers or pretending destructive commitments were canceled.

## 9. Production promotion gates

Packaging and local checks do not satisfy these gates. Assign an owner and
record acceptance or blocking follow-up for each before fleet promotion:

- Validate actual operator IAM/signing, networking, image/dependency security,
  secret rotation and state/log access. Approve operations/support ownership,
  budgets, incident response and real platform readiness/latency SLOs.
- Measure the intended pool count, worker configurations, simultaneous changes,
  sustained demand/retry/status traffic, ledger contention and OCI throttles.
  No thousand-worker/fleet throughput or five-minute latency guarantee exists.
- Adapt the sample outbox to shared transactional state; validate multi-replica
  ownership, takeover, unknown OCI outcomes and long-duration operation.
- Approve request/retirement retention, tombstone growth or sharding, consistent
  backup/restore and upgrade compatibility. No automated compaction is provided;
  do not purge active commitments or reset generation authority.
- Accept or address the bounded inline registry, shape caps and 100-exclusion
  limit. External registry design and cross-controller budget coordination are
  not implemented.
- Resolve your platform's OS-shutdown integration if STOPPED-worker cleanup is needed.
  Any worker/lifecycle-event endpoint needs separate implementation and caller
  binding; low CPU or OS-down alone is insufficient drain evidence.
- Accept or address service-limit-stalled `SCALING` with an approved OCI recovery
  procedure. The controller does not cancel unfulfilled launches, and continued
  caller ticks are not a capacity-recovery mechanism.
- Validate rollback with irreversible retirement and conduct a separately
  approved one-pool production canary before expansion, with one scaling writer.


## Appendix 1. Test harness

To functionally test the OCI Function, someone should test it in stages: **offline**, **signed read-only invocation**, **dry-run reconciliation**, then a **small disposable live pool canary**.

## 1. Run the offline tests first

From the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Also validate the Terraform:

```bash
terraform -chdir=deploy/reference init -backend=false
terraform -chdir=deploy/reference fmt -check
terraform -chdir=deploy/reference validate
```

These checks do not contact OCI or mutate resources. They validate the local controller behavior and deployment configuration.

## 2. Deploy a staging Function

Use an isolated OCI staging compartment, VCN/subnet, ledger bucket, and preferably one disposable existing instance pool.

Start with the safe settings:

```hcl
enroll_pools       = false
dry_run            = true
enable_termination = false
enable_bounded_growth = false
```

The repository recommends deploying the Function first in standby, then enrolling pools later. After deployment, capture:

```bash
terraform output function_ocid
terraform output invoke_endpoint
terraform output controller_scope_id
terraform output -json iam_review
```

The caller needs:

- An OCI user or machine identity
- Permission to invoke this exact Function
- An OCI CLI/SDK configuration and signing key
- The Function OCID
- The Functions invoke endpoint
- A persistent local outbox path

This is a direct OCI Functions invocation; no API Gateway is involved.

## 3. Perform signed read-only tests

Install the example client:

```bash
python3 -m venv .venv-client
.venv-client/bin/python -m pip install -r examples/requirements.txt
```

Check the Function’s pool view:

```bash
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint 'https://<functions-invoke-endpoint>' \
  --profile POOL_STAGING \
  --outbox /secure/state/controller-outbox.sqlite \
  pools
```

Test a known request status:

```bash
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint 'https://<functions-invoke-endpoint>' \
  --profile POOL_STAGING \
  --outbox /secure/state/controller-outbox.sqlite \
  status --request-id '<request-uuid>'
```

Verify that:

- IAM-authorized invocation succeeds.
- Unauthorized callers are rejected.
- Wrong pool keys or wrong scope are rejected.
- The application response envelope is decoded correctly.
- HTTP success is not mistaken for business success.

The Function returns an envelope such as:

```json
{
  "status_code": 202,
  "body": {
    "request_id": "...",
    "request_state": "submitted",
    "retryable": true
  }
}
```

The important status is inside `body`; transport-level HTTP 200 alone does not mean the operation succeeded.

## 4. Test demand reconciliation in dry-run mode

Submit a small demand request against the enrolled staging pool:

```bash
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint 'https://<functions-invoke-endpoint>' \
  --profile POOL_STAGING \
  --outbox /secure/state/controller-outbox.sqlite \
  demand \
  --pool '<pool-key>' \
  --target 1 \
  --generation 1
```

Then replay it:

```bash
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint 'https://<functions-invoke-endpoint>' \
  --profile POOL_STAGING \
  --outbox /secure/state/controller-outbox.sqlite \
  tick --pool '<pool-key>'
```

Repeat `tick` until the request reaches its expected terminal or retryable state.

With `dry_run = true`, the test should verify validation, ledger writes, request generation handling, and response behavior **without changing OCI pool capacity**.

Test request semantics such as:

- Replaying the same UUID and payload.
- Sending a newer generation.
- Sending an old generation and confirming it is superseded.
- Reusing a generation with a different target and confirming rejection.
- Retrying after a transient response.
- Confirming that the SQLite outbox preserves the request across client restarts.

The client requires a persistent outbox; do not delete it between runs.

## 5. Run a controlled live scale-out test

After dry-run validation, apply a reviewed Terraform change:

```hcl
dry_run            = false
enable_termination = false
```

Then submit a very small target, such as one worker:

```bash
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint 'https://<functions-invoke-endpoint>' \
  --profile POOL_STAGING \
  --outbox /secure/state/controller-outbox.sqlite \
  demand \
  --pool '<pool-key>' \
  --target 1 \
  --generation 2
```

Continue running:

```bash
... tick --pool '<pool-key>'
```

Check OCI pool membership and lifecycle state independently. Confirm:

- The pool reaches the desired non-retiring capacity.
- New instances have `InstanceTerminationProtectionEnabled="1"`.
- The Function does not exceed configured pool or aggregate limits.
- The new worker registers with the actual scheduler/runtime.
- The worker is genuinely dispatchable; `RUNNING` alone is not sufficient.

## 6. Test retirement only after drain validation

Retirement is destructive and permanent. Before testing it:

1. Stop assigning work to the selected worker.
2. Confirm its jobs and results are complete.
3. Confirm the worker is fully drained.
4. Enable termination through a reviewed IAM and Terraform change:

```hcl
enable_termination = true
```

Then issue the exact-worker retirement request:

```bash
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint 'https://<functions-invoke-endpoint>' \
  --profile POOL_STAGING \
  --outbox /secure/state/controller-outbox.sqlite \
  retire \
  --pool '<pool-key>' \
  --instance-id '<worker-ocid>' \
  --confirmed-idle-and-dispatch-disabled
```

Replay it:

```bash
... tick --pool '<pool-key>'
```

Verify that:

- Only the specified worker is selected.
- The protection tag is changed to the exact value `"0"`.
- The worker is detached and ultimately reaches `TERMINATED`.
- A busy or protected worker is not terminated.
- A committed retirement cannot be canceled by sending new demand.
- Returned demand causes a different worker to be launched rather than reusing the retiring worker.

The repository explicitly warns that a tag change or detach is not proof of termination; the exact worker must be observed in an authoritative terminal state.

## 7. Exercise the acceptance scenarios

The runbook lists the main functional scenarios to execute:

- Read-only/authentication and dry-run behavior
- Small scale-out
- Overlapping demand while the pool is `SCALING`
- Rapid demand changes such as `2 → 3 → 5 → 4`
- Independent reconciliation of two pools
- Busy-worker protection
- Permanent retirement with returning demand
- Completed-demand replay
- Stale and superseded requests
- Invalid protection/provenance/scope data
- Timeouts, `409`, `412`, `429`, and `5xx` responses
- Caller or Function restart
- Capacity/service-limit failures
- Exact cleanup of the disposable pool

These should be run in order, with evidence recorded for each test. The project’s runbook says to pause dependent tests after a failure and preserve the Function logs, request IDs, OCI work-request IDs, pool membership, lifecycle states, and outbox records.

## 8. Clean up safely

For a disposable test pool:

1. Stop job assignment.
2. Drain all workers.
3. Retire only explicitly approved worker OCIDs.
4. Publish a new demand generation with target zero if the entire pool is disposable.
5. Continue ticking until every intended worker is authoritatively `TERMINATED`.
6. Verify no detached-but-still-terminating workers remain.
7. Preserve the ledger and test evidence.

Do **not** use `terraform destroy` to clean up workers. The Terraform stack does not own the existing worker pool, and deleting the ledger during active retirement can destroy the controller’s durable safety state.

The project’s recommended test flow is summarized in [`docs/RUNBOOK.md`](https://github.com/Perseus1237/oci-pool-controller/blob/main/docs/RUNBOOK.md#L68-L127), while the command-line client is in [`examples/pool_controller.py`](https://github.com/Perseus1237/oci-pool-controller/blob/main/examples/pool_controller.py#L269-L319).