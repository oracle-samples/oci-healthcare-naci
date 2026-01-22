############################################################
# Variables (apps)
############################################################

# OCI auth (needed to fetch kubeconfig)
variable "tenancy_ocid" { type = string }
variable "user_ocid" { type = string }
variable "fingerprint" { type = string }
variable "private_key_path" { type = string }

variable "region" {
  type    = string
  default = "us-ashburn-1"
}

# PostgreSQL credentials
variable "psql_admin_username" {
  type    = string
  default = "admin"
}

variable "psql_admin_password" {
  type      = string
  sensitive = true
}

variable "psql_dicom_username" {
  type    = string
  default = "dicom"
}

variable "psql_dicom_password" {
  type      = string
  sensitive = true
}
