output "function_ocid" {
  value = oci_functions_function.controller.id
}

output "function_image" {
  description = "Deployed OCIR image address; automatically generated in source-build mode."
  value       = oci_functions_function.controller.image
}

output "build_review" {
  description = "Native x86 build, delivery and scoped IAM evidence."
  value = var.build_function_image ? {
    project_id        = oci_devops_project.function[0].id
    pipeline_id       = oci_devops_build_pipeline.function[0].id
    run_id            = oci_devops_build_run.function[0].id
    image_id          = try(local.built_registry_image.id, null)
    image_tag         = local.built_image_tag
    image_digest      = local.effective_image_digest
    source_repository = oci_devops_repository.function[0].id
    source_checksums  = local.function_source_checksums
    builder_image     = oci_devops_build_pipeline_stage.build[0].image
    policy_statements = local.build_policy_statements
    home_region       = local.home_region
  } : null
}

output "function_image_digest" {
  description = "Immutable image digest resolved by OCI Functions for the deployed image."
  value       = oci_functions_function.controller.image_digest
}

output "invoke_endpoint" {
  description = "OCI-signed Functions endpoint, not a public unauthenticated application URL."
  value       = oci_functions_function.controller.invoke_endpoint
}

output "application_ocid" {
  value = oci_functions_application.controller.id
}

output "log_group_ocid" {
  value = oci_logging_log_group.controller.id
}

output "ledger" {
  value = {
    namespace = oci_objectstorage_bucket.ledger.namespace
    bucket    = oci_objectstorage_bucket.ledger.name
  }
}

output "iam_review" {
  description = "Have the central IAM team apply these when create_iam_resources=false; review dependencies before enabling writes."
  value = {
    dynamic_group_name           = local.dynamic_group_name
    dynamic_group_matching_rule  = local.dynamic_group_matching_rule
    faas_policy_statements       = local.faas_policy_statements
    controller_policy_statements = local.controller_policy_statements
    invoker_policy_statements    = local.invoker_policy_statements
  }
}

output "pool_registry" {
  description = "Exact pinned pool registry to review in every Plan. Terraform does not retag or resize these pools."
  value       = local.profiles
}

output "controller_scope_id" {
  description = "Stable HarnessId value for pools, instance configurations and launch tags enrolled into this controller. Persist this value; later enrollment reuses the same Function and ledger."
  value       = module.enrollment.scope_id
}

output "enrollment_review" {
  description = "Plan-time discovery/manual selection, controller group, included/excluded OCIDs and exact profile limits. New discovery results take effect only through a subsequent Apply."
  value       = merge(module.enrollment.review, { pools = local.profiles })
}
