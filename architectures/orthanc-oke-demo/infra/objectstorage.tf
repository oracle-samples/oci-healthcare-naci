############################################################
# Object Storage bucket
############################################################

resource "oci_objectstorage_bucket" "demo_bucket" {
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  compartment_id = var.compartment_ocid
  name           = var.bucket_name
  access_type    = "NoPublicAccess"
}

############################################################
# Customer Secret Key (S3-compatible access key + secret)
############################################################

resource "oci_identity_customer_secret_key" "s3_key" {
  user_id      = var.user_ocid
  display_name = var.customer_secret_key_display_name
}

locals {
  bucket_endpoint = "https://${data.oci_objectstorage_namespace.ns.namespace}.compat.objectstorage.${var.region}.oraclecloud.com"
  bucket_url      = "${local.bucket_endpoint}/${oci_objectstorage_bucket.demo_bucket.name}"
}