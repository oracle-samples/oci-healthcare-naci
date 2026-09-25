"""Exercise pool enrollment through real, offline Terraform plans.

The enrollment module is pure Terraform: the fixture supplies OCI-shaped pool
metadata, so these tests need no providers, OCI credentials, or network access.
"""

import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TERRAFORM = shutil.which("terraform")
MODULE = Path(__file__).resolve().parents[1] / "deploy/reference/modules/enrollment"
COMPARTMENT = "ocid1.compartment.oc1..test-pools"
DEFAULTS = {
    # Existing discovery cases explicitly opt in. Separate tests below omit
    # this module argument entirely to verify the deploy-first default.
    "enroll_pools": True,
    "auto_discover_pools": True,
    "scope_id": "",
    "default_pool_max_size": 3,
    "pools": {},
    "pool_overrides": {},
    "candidates": [],
}


def candidate(name, scope="scope-a", profile="small", state="RUNNING", **extra):
    tags = {}
    if scope is not None:
        tags["HarnessId"] = scope
    if profile is not None:
        tags["ScaleTestProfile"] = profile
    value = {
        "id": "ocid1.instancepool.oc1.iad." + name,
        "display_name": name,
        "compartment_id": COMPARTMENT,
        "state": state,
        "freeform_tags": tags,
    }
    value.update(extra)
    return value


@unittest.skipUnless(TERRAFORM, "Terraform CLI is required for offline enrollment tests")
class PoolEnrollmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="pool-enrollment-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("TF_")
        }
        cls.environment["TF_IN_AUTOMATION"] = "1"
        harness = {
            "terraform": {"required_version": ">= 1.5.0"},
            "variable": {name: {"default": value} for name, value in DEFAULTS.items()},
            "module": {
                "enrollment": dict(
                    {"source": str(MODULE)},
                    **{name: "${var." + name + "}" for name in DEFAULTS},
                ),
            },
            "output": {
                name: {"value": "${module.enrollment." + name + "}"}
                for name in ("scope_id", "pools", "review")
            },
        }
        (cls.root / "main.tf.json").write_text(json.dumps(harness), encoding="utf-8")
        initialized = cls.terraform("init", "-backend=false", "-input=false", "-no-color")
        if initialized.returncode:
            raise AssertionError("Offline Terraform init failed:\n" + initialized.stdout + initialized.stderr)

    @classmethod
    def terraform(cls, *arguments, root=None):
        return subprocess.run(
            [TERRAFORM] + list(arguments), cwd=root or cls.root, env=cls.environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            timeout=30, check=False,
        )

    def plan(self, succeed=True, **values):
        inputs = dict(DEFAULTS, **values)
        (self.root / "input.tfvars.json").write_text(json.dumps(inputs), encoding="utf-8")
        result = self.terraform(
            "plan", "-input=false", "-no-color", "-refresh=false",
            "-var-file=input.tfvars.json", "-out=plan.bin",
        )
        diagnostics = result.stdout + result.stderr
        if not succeed:
            self.assertNotEqual(result.returncode, 0, diagnostics)
            self.assertIn("Error:", diagnostics)
            return diagnostics
        self.assertEqual(result.returncode, 0, diagnostics)
        shown = self.terraform("show", "-json", "plan.bin")
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        outputs = json.loads(shown.stdout)["planned_values"]["outputs"]
        return {name: output["value"] for name, output in outputs.items()}

    @contextlib.contextmanager
    def applied_state(self, candidates, module_defaults=False, **values):
        # Apply only built-in terraform_data guards to disposable local state.
        with tempfile.TemporaryDirectory(prefix="pool-identity-guard-test-") as temporary:
            root = Path(temporary)
            harness = json.loads((self.root / "main.tf.json").read_text(encoding="utf-8"))
            if module_defaults:
                del harness["module"]["enrollment"]["enroll_pools"]
            (root / "main.tf.json").write_text(json.dumps(harness), encoding="utf-8")
            inputs = dict(DEFAULTS, candidates=candidates, **values)
            (root / "input.tfvars.json").write_text(json.dumps(inputs), encoding="utf-8")
            initialized = self.terraform("init", "-backend=false", "-input=false", "-no-color", root=root)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            applied = self.terraform(
                "apply", "-auto-approve", "-input=false", "-no-color",
                "-var-file=input.tfvars.json", root=root,
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            yield root

    def state_plan(self, root, candidates, **values):
        inputs = dict(DEFAULTS, candidates=candidates, **values)
        (root / "input.tfvars.json").write_text(json.dumps(inputs), encoding="utf-8")
        return self.terraform(
            "plan", "-input=false", "-no-color", "-detailed-exitcode",
            "-var-file=input.tfvars.json", "-out=updated-plan.bin", root=root,
        )

    def state_values(self, root):
        shown = self.terraform("show", "-json", root=root)
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        values = json.loads(shown.stdout)["values"]
        outputs = {name: output["value"] for name, output in values["outputs"].items()}
        resources = {
            resource["address"]: resource["values"]["id"]
            for child in values["root_module"].get("child_modules", [])
            for resource in child.get("resources", [])
        }
        return outputs, resources

    def test_single_scope_derives_keys_worker_labels_and_fixed_default_ceiling(self):
        # Current capacity is not an approved controller ceiling. Extra OCI
        # metadata must not turn size=80 into authority to launch 80 workers.
        pool = candidate("pool-a", size=80)
        result = self.plan(candidates=[pool])
        self.assertEqual(result["scope_id"], "scope-a")
        self.assertEqual(result["review"]["mode"], "automatic")
        self.assertEqual(result["pools"], {
            "small": {"pool_id": pool["id"], "worker_type": "small", "max_size": 3},
        })
        self.assertEqual(result["review"]["scope_id"], "scope-a")
        self.assertEqual(result["review"]["included_pool_ids"], [pool["id"]])
        self.assertEqual(result["review"]["excluded_pool_ids"], [])

    def test_explicit_scope_filters_other_scopes(self):
        included = candidate("chosen", scope="scope-b", profile="batch")
        excluded = candidate("other", scope="scope-a", profile="batch")
        result = self.plan(scope_id="scope-b", candidates=[excluded, included])
        self.assertEqual(result["scope_id"], "scope-b")
        self.assertEqual(result["pools"]["batch"]["pool_id"], included["id"])
        self.assertEqual(result["review"]["included_pool_ids"], [included["id"]])
        self.assertEqual(result["review"]["excluded_pool_ids"], [excluded["id"]])

    def test_ambiguous_scope_requires_selection(self):
        self.plan(succeed=False, candidates=[
            candidate("pool-a", scope="scope-a"), candidate("pool-b", scope="scope-b"),
        ])

    def test_no_discoverable_scope_or_no_matching_scope_fails(self):
        for inputs in (
            {"candidates": []},
            {"candidates": [candidate("untagged", scope=None, profile=None)]},
            {"scope_id": "missing", "candidates": [candidate("other")]},
        ):
            with self.subTest(inputs=inputs):
                self.plan(succeed=False, **inputs)

    def test_selected_missing_or_invalid_profile_tag_fails(self):
        for profile in (None, "", "contains spaces", "x" * 65):
            with self.subTest(profile=profile):
                self.plan(succeed=False, candidates=[candidate("pool-a", profile=profile)])

    def test_duplicate_selected_profile_tags_fail(self):
        self.plan(succeed=False, candidates=[candidate("pool-a"), candidate("pool-b")])

    def test_stopped_pools_remain_enrolled_but_terminating_pools_are_excluded(self):
        stopped = candidate("stopped", profile="stopped", state="STOPPED")
        running = candidate("running", profile="running")
        terminating = candidate("terminating", scope="another-scope", state="TERMINATING")
        terminated = candidate("terminated", scope="another-scope", state="TERMINATED")
        result = self.plan(candidates=[terminated, stopped, terminating, running])
        self.assertEqual(set(result["pools"]), {"running", "stopped"})
        self.assertEqual(set(result["review"]["included_pool_ids"]), {stopped["id"], running["id"]})
        self.assertEqual(set(result["review"]["excluded_pool_ids"]), {terminating["id"], terminated["id"]})

    def test_unrelated_untagged_pool_is_reported_but_not_enrolled(self):
        selected = candidate("selected")
        unrelated = candidate("unrelated", scope=None, profile=None)
        result = self.plan(candidates=[unrelated, selected])
        self.assertEqual(set(result["pools"]), {"small"})
        self.assertEqual(result["review"]["excluded_pool_ids"], [unrelated["id"]])

    def test_overrides_allow_distinct_worker_labels_and_approved_ceilings(self):
        result = self.plan(
            candidates=[candidate("pool-a"), candidate("pool-b", profile="large")],
            default_pool_max_size=4,
            pool_overrides={"small": {"worker_type": "batch-workers", "max_size": 2}},
        )
        self.assertEqual(result["pools"]["small"]["worker_type"], "batch-workers")
        self.assertEqual(result["pools"]["small"]["max_size"], 2)
        self.assertEqual(result["pools"]["large"]["worker_type"], "large")
        self.assertEqual(result["pools"]["large"]["max_size"], 4)

    def test_unknown_override_profile_fails(self):
        self.plan(
            succeed=False, candidates=[candidate("pool-a")],
            pool_overrides={"misspelled": {"max_size": 2}},
        )

    def test_blank_or_invalid_override_worker_label_fails(self):
        for label in ("", "   ", "x" * 256):
            with self.subTest(label=label):
                self.plan(
                    succeed=False, candidates=[candidate("pool-a")],
                    pool_overrides={"small": {"worker_type": label}},
                )

    def test_nonpositive_or_fractional_override_ceiling_fails(self):
        for maximum in (0, -1, 1.5):
            with self.subTest(maximum=maximum):
                self.plan(
                    succeed=False, candidates=[candidate("pool-a")],
                    pool_overrides={"small": {"max_size": maximum}},
                )

    def test_nonpositive_or_fractional_default_ceiling_fails(self):
        for maximum in (0, -1, 1.5):
            with self.subTest(maximum=maximum):
                self.plan(succeed=False, candidates=[candidate("pool-a")], default_pool_max_size=maximum)

    def test_manual_map_takes_precedence_and_preserves_existing_metadata(self):
        pools = {
            "established": {
                "pool_id": candidate("existing")["id"],
                "worker_type": "existing-label", "max_size": 7,
            },
        }
        result = self.plan(
            enroll_pools=False, scope_id="retained-scope", pools=pools,
            candidates=[candidate("auto-a", scope="a"), candidate("auto-b", scope="b")],
        )
        self.assertEqual(result["scope_id"], "retained-scope")
        self.assertEqual(result["review"]["mode"], "manual")
        self.assertEqual(result["pools"], pools)
        self.assertEqual(result["review"]["included_pool_ids"], [pools["established"]["pool_id"]])

    def test_manual_mode_requires_pool_map_and_explicit_scope(self):
        self.plan(succeed=False, auto_discover_pools=False, scope_id="scope-a")
        self.plan(succeed=False, auto_discover_pools=False, pools={
            "small": {
                "pool_id": candidate("pool-a")["id"], "worker_type": "small", "max_size": 3,
            },
        })

    def test_inferred_scope_cannot_change_after_initial_local_apply(self):
        with self.applied_state([candidate("same-pool", scope="original-scope")]) as root:
            changed = self.state_plan(root, [candidate("same-pool", scope="different-scope")])
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("Error:", changed.stdout + changed.stderr)

    def test_existing_profile_cannot_rebind_to_a_different_pool_ocid(self):
        with self.applied_state([candidate("original", profile="stable-key")]) as root:
            changed = self.state_plan(root, [candidate("replacement", profile="stable-key")])
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("Resource postcondition failed", changed.stdout + changed.stderr)

    def test_disappearing_pool_cannot_delete_its_persisted_identity_guard(self):
        retained = candidate("retained", profile="retained")
        removed = candidate("removed", profile="removed")
        with self.applied_state([retained, removed]) as root:
            changed = self.state_plan(root, [retained])
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("prevent_destroy", changed.stdout + changed.stderr)

    def test_new_profile_addition_preserves_existing_pool_identity(self):
        original = candidate("original", profile="original")
        added = candidate("added", profile="added")
        with self.applied_state([original]) as root:
            changed = self.state_plan(root, [original, added])
            self.assertEqual(changed.returncode, 2, changed.stdout + changed.stderr)
            shown = self.terraform("show", "-json", "updated-plan.bin", root=root)
            self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
            plan = json.loads(shown.stdout)
            pools = plan["planned_values"]["outputs"]["pools"]["value"]
            self.assertEqual(set(pools), {"original", "added"})
            self.assertEqual(pools["original"]["pool_id"], original["id"])
            self.assertEqual(pools["added"]["pool_id"], added["id"])
            changes = [
                change for change in plan["resource_changes"]
                if change["change"]["actions"] != ["no-op"]
            ]
            self.assertEqual(len(changes), 1, changes)
            self.assertEqual(changes[0]["change"]["actions"], ["create"])
            self.assertEqual(changes[0].get("index"), "added")

    def test_unchanged_enrollment_has_no_changes_after_local_apply(self):
        pools = [candidate("pool-a"), candidate("pool-b", profile="large")]
        with self.applied_state(pools) as root:
            unchanged = self.state_plan(root, pools)
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout + unchanged.stderr)

    def test_default_mode_deploys_empty_registry_with_persistent_generated_scope(self):
        # Omit enroll_pools from the module call, not merely pass false, so this
        # tests the real public default independently of the discovery fixture.
        with self.applied_state([], module_defaults=True) as root:
            initial, resources = self.state_values(root)
            self.assertRegex(initial["scope_id"], r"^controller-[0-9a-f-]{36}$")
            self.assertEqual(initial["pools"], {})
            self.assertEqual(initial["review"]["mode"], "awaiting_enrollment")
            self.assertEqual(initial["review"]["included_pool_ids"], [])
            self.assertEqual(set(resources), {
                "module.enrollment.terraform_data.controller_identity",
                "module.enrollment.terraform_data.scope_guard",
            })
            unchanged = self.state_plan(root, [])
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout + unchanged.stderr)
            subsequent, subsequent_resources = self.state_values(root)
            self.assertEqual(subsequent["scope_id"], initial["scope_id"])
            self.assertEqual(subsequent_resources, resources)

    def test_standby_preserves_explicit_scope_and_ignores_unrelated_candidates(self):
        candidates = [
            candidate("pool-a", scope="other-a", profile=None),
            candidate("pool-b", scope="other-b", profile="bad profile"),
        ]
        result = self.plan(enroll_pools=False, scope_id="chosen-controller", candidates=candidates)
        self.assertEqual(result["scope_id"], "chosen-controller")
        self.assertEqual(result["pools"], {})
        self.assertEqual(result["review"]["mode"], "awaiting_enrollment")
        self.assertEqual(result["review"]["included_pool_ids"], [])

    def test_standby_rejects_discovery_overrides_instead_of_silently_ignoring_them(self):
        self.plan(
            succeed=False, enroll_pools=False, scope_id="new-controller",
            pool_overrides={"small": {"max_size": 2}},
        )

    def test_first_matching_enrollment_preserves_generated_identity_and_adds_only_pool_guard(self):
        with self.applied_state([], enroll_pools=False) as root:
            initial, original_resources = self.state_values(root)
            pool = candidate("first-pool", scope=initial["scope_id"])
            changed = self.state_plan(root, [pool], enroll_pools=True)
            self.assertEqual(changed.returncode, 2, changed.stdout + changed.stderr)
            shown = self.terraform("show", "-json", "updated-plan.bin", root=root)
            self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
            plan = json.loads(shown.stdout)
            changes = [
                change for change in plan["resource_changes"]
                if change["change"]["actions"] != ["no-op"]
            ]
            self.assertEqual(len(changes), 1, changes)
            self.assertEqual(changes[0]["address"], 'module.enrollment.terraform_data.pool_identity_guard["small"]')
            self.assertEqual(changes[0]["change"]["actions"], ["create"])
            enrolled = self.terraform("apply", "-input=false", "-no-color", "updated-plan.bin", root=root)
            self.assertEqual(enrolled.returncode, 0, enrolled.stdout + enrolled.stderr)
            final, final_resources = self.state_values(root)
            self.assertEqual(final["scope_id"], initial["scope_id"])
            self.assertEqual(final["review"]["mode"], "automatic")
            self.assertEqual(final["pools"]["small"]["pool_id"], pool["id"])
            for address, identity in original_resources.items():
                self.assertEqual(final_resources[address], identity)

    def test_first_enrollment_cannot_switch_generated_controller_to_another_group(self):
        with self.applied_state([], enroll_pools=False) as root:
            changed = self.state_plan(root, [candidate("wrong-group", scope="unrelated-scope")])
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("Resource postcondition failed", changed.stdout + changed.stderr)

    def test_existing_enrollment_cannot_silently_fall_back_to_standby(self):
        pool = candidate("pool-a")
        with self.applied_state([pool]) as root:
            changed = self.state_plan(root, [], enroll_pools=False, scope_id="scope-a")
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("prevent_destroy", changed.stdout + changed.stderr)

    def test_requested_enrollment_still_rejects_empty_registry_after_standby(self):
        with self.applied_state([], enroll_pools=False) as root:
            initial, _ = self.state_values(root)
            changed = self.state_plan(root, [], enroll_pools=True, scope_id=initial["scope_id"])
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("no pools matched", changed.stdout + changed.stderr)


if __name__ == "__main__":
    unittest.main()
