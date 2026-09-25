# Explicit SCALING-state qualification — 2026-09-17 UTC

Status: **focused SCALING-state qualification passed**, including deployed-Function
growth/replay/reburst and unattended controller cleanup. Final environment checks
passed at **15:47:23.758 UTC**. No operator recovery was needed in this focused run.
This is isolated staging evidence, not production or DNAnexus scheduler approval.
It supplements, rather than replaces, the earlier
[bounded-growth live acceptance](LIVE_ACCEPTANCE_20260917.md).

## Question and result

Does OCI itself prohibit all changes to a SCALING pool, or did the controller
unnecessarily serialize them? The answer is operation-specific. Direct API
target increases succeeded while the pool reported SCALING. Both exact-detach
variants failed with `409 IncorrectState` during active growth. The blanket
RUNNING gate was therefore replaced for bounded growth, not blindly removed
from every operation.

## Isolated scope

- Ashburn, previously approved dedicated acceptance resources, existing private
  subnet, no public IPs or IAM expansion. Two baseline workers were read-only.
- Worker shape: x86 VM.Standard3.Flex, 1 OCPU / 4 GB. ARM Function, OCI SDK 2.185.1.
- Hard physical ceiling 25 including the two baseline workers; Function budget
  remains 23 VMs / 23 OCPUs. Targets in this focused test were at most six;
  detached retirees still consumed capacity in addition to the target.
- Raw API probes ran with the Function in dry-run, termination disabled, and
  exclusive operator ownership. Controller acceptance uses the Function only;
  no raw pool mutations run concurrently with it.
- Existing state and ledger history were retained. No existing stack Apply,
  Git commit/push, or change to the public default activation flags.

## Direct API evidence

State below is a GetInstancePool observation immediately before each mutation,
not an atomic assertion about the service's internal admission instant. Mutation
SDK retries were disabled, and OCI responses/request IDs retained privately.

| Probe | Observed state | Response and result |
| --- | --- | --- |
| Detach only during growth, repeated | SCALING | 409 IncorrectState; service message required Running. |
| Detach with `is_auto_terminate=true`, repeated during growth | SCALING | Same 409 IncorrectState. |
| Raise target 5 to 6 during pending growth | SCALING | 200 at 15:30:08.937; converged to six RUNNING workers at 15:32:34.184. |
| Raise target 4 back to 6 just after a native 6-to-4 shrink | SCALING | 200 at 15:20:55.621; six workers observed RUNNING afterward. This very early reversal did not establish that committed bulk terminations had begun. |
| Exact combined detach-and-terminate from six workers | RUNNING | 202 at 15:22:31.977; pool target decremented to five. |
| Raise target 5 to 6 after that combined detach | SCALING | 200 at 15:22:36.025; at 15:23:16.600 inventory simultaneously showed five RUNNING, one PROVISIONING and one TERMINATING test worker. |

An initial unchanged-target update also returned 200 in SCALING. It is explicitly
**not** counted as evidence of growth; the later 5-to-6 increase is the real test.
The raw combined-detach overlap peaked at nine total physical workers including
the two baseline workers. An accepted higher target does not promise immediate
parallel provisioning or DNAnexus job readiness. The growth-during-growth probe
also observed intermediate convergence before the final extra worker appeared.

## Code and safety changes

In `function/func.py`, the opt-in bounded-growth path now:

1. Permits growth in RUNNING or SCALING, but not stopping, stopped, starting,
   provisioning, terminating, terminated, or unknown pool states.
2. Durably records a launch as `accepted=false` before submission, then changes
   it to `true` only after a successful API response. Missing legacy markers
   remain conservative. An unknown response, failed work request, inaccessible
   diagnostics, or observation timeout still blocks another launch.
3. Allows a positively acknowledged launch to extend to a higher absolute target
   when that target still matches OCI. Previously reserved/unmaterialized slots
   satisfy demand; the controller reserves only the incremental deficit. Same
   demand retries cannot duplicate growth. Reduced demand waits for the pending
   operation before choosing exact retirement victims.
4. Retains aggregate VM/OCPU/profile limits, detached-retirement accounting,
   conditional ledger writes, leases, desired-generation fences, OCI ETags,
   stable retry tokens, and the original launch observation deadline.
5. Restores the previous reservation if OCI explicitly rejects an extension.
   Unknown outcomes retain the larger reservation. If the final preflight
   changes before any API submission, only the known-unsent increment is
   released; callers retry instead of requiring operator recovery.
6. Keeps the operation-specific RUNNING requirement for exact detach, reporting
   `pending_reason=detach_requires_running`. Separate termination of an already
   detached worker can still progress while the pool is SCALING.
7. Keeps membership consistency checks. A short member list is permitted only
   when backed by acknowledged pending growth; overfull/stale lists still defer.
   Historical `Terminated` member summaries are excluded only after independent
   Compute confirmation. A still-terminating or unreadable instance is not
   treated as released capacity.

The last change was prompted by the live native-shrink probe: OCI retained two
Terminated summaries after target four and four live workers had converged. The
raw harness was stopped, those lifecycles inspected read-only, and its accounting
corrected before resuming. No termination was inferred just from a missing entry.

The legacy/default retire-first path remains unchanged. Activate the updated
behavior with `enable_bounded_growth=true` and a rebuilt image after staging
review; merely changing variables on an old image does not update its code.

## Automated acceptance

`python3 -m unittest discover -s tests -q`: **135 tests passed**, including
66 controller acceptance tests using OCI service doubles (not 135 live tests).
New cases cover SCALING growth, the retained detach restriction, other lifecycle
states, physical limits, stale membership, acknowledged target extension,
duplicate/reduced demand, lost responses, reservation restoration, deadline
retention, failed work requests, compatibility with old ledger records, and
authoritatively confirmed terminated summaries. Existing protected-worker,
conditional-write, last-slot contention and seeded transition tests remain.

## Deployed image and Function acceptance

- Image digest: `sha256:fff873defd9b6daafb8439b0c84e86562644cdec337f5e2875733bb106b5349c`.
- Function source SHA-256: `6e4b9651343330fe1380fc4cdddb2f26d813db21e340c675ad65333cd85c1021`.
- Source fingerprint/import and SDK version verified inside the built container;
  deployment changed only the isolated acceptance Function, initially in safe mode.
- At 15:37:02.940 generation 30 submitted target three. At 15:37:13.650,
  generation 31 submitted target six while the pre/post observations were
  SCALING. The controller counted three reserved VMs despite zero materialized
  workers and reserved only the additional three slots.
- Same-demand replay returned `launch_pending` without advancing reservation
  sequence 15 or issuing another growth operation. The Function reported
  completed at six usable workers at 15:39:44.394.
- This also directly exercised the historical-membership correction: the first
  invocation saw six old Terminated summaries but correctly counted zero live
  workers after Compute confirmation. No ledger edit was needed.
- Generation 32 detached one eligible worker at 15:40:07.507. Generation 33
  first deferred at 15:40:17.035 because target/membership were inconsistent,
  then submitted target six at 15:40:35.672 while that exact worker was being
  terminated. The pre-invocation state for this successful call was RUNNING;
  this case proves termination overlap, not a second SCALING-admission claim.
  At 15:40:58.874 a snapshot contained five protected RUNNING workers, one new
  PROVISIONING worker and the detached TERMINATING worker. All five protected
  identities remained running and protected throughout the checked sequence.
- Generation 33 completed at 15:42:59.443. Generation 34 then cleaned up all six
  synthetic workers through the controller: 12 reconciliation calls, zero errors,
  zero replacement launches, and completion at 15:46:58.083. No raw Compute/pool
  recovery or ledger edits were needed during the Function acceptance phase.

## Final verification and retained resources

At 15:47:23.758 UTC, independent read-only checks confirmed:

- Zero live test workers, zero test boot volumes, zero active pool members.
- Both pool targets zero and RUNNING; no outstanding launch reservations.
- The same two baseline workers still RUNNING, untouched.
- Function `DRY_RUN=true`, `ENABLE_TERMINATION=false`; VM/OCPU budget still 23
  for the test controller, leaving two slots for the baseline within the cap of 25.
- Current image/source match the digests above. The local builder VM is stopped.

An independent sampler recorded **146 observations** from 15:33:01.953 through
15:47:41.180, covering raw-probe cleanup and the entire Function acceptance phase.
Maximum observed physical count was **nine**, including terminating workers and
the two baseline workers. The earlier raw probes also recorded their own inventory
snapshots; their observed maximum was nine. The sampler was stopped after verification.

Disposable test workers and their boot volumes were permanently deleted. The
acceptance Function/application, empty pools/configurations, scoped IAM, private
image tags, ledger/history and logs remain. Retained storage/logging may have cost.
Credentials were neither printed nor added to packages. Changes are local and
uncommitted on main; the public Deploy on OCI button does not yet contain this fix.

## Evidence and limitations

Private operator evidence, excluded from packages: `.local/scaling-api-evidence.jsonl`,
`.local/live-acceptance-state.json`, `.local/worker-cap-scaling-samples.jsonl`.
The API journal records before-state, responses, OCI request/work-request IDs,
and inventory; Function evidence records exact payloads, results and snapshots.
Only sampled observations are claimed, not continuous or tenancy-wide enforcement.

This focused run does not repeat the complete 25-worker contention matrix,
prove behavior at 300 workers, validate x86 Function images, or measure DNAnexus
registration/job-start SLOs. The prior unattended-run error and broader production
qualification gates remain documented in the earlier report. Combined detach
and termination was tested directly, but the bounded controller continues to
use separate termination; changing that retirement protocol is not part of this fix.
