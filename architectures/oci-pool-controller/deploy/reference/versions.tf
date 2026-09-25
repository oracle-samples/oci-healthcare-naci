terraform {
  # OCI Resource Manager currently runs Terraform 1.5.7. Keep this module in
  # the supported 1.5.x line rather than accepting an unselectable newer CLI.
  required_version = ">= 1.5.0, < 1.6.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 8.29.0"
    }
  }
}

# Authentication comes from the operator's OCI profile or workload identity.
# Never put OCI API private keys in Terraform variables. The optional registry
# auth token is a sensitive stack input used only for private source publication.
provider "oci" {
  region = var.region
}

data "oci_identity_tenancy" "current" { tenancy_id = var.tenancy_ocid }
data "oci_identity_regions" "available" {}
locals {
  home_region = one([for region in data.oci_identity_regions.available.regions : region.name
  if region.key == data.oci_identity_tenancy.current.home_region_key])
}
provider "oci" {
  alias  = "home"
  region = local.home_region
}
