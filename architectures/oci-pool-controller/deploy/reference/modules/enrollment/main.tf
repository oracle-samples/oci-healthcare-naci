# Provider-free enrollment selection. Discovery is evaluated during Plan; the
# caller pins this result into Function configuration, never runtime discovery.
variable "enroll_pools" {
  type    = bool
  default = false
}

variable "auto_discover_pools" {
  type    = bool
  default = true
}

variable "scope_id" {
  type    = string
  default = ""

  validation {
    condition     = var.scope_id == "" || can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", var.scope_id))
    error_message = "Controller group must be blank for inference or a valid HarnessId tag value."
  }
}

variable "default_pool_max_size" {
  type    = number
  default = 3

  validation {
    condition     = var.default_pool_max_size >= 1 && floor(var.default_pool_max_size) == var.default_pool_max_size
    error_message = "default_pool_max_size must be a positive integer, not a current pool size."
  }
}

variable "pools" {
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
    ]) && length(distinct([for pool in values(var.pools) : pool.pool_id])) == length(var.pools)
    error_message = "Manual pools require unique pool OCIDs, valid profile keys, worker types of 1-255 characters and positive integer maxima."
  }
}

variable "pool_overrides" {
  type = map(object({
    worker_type = optional(string)
    max_size    = optional(number)
  }))
  default = {}

  validation {
    condition = alltrue([for key, override in var.pool_overrides :
      can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", key)) &&
      (override.worker_type == null ? true : length(trimspace(override.worker_type)) > 0 && length(override.worker_type) <= 255) &&
      (override.max_size == null ? true : override.max_size >= 1 && floor(override.max_size) == override.max_size)
    ])
    error_message = "Overrides require valid profile keys and, when supplied, worker types of 1-255 characters and positive integer maxima."
  }
}

variable "candidates" {
  type = list(object({
    id             = string
    display_name   = string
    compartment_id = string
    state          = string
    freeform_tags  = map(string)
  }))
  default = []
}

locals {
  # Legacy explicit maps remain authoritative despite the new deploy-only
  # default. Existing discovered enrollments are protected by identity guards.
  enrollment_requested = var.enroll_pools || length(var.pools) > 0
  automatic            = local.enrollment_requested && var.auto_discover_pools && length(var.pools) == 0
  active_candidates = [for pool in var.candidates : pool
    if !contains(["TERMINATING", "TERMINATED"], upper(pool.state))
  ]
  available_scopes = sort(distinct(compact([
    for pool in local.active_candidates : lookup(pool.freeform_tags, "HarnessId", "")
  ])))
  effective_scope = var.scope_id != "" ? var.scope_id : (
    !local.enrollment_requested ? "controller-${terraform_data.controller_identity.id}" : (
      local.automatic && length(local.available_scopes) == 1 ? local.available_scopes[0] : ""
    )
  )
  selected = local.automatic ? [for pool in local.active_candidates : pool if
    local.effective_scope != "" && lookup(pool.freeform_tags, "HarnessId", "") == local.effective_scope
  ] : []
  profile_groups = {
    for pool in local.selected : lookup(pool.freeform_tags, "ScaleTestProfile", "") => pool...
  }
  invalid_profile_ids = sort([for pool in local.selected : pool.id
    if !can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", lookup(pool.freeform_tags, "ScaleTestProfile", "")))
  ])
  duplicate_profiles = sort([for profile, pools in local.profile_groups :
    "${profile}: ${join(", ", [for pool in pools : pool.id])}" if length(pools) > 1
  ])
  discovered_pools = {
    for profile, pools in local.profile_groups : profile => {
      pool_id     = pools[0].id
      worker_type = coalesce(try(var.pool_overrides[profile].worker_type, null), profile, "invalid-profile")
      max_size    = coalesce(try(var.pool_overrides[profile].max_size, null), var.default_pool_max_size)
    }
  }
  effective_pools   = local.automatic ? local.discovered_pools : var.pools
  unknown_overrides = setsubtract(toset(keys(var.pool_overrides)), toset(keys(local.discovered_pools)))
  included_ids      = sort([for pool in values(local.effective_pools) : pool.pool_id])
}

# Always retain this identity so later enrollment cannot generate a different
# controller scope. Existing/manual first deployments still use their tag scope.
resource "terraform_data" "controller_identity" {}

# Preserve the original controller identity across later discovery refreshes.
# New tags/groups must not silently select a new ledger or resource name suffix.
resource "terraform_data" "scope_guard" {
  input = local.effective_scope

  lifecycle {
    ignore_changes = [input]
    postcondition {
      condition     = self.output == local.effective_scope
      error_message = "The discovered controller group changed from this stack's original group. Restore its HarnessId/scope_id; use a separate stack or an explicit migration for a different controller."
    }
  }
}

output "scope_id" {
  value      = local.effective_scope
  depends_on = [terraform_data.scope_guard, terraform_data.pool_identity_guard]
}

# Request/retirement history is keyed by profile. Preserve those bindings even
# when external tag changes or pool deletion would shrink discovery results.
resource "terraform_data" "pool_identity_guard" {
  for_each = local.effective_pools
  input    = each.value.pool_id

  lifecycle {
    ignore_changes  = [input]
    prevent_destroy = true
    postcondition {
      condition     = self.output == each.value.pool_id
      error_message = "Profile ${each.key} is already bound to a different pool OCID in this stack's ledger. Restore that binding; use a new profile or a deliberate enrollment/ledger migration instead of reusing its history."
    }
  }
}

output "pools" {
  value = local.effective_pools

  precondition {
    condition     = !local.automatic || var.scope_id != "" || length(local.available_scopes) <= 1
    error_message = "Multiple controller groups were found: ${join(", ", local.available_scopes)}. Set Controller group (scope_id) to the intended existing HarnessId; discovery will not choose for you."
  }
  precondition {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$", local.effective_scope))
    error_message = "No valid controller group is selected. Discovery needs an existing HarnessId tag, or set scope_id explicitly. Manual mode requires scope_id. This stack does not create or repair pool tags."
  }
  precondition {
    condition     = !local.enrollment_requested || length(local.effective_pools) > 0
    error_message = "Pool enrollment was requested but no pools matched. Prepare pools with matching HarnessId/ScaleTestProfile tags in the selected compartment/group, or supply the manual allowlist. For a new controller without pools, disable Enroll existing pools now. Existing enrollments cannot be silently removed."
  }
  precondition {
    condition     = length(local.invalid_profile_ids) == 0
    error_message = "Selected pools have missing or invalid ScaleTestProfile tags: ${join(", ", local.invalid_profile_ids)}. Have their owner prepare matching pool, configuration and launch tags; no automatic retagging is performed."
  }
  precondition {
    condition     = length(local.duplicate_profiles) == 0
    error_message = "ScaleTestProfile must be unique within the selected controller group. Duplicates: ${join("; ", local.duplicate_profiles)}."
  }
  precondition {
    condition     = length(distinct(local.included_ids)) == length(local.included_ids)
    error_message = "The same pool OCID cannot be enrolled more than once."
  }
  precondition {
    condition     = local.automatic ? length(local.unknown_overrides) == 0 : length(var.pool_overrides) == 0
    error_message = "Discovery overrides must reference selected ScaleTestProfile keys only (${join(", ", sort(tolist(local.unknown_overrides)))}). Leave overrides empty when deploying without pools. In manual mode, put worker_type/max_size in pools instead."
  }
}

output "review" {
  value = {
    mode              = !local.enrollment_requested ? "awaiting_enrollment" : (local.automatic ? "automatic" : "manual")
    scope_id          = local.effective_scope
    available_groups  = local.automatic ? local.available_scopes : []
    included_pool_ids = local.included_ids
    excluded_pool_ids = sort([for pool in var.candidates : pool.id if !contains(local.included_ids, pool.id)])
  }
}
