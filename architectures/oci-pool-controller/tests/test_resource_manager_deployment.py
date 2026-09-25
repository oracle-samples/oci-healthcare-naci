"""Regression coverage for automatic and prebuilt Resource Manager packages.

These tests use only the standard library and local source files. Package
prefilling runs against an in-memory fixture, independently of release refreshes.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_reference", ROOT / "scripts/package-reference.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)
SCHEMA_PATH = "deploy/reference/schema.yaml"


def variable_lines(schema, name):
    """Read one variable block from the repository's two-space YAML layout."""
    variables = schema.split("\nvariables:\n", 1)[1].split("\noutputs:", 1)[0]
    lines = variables.splitlines()
    start = lines.index("  {}:".format(name)) + 1
    result = []
    for line in lines[start:]:
        if line.startswith("  ") and not line.startswith("   "):
            break
        result.append(line)
    return result


def default_value(schema, name):
    defaults = [line[len("    default: "):] for line in variable_lines(schema, name)
                if line.startswith("    default: ")]
    if len(defaults) != 1:
        raise AssertionError("Expected exactly one default for {}".format(name))
    return json.loads(defaults[0])


def group_lines(schema, title):
    groups = schema.split("\nvariableGroups:\n", 1)[1].split("\nvariables:", 1)[0]
    lines = groups.splitlines()
    start = lines.index("  - title: {}".format(title)) + 1
    result = []
    for line in lines[start:]:
        if line.startswith("  - title:"):
            break
        result.append(line)
    return result


class ResourceManagerDeploymentTests(unittest.TestCase):
    def test_cross_compartment_pool_launch_has_preflight_and_vnic_permissions(self):
        main = (ROOT / "deploy/reference/main.tf").read_text(encoding="utf-8")
        self.assertIn("use vnics in compartment id ${var.pool_compartment_ocid}", main)
        self.assertIn("use subnets in compartment id ${var.pool_compartment_ocid} where request.permission = 'SUBNET_ATTACH'", main)
        network_vnic_line = next(line for line in main.splitlines()
                                if "use vnics in compartment id ${var.network_compartment_ocid}" in line)
        for permission in ("VNIC_READ", "VNIC_CREATE", "VNIC_ATTACH", "VNIC_DETACH", "VNIC_DELETE"):
            self.assertIn("'" + permission + "'", network_vnic_line)
        self.assertNotIn("VNIC_UPDATE", network_vnic_line)

    def test_bounded_growth_is_opt_in_with_explicit_vm_and_timeout_guards(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        self.assertIs(default_value(schema, "enable_bounded_growth"), False)
        self.assertEqual(default_value(schema, "max_total_vms"), 25)
        self.assertEqual(default_value(schema, "launch_timeout_seconds"), 900)
        main = (ROOT / "deploy/reference/main.tf").read_text(encoding="utf-8")
        for env, variable in [("ENABLE_BOUNDED_GROWTH", "enable_bounded_growth"),
                              ("CONTROLLER_MAX_TOTAL_VMS", "max_total_vms"),
                              ("CONTROLLER_LAUNCH_TIMEOUT_SECONDS", "launch_timeout_seconds")]:
            self.assertRegex(main, env + r"\s*=\s*tostring\(var\." + variable + r"\)")

    def setUp(self):
        self.values = {
            "function_image": "iad.ocir.io/testnamespace/controller:release-1",
            "function_image_digest": "sha256:" + "a" * 64,
            "function_shape": "GENERIC_X86",
        }
        schema = b"""schemaVersion: 1.1.0
variables:
  build_function_image:
    type: boolean
    default: true
  function_image:
    type: string
    title: Existing image
  function_image_digest:
    type: string
    required: false
  function_shape:
    type: enum
    default: GENERIC_ARM
outputs:
  function_image:
    type: string
"""
        self.files = {
            SCHEMA_PATH: schema,
            "function/func.py": b"# unchanged source fixture\n",
        }
        manifest = {
            "release": "test-1.0",
            "files": [
                {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in self.files.items()
            ],
        }
        self.files["RELEASE_MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    def test_generic_stack_builds_automatically_and_hides_manual_image_fields(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        self.assertIs(default_value(schema, "build_function_image"), True)
        for name in ("function_image", "function_image_digest", "function_shape"):
            with self.subTest(variable=name):
                lines = variable_lines(schema, name)
                self.assertIn("    visible:", lines)
                self.assertIn('      not: ["${build_function_image}"]', lines)
        self.assertIn("    required: false", variable_lines(schema, "function_image_digest"))
        for name in ("ocir_username", "ocir_auth_token"):
            self.assertIn("    visible: ${build_function_image}", variable_lines(schema, name))
        self.assertIn("    type: password", variable_lines(schema, "ocir_auth_token"))

    def test_ocir_repository_omits_unsupported_immutability_but_stays_private_and_protected(self):
        source = (ROOT / "deploy/reference/image.tf").read_text(encoding="utf-8")
        repository = re.search(r'^resource "oci_artifacts_container_repository" "function" \{.*?^\}',
                               source, re.MULTILINE | re.DOTALL).group(0)
        # Omit the argument entirely, rather than sending a different explicit value.
        self.assertNotRegex(repository, r"(?m)^\s*is_immutable\s*=")
        self.assertRegex(repository, r"(?m)^\s*is_public\s*=\s*false\s*$")
        self.assertRegex(repository, r"(?m)^\s*prevent_destroy\s*=\s*true\s*$")
        self.assertRegex(repository, r"count\s*=\s*var\.build_function_image \? 1 : 0")

    def test_source_build_keeps_unique_attempt_tags_and_function_dependency(self):
        source = (ROOT / "deploy/reference/image.tf").read_text(encoding="utf-8")
        self.assertIn('data.oci_identity_regions.available.regions', source)
        self.assertIn('if region.name == var.region', source)
        self.assertIn('"${local.registry_region_key}.ocir.io"', source)
        self.assertIn('"build-${self.id}"', source)
        self.assertIn('local.built_registry_image.digest', source)
        self.assertIn('version        = local.built_image_tag', source)
        self.assertIn('oci_devops_build_run.function[0].state == "SUCCEEDED"', source)
        self.assertIn('variable.name == "CONTROLLER_IMAGE_TAG" && variable.value == local.built_image_tag', source)
        self.assertIn('if artifact.artifact_type == "OCIR"', source)
        self.assertNotIn('artifact.deploy_artifact_id ==', source)
        self.assertIn('image.repository_id == oci_artifacts_container_repository.function[0].id', source)
        self.assertIn('image.compartment_id == var.registry_compartment_ocid', source)
        self.assertIn('image.state == "AVAILABLE"', source)
        self.assertIn('image.versions', source)
        self.assertNotIn('local.delivered_image', source)
        self.assertRegex(source, r"source_hash\s*=\s*local\.function_source_hash")
        self.assertRegex(source, r"repository_id\s*=\s*oci_artifacts_container_repository\.function\[0\]\.id")
        main = (ROOT / "deploy/reference/main.tf").read_text(encoding="utf-8")
        self.assertRegex(main, r"image\s*=\s*local\.effective_function_image")
        self.assertRegex(main, r'image_digest\s*=\s*local\.effective_image_digest')
        self.assertIn('local.delivery_confirmed &&', main)
        self.assertIn('oci_devops_build_run.function', main)

    def test_native_devops_build_and_home_region_scoped_iam(self):
        build = (ROOT / "deploy/reference/build.tf").read_text()
        self.assertIn('"OL8_X86_64_STANDARD_10"', build)
        self.assertIn('"DELIVER_ARTIFACT"', build)
        self.assertIn('resource "terraform_data" "build_iam_ready"', build)
        self.assertIn('time.sleep(180)', build)
        self.assertIn('terraform_data.build_iam_ready', build.split('resource "oci_devops_build_run" "function"', 1)[1])
        self.assertNotIn('build_pipeline_stage_type = "WAIT"', build)
        self.assertNotIn('self.state', build)
        self.assertIn('provider\'s create waiter requires SUCCEEDED', build)
        self.assertIn('description                        = "Verify packaged source', build)
        self.assertIn('description               = "Deliver the verified image', build)
        self.assertIn('target.repository.id', build)
        self.assertNotIn('target.artifact.id', build)
        self.assertIn('to read devops-deploy-artifact in compartment id ${var.controller_compartment_ocid}"', build)
        self.assertIn('to inspect repos in compartment id ${var.registry_compartment_ocid}"', build)
        self.assertIn('target.repo.name', build)
        self.assertNotIn('request.permission', build)
        self.assertIn("to manage repos in compartment id ${var.registry_compartment_ocid} where target.repo.name = '${oci_artifacts_container_repository.function[0].display_name}'", build)
        self.assertEqual(build.count('to manage repos'), 1)
        self.assertNotIn('to manage devops-family', build)
        for forbidden in ('manage all-resources', 'manage instances', 'secret-family', 'to manage repos in tenancy', 'ocir_auth_token'):
            self.assertNotIn(forbidden, build)
        self.assertIn("resource.id = '${oci_devops_build_pipeline.function[0].id}'", build)
        versions = (ROOT / "deploy/reference/versions.tf").read_text()
        self.assertIn('home_region_key', versions)
        self.assertIn('region = local.home_region', versions)

    def test_new_stack_defaults_to_standby_without_pool_inputs(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        self.assertEqual(group_lines(schema, "Pool enrollment"), [
            "    variables: [enroll_pools, pool_compartment_ocid, scope_id, auto_discover_pools, default_pool_max_size]",
        ])
        self.assertIs(default_value(schema, "enroll_pools"), False)
        self.assertIs(default_value(schema, "auto_discover_pools"), True)
        for name in ("pool_compartment_ocid", "auto_discover_pools"):
            self.assertIn("    visible: ${enroll_pools}", variable_lines(schema, name))
        self.assertIn("    required: true", variable_lines(schema, "pool_compartment_ocid"))
        self.assertEqual(default_value(schema, "default_pool_max_size"), 3)
        self.assertIn('      and: ["${enroll_pools}", "${auto_discover_pools}"]',
                      variable_lines(schema, "default_pool_max_size"))
        self.assertEqual(default_value(schema, "scope_id"), "")
        self.assertIn("    required: false", variable_lines(schema, "scope_id"))
        terraform = (ROOT / "deploy/reference/variables.tf").read_text(encoding="utf-8")
        for name, expected in (("enroll_pools", "false"), ("pool_compartment_ocid", '""')):
            declaration = re.search(r'^variable "' + name + r'" \{(.*?)^\}',
                                    terraform, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(declaration)
            self.assertRegex(declaration.group(1), r"(?m)^\s*default\s*=\s*" + expected + r"\s*$")

    def test_advanced_pool_groups_follow_mode_and_overrides_start_empty(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        manual = group_lines(schema, "Advanced manual enrollment")
        self.assertIn("    visible:", manual)
        self.assertIn("      and:", manual)
        self.assertIn("        - ${enroll_pools}", manual)
        self.assertIn('        - not: ["${auto_discover_pools}"]', manual)
        self.assertIn("    variables: [pools]", manual)
        self.assertIn("    required: true", variable_lines(schema, "pools"))
        overrides = group_lines(schema, "Advanced discovery overrides")
        self.assertIn('      and: ["${enroll_pools}", "${auto_discover_pools}"]', overrides)
        self.assertIn("    variables: [customize_pool_settings, pool_overrides]", overrides)
        self.assertEqual(default_value(schema, "pool_overrides"), {})
        self.assertIn("    required: false", variable_lines(schema, "pool_overrides"))

    def test_root_wires_explicit_empty_mode_without_discovery_or_compute_grants(self):
        enrollment = (ROOT / "deploy/reference/enrollment.tf").read_text(encoding="utf-8")
        self.assertRegex(enrollment, r"enrollment_requested\s*=\s*var\.enroll_pools \|\| length\(var\.pools\) > 0")
        self.assertRegex(enrollment, r"count\s*=\s*local\.enrollment_requested && var\.auto_discover_pools && length\(var\.pools\) == 0 \? 1 : 0")
        self.assertRegex(enrollment, r"enroll_pools\s*=\s*var\.enroll_pools")
        self.assertRegex(enrollment, r'target_pool_compartment_id\s*=\s*var\.pool_compartment_ocid != "" \? var\.pool_compartment_ocid : var\.controller_compartment_ocid')
        main = (ROOT / "deploy/reference/main.tf").read_text(encoding="utf-8")
        self.assertRegex(main, r"ALLOW_EMPTY_POOL_REGISTRY\s*=\s*tostring\(!local\.enrollment_requested\)")
        self.assertRegex(main, r"COMPARTMENT_OCID\s*=\s*local\.target_pool_compartment_id")
        policy = main.split("controller_policy_statements = concat([", 1)[1].split("\n  )", 1)[0]
        ledger_only, compute = policy.split("local.enrollment_requested ? [", 1)
        self.assertIn("objects", ledger_only)
        self.assertNotIn("instance-pools", ledger_only)
        self.assertIn("instance-pools", compute)
        self.assertIn("local.enrollment_requested && var.enable_termination ? [", compute)

    def test_pool_customization_toggle_hides_editor_without_disabling_saved_overrides(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        self.assertIs(default_value(schema, "customize_pool_settings"), False)
        toggle = variable_lines(schema, "customize_pool_settings")
        self.assertIn("    type: boolean", toggle)
        self.assertIn("    title: Customize per-pool settings", toggle)
        self.assertIn("    visible: ${customize_pool_settings}", variable_lines(schema, "pool_overrides"))
        terraform = (ROOT / "deploy/reference/variables.tf").read_text(encoding="utf-8")
        declaration = re.search(r'^variable "customize_pool_settings" \{(.*?)^\}',
                                terraform, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(declaration)
        self.assertRegex(declaration.group(1), r"\bdefault\s*=\s*false\b")
        enrollment = (ROOT / "deploy/reference/enrollment.tf").read_text(encoding="utf-8")
        self.assertRegex(enrollment, r"(?m)^\s*pool_overrides\s*=\s*var\.pool_overrides\s*$")
        self.assertNotIn("customize_pool_settings", enrollment)
        for source in (ROOT / "deploy/reference/modules/enrollment").glob("*.tf"):
            self.assertNotIn("customize_pool_settings", source.read_text(encoding="utf-8"))

    def test_caller_group_editor_is_optional_and_hidden_by_default(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        self.assertIs(default_value(schema, "configure_caller_groups"), False)
        self.assertIn("    title: Configure caller groups", variable_lines(schema, "configure_caller_groups"))
        self.assertEqual(default_value(schema, "invoker_group_ocids"), [])
        self.assertIn("    required: false", variable_lines(schema, "invoker_group_ocids"))
        self.assertIn("    visible: ${configure_caller_groups}", variable_lines(schema, "invoker_group_ocids"))
        main = (ROOT / "deploy/reference/main.tf").read_text(encoding="utf-8")
        # The display toggle must not disable legacy grants or allow malformed IDs.
        self.assertNotIn("configure_caller_groups", main)
        self.assertEqual(main.count("var.invoker_group_ocids"), 1)
        self.assertIn("for group in sort(tolist(local.effective_invoker_group_ocids))", main)
        self.assertIn("var.create_iam_resources && length(local.effective_invoker_group_ocids) > 0 ? 1 : 0", main)

    def test_form_variables_match_terraform_and_override_attributes_remain_optional(self):
        schema = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
        terraform = (ROOT / "deploy/reference/variables.tf").read_text(encoding="utf-8")
        declared = set(re.findall(r'^variable "([A-Za-z0-9_]+)"', terraform, re.MULTILINE))
        groups = schema.split("\nvariableGroups:\n", 1)[1].split("\nvariables:", 1)[0]
        for line in groups.splitlines():
            if line.startswith("    variables: ["):
                for name in line.split("[", 1)[1].rstrip("]").split(", "):
                    self.assertIn(name, declared)
                    self.assertTrue(variable_lines(schema, name))
        self.assertIn("    valueType: pool_override_entry", variable_lines(schema, "pool_overrides"))
        self.assertIn("    attributes: [pool_override_worker_type, pool_override_max_size]",
                      variable_lines(schema, "pool_override_entry"))
        for schema_name, actual_name, field_type in (
            ("pool_override_worker_type", "worker_type", "string"),
            ("pool_override_max_size", "max_size", "number"),
        ):
            lines = variable_lines(schema, schema_name)
            self.assertIn("    actualName: " + actual_name, lines)
            self.assertIn("    type: " + field_type, lines)
            self.assertIn("    required: false", lines)
            self.assertIn("    visible: false", lines)
            self.assertRegex(terraform, actual_name + r"\s*=\s*optional\(" + field_type + r"\)")

    def test_prefilled_package_selects_existing_image_and_preserves_immutable_pin(self):
        result = package.prefilled_resource_manager_files(self.files, self.values)
        schema = result[SCHEMA_PATH].decode("utf-8")
        self.assertIs(default_value(schema, "build_function_image"), False)
        for name, value in self.values.items():
            self.assertEqual(default_value(schema, name), value)
        self.assertIn("    title: Existing image", variable_lines(schema, "function_image"))
        self.assertIn("    required: false", variable_lines(schema, "function_image_digest"))
        self.assertTrue(schema.endswith("outputs:\n  function_image:\n    type: string\n"))

    def test_prefilled_package_updates_embedded_integrity_and_leaves_source_untouched(self):
        original = dict(self.files)
        result = package.prefilled_resource_manager_files(self.files, self.values)
        self.assertEqual(self.files, original)
        self.assertNotEqual(result[SCHEMA_PATH], original[SCHEMA_PATH])
        self.assertEqual(result["function/func.py"], original["function/func.py"])
        manifest = json.loads(result["RELEASE_MANIFEST.json"])
        for entry in manifest["files"]:
            data = result[entry["path"]]
            self.assertEqual(entry["bytes"], len(data))
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        provenance = json.loads(result["IMAGE_PROVENANCE.json"])
        for name, value in self.values.items():
            self.assertEqual(provenance[name], value)
        self.assertEqual(provenance["source_release"], "test-1.0")

    def test_repeated_prefill_is_stable_and_does_not_duplicate_defaults(self):
        once = package.prefilled_resource_manager_files(self.files, self.values)
        twice = package.prefilled_resource_manager_files(once, self.values)
        self.assertEqual(twice, once)
        schema = twice[SCHEMA_PATH].decode("utf-8")
        self.assertIs(default_value(schema, "build_function_image"), False)
        for name, value in self.values.items():
            self.assertEqual(default_value(schema, name), value)

    def test_prefill_requires_complete_valid_image_pins(self):
        self.assertEqual(package.image_prefill(argparse.Namespace(**self.values)), self.values)
        for replacement in (
            {"function_image_digest": None},
            {"function_image_digest": "sha256:abc"},
            {"function_image": "iad.ocir.io/testnamespace/controller"},
            {"function_shape": "GENERIC_UNKNOWN"},
        ):
            with self.subTest(replacement=replacement):
                values = dict(self.values, **replacement)
                with self.assertRaises(SystemExit):
                    package.image_prefill(argparse.Namespace(**values))

    def test_no_image_prefill_keeps_generic_build_mode(self):
        values = argparse.Namespace(function_image=None, function_image_digest=None, function_shape=None)
        self.assertIsNone(package.image_prefill(values))


if __name__ == "__main__":
    unittest.main()
