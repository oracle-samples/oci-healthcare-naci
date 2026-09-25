"""OCI service doubles adapted from the project's 0.11.0-rc.2 test support.

No credentials, OCI services or external test directories are used at runtime.
Responses model decisions and conditional writes, not OCI timing guarantees.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


MODULE_PATH = pathlib.Path(__file__).parents[1] / "function" / "func.py"
SPEC = importlib.util.spec_from_file_location("scaler_func", MODULE_PATH)
assert SPEC and SPEC.loader
func = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = func
SPEC.loader.exec_module(func)


POOL_ID = "ocid1.instancepool.oc1.iad.pool123"
COMPARTMENT_ID = "ocid1.compartment.oc1..compartment123"
OLD_ID = "ocid1.instance.oc1.iad.old123"
NEW_ID = "ocid1.instance.oc1.iad.new123"
SCALE_POOL_ID = "ocid1.instancepool.oc1.iad.scalepool123"
SCALE_INSTANCE_ID = "ocid1.instance.oc1.iad.scale123"
SCALE_MIDDLE_INSTANCE_ID = "ocid1.instance.oc1.iad.scalemiddle123"
SCALE_NEWEST_INSTANCE_ID = "ocid1.instance.oc1.iad.scalenewest123"
SCALE_CONFIGURATION_ID = "ocid1.instanceconfiguration.oc1.iad.scaleconfig123"
LEDGER_NAMESPACE = "testnamespace"
LEDGER_BUCKET = "request-ledger"
ATOMIC_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
CONFLICT_REQUEST_ID = "22222222-2222-4222-8222-222222222222"
STABLE_TOKEN_REQUEST_ID = "33333333-3333-4333-8333-333333333333"
SCALE_IN_REQUEST_ID = "44444444-4444-4444-8444-444444444444"
SHORTFALL_REQUEST_ID = "55555555-5555-4555-8555-555555555555"
AUTOSCALER_REQUEST_ID = "66666666-6666-4666-8666-666666666666"
STATUS_REQUEST_ID = "77777777-7777-4777-8777-777777777777"
MISMATCH_REQUEST_ID = "88888888-8888-4888-8888-888888888888"
NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def obj(**values):
    return SimpleNamespace(**values)


def response(data=None, **headers):
    return obj(data=data, headers=headers)


def fake_service_error(status, code=""):
    error = RuntimeError(f"fake OCI service error {status}")
    error.status = status
    error.code = code
    return error


class FakeObjectStorage:
    """Strongly consistent object store with OCI conditional-write semantics."""

    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.put_calls = []
        self.get_calls = []
        self.precondition_failures = []
        self.get_hook = None
        self._inside_get_hook = False
        self._etag_counter = 0
        self.seed(
            func.SCALE_TEST_BUDGET_LOCK_OBJECT,
            {"owner": None, "leaseUntil": None},
        )

    def _check_scope(self, namespace, bucket):
        if namespace != LEDGER_NAMESPACE or bucket != LEDGER_BUCKET:
            raise AssertionError("backend escaped its fixed request-ledger bucket")

    def _next_etag(self):
        self._etag_counter += 1
        return f'"object-etag-{self._etag_counter}"'

    def seed(self, object_name, value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.objects[object_name] = raw
        self.etags[object_name] = self._next_etag()

    def get_json(self, object_name):
        return json.loads(self.objects[object_name].decode("utf-8"))

    def get_object(self, namespace, bucket, object_name, **kwargs):
        self._check_scope(namespace, bucket)
        self.get_calls.append((object_name, kwargs))
        if self.get_hook is not None and not self._inside_get_hook:
            self._inside_get_hook = True
            try:
                self.get_hook(self, object_name, len(self.get_calls))
            finally:
                self._inside_get_hook = False
        if object_name not in self.objects:
            raise fake_service_error(404, "ObjectNotFound")
        return response(
            obj(content=self.objects[object_name]),
            etag=self.etags[object_name],
        )

    def put_object(
        self,
        namespace,
        bucket,
        object_name,
        body,
        *,
        if_none_match=None,
        if_match=None,
        **kwargs,
    ):
        self._check_scope(namespace, bucket)
        self.put_calls.append(
            {
                "object_name": object_name,
                "if_none_match": if_none_match,
                "if_match": if_match,
                **kwargs,
            }
        )
        exists = object_name in self.objects
        if if_none_match == "*" and exists:
            self.precondition_failures.append((object_name, "if-none-match"))
            raise fake_service_error(412, "NoEtagMatch")
        if if_match is not None and (not exists or self.etags[object_name] != if_match):
            self.precondition_failures.append((object_name, "if-match"))
            raise fake_service_error(412, "NoEtagMatch")
        raw = body.read() if hasattr(body, "read") else body
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, bytes):
            raise AssertionError("object body must be bytes or a readable byte stream")
        if kwargs.get("content_length") != len(raw):
            raise AssertionError("content_length does not match the object body")
        self.objects[object_name] = raw
        self.etags[object_name] = self._next_etag()
        return response(None, etag=self.etags[object_name])


class FakeAutoscalingClient:
    def __init__(self, configurations=None):
        self.configurations = list(configurations or [])
        self.list_calls = []

    def list_auto_scaling_configurations(self, compartment_id, **kwargs):
        if compartment_id != COMPARTMENT_ID:
            raise AssertionError("backend escaped its fixed autoscaling compartment")
        self.list_calls.append((compartment_id, kwargs))
        return response(list(self.configurations))


class FakeComputeClient:
    def __init__(self, instances, events):
        self.instances = {instance.id: instance for instance in instances}
        self.etags = {instance.id: "etag-1" for instance in instances}
        self.events = events
        self.update_calls = []
        self.terminate_calls = []
        self.fail_update_status = None

    def get_instance(self, instance_id):
        if instance_id not in self.instances:
            error = RuntimeError("not found")
            error.status = 404
            raise error
        return response(self.instances[instance_id], etag=self.etags[instance_id])

    def list_instances(self, compartment_id, **kwargs):
        if compartment_id != COMPARTMENT_ID:
            raise AssertionError("backend escaped its fixed compartment")
        return response(list(self.instances.values()))

    def update_instance(self, instance_id, details, if_match=None):
        if self.fail_update_status:
            error = RuntimeError("injected update failure")
            error.status = self.fail_update_status
            raise error
        if if_match != self.etags[instance_id]:
            error = RuntimeError("stale")
            error.status = 412
            raise error
        tags = dict(details.freeform_tags)
        self.instances[instance_id].freeform_tags = tags
        self.etags[instance_id] = "etag-2"
        self.events.append(("tag", instance_id))
        self.update_calls.append((instance_id, details, if_match))
        return response(self.instances[instance_id], etag=self.etags[instance_id])

    def terminate_instance(self, instance_id, preserve_boot_volume=None, if_match=None):
        if if_match != self.etags[instance_id]:
            error = RuntimeError("stale")
            error.status = 412
            raise error
        self.events.append(("terminate", instance_id))
        self.terminate_calls.append((instance_id, preserve_boot_volume, if_match))
        self.instances[instance_id].lifecycle_state = "TERMINATING"
        return response(None, **{"opc-work-request-id": "wr-terminate"})


class FakePoolClient:
    def __init__(self, pool, members, events):
        self.pool = pool
        self.members = list(members)
        self.events = events
        self.pool_etag = "pool-etag-1"
        self.update_calls = []
        self.detach_calls = []
        self.extra_pool_summaries = []

    def get_instance_pool(self, pool_id):
        if pool_id != self.pool.id:
            error = RuntimeError("not found")
            error.status = 404
            raise error
        return response(self.pool, etag=self.pool_etag)

    def list_instance_pools(self, compartment_id, **kwargs):
        pools = [self.pool, *self.extra_pool_summaries]
        display_name = kwargs.get("display_name")
        if display_name:
            pools = [pool for pool in pools if pool.display_name == display_name]
        return response(pools)

    def list_instance_pool_instances(self, compartment_id, pool_id, **kwargs):
        if compartment_id != COMPARTMENT_ID or pool_id != self.pool.id:
            raise AssertionError("backend escaped its fixed scope")
        return response(list(self.members))

    def update_instance_pool(self, pool_id, details, if_match=None, opc_retry_token=None):
        if pool_id != self.pool.id or if_match != self.pool_etag:
            raise AssertionError("unexpected update scope or ETag")
        self.events.append(("scale_up", details.size))
        self.update_calls.append((pool_id, details, if_match, opc_retry_token))
        self.pool.size = details.size
        self.pool_etag = "pool-etag-2"
        return response(None, **{"opc-work-request-id": "wr-scale-up"})

    def detach_instance_pool_instance(self, pool_id, details, opc_retry_token=None):
        if pool_id != self.pool.id:
            raise AssertionError("unexpected detach pool")
        target = next((member for member in self.members if member.id == details.instance_id), None)
        if target is None:
            error = RuntimeError("not a member")
            error.status = 409
            raise error
        self.events.append(("detach", details.instance_id))
        self.detach_calls.append((pool_id, details, opc_retry_token))
        self.members.remove(target)
        if details.is_decrement_size:
            self.pool.size -= 1
        return response(None, **{"opc-work-request-id": "wr-detach"})


def base_env(role="control", *, by_name=False):
    values = {
        "COMPARTMENT_OCID": COMPARTMENT_ID,
        "HARNESS_ID": "harness-a",
        "HARNESS_TOKEN": "control-secret",
        "WORKER_TOKEN": "worker-secret",
        "FUNCTION_ROLE": role,
        "MAX_POOL_SIZE": "3",
        "TERMINATION_DELAY_SECONDS": "120",
        "DRY_RUN": "false",
        "ENABLE_TERMINATION": "true",
    }
    if by_name:
        values["TARGET_INSTANCE_POOL_NAME"] = "harness-pool"
    else:
        values["TARGET_INSTANCE_POOL_OCID"] = POOL_ID
    return values


def scale_env(role="control"):
    values = base_env(role)
    values["OBJECT_STORAGE_NAMESPACE"] = LEDGER_NAMESPACE
    values["REQUEST_LEDGER_BUCKET"] = LEDGER_BUCKET
    values["SCALE_TEST_PROFILES_JSON"] = json.dumps(
        {
            "small": {
                "poolName": "harness-scale-small",
                "displayName": "Small",
                "dnanexusType": "oci:mem1_ssd1_v3i_x4",
                "ociShape": "VM.Optimized3.Flex",
                "ocpus": 2,
                "memoryGbs": 8,
                "maxSize": 3,
            }
        }
    )
    return values


def make_clients(size=2):
    events = []
    old = obj(
        id=OLD_ID,
        compartment_id=COMPARTMENT_ID,
        display_name="old-worker",
        lifecycle_state="RUNNING",
        freeform_tags={func.PROTECTION_TAG: "1", "CustomerTag": "keep-me"},
    )
    new = obj(
        id=NEW_ID,
        compartment_id=COMPARTMENT_ID,
        display_name="new-worker",
        lifecycle_state="RUNNING",
        freeform_tags={func.PROTECTION_TAG: "1", "CustomerTag": "keep-me-too"},
    )
    all_instances = [old, new]
    members = [
        obj(
            id=OLD_ID,
            display_name="old-worker",
            state="RUNNING",
            time_created="2026-09-04T10:00:00Z",
            availability_domain="AD-1",
            fault_domain="FAULT-DOMAIN-1",
        ),
        obj(
            id=NEW_ID,
            display_name="new-worker",
            state="RUNNING",
            time_created="2026-09-04T11:00:00Z",
            availability_domain="AD-1",
            fault_domain="FAULT-DOMAIN-1",
        ),
    ][:size]
    pool = obj(
        id=POOL_ID,
        compartment_id=COMPARTMENT_ID,
        display_name="harness-pool",
        lifecycle_state="RUNNING",
        size=size,
        freeform_tags={func.HARNESS_TAG: "harness-a"},
    )
    pool_client = FakePoolClient(pool, members, events)
    compute_client = FakeComputeClient(all_instances, events)
    return func.Clients(compute=compute_client, pools=pool_client), events


class ScalePoolClient:
    def __init__(
        self,
        pool,
        members,
        events,
        instance_configuration,
        *,
        update_failures=None,
        detach_failures=None,
    ):
        self.pool = pool
        self.members = list(members)
        self.events = events
        self.instance_configuration = instance_configuration
        self.etag = "scale-pool-etag-1"
        self.update_calls = []
        self.detach_calls = []
        self.update_failures = list(update_failures or [])
        self.detach_failures = list(detach_failures or [])
        self.detach_attempts = []
        self.compute = None
        self.defer_termination = False
        self.after_detach = None

    def list_instance_pools(self, compartment_id, **kwargs):
        if compartment_id != COMPARTMENT_ID:
            raise AssertionError("backend escaped its fixed compartment")
        pools = [self.pool]
        if kwargs.get("display_name"):
            pools = [item for item in pools if item.display_name == kwargs["display_name"]]
        return response(pools)

    def get_instance_pool(self, pool_id):
        if pool_id != self.pool.id:
            error = RuntimeError("not found")
            error.status = 404
            raise error
        return response(self.pool, etag=self.etag)

    def get_instance_configuration(self, instance_configuration_id):
        if instance_configuration_id != self.instance_configuration.id:
            raise fake_service_error(404, "NotAuthorizedOrNotFound")
        return response(self.instance_configuration, etag="configuration-etag-1")

    def list_instance_pool_instances(self, compartment_id, pool_id, **kwargs):
        if compartment_id != COMPARTMENT_ID or pool_id != self.pool.id:
            raise AssertionError("backend escaped its fixed scale-test scope")
        return response(list(self.members))

    def update_instance_pool(self, pool_id, details, if_match=None, opc_retry_token=None):
        if pool_id != self.pool.id or if_match != self.etag:
            raise AssertionError("unexpected scale-test pool scope or ETag")
        self.events.append(("scale_test_resize", details.size))
        self.update_calls.append((pool_id, details, if_match, opc_retry_token))
        if self.update_failures:
            raise self.update_failures.pop(0)
        self.pool.size = details.size
        self.pool.lifecycle_state = "SCALING"
        self.etag = "scale-pool-etag-2"
        return response(
            None,
            **{
                "opc-request-id": "opc-scale-test",
                "opc-work-request-id": "wr-scale-test",
            },
        )

    def detach_instance_pool_instance(self, pool_id, details, opc_retry_token=None):
        if pool_id != self.pool.id:
            raise AssertionError("unexpected scale-test detach pool")
        self.detach_attempts.append((details.instance_id, opc_retry_token))
        if self.detach_failures:
            raise self.detach_failures.pop(0)
        target = next((member for member in self.members if member.id == details.instance_id), None)
        if target is None:
            raise fake_service_error(409, "IncorrectState")
        self.events.append(("scale_test_detach", details.instance_id))
        self.detach_calls.append((pool_id, details, opc_retry_token))
        self.members.remove(target)
        if details.is_decrement_size:
            self.pool.size -= 1
            self.pool.current_size = max(0, self.pool.current_size - 1)
        if details.is_auto_terminate and self.compute is not None:
            self.compute.instances[details.instance_id].lifecycle_state = (
                "TERMINATING" if self.defer_termination else "TERMINATED"
            )
        if self.after_detach is not None:
            self.after_detach(self, details.instance_id)
        return response(
            None,
            **{"opc-work-request-id": f"wr-detach-{details.instance_id.rsplit('.', 1)[-1]}"},
        )


def make_scale_clients(
    *,
    size=0,
    lifecycle_state="RUNNING",
    ready=False,
    protections=None,
    attached_autoscaler=False,
    configuration_matches=True,
    update_failures=None,
    detach_failures=None,
):
    events = []
    protections = protections or {}
    instance_specs = [
        (SCALE_INSTANCE_ID, "scale-small-1", "2026-09-04T11:57:00Z"),
        (SCALE_MIDDLE_INSTANCE_ID, "scale-small-2", "2026-09-04T11:58:00Z"),
        (SCALE_NEWEST_INSTANCE_ID, "scale-small-3", "2026-09-04T11:59:00Z"),
    ]
    instances = []
    members = []
    for index, (instance_id, display_name, created_at) in enumerate(instance_specs):
        tags = {
            func.HARNESS_TAG: "harness-a",
            func.SCALE_TEST_PROFILE_TAG: "small",
            func.PROTECTION_TAG: protections.get(instance_id, "1"),
        }
        if ready and index == 0:
            tags[func.SCALE_TEST_READY_AT_TAG] = "2026-09-04T12:00:00Z"
        instances.append(
            obj(
                id=instance_id,
                compartment_id=COMPARTMENT_ID,
                display_name=display_name,
                lifecycle_state="RUNNING",
                time_created=created_at,
                shape="VM.Optimized3.Flex",
                shape_config=obj(ocpus=2, memory_in_gbs=8),
                freeform_tags=tags,
            )
        )
        if index < size:
            members.append(
                obj(
                    id=instance_id,
                    display_name=display_name,
                    state="RUNNING",
                    time_created=created_at,
                    availability_domain="AD-1",
                    fault_domain="FAULT-DOMAIN-1",
                )
            )
    configuration_tags = {
        func.HARNESS_TAG: "harness-a",
        func.SCALE_TEST_PROFILE_TAG: "small",
    }
    launch_tags = dict(configuration_tags)
    instance_configuration = obj(
        id=SCALE_CONFIGURATION_ID,
        compartment_id=COMPARTMENT_ID,
        freeform_tags=configuration_tags,
        instance_details=obj(
            launch_details=obj(
                shape="VM.Optimized3.Flex",
                shape_config=obj(ocpus=2 if configuration_matches else 4, memory_in_gbs=8),
                freeform_tags=launch_tags,
            )
        ),
    )
    pool = obj(
        id=SCALE_POOL_ID,
        compartment_id=COMPARTMENT_ID,
        display_name="harness-scale-small",
        lifecycle_state=lifecycle_state,
        size=size,
        current_size=size,
        instance_configuration_id=SCALE_CONFIGURATION_ID,
        freeform_tags={func.HARNESS_TAG: "harness-a", func.SCALE_TEST_PROFILE_TAG: "small"},
    )
    pool_client = ScalePoolClient(
        pool,
        members,
        events,
        instance_configuration,
        update_failures=update_failures,
        detach_failures=detach_failures,
    )
    compute_client = FakeComputeClient(instances, events)
    pool_client.compute = compute_client
    objects = FakeObjectStorage()
    autoscaling_configurations = []
    if attached_autoscaler:
        autoscaling_configurations.append(
            obj(
                id="ocid1.autoscalingconfig.oc1.iad.config123",
                is_enabled=False,
                resource=obj(type="instancePool", id=SCALE_POOL_ID),
            )
        )
    autoscaling = FakeAutoscalingClient(autoscaling_configurations)
    return func.Clients(
        compute=compute_client,
        pools=pool_client,
        objects=objects,
        autoscaling=autoscaling,
    ), events


def invoke(payload, clients, *, role="control", now=NOW, token=None, env=None, bearer=False, clock=None):
    actual_env = env or base_env(role)
    supplied = token or ("worker-secret" if role in {"terminator", "readiness"} else "control-secret")
    headers = {"Authorization": f"Bearer {supplied}"} if bearer else {"X-Harness-Token": supplied}
    return func.handle_request(
        payload,
        headers=headers,
        clients=clients,
        clock=clock or (lambda: now),
        environ=actual_env,
    )
