"""Exercise explicit deploy-first Function behavior without any OCI services."""

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "function/func.py"
SPEC = importlib.util.spec_from_file_location("empty_enrollment_function", SOURCE)
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


class EmptyEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "COMPARTMENT_OCID": "ocid1.compartment.oc1..testcontroller",
            "HARNESS_ID": "deployed-controller",
            "FUNCTION_ROLE": "control",
            "CONTROLLER_ONLY": "true",
            "AUTH_MODE": "oci_iam",
            "ALLOW_EMPTY_POOL_REGISTRY": "true",
            "SCALE_TEST_PROFILES_JSON": "{}",
            "SCALE_TEST_BUDGET_LIMITS_ENABLED": "true",
            "CONTROLLER_MAX_PROFILES": "6",
            "CONTROLLER_MAX_POOL_SIZE": "50",
            "MAX_ACTIVE_SCALE_TEST_PROFILES": "1",
            "MAX_SCALE_TEST_TOTAL_OCPUS": "16",
            "OBJECT_STORAGE_NAMESPACE": "testnamespace",
            "REQUEST_LEDGER_BUCKET": "controller-ledger",
        }
        self.profile = {
            "small": {
                "poolId": "ocid1.instancepool.oc1.iad.testpool",
                "poolName": "test-pool",
                "workerType": "small",
                "ociShape": "VM.Standard3.Flex",
                "ocpus": 1,
                "memoryGbs": 8,
                "maxSize": 3,
            },
        }
        warning = mock.patch.object(runtime.LOG, "warning")
        warning.start()
        self.addCleanup(warning.stop)

    def assert_invalid_configuration(self, environment):
        with self.assertRaises(runtime.HarnessError) as raised:
            runtime._config(environment)
        self.assertEqual(raised.exception.status, 500)
        self.assertEqual(raised.exception.reason, "invalid_configuration")

    def test_explicit_empty_json_object_is_accepted_only_with_opt_in(self):
        for raw in ("{}", " { } \n"):
            with self.subTest(raw=raw):
                config = runtime._config(dict(self.environment, SCALE_TEST_PROFILES_JSON=raw))
                self.assertTrue(config.allow_empty_pool_registry)
                self.assertEqual(config.scale_test_profiles, {})
        for flag in (None, "false"):
            environment = dict(self.environment)
            if flag is None:
                environment.pop("ALLOW_EMPTY_POOL_REGISTRY")
            else:
                environment["ALLOW_EMPTY_POOL_REGISTRY"] = flag
            self.assert_invalid_configuration(environment)

    def test_missing_blank_malformed_and_nonobject_registries_fail_closed(self):
        for raw in (None, "", " ", "{broken", "[]", "null", '"{}"', "0", {}):
            with self.subTest(raw=raw):
                environment = dict(self.environment)
                if raw is None:
                    environment.pop("SCALE_TEST_PROFILES_JSON")
                else:
                    environment["SCALE_TEST_PROFILES_JSON"] = raw
                self.assert_invalid_configuration(environment)

    def test_empty_registry_flag_uses_strict_boolean_parser(self):
        for flag in ("yes", "1", "", " true ", "null"):
            with self.subTest(flag=flag):
                self.assert_invalid_configuration(dict(self.environment, ALLOW_EMPTY_POOL_REGISTRY=flag))

    def test_opt_in_requires_controller_only_control_role_and_iam(self):
        for changes in (
            {"AUTH_MODE": "bearer", "HARNESS_TOKEN": "test-token"},
            {"CONTROLLER_ONLY": "false"},
            {"FUNCTION_ROLE": "readiness"},
            {"FUNCTION_ROLE": "terminator"},
            {"AUTH_MODE": "unknown"},
            {"CONTROLLER_ONLY": "false", "AUTH_MODE": "bearer", "HARNESS_TOKEN": "test-token",
             "TARGET_INSTANCE_POOL_OCID": "ocid1.instancepool.oc1.iad.testpool"},
        ):
            with self.subTest(changes=changes):
                self.assert_invalid_configuration(dict(self.environment, **changes))

    def test_capacity_ledger_and_scope_configuration_are_still_required(self):
        for changes in (
            {"SCALE_TEST_BUDGET_LIMITS_ENABLED": "false"},
            {"CONTROLLER_MAX_PROFILES": "0"},
            {"CONTROLLER_MAX_POOL_SIZE": "0"},
            {"MAX_ACTIVE_SCALE_TEST_PROFILES": "0"},
            {"MAX_SCALE_TEST_TOTAL_OCPUS": "0"},
            {"MAX_POOL_SIZE": "4"},
            {"OBJECT_STORAGE_NAMESPACE": "", "REQUEST_LEDGER_BUCKET": ""},
            {"REQUEST_LEDGER_BUCKET": ""},
            {"REQUEST_LEDGER_BUCKET": "bad bucket"},
            {"HARNESS_ID": ""},
            {"HARNESS_ID": "bad group"},
        ):
            with self.subTest(changes=changes):
                self.assert_invalid_configuration(dict(self.environment, **changes))

    def test_status_and_alias_report_zero_enrollment_without_creating_clients(self):
        with mock.patch.object(runtime, "_make_clients", side_effect=AssertionError("OCI clients must not be created")) as make_clients:
            for action in ("scale_test_status", "pool_status"):
                with self.subTest(action=action):
                    status, body = runtime.handle_request({"action": action}, environ=self.environment)
                    self.assertEqual(status, 200)
                    self.assertEqual(body["result"], "awaiting_pool_enrollment")
                    self.assertEqual(body["action"], "scale_test_status")
                    self.assertEqual(body["enrolledPoolCount"], 0)
                    self.assertEqual(body["profiles"], [])
                    self.assertEqual(body["reconcilePools"], [])
                    self.assertIn("no enrolled pools", body["message"])
                    self.assertFalse(body["workerSelfReclaim"]["enabled"])
                    self.assertTrue(body["safety"]["budgetLimitsEnabled"])
                    for field in ("activeProfiles", "totalDesiredOcpus", "totalEffectiveOcpus"):
                        self.assertEqual(body["safety"][field], 0)
                    if action == "pool_status":
                        self.assertEqual(body["requestedAction"], "pool_status")
            make_clients.assert_not_called()

    def test_other_allowed_controller_actions_conflict_before_clients_or_ledger(self):
        with mock.patch.object(runtime, "_make_clients") as make_clients:
            with mock.patch.object(runtime, "_require_ledger") as require_ledger:
                for action in ("reconcile_pool", "request_status", "set_pool_protection"):
                    with self.subTest(action=action):
                        status, body = runtime.handle_request({"action": action}, environ=self.environment)
                        self.assertEqual(status, 409)
                        self.assertEqual(body["reason"], "controller_not_enrolled")
                        self.assertIs(body["retryable"], False)
                make_clients.assert_not_called()
                require_ledger.assert_not_called()

    def test_empty_requests_do_not_touch_injected_compute_or_storage_clients(self):
        sdk_clients = {name: mock.Mock(name=name) for name in ("compute", "pools", "objects", "autoscaling")}
        clients = runtime.Clients(**sdk_clients)
        for action in ("pool_status", "reconcile_pool", "request_status", "set_pool_protection"):
            runtime.handle_request({"action": action}, environ=self.environment, clients=clients)
        for client in sdk_clients.values():
            self.assertEqual(client.mock_calls, [])

    def test_role_rejection_precedes_empty_registry_response(self):
        with mock.patch.object(runtime, "_authenticate") as authenticate:
            with mock.patch.object(runtime, "_make_clients") as make_clients:
                for action in ("status", "scale", "report_scale_test_ready", "terminate_if_ready"):
                    status, body = runtime.handle_request({"action": action}, environ=self.environment)
                    self.assertEqual(status, 403)
                    self.assertEqual(body["reason"], "action_not_allowed_for_role")
                authenticate.assert_not_called()
                make_clients.assert_not_called()

    def test_authentication_precedes_empty_status_and_mutation_responses(self):
        rejected = runtime.HarnessError(401, "unauthorized", "test authentication rejection")
        with mock.patch.object(runtime, "_authenticate", side_effect=rejected) as authenticate:
            with mock.patch.object(runtime, "_awaiting_pool_enrollment_status") as empty_status:
                with mock.patch.object(runtime, "_make_clients") as make_clients:
                    for action in ("pool_status", "reconcile_pool"):
                        status, body = runtime.handle_request({"action": action}, environ=self.environment)
                        self.assertEqual(status, 401)
                        self.assertEqual(body["reason"], "unauthorized")
                    self.assertEqual(authenticate.call_count, 2)
                    empty_status.assert_not_called()
                    make_clients.assert_not_called()

    def test_nonempty_registry_keeps_existing_dispatch_with_flag_true_or_false(self):
        for flag in (None, "false", "true"):
            environment = dict(self.environment, SCALE_TEST_PROFILES_JSON=json.dumps(self.profile))
            if flag is None:
                environment.pop("ALLOW_EMPTY_POOL_REGISTRY")
            else:
                environment["ALLOW_EMPTY_POOL_REGISTRY"] = flag
            config = runtime._config(environment)
            self.assertEqual(set(config.scale_test_profiles), {"small"})
            for action, handler_name in (
                ("scale_test_status", "_scale_test_status"),
                ("reconcile_pool", "_reconcile_pool"),
                ("request_status", "_request_status"),
                ("set_pool_protection", "_set_pool_protection"),
            ):
                with self.subTest(flag=flag, action=action):
                    with mock.patch.object(runtime, "_make_clients", return_value=mock.sentinel.clients) as make_clients:
                        with mock.patch.object(runtime, handler_name, return_value=(200, {"result": "existing-handler"})) as existing:
                            self.assertEqual(runtime.handle_request({"action": action}, environ=environment),
                                             (200, {"result": "existing-handler"}))
                            make_clients.assert_called_once_with()
                            existing.assert_called_once()

    def test_iam_handler_preserves_business_status_envelope_and_http_method_guard(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            with mock.patch.object(runtime, "fdk_response", None):
                with mock.patch.object(runtime, "_make_clients") as make_clients:
                    for action, expected in (("pool_status", 200), ("reconcile_pool", 409)):
                        reply = runtime.handler(None, io.BytesIO(json.dumps({"action": action}).encode("utf-8")))
                        self.assertEqual(reply.status_code, 200)
                        envelope = json.loads(reply.response_data)
                        self.assertEqual(envelope["status_code"], expected)
                        if expected == 409:
                            self.assertEqual(envelope["body"]["reason"], "controller_not_enrolled")
                            self.assertIs(envelope["body"]["retryable"], False)
                        else:
                            self.assertEqual(envelope["body"]["result"], "awaiting_pool_enrollment")
                    context = mock.Mock()
                    context.Method.return_value = "GET"
                    reply = runtime.handler(context)
                    self.assertEqual(json.loads(reply.response_data)["status_code"], 405)
                    make_clients.assert_not_called()


if __name__ == "__main__":
    unittest.main()
