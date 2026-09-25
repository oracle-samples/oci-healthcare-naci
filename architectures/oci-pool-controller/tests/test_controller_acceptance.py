"""Offline acceptance through the real handler and conditional OCI service doubles.

These tests prove controller decisions, NOT OCI timing, IAM, or scheduler safety.
Run: python3 -m unittest discover -s tests -p test_controller_acceptance.py -v
"""

import copy
import json
import importlib.util
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch
import uuid
from dataclasses import replace
from datetime import timedelta

from controller_fakes import (
    func, obj, response, fake_service_error, make_scale_clients, scale_env,
    NOW, SCALE_POOL_ID, SCALE_INSTANCE_ID, SCALE_NEWEST_INSTANCE_ID,
)


class ControllerAcceptance(unittest.TestCase):
    def setUp(self):
        self.clients, self.events = make_scale_clients(size=3)
        self.env = scale_env()
        profiles = json.loads(self.env["SCALE_TEST_PROFILES_JSON"])
        profiles["small"].update(poolId=SCALE_POOL_ID, maxSize=25, workerType="small")
        self.env.update(
            CONTROLLER_ONLY="true", AUTH_MODE="oci_iam", ENABLE_BOUNDED_GROWTH="true",
            CONTROLLER_MAX_TOTAL_VMS="25", MAX_SCALE_TEST_TOTAL_OCPUS="50",
            SCALE_TEST_PROFILES_JSON=json.dumps(profiles),
        )
        self.clients.pools.instance_configuration.instance_details.launch_details.freeform_tags[func.PROTECTION_TAG] = "1"
        self.time = NOW
        self.instance_sequence = 0

    def invoke(self, payload):
        return func.handle_request(payload, clients=self.clients, environ=self.env, clock=lambda: self.time)

    def demand(self, target, generation=1, profile="small"):
        return self.invoke({"action": "reconcile_pool", "pool_key": profile, "desired_size": target,
                            "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{profile}:{generation}")),
                            "desired_generation": generation})

    def protect(self, value, instance=SCALE_INSTANCE_ID):
        return self.invoke({"action": "set_pool_protection", "pool_key": "small", "instance_id": instance,
                            "request_id": str(uuid.uuid4()), "tag_value": value})

    def detached(self, count, state="TERMINATING"):
        config = func._config(self.env)
        scope = func._resolve_scale_test_scope(config.scale_test_profiles["small"], config, self.clients)
        template = self.clients.compute.instances[SCALE_INSTANCE_ID]
        for index in range(count):
            instance_id = f"ocid1.instance.oc1.iad.retired{index}"
            instance = copy.deepcopy(template)
            instance.id, instance.lifecycle_state = instance_id, state
            instance.freeform_tags[func.PROTECTION_TAG] = "0"
            self.clients.compute.instances[instance_id] = instance
            self.clients.compute.etags[instance_id] = "retired-etag"
            func._write_retirement(scope, self.clients, instance_id, str(uuid.uuid4()), self.time)

    def settle(self, target):
        """Simulate OCI finishing a launch; add genuinely new protected identities."""
        pool = self.clients.pools
        while len(pool.members) < target:
            self.instance_sequence += 1
            instance_id = f"ocid1.instance.oc1.iad.added{self.instance_sequence}"
            instance = copy.deepcopy(self.clients.compute.instances[SCALE_INSTANCE_ID])
            instance.id, instance.lifecycle_state = instance_id, "RUNNING"
            instance.freeform_tags[func.PROTECTION_TAG] = "1"
            self.clients.compute.instances[instance_id] = instance
            self.clients.compute.etags[instance_id] = "new-etag"
            pool.members.append(obj(id=instance_id, time_created="2026-09-04T12:00:00Z"))
        pool.pool.size = pool.pool.current_size = target
        pool.pool.lifecycle_state = "RUNNING"
        pool.etag += "-settled"

    def test_reburst_launches_while_detached_terminations_continue(self):
        self.detached(3)
        status, body = self.demand(8)
        self.assertEqual((status, body.get("outcome")), (202, "scale_out_submitted"), body)
        self.assertEqual(body["target_size"], 8)
        self.assertEqual(body["counted_vms"], 6)
        self.assertEqual(len(self.clients.pools.update_calls), 1)
        self.assertTrue(all(self.clients.compute.instances[f"ocid1.instance.oc1.iad.retired{i}"].lifecycle_state == "TERMINATING" for i in range(3)))

    def test_one_free_slot_makes_partial_progress_at_25_vm_cap(self):
        self.detached(21)  # 3 attached + 21 terminating = 24, not 3.
        status, body = self.demand(25)
        self.assertEqual((status, body.get("target_size")), (202, 4), body)
        self.assertEqual(body["counted_vms"], 24)
        self.settle(4)
        status, body = self.demand(25)
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertEqual(len(self.clients.pools.update_calls), 1)
        self.clients.compute.instances["ocid1.instance.oc1.iad.retired0"].lifecycle_state = "TERMINATED"
        status, body = self.demand(25)
        self.assertEqual(body["target_size"], 5, body)
        self.assertNotEqual(self.clients.pools.update_calls[0][3], self.clients.pools.update_calls[1][3])

    def test_at_cap_no_growth_until_authoritative_termination(self):
        self.detached(22)
        _, body = self.demand(25)
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_ocpu_cap_can_be_tighter_than_vm_cap(self):
        self.env["MAX_SCALE_TEST_TOTAL_OCPUS"] = "9"
        _, body = self.demand(10)
        self.assertEqual(body["target_size"], 4, body)

    def test_profile_physical_cap_includes_detached_workers(self):
        profiles = json.loads(self.env["SCALE_TEST_PROFILES_JSON"])
        profiles["small"]["maxSize"] = 5
        self.env["SCALE_TEST_PROFILES_JSON"] = json.dumps(profiles)
        self.detached(1)
        _, body = self.demand(5)
        self.assertEqual(body["target_size"], 4, body)

    def test_attached_commitments_do_not_satisfy_fresh_demand(self):
        self.assertEqual(self.protect("0")[0], 202)
        _, body = self.demand(3)
        self.assertEqual(body["target_size"], 4, body)
        self.assertEqual(body["observed_usable_size"], 2)
        self.assertFalse(self.clients.pools.detach_calls)

    def test_no_headroom_detaches_one_and_terminates_separately(self):
        self.env["CONTROLLER_MAX_TOTAL_VMS"] = "3"
        for member in self.clients.pools.members:
            self.clients.compute.instances[member.id].freeform_tags[func.PROTECTION_TAG] = "0"
        _, body = self.demand(3)
        self.assertEqual(body["outcome"], "retirement_detached", body)
        self.assertEqual(len(self.clients.pools.detach_calls), 1)
        details = self.clients.pools.detach_calls[0][1]
        self.assertTrue(details.is_decrement_size)
        self.assertFalse(details.is_auto_terminate)
        self.assertEqual(self.clients.compute.instances[details.instance_id].lifecycle_state, "RUNNING")
        self.demand(3)
        self.assertEqual(self.clients.compute.terminate_calls[0][0], details.instance_id)
        self.assertFalse(self.clients.pools.update_calls)

    def test_protected_workers_never_detached(self):
        _, body = self.demand(0)
        self.assertEqual(body["outcome"], "retirement_pending", body)
        self.assertFalse(self.clients.pools.detach_calls)
        self.assertFalse(self.clients.compute.terminate_calls)

    def test_scaling_pool_with_coherent_membership_can_grow_during_retirement(self):
        self.detached(1)
        self.clients.pools.pool.lifecycle_state = "SCALING"
        _, body = self.demand(8)
        self.assertEqual(body["outcome"], "scale_out_submitted", body)
        self.assertEqual(body["target_size"], 8)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_scaling_pool_defers_detach_after_live_api_incorrect_state(self):
        self.assertEqual(self.protect("0", SCALE_NEWEST_INSTANCE_ID)[0], 202)
        self.clients.pools.pool.lifecycle_state = "SCALING"
        _, body = self.demand(2)
        self.assertEqual(body["outcome"], "pool_busy", body)
        self.assertEqual(body["pending_reason"], "detach_requires_running")
        self.assertFalse(self.clients.pools.detach_calls)
        self.assertFalse(self.clients.pools.update_calls)

    def test_non_scaling_transitional_states_still_block_mutations(self):
        for state in ["PROVISIONING", "STARTING", "STOPPING", "STOPPED", "TERMINATING", "TERMINATED", "UNKNOWN"]:
            with self.subTest(state=state):
                self.setUp()
                self.clients.pools.pool.lifecycle_state = state
                _, body = self.demand(8)
                self.assertIn(body["outcome"], {"pool_busy", "pool_not_active"}, body)
                self.assertFalse(self.clients.pools.update_calls)
                self.assertFalse(self.clients.pools.detach_calls)

    def test_scaling_membership_mismatch_still_defers_both_mutations(self):
        for target in [2, 4]:
            with self.subTest(target=target):
                self.setUp()
                self.protect("0", SCALE_NEWEST_INSTANCE_ID)
                self.clients.pools.pool.lifecycle_state = "SCALING"
                self.clients.pools.pool.size = target
                _, body = self.demand(8)
                self.assertEqual(body["outcome"], "pool_state_changed", body)
                self.assertFalse(self.clients.pools.update_calls)
                self.assertFalse(self.clients.pools.detach_calls)

    def test_scaling_pool_still_obeys_physical_cap(self):
        self.detached(22)
        self.clients.pools.pool.lifecycle_state = "SCALING"
        _, body = self.demand(25)
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_scaling_pool_with_unknown_launch_does_not_resubmit(self):
        self.clients.pools.update_failures.append(TimeoutError("response lost"))
        self.demand(8)
        self.clients.pools.pool.lifecycle_state = "SCALING"
        _, body = self.demand(10, 2)
        self.assertEqual(body["outcome"], "launch_pending", body)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_acknowledged_launch_can_extend_target_while_scaling(self):
        self.assertEqual(self.demand(8)[1]["outcome"], "scale_out_submitted")
        _, body = self.demand(10, 2)
        self.assertEqual(body["outcome"], "scale_out_submitted", body)
        # Five unmaterialized slots in the first target already satisfy demand.
        self.assertEqual(body["target_size"], 10)
        self.assertEqual(body["counted_vms"], 8)
        self.assertEqual([c[1].size for c in self.clients.pools.update_calls], [8, 10])

    def test_same_demand_or_reduced_demand_does_not_duplicate_pending_growth(self):
        self.demand(8)
        for target, generation in [(8, 1), (8, 2), (4, 3)]:
            _, body = self.demand(target, generation)
            self.assertEqual(body["outcome"], "launch_pending", body)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_extension_uses_only_remaining_physical_capacity(self):
        self.detached(16)
        self.demand(8)
        _, body = self.demand(12, 2)
        self.assertEqual(body["target_size"], 9, body)
        self.assertEqual(body["counted_vms"], 24)
        _, body = self.demand(12, 2)
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertEqual(len(self.clients.pools.update_calls), 2)

    def test_rejected_extension_preserves_prior_accepted_reservation(self):
        self.demand(8)
        prior = copy.deepcopy(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"])
        self.clients.pools.update_failures.append(fake_service_error(409, "IncorrectState"))
        self.demand(10, 2)
        self.assertEqual(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"], prior)

    def test_unknown_extension_holds_larger_target_and_blocks_further_growth(self):
        self.demand(8)
        self.clients.pools.update_failures.append(TimeoutError("lost extension"))
        _, body = self.demand(10, 2)
        self.assertEqual(body["outcome"], "scale_out_outcome_unknown", body)
        self.assertEqual(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"]["target"], 10)
        self.assertEqual(self.demand(12, 3)[1]["outcome"], "launch_pending")
        self.assertEqual(len(self.clients.pools.update_calls), 2)

    def test_legacy_reservation_without_acceptance_marker_cannot_extend(self):
        self.demand(8)
        record = self.clients.objects.get_json(func.CAPACITY_OBJECT)
        record["pending"]["small"].pop("accepted", None)
        self.clients.objects.seed(func.CAPACITY_OBJECT, record)
        self.assertEqual(self.demand(10, 2)[1]["outcome"], "launch_pending")
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_membership_change_before_submission_refunds_only_unsent_increment(self):
        for has_prior in [False, True]:
            with self.subTest(has_prior=has_prior):
                self.setUp()
                prior = None
                if has_prior:
                    self.demand(8)
                    prior = copy.deepcopy(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"])
                original = func._put_capacity
                def racing_put(config, clients, record, etag):
                    result = original(config, clients, record, etag)
                    pending = record["pending"].get("small")
                    if pending and pending.get("accepted") is False:
                        self.clients.pools.members.pop()
                    return result
                with patch.object(func, "_put_capacity", racing_put):
                    _, body = self.demand(10, 2)
                self.assertEqual(body["outcome"], "pool_state_changed", body)
                self.assertTrue(body["retryable"])
                self.assertEqual(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"].get("small"), prior)
                self.assertEqual(len(self.clients.pools.update_calls), int(has_prior))

    def test_failed_work_request_blocks_extending_accepted_launch(self):
        self.demand(8)
        self.clients = replace(self.clients, work_requests=obj(
            get_work_request=lambda value: response(obj(status="FAILED")),
            list_work_request_errors=lambda value, **kwargs: response([]),
        ))
        self.assertEqual(self.demand(10, 2)[1]["outcome"], "launch_failed")
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_extension_keeps_original_deadline_and_work_request_ids(self):
        self.demand(8)
        prior = self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"]
        self.time += timedelta(seconds=800)
        self.demand(10, 2)
        pending = self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"]
        self.assertEqual(pending["createdAt"], prior["createdAt"])
        self.assertTrue(set(prior["workRequestIds"]) <= set(pending["workRequestIds"]))
        self.time += timedelta(seconds=101)
        self.assertEqual(self.demand(12, 3)[1]["outcome"], "launch_verification_required")
        self.assertEqual(len(self.clients.pools.update_calls), 2)

    def test_stale_membership_after_decrement_does_not_launch_during_cleanup(self):
        retirees = [m.id for m in self.clients.pools.members if m.id != SCALE_INSTANCE_ID]
        for instance_id in retirees:
            self.assertEqual(self.protect("0", instance_id)[0], 202)
        # Live OCI returned RUNNING with the decremented target before its
        # membership list stopped including the detached worker.
        self.clients.pools.pool.size = self.clients.pools.pool.current_size = 2
        _, body = self.demand(1)
        self.assertEqual(body["outcome"], "pool_state_changed", body)
        self.assertFalse(self.clients.pools.update_calls)
        self.assertFalse(self.clients.pools.detach_calls)
        self.clients.pools.members = [m for m in self.clients.pools.members if m.id != retirees[0]]
        self.clients.compute.instances[retirees[0]].lifecycle_state = "TERMINATING"
        _, body = self.demand(1)
        self.assertEqual(body["outcome"], "retirement_detached", body)
        self.assertFalse(self.clients.pools.update_calls)
        self.assertEqual(len(self.clients.pools.detach_calls), 1)

    def test_membership_smaller_than_target_defers_new_launch(self):
        self.clients.pools.pool.size = self.clients.pools.pool.current_size = 4
        _, body = self.demand(8)
        self.assertEqual(body["outcome"], "pool_state_changed", body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_historical_terminated_member_is_excluded_only_after_compute_confirmation(self):
        member = self.clients.pools.members[-1]
        member.state = "Terminated"  # Actual OCI summary uses title case.
        self.clients.compute.instances[member.id].lifecycle_state = "TERMINATED"
        self.clients.pools.pool.size = self.clients.pools.pool.current_size = 2
        _, body = self.demand(3)
        self.assertEqual(body["outcome"], "scale_out_submitted", body)
        self.assertEqual(body["observed_attached_size"], 2)

    def test_terminated_summary_with_live_compute_still_counts(self):
        member = self.clients.pools.members[-1]
        member.state = "Terminated"
        self.clients.compute.instances[member.id].lifecycle_state = "TERMINATING"
        self.clients.pools.pool.size = self.clients.pools.pool.current_size = 2
        _, body = self.demand(3)
        self.assertEqual(body["outcome"], "pool_state_changed", body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_terminated_summary_unreadable_compute_fails_closed(self):
        member = self.clients.pools.members[-1]
        member.state = "Terminated"
        del self.clients.compute.instances[member.id]
        status, _ = self.demand(8)
        self.assertGreaterEqual(status, 400)
        self.assertFalse(self.clients.pools.update_calls)

    def test_sdk_wrapped_transport_error_is_retryable_without_exposing_details(self):
        class RequestException(Exception):
            pass
        with patch.object(func, "oci", obj(exceptions=obj(RequestException=RequestException))):
            error = func._service_error(RequestException("SECRET-ENDPOINT-DETAIL"))
        self.assertEqual(error.reason, "oci_transient_error")
        self.assertTrue(error.retryable)
        self.assertNotIn("SECRET-ENDPOINT-DETAIL", error.message)

    def test_unknown_exception_logs_only_type_and_stays_fail_closed(self):
        def broken_read(*args, **kwargs):
            raise ValueError("SECRET-ENDPOINT-DETAIL")
        self.clients.pools.get_instance_pool = broken_read
        with self.assertLogs(func.LOG, level="ERROR") as logs:
            status, body = self.demand(3)
        self.assertEqual((status, body["outcome"]), (500, "internal_error"))
        self.assertFalse(body["retryable"])
        self.assertIn("ValueError", " ".join(logs.output))
        self.assertNotIn("SECRET-ENDPOINT-DETAIL", " ".join(logs.output))
        self.assertNotIn("SECRET-ENDPOINT-DETAIL", json.dumps(body))

    def test_lost_launch_response_preserves_reservation_and_no_resubmit(self):
        self.clients.pools.update_failures.append(TimeoutError("response lost"))
        _, body = self.demand(8)
        self.assertEqual(body["outcome"], "scale_out_outcome_unknown", body)
        self.assertEqual(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"]["small"]["target"], 8)
        for generation in [1, 2, 3]:
            _, body = self.demand(8, generation)
            self.assertEqual(body["outcome"], "launch_pending", body)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_accepted_lost_response_resolves_only_after_observation(self):
        original = self.clients.pools.update_instance_pool
        def accepted(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError("response lost after acceptance")
        self.clients.pools.update_instance_pool = accepted
        self.demand(8)
        self.assertEqual(self.demand(8)[1]["outcome"], "launch_pending")
        self.settle(8)
        _, body = self.demand(8)
        self.assertEqual(body["result"], "completed", body)
        self.assertFalse(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"])
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_observation_deadline_never_refunds_unknown_capacity(self):
        self.clients.pools.update_failures.append(TimeoutError())
        self.demand(8)
        self.time += timedelta(seconds=901)
        _, body = self.demand(8)
        self.assertEqual(body["outcome"], "launch_verification_required", body)
        self.assertTrue(body["intervention_required"])
        self.assertFalse(body["retryable"])
        self.assertTrue(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"])

    def test_failed_work_request_holds_reservation_and_requests_review(self):
        self.clients = replace(self.clients, work_requests=obj(
            get_work_request=lambda value: response(obj(status="FAILED")),
            list_work_request_errors=lambda value, **kwargs: response([obj(code="LimitExceeded", message="private details")]),
        ))
        self.demand(8)
        _, body = self.demand(8)
        self.assertEqual(body["outcome"], "launch_failed", body)
        self.assertFalse(body["retryable"])
        self.assertEqual(body["work_request_error_codes"], ["LimitExceeded"])
        self.assertNotIn("private details", json.dumps(body))
        self.assertTrue(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"])

    def test_explicit_limit_rejection_is_terminal_and_refunds_unaccepted_launch(self):
        self.clients.pools.update_failures.append(fake_service_error(400, "LimitExceeded"))
        status, body = self.demand(8)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["outcome"], "oci_capacity_limit", body)
        self.assertFalse(body["retryable"])
        self.assertFalse(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"])
        self.demand(8)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_stopped_member_prevents_false_completion(self):
        self.clients.compute.instances[SCALE_INSTANCE_ID].lifecycle_state = "STOPPED"
        status, body = self.demand(3)
        self.assertEqual(status, 202, body)
        self.assertNotEqual(body["result"], "completed")
        self.assertEqual(body["readiness_scope"], "infrastructure_only")
        self.assertIsNone(body["scheduler_ready"])

    def test_malformed_protection_values_are_400_not_500(self):
        for value in [[], {}, None, 0, 1, True, False, "", "true", ["0"]]:
            with self.subTest(value=value):
                status, body = self.protect(value)
                self.assertEqual(status, 400, body)
                self.assertEqual(body["reason"], "invalid_protection_value")
        self.assertFalse(self.clients.compute.update_calls)

    def test_committed_retirement_cannot_be_reprotected(self):
        self.assertEqual(self.protect("0")[0], 202)
        status, body = self.protect("1")
        self.assertEqual((status, body["reason"]), (409, "retirement_committed"))

    def test_dry_run_never_mutates_compute_or_activates_protocol(self):
        self.env["DRY_RUN"] = "true"
        self.assertEqual(self.demand(8)[0], 200)
        self.assertNotIn(func.CAPACITY_OBJECT, self.clients.objects.objects)
        self.assertFalse(self.clients.pools.update_calls)
        self.assertFalse(self.clients.pools.detach_calls)

    def test_termination_kill_switch_preserves_workers(self):
        self.env["ENABLE_TERMINATION"] = "false"
        self.protect("0")
        self.demand(0)
        self.assertFalse(self.clients.compute.terminate_calls)
        self.assertFalse(self.clients.pools.detach_calls)

    def test_missing_committed_instance_blocks_growth_fail_closed(self):
        self.detached(1)
        del self.clients.compute.instances["ocid1.instance.oc1.iad.retired0"]
        status, body = self.demand(8)
        self.assertEqual(body.get("outcome"), "retirement_verification_pending", body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_enrollment_changes_cannot_hide_reserved_capacity(self):
        self.demand(8)
        profiles = json.loads(self.env["SCALE_TEST_PROFILES_JSON"])
        profiles["small"]["ocpus"] = 4
        self.env["SCALE_TEST_PROFILES_JSON"] = json.dumps(profiles)
        status, body = self.demand(8, 2)
        self.assertEqual((status, body.get("outcome")), (409, "capacity_ledger_mismatch"), body)

    def test_flag_rollback_cannot_ignore_capacity_protocol(self):
        self.demand(8)
        self.env["ENABLE_BOUNDED_GROWTH"] = "false"
        status, body = self.demand(8, 2)
        self.assertEqual((status, body.get("outcome")), (409, "capacity_protocol_required"), body)

    def test_superseded_generation_cannot_resize(self):
        self.demand(3, 2)
        status, body = self.demand(8, 1)
        self.assertFalse(self.clients.pools.update_calls)
        self.assertIn(body.get("outcome", body.get("reason")), {"superseded", "stale_generation"}, body)

    def test_newer_demand_winning_before_lease_reports_superseded_and_replays(self):
        original = func._acquire_pool_mutation_lease
        successor = {}
        def race(config, clients, profile, request_id, attempt_id, generation, desired, now):
            if generation == 1:
                successor["reply"] = self.demand(8, 2)
                successor["capacity"] = copy.deepcopy(self.clients.objects.get_json(func.CAPACITY_OBJECT))
            return original(config, clients, profile, request_id, attempt_id, generation, desired, now)
        # The old request passed its initial latest-generation check, but a
        # newer invocation completes before the old one acquires the lease.
        with patch.object(func, "_acquire_pool_mutation_lease", side_effect=race):
            status, body = self.demand(2, 1)
        self.assertEqual(successor["reply"][1]["outcome"], "scale_out_submitted")
        self.assertEqual((status, body["result"], body["request_state"]), (200, "superseded", "superseded"), body)
        self.assertFalse(body["retryable"])
        record = self.clients.objects.get_json(func._request_object_name(body["request_id"]))
        self.assertEqual((record["state"], record["httpStatus"]), ("superseded", 200))
        replay_status, replay = self.demand(2, 1)
        self.assertEqual((replay_status, replay["result"]), (200, "superseded"))
        self.assertTrue(replay["idempotent_replay"])
        status_code, observed = self.invoke({"action": "request_status", "request_id": body["request_id"]})
        self.assertEqual((status_code, observed["request_state"]), (200, "superseded"))
        self.assertEqual(self.clients.objects.get_json(func.CAPACITY_OBJECT), successor["capacity"])
        self.assertEqual([call[1].size for call in self.clients.pools.update_calls], [8])
        self.assertFalse(self.clients.pools.detach_calls)

    def test_supersession_race_preserves_prior_submissions_and_successor_accounting(self):
        for operation in ("launch", "detach", "unknown_launch"):
            with self.subTest(operation=operation):
                self.setUp()
                if operation == "detach":
                    self.protect("0", SCALE_NEWEST_INSTANCE_ID)
                if operation == "unknown_launch":
                    self.clients.pools.update_failures.append(TimeoutError("launch response lost"))
                target = 2 if operation == "detach" else 5
                _, prior = self.demand(target, 1)
                original = func._acquire_pool_mutation_lease
                successor = {}
                def race(config, clients, profile, request_id, attempt_id, generation, desired, now):
                    if generation == 1:
                        successor["reply"] = self.demand(8, 2)
                        # Capture every coordination/retirement object after
                        # the newer request; finishing the old response must
                        # not change any of them.
                        successor["objects"] = copy.deepcopy(self.clients.objects.objects)
                    return original(config, clients, profile, request_id, attempt_id, generation, desired, now)
                with patch.object(func, "_acquire_pool_mutation_lease", side_effect=race):
                    status, body = self.demand(target, 1)
                self.assertEqual(successor["reply"][1]["outcome"],
                                 "launch_pending" if operation == "unknown_launch" else "scale_out_submitted")
                self.assertEqual((status, body["result"]), (200, "superseded"), body)
                for field in ("detached_instance_ids", "work_request_ids"):
                    self.assertTrue(set(prior.get(field, [])) <= set(body.get(field, [])))
                old_record = func._request_object_name(body["request_id"])
                for name, value in successor["objects"].items():
                    if name != old_record:
                        self.assertEqual(self.clients.objects.objects[name], value, name)
                counts = (len(self.clients.pools.update_calls), len(self.clients.pools.detach_calls), len(self.clients.compute.terminate_calls))
                _, replay = self.demand(target, 1)
                self.assertEqual(replay["result"], "superseded")
                self.assertEqual(counts, (len(self.clients.pools.update_calls), len(self.clients.pools.detach_calls), len(self.clients.compute.terminate_calls)))

    def test_retryable_supersession_signal_is_terminal_not_queued(self):
        with patch.object(func, "_acquire_pool_mutation_lease", side_effect=func.HarnessError(
                409, "request_superseded", "newer demand won", retryable=True)):
            status, body = self.demand(8)
        self.assertEqual((status, body["result"], body["request_state"]), (200, "superseded", "superseded"), body)
        self.assertFalse(body["retryable"])
        self.assertFalse(self.clients.pools.update_calls)

    def test_other_lease_errors_are_not_mislabeled_as_supersession(self):
        for retryable, expected_status, expected_state in [(True, 202, "queued"), (False, 502, "failed")]:
            with self.subTest(retryable=retryable):
                self.setUp()
                reason = "pool_mutation_busy" if retryable else "invalid_ledger_record"
                with patch.object(func, "_acquire_pool_mutation_lease", side_effect=func.HarnessError(
                        502, reason, "not supersession", retryable=retryable)):
                    status, body = self.demand(8)
                self.assertEqual((status, body["result"], body["outcome"]), (expected_status, expected_state, reason))
                self.assertFalse(self.clients.pools.update_calls)

    def test_legacy_behavior_remains_retire_first(self):
        self.env["ENABLE_BOUNDED_GROWTH"] = "false"
        self.detached(1)
        _, body = self.demand(8)
        self.assertEqual(body["replacement_policy"], "retire_first", body)
        self.assertFalse(self.clients.pools.update_calls)

    def add_second_pool(self):
        first = self.clients.pools
        second = copy.deepcopy(first)
        second.pool.id = "ocid1.instancepool.oc1.iad.second123"
        second.pool.display_name = "harness-scale-second"
        second.pool.size = second.pool.current_size = 0
        second.members = []
        second.pool.freeform_tags[func.SCALE_TEST_PROFILE_TAG] = "second"
        second.instance_configuration.id = "ocid1.instanceconfiguration.oc1.iad.second123"
        second.pool.instance_configuration_id = second.instance_configuration.id
        second.instance_configuration.freeform_tags[func.SCALE_TEST_PROFILE_TAG] = "second"
        second.instance_configuration.instance_details.launch_details.freeform_tags[func.SCALE_TEST_PROFILE_TAG] = "second"
        class Pools:
            def get_instance_pool(self, pool_id):
                return (first if pool_id == first.pool.id else second).get_instance_pool(pool_id)
            def get_instance_configuration(self, config_id):
                return (first if config_id == first.instance_configuration.id else second).get_instance_configuration(config_id)
            def list_instance_pool_instances(self, compartment, pool_id, **kwargs):
                return (first if pool_id == first.pool.id else second).list_instance_pool_instances(compartment, pool_id, **kwargs)
            def update_instance_pool(self, pool_id, *args, **kwargs):
                return (first if pool_id == first.pool.id else second).update_instance_pool(pool_id, *args, **kwargs)
        self.clients = replace(self.clients, pools=Pools())
        profiles = json.loads(self.env["SCALE_TEST_PROFILES_JSON"])
        profiles["second"] = {**profiles["small"], "poolId": second.pool.id, "poolName": second.pool.display_name}
        self.env["SCALE_TEST_PROFILES_JSON"] = json.dumps(profiles)
        return first, second

    def test_other_pool_counts_not_yet_visible_launch_reservation(self):
        first, second = self.add_second_pool()
        self.env["CONTROLLER_MAX_TOTAL_VMS"] = "5"
        first.update_failures.append(TimeoutError())
        _, body = self.demand(5)
        self.assertEqual(body["outcome"], "scale_out_outcome_unknown", body)
        self.assertEqual(first.pool.size, 3)  # OCI still reports the OLD target.
        _, body = self.demand(2, profile="second")
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertFalse(second.update_calls)

    def test_last_slot_is_serialized_across_competing_pools(self):
        first, second = self.add_second_pool()
        self.env["CONTROLLER_MAX_TOTAL_VMS"] = "4"
        nested = []
        original = first.update_instance_pool
        def competing(*args, **kwargs):
            nested.append(self.demand(1, profile="second"))
            return original(*args, **kwargs)
        first.update_instance_pool = competing
        self.demand(4)
        self.assertEqual(nested[0][1]["outcome"], "scale_test_budget_busy", nested)
        self.assertFalse(second.update_calls)
        _, body = self.demand(1, profile="second")
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertFalse(second.update_calls)

    def test_active_pool_cap_includes_reserved_and_retiring_profiles(self):
        first, second = self.add_second_pool()
        self.env["MAX_ACTIVE_SCALE_TEST_PROFILES"] = "1"
        _, body = self.demand(1, profile="second")
        self.assertEqual(body["outcome"], "capacity_pending", body)
        self.assertFalse(second.update_calls)

    def test_expired_budget_lease_prevents_launch_and_reservation(self):
        original = func._capacity_usage
        def expire(*args):
            usage = original(*args)
            self.time += timedelta(seconds=181)
            return usage
        from unittest.mock import patch
        with patch.object(func, "_capacity_usage", side_effect=expire):
            _, body = self.demand(8)
        self.assertIn(body["outcome"], {"pool_mutation_lease_lost", "scale_test_budget_busy", "pool_mutation_fence_lost"}, body)
        self.assertFalse(self.clients.pools.update_calls)
        self.assertFalse(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"])

    def test_reservation_cas_failure_prevents_oci_launch(self):
        self.demand(3)
        original = self.clients.objects.put_object
        def conflict(namespace, bucket, name, *args, **kwargs):
            if name == func.CAPACITY_OBJECT:
                raise fake_service_error(412, "NoEtagMatch")
            return original(namespace, bucket, name, *args, **kwargs)
        self.clients.objects.put_object = conflict
        _, body = self.demand(8, 2)
        self.assertEqual(body["outcome"], "stale_resource", body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_lost_detach_response_does_not_decrement_twice(self):
        self.protect("0", SCALE_NEWEST_INSTANCE_ID)
        def lost(*args):
            raise TimeoutError("accepted detach response lost")
        self.clients.pools.after_detach = lost
        self.demand(2)
        self.clients.pools.after_detach = None
        self.demand(2)
        self.assertEqual(len(self.clients.pools.detach_calls), 1)
        self.assertEqual(self.clients.pools.pool.size, 2)
        self.assertEqual(len(self.clients.compute.terminate_calls), 1)

    def test_detached_cleanup_continues_during_unknown_launch(self):
        self.detached(2, state="RUNNING")
        self.clients.pools.update_failures.append(TimeoutError())
        self.demand(8)
        self.demand(8)
        self.assertEqual(len(self.clients.compute.terminate_calls), 2)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_reprotected_committed_worker_is_not_detached_or_reused(self):
        self.protect("0")
        self.clients.compute.instances[SCALE_INSTANCE_ID].freeform_tags[func.PROTECTION_TAG] = "1"
        _, body = self.demand(2)
        self.assertEqual(body["observed_usable_size"], 2, body)
        self.assertEqual(body["retiring_count"], 1)
        self.assertFalse(self.clients.pools.detach_calls)

    def test_wrong_provenance_detached_worker_is_never_terminated(self):
        self.detached(1, state="RUNNING")
        self.clients.compute.instances["ocid1.instance.oc1.iad.retired0"].freeform_tags[func.HARNESS_TAG] = "other"
        self.demand(3)
        self.assertFalse(self.clients.compute.terminate_calls)

    def test_status_is_read_only_and_includes_unknown_reservations(self):
        self.clients.pools.update_failures.append(TimeoutError())
        self.demand(8)
        before = len(self.clients.objects.put_calls)
        status, body = self.invoke({"action": "scale_test_status"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["safety"]["totalEffectiveVms"], 8)
        self.assertEqual(body["profiles"][0]["pool"]["reservedTarget"], 8)
        self.assertEqual(body["profiles"][0]["retirement"]["replacementPolicy"], "bounded_growth_preview")
        self.assertIsNone(body["profiles"][0]["readiness"]["schedulerReady"])
        self.assertEqual(len(self.clients.objects.put_calls), before)

    def test_invalid_bounded_growth_configuration_fails_closed(self):
        for field, value in [("CONTROLLER_MAX_TOTAL_VMS", "0"), ("CONTROLLER_MAX_TOTAL_VMS", "-1"),
                             ("CONTROLLER_LAUNCH_TIMEOUT_SECONDS", "0"), ("AUTH_MODE", "bearer")]:
            with self.subTest(field=field, value=value):
                env = {**self.env, field: value}
                with self.assertRaises(func.HarnessError):
                    func._config(env)

    def test_request_id_reuse_with_changed_target_is_rejected(self):
        self.demand(3)
        status, body = self.demand(8)
        self.assertEqual(status, 409, body)
        self.assertFalse(self.clients.pools.update_calls)

    def test_permanent_and_transient_oci_error_classifications(self):
        for status, code, retryable, reason in [
            (400, "LimitExceeded", False, "oci_capacity_limit"),
            (400, "QuotaExceeded", False, "oci_capacity_limit"),
            (500, "OutOfHostCapacity", False, "oci_capacity_unavailable"),
            (400, "InvalidParameter", False, "oci_invalid_request"),
            (403, "NotAuthorized", False, "oci_authorization_failed"),
            (409, "IncorrectState", True, "pool_busy"),
            (429, "TooManyRequests", True, "oci_throttled"),
            (503, "ServiceUnavailable", True, "oci_transient_error"),
        ]:
            with self.subTest(status=status, code=code):
                error = func._service_error(fake_service_error(status, code))
                self.assertEqual((error.retryable, error.reason), (retryable, reason))

    def test_reburst_end_to_end_keeps_exact_retirement_and_converges(self):
        self.protect("0", SCALE_NEWEST_INSTANCE_ID)
        _, body = self.demand(2)
        self.assertEqual(body["outcome"], "retirement_detached", body)
        _, body = self.demand(3, 2)
        self.assertEqual(body["outcome"], "scale_out_submitted", body)
        self.assertEqual(body["target_size"], 3)
        self.assertEqual(self.clients.compute.instances[SCALE_NEWEST_INSTANCE_ID].lifecycle_state, "TERMINATING")
        self.assertEqual(set(body["work_request_ids"]), {"wr-terminate", "wr-scale-test"})
        self.settle(3)
        _, body = self.demand(3, 2)
        self.assertNotEqual(body["result"], "completed")
        self.clients.compute.instances[SCALE_NEWEST_INSTANCE_ID].lifecycle_state = "TERMINATED"
        _, body = self.demand(3, 2)
        self.assertEqual(body["result"], "completed", body)
        self.assertEqual(len(self.clients.pools.detach_calls), 1)
        self.assertEqual(len(self.clients.pools.update_calls), 1)

    def test_diagnostic_access_denied_retains_capacity_and_reports_blocker(self):
        def denied(value):
            raise fake_service_error(403, "NotAuthorized")
        self.clients = replace(self.clients, work_requests=obj(get_work_request=denied))
        self.demand(8)
        _, body = self.demand(8)
        self.assertEqual(body["outcome"], "launch_diagnostics_unavailable", body)
        self.assertEqual(body["pending_reason"], "oci_authorization_failed")
        self.assertFalse(body["retryable"])
        self.assertTrue(self.clients.objects.get_json(func.CAPACITY_OBJECT)["pending"])

    def test_final_protection_change_blocks_detach(self):
        self.protect("0")
        original = func._write_retirement
        def conflicting_writer(scope, clients, instance_id, *args, **kwargs):
            registry = original(scope, clients, instance_id, *args, **kwargs)
            clients.compute.instances[instance_id].freeform_tags[func.PROTECTION_TAG] = "1"
            return registry
        from unittest.mock import patch
        with patch.object(func, "_write_retirement", side_effect=conflicting_writer):
            self.demand(2)
        self.assertFalse(self.clients.pools.detach_calls)

    def test_termination_etag_conflict_cannot_kill_reprotected_worker(self):
        self.detached(1, state="RUNNING")
        original = self.clients.compute.terminate_instance
        def changed(instance_id, **kwargs):
            self.clients.compute.etags[instance_id] = "changed"
            self.clients.compute.instances[instance_id].freeform_tags[func.PROTECTION_TAG] = "1"
            return original(instance_id, **kwargs)
        self.clients.compute.terminate_instance = changed
        _, body = self.demand(3)
        self.assertEqual(body["outcome"], "stale_resource", body)
        self.assertFalse(self.clients.compute.terminate_calls)

    def test_excluded_worker_never_detached_or_terminated(self):
        self.protect("0")
        status, body = self.invoke({"action": "reconcile_pool", "pool_key": "small", "desired_size": 2,
                                   "request_id": str(uuid.uuid4()), "desired_generation": 1,
                                   "exclude_instance_ids": [SCALE_INSTANCE_ID]})
        self.assertEqual(status, 202, body)
        self.assertFalse(self.clients.pools.detach_calls)
        self.assertFalse(self.clients.compute.terminate_calls)

    def test_legacy_completion_also_checks_actual_instance_state(self):
        self.env["ENABLE_BOUNDED_GROWTH"] = "false"
        self.clients.compute.instances[SCALE_INSTANCE_ID].lifecycle_state = "STOPPED"
        status, body = self.demand(3)
        self.assertEqual(status, 202, body)
        self.assertEqual(body["outcome"], "convergence_pending", body)

    def test_seeded_mixed_demand_and_retirement_never_exceeds_caps(self):
        self.env["CONTROLLER_MAX_TOTAL_VMS"] = "10"
        self.env["MAX_SCALE_TEST_TOTAL_OCPUS"] = "16"  # tighter than VM cap
        rng = random.Random(20260916)
        for generation in range(1, 301):
            if self.clients.pools.pool.lifecycle_state == "SCALING" and rng.random() < 0.6:
                self.settle(self.clients.pools.pool.size)
            for instance in list(self.clients.compute.instances.values()):
                if instance.lifecycle_state == "TERMINATING" and rng.random() < 0.3:
                    instance.lifecycle_state = "TERMINATED"
            protected = [member.id for member in self.clients.pools.members
                         if self.clients.compute.instances[member.id].freeform_tags[func.PROTECTION_TAG] == "1"]
            if protected and rng.random() < 0.5:
                self.protect("0", rng.choice(protected))
            self.time += timedelta(seconds=5)
            status, body = self.demand(rng.randrange(13), generation)
            self.assertIn(status, {200, 202}, body)
            physical = sum(instance.lifecycle_state != "TERMINATED" for instance in self.clients.compute.instances.values())
            self.assertLessEqual(physical, 10, (generation, body))
            self.assertLessEqual(physical * 2, 16, (generation, body))
            config = func._config(self.env)
            capacity, _ = func._capacity_record(config, self.clients)
            usage = func._capacity_usage(config, self.clients, capacity)
            self.assertLessEqual(sum(usage.values()), 8, (generation, body))
        self.assertGreater(len(self.clients.pools.update_calls), 3)
        self.assertGreater(len(self.clients.pools.detach_calls), 3)

    def test_client_stops_maintenance_on_intervention_required(self):
        spec = importlib.util.spec_from_file_location("acceptance_client", Path(__file__).parents[1] / "examples/pool_controller.py")
        client = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = client
        spec.loader.exec_module(client)
        outbox = client.Outbox(":memory:")
        payload = outbox.demand("small", 8, 1)
        calls = []
        def transport(value):
            calls.append(value)
            return client.Reply(202, {"request_state": "submitted", "retryable": False,
                                      "intervention_required": True, "outcome": "launch_verification_required"})
        controller = client.Controller(transport, outbox)
        with self.assertRaises(client.ControllerRejected):
            controller.send(payload)
        self.assertEqual(outbox.state(payload["request_id"]), "failed")
        controller.tick("small")
        self.assertEqual(len(calls), 1)
        outbox.db.close()


if __name__ == "__main__":
    unittest.main()
