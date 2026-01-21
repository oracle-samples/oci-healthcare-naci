############################################################
# Basic data sources (AD, ObjectStorage namespace)
############################################################

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

locals {
  ad_name = data.oci_identity_availability_domains.ads.availability_domains[0].name
}

data "oci_objectstorage_namespace" "ns" {}

############################################################
# Networking: VCN, IGW, NAT GW, Service GW, route tables, subnets
############################################################

resource "oci_core_vcn" "demo_vcn" {
  compartment_id = var.compartment_ocid
  display_name   = "demo-vcn"
  cidr_block     = var.vcn_cidr
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  display_name   = "demo-igw"
  vcn_id         = oci_core_vcn.demo_vcn.id
  enabled        = true
}

# --------------------------
# NEW: NAT Gateway (private subnet -> internet egress)
# --------------------------
resource "oci_core_nat_gateway" "nat" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.demo_vcn.id
  display_name   = "demo-nat-gw"
}

# --------------------------
# NEW: Service Gateway (private subnet -> OCI services, e.g. Object Storage)
# --------------------------
data "oci_core_services" "all_oci_services" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

resource "oci_core_service_gateway" "sgw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.demo_vcn.id
  display_name   = "demo-service-gw"

  services {
    service_id = data.oci_core_services.all_oci_services.services[0].id
  }
}

resource "oci_core_route_table" "public_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.demo_vcn.id
  display_name   = "public-rt"

  route_rules {
    network_entity_id = oci_core_internet_gateway.igw.id
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
  }
}

# --------------------------
# NEW: Private route table (0.0.0.0/0 -> NAT, OCI services -> Service GW)
# --------------------------
resource "oci_core_route_table" "private_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.demo_vcn.id
  display_name   = "private-rt"

  # Internet egress (Docker image pulls, external APIs, etc.)
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.nat.id
  }

  # Private access to OCI services (Object Storage, etc.)
  route_rules {
    destination       = data.oci_core_services.all_oci_services.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.sgw.id
  }
}

resource "oci_core_security_list" "private_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.demo_vcn.id
  display_name   = "private-subnet-sl"

  # Allow all intra-VCN (nodes <-> nodes, control plane <-> nodes, DB access, etc.)
  ingress_security_rules {
    protocol = "all"
    source   = var.vcn_cidr
  }

  # Allow all egress (NAT/SGW will route appropriately)
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
}

resource "oci_core_security_list" "public_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.demo_vcn.id
  display_name   = "public-subnet-sl"

  # Allow all intra-VCN (nodes <-> nodes, control plane <-> nodes, DB access, etc.)
  ingress_security_rules {
    protocol = "all"
    source   = var.vcn_cidr
  }
  
  dynamic "ingress_security_rules" {
    for_each = var.public_allowed_ips
    content {
      protocol = "6" # TCP
      source   = ingress_security_rules.value
    }
  }

  # Allow all egress (NAT/SGW will route appropriately)
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
}

resource "oci_core_subnet" "public_subnet" {
  compartment_id               = var.compartment_ocid
  vcn_id                       = oci_core_vcn.demo_vcn.id
  cidr_block                   = var.public_subnet_cidr
  display_name                 = "public-subnet"
  prohibit_public_ip_on_vnic   = false
  route_table_id               = oci_core_route_table.public_rt.id
  security_list_ids            = [oci_core_security_list.public_sl.id]
}

resource "oci_core_subnet" "private_subnet" {
  compartment_id               = var.compartment_ocid
  vcn_id                       = oci_core_vcn.demo_vcn.id
  cidr_block                   = var.private_subnet_cidr
  display_name                 = "private-subnet"
  prohibit_public_ip_on_vnic   = true
  security_list_ids            = [oci_core_security_list.private_sl.id]

  # NEW: attach private route table so nodes/DB can reach internet via NAT and OCI services via SGW
  route_table_id               = oci_core_route_table.private_rt.id
}
