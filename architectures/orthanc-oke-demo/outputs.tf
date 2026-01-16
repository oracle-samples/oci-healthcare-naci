############################################################
# Outputs
############################################################

output "orthanc_config_path" {
  description = "Path to the generated JSON config file"
  value       = local_file.orthanc_config.filename
}

output "bucket_s3_endpoint" {
  description = "S3-compatible endpoint base"
  value       = local.bucket_endpoint
}

output "bucket_name" {
  value = oci_objectstorage_bucket.demo_bucket.name
}

output "postgres_primary_endpoint" {
  value = "${data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].fqdn}:${data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].port}"
}

output "oke_cluster_id" {
  value = oci_containerengine_cluster.oke.id
}

output "kubeconfig_path" {
  value = local_file.kubeconfig.filename
}

output "public_test_vm_ip" {
  description = "Public IP of the Oracle Linux VM"
  value       = oci_core_instance.test_vm.public_ip
}
