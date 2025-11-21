# provider "oci" {
#   alias                = "home"
#   region               = var.region
#   tenancy_ocid         = var.tenancy_ocid
#   user_ocid            = var.user_ocid
#   fingerprint          = var.fingerprint
#   private_key_path     = var.private_key_path
#   private_key_password = var.private_key_password
#   ignore_defined_tags  = ["Oracle-Tags.CreatedBy", "Oracle-Tags.CreatedOn"]
# }

# provider "oci" {
#   alias                = "home"
#   region               = local.regions_map[local.home_region_key]
#   tenancy_ocid         = var.tenancy_ocid
#   user_ocid            = var.user_ocid
#   fingerprint          = var.fingerprint
#   private_key_path     = var.private_key_path
#   private_key_password = var.private_key_password
#   ignore_defined_tags  = ["Oracle-Tags.CreatedBy", "Oracle-Tags.CreatedOn"]
# }

provider "oci" {
  alias                = "home"
  tenancy_ocid = var.tenancy_ocid
  region       = var.region
}

terraform {
  required_version = ">= 1.3.0"
  required_providers {
    oci = {
      source                = "oracle/oci"
      configuration_aliases = [oci.home]
    }
  }
}