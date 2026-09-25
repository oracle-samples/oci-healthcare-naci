variable "region" {
  description = "Region containing every enrolled pool and the controller."
  type        = string
}

variable "tenancy_ocid" {
  type = string
}

variable "controller_compartment_ocid" {
  description = "Compartment for the Function, ledger and logs. No worker resources are created."
  type        = string
}

variable "pool_compartment_ocid" {
  description = "Required when enrolling pools: single existing compartment containing the pools, configurations and workers. Leave blank when deploying now and enrolling later."
  type        = string
  default     = ""

  validation {
    condition     = var.pool_compartment_ocid == "" || startswith(var.pool_compartment_ocid, "ocid1.compartment.")
    error_message = "Leave the pool compartment blank for deploy-only mode, or supply a compartment OCID for enrollment."
  }
}

variable "network_compartment_ocid" {
  description = "Compartment containing existing Function and worker subnets; review IAM if these differ."
  type        = string
}

variable "function_vcn_ocid" {
  description = "Existing VCN containing the selected Function subnets."
  type        = string

  validation {
    condition     = startswith(var.function_vcn_ocid, "ocid1.vcn.")
    error_message = "function_vcn_ocid must be an OCI VCN OCID."
  }
}

variable "registry_compartment_ocid" {
  description = "Compartment where the stack creates its private OCIR repository, or containing the existing image in manual mode."
  type        = string
}

variable "subnet_ids" {
  description = "Existing Function subnet OCIDs with OCI-service connectivity. This module creates no network."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) > 0 && alltrue([for id in var.subnet_ids : startswith(id, "ocid1.subnet.")])
    error_message = "Supply at least one existing OCI subnet OCID."
  }
}

variable "name_prefix" {
  type    = string
  default = "oci-pool-controller"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9_-]{0,39}$", var.name_prefix))
    error_message = "name_prefix must start with a letter and contain at most 40 letters, digits, underscores or hyphens."
  }
}

variable "scope_id" {
  description = "Optional stable controller group. Deploy-only mode generates it when blank; use output controller_scope_id for later pool tags. Initial enrollment can infer a sole existing HarnessId group. An applied controller's group cannot change."
  type        = string
  default     = ""

  validation {
    condition     = var.scope_id == "" || can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", var.scope_id))
    error_message = "Leave scope_id blank for discovery or use a request-ID-compatible identifier."
  }
}

variable "build_function_image" {
  description = "Build source on native x86 OCI DevOps during Apply, deliver to private OCIR, then deploy an x86 Function. The Resource Manager host architecture is irrelevant."
  type        = bool
  default     = true
}

variable "create_build_iam_resources" {
  description = "Create scoped home-region IAM for the build pipeline only. Disable only when central IAM supplies the equivalent build-policy output. Independent of controller runtime IAM."
  type        = bool
  default     = true
}

variable "ocir_username" {
  description = "OCI username including identity domain (for example Default/user@example.com). Publishes packaged source to its private OCI code repository; tenancy name is added automatically. Variable name retained for compatibility."
  type        = string
  default     = ""
}

variable "ocir_auth_token" {
  description = "OCI auth token for source publication, not the Console password. Never passed to the build runner or stored in Git; image delivery uses resource-principal IAM. Protect stack variables and state."
  type        = string
  sensitive   = true
  default     = ""
}

variable "function_image" {
  description = "Only when automatic build is disabled: existing same-region OCIR image address including tag, built from the accompanying Function source."
  type        = string
  default     = ""
}

variable "function_image_digest" {
  description = "Optional digest pin for an existing image. Leave blank to let OCI Functions resolve the supplied image tag. Automatic builds resolve the digest without manual input."
  type        = string
  default     = ""

  validation {
    condition     = var.function_image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.function_image_digest))
    error_message = "Leave blank or supply a sha256: digest with 64 lowercase hexadecimal characters."
  }
}

variable "function_shape" {
  description = "Existing-image mode only: architecture must match the image. Automatic builds use GENERIC_X86, independent of worker shapes."
  type        = string
  default     = "GENERIC_ARM"

  validation {
    condition     = contains(["GENERIC_ARM", "GENERIC_X86"], var.function_shape)
    error_message = "Choose GENERIC_ARM or GENERIC_X86 to match the built image."
  }
}

variable "enroll_pools" {
  description = "Enroll existing pools during this deployment. Leave disabled to deploy the Function now and enroll later. A saved nonempty manual pools map remains authoritative; existing discovery stacks must enable this when upgrading."
  type        = bool
  default     = false
}

variable "auto_discover_pools" {
  description = "Discover enrolled pools in the selected pool compartment from their existing HarnessId and ScaleTestProfile tags. A nonempty manual pools map takes precedence for backwards compatibility."
  type        = bool
  default     = true
}

variable "default_pool_max_size" {
  description = "Approved maximum worker count for each discovered pool unless its profile has a max_size override."
  type        = number
  default     = 3

  validation {
    condition     = var.default_pool_max_size >= 1 && floor(var.default_pool_max_size) == var.default_pool_max_size
    error_message = "default_pool_max_size must be a positive integer."
  }
}

variable "customize_pool_settings" {
  description = "Resource Manager display control only: show optional per-profile override fields. Hiding fields does not disable or erase saved pool_overrides; remove entries to restore discovery defaults."
  type        = bool
  default     = false
}

variable "pool_overrides" {
  description = "Optional discovery overrides keyed by ScaleTestProfile. Set only the worker_type or approved max_size values that should differ from discovery defaults."
  type = map(object({
    worker_type = optional(string)
    max_size    = optional(number)
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, pool in var.pool_overrides :
      can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", key)) &&
      (pool.worker_type == null ? true : length(trimspace(pool.worker_type)) > 0 && length(pool.worker_type) <= 255) &&
      (pool.max_size == null ? true : pool.max_size >= 1 && floor(pool.max_size) == pool.max_size)
    ])
    error_message = "Override keys must be valid profile identifiers; supplied worker_type must be 1-255 characters and max_size must be a positive integer."
  }
}

variable "pools" {
  description = "Optional pinned existing-pool allowlist. A nonempty map takes precedence over automatic discovery for backwards compatibility. Terraform derives pool name, Intel shape, OCPUs and memory; keys must match each pool/configuration/worker ScaleTestProfile tag."
  type = map(object({
    pool_id     = string
    worker_type = string
    max_size    = number
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, pool in var.pools : can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", key)) &&
      startswith(pool.pool_id, "ocid1.instancepool.") && length(trimspace(pool.worker_type)) > 0 && length(pool.worker_type) <= 255 &&
      pool.max_size >= 1 && floor(pool.max_size) == pool.max_size
    ])
    error_message = "Each entry must pin an existing pool, include a worker_type of 1-255 characters and use a positive integer max_size."
  }

  validation {
    condition     = length(distinct([for pool in values(var.pools) : pool.pool_id])) == length(var.pools)
    error_message = "A pool OCID may be enrolled only once."
  }
}

variable "max_profiles" {
  description = "Explicit controller registry ceiling; raising it is not a validated throughput claim."
  type        = number
  default     = 6

  validation {
    condition     = var.max_profiles >= 1 && floor(var.max_profiles) == var.max_profiles
    error_message = "max_profiles must be a positive integer."
  }
}

variable "max_pool_size" {
  description = "Maximum configured size for any enrolled profile. Start small in staging."
  type        = number
  default     = 50

  validation {
    condition     = var.max_pool_size >= 1 && floor(var.max_pool_size) == var.max_pool_size
    error_message = "max_pool_size must be a positive integer."
  }
}

variable "max_active_pools" {
  description = "Aggregate operational ceiling on simultaneously active enrolled pools."
  type        = number
  default     = 1

  validation {
    condition     = var.max_active_pools >= 1 && floor(var.max_active_pools) == var.max_active_pools
    error_message = "max_active_pools must be a positive integer."
  }
}

variable "max_total_ocpus" {
  description = "Aggregate operational OCPU ceiling, including retiring capacity; not an OCI quota reservation."
  type        = number
  default     = 16

  validation {
    condition     = var.max_total_ocpus >= 1 && floor(var.max_total_ocpus) == var.max_total_ocpus
    error_message = "max_total_ocpus must be a positive integer."
  }
}

variable "dry_run" {
  description = "Safe initial mode. Inspect proposed behavior without Compute mutation; ledger/status activity still occurs."
  type        = bool
  default     = true
}

variable "enable_bounded_growth" {
  description = "Opt-in preview: durable launch reservations, bounded replacement growth and separate exact termination. Requires exclusive controller ownership and staging acceptance before production. Do not disable after ledger activation without migration."
  type        = bool
  default     = false
}

variable "max_total_vms" {
  description = "Aggregate VM cap across enrolled pools, including reserved launches and attached/detached retiring workers (bounded-growth preview). Not an OCI tenancy quota."
  type        = number
  default     = 25
  validation {
    condition     = var.max_total_vms >= 1 && floor(var.max_total_vms) == var.max_total_vms
    error_message = "max_total_vms must be a positive integer."
  }
}

variable "launch_timeout_seconds" {
  description = "Time before an unresolved bounded-growth launch requires operator review. Expiry never releases its capacity reservation."
  type        = number
  default     = 900
  validation {
    condition     = var.launch_timeout_seconds >= 1 && floor(var.launch_timeout_seconds) == var.launch_timeout_seconds
    error_message = "launch_timeout_seconds must be a positive integer."
  }
}

variable "enable_termination" {
  description = "Separate targeted-termination kill switch. Enable only after worker-drain and IAM acceptance."
  type        = bool
  default     = false
}

variable "create_iam_resources" {
  description = "Opt in to tenancy-level group/policy creation; otherwise send IAM outputs to the operator's administrator."
  type        = bool
  default     = false
}

variable "dynamic_group_name" {
  description = "Optional administrator-chosen name for the controller-only dynamic group."
  type        = string
  default     = null

  validation {
    condition     = var.dynamic_group_name == null ? true : can(regex("^[A-Za-z][A-Za-z0-9_-]{0,99}$", var.dynamic_group_name))
    error_message = "Use a simple dynamic group name without whitespace or policy syntax."
  }
}

variable "configure_caller_groups" {
  description = "Resource Manager display control only: show optional caller-group inputs. Hiding the editor does not disable saved group OCIDs; remove them to revoke stack-managed caller grants."
  type        = bool
  default     = false
}

variable "invoker_group_ocids" {
  description = "Optional existing OCI groups permitted to invoke this exact Function. These callers have full controller authority. Blank rows are ignored; every nonblank entry must be a group OCID."
  type        = set(string)
  default     = []
  nullable    = false

  validation {
    condition     = alltrue([for id in var.invoker_group_ocids : id == null ? true : (trimspace(id) == "" || can(regex("^ocid1[.]group[.][A-Za-z0-9._-]+$", trimspace(id))))])
    error_message = "Every nonblank invoker_group_ocids entry must be an OCI group OCID beginning ocid1.group.; group names, user OCIDs and dynamic-group OCIDs are not accepted. Leave the list empty to configure caller permissions later."
  }
}

variable "ledger_bucket_name" {
  description = "Optional dedicated private ledger bucket name; cannot be shared by unrelated controllers."
  type        = string
  default     = null

  validation {
    condition     = var.ledger_bucket_name == null ? true : can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", var.ledger_bucket_name))
    error_message = "Use a request-ID-compatible dedicated bucket name."
  }
}
