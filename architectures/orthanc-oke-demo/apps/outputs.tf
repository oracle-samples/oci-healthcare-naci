output "orthanc_helm_release_info" {
  description = "Orthanc Helm release info"
  value = {
    status    = helm_release.orthanc.status
    metadata  = helm_release.orthanc.metadata
  }
}