# Fresh Resource Manager deployment test — September 22, 2026

## Outcome

**Clean GitHub-button deployment passed: native x86 build, private OCIR delivery, Function creation and signed standby invocation.**
The original Resource Manager-local build was blocked before image build/push and Function deployment.
The corrected source subsequently passed a separate fresh-stack test described below. No ARM Function was deployed: the requested
Function architecture remained `GENERIC_X86`, and the target image `linux/amd64`.

## Scope and observed results

- Created a new stack through the public GitHub Deploy button in the operator's
  currently signed-in Ashburn tenancy, using its existing staging VCN/subnet.
  This was not recovery of the customer's failed stack in another tenancy.
- Source revision: `1b1ef7d`. The form and initial Plan succeeded. Plan showed
  ten additions, no changes/deletions, an x86 Functions application, no Compute
  worker resources and no IAM grants.
- First Apply created the private repository, ledger, application and logs but
  failed at the native-x86 builder check. It did not build/push an image or
  create the Function. Registry credentials therefore remain unvalidated.
- A source-only diagnostic update preserved every stack variable and the exact
  Terraform state bytes. Its reviewed recovery Plan changed only the build
  provisioner and missing Function, leaving infrastructure untouched.
- The diagnostic Apply reported: `Detected podman via docker reports platform
  linux/arm64`. This establishes that the managed **build host**, not the target
  Function, was ARM. It is separate from the earlier Docker `OSType` template
  failure.
- A possible existing-emulation test was prepared but **not applied**. It was
  withdrawn when the operator selected the native-x86 implementation. No
  emulator was installed or used, no ARM fallback occurred, no worker launched,
  no termination was enabled and no IAM grants were added.
- All partial test resources and state were retained. No cleanup/destroy was
  run, and the older validation stack was untouched.

The test date above uses America/Los_Angeles; the OCI jobs ran on September 23
in UTC. Private stack/job identifiers and credentials are intentionally omitted.

## Native x86 correction and live retry

The operator subsequently approved native OCI DevOps build resources and scoped
build IAM, plus publication of five allowlisted source/build files to a private
OCI code repository using the existing auth token. The token is not delivered
to the build runner. Runtime IAM remains disabled and no pools are enrolled.

- Source publication succeeded, validating the token for the private code
  repository (not a direct OCIR password login).
- The first DevOps run failed when fetching `build_spec.yaml`, before entering
  any stage. An in-pipeline WAIT cannot cover this first authorization check.
- The retry succeeded at source authorization with the **same permissions**.
  This supports IAM propagation as the cause of the first failure. The correction
  delays build-run submission for 180 seconds after creating/changing build IAM;
  this is a propagation allowance, not a guarantee of convergence.
- OCI selected `VM.Standard.E5.Flex`, 2 OCPUs / 8 GB, for the explicit
  `OL8_X86_64_STANDARD_10` build stage. Source download, checksum verification,
  native Podman `linux/amd64` check, image build and output-architecture check
  succeeded. The image-build command completed in approximately 159 seconds.
- The delivery stage failed reading the DevOps deploy artifact with
  `NotAuthorizedOrNotFound`. Read-only verification confirmed the active artifact
  ID and compartment exactly matched the policy. The cause of rejection of that
  exact-artifact conditional read remains unresolved. No image was delivered and
  no Function was created or invoked. The service's broad tenancy-wide policy
  suggestion was not applied. The operator approved compartment-scoped artifact
  metadata read and OCIR repository inspect; registry writes remain exact-repo.
- The final candidate removes the redundant in-pipeline WAIT and lets OCI retain
  its canonical repository default-branch value; builds pin a separate source
  branch. Build outputs tolerate missing failed-run data. The redundant build-run
  postcondition was removed because Terraform 1.5 still emitted `Invalid index`
  after failed resource creation; the OCI provider already waits for SUCCEEDED.
  Explicit stage descriptions address a separate `Invalid description` update
  failure observed on the existing WAIT stage. Subsequent retries passed these
  migration/error-reporting checks, but still failed at registry delivery.
- The metadata-update Apply changed IAM, then stopped with `409 Conflict` because
  Terraform attempted to delete the legacy WAIT stage before rewiring BUILD. The
  exact planned BUILD predecessor/description correction was applied through the
  API. A new Plan was reviewed before continuing; the following Apply removed
  the obsolete WAIT stage successfully. This migration issue does not exist in
  a new stack, which has no legacy WAIT stage.
- The subsequent retry successfully read the artifact and attempted OCIR upload,
  confirming progress past the previous artifact-read failure. Native image build
  again succeeded (approximately 168 seconds). OCIR denied initiation of layer
  upload for the pipeline resource principal; the repository remained empty.
  Its name, compartment, namespace and privacy were verified against the policy.
  The next candidate split the same exact-repository READ/UPDATE grant into
  two flat conditions, without adding permissions or broadening repository scope.
  That retry also failed at the same OCIR layer-upload authorization check.
  Its native image build and output verification succeeded a third time
  (approximately 168 seconds); the build run ended FAILED at 07:24:38 UTC on
  September 23. No artifact digest was delivered and no Function was deployed
  or invoked. Splitting the conditions was not a successful fix. The underlying
  authorization cause is not yet conclusively isolated; further blind retries
  or broader registry write grants are not justified by this evidence.
- All 160 offline tests and Terraform validation pass. These do not establish
  live success or a clean-deployment result.
- An isolated retry changed only the artifact endpoint to the documented
  region-key form (`iad.ocir.io`), addressing the same private repository.
  Native build passed; upload failed with the same authorization denial at
  07:43:23 UTC. The endpoint change did not resolve the failure. A bounded Audit
  lookup confirmed artifact read succeeded, but did not expose the registry
  authorization decision. With explicit administrator approval, the next
  candidate tests the standard registry-management role constrained to that
  one repository, instead of filtering individual READ/UPDATE permissions.
  This includes repository lifecycle permissions; it is not tenancy-admin or
  access to other repositories. That run completed SUCCEEDED at 08:02:31 UTC;
  its image was present in the still-private repository. The specific missing
  permission within the former READ/UPDATE filters has not been isolated.
- Successful DevOps OCIR delivery returned blank `image_uri` and
  `delivered_artifact_hash` fields. Terraform's digest precondition correctly
  prevented Function creation instead of accepting an unpinned image. The
  correction queries OCIR using the exact repository OCID and unique build-run
  tag after successful delivery, accepts exactly one available matching image,
  and pins its registry SHA-256 digest. It never selects `latest`. Recovery can
  reuse the successful build; another build is not required for this lookup fix.
- The provider also dropped the delivered artifact's name/ID, although OCI's API
  returned them. The final guard uses SUCCEEDED, the matching exported build-run
  tag, an OCIR delivery marker, and the exact registry image/digest. The first
  registry-lookup recovery verified the digest but was safely stopped by the
  now-corrected name/ID check; no unverified image was deployed.

## Recovered-stack acceptance — September 23, 2026

- Reviewed recovery Apply succeeded without rebuilding the delivered image.
- Function is ACTIVE in a `GENERIC_X86` application. Its digest matches the
  exact OCIR image selected by the successful build-run tag:
  `sha256:0be23b224880de11bc424fdf83b62cc1ec6d23681f7bd5f1a918d7ca87fd6a07`.
- Packaged Function source checksums match the successful build's recorded inputs.
- Signed `scale_test_status` invocation returned transport HTTP 200 and business
  `status_code: 200`, with `result: awaiting_pool_enrollment`, zero enrolled
  pools and `dryRun: true` at 08:24:28 UTC. Termination remains disabled.
- An initial helper request used the legacy `status` action and correctly
  received business 403 `action_not_allowed_for_role`. The helper was corrected;
  no Function role restriction was removed. Its cold-start invocation took about
  63 seconds in service logs; this is not a general latency benchmark.
- All 160 offline tests pass, along with Terraform validation and packaging.
- No workers were launched. Pool operations, ledger access from the Function,
  production readiness and a clean-stack deployment are not established by this
  standby smoke test.

## Clean GitHub-button acceptance — September 23, 2026

- Created a separate empty-state stack through the public GitHub README button
  using published revision `31550ba`. The downloaded deployment and Function
  files matched the local published source byte-for-byte. Working directory:
  `oci-pool-controller-main/deploy/reference`; Terraform 1.5.x.
- Used the existing staging private Function subnet in Ashburn. This tests a
  fresh stack, not provisioning an empty tenancy or new network.
- Reviewed Plan contained 25 additions and no changes/deletions. No worker or
  network resources were created. Automatic Apply was disabled; the exact saved
  Plan was selected in the Console after approval.
- Enabled scoped build IAM and runtime IAM creation. The pipeline manages only
  its own OCIR repository, with the documented source/metadata access. Runtime
  grants cover the exact Function's own ledger objects and compartment-scoped
  FaaS networking/image reads; no pool/worker permissions or caller-group grants.
- The first Apply succeeded at 08:55:46 UTC, approximately 14 minutes after
  submission. No source replacement, manual IAM repair, retry or prebuilt image
  was required. Existing stacks and their state were left untouched.
- Native build used `VM.Standard.E5.Flex`, 2 OCPUs / 8 GB. Source download,
  checksums, native x86 image build and architecture verification passed. The
  image-build step ran from 08:47:17 to 08:50:37 UTC (approximately 200 seconds).
  Both BUILD and private OCIR DELIVER stages succeeded.
- Function was ACTIVE in a `GENERIC_X86` application. Its image digest matched
  the exact private registry image and successful build-run tag:
  `sha256:d2a5ed7fc99baeebb0e06d654b0222b9b4b59a062fb4ece2d48e4ebef23e17f0`.
  Recorded Function source checksums matched the published source.
- Signed `scale_test_status` invocation returned transport HTTP 200 and business
  `status_code: 200` at 08:57:53 UTC, with `awaiting_pool_enrollment`, zero
  enrolled pools and `dryRun: true`. Termination remained disabled. No workers
  were launched. All 160 offline tests also passed again.

## Remaining qualification

The fresh-stack automatic-build path passed in this Ashburn tenancy using an
existing staging network and an administrator deployer. This is not empty-tenancy
bootstrap, a least-privilege deployer qualification, or production certification.
Still qualify different IAM-home/deployment regions, prebuilt-image transitions,
Function ledger access, pool-operation permissions, controller scaling/recovery
and customer-scale load. The standby action returns before exercising enrolled
pool operations; creation of runtime IAM is not proof those operations work.
