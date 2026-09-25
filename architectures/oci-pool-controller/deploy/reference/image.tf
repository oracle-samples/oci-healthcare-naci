# Resource Manager orchestrates a native x86 DevOps build, never a local build.
locals {
  function_source_dir = abspath("${path.module}/../../function")
  function_source_checksums = {
    for name in ["Dockerfile", "func.py", "requirements.txt"] : name => filesha256("${local.function_source_dir}/${name}")
  }
  function_source_hash = sha256(join("", concat(
    [for name in sort(keys(local.function_source_checksums)) : local.function_source_checksums[name]],
    [for name in ["publish_build_source.py", "native_build.py", "build_spec.yaml"] : filesha256("${path.module}/${name}")]
  )))
  # Use the region-key endpoint documented for DevOps artifact delivery.
  # It addresses the same regional OCIR repository; no registry is recreated.
  registry_region_key = lower(one([for region in data.oci_identity_regions.available.regions : region.key
  if region.name == var.region]))
  registry_endpoint = "${local.registry_region_key}.ocir.io"
  image_repository_url = var.build_function_image ? join("/", [
    local.registry_endpoint,
    data.oci_objectstorage_namespace.current.namespace,
    oci_artifacts_container_repository.function[0].display_name
  ]) : ""
  # build_spec.yaml derives the unique tag from this exact run OCID. DevOps can
  # report successful OCIR delivery with blank image_uri/hash output fields.
  built_image_tag = var.build_function_image ? "build-${regex("[^.]+$", oci_devops_build_run.function[0].id)}" : ""
  delivery_confirmed = var.build_function_image ? try(
    oci_devops_build_run.function[0].state == "SUCCEEDED" && length([
      for variable in oci_devops_build_run.function[0].build_outputs[0].exported_variables[0].items : variable
      if variable.name == "CONTROLLER_IMAGE_TAG" && variable.value == local.built_image_tag
      ]) == 1 && length([
      for artifact in oci_devops_build_run.function[0].build_outputs[0].delivered_artifacts[0].items : artifact
      # Provider 8.29 also drops the delivered artifact's name and ID.
      if artifact.artifact_type == "OCIR"
    ]) == 1, false
  ) : true
  built_registry_image = var.build_function_image ? try(one([
    for image in data.oci_artifacts_container_images.built[0].container_image_collection[0].items : image
    if image.repository_id == oci_artifacts_container_repository.function[0].id &&
    image.compartment_id == var.registry_compartment_ocid && image.state == "AVAILABLE" &&
    contains(concat([image.version], try([for version in image.versions : version.version], [])), local.built_image_tag)
  ]), null) : null
  effective_function_image = var.build_function_image ? "${local.image_repository_url}:${local.built_image_tag}" : trimspace(var.function_image)
  effective_image_digest   = var.build_function_image ? try(local.built_registry_image.digest, null) : (var.function_image_digest == "" ? null : var.function_image_digest)
  effective_function_shape = var.build_function_image ? "GENERIC_X86" : var.function_shape
}

data "oci_artifacts_container_images" "built" {
  count          = var.build_function_image ? 1 : 0
  compartment_id = var.registry_compartment_ocid
  repository_id  = oci_artifacts_container_repository.function[0].id
  version        = local.built_image_tag
  state          = "AVAILABLE"
  depends_on     = [oci_devops_build_run.function]
}

resource "oci_artifacts_container_repository" "function" {
  count          = var.build_function_image ? 1 : 0
  compartment_id = var.registry_compartment_ocid
  display_name   = "${lower(replace(var.name_prefix, "_", "-"))}-${local.suffix}/function"
  is_public      = false
  # Do not set is_immutable: OCIR can reject this optional API property even
  # though the provider exposes it. Privacy and deletion protection are separate.
  freeform_tags = local.tags

  # The deployed Function may still reference this repository when changing
  # deployment modes. Never delete its images as a side effect of that switch.
  lifecycle {
    prevent_destroy = true
  }
}

# Preserve this address for upgrades; it now publishes source, not an image.
resource "terraform_data" "function_image_build" {
  count = var.build_function_image ? 1 : 0
  triggers_replace = {
    source_hash       = local.function_source_hash
    repository_id     = oci_artifacts_container_repository.function[0].id
    source_repository = oci_devops_repository.function[0].id
  }

  lifecycle {
    precondition {
      condition     = length(trimspace(var.ocir_username)) > 0 && length(trimspace(var.ocir_auth_token)) > 0
      error_message = "Automatic builds require an OCI username and auth token to publish source to the private OCI code repository. The build runner never receives this token."
    }
  }

  provisioner "local-exec" {
    command = "python3 \"${path.module}/publish_build_source.py\""
    environment = {
      POOL_SOURCE_REPOSITORY   = oci_devops_repository.function[0].http_url
      POOL_SOURCE_BRANCH       = "build-${self.id}"
      POOL_SOURCE_USERNAME     = "${data.oci_identity_tenancy.current.name}/${trimspace(var.ocir_username)}"
      POOL_SOURCE_AUTH_TOKEN   = var.ocir_auth_token
      POOL_FUNCTION_SOURCE_DIR = local.function_source_dir
    }
  }
}
