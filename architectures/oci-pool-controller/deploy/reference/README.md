# Existing-pool reference deployment

This staging module deploys the **0.12.0-rc.6 controller reference package**, not the demo tenancy. It creates one OCI Function/application, a dedicated private versioned Object Storage ledger, the initial aggregate lease object, invocation logging, and optionally narrowly scoped IAM resources. By default it also creates a private OCIR repository, builds the included Function source, and pushes the image during Apply. It does **not** create, import, resize or retag any instance pool, worker, instance configuration or network, or create an API Gateway, UI, worker terminator or readiness Function.

New stacks deploy the Function in standby by default. Existing pools and
enrollment tags are not prerequisites for that first deployment; enroll pools
later by updating the same stack.

This is unsupported sample code; see [DISCLAIMER.md](../../DISCLAIMER.md) and
[NOTICE.md](../../NOTICE.md). `0.12.0-rc.1` introduces
generic naming and `workerType` metadata while retaining scaling/retirement safeguards.

### Existing deployment migration

Existing stacks that use discovery must explicitly set `enroll_pools = true`
when upgrading. New stacks default to `enroll_pools = false`, but guards prevent
that new default from removing an existing enrollment. Existing nonempty
`pools` maps continue to take precedence over both `enroll_pools = false` and
`auto_discover_pools`, preserving an older stack's explicit allowlist. Retain
that map when upgrading; switching to discovery is an intentional
configuration change. Terraform reads each selected pool and its immutable
instance configuration to derive its real name, Intel shape, OCPUs and memory,
then checks the compartment and enrollment/protection tags. `worker_type` is
still serialized as `workerType` in `SCALE_TEST_PROFILES_JSON` and returned by
pool status. Scale requests and retirement actions retain their existing fields.

The default `name_prefix` is now `oci-pool-controller`. For an existing
deployment, explicitly retain its current prefix, `scope_id`, pool keys/OCIDs
and ledger configuration when required to avoid resource replacement or lost
ownership. Never reset generations or retirement records for a naming change.
Review the exact Terraform plan and test a compatible upgrade in staging.
This module is not an automatic migration of existing lab infrastructure.

For an existing prebuilt-image deployment, retain `build_function_image = false`, `function_image` and the matching `function_shape` to keep its image/repository path. Existing automatically built stacks should retain `build_function_image = true`; their managed repository has destruction protection. Switching build modes requires a reviewed repository-ownership migration.

The default build uses the accompanying controller Dockerfile. Deploy and test this candidate in operator staging before promotion. Worker readiness in this package remains a diagnostic bootstrap proxy. Your platform retains its own registration and dispatch readiness authority.

## 1. Prerequisites and ownership

- Review the [integration overview](../../README.md), [client example](../../examples/README.md), and [operations runbook](../../docs/RUNBOOK.md) before deployment.
- Provide the controller, network and registry compartments plus an existing VCN and Function subnet. The default standby deployment needs no pools, pool tags or pool compartment. When enabling enrollment, explicitly select an existing compartment containing the pools, their immutable instance configurations and workers. One controller targets one pool compartment and region. Use a dedicated staging worker compartment where practical.
- For the default source build, provide an OCI username and auth token with read/update access to the private OCI code repository created in the controller compartment. The deployer needs permission to create the DevOps resources and approved scoped build IAM; delivery uses that pipeline's resource principal, not the user token. The stack creates the private OCIR repository and image during Apply; neither must exist before clicking Deploy. The build uses `linux/amd64` and deploys a `GENERIC_X86` Function. For the optional existing-image path, provide the image address and matching architecture; the digest is optional. Intel **worker** architecture is independent of the Function runtime architecture.
- The existing Function subnet must have DNS and outbound connectivity to the regional OCI APIs and Object Storage. Its routing, service/NAT gateways, security lists/NSGs and available IPs are the operator's responsibility. This module does not make an invocation endpoint private merely by using a private subnet.
- Have the tenancy administrator review FaaS image/network access, controller resource-principal permissions, caller permissions, and OCI service limits. Cross-compartment images, volumes, VNICs, subnets, encryption keys or other custom launch dependencies may require additional **reviewed** permissions not inferred by this module.
- The Resource Manager execution identity needs permission to create the defined resources, including the private OCIR repository in source-build mode. Standby performs no pool listing or reads. Enrollment additionally requires permission to list/read pools in the selected pool compartment and read their instance configurations so Plan can discover and verify them.
- Protect Resource Manager stack variables, state and saved plans. `ocir_auth_token` is marked sensitive and used only for private OCI source publication, but Terraform 1.5 can retain sensitive inputs in state/plans. For local Terraform, configure an access-controlled, encrypted backend with locking and backups; this module does not prescribe one. Never commit tokens, populated `.tfvars`, state or plan files.

## 2. Deploy first, enroll pools later

### First deployment: standby

Leave **Enroll existing pools now** unchecked (`enroll_pools = false`) and the
advanced `pools` map empty. `pool_compartment_ocid` can remain blank. Terraform
does not list or read any pool, and missing pool tags do not block this deployment.
The Function, image repository, ledger and logs are created as usual. The
controller compartment supplies the initial Function configuration's compartment
value, but this does not enroll its pools or grant Compute permissions.

If the future pools already have a chosen `HarnessId` group, set `scope_id` to
that value before the first Apply. Otherwise Terraform generates and persists a
`controller-<UUID>` scope. After Apply, read `controller_scope_id`; this is the
`HarnessId` value the pools, instance configurations and launch details must use
when they are enrolled. Keep the same scope when updating the stack: it also
identifies the ledger and named resources. You cannot bootstrap one scope and
later change it to an unrelated existing group.

Signed controller status reports `awaiting_pool_enrollment`. Pool actions return
application status `409` with `controller_not_enrolled` before creating OCI clients.
Standby status is not a connectivity or resource-permission check.
This intentional standby mode is controlled by Terraform; it does not turn an
empty discovery result or an OCI permission failure into a successful enrollment.

### Later: enroll pools in the same stack

1. Read `controller_scope_id`. Have the infrastructure owner prepare the pool,
   immutable instance configuration and launch tags using that exact scope and
   the profile/protection contract below. This stack never creates or retags pools.
2. Set `enroll_pools = true`, explicitly select `pool_compartment_ocid`, and set
   `scope_id` to the saved `controller_scope_id` value. This selects the same
   controller even when the compartment contains multiple groups. Keep
   `auto_discover_pools = true` and the advanced `pools` map empty for discovery.
3. Review the new Plan's exact pool IDs, capacity limits and controller IAM
   statements. Update centrally managed IAM, or let an authorized administrator
   apply the enabled IAM resources. Enrollment needs Compute permissions that
   standby did not grant.
4. Apply to the same stack. It retains the Function, ledger, controller scope
   and other deployment resources and configures the fixed pool allowlist.
   Keep `dry_run = true` and `enable_termination = false` for the signed checks.

To enroll at initial deployment, select **Enroll existing pools now** and provide
the pool compartment. A blank `scope_id` infers the sole nonempty `HarnessId`
group among nonterminal pools; when several groups exist, enter the intended
group explicitly before the first Apply. This inference applies only when
enrollment is enabled from the start. Later enrollment must match the scope
already persisted by the standby deployment. `scope_id` is a text input, not a
live OCI group selector.

### Discovery and capacity settings

With enrollment enabled, discovery runs during **Plan**, only in the explicit
`pool_compartment_ocid`, and selects nonterminal pools matching the persisted
controller scope. `auto_discover_pools` defaults to true but has no effect in
standby. A compartment without matching enrolled pools needs preparation by its
pool owner; empty enrollment and read/permission failures remain errors.

Each pool's `ScaleTestProfile` tag becomes its profile key and default
`worker_type`. `default_pool_max_size` sets each pool's capacity ceiling and
defaults to **3**; it is not inferred from current pool size and is not a desired
size request. Leave **Customize per-pool settings** unchecked to keep the
optional override rows hidden in a new Resource Manager form. Check it only to
view or edit `pool_overrides`, keyed by the existing `ScaleTestProfile` tag (not
the placeholder `key1`). Remove unused rows completely instead of leaving an
empty key. Each entry can set a different `max_size` or `worker_type`.

The checkbox controls visibility only: saved overrides still apply when hidden.
Remove their entries to restore defaults. CLI deployments can set
`pool_overrides` directly without setting `customize_pool_settings`. For example:

```hcl
enroll_pools          = true
pool_compartment_ocid = "ocid1.compartment.oc1..REPLACE_POOLS"
auto_discover_pools   = true
scope_id             = "REPLACE_WITH_SAVED_CONTROLLER_SCOPE_ID"
default_pool_max_size = 3
pool_overrides = {
  small = { max_size = 5, worker_type = "small-worker" }
}
```

The Plan fails when the initial group is ambiguous, the selected scope differs
from the persisted controller scope, no matching pools are found, selected profiles are missing,
invalid or duplicated, or the existing configurations fail the enrollment,
shape or protection checks. Terraform does not fix these conditions by retagging
or changing pools. The infrastructure owner prepares the existing enrollment
contract below before deployment:

| Resource | Required contract |
| --- | --- |
| Pool | Existing `HarnessId` identifies the group; unique `ScaleTestProfile` identifies its profile. Terraform pins the discovered pool OCID and verifies the region/compartment and tags. |
| Instance configuration | Terraform reads the attached immutable configuration, verifies its compartment and enrollment tags, and derives its Intel shape, OCPUs and memory. |
| Launch details | Both enrollment tags and exact free-form `InstanceTerminationProtectionEnabled = "1"` so new workers are protected. |
| Existing worker | Same compartment, correct membership and enrollment tags; protected unless your platform has irrevocably finished retirement preparation. |

The Plan's `enrollment_review` and `pool_registry` outputs show the exact pool
OCIDs and limits that will be pinned in the Function-owned registry. Review every
Plan: subsequent Plans may discover new profiles, but the running Function does
not discover pools or expand its allowlist. Client input cannot expand it either.
Preserve the existing group's tags and scope when upgrading: `scope_id` also
contributes to resource/ledger naming and ownership.

After the first Apply, Terraform retains the controller scope; after enrollment
it retains each profile's pool OCID using Terraform-state identity guards. A changed group or a replacement
pool under an existing profile fails the Plan. Profile guards also have
`prevent_destroy`: removal from discovery (including tag loss or pool deletion)
cannot silently discard an existing enrollment. Intentional retirement/removal
requires a deliberate enrollment/ledger migration and review of historical
retirement and request records. Do not remove identity guards merely to suppress
an error or reuse a profile's ledger history for a different pool.

For explicit manual enrollment on a new stack, set `enroll_pools = true`,
select `pool_compartment_ocid`, set `auto_discover_pools = false`,
and use the advanced `pools` map with the profile key, exact `pool_id`,
`worker_type` and approved `max_size`; set `scope_id` to the existing group.
A nonempty legacy `pools` map always takes precedence over standby and discovery,
including on upgraded stacks that have not set the new booleans. Manual enrollment still
uses the same plan-time pool/configuration checks.

Instance configurations are immutable: if the existing launch template is missing required tags or shape settings, your platform creates a replacement configuration through its normal infrastructure workflow and associates it with the staging pool. The controller does not modify operator launch templates. Verify new workers inherit tags. Audit free-form tag capacity for the two enrollment tags and protection flag. Do not silently remove unrelated operator tags.

The inherited enrollment names `HarnessId` and `ScaleTestProfile` are retained for compatibility; they do not require running the demo. `OriginPoolId`, `DrainOperationId` and `DrainRequestedAt` belong to the legacy contrast flow and are not required by the managed retirement path, which records commitments in the ledger. Exact `"0"` commits irrevocable retirement. Boolean `false`, the string `"false"`, missing or malformed protection tags are not equivalent. There is no controller-enforced post-tag grace interval: your platform must stop scheduling and finish any required drain **before** committing `"0"`.

## 3. Choose the Function image path

### Default: build during Resource Manager Apply

Keep `build_function_image = true` (the default). Provide `ocir_username` as your OCI
domain/username, for example `Default/user`; the stack adds the tenancy name
to form the OCI Git login (not the Object Storage namespace). Provide
`ocir_auth_token` from your OCI user profile's **Tokens and keys → Auth Tokens**
section. This is an OCI auth token, not your account password.
[Oracle's token instructions](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrygettingauthtoken.htm)

**Staging candidate:** Resource Manager assigned an ARM64 host in the September
22 fresh test. Automatic builds now use native x86 DevOps. A separate fresh
GitHub-button stack passed build, private push, x86 Function deployment and
signed standby invocation on September 23 using an existing staging network.
This does not qualify worker scaling or production use. See the
[test record](../../docs/RESOURCE-MANAGER-BUILD-TEST-20260922.md).

Apply creates private source and OCIR repositories, a DevOps project/pipeline,
artifact, build log and notification topic without subscriptions. The publisher
copies only three Function files, `native_build.py` and `build_spec.yaml` from
the applied package onto a fresh private Git branch. The OCI auth token is used
only for this publication, through a temporary repository-scoped credential
helper, never in Git or the build runner.

The build selects `OL8_X86_64_STANDARD_10`, verifies packaged source checksums and
native/output `linux/amd64`, then delivers through resource-principal IAM.
Terraform waits for successful delivery and pins the `GENERIC_X86` Function to
the digest looked up in OCIR by the exact repository and unique build-run tag.
DevOps can return blank image URI/digest fields even after successful delivery;
the stack does not depend on those fields or substitute an unrelated image.
No local engine, prebuilt image, GitHub PAT or manually
prepared pipeline is required. Resource Manager host architecture is irrelevant.

`create_build_iam_resources` defaults to true, separately from runtime IAM. Its
home-region dynamic group matches only this pipeline. Policies allow reading
its exact source repository, DevOps artifact metadata in the controller
compartment, OCIR repository metadata in the registry compartment, and
management of its one exact-named OCIR repository. Artifact metadata
read and repository inspect are not restricted to a single object; the live
delivery stage rejected the prior exact-artifact conditional read.
Repository management includes image/repository deletion and metadata changes;
this is not a push-only role. The build code does not delete repositories or
change their privacy. No Compute, Function
deployment or secret permissions are granted to the build. Terraform waits
180 seconds after creating/changing build IAM before submitting a build, because
DevOps reads its specification before pipeline stages run. This is not a guarantee
of IAM convergence; review authorization failures rather than broadening IAM.

For a partial Apply that failed with `can't evaluate field OSType` or Podman's
`--config` warning, update the same stack's Terraform configuration with the
corrected package. Preserve its state, variables and existing resources. Use
`deploy/reference` for a generated Resource Manager ZIP, or
`oci-pool-controller-main/deploy/reference` for GitHub `main.zip`. Review a new
Plan: the build step is replaced/retried, while existing infrastructure should
be retained. Do not Apply unexplained bucket, repository, application or logging
replacements. The GitHub deploy button creates a new stack, not a recovery of an
existing stack. An error before source publication leaves the supplied token
untested; registry delivery separately validates the pipeline's IAM.

Upgrades from the early native-build candidate that included
`oci_devops_build_pipeline_stage.iam_propagation` need a two-step graph migration:
first point the BUILD stage's predecessor directly at the build pipeline and
set its nonempty description; then remove the unused WAIT stage. Do not delete
the pipeline or its artifacts to recover this. OCI refuses to delete a stage
while another stage references it, and Terraform can attempt that deletion
before rewiring its successor. Review the exact stage IDs and preserve state;
re-plan after any approved Console/API predecessor correction. New stacks have
no legacy WAIT stage and do not need this migration.

The build runs again when the included Function source or build helper changes.
An unchanged configuration reuses the existing built image. Failed build retries
receive a new tag, so this build workflow does not reuse a previous build's tag.
Tags are not registry-enforced immutable: other authorized pushers could replace
them, so restrict registry write access. The stack-created
repository has `prevent_destroy` protection so changing image modes cannot
silently delete the running Function's image. Choose the mode at stack creation;
later mode changes require a deliberate repository-ownership migration.
Mutable base-image tags and dependency downloads mean separate source builds
are not guaranteed to produce identical bytes. Choose an existing pinned image
when you need to deploy the same previously built artifact.

The stack deliberately omits `is_immutable` rather than requesting repository
immutability. OCIR can reject this optional provider field during repository
creation with `400-BAD_REQUEST, Setting isImmutable is not currently supported`.
This is separate from repository privacy, Terraform deletion protection and
the Function's resolved image digest. If an existing stack failed with that
error, update its Terraform configuration with this corrected package, retain
its state and inputs, then review a fresh Plan before Apply. Do not delete the
stack or previously created resources to retry; a failed Apply may have already
created other resources. This change does not require a public repository or
a prebuilt image, and does not change the repository's resource address/name.

### Optional: use an existing private OCIR image

Set `build_function_image = false`, then set `function_image` to an operator-owned
private OCIR image address including its tag. This skips repository creation and the source build, so
`ocir_username` and `ocir_auth_token` are not needed. Set `function_shape` to
match the image (`GENERIC_ARM` / `linux/arm64` remains the default in this mode).
Set `function_image_digest` to pin its reviewed `sha256:` digest, or leave it
blank for OCI to resolve the tag during deployment. Use an accompanying-source
image, not a historical demo image.

The optional command below builds and publishes an image from the release root
on a workstation or CI runner with an authenticated Podman session:

```sh
export POOL_IMAGE="iad.ocir.io/OCIR_NAMESPACE/OCIR_REPOSITORY:release-2026-09-15"
python3 scripts/build-publish-function-image.py --image "$POOL_IMAGE" --output-dir dist
```

The command builds the accompanying Function source, pushes it, captures the
registry-returned immutable `sha256:` digest, and creates a Resource Manager ZIP
with `build_function_image = false` and the image address, digest and architecture
pre-filled for visible review.
It never accepts OCI terms, uploads the ZIP, creates a stack or applies
Terraform. Authenticate only through the operator's approved CI secret store or
credential helper; never copy a demo auth token or pass credentials on a
command line. Use `--architecture amd64` only with a reviewed `linux/amd64`
image; it selects `GENERIC_X86`.

## 4. Review configuration and IAM

In Resource Manager, configure the stack form. For local Terraform, copy `terraform.tfvars.example` to local `terraform.tfvars` and replace every placeholder; provide the source-publication token through a protected input, not a committed file. Keep `dry_run = true` and `enable_termination = false` initially. Choose operator-owned pool/profile and aggregate OCPU ceilings from staging budget and service-limit review; raising ceilings does not demonstrate that the controller can sustain that fleet size. Capacity guards remain enabled and include retiring capacity.

The pool registry is currently inline Function configuration. [OCI limits combined Function/application configuration to 4 KB](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionspassingconfigparams-about.htm); this module conservatively rejects serialized configuration at approximately 4,000 bytes, including UTF-8 metadata. Raising `max_profiles` cannot override that service limit. Long pool identifiers/names reduce how many profiles fit. An external registry is not implemented. Separately reviewed, non-overlapping controller shards are an option, but each needs its own scope, ledger and budget allocation—there is no cross-controller aggregate guard. Do not add unreviewed application-level configuration outside Terraform.

Controller runtime IAM creation defaults to **off** (`create_iam_resources = false`). Build IAM is independent (`create_build_iam_resources = true` for automatic builds). With runtime IAM off:

1. The administrator preinstalls the two FaaS statements shown in `main.tf`, replacing the network/registry compartment placeholders. They must exist before creating the Function application/image.
2. Deploy the Function with both safety switches unchanged.
3. Read `terraform output -json iam_review`. Have the administrator create the named dynamic group with the exact Function-OCID matching rule, controller statements and approved caller statements. Allow IAM propagation before testing.
4. If the administrator chooses a different dynamic-group name, set `dynamic_group_name` to that exact name and regenerate the statements. Never attach another resource to the controller's dynamic group.

Alternatively, an authorized tenancy administrator may set `create_iam_resources = true` to create this module's dynamic group and policies. The module does not create caller users, groups, API keys or passwords. Supply only existing approved `invoker_group_ocids`. With no configured groups it creates no caller invocation policy; an administrator can instead provide a reviewed workload-principal policy for the exact Function.

**Configure caller groups** is unchecked by default and only controls whether
the optional group editor is visible. Leave it unchecked to deploy first and
configure caller permissions later. Saved group OCIDs remain active when the
editor is hidden; clear their entries and review the Plan to remove stack-managed
grants. CLI users can set `invoker_group_ocids` without enabling this display toggle.
Blank, whitespace-only and null rows are ignored, including blank rows recreated
by Resource Manager. Surrounding whitespace is trimmed and duplicate OCIDs are
collapsed. Every nonblank value must still be an IAM group OCID beginning
`ocid1.group.`, not a group name, user, compartment or dynamic-group OCID.
An empty list creates no caller policy and does not permit unauthenticated invocation.

For an existing Resource Manager stack showing the old group validation error,
update that stack's Terraform configuration from the revised repository/package
and run a new Plan. Publishing to GitHub does not update an existing stack's
uploaded configuration. Retain the stack's existing state and deployment inputs;
do not recreate the stack to resolve an empty input row.

Standby omits Compute permissions from the controller's IAM statements. Enabling
pool enrollment changes those statements: review and apply the updated policy,
including centrally managed policies when IAM creation remains off, before
testing enrolled operations.

Permissions are bounded to specified compartments, with Object Storage writes limited to this bucket and without object-list/delete authority. Runtime Compute rights are **not individually IAM-bound to each pool OCID**; the code's pinned allowlist is an additional safety boundary. The controller needs pool updates and launch dependencies for scale-out. Targeted detach/delete dependencies are added only when `enable_termination = true`; centrally managed IAM must be updated manually at that point. Custom image/network/volume placement must be reviewed rather than broadening to tenancy-wide `manage all-resources`.

When pool instances and their subnet are in different compartments, review
both launch-preflight and actual network-resource permissions. The controller
statements include `SUBNET_ATTACH` in the pool compartment and restricted VNIC
create/read/attach/detach/delete permissions in the network compartment. A live
cross-compartment launch failed with `Missing instance launch permissions`
until these dependencies were present. Centrally managed IAM must incorporate
these statements too; updating Function configuration alone does not fix IAM.

## 5. Deploy dry-run and perform signed checks

### Resource Manager option

For the public one-click launch, use the button in the repository root README.
It downloads the GitHub `main.zip`, whose full Terraform working directory is
**`oci-pool-controller-main/deploy/reference`**. That directory contains the
included `schema.yaml`, which groups the required compartments, subnet, registry
build credentials, optional existing image, pool discovery, Object Storage ledger,
and safety controls. No real tenancy values are
bundled. In Resource Manager's **Stack information** page, select Terraform
**1.5.x** before selecting **Next**. The module supports the Resource Manager
1.5.x runtime (CLI 1.5.7) only; selecting a blank or retired version produces
an `Invalid Terraform version` error.

For a version-pinned package using the same default source build, run
`python3 scripts/package-reference.py` from the repository root. For the
optional package prefilled with an existing image and digest, use
`scripts/build-publish-function-image.py` above. Host the generated Resource
Manager ZIP through a read-only Object Storage PAR. The package has no GitHub
archive-root directory, so its exact
Terraform working directory is **`deploy/reference`**. A PAR launch URL must
include `&workingDirectory=deploy%2Freference`.

Select the compartments, Function VCN and Function subnet from the form. The
subnet selector is filtered by the selected network compartment and VCN. Provide
the OCI source-publication username/auth token for the default build, or use the optional existing
image inputs. Leave **Enroll existing pools now** unchecked to deploy in standby;
the pool compartment and discovery settings are not needed yet. Set `scope_id`
before the first Apply only if you have a chosen future group; otherwise use the
generated `controller_scope_id` for later enrollment. When enrolling, choose the
pool compartment and per-pool maximum, optionally customize individual profiles,
and review the exact discovered pool IDs in the Plan. The
dedicated Object Storage ledger bucket is created automatically; leave its
optional name blank unless you need a reviewed fixed name. The Resource Manager
execution identity needs permission to create the defined resources and read
the selected subnets; pool/configuration read permissions are needed only during
enrollment. Runtime Function IAM is
a separate requirement. Enter the source-publication token only in the sensitive
`ocir_auth_token` input; restrict access to stack variables, state and plans.

Deselect **Run apply**, create the stack, run a **Plan**, and review it before
applying. A successful plan is not a runtime test. After apply, perform signed
status and dry-run checks below. Do not enroll a pool into a second live writer.
The packaged root configuration is identical to this module; do not manage the
same deployed resources from both local Terraform state and a Resource Manager
stack.

### Local Terraform option

From `deploy/reference`, after configuring the approved Terraform backend and OCI deployer credentials. Source-build mode requires Python 3, Git and OCI source-publication credentials locally, plus permission to create the native x86 DevOps build workflow. It does not need a local container engine. The existing-image path requires neither source-publication credentials nor a build pipeline:

```sh
terraform init
terraform fmt -check
terraform validate
terraform plan -out=reference-staging.tfplan
terraform show reference-staging.tfplan
terraform apply reference-staging.tfplan
terraform output function_ocid
terraform output invoke_endpoint
terraform output controller_scope_id
terraform output -json iam_review
```

Review the saved plan: only the Function/application, dedicated ledger/lease, logs, source-build repository/build action when selected, and explicitly enabled IAM should be created. There must be **no** worker/pool/network mutation. Protect and dispose of saved plans according to operator policy.

For a standby deployment, verify signed status is `awaiting_pool_enrollment`.
Complete section 2's later-enrollment steps before the pool dry-run and cutover
checks below.

Use the accompanying integration client with the Function OCID and OCI signer. This deployment sets `CONTROLLER_ONLY=true` and `AUTH_MODE=oci_iam`: the OCI InvokeFunction front door verifies the signed caller before dispatch. The application does not trust a caller-supplied `Authorization` header as evidence of OCI identity and requires no shared demo bearer token. A signed direct invocation returns a JSON `{status_code, body}` envelope over successful Function transport; inspect the **business** `status_code`, `body.retryable`, and outcome, not just transport HTTP 200.

Treat any permission to invoke this Function as full controller-operation authority across its enrolled pools. Do not put an unauthenticated gateway or other broadly authorized trigger in front of it, do not grant worker identities invocation rights, and do not expose the raw FDK server. API Gateway/service invocation would act with that service's authority, not automatically the end user's identity. The controller-only mode disables the browser frontend, legacy contrast mutations/reset, worker reclaim, and benchmark readiness mutation routes. Managed protection `"1"` is permitted only before commitment; re-protecting a committed worker is rejected. A control plane hosted outside OCI still needs an approved OCI signing identity and credential lifecycle; its host-platform identity alone does not establish OCI invocation authority.

For IAM syntax and the signed service invocation boundary, see [Oracle's Function access-control documentation](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsrestrictinguseraccess.htm) and [invocation documentation](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsinvokingfunctions.htm). Invocation logs use the OCI Functions `invoke` category as described in [Oracle's logging example](https://docs.oracle.com/en-us/iaas/Content/Logging/Task/functions_eg.htm).

## 6. Controlled cutover

1. Start with one disposable, enrolled staging pool at target zero. Read pool status through the signed client; verify pool identity, immutable template, protected launch tags and configured limits.
2. Submit and inspect a dry-run scale-out request. Dry-run prevents Compute/tag mutations but may create request/coordination ledger records. Use a **new generation and request ID** when making a later live demand change; do not assume a previously completed dry-run request will execute live.
3. Pause the legacy policy writer and, with operator approval, export and remove its attached OCI Autoscaling configuration **for this pool**, then verify absence. A configuration that remains attached is rejected even when disabled. The scheduler remains authoritative for demand; only this controller may mutate pool capacity. Do not leave two resize writers active.
4. Set `dry_run = false` while retaining `enable_termination = false`, review/apply the Function configuration, and test small protected scale-out. Confirm actual platform registration/dispatchability independently.
5. After drain integration acceptance, enable targeted termination through both configuration and IAM, review/apply, then execute the [ordered staging checks](../../docs/RUNBOOK.md). A worker committed for retirement must never receive another job.
6. Use your periodic maintenance loop: send new monotonic generations only for changed demand; retry/replay the identical latest request as needed. Status reads do not advance reconciliation. Request IDs and generations must survive the caller's restart. Scale-out while old workers retire is deliberately retire-first, not surge replacement.

## Stop and rollback

Stop scheduler writes first, set `dry_run=true` and `enable_termination=false`, and apply the reviewed safety-only change. These switches do not cancel an OCI operation already accepted. With centrally managed IAM, revoke detach/delete authority separately when required by the incident procedure. Preserve all ledger versions, desired generations and retirement commitments.

Before switching back to another scaler, reconcile actual OCI membership, committed retirements and latest demand with your platform; never re-protect or reuse committed workers. Do not run two writers during rollback. Pin any rollback image digest and verify that version understands the existing ledger schema and retirement semantics; old demo versions are not a safe generic downgrade.

`terraform destroy` is **not worker drain or safe rollback**: this module does not own workers, and the ledger/lease have `prevent_destroy` guards. Removing those guards, deleting state, recreating the ledger or importing another environment's state requires an explicit retention/migration review. Retain operator operational records; no automated history pruning is supplied.

## Bounded-growth preview supplement

The controller changes and required staging gates are documented in
[bounded-growth acceptance](../../docs/BOUNDED_GROWTH_ACCEPTANCE.md).
`enable_bounded_growth=false` remains the default; `max_total_vms` and
`launch_timeout_seconds` apply to the preview. Enabling it requires a rebuilt
image containing this source, not just new Function environment variables.
Existing Terraform state and the Object Storage ledger must be preserved.
