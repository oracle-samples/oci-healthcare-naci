# Deploying the OCI Generative AI gateway reference architecture

The stack demonstrates a shared OCI model-access layer: LiteLLM provides the
OpenAI-compatible Gateway and Gateway Router, OCI Generative AI runs the selected
models, and Presidio demonstrates a pluggable request-and-response guardrail.
The services run on one Ubuntu 24.04 AMD Compute VM. Its VCN has a public subnet
and internet gateway for package downloads and OCI inference. The only inbound
rule is TCP 22 from the CIDR you provide. Docker publishes the gateway on
`127.0.0.1:4000`; connect through an SSH tunnel. Presidio has no public endpoint.
The default E5 Flex shape is 1 OCPU / 16 GB RAM with a 50 GB boot volume. This is
a small evaluation deployment, with a single VM as a single point of failure.

## Before you deploy

Use a dedicated OCI compartment, an SSH public key, and your workstation's public
IPv4 address as a `/32` CIDR (or a trusted range no broader than `/24`). The OCI
operator needs permission to create Compute and network resources in the chosen
compartment, read platform images and tenancy metadata, and create a dynamic group
and IAM policy in the tenancy. The stack creates those IAM resources in the
tenancy's home region even when the VM runs elsewhere.

Select a Generative AI region in which **both** configured models offer on-demand
inference and are enabled for your tenancy. No dedicated AI cluster is created.
The default models are `google.gemini-2.5-flash-lite` for `basic` and `xai.grok-4.3`
for `reasoning`. The OCI model catalog and hosting location are part of customer
review: the default Google and xAI models can be externally hosted behind OCI's
service. `genai_region` defaults to the VM region; selecting another region routes
the sanitized inference requests there.

## Resource Manager

Use the README deploy button for a published release, or upload the generated
Resource Manager ZIP directly when creating a stack. Select Terraform **1.5.x**
and fill in the form. Review the plan, then run Apply. The ZIP includes the
Terraform configuration, `schema.yaml`, cloud-init template, Compose definition,
and application source. The VM does not clone a Git repository. Bootstrap still
downloads operating-system packages, pinned Python packages, container base images,
and the NLP model from their upstream repositories.

Terraform completion means that OCI resources have been created. The VM continues
building the containers in cloud-init, which can take 10–20 minutes. Use the
`bootstrap_status_command` output to check it. The systemd unit retries a failed
container build or startup every 30 seconds. OCI IAM propagation can delay successful
inference beyond application startup; retry the synthetic smoke test after the
policy takes effect. Oracle documents that dynamic-group membership changes can
take about an hour to propagate.

The bootstrap retries Ubuntu package metadata and runtime installation up to eight
times to accommodate short-lived DNS or package-repository failures. If all retries
are exhausted, wait for network connectivity to recover, then run:

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 python3 curl
sudo systemctl enable --now docker
sudo systemctl restart oci-genai-phi
```

Open the `ssh_tunnel_command` in a terminal, adding `-i /path/to/private/key` if
necessary. Retrieve the bearer key with `api_key_command`. Follow the README's
synthetic request examples at `http://127.0.0.1:4000/v1`.

## Credentials and IAM

The gateway uses the VM's instance principal. There is no OCI API signing key in
Terraform, cloud-init, or the containers. A dynamic group matches **only this VM's
instance OCID**. Its policy permits `use generative-ai-chat` in the chosen GenAI
compartment. If you deliberately select the root tenancy compartment, OCI's
`in tenancy` policy scope applies; use a dedicated compartment for narrower scope.
The inference identity cannot create models or dedicated clusters.

At first boot, Python generates a random `sk-`-prefixed LiteLLM Gateway master key into the root-owned,
mode `0600` file `/opt/oci-genai-phi/.env`. Terraform outputs only the SSH command
for retrieving it; the token itself never enters Terraform state. Operators with
root or Docker access on the VM can read application environment variables.
The SSH tunnel encrypts client traffic to the VM; the loopback API uses HTTP.

## Local Terraform

Use Terraform 1.5 or newer and an OCI SDK configuration/profile with the required
permissions. Resource Manager provides its own authentication. The provider file
intentionally contains no user, fingerprint, or private-key settings.

```sh
terraform init
terraform plan -var-file=/secure/path/deployment.tfvars
terraform apply -var-file=/secure/path/deployment.tfvars
```

The required input values are `tenancy_ocid`, `compartment_ocid`, `region`,
`ssh_public_key`, and `ssh_allowed_cidr`. Optional variables cover model IDs, the
inference compartment and region, availability domain, AMD shape and VM size.
Use `image_ocid` to pin a compatible Ubuntu 24.04 AMD64 platform image; otherwise
Terraform discovers the newest compatible image on every plan.

The application is stateless. Changing embedded source or runtime configuration
**replaces the VM**, because cloud-init only installs source at first boot.
Image or placement changes can also replace it. Review the plan before applying
updates. A replacement gets a new bearer key and may get a new public IP. No chat
database, request history, or permanent redaction mapping is provisioned.

## Operations and cleanup

On the VM:

```sh
sudo cloud-init status --long
sudo systemctl status oci-genai-phi --no-pager
sudo journalctl -u oci-genai-phi --no-pager -n 100
sudo docker compose --project-directory /opt/oci-genai-phi ps
```

Terraform verifies image and shape compatibility in the selected availability
domain before launch. If a shape is not offered there or has insufficient capacity,
change `availability_domain` or `instance_shape` and apply again. If inference returns an authorization error,
check the selected compartment, dynamic-group matching rule, IAM propagation,
regional model availability, and tenancy model access. Do not weaken the SSH
CIDR or publish port 4000 to diagnose an application issue.

Destroy the Resource Manager stack's resources, or run `terraform destroy` using
the same variables, to remove the VM, boot volume, VCN, subnet, gateway, routing,
security list, dynamic group, and policy. Billable resources remain until destroy
finishes successfully; removing only the stack record does not clean them up.

## Implementation references

- [Oracle deploy-button format](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Tasks/deploybutton.htm)
- [Resource Manager ZIP structure and authentication](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Concepts/terraformconfigresourcemanager.htm)
- [Resource Manager schema controls](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Concepts/terraformconfigresourcemanager_topic-schema.htm)
- [Supported Resource Manager Terraform versions](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Reference/terraformversions.htm)
- [OCI provider instance schema and 32,000-byte metadata limit](https://docs.oracle.com/en-us/iaas/tools/terraform-provider-oci/latest/docs/r/core_instance.html)
- [OCI platform image discovery](https://docs.oracle.com/en-us/iaas/tools/terraform-provider-oci/latest/docs/d/core_images.html)
- [Dynamic-group instance matching](https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/Writing_Matching_Rules_to_Define_Dynamic_Groups.htm)
- [Dynamic-group propagation](https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingdynamicgroups.htm)
- [Chat API IAM permission](https://docs.oracle.com/en-us/iaas/Content/generative-ai/chat-permissions.htm)
- [IAM syntax for dynamic-group and compartment OCIDs](https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/policysyntax.htm)

Cloud-init is gzip-compressed and base64-encoded; Terraform rejects a payload that
would exceed OCI's metadata limit. The explicit source manifest includes the
Gateway Router YAML, callbacks, guardrail source, Dockerfiles, requirements, and
Compose definition, so local secrets and caches are excluded from metadata.
