data "oci_objectstorage_namespace" "current" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_subnet" "function" {
  for_each = toset(var.subnet_ids)

  subnet_id = each.value
}

data "oci_core_instance_pool" "enrolled" {
  for_each = module.enrollment.pools

  instance_pool_id = each.value.pool_id

  lifecycle {
    precondition {
      condition     = var.pool_compartment_ocid != ""
      error_message = "An explicit pool compartment is required for automatic or manual enrollment."
    }
  }
}

data "oci_core_instance_configuration" "enrolled" {
  for_each = module.enrollment.pools

  instance_configuration_id = data.oci_core_instance_pool.enrolled[each.key].instance_configuration_id
}

locals {
  # Resource Manager can recreate empty optional list rows. Normalize once for
  # both policy statements and resource count; hiding the editor preserves grants.
  effective_invoker_group_ocids = toset(compact([for id in var.invoker_group_ocids : trimspace(id) if id != null]))
  suffix                        = substr(sha256("${var.controller_compartment_ocid}:${module.enrollment.scope_id}"), 0, 8)
  dynamic_group_name            = coalesce(var.dynamic_group_name, "${var.name_prefix}-${local.suffix}")
  ledger_bucket_name            = coalesce(var.ledger_bucket_name, "${var.name_prefix}-ledger-${local.suffix}")
  tags                          = { ManagedBy = "terraform", ControllerScope = module.enrollment.scope_id }
  function_subnets_valid = alltrue([
    for subnet in data.oci_core_subnet.function :
    subnet.compartment_id == var.network_compartment_ocid && subnet.vcn_id == var.function_vcn_ocid
  ])
  pool_launch_details = {
    for key, configuration in data.oci_core_instance_configuration.enrolled :
    key => try(configuration.instance_details[0].launch_details[0], null)
  }
  pool_contract_valid = {
    for key, pool in module.enrollment.pools : key => alltrue([
      data.oci_core_instance_pool.enrolled[key].compartment_id == var.pool_compartment_ocid,
      lookup(data.oci_core_instance_pool.enrolled[key].freeform_tags, "HarnessId", "") == module.enrollment.scope_id,
      lookup(data.oci_core_instance_pool.enrolled[key].freeform_tags, "ScaleTestProfile", "") == key,
      data.oci_core_instance_configuration.enrolled[key].compartment_id == var.pool_compartment_ocid,
      lookup(data.oci_core_instance_configuration.enrolled[key].freeform_tags, "HarnessId", "") == module.enrollment.scope_id,
      lookup(data.oci_core_instance_configuration.enrolled[key].freeform_tags, "ScaleTestProfile", "") == key,
      try(local.pool_launch_details[key].freeform_tags["HarnessId"] == module.enrollment.scope_id, false),
      try(local.pool_launch_details[key].freeform_tags["ScaleTestProfile"] == key, false),
      try(local.pool_launch_details[key].freeform_tags["InstanceTerminationProtectionEnabled"] == "1", false),
      try(contains(["VM.Standard3.Flex", "VM.Optimized3.Flex"], local.pool_launch_details[key].shape), false),
      try(local.pool_launch_details[key].shape_config[0].ocpus >= 1, false),
      try(local.pool_launch_details[key].shape_config[0].memory_in_gbs >= local.pool_launch_details[key].shape_config[0].ocpus, false),
      try(local.pool_launch_details[key].shape_config[0].memory_in_gbs <= local.pool_launch_details[key].shape_config[0].ocpus * 64, false),
      try(local.pool_launch_details[key].shape == "VM.Optimized3.Flex" ? local.pool_launch_details[key].shape_config[0].ocpus <= 18 && local.pool_launch_details[key].shape_config[0].memory_in_gbs <= 256 : local.pool_launch_details[key].shape_config[0].ocpus <= 32 && local.pool_launch_details[key].shape_config[0].memory_in_gbs <= 512, false),
    ])
  }
  profiles = {
    for key, pool in module.enrollment.pools : key => {
      poolId      = pool.pool_id
      poolName    = data.oci_core_instance_pool.enrolled[key].display_name
      displayName = data.oci_core_instance_pool.enrolled[key].display_name
      workerType  = pool.worker_type
      ociShape    = try(local.pool_launch_details[key].shape, "")
      ocpus       = try(local.pool_launch_details[key].shape_config[0].ocpus, 0)
      memoryGbs   = try(local.pool_launch_details[key].shape_config[0].memory_in_gbs, 0)
      maxSize     = pool.max_size
    }
  }

  # The Function itself is the only runtime member. Do not use an entire
  # compartment or application as the deletion-capable dynamic-group rule.
  dynamic_group_matching_rule = "ALL {resource.type = 'fnfunc', resource.id = '${oci_functions_function.controller.id}'}"
  faas_policy_statements = [
    "Allow service FaaS to use virtual-network-family in compartment id ${var.network_compartment_ocid}",
    "Allow service FaaS to read repos in compartment id ${var.registry_compartment_ocid}",
  ]
  controller_policy_statements = concat([
    "Allow dynamic-group ${local.dynamic_group_name} to manage objects in compartment id ${var.controller_compartment_ocid} where all {target.bucket.name = '${oci_objectstorage_bucket.ledger.name}', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_OVERWRITE'}}",
    ], local.enrollment_requested ? [
    "Allow dynamic-group ${local.dynamic_group_name} to read instance-pools in compartment id ${var.pool_compartment_ocid}",
    "Allow dynamic-group ${local.dynamic_group_name} to manage instance-pools in compartment id ${var.pool_compartment_ocid} where request.permission = 'INSTANCE_POOL_UPDATE'",
    "Allow dynamic-group ${local.dynamic_group_name} to read instance-configurations in compartment id ${var.pool_compartment_ocid}",
    "Allow dynamic-group ${local.dynamic_group_name} to inspect auto-scaling-configurations in compartment id ${var.pool_compartment_ocid}",
    "Allow dynamic-group ${local.dynamic_group_name} to manage instance-configurations in compartment id ${var.pool_compartment_ocid} where request.permission = 'INSTANCE_CONFIGURATION_LAUNCH'",
    "Allow dynamic-group ${local.dynamic_group_name} to manage instances in compartment id ${var.pool_compartment_ocid} where request.permission = 'INSTANCE_CREATE'",
    "Allow dynamic-group ${local.dynamic_group_name} to read instance-images in compartment id ${var.pool_compartment_ocid}",
    "Allow dynamic-group ${local.dynamic_group_name} to use vnics in compartment id ${var.pool_compartment_ocid}",
    # Pool scale-out checks launch permissions in the instance compartment;
    # actual VNICs reside in the subnet's compartment when these differ.
    "Allow dynamic-group ${local.dynamic_group_name} to use subnets in compartment id ${var.pool_compartment_ocid} where request.permission = 'SUBNET_ATTACH'",
    "Allow dynamic-group ${local.dynamic_group_name} to use vnics in compartment id ${var.network_compartment_ocid} where ANY {request.permission = 'VNIC_READ', request.permission = 'VNIC_CREATE', request.permission = 'VNIC_ATTACH', request.permission = 'VNIC_DETACH', request.permission = 'VNIC_DELETE'}",
    "Allow dynamic-group ${local.dynamic_group_name} to use subnets in compartment id ${var.network_compartment_ocid}",
    "Allow dynamic-group ${local.dynamic_group_name} to read instances in compartment id ${var.pool_compartment_ocid}",
    "Allow dynamic-group ${local.dynamic_group_name} to use instances in compartment id ${var.pool_compartment_ocid} where request.permission = 'INSTANCE_UPDATE'",
    "Allow dynamic-group ${local.dynamic_group_name} to use volumes in compartment id ${var.pool_compartment_ocid}",
    ] : [], local.enrollment_requested && var.enable_termination ? [
    "Allow dynamic-group ${local.dynamic_group_name} to manage instance-pools in compartment id ${var.pool_compartment_ocid} where request.permission = 'INSTANCE_POOL_INSTANCE_DETACH'",
    "Allow dynamic-group ${local.dynamic_group_name} to manage instances in compartment id ${var.pool_compartment_ocid} where ANY {request.permission = 'INSTANCE_DELETE', request.permission = 'INSTANCE_DETACH_VOLUME'}",
    "Allow dynamic-group ${local.dynamic_group_name} to manage volume-attachments in compartment id ${var.pool_compartment_ocid} where request.permission = 'VOLUME_ATTACHMENT_DELETE'",
  ] : [])
  invoker_policy_statements = flatten([
    for group in sort(tolist(local.effective_invoker_group_ocids)) : [
      "Allow group id ${group} to read fn-function in compartment id ${var.controller_compartment_ocid} where target.function.id = '${oci_functions_function.controller.id}'",
      "Allow group id ${group} to use fn-invocation in compartment id ${var.controller_compartment_ocid} where target.function.id = '${oci_functions_function.controller.id}'",
    ]
  ])
}

resource "oci_objectstorage_bucket" "ledger" {
  compartment_id        = var.controller_compartment_ocid
  namespace             = data.oci_objectstorage_namespace.current.namespace
  name                  = local.ledger_bucket_name
  access_type           = "NoPublicAccess"
  storage_tier          = "Standard"
  versioning            = "Enabled"
  object_events_enabled = false
  freeform_tags         = local.tags

  lifecycle {
    # Retirement commitments cannot be reconstructed safely by deleting this
    # bucket. A reviewed data-retention migration is required before removal.
    prevent_destroy = true
  }
}

resource "oci_objectstorage_object" "budget_lock" {
  namespace    = oci_objectstorage_bucket.ledger.namespace
  bucket       = oci_objectstorage_bucket.ledger.name
  object       = "coordination/scale-test-budget.json"
  content_type = "application/json"
  content      = jsonencode({ owner = null, leaseUntil = null })

  lifecycle {
    ignore_changes  = [content]
    prevent_destroy = true
  }
}

resource "oci_identity_policy" "faas_service" {
  provider       = oci.home
  count          = var.create_iam_resources ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-faas-${local.suffix}"
  description    = "Operator controller Function networking and private image pull"
  statements     = local.faas_policy_statements
}

resource "oci_functions_application" "controller" {
  compartment_id = var.controller_compartment_ocid
  display_name   = "${var.name_prefix}-${local.suffix}"
  subnet_ids     = var.subnet_ids
  shape          = local.effective_function_shape
  freeform_tags  = local.tags

  lifecycle {
    precondition {
      condition     = var.build_function_image || length(trimspace(var.function_image)) > 0
      error_message = "Enable automatic image building or supply an existing same-region OCIR Function image."
    }
    precondition {
      condition     = local.function_subnets_valid
      error_message = "Every selected Function subnet must be in the configured network compartment and Function VCN."
    }
  }

  depends_on = [oci_identity_policy.faas_service]
}

locals {
  function_config = {
    CONTROLLER_ONLY                   = "true"
    AUTH_MODE                         = "oci_iam"
    FUNCTION_ROLE                     = "control"
    COMPARTMENT_OCID                  = local.target_pool_compartment_id
    ALLOW_EMPTY_POOL_REGISTRY         = tostring(!local.enrollment_requested)
    HARNESS_ID                        = module.enrollment.scope_id
    DRY_RUN                           = tostring(var.dry_run)
    ENABLE_TERMINATION                = tostring(var.enable_termination)
    ENABLE_BOUNDED_GROWTH             = tostring(var.enable_bounded_growth)
    CONTROLLER_MAX_TOTAL_VMS          = tostring(var.max_total_vms)
    CONTROLLER_LAUNCH_TIMEOUT_SECONDS = tostring(var.launch_timeout_seconds)
    FLAG_TAG_KEY                      = "InstanceTerminationProtectionEnabled"
    SCALE_TEST_PROFILES_JSON          = jsonencode(local.profiles)
    CONTROLLER_MAX_PROFILES           = tostring(var.max_profiles)
    CONTROLLER_MAX_POOL_SIZE          = tostring(var.max_pool_size)
    SCALE_TEST_BUDGET_LIMITS_ENABLED  = "true"
    MAX_ACTIVE_SCALE_TEST_PROFILES    = tostring(var.max_active_pools)
    MAX_SCALE_TEST_TOTAL_OCPUS        = tostring(var.max_total_ocpus)
    OBJECT_STORAGE_NAMESPACE          = data.oci_objectstorage_namespace.current.namespace
    REQUEST_LEDGER_BUCKET             = oci_objectstorage_bucket.ledger.name
  }
}

resource "oci_functions_function" "controller" {
  application_id     = oci_functions_application.controller.id
  display_name       = "${var.name_prefix}-control"
  image              = local.effective_function_image
  image_digest       = local.effective_image_digest
  memory_in_mbs      = 256
  timeout_in_seconds = 120
  freeform_tags      = local.tags
  config             = local.function_config

  lifecycle {
    precondition {
      condition = !var.build_function_image || try(
        local.delivery_confirmed &&
        startswith(local.effective_function_image, "${local.image_repository_url}:build-") &&
      can(regex("^sha256:[0-9a-f]{64}$", local.effective_image_digest)), false)
      error_message = "A successful build and exactly one available image with its unique build-run tag and SHA-256 digest are required in this stack's exact private OCIR repository. If registry metadata is not yet visible, rerun Plan/Apply; never substitute latest or an unrelated image."
    }
    precondition {
      # A conservative UTF-8 serialized-JSON bound under OCI's 4-KB combined
      # configuration ceiling. Base64 counts bytes correctly for Unicode names.
      # This application has no extra application-level configuration.
      condition     = length(base64encode(jsonencode(local.function_config))) <= 5332
      error_message = "Inline registry/configuration is too large for OCI Functions' 4-KB limit. Shorten metadata or use separately reviewed non-overlapping controller shards; an external registry is not implemented."
    }
    precondition {
      condition     = length(module.enrollment.pools) <= var.max_profiles && alltrue([for pool in values(module.enrollment.pools) : pool.max_size <= var.max_pool_size])
      error_message = "Pool enrollment exceeds an explicit controller profile or per-pool ceiling."
    }
    precondition {
      condition     = alltrue(values(local.pool_contract_valid))
      error_message = "Invalid enrolled pools: ${join(", ", [for key, valid in local.pool_contract_valid : "${key} (${module.enrollment.pools[key].pool_id}, configuration ${data.oci_core_instance_pool.enrolled[key].instance_configuration_id})" if !valid])}. Pool and configuration must be in the selected compartment with matching HarnessId/ScaleTestProfile tags; launch tags must match and protection must be exactly 1, with a supported Intel Flex shape/configuration. Have the pool owner correct the configuration; no automatic retagging occurs."
    }
    precondition {
      condition     = var.max_active_pools <= var.max_profiles
      error_message = "max_active_pools cannot exceed max_profiles."
    }
  }

  depends_on = [oci_objectstorage_object.budget_lock, oci_devops_build_run.function]
}

resource "oci_identity_dynamic_group" "controller" {
  provider       = oci.home
  count          = var.create_iam_resources ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = local.dynamic_group_name
  description    = "Only the OCI pool controller Function"
  matching_rule  = local.dynamic_group_matching_rule
}

resource "oci_identity_policy" "controller" {
  provider       = oci.home
  count          = var.create_iam_resources ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-runtime-${local.suffix}"
  description    = "Compartment-bound controller permissions; no ledger list or delete permission"
  statements     = local.controller_policy_statements

  depends_on = [oci_identity_dynamic_group.controller]
}

resource "oci_identity_policy" "invokers" {
  provider       = oci.home
  count          = var.create_iam_resources && length(local.effective_invoker_group_ocids) > 0 ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-invoke-${local.suffix}"
  description    = "Existing operator groups may invoke this controller Function only"
  statements     = local.invoker_policy_statements
}

resource "oci_logging_log_group" "controller" {
  compartment_id = var.controller_compartment_ocid
  display_name   = "${var.name_prefix}-logs-${local.suffix}"
  description    = "Operator controller Function invocation logs"
  freeform_tags  = local.tags
}

resource "oci_logging_log" "controller" {
  display_name  = "${var.name_prefix}-invocations"
  log_group_id  = oci_logging_log_group.controller.id
  log_type      = "SERVICE"
  is_enabled    = true
  freeform_tags = local.tags

  configuration {
    compartment_id = var.controller_compartment_ocid

    source {
      category    = "invoke"
      resource    = oci_functions_application.controller.id
      service     = "functions"
      source_type = "OCISERVICE"
    }
  }
}
