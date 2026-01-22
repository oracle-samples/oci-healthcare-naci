############################################################
# Dynamic Group + IAM Policies for OKE workloads
############################################################

resource "oci_identity_dynamic_group" "oke_nodes" {
  compartment_id = var.tenancy_ocid
  name           = "demo-oke-nodes-dg"
  description    = "All compute instances in the OKE compartment"

  matching_rule = "ALL {instance.compartment.id = '${var.compartment_ocid}'}"
}

resource "oci_identity_policy" "oke_workload_policy" {
  compartment_id = var.tenancy_ocid
  name           = "demo-oke-workload-policy"
  description    = "OKE workloads can manage bucket and use PostgreSQL DB systems"

  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.oke_nodes.name} to manage object-family in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.oke_nodes.name} to use postgres-db-systems in compartment id ${var.compartment_ocid}"
  ]
}
