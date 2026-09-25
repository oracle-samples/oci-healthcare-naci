# OCI Pool Controller POC — Reference Implementation

A sample code reference implementation for integrating a scheduler or control plane with
OCI instance pools. It demonstrates durable desired-capacity reconciliation,
per-pool coordination, and explicit retirement of drained workers. Adapt and
validate it for your platform; it is not a production-qualified service.

> **Public-release draft — not approved for external distribution.**
> This clean-history source snapshot is prepared for release review only.
> Complete the [release checklist](RELEASE_CHECKLIST.md) before publishing,
> pushing to an external repository, or distributing source or images.

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?zipUrl=https%3A%2F%2Fgithub.com%2Fsrudos%2Foci-healthcare-naci%2Fblob%2Fpool-controller%2Farchitectures%2Foci-pool-controller%2Freleases%2Foci-pool-controller-main.zip&workingDirectory=oci-pool-controller-main%2Fdeploy%2Freference)

**One-click Resource Manager launch:** the button downloads the public `main`
branch archive and opens the OCI Resource Manager **Create stack** form. The
complete Terraform working-directory path in that GitHub archive is exactly
**`oci-pool-controller-main/deploy/reference`**. That directory contains the
Terraform configuration and `schema.yaml`; it is why Resource Manager renders
the deployment inputs instead of treating the repository root as a stack.

The form asks for the controller, network, and OCIR compartments; the Function
VCN and subnet; an OCI username and auth token; IAM options; and the optional
dedicated Object Storage ledger-bucket name. Leave **Enroll existing pools now**
unchecked to deploy the Function first. No existing pool or enrollment tags are
required for this default. During **Apply**, the stack creates a private OCIR
repository and a native x86 OCI DevOps build pipeline. The token publishes only
the allowlisted source/build files to a private OCI code repository; it is never
sent to the build runner. The pipeline builds and verifies a `linux/amd64` image,
delivers it to OCIR using its scoped resource principal, and the stack deploys
the `GENERIC_X86` Function pinned to the resulting digest. Automatic builds
require the scoped build IAM described in the deployment guide, separately from
the optional controller-runtime IAM.
You do not need to build an image or create a repository before clicking the
button. Opening the button only opens the form; it does not deploy resources.

**Configure caller groups** is optional and unchecked by default. Blank group
rows are ignored; saved real group OCIDs still apply when the editor is hidden.
An empty group list grants no invocation access. Configure approved caller IAM
before invoking the Function; see the [IAM guide](deploy/reference/README.md#4-review-configuration-and-iam).

## Sample Code Disclaimer

**Unsupported sample code—not an Oracle-supported product or service.**

**Sample Code Disclaimer**: This script is provided as a sample. Please ensure thorough testing and modify the code as necessary to meet your specific requirements.

You are responsible for security review, testing, adaptation, deployment,
operation and resulting cloud costs. No support, maintenance, update or
security-fix commitment is made unless separately agreed in writing. This code
can permanently terminate instances. Read [DISCLAIMER.md](DISCLAIMER.md) and [NOTICE.md](NOTICE.md) before use or distribution.


## Start here

Keep your existing scheduler, demand calculation and worker runtime. Replace
the pool's existing capacity writer with OCI IAM-signed direct Function calls
from your control plane, hosted in OCI, another cloud or your own environment.
Run a durable retry/maintenance loop at your chosen interval. The deployment
stack uses existing networking and creates the controller, private image
repository, ledger/logging and optionally reviewed IAM. You can deploy the
Function before preparing pools, then enroll existing pools in the same stack.

Read these guides in order:

1. [Architecture and request contract](PRODUCT_ARCHITECTURE.md): demand,
   permanent retirement, ownership, request bodies and returning demand.
2. [Reference deployment](deploy/reference/README.md): pool enrollment, image
   build, IAM/network prerequisites and reviewed Terraform deployment.
3. [Signed client integration](examples/README.md): OCI signer configuration,
   persistent outbox, demand/retirement calls and maintenance ticks.
4. [Staging acceptance and operations](docs/RUNBOOK.md): operator decisions,
   ordered canary tests, failure recovery, exact cleanup and promotion gates.

The [implementation notes](docs/implementation-notes.md) provide a practical
engineering guide to the controller API, durable caller state, scale-out,
irreversible retirement, response handling and current safety boundaries.

The opt-in [bounded-growth controller preview](docs/BOUNDED_GROWTH_ACCEPTANCE.md)
adds durable VM/OCPU launch reservations and separate exact-worker termination
so returning demand can launch while retirees remain, when OCI permits a pool
update and capacity is available. It is **disabled by default and not production
qualified**. The [isolated live acceptance report](docs/LIVE_ACCEPTANCE_20260917.md)
records the observed OCI results and remaining qualification gaps. The guide
documents activation, IAM and rollback constraints.

## Platform integration responsibilities

- Publish absolute desired **non-retiring** capacity. Persist a UUID, payload
  and increasing generation per pool before sending; retry the same request.
- Stop assignment and complete jobs, result publication and worker cleanup
  before committing an exact worker with `set_pool_protection(tag_value="0")`.
  Retirement is permanent. New workers start protected with `"1"`; the string
  `"false"` is not equivalent to `"0"`. Approve any tag-convention migration.
- The default remains retire-first/no surge with a possible capacity gap.
  The bounded-growth preview requires explicit staging acceptance. It can grow
  in SCALING with verified accounting, but cannot make committed workers reusable;
  exact detach still requires RUNNING. See the
  [SCALING-state tests and remaining guards](docs/SCALING_STATE_ACCEPTANCE_20260917.md).
- Own continued retries and periodic replay of the latest demand, including
  after completion. Status reads do not advance work. Adapt the illustrative
  single-host SQLite outbox to shared transactional control-plane state for
  multiple replicas, with one generation/replay owner per pool.
- Decode the signed invoke application's `status_code`/`body` envelope;
  transport HTTP success alone is insufficient. Use your worker registration
  and application-specific dispatchability checks to establish readiness.
- Assign platform/security owners for existing pools, scope tags, IAM, signing
  credentials, networking, images and protected state; runtime owners for the
  drain barrier; and operations owners for budgets, retries, alerts, recovery
  and rollback. Agree on actual pool count, request rate and latency targets.

Invocation permission grants authority over every pool in that controller's
allowlist. Signed invocation does not establish private network connectivity.
Each migrated pool must have one scaling writer and no attached OCI autoscaling
configuration, even a disabled one.

## Included

- Controller-only image context and commented source.
- Terraform referencing existing operator pools and networking; no demo fleet.
- Signed OCI invocation client with an illustrative durable local outbox.
- Architecture, implementation notes, operations and staging acceptance guides.
- Controller offline acceptance, deployment and packaging regression tests
  (`python3 -m unittest discover -s tests -v`).
- `RELEASE_MANIFEST.json` with the SHA-256 of every included content file.

Historical controller tests, lab assets and historical evidence are not included
in this public-source snapshot. The controller Dockerfile copies only Function source and
requirements. Terraform state, plans, populated variables, credentials, outboxes
and local attachments are not packaged.

## Deploy to Oracle Cloud with Resource Manager

See Oracle's [Using the Deploy to Oracle Cloud Button](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Tasks/deploybutton.htm)
for instructions on linking a Terraform configuration ZIP to the OCI Resource
Manager **Create stack** page.

### One-click deployment from this repository

Click the **Deploy to Oracle Cloud** button above. Its complete launch URL is:

```text
https://cloud.oracle.com/resourcemanager/stacks/create?zipUrl=https%3A%2F%2Fgithub.com%2FPerseus1237%2Foci-pool-controller%2Farchive%2Frefs%2Fheads%2Fmain.zip&workingDirectory=oci-pool-controller-main%2Fdeploy%2Freference
```

GitHub puts the source under a top-level
`oci-pool-controller-main/` directory in `main.zip`. Therefore the direct
GitHub launch must use the full working directory
**`oci-pool-controller-main/deploy/reference`**. `schema.yaml` is located at
that path and renders the Resource Manager variable form. On the **Stack
information** page, explicitly select Terraform **1.5.x** before selecting
**Next**. The module constrains Terraform to Resource Manager's supported
1.5.x runtime (CLI 1.5.7); selecting a blank or retired version causes the
`Invalid Terraform version` error.

### Deploy first, enroll pools later

The default `enroll_pools = false` deploys the Function in standby. It does not
list or read pools or require a pool compartment. Signed status reports
`awaiting_pool_enrollment`; pool actions return application status `409` with
`controller_not_enrolled` before creating OCI clients. Standby IAM grants no Compute
permissions.

If you know the future pools' existing `HarnessId` group, enter that value in
`scope_id` before the first Apply. Otherwise Terraform generates and persists a
`controller-<UUID>` scope. Save the `controller_scope_id` output: later pools,
their instance configurations and launch details must use that value for their
`HarnessId` tags. The scope identifies this controller and ledger and cannot be
changed later to select an unrelated group.

When the pools are ready, update the same stack: select **Enroll existing pools
now**, select their compartment, set `scope_id` to the saved
`controller_scope_id`, and review the discovered pool IDs and IAM changes in a
new Plan before Apply. The Function, ledger and controller scope
are retained. The stack never creates or retags pools. See the
[enrollment steps](deploy/reference/README.md#2-deploy-first-enroll-pools-later).

You can also enable enrollment on the very first deployment. In that case a
blank `scope_id` infers the sole existing `HarnessId` group in the selected pool
compartment; when there are multiple groups, enter the intended group explicitly.
That initial choice is then preserved for subsequent deployments.

### Build and deploy the Function in Resource Manager

**Staging candidate:** a fresh Console test exposed an ARM64 Resource Manager
host. Automatic builds now use an explicitly selected native x86 OCI DevOps
runner instead. A fresh GitHub-button stack passed build, private OCIR push,
x86 Function deployment and signed standby invocation on September 23 using an
existing staging network. No workers were launched; this is not production
scaling certification. See the
[deployment test record](docs/RESOURCE-MANAGER-BUILD-TEST-20260922.md).

Keep `build_function_image = true` (the default) to build from source.
The stack creates a private repository in the selected OCIR
compartment, creates a private OCI code repository and DevOps project/pipeline,
builds `function/Dockerfile` for `linux/amd64` on `OL8_X86_64_STANDARD_10`,
delivers the image using its resource principal, and deploys a `GENERIC_X86`
Function pinned to the delivered image's digest. The image address and digest
are deployment outputs, not values you need to look up. The Function's
architecture is independent of the enrolled workers' architecture.

The stack omits OCIR's optional `is_immutable` setting because the service can
reject it with `Setting isImmutable is not currently supported`. Each build
attempt uses a unique tag, but repository-enforced tag immutability is not
enabled; restrict registry push permissions. The repository remains private and
protected against Terraform deletion.

Select the compartments and existing Function VCN/subnets, then enter your
**OCI source-publication username** (for example, `Default/user`; the stack adds
the tenancy name) and **OCI auth token**. Generate a token from your OCI user profile
under **Tokens and keys → Auth Tokens** if you do not already have one. The
token's user needs read/update access to the new private OCI code repository.
For compatibility these inputs remain named `ocir_username` and `ocir_auth_token`.
[Oracle Git authentication instructions](https://docs.oracle.com/en-us/iaas/Content/devops/using/https_auth.htm)

Resource Manager publishes only five allowlisted files from the applied package
on a fresh private source branch. Its temporary, repository-scoped Git credential
helper is removed on exit. The token is never committed or passed to DevOps.
The build verifies source checksums, native x86 engine and x86 output before
delivery. Failed build/delivery prevents Function creation. No local container
engine, prebuilt image, GitHub PAT or manually prepared pipeline is needed.
Packaged source/build changes trigger a new build, with a unique tag per run.
Base images/dependencies are not fully digest locked; use the existing-image
option for a previously reviewed artifact.

`create_build_iam_resources = true` creates home-region IAM for only this
pipeline: exact source-repository read, artifact/repository metadata access in
the selected compartments, and management of its one exact-named OCIR
repository. Management includes image/repository deletion and metadata changes;
this is not a push-only grant. The build code does not delete the repository or
change its privacy. The build gets no worker,
Function-deployment or secret-read permissions. Build logs and a Notifications
topic are created, but no subscriptions or automatic triggers. Build IAM is
independent of optional controller runtime IAM. Review all additions in Plan.

If an earlier Apply failed with `can't evaluate field OSType` or a Podman
`--config` warning, update the **existing stack's** configuration with the
corrected source, retain its state and variables, then review a new Plan before
Apply. Do not recreate the ledger, repository, application or logs. Clicking the
button again starts a new stack; it does not update a preserved partial stack.
A build failure before login does not validate the OCIR auth token.

The auth token is a sensitive stack input used for OCI code publication. Terraform
1.5 can retain sensitive inputs in state and saved plans; protect Resource
Manager stack access and never commit the token, populated variables, or state.
Controller runtime IAM creation defaults to off: required FaaS policies must already exist, or
an authorized administrator can enable `create_iam_resources`. See the
[deployment prerequisites](deploy/reference/README.md#1-prerequisites-and-ownership).

### Optional: deploy an existing image

Set `build_function_image = false`, then set `function_image` to an existing
private OCIR image with a tag to skip the stack's image build and repository creation. Set
`function_shape` to match that image (`GENERIC_ARM` remains the default for this
mode). Supply `function_image_digest` to pin a previously reviewed artifact, or
leave it blank for OCI to resolve the tag during deployment. Registry build
credentials are not required in this mode.

Choose the image mode when creating the stack. The automatically created
repository is protected against destruction: switching an existing source-build
stack to manual mode requires a deliberate repository-ownership migration, not
just unchecking the box. Leaving source-build mode enabled does not rebuild an
unchanged image on every Apply.

For a package prefilled with an existing image and digest, the optional helper
below builds and publishes from an authenticated workstation or CI runner:

```sh
export POOL_IMAGE="iad.ocir.io/OCIR_NAMESPACE/OCIR_REPOSITORY:release-2026-09-15"
python3 scripts/build-publish-function-image.py --image "$POOL_IMAGE" --output-dir dist
```

The command builds `function/Dockerfile` for `linux/arm64`, pushes it, captures
the digest returned by OCIR, and creates a
`*-resource-manager-<digest-prefix>.zip` plus a non-secret image-values JSON
sidecar. The ZIP sets `build_function_image = false`, pre-fills the image address,
digest, and `GENERIC_ARM` in the Resource Manager form, and includes
`IMAGE_PROVENANCE.json` for review. It does
not create a stack, upload an artifact, accept terms, or run Terraform apply.
Use `--architecture amd64` only when the reviewed image is built for that
architecture; it pre-fills `GENERIC_X86`.

Host that generated ZIP behind an approved read-only Object Storage PAR and
open it with `workingDirectory=deploy%2Freference`, as documented below. The
image fields identify the artifact to deploy. This optional package is not
needed for the public button's default source build.

### Packaged release ZIP

Build the source archive and Resource Manager ZIP from the verified manifest:

```sh
python3 scripts/package-reference.py
```

The `*-resource-manager.zip` preserves the source layout. Its full Terraform
working directory is **`deploy/reference`** (without the GitHub archive-root
prefix). This directory contains the Terraform files and `schema.yaml` that
render the deployment form. The form uses OCI selectors for compartments, the
Function VCN and Function subnets, with subnets filtered to the selected VCN.
Pool enrollment is optional at deployment. When enabled, discovery runs during
Plan in the explicitly selected pool compartment, using the controller's
`HarnessId` scope and the pools' `ScaleTestProfile` tags. The default maximum is
three instances per pool, independently of its current size; adjust
`default_pool_max_size` or optional profile overrides for your approved capacity.
Terraform verifies the selected pools/configurations and derives each pool's
name, shape, OCPUs and memory. The dedicated Object Storage ledger bucket
is created automatically. The full source `.tar.gz` is for review, not Resource
Manager. Follow the [deployment guide](deploy/reference/README.md) first:
existing networking, an OCIR username/auth token for the default build, IAM
permissions, and deployment variables are still required. Existing pools are
required only when enrollment is enabled. The generic
package supports the same source-build path as the public GitHub button.

To host a version-pinned package privately, use an approved read-only,
single-object Object Storage pre-authenticated request (PAR) for the deployment
ZIP. Its launch URL must use the ZIP-specific working directory:

```text
https://cloud.oracle.com/resourcemanager/stacks/create?zipUrl=<URL-encoded-PAR>&workingDirectory=deploy%2Freference
```

Treat a PAR as a bearer link; do not commit it to this repository. An expired
PAR will no longer work for new stack creation.

When creating the stack, deselect **Run apply**, review the variables, and run
and review a plan before applying. Keep `dry_run = true` and
`enable_termination = false` for initial validation. A deploy button does not
replace release approval or the staging checks in the runbook.

Terraform preserves the controller scope after the first Apply and each
profile-to-pool binding after enrollment. A changed group, replacement pool under an old profile,
or removal of an enrolled profile is blocked. Retiring an enrollment requires
a deliberate ledger/enrollment migration; losing tags must not silently discard
controller history.

## Configuration and upgrade boundary

Profiles use `worker_type` in Terraform and `workerType` in Function profile
JSON/status output. These are descriptive worker-class labels, not OCI shape
identifiers; Terraform derives shape, OCPUs and memory from the attached
immutable instance configuration and validates them at plan time.
With automatic discovery, `worker_type` defaults to the `ScaleTestProfile` key;
`pool_overrides` can set its label or maximum size per profile. In Resource
Manager, **Customize per-pool settings** is unchecked by default and reveals
the optional rows only when selected. Remove unused rows rather than entering
a placeholder key. This checkbox only controls visibility; saved overrides
remain active until their entries are removed. The Plan and
outputs list the exact pool IDs that will be pinned in the Function. Discovery
runs on each new Plan, not while the Function runs: review pool additions and
removals before every Apply. Missing or conflicting enrollment tags must be
corrected through the pool owner's existing workflow; this stack does not tag,
create or resize pools during deployment.
Invoke the example as `examples/pool_controller.py`. The release archive and
default deployment prefix are `oci-pool-controller`.

This is not an unattended in-place upgrade of a prior customized deployment.
Adapt profile mappings and status consumers to the new metadata names and
review the Terraform plan. Existing deployments must explicitly retain their
resource naming, pool keys/OCIDs, scope ID and ledger ownership where needed;
changing defaults can rename or replace resources. Never reset retirement
records or generation counters to adopt the new naming. See the
[deployment guide](deploy/reference/README.md) before migration.
Existing nonempty `pools` maps take precedence over `enroll_pools = false` and
automatic discovery, so an older stack keeps its explicit allowlist. Existing
discovery-based stacks must set `enroll_pools = true` when upgrading; identity
guards block removal of their existing enrollments. For a new manually
configured stack, enable enrollment, disable `auto_discover_pools`, and use the
advanced `pools` map.

The source still supports only its documented Intel Flex profiles. Generic
naming does not add support for arbitrary shapes, regions, scheduler products
or fleet sizes. Your scheduler decides worker demand and proves all work on a
worker has drained before committing retirement.

## Verify before using

Verify source content against `RELEASE_MANIFEST.json`; it records file
integrity, not release approval. Review the source, dependencies and image
build before staging. From the source root:

```sh
terraform -chdir=deploy/reference init -backend=false
terraform -chdir=deploy/reference fmt -check
terraform -chdir=deploy/reference validate
```

Terraform init downloads the locked provider if not cached; validation does
not deploy resources. Regression/fault tests are maintained and run in the
development repository; request their version-specific results from the release
owner. Follow the deployment guide and runbook before planning live changes.

Pattern scanning and an explicit inclusion list reduce accidental disclosure;
they do not replace manual security review. **Confirm sharing/license terms in
[NOTICE.md](NOTICE.md) before external distribution or incorporation.**

Known limitations include caller-driven reconciliation, no cancellation of
service-limit-stalled `SCALING`, and no managed `STOPPED`-worker cleanup. Shape
caps, the 100-instance exclusion limit, bounded inline registry, ledger growth
and unqualified fleet throughput require operator planning. Read the
[architecture limits](PRODUCT_ARCHITECTURE.md#7-limits-and-failure-boundaries)
and [production promotion gates](docs/RUNBOOK.md#9-production-promotion-gates).

## POC SAMPLE CODE
ORACLE AND ITS AFFILIATES DO NOT PROVIDE ANY WARRANTY WHATSOEVER, EXPRESS OR IMPLIED, FOR ANY SOFTWARE, MATERIAL OR CONTENT OF ANY KIND CONTAINED OR PRODUCED WITHIN THIS REPOSITORY, AND IN PARTICULAR SPECIFICALLY DISCLAIM ANY AND ALL IMPLIED WARRANTIES OF TITLE, NON-INFRINGEMENT, MERCHANTABILITY, AND FITNESS FOR A PARTICULAR PURPOSE. FURTHERMORE, ORACLE AND ITS AFFILIATES DO NOT REPRESENT THAT ANY CUSTOMARY SECURITY REVIEW HAS BEEN PERFORMED WITH RESPECT TO ANY SOFTWARE, MATERIAL OR CONTENT CONTAINED OR PRODUCED WITHIN THIS REPOSITORY. IN ADDITION, AND WITHOUT LIMITING THE FOREGOING, THIRD PARTIES MAY HAVE POSTED SOFTWARE, MATERIAL OR CONTENT TO THIS REPOSITORY WITHOUT ANY REVIEW. USE AT YOUR OWN RISK.
