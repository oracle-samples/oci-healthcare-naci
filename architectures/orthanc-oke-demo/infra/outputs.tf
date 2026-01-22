############################################################
# Outputs (infra)
############################################################

output "oke_cluster_id" {
  value = oci_containerengine_cluster.oke.id
}

output "oke_node_pool_id" {
  value = oci_containerengine_node_pool.node_pool.id
}

output "bucket_s3_endpoint" {
  description = "S3-compatible endpoint base"
  value       = local.bucket_endpoint
}

output "bucket_name" {
  value = oci_objectstorage_bucket.demo_bucket.name
}

output "s3_access_key_id" {
  description = "Customer secret key OCID (used as access key ID in this demo)"
  value       = oci_identity_customer_secret_key.s3_key.id
}

output "s3_secret_key" {
  description = "Customer secret key value (ONLY available at creation time; stored in state)"
  value       = oci_identity_customer_secret_key.s3_key.key
  sensitive   = true
}

output "postgres_primary_fqdn" {
  value = data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].fqdn
}

output "postgres_primary_port" {
  value = data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].port
}

output "public_test_vm_ip" {
  description = "Public IP of the Oracle Linux VM"
  value       = oci_core_instance.test_vm.public_ip
}
