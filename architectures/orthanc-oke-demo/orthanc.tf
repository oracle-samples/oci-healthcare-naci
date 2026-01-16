############################################################
# JSON config file with accessKey, secretKey, bucket endpoint,
# bucket name/url, and DB connection info, as required by Orthanc
############################################################

locals {
  psql_connection = format(
      "postgresql://%s:%s@%s:%d/orthanc?sslmode=prefer",
      var.psql_dicom_username,
      var.psql_dicom_password,
      data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].fqdn,
      data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].port
  )
}

resource "local_file" "orthanc_config" {
  filename = "${path.module}/orthanc.json"

  content = jsonencode({
    Name = "Demo Archive"
    HttpPort = 8042
    DicomAet = "ARCHIVE"
    DicomPort = 104
    AuthenticationEnabled = false
    RemoteAccessAllowed = true

    AwsS3Storage = {
        BucketName = oci_objectstorage_bucket.demo_bucket.name
        Region = var.region
        AccessKey = oci_identity_customer_secret_key.s3_key.id
        SecretKey = oci_identity_customer_secret_key.s3_key.key
        Endpoint  = local.bucket_endpoint
        VirtualAddressing = false
        # bucketUrl = local.bucket_url
    }
    
    PostgreSQL = {
      EnableIndex = true
      EnableStorage = false
      Lock = false
      ConnectionUri = local.psql_connection
    }

    Plugins = [
      "/usr/share/orthanc/plugins-available/libOrthancAwsS3Storage.so",
	    "/usr/share/orthanc/plugins-available/libOrthancPostgreSQLIndex.so",
	    "/usr/share/orthanc/plugins-available/libOrthancExplorer2.so",
	    "/usr/share/orthanc/plugins-available/libOrthancGdcm.so",
	    "/usr/share/orthanc/plugins-available/libOrthancDicomWeb.so"
    ]
  })
}

resource "kubernetes_secret_v1" "orthanc_json" {
  metadata {
    name      = "orthanc-json"
    namespace = "default"
  }

  binary_data = {  
    "orthanc.json" = base64encode(local_file.orthanc_config.content)
  }
  type = "Opaque"
}

############################################################
# Helm release
############################################################

resource "helm_release" "orthanc" {
  depends_on = [
    oci_containerengine_node_pool.node_pool,
    local_file.kubeconfig
  ]
  name      = "orthanc"
  namespace = "default"

  # Point Helm at your packaged chart
  chart = "${path.module}/helm/orthanc-oke-0.1.3.tgz"

  wait          = true
  timeout       = 300
  # atomic        = true
  # cleanup_on_fail = true
}