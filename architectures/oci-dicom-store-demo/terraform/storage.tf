resource "oci_objectstorage_bucket" "dicom" {
  compartment_id = var.compartment_id
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = local.bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = var.enable_object_versioning ? "Enabled" : "Disabled"
  auto_tiering   = "Disabled"

  object_events_enabled = true
  freeform_tags         = local.tags
}
