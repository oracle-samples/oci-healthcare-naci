module "landing_zone_network" {
  source = "github.com/oci-landing-zones/terraform-oci-modules-networking?ref=main"

  tenancy_ocid          = var.tenancy_ocid
  network_configuration = local.network_configuration
}

#For Future use
#module "oke" {
#  source                 = "github.com/oci-landing-zones/terraform-oci-modules-workloads//cis-oke?ref=main"
#  clusters_configuration = local.clusters_configuration
#  workers_configuration  = local.workers_configuration
#  depends_on             = [module.landing_zone_network]
#}

resource "oci_kms_vault" "my_vault" {
  compartment_id = var.compartment_id
  display_name   = "Mirth Demo Vault"
  vault_type     = "DEFAULT"
  timeouts {
    create = "80m"
  }
}

resource "oci_kms_key" "my_key" {
  compartment_id      = var.compartment_id
  display_name        = "vm-kms-key"
  key_shape {
    algorithm = "AES"
    length    = 32
  }
  management_endpoint = oci_kms_vault.my_vault.management_endpoint
}

resource "tls_private_key" "instance_ssh_key_pair" {
  algorithm = "RSA" # Or "ED25519" for a more modern algorithm
  rsa_bits  = 4096  # Recommended bit length for RSA
}

resource "oci_vault_secret" "instance_ssh_key_private" {
  compartment_id    = var.compartment_id
  vault_id          = oci_kms_vault.my_vault.id
  key_id            = oci_kms_key.my_key.id
  secret_content {
    content_type = "BASE64"
    content      = base64encode(tls_private_key.instance_ssh_key_pair.private_key_openssh)
  }
  secret_name       = "instance-private-key"
  description       = "SSH Private Key stored as a secret"
}

resource "oci_vault_secret" "instance_ssh_key_public" {
  compartment_id    = var.compartment_id
  vault_id          = oci_kms_vault.my_vault.id
  key_id            = oci_kms_key.my_key.id
  secret_content {
    content_type = "BASE64"
    content      = base64encode(tls_private_key.instance_ssh_key_pair.public_key_openssh)
  }
  secret_name       = "instance-public-key"
  description       = "SSH Public Key stored as a secret"
}

# Terraform script to deploy a VM in OCI and install Mirth Connect

# Create a VM instance
resource "oci_core_instance" "mirth_vm" {
  availability_domain = "${lookup(data.oci_identity_availability_domains.ADs.availability_domains[0],"name")}"
  compartment_id      = var.compartment_id
  display_name        = "Mirth-Connect-VM"
  shape               = "VM.Standard.E3.Flex" # Adjust shape as needed
  shape_config {
    #Optional
    memory_in_gbs = 4
    ocpus         = 1
  }

  create_vnic_details {
    #subnet_id        = module.landing_zone_network.provisioned_networking_resources.subnets["${var.services_subnet_name}"].id
    subnet_id                = data.oci_core_subnets.app_subnet.subnets[0].id
    assign_public_ip = false # Set to false if public IP is not required
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.oracle_linux.images[0].id # Use latest Oracle Linux image
  }

  agent_config {
    are_all_plugins_disabled = false
    is_management_disabled   = false
    is_monitoring_disabled   = false
    plugins_config {
      name   = "Bastion"
      desired_state = "ENABLED"
    }
  }

  metadata = {
    ssh_authorized_keys = tls_private_key.instance_ssh_key_pair.public_key_openssh
    user_data           = base64encode(file("user_data.sh")) # Cloud-init script for Mirth installation
  }
  depends_on = [
   module.landing_zone_network
  ]
}

resource "oci_bastion_bastion" "oci_bastion" {
  compartment_id                  = var.compartment_id
  bastion_type                    = "STANDARD" # OCI Bastion Service
  #target_subnet_id                = module.landing_zone_network.provisioned_networking_resources.subnets["sub-private-app"].id
  target_subnet_id                = data.oci_core_subnets.pub_subnet.subnets[0].id
  name                            = "Mirth-Demo-Bastion"
  client_cidr_block_allow_list    = [ var.client_cidr_block_allow_list ]
  #max_session_ttl_in_seconds      = var.max_session_ttl_in_seconds
  depends_on = [
    module.landing_zone_network
  ]
}

resource "oci_bastion_session" "demo_bastionsession" {

  bastion_id = oci_bastion_bastion.oci_bastion.id

  key_details {
    public_key_content = tls_private_key.instance_ssh_key_pair.public_key_openssh
  }

  target_resource_details {

    session_type       = "MANAGED_SSH"
    target_resource_id = oci_core_instance.mirth_vm.id
    target_resource_operating_system_user_name = "opc"
    target_resource_port                       = "22"
  }

  session_ttl_in_seconds = 3600

  display_name = "bastionsession-private-host"
  depends_on = [

  ]
}

# Data source to fetch the latest Oracle Linux image
data "oci_core_images" "oracle_linux" {
  compartment_id           = var.compartment_id
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = "VM.Standard.E3.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

data "oci_identity_availability_domains" "ADs" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_vcns" "simple_vcn" {
  compartment_id = var.compartment_id
  display_name = "vcn-simple"
}

data "oci_core_subnets" "app_subnet" {
  # Required: The OCID of the compartment where the subnet resides
  compartment_id = var.compartment_id
  # Optional: Filter by display_name (the name you gave the subnet)
  display_name = "sub-private-app"

  # Required: The OCID of the VCN the subnet belongs to
  vcn_id         = data.oci_core_vcns.simple_vcn.virtual_networks[0].id
}

data "oci_core_subnets" "db_subnet" {
  # Required: The OCID of the compartment where the subnet resides
  compartment_id = var.compartment_id
  # Optional: Filter by display_name (the name you gave the subnet)
  display_name = "sub-private-db"

  # Required: The OCID of the VCN the subnet belongs to
  vcn_id         = data.oci_core_vcns.simple_vcn.virtual_networks[0].id
}

data "oci_core_subnets" "pub_subnet" {
  # Required: The OCID of the compartment where the subnet resides
  compartment_id = var.compartment_id
  # Optional: Filter by display_name (the name you gave the subnet)
  display_name = "sub-public-lb"

  # Required: The OCID of the VCN the subnet belongs to
  vcn_id         = data.oci_core_vcns.simple_vcn.virtual_networks[0].id
}

output "vcn_id" {
 # value = data.oci_core_vcns.simple_vcn.virtual_networks[0].id
  value = data.oci_core_vcns.simple_vcn.virtual_networks[0].id
}
output "app_subnets" {
  value = data.oci_core_subnets.app_subnet.subnets[0].id
}

output "private_key" {
  value = tls_private_key.instance_ssh_key_pair.private_key_openssh
  sensitive = true
}
# Output the public IP of the VM
output "connection_details" {
  value = oci_bastion_session.demo_bastionsession.ssh_metadata.command
}
