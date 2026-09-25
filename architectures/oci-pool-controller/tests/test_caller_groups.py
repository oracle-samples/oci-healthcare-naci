"""Offline Terraform plans of the actual caller-group validation/policy expressions.

Only the Function ID is substituted with a fixture. No OCI provider, credentials,
remote backend or cloud resources are used.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = shutil.which("terraform")
GROUP_A = "ocid1.group.oc1..approved-a"
GROUP_B = "ocid1.group.oc1..approved-b"


@unittest.skipUnless(TERRAFORM, "Terraform CLI is required for offline caller-group tests")
class CallerGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="caller-groups-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.environment = {key: value for key, value in os.environ.items() if not key.startswith("TF_")}
        cls.environment["TF_IN_AUTOMATION"] = "1"
        variables = (ROOT / "deploy/reference/variables.tf").read_text(encoding="utf-8")
        main = (ROOT / "deploy/reference/main.tf").read_text(encoding="utf-8")
        declarations = [re.search(r'^variable "' + name + r'" \{.*?^\}', variables,
                                  re.MULTILINE | re.DOTALL).group(0)
                        for name in ("invoker_group_ocids", "configure_caller_groups", "create_iam_resources")]
        normalized = re.search(r'^  effective_invoker_group_ocids\s*=.*$', main, re.MULTILINE).group(0)
        statements = re.search(r'^  invoker_policy_statements = flatten\(\[.*?^  \]\)', main,
                               re.MULTILINE | re.DOTALL).group(0)
        statements = statements.replace("oci_functions_function.controller.id", "local.function_id")
        policy_resource = main.split('resource "oci_identity_policy" "invokers" {', 1)[1].split("\n}", 1)[0]
        count = re.search(r'^  count\s*=\s*(.*)$', policy_resource, re.MULTILINE).group(1)
        fixture = '\n\n'.join(declarations) + '''
variable "controller_compartment_ocid" { default = "ocid1.compartment.oc1..controller" }
locals {
  function_id = "ocid1.fnfunc.oc1.iad.controller"
''' + normalized + "\n" + statements + '\n}\n' + '''
output "groups" { value = sort(tolist(local.effective_invoker_group_ocids)) }
output "statements" { value = local.invoker_policy_statements }
output "policy_count" { value = ''' + count + ' }\n'
        (cls.root / "main.tf").write_text(fixture, encoding="utf-8")
        result = cls.terraform("init", "-backend=false", "-input=false", "-no-color")
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def terraform(cls, *arguments):
        return subprocess.run([TERRAFORM] + list(arguments), cwd=cls.root, env=cls.environment,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=30, check=False)

    def plan(self, succeed=True, **inputs):
        (self.root / "input.tfvars.json").write_text(json.dumps(inputs), encoding="utf-8")
        result = self.terraform("plan", "-input=false", "-no-color", "-refresh=false",
                                "-var-file=input.tfvars.json", "-out=plan.bin")
        diagnostics = result.stdout + result.stderr
        if not succeed:
            self.assertNotEqual(result.returncode, 0, diagnostics)
            self.assertIn("Every nonblank invoker_group_ocids entry", diagnostics)
            return
        self.assertEqual(result.returncode, 0, diagnostics)
        shown = self.terraform("show", "-json", "plan.bin")
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        outputs = json.loads(shown.stdout)["planned_values"]["outputs"]
        return {name: item["value"] for name, item in outputs.items()}

    def test_omitted_groups_default_to_no_caller_policy(self):
        self.assertEqual(self.plan(create_iam_resources=True),
                         {"groups": [], "statements": [], "policy_count": 0})

    def test_empty_blank_whitespace_and_null_rows_generate_no_policy(self):
        for entries in ([], [""], [" ", "\t\n", ""], [None], [None, "", "  "], None):
            for visible in (False, True):
                with self.subTest(entries=entries, visible=visible):
                    self.assertEqual(self.plan(invoker_group_ocids=entries, create_iam_resources=True,
                                               configure_caller_groups=visible),
                                     {"groups": [], "statements": [], "policy_count": 0})

    def test_mixed_rows_trim_deduplicate_and_preserve_valid_groups(self):
        result = self.plan(invoker_group_ocids=["", GROUP_B, " " + GROUP_A + "\n", GROUP_A, None],
                           create_iam_resources=True)
        self.assertEqual(result["groups"], [GROUP_A, GROUP_B])
        self.assertEqual(result["policy_count"], 1)
        self.assertEqual(len(result["statements"]), 4)
        for group in (GROUP_A, GROUP_B):
            self.assertEqual(sum("Allow group id " + group + " to " in s for s in result["statements"]), 2)
        for statement in result["statements"]:
            self.assertIn("where target.function.id = 'ocid1.fnfunc.oc1.iad.controller'", statement)

    def test_hiding_editor_does_not_change_saved_group_grants(self):
        inputs = {"invoker_group_ocids": [GROUP_A], "create_iam_resources": True}
        hidden = self.plan(configure_caller_groups=False, **inputs)
        visible = self.plan(configure_caller_groups=True, **inputs)
        self.assertEqual(hidden, visible)
        self.assertEqual(hidden["policy_count"], 1)
        self.assertEqual(len(hidden["statements"]), 2)

    def test_centrally_managed_iam_keeps_review_statements_without_creating_policy(self):
        result = self.plan(invoker_group_ocids=[GROUP_A], create_iam_resources=False)
        self.assertEqual(result["policy_count"], 0)
        self.assertEqual(len(result["statements"]), 2)

    def test_invalid_nonblank_values_fail_even_when_editor_and_iam_are_disabled(self):
        for value in ("Administrators", "ocid1.user.oc1..user", "ocid1.compartment.oc1..compartment",
                      "ocid1.dynamicgroup.oc1..dynamic", "ocid1XgroupYinvalid", "ocid1.group.",
                      GROUP_A + " to manage all-resources in tenancy", "not-an-ocid"):
            with self.subTest(value=value):
                self.plan(succeed=False, invoker_group_ocids=["", GROUP_A, value],
                          configure_caller_groups=False, create_iam_resources=False)


if __name__ == "__main__":
    unittest.main()
