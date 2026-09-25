# Bounded-growth controller preview and acceptance

Implementation date: 2026-09-16. The original results below are an **offline
implementation snapshot**, not production qualification. The subsequent
[2026-09-17 isolated live acceptance report](LIVE_ACCEPTANCE_20260917.md) records
the actual OCI tests, IAM correction and remaining gaps; use that report for
current live status. The later [SCALING-state qualification](SCALING_STATE_ACCEPTANCE_20260917.md)
records operation-specific API probes and the removal of the blanket growth gate.

## Changes

- Opt-in `enable_bounded_growth` / `ENABLE_BOUNDED_GROWTH` (default `false`).
  New demand can launch without waiting for every committed termination, when
  the pool is RUNNING or SCALING and physical VM/OCPU headroom exists. Attached retiring
  instances never satisfy new usable-capacity demand.
- At most one exact detach and one detached-worker termination per replay.
  Detach decrements pool size with `is_auto_terminate=false`; a later replay
  terminates that exact, durably committed identity using its instance ETag.
  Committed retirement remains irreversible across desired generations.
- Durable aggregate capacity record at
  `coordination/bounded-capacity-v1.json`, with conditional writes before each
  launch. Count the maximum of pool target, reported current, member count and
  reserved target, plus detached retirees. Apply VM, OCPU, active-pool and
  per-profile physical limits together. Partial growth uses available slots.
- Unknown launch outcomes retain their reservations across invocation restarts
  and new desired generations; no second update is submitted until verified.
  A positively acknowledged launch can be extended to a higher absolute target
  during SCALING. Its reserved, unmaterialized slots already satisfy demand:
  only the additional deficit is reserved. The original timeout and work-request
  history remain. Explicit pre-admission rejection of an extension restores its
  prior reservation; uncertain outcomes retain the larger target. Old records
  without the boolean `accepted` marker retain conservative wait-for-verification
  behavior. Never edit this marker manually or clear reservations on timeout.
- `CONTROLLER_LAUNCH_TIMEOUT_SECONDS` (default 900) bounds launch observation.
  A failed work request, unavailable diagnostics or an exceeded deadline returns
  `intervention_required=true`, `retryable=false`, and available work-request
  IDs/error codes. Server logs record an intervention warning. The sample
  client stops automatic maintenance for that demand and raises
  `ControllerRejected`; the platform must connect this to its alerting.
- Permanent limit/quota, invalid-parameter and known host-capacity errors are
  distinguished from transient OCI failures. Raw service error messages are
  not exposed in responses.
- Malformed protection values (including arrays/maps) return 400 instead of
  500. Both default and preview completion require actual RUNNING instances.
  Infrastructure/boot readiness is explicitly not scheduler dispatchability.
- The subsequent live-test correction waits when pool target and membership
  counts disagree unless the shortfall belongs to positively acknowledged growth.
  A stale member list after size decrement must not create a false deficit.
  Historical Terminated membership summaries are excluded only after independent
  Compute confirmation; unreadable or still-terminating identities fail closed.
- Subsequent hardening recognizes the OCI SDK's wrapped request exceptions as
  retryable and logs bounded exception type/classification. Unknown exceptions
  remain terminal; an observed cleanup failure's original cause is unconfirmed
  in the linked live report, so this is not a clean production-acceptance claim.
- Status includes held launch reservations in effective VM/OCPU counts.
  Status remains read-only; callers must replay demand to drive progress.

## Safety contract and activation

This is an opt-in staging protocol. Growth may proceed in SCALING, including a
monotonic extension of a positively acknowledged launch. Unknown launch outcomes,
inconsistent membership, failed work requests, exhausted budgets and nonmutable
lifecycle states still block it. Exact detach retains a RUNNING-only guard:
both detach variants returned OCI `409 IncorrectState` during active growth in
the explicit live probe. Growth after combined detach-and-terminate was accepted
in SCALING. These are operation-specific observations, not a universal promise
that every mutation is accepted in every SCALING phase. Completion still requires
RUNNING workers and completed retirements; submission is not scheduler readiness.
The legacy/default path remains retire-first; the opt-in flag has not changed.

Before activation:

1. Agree on the irreversible drain contract: stop assignments, finish jobs and
   publish results before `tag_value="0"`. Never reuse/re-protect a committed
   identity. A workload epoch/reversible-drain protocol is not implemented.
2. Establish one pool-sizing, membership, protection-tag and ledger writer.
   Remove native autoscaling. Separate controller shards require disjoint
   pools and independent allocated budgets. Out-of-band workers are outside
   this enrolled-pool budget; this is not a tenancy-wide quota enforcer.
3. Quiesce callers and finish/verify existing OCI operations before switching
   from the old protocol. A previously lost legacy launch response cannot be
   reconstructed automatically. Preserve and back up ledger and Terraform state.
4. Rebuild the Function image from this source; match image/Function CPU
   architecture. Start in dry-run and review the stack Plan. Defaults remain
   `dry_run=true`, `enable_termination=false`, `enable_bounded_growth=false`.
5. Approve exact staging resources, cost/window, VM/OCPU and per-profile limits.
   Preview VM cap defaults to 25; that is a configurable safety guard, **not**
   the external tester's daily OCI resource-creation limit. An OCPU ceiling of
   280 must be explicitly reviewed/set; it is not enabled by these changes.
6. Verify exact-detach and Compute termination permissions and work-request
   diagnostics with the deployed principal. The later live run added narrow
   cross-compartment launch dependencies to the stack, documented in its report.
   OCI work-request detail/error access inherits resource-operation permissions;
   a generic `read work-requests` grant is insufficient. A proposed expanded
   diagnostic grant was blocked by automatic security review and was not
   installed. Have a security owner approve a least-privilege policy separately.

OCI's detach API has no instance-tag ETag precondition. The final tag read can
catch a change before that read, but **cannot eliminate an independent writer's
change between read and detach**. The separate terminate call does use instance
ETag. Tests do not claim to solve the external-writer race or stale workload
callbacks on reused workers; those require the ownership/drain contract or a
separately designed workload protocol.

## Reproduce local acceptance

Results from this implementation run: **45/45 controller acceptance tests
passed; 113/113 tests passed across the full repository suite.** The seeded
acceptance case executes 300 demand/retirement transitions. Terraform 1.5.7
configuration validation passed with the locked OCI provider. No live tests
were run; these totals do not include or re-certify the external handoff's tests.

From the checked-out repository root:

```sh
python3 -m unittest discover -s tests -p test_controller_acceptance.py -v
python3 -m unittest discover -s tests -v
terraform -chdir=deploy/reference validate -no-color
```

The acceptance suite exercises the real Function request handler and the real
client's outbox handling against self-contained OCI service doubles. It does
not use the external handoff's unpublished hotfix/harness code. Tests cover:

- Reburst during termination, attached commitments and one-slot partial growth
  at 24/25 VMs; zero headroom; tighter OCPU/per-profile/active-pool caps.
- Cross-pool contention for the last slot; unobserved reservations in another
  pool; lease expiry and reservation CAS failure.
- Accepted-but-lost and unknown launch responses, lost detach response,
  replay/new generation, supersession and request-ID payload conflict.
- Full detach → separate termination → reburst → convergence sequence; cleanup
  while a launch is unresolved; protected/excluded/wrong-scope workers;
  termination kill switch, instance ETag conflict and re-protection rejection.
- Failed work requests, quota rejection, diagnostic access denied, launch
  deadline, stopping client maintenance, STOPPED readiness, malformed input.
- Dry-run, read-only status, protocol rollback/enrollment-change rejection,
  default retire-first compatibility, and 300 seeded mixed-demand transitions
  with VM/OCPU accounting assertions at every transition.

The original doubles allowed RUNNING after detach; the expanded tests also
exercise SCALING growth. The separate live reports provide service evidence,
not the doubles. No offline test can
prove launch latency, scheduler registration, IAM correctness or job safety.

## Live acceptance checklist — subsequent results in linked report

Prerequisites: operator-confirmed OCI profile/tenancy/region, staging Function
and pool OCIDs, built image digest/architecture, VM/OCPU cap and test window,
drain ownership approval, and diagnostic IAM acceptance. Do not substitute an
unrelated logged-in tenancy for the handoff's staging environment.

1. Start small: two protected workers, complete synthetic jobs, then retire one
   exact worker. Record detach acceptance, pool target/membership/state changes,
   termination start/end and work-request IDs.
2. Submit fresh demand while the detached worker remains terminating. Require
   a new launch to be accepted before that termination completes **when OCI
   permits the pool update and approved headroom exists**. Record request-to-
   launch and launch-to-scheduler-ready latency against an agreed SLO. Record
   pool state and the actual API result; SCALING alone is not a failed requirement.
   If safe growth cannot be admitted before termination completes, report that
   limitation explicitly rather than claiming immediate reburst.
3. Exercise no headroom, then exactly one freed slot; under the agreed 25-VM
   test guard, verify global physical+reserved accounting never exceeds 25
   or the OCPU cap. Daily creation quotas are a separate environmental limit.
4. Race requests from two pools for one slot. Test client/function restart,
   lost responses, throttling, stale ETags, partial/failed OCI work requests,
   denied diagnostics and operator intervention without losing reservations.
5. Keep a real synthetic job on a protected worker throughout; verify no
   assignment after irreversible drain, no job loss, correct results, and
   scheduler-ready checks independent of OCI RUNNING/boot markers.
6. Clean up only explicitly approved committed workers. Confirm no detached
   leftovers, no unresolved reservations, and return to approved safe switches.

## Recovery, migration and remaining work

`intervention_required` means stop automatic retry, alert, and inspect pool,
members, retirement registry, reservation, work requests and actual instances.
Reservations remain held even if the request is no longer retried. Never erase
them just to resume growth. Successful authoritative observations can clear a
launch reservation on a reviewed replay; failed/unknown outcomes require an
audited reconciliation/migration procedure, not an automatic TTL refund.

Once activated, the ledger records the pool identities and profile sizes/OCPUs.
Changing enrollment or turning off the new protocol is rejected rather than
silently dropping accounting. Old images do not understand this record: **do
not roll back the image** while it owns outstanding work. A fully automated
operator recovery/migration tool is not implemented. Dry-run can stop Compute
mutations without deleting ledger history, and termination has a separate
kill switch; neither switch completes pending work by itself.

Remaining gates at the original offline snapshot: live same-pool reburst and IAM, agreed scheduler readiness/SLO,
workload epochs if reuse is required, and performance/load validation beyond
bounded staging. Fleet-scale polling optimization/tombstone sharding and the
separate deployment-hotfix consolidation remain outside this controller change.

Primary API references: [OCI detach options](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/updatinginstancepool-detaching-an-instance-from-an-instance-pool.htm),
[Compute SDK](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/client/oci.core.ComputeClient.html),
[work-request SDK](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/work_requests/client/oci.work_requests.WorkRequestClient.html),
[Compute IAM permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/corepolicyreference.htm).
