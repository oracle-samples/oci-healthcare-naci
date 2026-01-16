# =========================
# Network / Access
# Requests from these IPs are allowed to access provisioned resources
# =========================
public_allowed_ips = [
  "x.x.x.x/32", # home IP
  "x.x.x.x/32" # office IP 
]

vcn_cidr             = "10.0.0.0/16"
public_subnet_cidr   = "10.0.1.0/24"
private_subnet_cidr  = "10.0.2.0/24"

# =========================
# OCI Authentication
# =========================
tenancy_ocid      = "<oci_tenancy_ocid>"
user_ocid         = "<oci_user_ocid>"
fingerprint       = "<api_key_fingerprint>"
private_key_path  = "<local_path_to_oci_api_private_key>"
region            = "<oci_region>"
compartment_ocid  = "<oci_compartment_ocid>"

# =========================
# SSH
# =========================
ssh_public_key_path = "<local_path_to_ssh_public_key>"

# =========================
# OKE Cluster
# =========================
cluster_name                 = "demo-oke-cluster"
cluster_kubernetes_version   = "v1.33.1"

node_pool_size   = 2
node_shape       = "VM.Standard.E5.Flex"
node_ocpus       = 2
node_memory_gbs  = 16

# using "Oracle-Linux-8.10-2025.09.16-0-OKE-1.33.1-1330"
node_image_ocid = "ocid1.image.oc1.iad.aaaaaaaadbeckykfjep2ktkxajpbpnxjj726pzlxmrxvo5fapaa2laz5sgbq"

# =========================
# Object Storage
# =========================
bucket_name = "demo-oke-bucket"

customer_secret_key_display_name = "demo-oke-accesskey"

# =========================
# PostgreSQL DB System
# =========================
psql_admin_username = "<username>" # set username
psql_admin_password = "<password>" # set password

psql_dicom_username = "<username>" # set username
psql_dicom_password = "<password>" # set password

psql_db_version = "15"
psql_shape      = "PostgreSQL.VM.Standard.E5.Flex"
psql_ocpus      = 2
psql_memory_gbs = 16

# =========================
# Test VM
# =========================
# using "Oracle-Linux-8.10-2025.11.20-0"
test_vm_image_ocid = "ocid1.image.oc1.iad.aaaaaaaazigqixefhjb6jew2etuzox5erpff6wjtjhe5lzextgxm76jymz2q"