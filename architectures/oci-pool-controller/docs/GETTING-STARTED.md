# OCI Pool Controller: environment preparation and getting started

Prepared September 18, 2026. Reviewed source: `52528e6ffee7fc08511c1116db0ad4bacadade43` in [Perseus1237/oci-pool-controller](https://github.com/Perseus1237/oci-pool-controller/tree/52528e6ffee7fc08511c1116db0ad4bacadade43).

**Status: preparation guide for a staging reference implementation, not production certification.** The September 23 correction passed a fresh GitHub-button stack's x86 build/private push, Function creation and signed standby invocation using an existing staging network. No workers were launched; see the [deployment test record](RESOURCE-MANAGER-BUILD-TEST-20260922.md). The repository describes this as unsupported sample code.

## 1. Understand what you are deploying

The published stack is a **controller deployment into existing infrastructure**, not a complete test-environment installer. There are two separate milestones:

1. **Controller deployed:** Function, private ledger and logs exist, the correct image runs, and an authorized caller can invoke it.
2. **Ready to test scaling:** prepared worker pools are enrolled, runtime permissions work, and the caller can publish demand and continue reconciliation.

| Component | Customer prepares/selects | Stack creates |
| --- | --- | --- |
| Compartments, VCN, subnet and routing | Yes, before deployment | No |
| Deployment identity and permission approvals | Yes | No |
| Controller container image | Supply one, or supply credentials for automatic build | Repository and image in automatic-build mode only |
| Functions application and controller Function | Select placement | Yes |
| Dedicated private, versioned ledger bucket and invocation logs | Select compartment | Yes |
| Runtime and caller IAM | Administrator reviews and either provisions centrally or enables stack creation | Optional |
| Worker OS image, instance configurations and pools | Yes, before enrollment | No |
| Caller/test client and continued reconciliation | Yes | No scheduled reconciler or runner host |
| API Gateway | Only if an additional authenticated HTTP interface is required | No; unnecessary for the supplied signed client |

**Three different images:** the controller is a Functions container image; a worker image boots a VM; the customer job container runs on that worker. A job container cannot be entered as the controller image.

You do not install OCI Resource Manager on a machine. Use the managed service in the OCI Console. The GitHub deployment button does not require a customer-hosted ZIP bucket. The ledger bucket created by the stack serves controller state, not package hosting.

## 2. Record region, ownership and limits

Have the environment owner record:

- Tenancy OCID, tenancy home region and target deployment region.
- Controller, network and registry compartment OCIDs. An isolated staging environment can use the same existing compartment for all three; they need not be three new compartments.
- Worker compartment OCID when pools will be enrolled. This controller manages one worker compartment in one region.
- Approved caller identity, IAM administrator, test owner and cleanup owner.
- Worker shape, OCPUs, RAM, maximum concurrent workers, test duration and spending bound.

Check quotas, service limits and available capacity independently of the controller's limits. For our test environment, **25 concurrent workers is the ceiling, not a deployment target or an OCI-wide limit**. Start the first live smoke test with one worker.

**Checkpoint:** the operator can select the intended compartments and region in the Console. Do not substitute a different tenancy or region merely because a selector is empty.

## 3. Prepare the network

Reuse approved networking when available. Otherwise, ask the network owner to create this small staging foundation in **Networking → Virtual cloud networks**:

1. Create a VCN with an approved, non-overlapping CIDR and DNS enabled.
2. Create a private regional subnet for the Function, with sufficient free IP addresses. Record its OCID and the VCN OCID. The current form expects selected Function subnets in the selected VCN and network compartment.
3. Configure a Service Gateway for **All <region> Services in Oracle Services Network**, with a matching subnet route to that gateway. Confirm that the controller's required regional API, registry and Object Storage endpoints are reachable through the approved design.
4. Review security-list/NSG egress for required HTTPS destinations and DNS. Do not add public inbound access to worker VMs or broad SSH access just to deploy the Function.
5. Add a NAT gateway, approved proxy or other egress path only if workers or other components need public repositories or external services. A Service Gateway is not general internet access.
6. Prepare an appropriate worker subnet before creating pools. It can use the same VCN; separate Function and worker subnets make ownership and access rules clearer.

The Function's private subnet does **not** itself make its invocation endpoint private. The native DevOps image build uses a separate managed runner, not this Function subnet. Fixing subnet routing does not fix a build-host architecture problem.

**Checkpoint:** network owner confirms routes, DNS, available IPs and security rules. Later authenticated Function calls must verify actual service access; looking at routes alone is not an end-to-end test. See [Oracle Service Gateway guidance](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/service-gateway_management.htm) and [Function network security groups](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsusingnsgs.htm).

## 4. Arrange IAM in the correct order

There are four separate permission paths. Giving the operator access to Resource Manager does not automatically grant the Function access to Compute.

| Identity | Required purpose |
| --- | --- |
| Deployer / Resource Manager job | Read selected networking and create the stack's Function, ledger, logs and optional registry resources; read pools/configurations at enrollment |
| FaaS service | Use the selected networking and pull the private controller image |
| Controller Function resource principal | Access its dedicated ledger and, after enrollment, approved Compute/network/volume dependencies |
| Scheduler or test caller | Invoke the controller Function; this conveys the controller's allowlisted scaling authority |

**Centrally managed IAM path, with `create_iam_resources=false`:**

1. Before creating the Function, have the administrator install the two FaaS statements from the reviewed module, with real compartment OCIDs:

   ```text
   Allow service FaaS to use virtual-network-family in compartment id <network-compartment-ocid>
   Allow service FaaS to read repos in compartment id <registry-compartment-ocid>
   ```

2. Deploy in dry-run with termination disabled.
3. Read the stack's `iam_review` output. It supplies the dynamic-group name, exact Function-OCID membership rule, and generated runtime/caller policies. Have the administrator apply those in the tenancy home region. Do not substitute a dynamic group covering every resource in a compartment.
4. If using existing caller groups, enter their **group OCIDs**, not names or user OCIDs. With no approved invoker configured, deployment does not grant a new caller access.
5. Allow IAM propagation and perform a signed invocation. Review and update runtime IAM again when enrolling pools and when enabling termination.

**Stack-managed IAM path:** an authorized administrator can opt into runtime IAM creation. The candidate discovers the tenancy home region and uses that endpoint for IAM writes. The current Ashburn test alone does not qualify a Phoenix deployment/Ashburn home-region combination; retain that separate acceptance case.

Custom worker images, subnets, encrypted volumes or keys in other compartments can require additional specific permissions. Do not solve dependency failures with tenancy-wide `manage all-resources`. Runtime Compute permissions are compartment-scoped; the controller's fixed pool allowlist is an additional application boundary, not per-pool IAM isolation.

## 5. Choose how the controller image is supplied

### Path A: automatic build, intended one-click experience

**Fresh-stack deployment passed:**  The  stack uses an explicit native x86 OCI
DevOps runner and passed build/private push/deploy/standby invocation through
the GitHub button. The Docker/Podman compatibility fix alone was insufficient.
See the [test record](RESOURCE-MANAGER-BUILD-TEST-20260922.md).

Select **Build and deploy the Function automatically**. Prepare:

1. An OCI user with approved read/update access to the new private OCI DevOps code repository, plus deployer permissions to create build resources and approved build IAM.
2. The domain/username, for example `Default/user@example.com`. The form adds the tenancy name; do not prefix it yourself.
3. An OCI auth token. In the Console, open your profile, then **Tokens and keys → Auth Tokens → Generate token**. Save it securely immediately; existing token values cannot be displayed later. Do not delete an existing token without confirming its dependencies. [Oracle token instructions](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrygettingauthtoken.htm)

Enter the token only into the sensitive stack field. It is not the Console password or the caller's API-signing credential. Restrict access to variables, state and saved plans because sensitive Terraform inputs can remain there.

The stack creates the native x86 DevOps pipeline and private source/OCIR
repositories. The auth token publishes only the packaged build inputs through
a temporary, repository-scoped Git credential helper; it is never committed or
passed to the build runner. Delivery uses the pipeline's scoped resource-principal
IAM. The build verifies packaged checksums, native x86 and `linux/amd64` output,
then Terraform waits for delivery and pins the Function to the delivered digest.
`create_build_iam_resources=true` creates build-only IAM in the tenancy home
region. This is separate from controller runtime IAM and grants no Compute access.
Only this pipeline receives the grants: exact-source read, compartment-scoped
DevOps artifact metadata read and OCIR repository metadata inspect, plus image
read/update restricted to its one private OCIR repository.
Automated builder tests do not establish end-to-end one-click success: retain
the exact revision, successful build/push, deployed digest and signed invocation
results from a real Resource Manager Apply before making that claim.

If Apply already created the ledger, repository, application or logs and then
failed during image build, **update that existing stack**, keeping its state and
variables. Review a fresh Plan before applying; do not delete/recreate those
resources or click the deploy button to recover them. A failure before source
publication does not validate the auth token; image delivery separately tests
the pipeline's OCIR permissions. See the
[build and recovery instructions](../deploy/reference/README.md#default-build-during-resource-manager-apply).

### Path B: existing reviewed controller image

Use an approved image built from the controller source and stored in same-region OCIR. Disable automatic build and provide:

- Full image address including tag.
- Matching image architecture: **`GENERIC_X86` for our x86 image**. The current manual-image default is ARM, so explicitly change it.
- Reviewed `sha256:` digest, recommended to pin the artifact.
- Repository compartment and FaaS image-pull permission.

Registry push credentials are not required in this stack path. If the customer has no controller image, their build owner must create one first or use the corrected automatic-build path. Merely having a worker or job image is insufficient.

For a build owner using an authenticated native-x86 Linux Podman runner, the repository supplies this helper. From the reviewed repository root, replace the example image address:

```sh
python3 scripts/build-publish-function-image.py \
  --image '<regional-ocir-host>/<namespace>/<private-repository>:reviewed-release' \
  --architecture amd64 \
  --output-dir dist
```

The runner needs Python, Podman, registry push permission and dependency-download connectivity. Authenticate through the approved credential workflow, never a token in the command line. The helper produces a pinned image and Resource Manager package; it does not deploy them. This alternative avoids the Resource Manager build-host dependency but is not evidence the automatic path works.

## 6. Fill out the new-stack form

Click the repository's **Deploy to Oracle Cloud** button. Confirm the downloaded revision before testing because `main` can move. Select Terraform **1.5.x** for this reviewed module.

| Form field | Initial selection |
| --- | --- |
| Region and tenancy | Approved target region and tenancy OCID |
| Controller / network / registry compartments | Values recorded in step 2 |
| Existing Function VCN and subnets | Prepared resources from step 3 |
| Build automatically | Path A, or uncheck for Path B |
| Create scoped build IAM | On for Path A after review; independent of controller runtime IAM |
| OCI source-publication username / auth token | Path A only; used for private OCI Git publication, never sent to the builder |
| Existing image / digest / architecture | Path B only; explicitly use `GENERIC_X86` for x86 |
| Enroll existing pools now | Unchecked for first deployment if pools are not prepared |
| Controller group | Leave at generated default for a new group; use an existing planned scope only deliberately |
| Dry-run | Checked |
| Enable targeted termination | Unchecked |
| Bounded-growth preview | Unchecked initially; separate staging acceptance required |
| Create reviewed IAM policies and dynamic group | Off for central IAM; on only after administrator review and deployment fix |
| Configure caller groups | Enter approved existing group OCIDs if using that policy path |
| Resource name prefix | Unique, valid prefix for this new environment |
| Optional dynamic-group / ledger names | Prefer generated defaults; explicit empty-string handling is an outstanding Console validation check |
| Run apply | Unchecked so the Plan is reviewed first |

Do not change an existing deployment's prefix, controller scope, pool keys or ledger as a casual retry. Those identify existing resources and history. Hiding the caller-group or per-pool override editor does not erase previously saved entries.

Capacity controls are independent. The discovered per-pool maximum defaults to **3**, active-pool ceiling to **1**, aggregate OCPU ceiling to **16**, and bounded-growth VM cap to **25**. Increasing one does not increase the others. For example, 25 workers at 2 OCPUs each need an OCPU allowance of at least 50 if all count concurrently. Retiring workers and pending launches must remain accounted for. Do not increase limits simply to bypass an unexplained failure.

For manual upload, upload the **generated Resource Manager ZIP**, not a developer handoff bundle. Its Terraform working directory is `deploy/reference`. The GitHub source ZIP instead has `oci-pool-controller-main/deploy/reference`. No customer-hosted bucket is necessary for a Console file upload.

## 7. Plan, apply and verify controller deployment

1. Create the stack with automatic Apply off, then run Plan.
2. Confirm the source version and intended Function/application, ledger, logs, optional repository and reviewed IAM. No existing worker, pool or network should be altered by this reference module.
3. Investigate unexpected replacements or failures. Retain state after a failed Apply because some resources may already exist.
4. Apply only the approved plan.
5. Record `function_ocid`, `invoke_endpoint`, `function_image_digest`, `controller_scope_id`, `iam_review` and ledger outputs.
6. Complete central runtime/caller IAM if using that path. Verify the Function is ACTIVE and the deployed digest is the intended artifact.
7. Invoke the read-only `scale_test_status` action with the approved signed client. An unenrolled controller intentionally reports `awaiting_pool_enrollment`; this proves neither Compute access nor readiness to scale. The legacy `status` action is intentionally rejected by controller-only deployments.

**Checkpoint:** separately record deployment success, signed invocation success and remaining enrollment work. A successful Terraform Plan alone is not any of those runtime results.

## 8. Prepare and enroll one disposable worker pool

Use a new staging pool, not a production pool, for the first acceptance run.

1. Copy the saved `controller_scope_id`.
2. Select a compatible x86 worker OS image, bootstrap method and subnet. For synthetic tests, arrange an observable synthetic job/drain mechanism; a booted VM alone does not prove job protection. The reviewed package currently permits `VM.Standard3.Flex` and `VM.Optimized3.Flex` workers. AMD support is separate implementation/qualification work.
3. Create an immutable instance configuration with the approved shape, OCPUs, RAM, image, boot volume and networking. Set these free-form tags on the configuration and on its instance launch details:

   ```text
   HarnessId = <saved-controller-scope-id>
   ScaleTestProfile = staging-small
   InstanceTerminationProtectionEnabled = "1"
   ```

4. Create a zero-sized instance pool from that configuration. Tag the pool with the same `HarnessId` and `ScaleTestProfile`. Use a different profile key for every pool. Check that newly launched workers inherit protection and both enrollment tags.
5. Do not attach a native autoscaling configuration or allow a second writer to change this pool. The controller rejects attached autoscaling configurations even if disabled. Migration of an existing pool needs its own approved cutover.
6. Update the **same controller stack**: enable enrollment, select the worker compartment, retain the saved scope, and enable discovery. Set the per-pool maximum to a small approved value.
7. Review discovered pool IDs, configuration, ceilings and added Compute IAM. Apply with dry-run still on and termination off.

The protection flag is a controller contract, not universal protection from every OCI termination operation. A direct manual termination or another authorized writer can bypass the controller. Limit those access paths operationally.

**Checkpoint:** the registry contains only the intended pool. Signed checks can read its real configuration, membership and tags without permission errors. An empty registry is not a passed scaling test.

## 9. Prepare the caller and run staged acceptance

On an approved workstation or runner, download the reviewed repository. From its root:

```sh
python3 -m venv .venv-client
.venv-client/bin/python -m pip install -r examples/requirements.txt
```

Configure the approved OCI signing profile separately. Keep credentials and the persistent outbox in an access-controlled location. Substitute actual non-secret values below; the endpoint is the Functions **base invoke endpoint**, not a URL ending in `/actions/invoke`:

```sh
.venv-client/bin/python examples/pool_controller.py \
  --function-id '<function-ocid>' \
  --endpoint '<invoke-base-endpoint>' \
  --profile POOL_STAGING \
  --outbox '<protected-state-directory>/controller-outbox.sqlite' \
  pools
```

The same connection/outbox arguments precede each subcommand:

| Phase | Subcommand / check |
| --- | --- |
| Demand | `demand --pool staging-small --target 1 --generation <next-generation>` |
| Continue work | `tick --pool staging-small` |
| Inspect request | `status --request-id <persisted-request-uuid>` |
| Commit a drained worker | `retire --pool staging-small --instance-id <exact-worker-ocid> --confirmed-idle-and-dispatch-disabled` |

Use strictly increasing per-pool generations from durable state, not a guessed starting value. Retry the same decision with the same UUID and payload. Status polling does not advance reconciliation; the caller must continue ticks/replay with bounded backoff and alerting. The included SQLite outbox is a single-host example, not a distributed production scheduler.

Accept in stages:

1. Dry-run input validation, signed access, wrong-pool rejection and denied unauthorized callers.
2. With explicit live-test approval, turn dry-run off, retain termination disabled, publish a new live demand decision and grow to one protected worker. Record request-to-RUNNING and separately application-ready timing.
3. Verify a protected worker is retained under lower demand. Do not use a generic pool-size decrease as the scale-down test.
4. Enable targeted termination through a reviewed stack/IAM change. Stop job assignment, finish synthetic work and persist its result before retirement. The client writes exact string `"0"`; deleting the tag or setting `false` is not equivalent. Committed retirement is irreversible under this version's contract.
5. Publish the intended lower target and continue ticks. Verify the exact retired worker is removed and no other worker is lost.
6. Test grow-during-retirement only after separately accepting the bounded-growth preview and its caps. Default retire-first behavior does not establish immediate growth during retirement. Follow the repository's [bounded-growth acceptance gates](https://github.com/Perseus1237/oci-pool-controller/blob/52528e6ffee7fc08511c1116db0ad4bacadade43/docs/BOUNDED_GROWTH_ACCEPTANCE.md).
7. Return disposable pools to zero using safe retirement, verify workers and associated disposable storage are cleaned up, and disable mutations. Preserve ledger/history and Terraform state under the agreed retention policy.

Inspect the controller's business `status_code`, outcome and retryability, not only the invocation's HTTP 200. Record every unrun check as untested, not passed. Infrastructure convergence is not DNAnexus worker registration or scientific-result integrity.

## 10. API Gateway is optional

The supplied client signs direct OCI Functions requests. It can run outside OCI with approved connectivity and an OCI signer. No Gateway is required for that path. [OCI invocation methods](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsinvokingfunctions.htm)

Add Gateway only for an agreed additional HTTP API requirement. It needs frontend authentication/authorization, narrowly scoped Gateway-to-Function permission and tested business-error mapping. A public anonymous route to this controller is unsafe. Gateway does not automatically solve AWS-to-OCI identity or credentials.

## 11. Known deployment issues before publishing this as a supported quickstart

| Issue in reviewed source | Required disposition |
| --- | --- |
| Home-region IAM | Candidate discovers the tenancy home region for IAM writes; qualify same/different deployment-region cases |
| Docker/Podman build compatibility | Offline engine/auth/architecture tests pass; automatic deployment uses native DevOps rather than building on the Resource Manager host |
| Resource Manager assigned an ARM64 build host | Native x86 DevOps passed fresh GitHub-button build/private delivery/deployment/standby invocation in Ashburn; other environments remain to be qualified |
| Manual-image default is ARM | Require explicit architecture matching and actionable mismatch checks |
| Switching build modes removes a protected managed repository from configuration | Implement a reviewed retention/migration path; do not bypass `prevent_destroy` |
| Optional names submitted as explicit empty strings fail validation | Normalize optional blanks and test real Console serialization |
| No worker-pool/bootstrap creation mode | Add an opt-in test-environment module, or supply a tested companion provisioning package |

The existing guide contains many of these prerequisites, but it is not an end-to-end empty-tenancy quickstart. For the customer's desired experience, provide two explicit modes: **use existing infrastructure** and **create a disposable test environment**. The latter should provision the missing network foundation when requested, worker configuration, zero-sized pool, tags and enrollment, then output copy-ready signed test commands. It should not silently launch billable workers or enable termination.

Before calling this customer-ready, have someone unfamiliar with the project follow the released guide and package in a clean environment, with no undocumented administrator rescue. Retain evidence for both automatic-build and prebuilt-image paths, same/different home region, blank/default form inputs, deployment, first grow, protected scale-down and cleanup.

Repository evidence: [builder regression tests](../tests/test_resource_manager_image_build.py)
and [prior focused live controller results](SCALING_STATE_ACCEPTANCE_20260917.md).
The prior controller tests are not evidence of a fresh one-click deployment.



