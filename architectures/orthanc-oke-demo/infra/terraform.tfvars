# =========================
# Network / Access
# =========================
public_allowed_ips = [
  "x.x.x.x/32",   # Office
  "x.x.x.x/32"    # Home
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
private_key_path  = "<local_path_to_your_api_private_key>"
region            = "<oci_region>"
compartment_ocid  = "<compartment_ocid>"

# =========================
# SSH
# =========================
ssh_public_key_path = "<local_path_to_your_ssh_public_key>"

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
psql_admin_username = "<set_psql_admin_username_here>"
psql_admin_password = "<set_psql_admin_password_here>"
psql_dicom_username = "<set_psql_orthanc_db_username_here>"
psql_dicom_password = "<set_psql_orthanc_db_password_here>"

psql_db_version = "15"
psql_shape      = "PostgreSQL.VM.Standard.E5.Flex"
psql_ocpus      = 2
psql_memory_gbs = 16

# =========================
# Test VM
# =========================
# using "Oracle-Linux-8.10-2025.11.20-0"
test_vm_image_ocid = "ocid1.image.oc1.iad.aaaaaaaazigqixefhjb6jew2etuzox5erpff6wjtjhe5lzextgxm76jymz2q"

