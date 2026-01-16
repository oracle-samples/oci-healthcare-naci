############################################################
# OKE Cluster + Node Pool
############################################################

resource "oci_containerengine_cluster" "oke" {
  compartment_id     = var.compartment_ocid
  name               = var.cluster_name
  kubernetes_version = var.cluster_kubernetes_version
  vcn_id             = oci_core_vcn.demo_vcn.id
  type               = "BASIC_CLUSTER"

  endpoint_config {
    is_public_ip_enabled = true
    subnet_id            = oci_core_subnet.public_subnet.id
  }

  options {
    service_lb_subnet_ids = [oci_core_subnet.public_subnet.id]
  }
}

resource "oci_containerengine_node_pool" "node_pool" {
  compartment_id     = var.compartment_ocid
  cluster_id         = oci_containerengine_cluster.oke.id
  name               = "${var.cluster_name}-pool"
  kubernetes_version = var.cluster_kubernetes_version

  node_config_details {
    size = var.node_pool_size

    placement_configs {
      availability_domain = local.ad_name
      subnet_id           = oci_core_subnet.private_subnet.id
    }
  }

  node_shape = var.node_shape

  node_shape_config {
    ocpus         = var.node_ocpus
    memory_in_gbs = var.node_memory_gbs
  }

  node_source_details {
    source_type = "IMAGE"
    image_id    = var.node_image_ocid
  }

  node_metadata = {
    ssh_authorized_keys = local.ssh_public_key
  }
}

############################################################
# Get kubeconfig
############################################################
data "oci_containerengine_cluster_kube_config" "kubeconfig" {
  cluster_id = oci_containerengine_cluster.oke.id
}

resource "local_file" "kubeconfig" {
  content  = data.oci_containerengine_cluster_kube_config.kubeconfig.content
  filename = "${path.module}/kubeconfig"
}