terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 8.0.0, < 9.0.0"
    }
  }
}

# Resource Manager supplies authentication. Local Terraform uses the standard
# OCI SDK configuration / environment; no credentials belong in this stack.
provider "oci" {
  region = var.region
}

# IAM is a tenancy-wide service administered in the tenancy's home region.
provider "oci" {
  alias  = "home"
  region = local.home_region
}
