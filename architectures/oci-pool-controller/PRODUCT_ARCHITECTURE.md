# OCI Pool Controller Reference Implementation: architecture and event flows

## Product outcome and release boundary

Keep your scheduler's demand calculation and worker lifecycle ownership. Replace the
OCI autoscaling-policy update with a direct, retryable controller call for each
migrated pool. The controller launches capacity and retires only explicit,
eligible worker identities; it does not choose a busy worker to satisfy a lower
target.

The control plane can run in OCI, another cloud or your own environment.
Keep its existing worker runtime and add a durable periodic reconciliation loop.
The loop calls this OCI Function instead of the previous pool-capacity writer.
Event-driven demand publishing can follow without changing the API contract.

This is a **reference architecture for review and staging**, not a production
qualification. Release `0.12.0-rc.1` introduces generic naming and `workerType`
profile metadata while retaining scaling and retirement safeguards. Validate the image,
configuration, signed transport and real application workers in staging. Start at
the package [README](README.md) and read the [sample-code disclaimer](DISCLAIMER.md).

## 1. Non-negotiable lifecycle contract

| Question | Contract |
| --- | --- |
| Who calculates demand? | Your scheduler, using its demand/capacity model. Any worker reuse must occur before retirement commitment. |
| What does `desired_size` mean? | Absolute desired **non-retiring capacity**, not physical pool size and not an increment. It includes provisioning usable capacity; it does not mean ready workers. |
| Who decides a worker is safe? | your platform stops assignment and drains the selected worker before committing retirement. |
| What commits retirement? | `set_pool_protection` with exact string `"0"` persists the commitment before writing the tag. Direct tag-0 writes are discovered and committed on reconciliation. |
| Can returning demand cancel retirement? | No. Never re-protect or reuse a committed worker. New demand needs different worker identities. |
| Does tag `"0"` immediately delete the VM? | No. It commits intent; a caller-driven `reconcile_pool` attempt advances termination. |
| What is the replacement policy? | **Retire first, no surge**: confirm pending committed workers terminated before launching replacements. This creates a possible capacity gap. |
| What owns execution progress? | your platform's retry/maintenance loop. Status queries are read-only; there is no autonomous ledger consumer. |

The canonical tag is `InstanceTerminationProtectionEnabled="0"`. The current
implementation does **not** treat `"false"`, boolean false, numeric zero,
whitespace, missing tags, or malformed values as equivalent. Converting an
an existing platform's `false` convention requires an explicitly reviewed adapter or
code change. All new workers must start protected (`"1"`).

Brief worker reuse is allowed **only before retirement commitment**. A retiring
worker remains unavailable even if someone changes its tag back out of band:
the conflicting tag blocks deletion, but does not cancel the commitment.

## 2. Deployment and call path

```text
 Your scheduler / control plane                      OCI: execution plane
 ┌────────────────────────────────────┐              ┌──────────────────────────────┐
 │ Scheduler / Cloud Manager           │              │ IAM-authenticated invocation │
 │ - running + waiting jobs            │ signed HTTPS │ of one control Function      │
 │ - pool/type mapping                 ├─────────────►│                              │
 │ - worker busy/idle ownership        │              │ Validate caller boundary,    │
 │ - stop-assignment barrier           │              │ pool allowlist, payload,     │
 │                                    │              │ scope, operational limits    │
 │ Periodic replay loop + thin client  │              └──────────────┬───────────────┘
 │ - UUID per logical request          │                             │
 │ - increasing desired_generation    │                             ▼
 │ - persist latest absolute demand   │              ┌──────────────────────────────┐
 │ - retry same payload + UUID        │              │ Private Object Storage ledger│
 │ - replay latest after completion   │◄─ status ────│ requests / latest generation │
 └────────────────┬───────────────────┘  (read only)  │ per-pool lease + fence        │
                  │                                  │ irreversible retirement IDs  │
                  │ real worker registration           │ fleet budget coordination    │
                  │                                  └──────────────┬───────────────┘
                  │                                                 │
                  │                                  ┌──────────────▼───────────────┐
                  │                                  │ Bounded reconcile attempt    │
                  │                                  │ latest demand wins           │
                  │                                  │ retirement survives demand   │
                  │                                  │ pool/config/tag/ETag checks  │
                  │                                  └───────┬───────────┬──────────┘
                  │                                          │           │
                  │                          no retirees; grow│           │retire exact ID
                  │                                          ▼           ▼
                  │                              UpdateInstancePool   DetachInstance
                  │                              larger target only   decrement=true
                  │                                                   auto_terminate=true
                  │                                          │           │
                  │                         ┌────────────────▼───────────▼─────────┐
                  └─────────────────────────┤ Existing allowlisted OCI pools       │
                                            │ protected VM -> bootstrap -> work  │
                                            │ drained VM -> detach -> TERMINATED  │
                                            └─────────────────────────────────────┘
```

The reference module deploys one controller and its supporting ledger/logging;
it references existing pools, instance configurations, and subnets. It does not
create demo pools, browser ingress, a worker watcher, or a readiness service.
The function image retains shared source, but controller-only mode disables
demo and legacy routes. Operator invocation uses OCI IAM signing, not a browser
bearer token. Invocation permission grants authority over the controller's
whole configured pool allowlist; separate deployments/identities are needed
for finer isolation.

No private ingress architecture is implied by signed invocation. Your platform
security/network owners must approve the caller endpoint and route, OCI
service access from the Function subnet, credential lifecycle, and image pull
access. See [reference deployment instructions](deploy/reference/README.md).

Your platform owns the assignment/drain barrier, including job completion, durable
results and any storage/runtime cleanup. It also owns real worker registration,
networking and signing credentials, generation/retry ownership, capacity budgets,
monitoring and incident recovery. The Function cannot establish job safety from
a protection tag or verify application readiness on the operator's behalf.

## 3. State and ownership

| State | Source of truth | Important boundary |
| --- | --- | --- |
| Latest desired non-retiring count | your platform decision, retained in per-pool queue ledger | A newer generation supersedes older demand, not worker retirement. |
| Retirement commitment | Separate pool-OCID-keyed retirement registry | Persists across request generations and Function restarts; do not delete active entries. |
| Pool target, membership, lifecycle | Fresh OCI reads | Attached is not ready; detached is not necessarily terminated. |
| Ready/dispatchable worker | your platform worker registry | VM RUNNING or the demo bootstrap callback is insufficient proof of a usable application worker. |
| Protected/eligible state | Exact tag plus durable commitment and scope checks | Tag `"0"` is permission to retire, not proof that job draining actually happened. |
| Request outcome | Request ledger | `completed` means pool convergence, not application readiness; the latest completed request may reopen on later drift/retirement. |

The retirement registry also tracks detached-but-still-terminating instances.
They do not become usable and are not dropped from effective-capacity cost
accounting just because they leave the membership list. Terminated identity
tombstones prevent a repeated callback from resurrecting the same identity.
The current per-pool registry grows over time; retention/sharding is a fleet
hardening task, not implemented compaction.

Scope enrollment currently retains the source's tag names `HarnessId` and
`ScaleTestProfile`: these identify the configured controller scope and pool key.
They are ownership checks, not extra idle signals. Enroll existing pools,
instance configurations, launch-instance tags, and workers as documented in the
deployment guide. Never overwrite unrelated operator tags.

## 4. Actual request contract

These JSON bodies are sent to the controller Function's signed invoke endpoint.
They are not new REST routes and do not require the browser API Gateway.

New demand decision:

```json
{
  "action": "reconcile_pool",
  "request_id": "7d94d798-50a0-4afb-9001-67bb46e33ab2",
  "pool_key": "intel-large",
  "desired_size": 3,
  "desired_generation": 18421,
  "exclude_instance_ids": []
}
```

Commit one scheduler-selected, already-drained worker:

```json
{
  "action": "set_pool_protection",
  "request_id": "438fdf12-2608-47b5-9417-4fc29b160231",
  "pool_key": "intel-large",
  "instance_id": "ocid1.instance.REPLACE_WITH_SELECTED_WORKER",
  "tag_value": "0"
}
```

Read a durable reconcile request without advancing it:

```json
{
  "action": "request_status",
  "request_id": "7d94d798-50a0-4afb-9001-67bb46e33ab2"
}
```

Use a valid UUID per logical request. Each new demand decision supplies a new
UUID and a positive, strictly increasing, JavaScript-safe integer
`desired_generation` per pool. Persist both in your platform state before sending;
do not allocate generations independently in competing processes. A retry
keeps the same UUID, target, generation, and exclusions. A changed payload is
a new decision, not a retry. A superseded UUID must never become authoritative
again.

`exclude_instance_ids` accepts at most **100 instance OCIDs** and prevents
selection but does not undo a retirement.
Conflicting exclusions on a retiring worker can block convergence and must be
resolved by your platform, without returning the worker to job assignment.

Signed direct invocation returns an application envelope
`{"status_code": 202, "body": {...}}`. Transport HTTP success alone is not
controller success: the client must unwrap `status_code` and inspect
`result`, `outcome`, `request_state`, and `retryable`. A queued/submitted
response acknowledges progress, not completion. Retry transient outcomes with
bounded backoff and jitter; surface permanent validation/authorization problems.
A timeout may have an unknown downstream outcome, so replay the original
request and reconcile fresh state instead of creating another logical action.

After completion, periodically replay the **latest** demand anyway. That
permits subsequent retirements to finish and replenishes lost usable capacity
without requiring an artificial change to the numerical demand. The supplied
client is a building block, not a hosted background service. Its SQLite outbox
is a single-host example; production replicas need shared transactional state
for atomic generation allocation, outbox insertion and replay ownership.

## 5. Event flow: normal scale-out

```text
Your platform             Caller / Function                    OCI / workers
   │ calculate D=3          │                                  │
   ├─ persist UUID+gen ────►│                                  │
   │                       ├─ durable latest generation       │
   │                       ├─ scope/autoscaling/cost checks    │
   │                       ├─ no pending retirement?           │
   │                       ├─ pool RUNNING + fresh ETag?       │
   │                       ├─ UpdateInstancePool(size=3) ─────►│ launch protected VMs
   │◄─ submitted/retryable ─┤                                  │
   ├─ identical replay ────►├─ observe target/member convergence│
   │◄─ completed ──────────┤                                  │
   │◄─────────────────────────────────────────────────────────┤ register worker
   └─ only now dispatch jobs                                  │
```

If OCI is already `SCALING`, newest desired state remains durable and the caller
retries. This is not an OCI FIFO of all intermediate requested targets. Pool
keys coordinate separately, with shared operational-budget admission checks.

## 6. Event flow: retirement, including returning demand

```text
Initial: physical A,B,C; usable=3; desired=3

1. Your platform stops assigning work to A,B,C and confirms all jobs are drained.
2. For each exact OCID, call set_pool_protection(tag_value="0").
   Commitment is durable before the tag write; never cancel it after timeout.
   Physical=3, retiring=3, usable=0, desired still=3 until a new demand decision.
3. Send desired=0 with generation 100. Caller replays; exact retirement starts.
4. Before termination finishes, demand returns: desired=3, generation 101.
   Generation 100 is superseded. Retirement(A,B,C) is NOT superseded.
5. Caller replays generation 101 under the SAME request UUID.
   pool SCALING -> queued/retryable; do not submit a generic shrink.
6. When safe, detach each named RUNNING, eligible member with:
       is_decrement_size=true, is_auto_terminate=true
   Use its stable retirement token even though demand generation changed.
7. Wait until all A,B,C are authoritatively TERMINATED, not merely detached.
8. Reconcile latest desired=3; launch D,E,F. No A/B/C reuse and no surge.
9. Confirm exactly three non-retiring members, then separately observe real
   your platform registration/readiness of D,E,F.
10. Old generation 100 replay remains superseded and cannot drain D,E,F.
```

There is no need for your platform to manually subtract each retiring worker from
OCI's observed physical count. It publishes required usable capacity; the
controller decrements physical size during exact detach and later reconciles to
that desired capacity. If three workers retire but demand remains three,
replay the existing latest target-three request; replacements are still needed.

For a lower target with insufficient eligible workers, the controller returns
an eligibility shortfall and leaves protected workers alone. All committed
retirements must finish even when their count exceeds the desired reduction.
The safe result can temporarily fall below target before replenishment.

## 7. Limits and failure boundaries

Operational pool/profile/aggregate OCPU ceilings are operator-configured, but
do not raise OCI quotas or prove throughput. The supported worker shapes remain
`VM.Standard3.Flex` (1–32 OCPUs, up to 512 GB) and `VM.Optimized3.Flex`
(1–18 OCPUs, up to 256 GB). OCPU and memory values must be integers, with memory
between 1 and 64 GB per OCPU and within the shape cap; the registered pool
configuration must match. These are package validation caps, not
a statement of OCI's full shape offerings. Each request supports at most 100
exclusions; a larger list requires a separately reviewed design change.

The inline pool registry must fit the reference module's conservative Function
configuration-size guard (approximately 4,000 bytes); raising `max_profiles`
does not bypass that guard. An external registry and retirement-tombstone
compaction/sharding are not implemented. Separate
controllers must use disjoint pools and explicit budgets; their independent
guards do not provide a global fleet limit.

- **Service-limit-stalled `SCALING`:** desired state survives, but this release
  does not cancel OCI's unfulfilled launch attempts. Drain may stay blocked.
  Stop hot retries, preserve intent, alert and investigate work-request errors.
  Do not recover by forcing a generic smaller target or deleting the ledger.
- **STOPPED/OS shutdown:** managed detach currently selects `RUNNING` workers.
  A stopped retiring worker can block progress. Lifecycle-event-triggered
  termination and stopped-worker support are not implemented. Review the
  your platform guest OS shutdown sequence before staging.
- **Protection conflict:** a committed worker with a nonzero tag remains
  retiring but is not deleted until the inconsistency is safely resolved.
- **Ledger/IAM failures:** fail closed. Unknown state is not permission to
  terminate; retain request IDs and evidence for operator recovery.
- **Self-reclaim:** legacy watcher/terminator routes are disabled in controller-only
  mode. A future worker/event endpoint
  needs caller identity binding, safe eligibility evidence and the same pool
  coordinator. Low CPU or OS-down alone does not prove job-safe retirement.
- **Throughput:** no thousand-worker/pool-count SLA is established. Measure
  real pool count, target-change rate, ledger contention, OCI throttles, and
  end-to-end dispatchable readiness in your platform staging.

## 8. Validation

Follow the [deployment guide](deploy/reference/README.md), integrate the
[signed client](examples/README.md), and record the ordered acceptance checks
in the [runbook](docs/RUNBOOK.md). Qualify the exact source/image, identity,
region, pools and worker runtime. Record demand-to-dispatchable latency
separately from launch-to-ready timing; controller completion and Compute
`RUNNING` do not prove a dispatchable application worker. No five-minute latency or
thousand-worker/fleet guarantee is established by this package.
