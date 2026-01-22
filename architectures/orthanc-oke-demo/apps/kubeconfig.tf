############################################################
# Fetch kubeconfig from OKE (using OCI provider) and configure
# Kubernetes + Helm providers WITHOUT relying on a kubeconfig file.
############################################################

data "oci_containerengine_cluster_kube_config" "this" {
  cluster_id = data.terraform_remote_state.infra.outputs.oke_cluster_id
}

locals {
  kubeconfig = yamldecode(data.oci_containerengine_cluster_kube_config.this.content)
}

provider "kubernetes" {
  host                   = local.kubeconfig.clusters[0].cluster.server
  cluster_ca_certificate = base64decode(local.kubeconfig.clusters[0].cluster["certificate-authority-data"])

  exec {
    api_version = local.kubeconfig.users[0].user.exec.apiVersion
    command     = local.kubeconfig.users[0].user.exec.command
    args        = local.kubeconfig.users[0].user.exec.args
  }
}

provider "helm" {
  kubernetes = {
    host                   = local.kubeconfig.clusters[0].cluster.server
    cluster_ca_certificate = base64decode(local.kubeconfig.clusters[0].cluster["certificate-authority-data"])

    exec = {
      api_version = local.kubeconfig.users[0].user.exec.apiVersion
      command     = local.kubeconfig.users[0].user.exec.command
      args        = local.kubeconfig.users[0].user.exec.args
    }
  }
}
