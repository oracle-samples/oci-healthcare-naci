data "oci_identity_tenancy" "current" {
  tenancy_id = var.tenancy_ocid
}

data "oci_identity_regions" "all" {}

data "oci_identity_availability_domains" "available" {
  compartment_id = var.compartment_ocid
}

data "oci_core_images" "ubuntu" {
  count                    = var.image_ocid == "" ? 1 : 0
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.instance_shape
  state                    = "AVAILABLE"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

data "oci_core_shapes" "compatible" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  image_id            = local.image_ocid
}

locals {
  home_region            = one([for region in data.oci_identity_regions.all.regions : region.name if region.key == data.oci_identity_tenancy.current.home_region_key])
  genai_compartment_ocid = var.genai_compartment_ocid != "" ? var.genai_compartment_ocid : var.compartment_ocid
  genai_region           = var.genai_region != "" ? var.genai_region : var.region
  availability_domain    = var.availability_domain != "" ? var.availability_domain : data.oci_identity_availability_domains.available.availability_domains[0].name
  image_ocid             = var.image_ocid != "" ? var.image_ocid : try(data.oci_core_images.ubuntu[0].images[0].id, "")
  tags                   = { project = "oci-genai-litellm-gateway", managed_by = "terraform" }

  # Explicit manifest prevents local .env, credentials, caches or tests from
  # ever being copied into instance metadata. All paths ship inside the ZIP.
  application_paths = concat([
    "compose.yaml",
    "gateway/Dockerfile",
    "gateway/config.yaml",
    "gateway/requirements.txt",
    "guardrail/Dockerfile",
    "guardrail/requirements.txt",
  ], sort(tolist(fileset(path.module, "gateway/*.py"))), sort(tolist(fileset(path.module, "guardrail/*.py"))))
  application_files = [for source_path in local.application_paths : {
    path    = source_path
    content = filebase64("${path.module}/${source_path}")
  }]
  runtime_environment = join("\n", [
    "OCI_REGION=${local.genai_region}",
    "OCI_COMPARTMENT_ID=${local.genai_compartment_ocid}",
    "BASIC_MODEL=oci/${var.basic_model}",
    "REASONING_MODEL=oci/${var.reasoning_model}",
    "",
  ])
  user_data = base64gzip(templatefile("${path.module}/deploy/cloud-init.yaml.tftpl", {
    application_files   = local.application_files
    runtime_environment = base64encode(local.runtime_environment)
  }))
}

resource "oci_core_vcn" "reference" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.42.0.0/16"]
  display_name   = "${var.display_name}-vcn"
  dns_label      = "genaiphi"
  freeform_tags  = local.tags
}

resource "oci_core_internet_gateway" "reference" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.reference.id
  enabled        = true
  display_name   = "${var.display_name}-internet"
  freeform_tags  = local.tags
}

resource "oci_core_route_table" "reference" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.reference.id
  display_name   = "${var.display_name}-routes"
  freeform_tags  = local.tags
  route_rules {
    network_entity_id = oci_core_internet_gateway.reference.id
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
  }
}

resource "oci_core_security_list" "reference" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.reference.id
  display_name   = "${var.display_name}-ssh-only"
  freeform_tags  = local.tags

  ingress_security_rules {
    description = "SSH tunnel and administration from the operator's trusted network"
    protocol    = "6"
    source      = var.ssh_allowed_cidr
    source_type = "CIDR_BLOCK"
    stateless   = false
    tcp_options {
      min = 22
      max = 22
    }
  }

  egress_security_rules {
    description      = "Bootstrap packages, container images, OCI APIs and on-demand model inference"
    protocol         = "all"
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    stateless        = false
  }
}

resource "oci_core_subnet" "reference" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.reference.id
  cidr_block                 = "10.42.1.0/24"
  display_name               = "${var.display_name}-subnet"
  dns_label                  = "reference"
  route_table_id             = oci_core_route_table.reference.id
  security_list_ids          = [oci_core_security_list.reference.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.tags
}

# Cloud-init only runs at first boot. Replacing the stateless VM when source or
# configuration changes avoids an apparently successful but stale deployment.
resource "terraform_data" "bootstrap" {
  input = sha256(local.user_data)
}

resource "oci_core_instance" "reference" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = var.display_name
  shape               = var.instance_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.reference.id
    assign_public_ip = true
    hostname_label   = "genai-phi"
  }

  source_details {
    source_type             = "image"
    source_id               = local.image_ocid
    boot_volume_size_in_gbs = var.boot_volume_gbs
  }

  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  metadata = {
    ssh_authorized_keys = trimspace(var.ssh_public_key)
    user_data           = local.user_data
  }

  lifecycle {
    replace_triggered_by = [terraform_data.bootstrap]

    precondition {
      condition     = local.image_ocid != ""
      error_message = "No compatible Ubuntu 24.04 platform image was found. Choose another AMD shape or provide image_ocid."
    }

    precondition {
      condition     = contains([for shape in data.oci_core_shapes.compatible.shapes : shape.name], var.instance_shape)
      error_message = "The selected shape is not available for this image in the chosen availability domain. Select another shape or availability domain."
    }

    precondition {
      condition     = length(local.user_data) + length(var.ssh_public_key) + 100 < 32000
      error_message = "The compressed cloud-init payload exceeds OCI's 32,000-byte metadata limit. Reduce application source before deploying."
    }

    precondition {
      condition     = var.instance_memory_gbs <= var.instance_ocpus * 64
      error_message = "The selected memory cannot exceed 64 GB per OCPU."
    }
  }
}

resource "oci_identity_dynamic_group" "reference" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${var.display_name}-${substr(sha256(oci_core_instance.reference.id), 0, 8)}"
  description    = "Only the Compute instance belonging to this OCI GenAI LiteLLM reference stack"
  matching_rule  = "ALL {instance.id = '${oci_core_instance.reference.id}'}"
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "genai_chat" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${var.display_name}-chat-${substr(sha256(oci_core_instance.reference.id), 0, 8)}"
  description    = "Allow this reference instance to perform chat inference in the selected compartment"
  statements = [
    "Allow dynamic-group id ${oci_identity_dynamic_group.reference.id} to use generative-ai-chat in ${local.genai_compartment_ocid == var.tenancy_ocid ? "tenancy" : "compartment id ${local.genai_compartment_ocid}"}",
  ]
  freeform_tags = local.tags
}
