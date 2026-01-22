
############################################################
# Variables
############################################################

variable "public_allowed_ips" {
  description = "Public IP CIDRs allowed to access public subnet"
  type        = list(string)
}

variable "tenancy_ocid" {
  description = "OCID of your tenancy"
  type        = string
}

variable "user_ocid" {
  description = "OCID of the user running Terraform (for customer secret key)"
  type        = string
}

variable "fingerprint" {
  description = "API key fingerprint"
  type        = string
}

variable "private_key_path" {
  description = "Path to the private API key"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "us-ashburn-1"
}

variable "compartment_ocid" {
  description = "Compartment to create VCN/OKE/bucket/db in"
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key for OKE nodes"
  type        = string
}

variable "vcn_cidr" {
  description = "CIDR for VCN"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "CIDR for public subnet (LB, API endpoint)"
  type        = string
  default     = "10.0.1.0/24"
}

variable "private_subnet_cidr" {
  description = "CIDR for private subnet (nodes, DB)"
  type        = string
  default     = "10.0.2.0/24"
}

variable "cluster_name" {
  description = "OKE cluster name"
  type        = string
  default     = "demo-oke-cluster"
}

variable "cluster_kubernetes_version" {
  description = "Kubernetes version (must match available OKE versions)"
  type        = string
  default     = "v1.33.1"
}

variable "node_pool_size" {
  description = "Number of nodes in the node pool"
  type        = number
  default     = 2
}

variable "node_image_ocid" {
  description = "OCID of the OKE worker node image to use"
  type        = string
  # using "Oracle-Linux-8.10-2025.09.16-0-OKE-1.33.1-1330"
  default     = "ocid1.image.oc1.iad.aaaaaaaadbeckykfjep2ktkxajpbpnxjj726pzlxmrxvo5fapaa2laz5sgbq"
}

variable "node_shape" {
  description = "Compute shape for worker nodes"
  type        = string
  default     = "VM.Standard.E5.Flex"
}

variable "node_ocpus" {
  description = "OCPUs per worker node (for Flex shapes)"
  type        = number
  default     = 2
}

variable "node_memory_gbs" {
  description = "Memory (GB) per worker node"
  type        = number
  default     = 16
}

variable "bucket_name" {
  description = "Object Storage bucket name"
  type        = string
  default     = "demo-oke-bucket"
}

variable "customer_secret_key_display_name" {
  description = "Display name for customer secret key"
  type        = string
  default     = "demo-oke-accesskey"
}

variable "psql_admin_username" {
  description = "OCI PostgreSQL DB system admin user name"
  type        = string
  default = "admin"
}

variable "psql_admin_password" {
  description = "Admin password for OCI PostgreSQL DB system"
  type        = string
  sensitive   = true
  default = "Admin123!"
}

variable "psql_dicom_username" {
  description = "Orthanc DB user name"
  type        = string
  default     = "dicom"
}

variable "psql_dicom_password" {
  description = "Password for dicom user (orthanc DB)"
  type        = string
  default     = "Dicom123!"
  sensitive   = true
}

variable "psql_db_version" {
  description = "PostgreSQL version string for DB system"
  type        = string
  default     = "15"
}

variable "psql_shape" {
  description = "Shape for OCI PostgreSQL DB system"
  type        = string
  default     = "PostgreSQL.VM.Standard.E5.Flex"
}

variable "psql_ocpus" {
  description = "OCPUs per DB instance"
  type        = number
  default     = 2
}

variable "psql_memory_gbs" {
  description = "Memory (GB) per DB instance"
  type        = number
  default     = 16
}

variable "test_vm_image_ocid" {
  description = "OCID of the Oracle Linux image to use for the test VM"
  type        = string
  # using "Oracle-Linux-8.10-2025.11.20-0"
  default     = "ocid1.image.oc1.iad.aaaaaaaazigqixefhjb6jew2etuzox5erpff6wjtjhe5lzextgxm76jymz2q"
}

locals {
  ssh_public_key = file(var.ssh_public_key_path)
}