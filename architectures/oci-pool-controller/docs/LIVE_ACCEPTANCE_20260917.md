# Live bounded-growth acceptance — 2026-09-17 UTC

Status: **core scenarios passed, but full live acceptance is not clean**.
An unclassified internal error interrupted final cleanup; operator recovery is
complete and the environment was verified safe at **08:14:28 UTC**. Do not
treat this run as production approval or an unattended end-to-end pass.
This report records isolated OCI staging evidence, not
production qualification or a from-scratch Resource Manager deployment test.

## Environment and boundaries

- Ashburn; new, dedicated acceptance compartment, two new instance pools,
  Function, ledger and scoped IAM. Two pre-existing lab workers were read-only.
- Hard test ceiling: **25 physical workers**, including terminating instances
  and the two existing workers. Controller budget: **23 test VMs / 23 OCPUs**.
- Workers: x86 `VM.Standard3.Flex`, 1 OCPU / 4 GB each, Oracle Linux 8.10.
  Function: `GENERIC_ARM`, Python 3.11, OCI SDK 2.185.1 / FDK 0.1.121.
  This does not qualify an x86 Function image.
- Membership-corrected image used for the repeated cap/reburst tests:
  `sha256:f4932eb3dba9ff90e3039bcd82695a11597c3c549a9046b9fc0d498d7cc0761b`.
  Runtime source fingerprint:
  `a03c8726d29ab2844b4a403fd5f940db788d495fc6f0591fe9a6f9e44950813c`.
  Final diagnostics/transport-hardening image:
  `sha256:627844b02b40ed87690daa4c50d10706d0e44737c83071efd26d685f9cb70191`,
  source fingerprint
  `f1e227bdf34dfa073953929ea0fb19fa45623ca51b0958f371276afcb3a951bc`.
  The full cap/reburst matrix was not repeated on this last image; its live
  verification covered recovery/cleanup, status and malformed-input rejection.
  The initial run used image digest
  `sha256:5493c190104fa779974fb59f6b6f3d93657f188ee0308649464db6827b5a4ded`
  and source fingerprint
  `25eacd284c611786dad06441253dbf62f8d453485ca161c057f02df1bb2fa047`.
- Existing private services-only subnet; no public IPs, new ingress rules,
  modification of the old Function, or baseline-worker mutations.
- Serial evidence writers; an independent approximately six-second sampler
  counts every non-TERMINATED instance in the two in-scope compartments.
  This is sampled evidence, not continuous observation or a tenancy-wide cap.

## Observed results

The following table describes the initial image unless explicitly noted.
Corrected-image requalification is recorded separately below.

| Case | Observed result |
| --- | --- |
| Initial safe mode, profile/configuration checks | Passed before live mutations. |
| Malformed protection values | Arrays, objects, numeric zero and null returned 400, without Compute writes. |
| Scale-down with all workers protected | Returned retirement pending; no worker detached. |
| Same-pool reburst | Passed: replacement launch accepted while an exact retired worker remained TERMINATING. |
| Protected synthetic work during reburst | Four-minute command SUCCEEDED, exit 0. Its returned checksum text was empty, so that run alone does not establish checksum correctness. |
| Run Command readiness/checksum canary | Short command SUCCEEDED and its returned SHA-256 matched. |
| Protected checksum job during capacity scaling | Four-minute job SUCCEEDED with exit 0; its script explicitly asserted the expected checksum before returning success. |
| Larger launch | Pool A converged from 3 to 21 running workers; pool B subsequently reached 1. |
| Cross-pool last-slot contention | Passed: A requested +2 and B requested +2 with one free slot. Only A launched one worker (target 22); B was queued on the aggregate budget. Both then returned capacity pending at 25 total physical workers, including repeated requests through fresh clients. |
| At-cap retirement and replacement | Passed: the detached worker continued to count while TERMINATING; replacement was blocked. After it left inventory, exactly one replacement launched and the pool converged, never observing more than 25 total workers. |
| Conflicting payload / irreversible retirement | Both rejected with 409: reused request UUID with changed desired size, and re-protection of an already committed worker. |
| Initial cleanup | Failed: one unnecessary replacement was submitted. Mutations were paused, ledger preserved, and the membership-consistency correction was deployed. See requalification below. |

### Same-pool reburst timeline

All times UTC on 2026-09-17. The protected worker was RUNNING, protection tag 1,
and its synthetic job was IN_PROGRESS before retirement began.

1. 07:03:15: controller accepted an exact detach with pool-size decrement,
   retaining the instance for separate termination.
2. 07:03:24: a replay encountered the pool's temporary busy state.
3. 07:03:26.889: fresh demand for three usable workers was invoked.
4. 07:03:33.979: controller returned `scale_out_submitted`, physical pool target
   four, including a second committed-but-still-attached retiree.
5. 07:03:36.514 and 07:03:39.202: independent snapshots after the accepted
   launch still observed the detached victim TERMINATING and the protected
   worker RUNNING. Fresh-demand-to-accepted-response was approximately 7.1 s.
6. 07:06:27: reconciliation reported completed at three usable workers with
   no remaining retirees; the subsequent snapshot confirmed convergence.

This demonstrates launch overlap in this environment, not an unconditional
promise to update a SCALING pool. The controller still obeys OCI lifecycle
restrictions and requires available physical/reserved capacity.

### Capacity-bound retirement timeline

- 07:22:55: exact worker detached; physical count remained 25.
- 07:23:25, 07:23:50 and 07:24:14: `capacity_pending`; snapshots confirmed
  the detached worker was TERMINATING and still included in the cap.
- 07:24:28: inventory fell to 24 total workers after that retirement finished.
- 07:24:39: one replacement accepted; pool A target returned from 21 to 22.
- 07:26:10: reconciliation completed with 22 A workers and one B worker,
  plus the same two existing lab workers. No extra replacement was submitted.

## Corrected-image requalification

- Deployed the membership-consistency fix in safe mode at 07:36:07, preserving
  pool identities, desired generations, retirement commitments and reservations.
- At 07:39:42, accepted returning demand while two retirees were still
  TERMINATING in post-launch snapshots (07:39:45 and 07:39:48).
- At 07:44:10, a natural OCI throttling response was reported as retryable
  `oci_throttled`; replay of the same request resumed retirement at 07:44:25.
  This is observed transient-error recovery, not controlled fault injection.
- At 07:45:58, reconciliation completed at three usable workers, with all
  earlier retirees gone and **zero unnecessary launches** during convergence.
  The new guard's specific inconsistent-read branch was not observed in this
  replay; its two service-double regressions reproduce and cover that branch.
- At 07:52:24, the last-slot contention test passed again on the corrected image.
  Pool B won this time: A remained at 21 and B reached 2. Both pools rejected
  further growth with `capacity_pending`, including fresh-client replays, at
  25 total physical workers. The earlier run had the opposite winner.
- At 07:56:06, the at-cap retirement test passed on the corrected image:
  retirement remained charged while TERMINATING, exactly one replacement was
  submitted when the slot freed, and the system returned to 25 total workers.
  Request-ID payload conflict and re-protection of a committed worker again
  returned 409 without reversing the retirement.
- The final drain directly exercised the new guard at 07:59:35 and 08:00:41:
  `pool_state_changed` deferred mutations on inconsistent target/membership
  observations; retirement resumed on the next coherent observation with no
  unnecessary launch. This is live coverage of the defect-triggering condition,
  in addition to the reproducing unit tests.
- At 08:10:10, the final protected job was verified SUCCEEDED, exit 0, after
  8m0.246s of execution. Its script asserted the expected SHA-256 internally
  before success; Run Command again returned empty text. It had been observed
  IN_PROGRESS immediately before the final multi-worker drain at 07:56:52.
- At 08:10:14, operator review confirmed no outstanding launch reservation,
  one protected attached worker, and one detached worker with a durable
  retirement commitment. New generation 15 resumed that retirement; generation
  14's terminal failure was preserved. Generation 16 retired the protected
  worker only after its checksum job succeeded.
- At 08:13:14, all test workers were gone and no launch reservations remained.
  Safe mode was restored at 08:13:15. Final-image malformed protection values
  again returned 400; final environment verification passed at 08:14:28.

## Final state and retained resources

- **0 test workers, 0 test boot volumes, both pool targets/memberships 0**.
  Test workers and their disposable boot volumes were permanently removed;
  they cannot be recovered from this run. The two original workers remain
  RUNNING and were not modified.
- Function: `DRY_RUN=true`, `ENABLE_TERMINATION=false`; launch reservations empty.
- **857 physical-count samples**, 07:03:23–08:15:30 UTC, maximum **25**;
  overlapping samplers are included. All main reburst/cap/cleanup tests fell
  inside that observation window. Sampling is not continuous observation.
- Final multi-worker cleanup exercised three inconsistent-read deferrals and
  issued **zero unnecessary launches**. Its terminal internal error still
  required explicit operator recovery; that failure is not counted as a pass.
- Retained for review/reuse: isolated compartment, empty pools/configurations,
  safe-mode Function/application, scoped IAM, logs, ledger/history and private
  OCIR image tags. Storage/logging costs can remain; this was worker cleanup,
  not deletion of all staging infrastructure.
- Retained OCIR credentials remain owner-only and Git-ignored in the permanent
  project. The separate local builder VM is stopped; the original VM was
  preserved. No commit, push, existing-stack update or Apply was performed.

## Fixes and unresolved failure discovered during live testing

### Unclassified cleanup exception — root cause unconfirmed

At 08:02:47, request generation 14 returned terminal `internal_error` while
draining the final detached worker. The test runner stopped and restored safe
mode at 08:02:49. Read-only status subsequently succeeded. The failure occurred
after the retirement-registry read; existing logs did not record its exception
type. **The exact original cause cannot be established from this evidence.**

Inspection independently found that the OCI SDK wraps some request/transport
failures as `oci.exceptions.RequestException`, which the old name-based
classifier did not recognize. Oracle also documents `RequestException` for
Python read timeouts in its [SDK troubleshooting guide](https://docs.oracle.com/iaas/Content/API/Concepts/sdk_troubleshooting.htm).
The final image recognizes that SDK class as
retryable and records bounded exception type/classification in the reconciler's
error path, without logging SDK messages, headers or request payloads. Unknown
errors still fail closed. Two new tests cover classification and redaction.
This hardening is **not proof that it fixed the observed error's root cause**.
The failed request and retirement records are retained; operator recovery uses
new reviewed generations instead of rewriting history or releasing reservations
on a timer. A repeat unattended end-to-end run and diagnosis of any recurrence
remain release gates.

### Stale membership during cleanup

At 07:29:47, scale-down to one usable worker unexpectedly submitted a launch.
OCI had exposed the reduced target before membership stopped including a
detached retiree. Combining that target with stale membership created a false
deficit. The physical cap was not exceeded, but cleanup was not correct. The
driver was stopped and the Function returned to safe mode at 07:31:48.

Two new regression tests failed against the original image source and pass
after the correction: wait whenever target and membership counts disagree,
and compute demand deficit from observed non-retiring membership. This preserves
retirement commitments and reservations; no ledger history was cleared.
Corrected-image live results and their limitations are recorded above.

### Cross-compartment IAM

Cross-compartment launch initially returned 404 with missing instance-launch
permissions. OCI authorization diagnostics identified launch preflight checks
in the instance compartment; actual VNICs belong to the subnet compartment.
The isolated policy and `deploy/reference/main.tf` now include `SUBNET_ATTACH`
in the pool compartment and restricted VNIC read/create/attach/detach/delete
rights in the network compartment. No tenancy-wide manage grant was added.
The subsequent launch succeeded. A deployment regression test covers these
statements. Centrally managed IAM must receive the same reviewed correction.

## Verification and evidence

- Repository suite: **118/118 passed**, including **49 controller acceptance
  tests** using service doubles. Those are distinct from the live cases above.
- Terraform configuration validation passed using the installed, locked OCI
  provider; this was validation only, not a stack Plan or Apply.
- Raw, private evidence: `.local/live-acceptance-state.json` (request/response
  events and exact identities), `.local/worker-cap-samples.jsonl`,
  `.local/worker-cap-cleanup-samples.jsonl`, `.local/worker-cap-final-samples.jsonl`
  (independent physical counts), `.local/function-log-evidence.json`, and
  `.local/cleanup-error-window.json` (the unresolved failure's log window).
- Private scripts pin the approved tenancy, region, test identities and cap.
  They are operator-run evidence tooling, not a portable released test harness.
- OCIR credentials remain owner-only and Git-ignored under `.local/credentials`;
  they must not be included in a release or evidence export.

## Remaining qualification limits

- Observed UpdateInstancePool responses contained no work-request IDs. Launch
  convergence was verified from pool/member/instance state; live failed-work-
  request diagnostics and denied-diagnostic behavior were **not proven**.
  The implementation retains an observation timeout, but work-request
  correlation needs further validation before claiming reliable early failure
  reporting for every OCI failure mode.
- Live transport loss inside the Function's OCI SDK, 429/5xx injection, stale
  external-writer ETag races, process death at reservation boundaries and failed
  work requests remain service-double tests, not live fault-injection passes.
- Persistent `pool_busy` / `pool_state_changed` responses still require
  caller-side age monitoring and alerts. The launch-reservation timeout is not
  a deadline for every possible pending reconciliation state.
- No DNAnexus scheduler is connected. OCI RUNNING and agent execution are not
  proof of scheduler registration, dispatch readiness or workload SLOs.
- Irreversible retirement and exclusive ownership of sizing, membership and
  protection writes remain prerequisites. Workload epochs/reversible drain are
  not implemented; an independent writer can still race the detach API.
- The 300-to-100 fleet scenario and sustained load are not qualified by this
  bounded 25-worker exercise. No upstream commit, release or stack Apply is
  implied by these tests.
