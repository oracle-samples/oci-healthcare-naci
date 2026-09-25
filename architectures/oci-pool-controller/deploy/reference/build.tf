resource "oci_ons_notification_topic" "build" {
  count          = var.build_function_image ? 1 : 0
  compartment_id = var.controller_compartment_ocid
  name           = "${var.name_prefix}-build-${local.suffix}"
  description    = "Controller build project events; no subscriptions created"
  freeform_tags  = local.tags
}

resource "oci_devops_project" "function" {
  count          = var.build_function_image ? 1 : 0
  compartment_id = var.controller_compartment_ocid
  name           = "${var.name_prefix}-build-${local.suffix}"
  description    = "Native x86 controller image builds only"
  notification_config { topic_id = oci_ons_notification_topic.build[0].id }
  freeform_tags = local.tags
}

resource "oci_devops_repository" "function" {
  count           = var.build_function_image ? 1 : 0
  project_id      = oci_devops_project.function[0].id
  name            = "controller-source"
  repository_type = "HOSTED"
  # Let OCI own its canonical default branch; the build pins an explicit branch.
  description   = "Allowlisted Function and build files from the applied stack"
  freeform_tags = local.tags
}

resource "oci_devops_build_pipeline" "function" {
  count         = var.build_function_image ? 1 : 0
  project_id    = oci_devops_project.function[0].id
  display_name  = "controller-native-x86"
  freeform_tags = local.tags
  build_pipeline_parameters {
    items {
      name          = "POOL_SOURCE_SHA256"
      default_value = jsonencode(local.function_source_checksums)
      description   = "Exact Function source checksums from the Terraform package"
    }
  }
}

resource "oci_devops_deploy_artifact" "function" {
  count                      = var.build_function_image ? 1 : 0
  project_id                 = oci_devops_project.function[0].id
  display_name               = "controller-image"
  deploy_artifact_type       = "DOCKER_IMAGE"
  argument_substitution_mode = "SUBSTITUTE_PLACEHOLDERS"
  deploy_artifact_source {
    deploy_artifact_source_type = "OCIR"
    image_uri                   = "${local.image_repository_url}:$${CONTROLLER_IMAGE_TAG}"
  }
  freeform_tags = local.tags
}

resource "oci_logging_log" "build" {
  count         = var.build_function_image ? 1 : 0
  display_name  = "${var.name_prefix}-build"
  log_group_id  = oci_logging_log_group.controller.id
  log_type      = "SERVICE"
  is_enabled    = true
  freeform_tags = local.tags
  configuration {
    compartment_id = var.controller_compartment_ocid
    source {
      category    = "all"
      resource    = oci_devops_project.function[0].id
      service     = "devops"
      source_type = "OCISERVICE"
    }
  }
}

resource "oci_identity_dynamic_group" "build" {
  provider       = oci.home
  count          = var.build_function_image && var.create_build_iam_resources ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-build-${local.suffix}"
  description    = "Only this controller image build pipeline"
  matching_rule  = "ALL {resource.type = 'devopsbuildpipeline', resource.id = '${oci_devops_build_pipeline.function[0].id}'}"
}

locals {
  build_policy_statements = var.build_function_image ? [
    "Allow dynamic-group ${var.name_prefix}-build-${local.suffix} to read devops-repository in compartment id ${var.controller_compartment_ocid} where target.repository.id = '${oci_devops_repository.function[0].id}'",
    # Delivery rejected the exact-artifact conditional read in live testing.
    # Metadata access is compartment-scoped; artifact mutation is not granted.
    "Allow dynamic-group ${var.name_prefix}-build-${local.suffix} to read devops-deploy-artifact in compartment id ${var.controller_compartment_ocid}",
    "Allow dynamic-group ${var.name_prefix}-build-${local.suffix} to inspect repos in compartment id ${var.registry_compartment_ocid}",
    # Standard OCIR management role, constrained to this stack's exact repository.
    # Includes repository lifecycle permissions; never grant it across the tenancy.
    "Allow dynamic-group ${var.name_prefix}-build-${local.suffix} to manage repos in compartment id ${var.registry_compartment_ocid} where target.repo.name = '${oci_artifacts_container_repository.function[0].display_name}'",
  ] : []
}

resource "oci_identity_policy" "build" {
  provider       = oci.home
  count          = var.build_function_image && var.create_build_iam_resources ? 1 : 0
  compartment_id = var.tenancy_ocid
  name           = "${var.name_prefix}-build-${local.suffix}"
  description    = "Build reads source/artifact metadata and manages only its private repository; no Compute, Functions or secrets access"
  statements     = local.build_policy_statements
  depends_on     = [oci_identity_dynamic_group.build]
}

# DevOps fetches build_spec.yaml before entering any pipeline stage. A WAIT
# stage cannot cover first-use IAM propagation, so delay submission itself.
resource "terraform_data" "build_iam_ready" {
  count = var.build_function_image && var.create_build_iam_resources ? 1 : 0
  triggers_replace = {
    policy_id   = oci_identity_policy.build[0].id
    policy_hash = sha256(jsonencode(local.build_policy_statements))
    group_rule  = oci_identity_dynamic_group.build[0].matching_rule
  }
  provisioner "local-exec" {
    command = "python3 -c 'import time; time.sleep(180)'"
  }
  depends_on = [oci_identity_policy.build]
}

resource "oci_devops_build_pipeline_stage" "build" {
  count                              = var.build_function_image ? 1 : 0
  build_pipeline_id                  = oci_devops_build_pipeline.function[0].id
  build_pipeline_stage_type          = "BUILD"
  display_name                       = "Build and verify native x86 controller"
  description                        = "Verify packaged source and build a native linux/amd64 Function image"
  image                              = "OL8_X86_64_STANDARD_10"
  primary_build_source               = "controller"
  build_spec_file                    = "build_spec.yaml"
  stage_execution_timeout_in_seconds = 1800
  build_runner_shape_config { build_runner_type = "DEFAULT" }
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline.function[0].id }
  }
  build_source_collection {
    items {
      connection_type = "DEVOPS_CODE_REPOSITORY"
      name            = "controller"
      repository_id   = oci_devops_repository.function[0].id
      repository_url  = oci_devops_repository.function[0].http_url
      branch          = "build-${terraform_data.function_image_build[0].id}"
    }
  }
  freeform_tags = local.tags
}

resource "oci_devops_build_pipeline_stage" "deliver" {
  count                     = var.build_function_image ? 1 : 0
  build_pipeline_id         = oci_devops_build_pipeline.function[0].id
  build_pipeline_stage_type = "DELIVER_ARTIFACT"
  display_name              = "Deliver verified x86 image to private OCIR"
  description               = "Deliver the verified image using the scoped build resource principal"
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline_stage.build[0].id }
  }
  deliver_artifact_collection {
    items {
      artifact_id   = oci_devops_deploy_artifact.function[0].id
      artifact_name = "controller-image"
    }
  }
  freeform_tags = local.tags
}

resource "oci_devops_build_run" "function" {
  count             = var.build_function_image ? 1 : 0
  build_pipeline_id = oci_devops_build_pipeline.function[0].id
  display_name      = "controller-${substr(local.function_source_hash, 0, 12)}"
  build_run_arguments {
    items {
      name  = "POOL_SOURCE_SHA256"
      value = jsonencode(local.function_source_checksums)
    }
  }
  freeform_tags = local.tags
  lifecycle {
    replace_triggered_by = [terraform_data.function_image_build]
    # The provider's create waiter requires SUCCEEDED. Avoid a redundant self
    # postcondition: Terraform 1.5 can emit Invalid index after failed creation.
  }
  timeouts { create = "45m" }
  depends_on = [oci_devops_build_pipeline_stage.deliver, terraform_data.build_iam_ready, oci_logging_log.build]
}
