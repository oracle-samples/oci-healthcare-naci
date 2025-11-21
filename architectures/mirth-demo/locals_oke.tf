locals {
  clusters_configuration = {
    default_compartment_id = var.compartment_id
    clusters = {
      OKE1 = {
        name        = "oke-npn-cluster"
        cni_type    = "native"
        is_enhanced = true
        networking = {
          vcn_id                 = module.landing_zone_network.provisioned_networking_resources.vcns["OKE-VCN"].id
          api_endpoint_subnet_id = module.landing_zone_network.provisioned_networking_resources.subnets["oke-api-subnet"].id
          services_subnet_id     = [module.landing_zone_network.provisioned_networking_resources.subnets["oke-services-subnet"].id]
          api_endpoint_nsg_ids   = [module.landing_zone_network.provisioned_networking_resources.network_security_groups["NSG-API"].id]
        }
      }
    }
  }
  workers_configuration = {
    ssh_public_key    = try(base64decode(var.ssh_public_key), var.ssh_public_key)
    node_pools = {
      NODEPOOL1 = {
        cluster_id = "OKE1"
        name       = "node-pool"
        size       = 1
        networking = {
          workers_subnet_id = module.landing_zone_network.provisioned_networking_resources.subnets["oke-workers-subnet"].id
          pods_subnet_id    = module.landing_zone_network.provisioned_networking_resources.subnets["oke-pods-subnet"].id
          workers_nsg_ids   = [module.landing_zone_network.provisioned_networking_resources.network_security_groups["NSG-WORKERS"].id]
          pods_nsg_ids      = [module.landing_zone_network.provisioned_networking_resources.network_security_groups["NSG-PODS"].id]
        }
        node_config_details = {
          node_shape = "VM.Standard.E5.Flex"
        }
      }
    }
  }
}