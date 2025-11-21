# --- Provider and Tenancy Variables ---
variable "tenancy_ocid" {
  type        = string
  default = "ocid1.tenancy.oc1..aaaaaaaazjp72z73flagbjdtbdqc54chuq6hrqn4mxczvnsn6uvgy2gs3blq"
  description = "The OCID of your OCI tenancy."
}

variable "region" {
  type        = string
  default = "us-ashburn-1"
  description = "The OCI region to deploy resources in."
}

variable "compartment_id" {
  description = "The OCID of the compartment to deploy resources in."
  default = "ocid1.compartment.oc1..aaaaaaaac3kiloqnzku77mrwvxxvmgvyj3hhjjuomquli3fxakzsyrrewopq"
  type        = string
}

variable availability_domain {
  default     = ""
  description = "(Optional, Default to let the use."
}

variable client_cidr_block_allow_list {
  default = "0.0.0.0/0"
  description = "CIDR block allow list for IP addresses for SSH Bastion Service. Default=Everyone"
}