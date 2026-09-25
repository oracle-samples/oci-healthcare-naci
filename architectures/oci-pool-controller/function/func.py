"""OCI Function backend for instance-pool scaling and exact worker reclaim.

The production-shaped path accepts an *absolute* desired size, not a relative
``+1``/``-1`` instruction. Each request is durably recorded in Object Storage,
and an increasing per-pool generation makes newer scheduler intent supersede
older demand intent. Retirement is different: once committed for an instance,
it survives every later demand generation. Request IDs and OCI retry tokens
remain stable across retries of the same operation.

Scale-out uses OCI's normal ``UpdateInstancePool(size=desired)`` API. Scale-in
intentionally never uses that API: a smaller generic size would allow OCI to
choose the termination victim. Instead, the reconciler detaches only explicitly
drained workers whose exact protection tag is the string ``"0"``. Each detach
decrements the pool. The default policy auto-terminates that exact instance;
the opt-in bounded-growth preview terminates it separately.

Desired size means non-retiring capacity, not all attached instances. The
bounded replacement policy is retire-first: finish committed retirements before
launching toward the latest demand, without temporarily requesting surge size.
ENABLE_BOUNDED_GROWTH opts into a preview protocol with durable launch
reservations and bounded growth while retirement continues. Live OCI timing
and scheduler acceptance remain deployment prerequisites.

One container image serves three least-privilege Function roles:

* ``control`` exposes status, tag, and desired-state operations;
* ``terminator`` supports the optional worker self-reclaim demonstration; and
* ``readiness`` records a benchmark worker's bootstrap-ready marker.

The browser is only a test client. Your platform can call the same control actions
directly from its scheduler. Legacy direct-resize actions remain rejected so
all benchmark mutations pass through the durable, fenced reconciler.
"""

from __future__ import annotations

import hmac
import hashlib
import io
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, MutableMapping, Sequence

try:  # The deployment image supplies the OCI SDK; unit tests use fakes.
    import oci  # type: ignore
except ImportError:  # pragma: no cover - depends on the local environment
    oci = None  # type: ignore

try:  # The deployment image supplies the Functions Development Kit.
    from fdk import response as fdk_response  # type: ignore
except ImportError:  # pragma: no cover - depends on the local environment
    fdk_response = None


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

HARD_MAX_POOL_SIZE = 3
DEFAULT_MAX_POOL_SIZE = 3
DEFAULT_TERMINATION_DELAY_SECONDS = 120
HARD_MAX_SCALE_TEST_POOL_SIZE = 50
MAX_SCALE_TEST_PROFILES = 6
HARD_MAX_ACTIVE_SCALE_TEST_PROFILES = 3
DEFAULT_MAX_ACTIVE_SCALE_TEST_PROFILES = 2
# The full Intel benchmark deliberately runs Small 20 (40 OCPUs), Medium 15
# (120 OCPUs), and Large 10 (160 OCPUs) at the same time.  Keep the aggregate
# ceiling equal to that explicit 320-OCPU campaign so no additional capacity
# can be requested accidentally through the benchmark profiles.
HARD_MAX_SCALE_TEST_TOTAL_OCPUS = 320
DEFAULT_MAX_SCALE_TEST_TOTAL_OCPUS = 128
OCI_MAX_FREEFORM_TAGS = 10
DEFAULT_REQUEST_LEASE_SECONDS = 60
DEFAULT_MUTATION_LEASE_SECONDS = 180
MAX_EXCLUDED_INSTANCES = 100
SCALE_TEST_BUDGET_LOCK_OBJECT = "coordination/scale-test-budget.json"

PROTECTION_TAG = "InstanceTerminationProtectionEnabled"
HARNESS_TAG = "HarnessId"
ORIGIN_POOL_TAG = "OriginPoolId"
OPERATION_TAG = "DrainOperationId"
REQUESTED_AT_TAG = "DrainRequestedAt"
SCALE_TEST_PROFILE_TAG = "ScaleTestProfile"
SCALE_TEST_READY_AT_TAG = "ScaleTestReadyAt"
# Instance-pool membership currently contributes three OCI-managed freeform
# tags. OCI permits ten total, so discard these cosmetic harness tags before
# adding the safety-critical drain provenance fields.
DRAIN_PRUNABLE_TAGS = ("ManagedBy", "Purpose")

ACTION_ALIASES = {
    "pool_status": "scale_test_status",
    "status": "status",
    "scale": "scale_pool",
    "scale_pool": "scale_pool",
    "set_termination_flag": "set_protection",
    "set_protection": "set_protection",
    "reset": "reset_protection",
    "reset_protection": "reset_protection",
    "targeted_scale_in": "drain_and_scale",
    "drain_and_scale": "drain_and_scale",
    "request_termination": "terminate_if_ready",
    "terminate_if_ready": "terminate_if_ready",
    "scale_test_status": "scale_test_status",
    "scale_test_resize": "scale_test_resize",
    "scale_test_cleanup": "scale_test_cleanup",
    "simulate_scale_burst": "simulate_scale_burst",
    "report_scale_test_ready": "report_scale_test_ready",
    "reconcile_pool": "reconcile_pool",
    "request_status": "request_status",
    "set_pool_protection": "set_pool_protection",
}
CONTROL_ACTIONS = {
    "status",
    "scale_pool",
    "set_protection",
    "reset_protection",
    "drain_and_scale",
    "scale_test_status",
    "scale_test_resize",
    "scale_test_cleanup",
    "simulate_scale_burst",
    "reconcile_pool",
    "request_status",
    "set_pool_protection",
}
TERMINATOR_ACTIONS = {"terminate_if_ready"}
READINESS_ACTIONS = {"report_scale_test_ready"}
WORKER_ACTIONS = {"terminate_if_ready", "report_scale_test_ready"}
ACTIVE_POOL_STATES = {"PROVISIONING", "RUNNING", "SCALING", "STARTING", "STOPPING", "STOPPED"}

OCID_RE = re.compile(r"^ocid1\.[a-z][a-z0-9-]*\.[A-Za-z0-9._-]+$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[1-5][0-9A-Fa-f]{3}-[89ABab][0-9A-Fa-f]{3}-[0-9A-Fa-f]{12}$")


class HarnessError(Exception):
    """An expected request/configuration error with an HTTP representation."""

    def __init__(self, status: int, reason: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ScaleTestProfile:
    """Allowlisted OCI pool with a worker-class label and capacity limits."""

    key: str
    pool_name: str
    display_name: str
    worker_type: str
    oci_shape: str
    ocpus: int
    memory_gbs: int
    max_size: int
    pool_id: str | None = None


@dataclass(frozen=True)
class Config:
    """Validated Function configuration derived from deployment variables."""

    configured_pool_id: str | None
    pool_name: str | None
    compartment_id: str
    harness_id: str
    harness_token: str
    worker_token: str
    function_role: str
    max_pool_size: int
    termination_delay_seconds: int
    dry_run: bool
    enable_termination: bool
    scale_test_profiles: Mapping[str, ScaleTestProfile]
    scale_test_budget_limits_enabled: bool
    max_active_scale_test_profiles: int
    max_scale_test_total_ocpus: int
    ledger_namespace: str | None
    ledger_bucket: str | None
    controller_only: bool = False
    auth_mode: str = "bearer"
    max_profiles: int = MAX_SCALE_TEST_PROFILES
    max_instances_per_profile: int = HARD_MAX_SCALE_TEST_POOL_SIZE
    allow_empty_pool_registry: bool = False
    bounded_growth: bool = False
    max_total_vms: int = 25
    launch_timeout_seconds: int = 900


@dataclass(frozen=True)
class Clients:
    """OCI SDK clients grouped for dependency injection in unit tests."""

    compute: Any
    pools: Any
    objects: Any | None = None
    autoscaling: Any | None = None
    work_requests: Any | None = None


@dataclass(frozen=True)
class Scope:
    """Authoritatively resolved pool plus the ETag used for guarded writes."""

    config: Config
    pool_id: str
    pool: Any
    pool_etag: str


@dataclass(frozen=True)
class PoolMutationLease:
    """Snapshot proving which request currently owns a per-pool OCI mutation."""

    object_name: str
    request_id: str
    attempt_id: str
    generation: int
    fence: int
    etag: str


def _mapping_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_headers(value: Any) -> Mapping[str, Any]:
    headers = _mapping_value(value, "headers", {})
    return headers if isinstance(headers, Mapping) else {}


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted and isinstance(value, str):
            return value
    return None


def _work_request_id(response: Any) -> str | None:
    return _header(_response_headers(response), "opc-work-request-id")


def _opc_request_id(response: Any) -> str | None:
    return _header(_response_headers(response), "opc-request-id")


def _etag(response: Any) -> str:
    etag = _header(_response_headers(response), "etag")
    if not etag:
        raise HarnessError(502, "missing_etag", "OCI response did not include an ETag")
    return etag


def _require_ledger(config: Config, clients: Clients) -> tuple[str, str, Any]:
    if not config.ledger_namespace or not config.ledger_bucket or clients.objects is None:
        raise HarnessError(503, "request_ledger_unavailable", "the durable request ledger is not configured")
    return config.ledger_namespace, config.ledger_bucket, clients.objects


def _decode_object(response: Any) -> Mapping[str, Any]:
    data = _mapping_value(response, "data")
    raw = _mapping_value(data, "content")
    if raw is None and hasattr(data, "read"):
        raw = data.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise HarnessError(502, "invalid_ledger_record", "the request ledger returned an invalid object body")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HarnessError(502, "invalid_ledger_record", "the request ledger contains invalid JSON") from error
    if not isinstance(value, Mapping):
        raise HarnessError(502, "invalid_ledger_record", "the request ledger record must be a JSON object")
    return value


def _get_ledger_record(
    config: Config,
    clients: Clients,
    object_name: str,
) -> tuple[Mapping[str, Any], str]:
    """Read one durable JSON record and return it with its current OCI ETag."""

    namespace, bucket, objects = _require_ledger(config, clients)
    response = objects.get_object(namespace, bucket, object_name)
    return _decode_object(response), _etag(response)


def _put_ledger_record(
    config: Config,
    clients: Clients,
    object_name: str,
    value: Mapping[str, Any],
    *,
    if_match: str | None = None,
    if_none_match: str | None = None,
) -> str:
    """Write canonical JSON using optional Object Storage compare-and-swap.

    ``if_none_match="*"`` creates a record exactly once. ``if_match`` updates
    only the version previously read. Those two conditions are the controller's
    durable concurrency primitive; an unguarded ledger overwrite is never used.
    """

    namespace, bucket, objects = _require_ledger(config, clients)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    kwargs: dict[str, Any] = {
        "content_length": len(encoded),
        "content_type": "application/json",
    }
    if if_match is not None:
        kwargs["if_match"] = if_match
    if if_none_match is not None:
        kwargs["if_none_match"] = if_none_match
    response = objects.put_object(namespace, bucket, object_name, io.BytesIO(encoded), **kwargs)
    return _etag(response)


def _acquire_scale_test_budget_lock(
    config: Config,
    clients: Clients,
    request_id: str,
    now: datetime,
) -> str:
    """Serialize the cross-pool capacity check and its following OCI mutation.

    Per-pool leases prevent two writes to one pool. This separate short-lived
    lease closes the race where two different pools could each pass the global
    active-profile/OCPU calculation and jointly exceed the configured budget.
    """

    for _ in range(3):
        record, etag = _get_ledger_record(config, clients, SCALE_TEST_BUDGET_LOCK_OBJECT)
        owner = record.get("owner")
        lease_until_raw = record.get("leaseUntil")
        lease_until = None
        if isinstance(lease_until_raw, str):
            try:
                lease_until = datetime.fromisoformat(lease_until_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                lease_until = None
        if owner is not None and lease_until is None:
            raise HarnessError(
                502,
                "invalid_ledger_record",
                "the aggregate capacity lease has an owner but no valid expiry",
            )
        if owner is not None and lease_until is not None and lease_until > now:
            raise HarnessError(
                409,
                "scale_test_budget_busy",
                "another scale-test mutation holds the aggregate capacity lease",
                retryable=True,
            )
        claimed = {
            "owner": request_id,
            "leaseUntil": _timestamp(now + timedelta(seconds=DEFAULT_MUTATION_LEASE_SECONDS)),
        }
        try:
            return _put_ledger_record(
                config,
                clients,
                SCALE_TEST_BUDGET_LOCK_OBJECT,
                claimed,
                if_match=etag,
            )
        except Exception as error:
            if getattr(error, "status", None) != 412:
                raise
    raise HarnessError(
        409,
        "scale_test_budget_busy",
        "the aggregate capacity lease changed concurrently; retry with jitter",
        retryable=True,
    )


def _release_scale_test_budget_lock(
    config: Config,
    clients: Clients,
    request_id: str,
    etag: str,
) -> None:
    """Release the aggregate budget lease only if its ETag is still ours."""

    try:
        _put_ledger_record(
            config,
            clients,
            SCALE_TEST_BUDGET_LOCK_OBJECT,
            {"owner": None, "leaseUntil": None, "lastOwner": request_id},
            if_match=etag,
        )
    except Exception:
        # A stale lease is safe and expires quickly; never hide the primary
        # mutation result because cleanup of the coordination record failed.
        LOG.exception("Failed to release scale-test aggregate capacity lease")


def _pool_mutation_lock_object(profile: ScaleTestProfile) -> str:
    # Queue ownership and the mutation lease intentionally share one object.
    # A single Object Storage ETag therefore interlocks a new desired state
    # with the invocation that is about to send an OCI mutation.
    return _pool_queue_object_name(profile.key)


def _lease_expiry(record: Mapping[str, Any]) -> datetime | None:
    raw = record.get("leaseUntil")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _acquire_pool_mutation_lease(
    config: Config,
    clients: Clients,
    profile: ScaleTestProfile,
    request_id: str,
    attempt_id: str,
    generation: int,
    desired: int,
    now: datetime,
) -> PoolMutationLease:
    """Claim exclusive permission to issue the next OCI mutation for a pool.

    The queue record must still name this exact request, generation, and target.
    Claiming increments a fencing number and uses the queue object's ETag as a
    compare-and-swap. Once a lease is released or expires, its fencing value
    prevents an older invocation from writing under superseded ownership.
    """

    object_name = _pool_mutation_lock_object(profile)
    for _ in range(5):
        record, etag = _get_ledger_record(config, clients, object_name)

        owner_attempt = record.get("ownerAttemptId")
        owner_request = record.get("ownerRequestId")
        if record.get("poolKey") != profile.key:
            raise HarnessError(502, "invalid_ledger_record", "the per-pool mutation lease has the wrong pool key")
        if (owner_attempt is None) != (owner_request is None):
            raise HarnessError(502, "invalid_ledger_record", "the per-pool mutation lease owner is incomplete")
        if owner_request is not None and (
            not isinstance(owner_request, str)
            or not UUID_RE.fullmatch(owner_request)
            or not isinstance(owner_attempt, str)
            or not UUID_RE.fullmatch(owner_attempt)
        ):
            raise HarnessError(502, "invalid_ledger_record", "the per-pool mutation lease owner is invalid")
        record_generation = record.get("generation")
        if (
            isinstance(record_generation, bool)
            or not isinstance(record_generation, int)
            or record_generation < 1
        ):
            raise HarnessError(502, "invalid_ledger_record", "the per-pool mutation lease generation is invalid")
        if (
            record_generation != generation
            or record.get("latestRequestId") != request_id
            or record.get("desiredSize") != desired
        ):
            raise HarnessError(
                409,
                "request_superseded",
                "a newer desired state won before this request acquired the mutation lease",
            )
        expiry = _lease_expiry(record)
        if (owner_attempt is not None or owner_request is not None) and expiry is None:
            raise HarnessError(
                502,
                "invalid_ledger_record",
                "the per-pool mutation lease has an owner but no valid expiry",
            )
        if (owner_attempt is not None or owner_request is not None) and expiry is not None and expiry > now:
            raise HarnessError(
                409,
                "pool_mutation_busy",
                "another request holds the per-pool mutation lease; replay this request after backoff",
                retryable=True,
            )
        fence = record.get("fence", 0)
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise HarnessError(502, "invalid_ledger_record", "the per-pool mutation fence is invalid")
        claimed = dict(record)
        claimed.update({
            "ownerRequestId": request_id,
            "ownerAttemptId": attempt_id,
            "fence": fence + 1,
            "leaseUntil": _timestamp(now + timedelta(seconds=DEFAULT_MUTATION_LEASE_SECONDS)),
            "mutationUpdatedAt": _timestamp(now),
        })
        try:
            next_etag = _put_ledger_record(config, clients, object_name, claimed, if_match=etag)
            return PoolMutationLease(object_name, request_id, attempt_id, generation, fence + 1, next_etag)
        except Exception as error:
            if getattr(error, "status", None) != 412:
                raise
    raise HarnessError(
        409,
        "pool_mutation_busy",
        "the per-pool mutation lease changed concurrently; replay this request with jitter",
        retryable=True,
    )


def _assert_pool_mutation_fence(
    config: Config,
    clients: Clients,
    lease: PoolMutationLease,
    now: datetime,
) -> None:
    """Fail closed unless the lease is unchanged, unexpired, and still ours.

    This check is deliberately performed immediately before every OCI write.
    Holding a Python object is not sufficient: another invocation may have
    replaced an expired lease or registered a newer desired generation.
    """

    record, etag = _get_ledger_record(config, clients, lease.object_name)
    expiry = _lease_expiry(record)
    if (
        record.get("ownerRequestId") != lease.request_id
        or record.get("ownerAttemptId") != lease.attempt_id
        or record.get("generation") != lease.generation
        or record.get("fence") != lease.fence
        or etag != lease.etag
        or expiry is None
        or expiry <= now
    ):
        raise HarnessError(
            409,
            "pool_mutation_lease_lost",
            "the per-pool mutation lease changed; no OCI mutation was attempted",
            retryable=True,
        )


def _release_pool_mutation_lease(
    config: Config,
    clients: Clients,
    lease: PoolMutationLease,
) -> None:
    """Best-effort release that cannot erase a successor invocation's lease."""

    try:
        record, etag = _get_ledger_record(config, clients, lease.object_name)
        if (
            etag != lease.etag
            or record.get("ownerRequestId") != lease.request_id
            or record.get("ownerAttemptId") != lease.attempt_id
            or record.get("generation") != lease.generation
            or record.get("fence") != lease.fence
        ):
            return
        released = dict(record)
        released.update(
            {
                "ownerRequestId": None,
                "ownerAttemptId": None,
                "leaseUntil": None,
                "lastOwnerRequestId": lease.request_id,
                "lastOwnerAttemptId": lease.attempt_id,
            }
        )
        _put_ledger_record(
            config,
            clients,
            lease.object_name,
            released,
            if_match=etag,
        )
    except Exception:
        # An expired/stolen lease is fenced by its monotonically increasing
        # value. Never hide the primary reconciliation outcome during cleanup.
        LOG.exception("Failed to release per-pool mutation lease")


def _model(name: str, **values: Any) -> Any:
    if oci is not None:
        model = getattr(oci.core.models, name)
        return model(**values)
    return SimpleNamespace(**values)


def _parse_nonnegative_int(raw: Any, name: str) -> int:
    if isinstance(raw, bool):
        raise HarnessError(500, "invalid_configuration", f"{name} must be an integer")
    if isinstance(raw, int):
        parsed = raw
    elif isinstance(raw, str) and raw.isdigit():
        parsed = int(raw)
    else:
        raise HarnessError(500, "invalid_configuration", f"{name} must be an integer")
    if parsed < 0:
        raise HarnessError(500, "invalid_configuration", f"{name} cannot be negative")
    return parsed


def _parse_bool(raw: Any, name: str, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    raise HarnessError(500, "invalid_configuration", f"{name} must be true or false")


def _parse_scale_test_profiles(
    raw: Any, *, max_profiles: int = MAX_SCALE_TEST_PROFILES,
    max_pool_size: int = HARD_MAX_SCALE_TEST_POOL_SIZE, require_pool_ids: bool = False,
) -> Mapping[str, ScaleTestProfile]:
    """Parse the server-owned pool allowlist and enforce shape/cost ceilings."""

    if raw in (None, ""):
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HarnessError(
            500,
            "invalid_configuration",
            "SCALE_TEST_PROFILES_JSON must be valid JSON",
        ) from error
    if not isinstance(value, Mapping) or len(value) > max_profiles:
        raise HarnessError(
            500,
            "invalid_configuration",
            f"SCALE_TEST_PROFILES_JSON must contain at most {max_profiles} profiles",
        )

    profiles: dict[str, ScaleTestProfile] = {}
    allowed_shapes = {"VM.Standard3.Flex", "VM.Optimized3.Flex"}
    for raw_key, raw_profile in value.items():
        if not isinstance(raw_key, str) or not REQUEST_ID_RE.fullmatch(raw_key):
            raise HarnessError(500, "invalid_configuration", "scale-test profile key is invalid")
        if not isinstance(raw_profile, Mapping):
            raise HarnessError(500, "invalid_configuration", f"scale-test profile {raw_key} must be an object")

        def profile_text(field: str, *, fallback: str | None = None) -> str:
            candidate = raw_profile.get(field, fallback)
            if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 255:
                raise HarnessError(
                    500,
                    "invalid_configuration",
                    f"scale-test profile {raw_key}.{field} is invalid",
                )
            return candidate.strip()

        pool_name = profile_text("poolName")
        pool_id = raw_profile.get("poolId")
        if require_pool_ids or pool_id is not None:
            pool_id = _validate_ocid(pool_id, "instancepool", f"profile {raw_key}.poolId", status=500)
        if pool_id and any(item.pool_id == pool_id for item in profiles.values()):
            raise HarnessError(500, "invalid_configuration", "poolId must be unique across profiles")
        display_name = profile_text("displayName", fallback=raw_key)
        worker_type = profile_text("workerType")
        oci_shape = profile_text("ociShape")
        if oci_shape not in allowed_shapes:
            raise HarnessError(
                500,
                "invalid_configuration",
                f"scale-test profile {raw_key}.ociShape must be an approved Intel Flex shape",
            )
        ocpus = _parse_nonnegative_int(raw_profile.get("ocpus"), f"scale-test profile {raw_key}.ocpus")
        memory_gbs = _parse_nonnegative_int(
            raw_profile.get("memoryGbs"),
            f"scale-test profile {raw_key}.memoryGbs",
        )
        max_size = _parse_nonnegative_int(
            raw_profile.get("maxSize", max_pool_size),
            f"scale-test profile {raw_key}.maxSize",
        )
        if (
            not 1 <= ocpus <= 32
            or (oci_shape == "VM.Optimized3.Flex" and ocpus > 18)
            or not 1 <= memory_gbs <= 512
            or (oci_shape == "VM.Optimized3.Flex" and memory_gbs > 256)
            or memory_gbs < ocpus
            or memory_gbs > ocpus * 64
        ):
            raise HarnessError(
                500,
                "invalid_configuration",
                f"scale-test profile {raw_key} has an unsafe shape configuration",
            )
        if not 1 <= max_size <= max_pool_size:
            raise HarnessError(
                500,
                "invalid_configuration",
                f"scale-test profile {raw_key}.maxSize must be between 1 and {max_pool_size}",
            )
        profiles[raw_key] = ScaleTestProfile(
            key=raw_key,
            pool_name=pool_name,
            display_name=display_name,
            worker_type=worker_type,
            oci_shape=oci_shape,
            ocpus=ocpus,
            memory_gbs=memory_gbs,
            max_size=max_size,
            pool_id=pool_id,
        )
    return profiles


def _validate_ocid(value: Any, kind: str, field: str, *, status: int = 400) -> str:
    if not isinstance(value, str) or not OCID_RE.fullmatch(value) or not value.startswith(f"ocid1.{kind}."):
        raise HarnessError(status, "invalid_ocid", f"{field} must be an OCI {kind} OCID")
    return value


def _config(environ: Mapping[str, str]) -> Config:
    """Build a fail-closed configuration shared by all Function roles.

    Client input cannot expand the compartment, pool allowlist, shape limits,
    or Function role. Invalid or partially supplied deployment settings stop
    the request before an OCI client can mutate anything.
    """

    compartment_id = environ.get("TARGET_COMPARTMENT_OCID") or environ.get("COMPARTMENT_OCID") or ""
    harness_id = environ.get("HARNESS_ID", "")
    harness_token = environ.get("HARNESS_TOKEN", "")
    function_role = environ.get("FUNCTION_ROLE", "")
    controller_only = _parse_bool(environ.get("CONTROLLER_ONLY"), "CONTROLLER_ONLY", default=False)
    allow_empty_pool_registry = _parse_bool(
        environ.get("ALLOW_EMPTY_POOL_REGISTRY"), "ALLOW_EMPTY_POOL_REGISTRY", default=False,
    )
    auth_mode = environ.get("AUTH_MODE", "bearer")
    if auth_mode not in {"bearer", "oci_iam"} or (
        auth_mode == "oci_iam" and (not controller_only or function_role != "control")
    ):
        raise HarnessError(500, "invalid_configuration", "oci_iam requires CONTROLLER_ONLY=true and FUNCTION_ROLE=control")
    if controller_only and function_role != "control":
        raise HarnessError(500, "invalid_configuration", "controller-only deployments require the control role")
    if allow_empty_pool_registry and (not controller_only or auth_mode != "oci_iam"):
        raise HarnessError(500, "invalid_configuration", "ALLOW_EMPTY_POOL_REGISTRY requires controller-only OCI IAM authentication")
    missing = [
        name
        for name, value in (
            ("COMPARTMENT_OCID", compartment_id),
            ("HARNESS_ID", harness_id),
            ("HARNESS_TOKEN", harness_token if auth_mode == "bearer" else "not-used"),
            ("FUNCTION_ROLE", function_role),
        )
        if not value
    ]
    if missing:
        raise HarnessError(500, "invalid_configuration", f"missing configuration: {', '.join(missing)}")

    _validate_ocid(compartment_id, "compartment", "COMPARTMENT_OCID", status=500)
    configured_pool_id = environ.get("TARGET_INSTANCE_POOL_OCID") or None
    pool_name = environ.get("TARGET_INSTANCE_POOL_NAME") or None
    if configured_pool_id:
        _validate_ocid(configured_pool_id, "instancepool", "TARGET_INSTANCE_POOL_OCID", status=500)
    elif not pool_name and not controller_only:
        raise HarnessError(
            500,
            "invalid_configuration",
            "TARGET_INSTANCE_POOL_OCID or TARGET_INSTANCE_POOL_NAME is required",
        )
    if pool_name is not None and (not pool_name.strip() or len(pool_name) > 255):
        raise HarnessError(500, "invalid_configuration", "TARGET_INSTANCE_POOL_NAME is invalid")
    if not REQUEST_ID_RE.fullmatch(harness_id):
        raise HarnessError(500, "invalid_configuration", "HARNESS_ID has an invalid format")
    if function_role not in {"control", "terminator", "readiness"}:
        raise HarnessError(500, "invalid_configuration", "FUNCTION_ROLE must be control, terminator, or readiness")
    flag_tag_key = environ.get("FLAG_TAG_KEY")
    if flag_tag_key is not None and flag_tag_key != PROTECTION_TAG:
        raise HarnessError(500, "invalid_configuration", f"FLAG_TAG_KEY must be {PROTECTION_TAG}")

    max_pool_size = _parse_nonnegative_int(environ.get("MAX_POOL_SIZE", DEFAULT_MAX_POOL_SIZE), "MAX_POOL_SIZE")
    if max_pool_size < 1 or max_pool_size > HARD_MAX_POOL_SIZE:
        raise HarnessError(
            500,
            "invalid_configuration",
            f"MAX_POOL_SIZE must be between 1 and {HARD_MAX_POOL_SIZE}",
        )
    delay = _parse_nonnegative_int(
        environ.get("TERMINATION_DELAY_SECONDS", DEFAULT_TERMINATION_DELAY_SECONDS),
        "TERMINATION_DELAY_SECONDS",
    )
    if delay > 86400:
        raise HarnessError(500, "invalid_configuration", "TERMINATION_DELAY_SECONDS cannot exceed 86400")
    max_active_scale_test_profiles = _parse_nonnegative_int(
        environ.get("MAX_ACTIVE_SCALE_TEST_PROFILES", DEFAULT_MAX_ACTIVE_SCALE_TEST_PROFILES),
        "MAX_ACTIVE_SCALE_TEST_PROFILES",
    )
    if max_active_scale_test_profiles < 1 or (
        not controller_only and max_active_scale_test_profiles > HARD_MAX_ACTIVE_SCALE_TEST_PROFILES
    ):
        raise HarnessError(
            500,
            "invalid_configuration",
            f"MAX_ACTIVE_SCALE_TEST_PROFILES must be between 1 and {HARD_MAX_ACTIVE_SCALE_TEST_PROFILES}",
        )
    max_scale_test_total_ocpus = _parse_nonnegative_int(
        environ.get("MAX_SCALE_TEST_TOTAL_OCPUS", DEFAULT_MAX_SCALE_TEST_TOTAL_OCPUS),
        "MAX_SCALE_TEST_TOTAL_OCPUS",
    )
    if max_scale_test_total_ocpus < 1 or (
        not controller_only and max_scale_test_total_ocpus > HARD_MAX_SCALE_TEST_TOTAL_OCPUS
    ):
        raise HarnessError(
            500,
            "invalid_configuration",
            f"MAX_SCALE_TEST_TOTAL_OCPUS must be between 1 and {HARD_MAX_SCALE_TEST_TOTAL_OCPUS}",
        )
    ledger_namespace = environ.get("OBJECT_STORAGE_NAMESPACE") or None
    ledger_bucket = environ.get("REQUEST_LEDGER_BUCKET") or None
    if bool(ledger_namespace) != bool(ledger_bucket):
        raise HarnessError(
            500,
            "invalid_configuration",
            "OBJECT_STORAGE_NAMESPACE and REQUEST_LEDGER_BUCKET must be configured together",
        )
    if ledger_namespace is not None and (
        not REQUEST_ID_RE.fullmatch(ledger_namespace) or not REQUEST_ID_RE.fullmatch(ledger_bucket or "")
    ):
        raise HarnessError(500, "invalid_configuration", "request-ledger namespace or bucket is invalid")

    # Reference deployment ceilings are operator configuration, never caller
    # input. Raising them is not evidence of tested throughput or OCI capacity.
    max_profiles = MAX_SCALE_TEST_PROFILES
    max_instances = HARD_MAX_SCALE_TEST_POOL_SIZE
    if controller_only:
        max_profiles = _parse_nonnegative_int(environ.get("CONTROLLER_MAX_PROFILES", max_profiles), "CONTROLLER_MAX_PROFILES")
        max_instances = _parse_nonnegative_int(environ.get("CONTROLLER_MAX_POOL_SIZE", max_instances), "CONTROLLER_MAX_POOL_SIZE")
        if min(max_profiles, max_instances) < 1 or not ledger_namespace:
            raise HarnessError(500, "invalid_configuration", "controller-only mode requires positive ceilings and a durable ledger")
    raw_profiles = environ.get("SCALE_TEST_PROFILES_JSON")
    if allow_empty_pool_registry and (not isinstance(raw_profiles, str) or not raw_profiles.strip()):
        raise HarnessError(500, "invalid_configuration", "ALLOW_EMPTY_POOL_REGISTRY requires explicit SCALE_TEST_PROFILES_JSON; use {} for no enrolled pools")
    profiles = _parse_scale_test_profiles(
        raw_profiles, max_profiles=max_profiles,
        max_pool_size=max_instances, require_pool_ids=controller_only,
    )
    budget_enabled = _parse_bool(environ.get("SCALE_TEST_BUDGET_LIMITS_ENABLED"), "SCALE_TEST_BUDGET_LIMITS_ENABLED", default=True)
    if controller_only and (not budget_enabled or (not profiles and not allow_empty_pool_registry)):
        raise HarnessError(500, "invalid_configuration", "controller-only mode requires registered pools and enabled capacity guards")
    bounded_growth = _parse_bool(environ.get("ENABLE_BOUNDED_GROWTH"), "ENABLE_BOUNDED_GROWTH", default=False)
    max_total_vms = _parse_nonnegative_int(environ.get("CONTROLLER_MAX_TOTAL_VMS", "25"), "CONTROLLER_MAX_TOTAL_VMS")
    launch_timeout = _parse_nonnegative_int(environ.get("CONTROLLER_LAUNCH_TIMEOUT_SECONDS", "900"), "CONTROLLER_LAUNCH_TIMEOUT_SECONDS")
    if min(max_total_vms, launch_timeout) < 1 or (bounded_growth and (not controller_only or auth_mode != "oci_iam")):
        raise HarnessError(500, "invalid_configuration", "bounded growth requires controller-only IAM mode and positive VM/launch-timeout limits")

    return Config(
        configured_pool_id=configured_pool_id,
        pool_name=pool_name,
        compartment_id=compartment_id,
        harness_id=harness_id,
        harness_token=harness_token,
        worker_token=environ.get("WORKER_TOKEN") or harness_token,
        function_role=function_role,
        max_pool_size=max_pool_size,
        termination_delay_seconds=delay,
        dry_run=_parse_bool(environ.get("DRY_RUN"), "DRY_RUN", default=function_role == "control"),
        enable_termination=_parse_bool(
            environ.get("ENABLE_TERMINATION"),
            "ENABLE_TERMINATION",
            default=False,
        ),
        scale_test_profiles=profiles,
        scale_test_budget_limits_enabled=budget_enabled,
        max_active_scale_test_profiles=max_active_scale_test_profiles,
        max_scale_test_total_ocpus=max_scale_test_total_ocpus,
        ledger_namespace=ledger_namespace,
        ledger_bucket=ledger_bucket,
        controller_only=controller_only,
        auth_mode=auth_mode,
        max_profiles=max_profiles,
        max_instances_per_profile=max_instances,
        allow_empty_pool_registry=allow_empty_pool_registry,
        bounded_growth=bounded_growth,
        max_total_vms=max_total_vms,
        launch_timeout_seconds=launch_timeout,
    )


def _request_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("requestId", payload.get("operationId"))
    if not isinstance(value, str) or not REQUEST_ID_RE.fullmatch(value):
        raise HarnessError(400, "invalid_request_id", "requestId is required and has an invalid format")
    return value


def _operation_id(payload: Mapping[str, Any], request_id: str) -> str:
    value = payload.get("drainOperationId", payload.get("operationId", request_id))
    if not isinstance(value, str) or not REQUEST_ID_RE.fullmatch(value):
        raise HarnessError(400, "invalid_drain_operation_id", "drainOperationId has an invalid format")
    return value


def _instance_id(payload: Mapping[str, Any]) -> str:
    return _validate_ocid(payload.get("instanceId"), "instance", "instanceId")


def _desired_count(payload: Mapping[str, Any], config: Config) -> int:
    value = payload.get("desiredCount")
    if isinstance(value, bool) or not isinstance(value, int):
        raise HarnessError(400, "invalid_desired_count", "desiredCount must be an integer")
    if value < 0 or value > config.max_pool_size:
        raise HarnessError(
            400,
            "invalid_desired_count",
            f"desiredCount must be between 0 and {config.max_pool_size}",
        )
    return value


def _canonical_action(payload: Mapping[str, Any]) -> tuple[str, str]:
    requested = payload.get("action")
    if not isinstance(requested, str) or requested not in ACTION_ALIASES:
        raise HarnessError(400, "invalid_action", "action is missing or unsupported")
    return requested, ACTION_ALIASES[requested]


def _authorize_role(action: str, config: Config) -> None:
    """Enforce the action allowlist for the deployed Function identity."""

    if config.controller_only:
        allowed = {"reconcile_pool", "request_status", "set_pool_protection", "scale_test_status"}
    elif config.function_role == "control":
        allowed = CONTROL_ACTIONS
    elif config.function_role == "terminator":
        allowed = TERMINATOR_ACTIONS
    else:
        allowed = READINESS_ACTIONS
    if action not in allowed:
        raise HarnessError(403, "action_not_allowed_for_role", "this Function role cannot perform the action")


def _authenticate(
    action: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, Any],
    config: Config,
) -> None:
    """Use POC tokens or the reference deployment's OCI InvokeFunction boundary.

    In oci_iam mode OCI authenticates and authorizes the signed request before
    running this Function. No body/header claim is treated as identity here.
    This mode MUST NOT be exposed through an unauthenticated gateway, proxy,
    Fn development server, or broadly authorized invocation principal. Anyone
    allowed to invoke this Function can use all controller actions.
    """

    if config.auth_mode == "oci_iam":
        return

    supplied = _header(headers, "X-Harness-Token")
    authorization = _header(headers, "Authorization")
    if supplied is None and authorization:
        scheme, separator, value = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            supplied = value
    if supplied is None:
        candidate = payload.get("token")
        supplied = candidate if isinstance(candidate, str) else None
    expected = config.worker_token if action in WORKER_ACTIONS else config.harness_token
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise HarnessError(401, "unauthorized", "a valid harness token is required")


def _pool_summaries_by_name(config: Config, clients: Clients, pool_name: str) -> list[Any]:
    summaries: list[Any] = []
    page: str | None = None
    seen_pages: set[str] = set()
    while True:
        kwargs: dict[str, Any] = {"display_name": pool_name, "limit": 100}
        if page:
            kwargs["page"] = page
        response = clients.pools.list_instance_pools(config.compartment_id, **kwargs)
        data = _mapping_value(response, "data", [])
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise HarnessError(502, "invalid_oci_response", "OCI returned an invalid pool list")
        summaries.extend(data)
        next_page = _header(_response_headers(response), "opc-next-page")
        if not next_page:
            return summaries
        if next_page in seen_pages:
            raise HarnessError(502, "invalid_oci_response", "OCI returned a repeated pagination token")
        seen_pages.add(next_page)
        page = next_page


def _iter_pool_summaries(config: Config, clients: Clients) -> list[Any]:
    if not config.pool_name:
        return []
    return _pool_summaries_by_name(config, clients, config.pool_name)


def _resolve_scope(config: Config, clients: Clients) -> Scope:
    """Resolve the fixed contrast pool and revalidate its identity/provenance.

    Name-based discovery is accepted only when exactly one active pool has both
    the configured name and HarnessId. Every request then re-reads the full pool
    and rejects compartment, name, or tag drift.
    """

    if config.configured_pool_id:
        pool_id = config.configured_pool_id
    else:
        candidates = []
        for summary in _iter_pool_summaries(config, clients):
            tags = _mapping_value(summary, "freeform_tags", {}) or {}
            state = _mapping_value(summary, "lifecycle_state")
            if (
                _mapping_value(summary, "display_name") == config.pool_name
                and isinstance(tags, Mapping)
                and tags.get(HARNESS_TAG) == config.harness_id
                and state in ACTIVE_POOL_STATES
            ):
                candidates.append(summary)
        if len(candidates) != 1:
            reason = "pool_not_found" if not candidates else "pool_resolution_ambiguous"
            raise HarnessError(
                409,
                reason,
                "pool name and HarnessId must resolve to exactly one active instance pool",
            )
        pool_id = _validate_ocid(_mapping_value(candidates[0], "id"), "instancepool", "resolved pool ID", status=502)

    response = clients.pools.get_instance_pool(pool_id)
    pool = _mapping_value(response, "data")
    if pool is None or _mapping_value(pool, "id", pool_id) != pool_id:
        raise HarnessError(502, "invalid_oci_response", "OCI returned an unexpected instance pool")
    if _mapping_value(pool, "compartment_id") != config.compartment_id:
        raise HarnessError(403, "compartment_not_allowed", "the pool is outside the fixed compartment")
    if config.pool_name and _mapping_value(pool, "display_name") != config.pool_name:
        raise HarnessError(409, "pool_identity_changed", "the resolved pool name no longer matches")
    tags = _mapping_value(pool, "freeform_tags", {}) or {}
    if not isinstance(tags, Mapping) or tags.get(HARNESS_TAG) != config.harness_id:
        raise HarnessError(409, "pool_identity_changed", "the pool HarnessId tag no longer matches")
    return Scope(config=config, pool_id=pool_id, pool=pool, pool_etag=_etag(response))


def _scale_test_profile(payload: Mapping[str, Any], config: Config) -> ScaleTestProfile:
    key = payload.get("profileKey")
    if not isinstance(key, str) or key not in config.scale_test_profiles:
        raise HarnessError(400, "invalid_profile", "profileKey must name a configured scale-test profile")
    return config.scale_test_profiles[key]


def _resolve_scale_test_scope(
    profile: ScaleTestProfile,
    config: Config,
    clients: Clients,
) -> Scope:
    """Resolve exactly one allowlisted benchmark pool for a server profile.

    The pool name, HarnessId, and ScaleTestProfile tag must all match. This
    prevents a caller-supplied pool key from redirecting mutations to an
    unrelated pool in the same compartment.
    """

    # Operator registrations pin identity: a pool recreated with the same name
    # and tags must never inherit authority over the old pool's demand ledger.
    if profile.pool_id:
        pool_id = profile.pool_id
    else:
        candidates = []
        for summary in _pool_summaries_by_name(config, clients, profile.pool_name):
            tags = _mapping_value(summary, "freeform_tags", {}) or {}
            state = _mapping_value(summary, "lifecycle_state")
            if (
                _mapping_value(summary, "display_name") == profile.pool_name
                and isinstance(tags, Mapping)
                and tags.get(HARNESS_TAG) == config.harness_id
                and tags.get(SCALE_TEST_PROFILE_TAG) == profile.key
                and state in ACTIVE_POOL_STATES
            ):
                candidates.append(summary)
        if len(candidates) != 1:
            reason = "scale_test_pool_not_found" if not candidates else "scale_test_pool_resolution_ambiguous"
            raise HarnessError(
                409,
                reason,
                f"profile {profile.key} must resolve to exactly one active scale-test pool",
            )
        pool_id = _validate_ocid(
            _mapping_value(candidates[0], "id"),
            "instancepool",
            "resolved scale-test pool ID",
            status=502,
        )
    response = clients.pools.get_instance_pool(pool_id)
    pool = _mapping_value(response, "data")
    if pool is None or _mapping_value(pool, "id", pool_id) != pool_id:
        raise HarnessError(502, "invalid_oci_response", "OCI returned an unexpected scale-test pool")
    if _mapping_value(pool, "compartment_id") != config.compartment_id:
        raise HarnessError(403, "compartment_not_allowed", "the scale-test pool is outside the fixed compartment")
    if _mapping_value(pool, "lifecycle_state") not in ACTIVE_POOL_STATES:
        raise HarnessError(409, "pool_not_active", "the registered pool is not active")
    tags = _mapping_value(pool, "freeform_tags", {}) or {}
    if (
        _mapping_value(pool, "display_name") != profile.pool_name
        or not isinstance(tags, Mapping)
        or tags.get(HARNESS_TAG) != config.harness_id
        or tags.get(SCALE_TEST_PROFILE_TAG) != profile.key
    ):
        raise HarnessError(409, "pool_identity_changed", "the scale-test pool identity no longer matches")
    return Scope(config=config, pool_id=pool_id, pool=pool, pool_etag=_etag(response))


def _validate_request_scope(payload: Mapping[str, Any], scope: Scope) -> None:
    supplied_pool = payload.get("poolId")
    supplied_compartment = payload.get("compartmentId")
    if supplied_pool is not None:
        _validate_ocid(supplied_pool, "instancepool", "poolId")
        if supplied_pool != scope.pool_id:
            raise HarnessError(403, "pool_not_allowed", "the requested pool is not configured for this harness")
    if supplied_compartment is not None:
        _validate_ocid(supplied_compartment, "compartment", "compartmentId")
        if supplied_compartment != scope.config.compartment_id:
            raise HarnessError(403, "compartment_not_allowed", "the requested compartment is not configured")


def _require_pool_running(scope: Scope) -> None:
    if _mapping_value(scope.pool, "lifecycle_state") != "RUNNING":
        raise HarnessError(409, "pool_not_running", "the instance pool must be in RUNNING state")


def _pool_size(pool: Any) -> int:
    size = _mapping_value(pool, "size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise HarnessError(502, "invalid_oci_response", "OCI returned an invalid pool size")
    return size


def _reported_current_size(pool: Any, attached: int) -> int:
    value = _mapping_value(pool, "current_size", attached)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return attached
    return value


def _effective_pool_size(scope: Scope, clients: Clients) -> int:
    """Keep detached-but-terminating workers visible to cross-pool cost guards."""

    members = _members(scope, clients)
    attached = len(members)
    profile = scope.config.scale_test_profiles[_tags(scope.pool)[SCALE_TEST_PROFILE_TAG]]
    _, retiring = _retirement_snapshot(scope, clients, profile, members)
    detached_retiring = len(set(retiring) - {_mapping_value(member, "id") for member in members})
    return max(_pool_size(scope.pool), _reported_current_size(scope.pool, attached), attached) + detached_retiring


def _members(scope: Scope, clients: Clients) -> list[Any]:
    members: list[Any] = []
    page: str | None = None
    seen_pages: set[str] = set()
    while True:
        kwargs: dict[str, Any] = {"limit": 100}
        if page:
            kwargs["page"] = page
        response = clients.pools.list_instance_pool_instances(
            scope.config.compartment_id,
            scope.pool_id,
            **kwargs,
        )
        data = _mapping_value(response, "data", [])
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise HarnessError(502, "invalid_oci_response", "OCI returned an invalid member list")
        for member in data:
            # Native shrink can leave historical "Terminated" summaries in
            # the pool list even after the pool is RUNNING at its lower target.
            # Never treat a summary alone as released capacity: independently
            # confirm the Compute lifecycle before excluding that identity.
            state = _mapping_value(member, "state", _mapping_value(member, "lifecycle_state", ""))
            if isinstance(state, str) and state.upper() == "TERMINATED":
                instance, _ = _instance(scope, clients, _mapping_value(member, "id"))
                if _mapping_value(instance, "lifecycle_state") == "TERMINATED":
                    continue
            members.append(member)
        next_page = _header(_response_headers(response), "opc-next-page")
        if not next_page:
            return members
        if next_page in seen_pages:
            raise HarnessError(502, "invalid_oci_response", "OCI returned a repeated pagination token")
        seen_pages.add(next_page)
        page = next_page


def _compartment_instances(scope: Scope, clients: Clients) -> list[Any]:
    instances: list[Any] = []
    page: str | None = None
    seen_pages: set[str] = set()
    while True:
        kwargs: dict[str, Any] = {"limit": 100}
        if page:
            kwargs["page"] = page
        response = clients.compute.list_instances(scope.config.compartment_id, **kwargs)
        data = _mapping_value(response, "data", [])
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise HarnessError(502, "invalid_oci_response", "OCI returned an invalid instance list")
        instances.extend(data)
        next_page = _header(_response_headers(response), "opc-next-page")
        if not next_page:
            return instances
        if next_page in seen_pages:
            raise HarnessError(502, "invalid_oci_response", "OCI returned a repeated pagination token")
        seen_pages.add(next_page)
        page = next_page


def _member_by_id(members: Sequence[Any], instance_id: str) -> Any | None:
    return next((member for member in members if _mapping_value(member, "id") == instance_id), None)


def _instance(scope: Scope, clients: Clients, instance_id: str) -> tuple[Any, str]:
    response = clients.compute.get_instance(instance_id)
    instance = _mapping_value(response, "data")
    if instance is None or _mapping_value(instance, "id") != instance_id:
        raise HarnessError(502, "invalid_oci_response", "OCI returned an unexpected instance")
    if _mapping_value(instance, "compartment_id") != scope.config.compartment_id:
        raise HarnessError(403, "compartment_not_allowed", "the instance is outside the fixed compartment")
    return instance, _etag(response)


def _tags(instance: Any) -> MutableMapping[str, str]:
    tags = _mapping_value(instance, "freeform_tags", {})
    if tags is None:
        return {}
    if not isinstance(tags, Mapping):
        raise HarnessError(502, "invalid_oci_response", "OCI returned invalid freeform tags")
    return dict(tags)


def _utcnow(clock: Callable[[], datetime] | None) -> datetime:
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HarnessError(409, "invalid_drain_timestamp", "DrainRequestedAt must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise HarnessError(409, "invalid_drain_timestamp", "DrainRequestedAt is invalid") from error
    return parsed.astimezone(timezone.utc)


def _expected_drain_tags(scope: Scope, operation_id: str) -> Mapping[str, str]:
    return {
        PROTECTION_TAG: "0",
        HARNESS_TAG: scope.config.harness_id,
        ORIGIN_POOL_TAG: scope.pool_id,
        OPERATION_TAG: operation_id,
    }


def _validate_drain_tags(instance: Any, scope: Scope, operation_id: str) -> Mapping[str, str]:
    """Require the complete, exact provenance set for a contrast-pool drain."""

    tags = _tags(instance)
    for key, expected in _expected_drain_tags(scope, operation_id).items():
        if tags.get(key) != expected:
            raise HarnessError(409, "instance_not_drainable", f"instance tag {key} is not the expected value")
    if REQUESTED_AT_TAG not in tags:
        raise HarnessError(409, "instance_not_drainable", f"instance tag {REQUESTED_AT_TAG} is missing")
    _parse_timestamp(tags[REQUESTED_AT_TAG])
    return tags


def _tag_instance(
    scope: Scope,
    clients: Clients,
    instance: Any,
    etag: str,
    *,
    enabled: bool,
    operation_id: str,
    now: datetime,
) -> tuple[Any, str, bool]:
    """Merge protection/provenance tags using ETag concurrency control."""

    desired = {
        PROTECTION_TAG: "1" if enabled else "0",
        HARNESS_TAG: scope.config.harness_id,
        ORIGIN_POOL_TAG: scope.pool_id,
        OPERATION_TAG: operation_id,
    }
    existing = _tags(instance)
    if all(existing.get(key) == value for key, value in desired.items()) and existing.get(REQUESTED_AT_TAG):
        _parse_timestamp(existing[REQUESTED_AT_TAG])
        return instance, etag, False

    merged = dict(existing)
    for key in DRAIN_PRUNABLE_TAGS:
        merged.pop(key, None)
    merged.update(desired)
    merged[REQUESTED_AT_TAG] = _timestamp(now)
    if len(merged) > OCI_MAX_FREEFORM_TAGS:
        raise HarnessError(
            409,
            "freeform_tag_limit_exceeded",
            "the drain markers would exceed OCI's ten-freeform-tag limit",
        )
    response = clients.compute.update_instance(
        _mapping_value(instance, "id"),
        _model("UpdateInstanceDetails", freeform_tags=merged),
        if_match=etag,
    )
    updated = _mapping_value(response, "data")
    if updated is not None:
        return updated, _header(_response_headers(response), "etag") or etag, True
    reread, reread_etag = _instance(scope, clients, _mapping_value(instance, "id"))
    return reread, reread_etag, True


def _detach(
    scope: Scope,
    clients: Clients,
    instance_id: str,
    request_id: str,
    *,
    auto_terminate: bool = False,
) -> Any:
    """Detach one exact OCID while decrementing its instance-pool slot.

    Callers choose whether OCI also terminates the detached instance. All
    legacy scale-in and self-reclaim paths pass ``auto_terminate=True``;
    bounded growth submits termination separately.
    ``request_id`` is reused as OCI's retry token for idempotent submission.
    """

    if scope.config.dry_run:
        raise HarnessError(409, "dry_run", "dry-run mode cannot detach an instance")
    if auto_terminate and not scope.config.enable_termination:
        raise HarnessError(409, "termination_disabled", "the termination kill switch is disabled")
    details = _model(
        "DetachInstancePoolInstanceDetails",
        instance_id=instance_id,
        is_decrement_size=True,
        is_auto_terminate=auto_terminate,
    )
    return clients.pools.detach_instance_pool_instance(
        scope.pool_id,
        details,
        opc_retry_token=request_id,
    )


def _serialize_instance(instance: Any, member: Any | None) -> Mapping[str, Any]:
    tags = _tags(instance)
    is_member = member is not None
    shape_config = _mapping_value(instance, "shape_config")
    return {
        "id": _mapping_value(instance, "id"),
        "displayName": _mapping_value(instance, "display_name"),
        "state": (
            _mapping_value(member, "state", _mapping_value(member, "lifecycle_state"))
            if is_member
            else _mapping_value(instance, "lifecycle_state")
        ),
        "lifecycleState": _mapping_value(instance, "lifecycle_state"),
        "timeCreated": str(
            _mapping_value(member, "time_created", _mapping_value(instance, "time_created", ""))
        )
        or None,
        "availabilityDomain": _mapping_value(
            member,
            "availability_domain",
            _mapping_value(instance, "availability_domain"),
        ),
        "faultDomain": _mapping_value(member, "fault_domain", _mapping_value(instance, "fault_domain")),
        "shape": _mapping_value(instance, "shape"),
        "shapeConfig": {
            "ocpus": _mapping_value(shape_config, "ocpus"),
            "memoryGbs": _mapping_value(shape_config, "memory_in_gbs"),
        },
        "isPoolMember": is_member,
        "membershipState": "ATTACHED" if is_member else "DETACHED",
        "freeformTags": tags,
        "terminationProtectionEnabled": tags.get(PROTECTION_TAG),
        "drainOperationId": tags.get(OPERATION_TAG),
        "scaleTestReadyAt": tags.get(SCALE_TEST_READY_AT_TAG),
    }


def _status(scope: Scope, clients: Clients) -> tuple[int, Mapping[str, Any]]:
    members = _members(scope, clients)
    members_by_id = {_mapping_value(member, "id"): member for member in members}
    instances_by_id: dict[str, Any] = {}
    for member in members:
        instance, _ = _instance(scope, clients, _mapping_value(member, "id"))
        instances_by_id[_mapping_value(instance, "id")] = instance

    # Keep detached/terminating workers visible so the UI can prove there was
    # no replacement. Provenance must match both the harness and origin pool.
    for instance in _compartment_instances(scope, clients):
        instance_id = _mapping_value(instance, "id")
        if instance_id in instances_by_id:
            continue
        tags = _tags(instance)
        if tags.get(HARNESS_TAG) == scope.config.harness_id and tags.get(ORIGIN_POOL_TAG) == scope.pool_id:
            instances_by_id[instance_id] = instance

    serialized = [
        _serialize_instance(instance, members_by_id.get(instance_id))
        for instance_id, instance in instances_by_id.items()
    ]
    serialized.sort(key=lambda item: (item.get("timeCreated") or "9999", item.get("id") or ""))
    attached = [item for item in serialized if item["isPoolMember"]]
    native_oldest = attached[0]["id"] if attached else None
    recommended_newest = attached[-1]["id"] if attached else None
    return 200, {
        "result": "ok",
        "action": "status",
        "pool": {
            "id": scope.pool_id,
            "displayName": _mapping_value(scope.pool, "display_name"),
            "size": _pool_size(scope.pool),
            "lifecycleState": _mapping_value(scope.pool, "lifecycle_state"),
            "maxSize": scope.config.max_pool_size,
        },
        "instances": serialized,
        "proof": {
            "nativeOldestInstanceId": native_oldest,
            "recommendedNewestInstanceId": recommended_newest,
        },
        "safety": {
            "dryRun": scope.config.dry_run,
            "allowNativeScaleIn": False,
            "maxPoolSize": scope.config.max_pool_size,
            "terminationEnabled": scope.config.enable_termination,
            "terminationDelaySeconds": scope.config.termination_delay_seconds,
        },
    }


def _scale_test_profile_payload(profile: ScaleTestProfile) -> Mapping[str, Any]:
    return {
        "key": profile.key,
        "displayName": profile.display_name,
        "workerType": profile.worker_type,
        "ociShape": profile.oci_shape,
        "ocpus": profile.ocpus,
        "vcpus": profile.ocpus * 2,
        "memoryGbs": profile.memory_gbs,
        "maxSize": profile.max_size,
    }


def _has_valid_ready_marker(instance: Mapping[str, Any]) -> bool:
    value = instance.get("freeformTags", {}).get(SCALE_TEST_READY_AT_TAG)
    try:
        _parse_timestamp(value)
    except HarnessError:
        return False
    return True


def _matches_scale_test_profile(instance: Any, profile: ScaleTestProfile) -> bool:
    shape_config = _mapping_value(instance, "shape_config")
    return (
        _mapping_value(instance, "shape") == profile.oci_shape
        and _mapping_value(shape_config, "ocpus") == profile.ocpus
        and _mapping_value(shape_config, "memory_in_gbs") == profile.memory_gbs
    )


def _scale_test_configuration_check(
    scope: Scope,
    profile: ScaleTestProfile,
    clients: Clients,
) -> tuple[bool, str]:
    configuration_id = _validate_ocid(
        _mapping_value(scope.pool, "instance_configuration_id"),
        "instanceconfiguration",
        "scale-test instance configuration ID",
        status=502,
    )
    response = clients.pools.get_instance_configuration(configuration_id)
    configuration = _mapping_value(response, "data")
    if configuration is None or _mapping_value(configuration, "id", configuration_id) != configuration_id:
        raise HarnessError(502, "invalid_oci_response", "OCI returned an unexpected instance configuration")
    if _mapping_value(configuration, "compartment_id") != scope.config.compartment_id:
        return False, "The pool's instance configuration is outside the fixed compartment."
    configuration_tags = _mapping_value(configuration, "freeform_tags", {}) or {}
    details = _mapping_value(configuration, "instance_details")
    launch = _mapping_value(details, "launch_details")
    launch_tags = _mapping_value(launch, "freeform_tags", {}) or {}
    shape_config = _mapping_value(launch, "shape_config")
    if not isinstance(configuration_tags, Mapping) or not isinstance(launch_tags, Mapping):
        return False, "OCI returned malformed instance-configuration tags."
    if scope.config.controller_only and launch_tags.get(PROTECTION_TAG) != "1":
        return False, "Worker launch configuration must start with protection tag exactly 1."
    if (
        configuration_tags.get(HARNESS_TAG) != scope.config.harness_id
        or configuration_tags.get(SCALE_TEST_PROFILE_TAG) != profile.key
        or launch_tags.get(HARNESS_TAG) != scope.config.harness_id
        or launch_tags.get(SCALE_TEST_PROFILE_TAG) != profile.key
    ):
        return False, "The pool's immutable instance configuration has unexpected provenance tags."
    if (
        _mapping_value(launch, "shape") != profile.oci_shape
        or _mapping_value(shape_config, "ocpus") != profile.ocpus
        or _mapping_value(shape_config, "memory_in_gbs") != profile.memory_gbs
    ):
        return False, "The pool's instance configuration shape, OCPU count, or memory does not match the profile."
    return True, "The pool and immutable instance configuration match the benchmark profile."


def _latest_desired_generation(
    config: Config,
    clients: Clients,
    profile: ScaleTestProfile,
) -> int:
    """Return the queue's monotonic generation without creating or changing it."""

    if not config.ledger_namespace or not config.ledger_bucket or clients.objects is None:
        return 0
    try:
        record, _ = _get_ledger_record(config, clients, _pool_queue_object_name(profile.key))
    except Exception as error:
        if getattr(error, "status", None) == 404:
            return 0
        raise
    generation = record.get("generation")
    # Records written before desired_generation was accepted already used a
    # positive server generation. Treat that value as the migration baseline.
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        return 0
    return generation


def _awaiting_pool_enrollment_status(config: Config, now: datetime) -> tuple[int, Mapping[str, Any]]:
    """Report a deliberately empty controller without contacting OCI services."""

    message = "The controller is deployed with no enrolled pools. Enroll pools and update the Function configuration before requesting pool operations."
    return 200, {
        "result": "awaiting_pool_enrollment",
        "action": "scale_test_status",
        "serverTime": _timestamp(now),
        "message": message,
        "enrolledPoolCount": 0,
        "configurationMatches": False,
        "configurationMessage": message,
        "profiles": [],
        "reconcilePools": [],
        "workerSelfReclaim": {
            "enabled": False,
            "scope": "contrast_pool_only",
            "status": "DISABLED",
            "detail": "Worker self-reclaim is disabled for controller-only deployments.",
        },
        "safety": {
            "dryRun": config.dry_run,
            "budgetLimitsEnabled": config.scale_test_budget_limits_enabled,
            "maxProfiles": config.max_profiles,
            "maxInstancesPerProfile": config.max_instances_per_profile,
            "maxActiveProfiles": config.max_active_scale_test_profiles,
            "activeProfiles": 0,
            "maxTotalOcpus": config.max_scale_test_total_ocpus,
            "totalDesiredOcpus": 0,
            "totalEffectiveOcpus": 0,
            "queuePersistence": "object_storage_request_ledger",
        },
    }


def _scale_test_status(config: Config, clients: Clients, now: datetime) -> tuple[int, Mapping[str, Any]]:
    """Return authoritative pool, membership, shape, and readiness state.

    ``ready`` is intentionally stricter than OCI request acceptance: the pool
    must be RUNNING, desired/current/attached counts must agree, each instance
    must be RUNNING, and every worker must have a valid bootstrap timestamp.
    """

    profiles = []
    active_profiles = 0
    total_desired_ocpus = 0
    total_effective_ocpus = 0
    total_effective_vms = 0
    capacity, _ = _capacity_record(config, clients) if config.controller_only else (None, None)
    all_configurations_match = True
    for profile in config.scale_test_profiles.values():
        scope = _resolve_scale_test_scope(profile, config, clients)
        configuration_matches, configuration_message = _scale_test_configuration_check(
            scope,
            profile,
            clients,
        )
        members = _members(scope, clients)
        registry, retiring = _retirement_snapshot(scope, clients, profile, members)
        instances = []
        for member in members:
            instance, _ = _instance(scope, clients, _mapping_value(member, "id"))
            if not _matches_scale_test_profile(instance, profile):
                configuration_matches = False
                configuration_message = "At least one attached instance does not match the configured shape, OCPUs, or memory."
            serialized = dict(_serialize_instance(instance, member))
            serialized["retiring"] = _mapping_value(instance, "id") in retiring
            serialized["retirementCommitted"] = _mapping_value(instance, "id") in registry["instances"]
            instances.append(serialized)
        all_configurations_match = all_configurations_match and configuration_matches
        instances.sort(key=lambda item: (item.get("timeCreated") or "9999", item.get("id") or ""))
        running_count = sum(item.get("lifecycleState") == "RUNNING" for item in instances)
        usable_instances = [item for item in instances if not item["retiring"]]
        ready_count = sum(
            item.get("lifecycleState") == "RUNNING" and _has_valid_ready_marker(item)
            for item in usable_instances
        )
        desired = _pool_size(scope.pool)
        try:
            demand, _ = _get_ledger_record(config, clients, _pool_queue_object_name(profile.key))
            desired_usable = demand["desiredSize"]
        except Exception as error:
            if getattr(error, "status", None) != 404:
                raise
            desired_usable = desired
        reported_current = _reported_current_size(scope.pool, len(members))
        member_ids = {_mapping_value(member, "id") for member in members}
        detached_retiring = len(set(retiring) - member_ids)
        reservation = capacity["pending"].get(profile.key, {}) if capacity else {}
        effective_size = max(desired, reported_current, len(members), reservation.get("target", 0)) + detached_retiring
        if effective_size > 0:
            active_profiles += 1
        total_desired_ocpus += desired * profile.ocpus
        total_effective_ocpus += effective_size * profile.ocpus
        total_effective_vms += effective_size
        profiles.append(
            {
                **_scale_test_profile_payload(profile),
                "latestDesiredGeneration": _latest_desired_generation(config, clients, profile),
                "configurationMatches": configuration_matches,
                "configurationMessage": configuration_message,
                "pool": {
                    "id": scope.pool_id,
                    "displayName": _mapping_value(scope.pool, "display_name"),
                    "lifecycleState": _mapping_value(scope.pool, "lifecycle_state"),
                    "desiredSize": desired,
                    "currentSize": reported_current,
                    "attachedSize": len(members),
                    "effectiveSize": effective_size,
                    "reservedTarget": reservation.get("target", 0),
                    "desiredUsableSize": desired_usable,
                    "usableSize": len(usable_instances),
                    "retiringSize": len(retiring),
                },
                "instances": instances,
                "retirement": {
                    "policy": "irreversible", "replacementPolicy": "bounded_growth_preview" if config.bounded_growth else "retire_first",
                    "pendingInstanceIds": sorted(retiring),
                    "pending": len(retiring), "revision": registry["revision"],
                },
                "readiness": {
                    "scope": "infrastructure_and_bootstrap_only",
                    "schedulerReady": None,
                    "attached": len(members),
                    "running": running_count,
                    "bootstrapReady": ready_count,
                    "usableAttached": len(usable_instances),
                    "usableRunning": sum(item.get("lifecycleState") == "RUNNING" for item in usable_instances),
                    "retiring": len(retiring),
                    "configurationMatches": configuration_matches,
                    "complete": (
                        _mapping_value(scope.pool, "lifecycle_state") == "RUNNING"
                        and not retiring
                        and desired_usable == desired == reported_current == len(members)
                        and running_count == desired
                        and ready_count == desired
                        and configuration_matches
                    ),
                },
            }
        )
    return 200, {
        "result": "ok",
        "action": "scale_test_status",
        "serverTime": _timestamp(now),
        "configurationMatches": all_configurations_match,
        "configurationMessage": (
            "Every scale-test pool matches its configured immutable launch profile."
            if all_configurations_match
            else "One or more scale-test pools or attached instances do not match their configured profiles."
        ),
        "profiles": profiles,
        "reconcilePools": [
            {
                "key": item["key"],
                "id": item["pool"]["id"],
                "name": item["pool"]["displayName"],
                "state": item["pool"]["lifecycleState"],
                "currentSize": item["pool"]["currentSize"],
                "desiredSize": item["pool"]["desiredSize"],
                "attachedSize": item["pool"]["attachedSize"],
                "effectiveSize": item["pool"]["effectiveSize"],
                "desiredUsableSize": item["pool"]["desiredUsableSize"],
                "usableSize": item["pool"]["usableSize"],
                "retiringSize": item["pool"]["retiringSize"],
                "minSize": 0,
                "maxSize": item["maxSize"],
                "latestDesiredGeneration": item["latestDesiredGeneration"],
                "configurationMatches": item["configurationMatches"],
                "instances": item["instances"],
            }
            for item in profiles
        ],
        "workerSelfReclaim": {
            "enabled": config.enable_termination and not config.controller_only,
            "scope": "contrast_pool_only",
            "status": "ENABLED" if config.enable_termination and not config.controller_only else "DISABLED",
            "detail": (
                "Optional self-reclaim is isolated to the contrast pool. Managed pools commit retirement with set_pool_protection and advance it through reconcile_pool."
                if config.enable_termination and not config.controller_only
                else "The worker self-reclaim kill switch is disabled."
            ),
        },
        "safety": {
            "dryRun": config.dry_run,
            "budgetLimitsEnabled": config.scale_test_budget_limits_enabled,
            "maxProfiles": config.max_profiles,
            "maxInstancesPerProfile": config.max_instances_per_profile,
            "maxActiveProfiles": config.max_active_scale_test_profiles,
            "activeProfiles": active_profiles,
            "maxTotalOcpus": config.max_scale_test_total_ocpus,
            "totalDesiredOcpus": total_desired_ocpus,
            "totalEffectiveOcpus": total_effective_ocpus,
            "maxTotalVms": config.max_total_vms if config.bounded_growth else None,
            "totalEffectiveVms": total_effective_vms,
            "boundedGrowthPreview": config.bounded_growth,
            "queuePersistence": "object_storage_request_ledger",
        },
    }


def _scale_test_desired(payload: Mapping[str, Any], profile: ScaleTestProfile) -> int:
    desired = payload.get("desiredCount")
    if isinstance(desired, bool) or not isinstance(desired, int):
        raise HarnessError(400, "invalid_desired_count", "desiredCount must be an integer")
    if desired < 0 or desired > profile.max_size:
        raise HarnessError(
            400,
            "invalid_desired_count",
            f"desiredCount must be between 0 and {profile.max_size} for profile {profile.key}",
        )
    return desired


def _request_field(payload: Mapping[str, Any], snake_name: str, camel_name: str) -> Any:
    snake_present = snake_name in payload
    camel_present = camel_name in payload
    if snake_present and camel_present and payload[snake_name] != payload[camel_name]:
        raise HarnessError(400, "conflicting_fields", f"{snake_name} and {camel_name} disagree")
    return payload.get(snake_name) if snake_present else payload.get(camel_name)


def _reconcile_request_id(payload: Mapping[str, Any]) -> str:
    value = _request_field(payload, "request_id", "requestId")
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        raise HarnessError(400, "invalid_request_id", "request_id must be a canonical UUID")
    return value.lower()


def _reconcile_profile(payload: Mapping[str, Any], config: Config) -> ScaleTestProfile:
    key = _request_field(payload, "pool_key", "poolKey")
    if not isinstance(key, str) or key not in config.scale_test_profiles:
        raise HarnessError(400, "invalid_pool_key", "pool_key must name an allowlisted pool")
    return config.scale_test_profiles[key]


def _reconcile_desired(payload: Mapping[str, Any], profile: ScaleTestProfile) -> int:
    desired = _request_field(payload, "desired_size", "desiredSize")
    if isinstance(desired, bool) or not isinstance(desired, int):
        raise HarnessError(400, "invalid_desired_size", "desired_size must be an integer")
    if desired < 0 or desired > profile.max_size:
        raise HarnessError(
            400,
            "invalid_desired_size",
            f"desired_size must be between 0 and {profile.max_size} for pool_key {profile.key}",
        )
    return desired


def _reconcile_exclusions(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = _request_field(payload, "exclude_instance_ids", "excludeInstanceIds")
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > MAX_EXCLUDED_INSTANCES:
        raise HarnessError(
            400,
            "invalid_exclude_instance_ids",
            f"exclude_instance_ids must contain at most {MAX_EXCLUDED_INSTANCES} instance OCIDs",
        )
    result: list[str] = []
    for value in raw:
        instance_id = _validate_ocid(value, "instance", "exclude_instance_ids entry")
        if instance_id not in result:
            result.append(instance_id)
    return tuple(result)


def _reconcile_desired_generation(payload: Mapping[str, Any]) -> int | None:
    """Validate the caller's monotonic demand version.

    Generation is optional only for migration from the first POC contract. A
    production caller should always provide one and increase it for each new
    demand decision while keeping it unchanged for retries.
    """

    supplied = [
        payload[name]
        for name in ("desired_generation", "desiredGeneration")
        if name in payload
    ]
    if not supplied:
        return None
    if any(value != supplied[0] for value in supplied[1:]):
        raise HarnessError(400, "conflicting_fields", "desired_generation aliases disagree")
    value = supplied[0]
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > 9_007_199_254_740_991
    ):
        raise HarnessError(
            400,
            "invalid_desired_generation",
            "desired_generation must be a positive JavaScript-safe integer",
        )
    return value


def _request_object_name(request_id: str) -> str:
    return f"requests/{request_id}.json"


def _pool_queue_object_name(pool_key: str) -> str:
    return f"queues/{pool_key}.json"


def _request_fingerprint(
    profile: ScaleTestProfile,
    desired: int,
    exclusions: Sequence[str],
    desired_generation: int | None,
) -> str:
    """Hash the immutable meaning of one logical request ID.

    Reusing an ID with a different pool, target, exclusions, or generation is
    an idempotency conflict rather than a new request.
    """

    fingerprint_value: dict[str, Any] = {
        "poolKey": profile.key,
        "desiredSize": desired,
        "excludeInstanceIds": sorted(exclusions),
    }
    # Preserve the original POC fingerprint byte-for-byte when the optional
    # generation is absent so requests written before this field was added
    # remain replayable after deployment.
    if desired_generation is not None:
        fingerprint_value["desiredGeneration"] = desired_generation
    normalized = json.dumps(
        fingerprint_value,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _claim_request(
    config: Config,
    clients: Clients,
    request_id: str,
    fingerprint: str,
    request_value: Mapping[str, Any],
    now: datetime,
    *,
    resume_completed: bool = False,
) -> tuple[Mapping[str, Any], str, bool, str | None]:
    """Create, replay, or safely resume one durable logical request.

    A short processing lease prevents two invocations from executing the same
    request concurrently. Terminal records are normally replayed verbatim; only
    the latest completed demand can reopen for capacity drift or retirement.
    Superseded requests never reopen. An abandoned
    nonterminal record can be resumed only after its lease expires and an ETag
    compare-and-swap succeeds. This request lease is distinct from the per-pool
    mutation lease used immediately around OCI writes.
    """

    object_name = _request_object_name(request_id)
    attempt_id = str(uuid.uuid4())
    pending = {
        "version": 1,
        "requestId": request_id,
        "fingerprint": fingerprint,
        "state": "processing",
        "attemptId": attempt_id,
        "createdAt": _timestamp(now),
        "updatedAt": _timestamp(now),
        "leaseUntil": _timestamp(now + timedelta(seconds=DEFAULT_REQUEST_LEASE_SECONDS)),
        "request": dict(request_value),
    }
    if "desiredGeneration" in request_value:
        pending["desiredGeneration"] = request_value["desiredGeneration"]
    try:
        etag = _put_ledger_record(
            config,
            clients,
            object_name,
            pending,
            if_none_match="*",
        )
        return pending, etag, True, None
    except Exception as error:
        if getattr(error, "status", None) != 412:
            raise

    existing, etag = _get_ledger_record(config, clients, object_name)
    if existing.get("fingerprint") != fingerprint:
        raise HarnessError(
            409,
            "idempotency_conflict",
            "request_id was already used with a different pool, desired size, or exclusion set",
        )
    existing_state = existing.get("state")
    if existing_state in {"superseded", "failed"} or (existing_state == "completed" and not resume_completed):
        return existing, etag, False, str(existing_state)

    lease_until = None
    raw_lease = existing.get("leaseUntil")
    if isinstance(raw_lease, str):
        try:
            lease_until = datetime.fromisoformat(raw_lease.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            lease_until = None
    if existing.get("state") == "processing" and lease_until is not None and lease_until > now:
        return existing, etag, False, "processing"

    resumed = dict(existing)
    resumed.update(
        {
            "state": "processing",
            "attemptId": attempt_id,
            "updatedAt": _timestamp(now),
            "leaseUntil": _timestamp(now + timedelta(seconds=DEFAULT_REQUEST_LEASE_SECONDS)),
        }
    )
    try:
        next_etag = _put_ledger_record(config, clients, object_name, resumed, if_match=etag)
    except Exception as error:
        if getattr(error, "status", None) == 412:
            raise HarnessError(
                409,
                "request_in_progress",
                "another invocation resumed this request",
                retryable=True,
            ) from error
        raise
    return resumed, next_etag, True, str(existing_state) if isinstance(existing_state, str) else None


def _register_latest_request(
    config: Config,
    clients: Clients,
    profile: ScaleTestProfile,
    request_id: str,
    desired: int,
    desired_generation: int | None,
    now: datetime,
) -> int:
    """Atomically publish this request as the newest desired state for a pool.

    The queue stores one latest absolute target, not a FIFO of relative resize
    commands. A caller-supplied generation at or below the stored generation is
    left unpublished; the later latest-request check classifies it as
    superseded. ETag compare-and-swap makes simultaneous publishers retryable.
    """

    object_name = _pool_queue_object_name(profile.key)
    for _ in range(5):
        try:
            existing, etag = _get_ledger_record(config, clients, object_name)
        except Exception as error:
            if getattr(error, "status", None) != 404:
                raise
            generation = desired_generation if desired_generation is not None else 1
            initial: dict[str, Any] = {
                "version": 1,
                "poolKey": profile.key,
                "generation": generation,
                "latestRequestId": request_id,
                "desiredSize": desired,
                "updatedAt": _timestamp(now),
                "ownerRequestId": None,
                "ownerAttemptId": None,
                "fence": 0,
                "leaseUntil": None,
            }
            if desired_generation is not None:
                initial["desiredGeneration"] = desired_generation
            try:
                _put_ledger_record(config, clients, object_name, initial, if_none_match="*")
                return generation
            except Exception as create_error:
                if getattr(create_error, "status", None) != 412:
                    raise
                continue
        if existing.get("latestRequestId") == request_id:
            generation = existing.get("generation")
            if isinstance(generation, int) and not isinstance(generation, bool) and generation > 0:
                if desired_generation is not None and desired_generation != generation:
                    raise HarnessError(
                        502,
                        "invalid_ledger_record",
                        "the request and per-pool queue generations disagree",
                    )
                return generation
        generation = existing.get("generation", 0)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise HarnessError(502, "invalid_ledger_record", "the per-pool queue generation is invalid")
        if desired_generation is not None:
            if desired_generation <= generation:
                return desired_generation
            next_generation = desired_generation
        else:
            next_generation = generation + 1
        owner_request = existing.get("ownerRequestId")
        owner_attempt = existing.get("ownerAttemptId")
        if (owner_request is None) != (owner_attempt is None):
            raise HarnessError(502, "invalid_ledger_record", "the per-pool queue mutation owner is incomplete")
        expiry = _lease_expiry(existing)
        if owner_request is not None and expiry is None:
            raise HarnessError(502, "invalid_ledger_record", "the per-pool queue mutation lease is invalid")
        if owner_request is not None and expiry is not None and expiry > now:
            raise HarnessError(
                409,
                "pool_mutation_busy",
                "the current desired state holds the per-pool mutation lease; replay after backoff",
                retryable=True,
            )
        fence = existing.get("fence", 0)
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise HarnessError(502, "invalid_ledger_record", "the per-pool queue mutation fence is invalid")
        updated: dict[str, Any] = dict(existing)
        updated.update({
            "version": 1,
            "poolKey": profile.key,
            "generation": next_generation,
            "latestRequestId": request_id,
            "desiredSize": desired,
            "updatedAt": _timestamp(now),
            "ownerRequestId": None,
            "ownerAttemptId": None,
            "fence": fence,
            "leaseUntil": None,
        })
        if desired_generation is not None:
            updated["desiredGeneration"] = desired_generation
        else:
            updated.pop("desiredGeneration", None)
        try:
            _put_ledger_record(config, clients, object_name, updated, if_match=etag)
            return next_generation
        except Exception as error:
            if getattr(error, "status", None) != 412:
                raise
    raise HarnessError(409, "queue_busy", "the per-pool desired-state queue changed concurrently", retryable=True)


def _is_latest_request(
    config: Config,
    clients: Clients,
    profile: ScaleTestProfile,
    request_id: str,
    generation: int,
    desired: int,
) -> bool:
    """Return whether the durable queue still authorizes this exact intent."""

    try:
        current, _ = _get_ledger_record(config, clients, _pool_queue_object_name(profile.key))
    except Exception as error:
        if getattr(error, "status", None) == 404:
            return False  # Legacy terminal records may predate the pool queue.
        raise
    current_generation = current.get("generation")
    if (
        isinstance(current_generation, bool)
        or not isinstance(current_generation, int)
        or current_generation < 1
    ):
        raise HarnessError(502, "invalid_ledger_record", "the per-pool queue generation is invalid")
    return (
        current.get("latestRequestId") == request_id
        and current_generation == generation
        and current.get("desiredSize") == desired
    )


def _enforce_scale_test_budget(
    profile: ScaleTestProfile,
    desired: int,
    config: Config,
    clients: Clients,
) -> None:
    """Check projected active-profile and aggregate-OCPU usage.

    ``effective`` observed size is used instead of trusting only the desired
    field, so instances still attaching or detaching cannot disappear from the
    cost calculation.
    """

    if not config.scale_test_budget_limits_enabled:
        return
    active_profiles = 0
    total_ocpus = 0
    for candidate in config.scale_test_profiles.values():
        candidate_scope = _resolve_scale_test_scope(candidate, config, clients)
        observed_size = _effective_pool_size(candidate_scope, clients)
        candidate_size = max(desired, observed_size) if candidate.key == profile.key else observed_size
        if candidate_size > 0:
            active_profiles += 1
        total_ocpus += candidate_size * candidate.ocpus
    if active_profiles > config.max_active_scale_test_profiles:
        raise HarnessError(
            409,
            "active_profile_limit_exceeded",
            f"cleanup another benchmark pool first; at most {config.max_active_scale_test_profiles} profile may be active",
        )
    if total_ocpus > config.max_scale_test_total_ocpus:
        raise HarnessError(
            409,
            "scale_test_ocpu_limit_exceeded",
            f"the requested benchmark state exceeds the aggregate {config.max_scale_test_total_ocpus}-OCPU guard",
        )


def _assert_autoscaling_removed(scope: Scope, clients: Clients) -> None:
    """Refuse controller ownership while any OCI autoscaler targets the pool.

    Even a disabled native configuration is rejected. This establishes one
    writer for desired capacity and prevents native scale-in from bypassing the
    controller's explicit-victim policy.
    """

    if clients.autoscaling is None:
        raise HarnessError(
            503,
            "autoscaling_guard_unavailable",
            "the autoscaling cutover guard is not configured",
        )
    page: str | None = None
    seen_pages: set[str] = set()
    matches: list[str] = []
    while True:
        kwargs: dict[str, Any] = {"limit": 100}
        if page:
            kwargs["page"] = page
        response = clients.autoscaling.list_auto_scaling_configurations(
            scope.config.compartment_id,
            **kwargs,
        )
        values = _mapping_value(response, "data", [])
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise HarnessError(502, "invalid_oci_response", "OCI returned an invalid autoscaling configuration list")
        for value in values:
            resource = _mapping_value(value, "resource")
            if _mapping_value(resource, "type") == "instancePool" and _mapping_value(resource, "id") == scope.pool_id:
                matches.append(str(_mapping_value(value, "id", "unknown")))
        next_page = _header(_response_headers(response), "opc-next-page")
        if not next_page:
            break
        if next_page in seen_pages:
            raise HarnessError(502, "invalid_oci_response", "OCI returned a repeated autoscaling page token")
        seen_pages.add(next_page)
        page = next_page
    if matches:
        raise HarnessError(
            409,
            "autoscaling_configuration_attached",
            "remove the pool's OCI autoscaling configuration before the Function reconciler is allowed to mutate it",
        )


def _stable_detach_token(request_id: str, instance_id: str) -> str:
    digest = hashlib.sha256(f"{request_id}|{instance_id}".encode("utf-8")).hexdigest()
    return f"detach-{digest[:56]}"


def _retirement_object_name(scope: Scope) -> str:
    # Key by immutable pool OCID, not a reusable profile/display name.
    return f"retirements/{scope.pool_id}.json"


def _retirement_registry(scope: Scope, clients: Clients) -> tuple[Mapping[str, Any], str | None]:
    """Read irreversible worker commitments independently of desired generations.

    Tombstones are retained so neither re-protection nor a repeated callback
    can reuse a retired identity. This bounded POC retains them in one pool
    object; an unbounded fleet should archive/shard tombstones, never discard
    active commitments. Status reads never create or advance records.
    """

    try:
        record, etag = _get_ledger_record(scope.config, clients, _retirement_object_name(scope))
    except Exception as error:
        if getattr(error, "status", None) != 404:
            raise
        return {
            "version": 1, "poolId": scope.pool_id, "harnessId": scope.config.harness_id,
            "revision": 0, "instances": {},
        }, None
    entries = record.get("instances")
    revision = record.get("revision")
    if (
        record.get("version") != 1 or record.get("poolId") != scope.pool_id
        or record.get("harnessId") != scope.config.harness_id
        or not isinstance(entries, Mapping) or isinstance(revision, bool)
        or not isinstance(revision, int) or revision != len(entries)
        or any(
            not isinstance(key, str) or not key.startswith("ocid1.instance.") or not OCID_RE.fullmatch(key)
            or not isinstance(value, Mapping) or value.get("instanceId") != key
            or value.get("state") not in {"committed", "terminated"}
            or value.get("detachRetryToken") != _stable_detach_token(scope.pool_id, key)
            for key, value in entries.items()
        )
    ):
        raise HarnessError(502, "invalid_ledger_record", "the pool retirement registry is invalid")
    return record, etag


def _write_retirement(
    scope: Scope, clients: Clients, instance_id: str, request_id: str, now: datetime,
    *, terminated: bool = False,
) -> Mapping[str, Any]:
    """Create a commitment once, or monotonically acknowledge OCI termination.

    Persist BEFORE the tag/detach write. If that write times out, retirement
    intent remains durable and fail-closed; a retry cannot change its meaning.
    The detach token belongs to the worker retirement, NOT to demand UUIDs.
    """

    for _ in range(5):
        registry, etag = _retirement_registry(scope, clients)
        entries = dict(registry["instances"])
        existing = entries.get(instance_id)
        if existing is not None and (not terminated or existing["state"] == "terminated"):
            return registry
        if terminated and existing is None:
            raise HarnessError(502, "invalid_ledger_record", "cannot complete an unrecorded retirement")
        entry = dict(existing) if existing is not None else {
            "instanceId": instance_id, "requestId": request_id, "committedAt": _timestamp(now),
            "state": "committed", "detachRetryToken": _stable_detach_token(scope.pool_id, instance_id),
        }
        if terminated:
            entry.update(state="terminated", terminatedAt=_timestamp(now))
        entries[instance_id] = entry
        updated = {**registry, "revision": len(entries), "instances": entries}
        try:
            _put_ledger_record(
                scope.config, clients, _retirement_object_name(scope), updated,
                if_match=etag, if_none_match="*" if etag is None else None,
            )
            return updated
        except Exception as error:
            if getattr(error, "status", None) != 412:
                raise
    raise HarnessError(409, "retirement_registry_busy", "retry the retirement registry update", retryable=True)


def _retirement_snapshot(
    scope: Scope, clients: Clients, profile: ScaleTestProfile, members: Sequence[Any],
    *, request_id: str | None = None, now: datetime | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Separate retiring (including detached-but-terminating) from usable workers.

    Direct operator tag writes are discovered on reconciliation. An existing
    commitment stays retiring even if a tag is later changed out of band; that
    conflict blocks detach, never makes the instance schedulable again.
    """

    persist = request_id is not None and now is not None and not scope.config.dry_run
    registry, _ = _retirement_registry(scope, clients)
    observed: dict[str, Any] = {}
    retiring: dict[str, Any] = {}
    for member in members:
        instance_id = _mapping_value(member, "id")
        instance, _ = _instance(scope, clients, instance_id)
        observed[instance_id] = instance
        tags = _tags(instance)
        if tags.get(PROTECTION_TAG) == "0":
            retiring[instance_id] = instance
            if (
                persist and tags.get(HARNESS_TAG) == scope.config.harness_id
                and tags.get(SCALE_TEST_PROFILE_TAG) == profile.key
            ):
                registry = _write_retirement(scope, clients, instance_id, request_id, now)
    for instance_id, entry in registry["instances"].items():
        if entry["state"] == "terminated":
            if instance_id in observed:
                # A completed identity reappearing in membership is never usable.
                retiring[instance_id] = observed[instance_id]
            continue
        instance = observed.get(instance_id)
        if instance is None:
            try:
                instance, _ = _instance(scope, clients, instance_id)
            except Exception as error:
                if getattr(error, "status", None) == 404:
                    raise HarnessError(
                        409, "retirement_verification_pending",
                        "a committed worker is no longer readable; verify termination before replacement",
                        retryable=True,
                    ) from error
                raise
        if _mapping_value(instance, "lifecycle_state") == "TERMINATED" and instance_id not in observed:
            if persist:
                registry = _write_retirement(scope, clients, instance_id, request_id, now, terminated=True)
            continue
        retiring[instance_id] = instance
    return registry, retiring


def _scale_out_retry_token(request_id: str, retirement_revision: int) -> str:
    # Replaying a completed demand after a later retirement is a NEW OCI launch
    # phase. Reusing its original OCI token could deduplicate that replacement.
    if retirement_revision == 0:
        return request_id
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{request_id}:retirements:{retirement_revision}"))


def _classify_detach_error(error: Exception, *, member_still_attached: bool) -> HarnessError:
    """Disambiguate OCI's intentionally opaque authorization 404 for detach.

    OCI returns ``NotAuthorizedOrNotFound`` for both authorization failures and
    resources the caller cannot see. The detach path has already read the fixed
    pool, instance, and membership authoritatively. If the member remains
    attached after the failed call, report the actionable authorization failure
    without exposing any OCI response detail. A member that disappeared is
    handled by the caller as a successful concurrent detach, while other 404
    codes retain the generic resource-not-found classification.
    """

    if isinstance(error, HarnessError):
        return error
    if (
        member_still_attached
        and getattr(error, "status", None) == 404
        and str(getattr(error, "code", "") or "") == "NotAuthorizedOrNotFound"
    ):
        return HarnessError(
            502,
            "oci_authorization_failed",
            "the Function is not authorized for the targeted detach operation",
        )
    return _service_error(error)


def _pool_converged(scope: Scope, clients: Clients, desired: int) -> tuple[bool, list[Any], int]:
    """Require OCI target, reported current size, and membership to agree."""

    members = _members(scope, clients)
    reported_current = _reported_current_size(scope.pool, len(members))
    complete = (
        _mapping_value(scope.pool, "lifecycle_state") == "RUNNING"
        and _pool_size(scope.pool) == desired
        and reported_current == desired
        and len(members) == desired
        and all(_mapping_value(_instance(scope, clients, _mapping_value(member, "id"))[0], "lifecycle_state") == "RUNNING" for member in members)
    )
    return complete, members, reported_current


def _eligible_scale_in_candidates(
    scope: Scope,
    clients: Clients,
    profile: ScaleTestProfile,
    exclusions: Sequence[str],
    members: Sequence[Any],
) -> list[tuple[str, str]]:
    """Return only running, in-scope members with the exact string tag ``0``.

    Exclusions represent workers the scheduler still considers busy. Missing,
    numeric, boolean, or malformed protection values are not coerced and remain
    protected. Candidate ordering is only a deterministic preference; the
    instance OCID passed to detach is the actual termination decision.
    """

    candidates: list[tuple[str, str]] = []
    excluded = set(exclusions)
    for member in members:
        instance_id = _mapping_value(member, "id")
        if not isinstance(instance_id, str) or instance_id in excluded:
            continue
        instance, _ = _instance(scope, clients, instance_id)
        tags = _tags(instance)
        if (
            tags.get(PROTECTION_TAG) == "0"
            and tags.get(HARNESS_TAG) == scope.config.harness_id
            and tags.get(SCALE_TEST_PROFILE_TAG) == profile.key
            and _mapping_value(instance, "lifecycle_state") == "RUNNING"
        ):
            candidates.append((str(_mapping_value(member, "time_created", "")), instance_id))
    # Newest eligible first makes the proof visibly different from OCI's
    # native oldest-first baseline while the exact tag remains authoritative.
    candidates.sort(reverse=True)
    return candidates


def _superseded_reconcile_result(
    profile: ScaleTestProfile,
    desired: int,
    request_id: str,
    generation: int,
    *,
    detached: Sequence[str] = (),
    work_ids: Sequence[str] = (),
) -> tuple[int, Mapping[str, Any], str]:
    """Produce a terminal result when a newer pool generation has won."""

    return 200, {
        "result": "superseded",
        "outcome": "superseded_after_partial_submit" if detached else "superseded",
        "action": "reconcile_pool",
        "request_id": request_id,
        "pool_key": profile.key,
        "generation": generation,
        "desired_generation": generation,
        "desired_size": desired,
        "detached_instance_ids": list(detached),
        "shortfall": 0,
        "work_request_ids": list(work_ids),
        "retryable": False,
    }, "superseded"


CAPACITY_OBJECT = "coordination/bounded-capacity-v1.json"


def _capacity_identity(config: Config) -> list[list[Any]]:
    # A removed pool must not make its outstanding workers/reservations vanish.
    # Enrollment/shape changes after activation therefore require a quiescent,
    # audited migration, not an automatic reset of this record.
    return [[key, p.pool_id, p.ocpus, p.max_size] for key, p in sorted(config.scale_test_profiles.items())]


def _capacity_record(config: Config, clients: Clients) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        record, etag = _get_ledger_record(config, clients, CAPACITY_OBJECT)
    except Exception as error:
        if getattr(error, "status", None) == 404:
            return None, None
        raise
    pending = record.get("pending")
    sequence = record.get("sequence")
    if (
        record.get("version") != 1 or record.get("harnessId") != config.harness_id
        or record.get("profiles") != _capacity_identity(config)
        or not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0
        or not isinstance(pending, Mapping)
        or any(
            key not in config.scale_test_profiles or not isinstance(value, Mapping)
            or not isinstance(value.get("target"), int) or isinstance(value.get("target"), bool)
            or value["target"] < 1 or value["target"] > config.scale_test_profiles[key].max_size
            or not isinstance(value.get("token"), str) or not UUID_RE.fullmatch(value["token"])
            or not isinstance(value.get("createdAt"), str)
            or not isinstance(value.get("workRequestIds"), list)
            or any(not isinstance(item, str) for item in value["workRequestIds"])
            or ("accepted" in value and not isinstance(value["accepted"], bool))
            for key, value in pending.items()
        )
    ):
        raise HarnessError(409, "capacity_ledger_mismatch", "capacity ledger/configuration mismatch; preserve the ledger and review enrollment with an operator")
    return record, etag


def _put_capacity(config: Config, clients: Clients, record: Mapping[str, Any], etag: str | None) -> str:
    return _put_ledger_record(config, clients, CAPACITY_OBJECT, record, if_match=etag, if_none_match="*" if etag is None else None)


def _capacity_usage(config: Config, clients: Clients, capacity: Mapping[str, Any]) -> dict[str, int]:
    """Count physical/target capacity plus unobserved reservations exactly once.

    Retiring workers, including detached TERMINATING workers, still cost a VM
    and their profile's OCPUs. Missing/ambiguous retirement observations fail
    closed in _retirement_snapshot. Reservations have no expiry-based refund.
    """
    usage = {}
    for key, candidate in config.scale_test_profiles.items():
        scope = _resolve_scale_test_scope(candidate, config, clients)
        members = _members(scope, clients)
        _, retiring = _retirement_snapshot(scope, clients, candidate, members)
        detached = len(set(retiring) - {_mapping_value(member, "id") for member in members})
        reserved = capacity["pending"].get(key, {}).get("target", 0)
        usage[key] = max(_pool_size(scope.pool), _reported_current_size(scope.pool, len(members)), len(members), reserved) + detached
    return usage


def _assert_budget_fence(config: Config, clients: Clients, request_id: str, etag: str, now: datetime) -> None:
    record, current_etag = _get_ledger_record(config, clients, SCALE_TEST_BUDGET_LOCK_OBJECT)
    expiry = _lease_expiry(record)
    if current_etag != etag or record.get("owner") != request_id or expiry is None or expiry <= now:
        raise HarnessError(409, "scale_test_budget_busy", "aggregate capacity lease changed or expired; retry after backoff", retryable=True)


def _bounded_reconcile_once(
    config: Config, clients: Clients, profile: ScaleTestProfile, desired: int,
    exclusions: Sequence[str], request_id: str, generation: int, attempt_id: str,
    now: datetime, clock: Callable[[], datetime] | None,
) -> tuple[int, Mapping[str, Any], str]:
    """Preview protocol: detach one exact worker, terminate separately, grow with headroom.

    Requires exclusive ownership of pool sizing, membership, protection and
    ledger writes. Growth may extend a positively acknowledged target during
    SCALING. Exact detach requires RUNNING (live OCI returns IncorrectState
    during active growth); ambiguous launches never permit another mutation.
    Caller replays drive progress; there is no hidden background worker.
    """
    if not _is_latest_request(config, clients, profile, request_id, generation, desired):
        return _superseded_reconcile_result(profile, desired, request_id, generation)
    lease = _acquire_pool_mutation_lease(config, clients, profile, request_id, attempt_id, generation, desired, _utcnow(clock))
    budget_etag = None
    try:
        budget_etag = _acquire_scale_test_budget_lock(config, clients, request_id, _utcnow(clock))

        def fence() -> None:
            _assert_pool_mutation_fence(config, clients, lease, _utcnow(clock))
            _assert_budget_fence(config, clients, request_id, budget_etag, _utcnow(clock))
            if not _is_latest_request(config, clients, profile, request_id, generation, desired):
                raise HarnessError(409, "request_superseded", "a newer demand generation owns the pool", retryable=True)

        capacity, capacity_etag = _capacity_record(config, clients)
        if capacity is None:
            capacity = {"version": 1, "harnessId": config.harness_id, "profiles": _capacity_identity(config), "sequence": 0, "pending": {}}
            if not config.dry_run:
                fence()
                capacity_etag = _put_capacity(config, clients, capacity, None)
        scope = _resolve_scale_test_scope(profile, config, clients)
        _assert_autoscaling_removed(scope, clients)
        current = _pool_size(scope.pool)
        converged, members, reported = _pool_converged(scope, clients, desired)
        _, retiring = _retirement_snapshot(scope, clients, profile, members, request_id=request_id, now=now)
        member_ids = {_mapping_value(member, "id") for member in members}
        usable = len(member_ids - set(retiring))
        base = {
            "action": "reconcile_pool", "request_id": request_id, "pool_key": profile.key,
            "pool_id": scope.pool_id, "generation": generation, "desired_generation": generation,
            "desired_size": desired, "desired_usable_size": desired, "size_before": current,
            "observed_current_size": reported, "observed_attached_size": len(members),
            "observed_usable_size": usable, "retiring_instance_ids": sorted(retiring),
            "retiring_count": len(retiring), "replacement_policy": "bounded_growth_preview",
            "readiness_scope": "infrastructure_only", "scheduler_ready": None,
            "detached_instance_ids": [], "work_request_ids": [], "target_size": desired,
            "max_total_vms": config.max_total_vms,
        }

        def result(outcome: str, *, complete: bool = False, **extra: Any) -> tuple[int, Mapping[str, Any], str]:
            state = "completed" if complete else "submitted"
            if extra.get("intervention_required"):
                LOG.warning("Controller intervention required: %s", outcome)
            if "work_request_ids" in extra:
                extra["work_request_ids"] = list(dict.fromkeys(base["work_request_ids"] + extra["work_request_ids"]))
            return (200 if complete else 202), {**base, "result": "dry_run" if config.dry_run else state,
                "outcome": outcome, "size_after": reported, "shortfall": max(0, desired - usable),
                "retryable": not complete, **extra}, state

        # Cleanup can progress even when a launch's outcome is unresolved.
        if not config.dry_run:
            base["work_request_ids"] = _advance_detached_retirement(config, clients, scope, profile, retiring, member_ids, exclusions, fence)
        pending = capacity["pending"].get(profile.key)
        extending = False
        if pending:
            # Re-observation, never a time-based refund. Do not issue a second
            # update while acceptance of the first is unknown, even on new demand.
            statuses = []
            error_codes = []
            if clients.work_requests is not None:
                for work_id in pending["workRequestIds"]:
                    try:
                        work_status = _mapping_value(_mapping_value(clients.work_requests.get_work_request(work_id), "data"), "status")
                    except Exception as error:
                        converted = _service_error(error)
                        return result("launch_diagnostics_unavailable", work_request_ids=pending["workRequestIds"],
                                      reserved_target=pending["target"], pending_reason=converted.reason,
                                      intervention_required=True, retryable=False)
                    statuses.append(work_status)
                    if work_status == "FAILED":
                        try:
                            errors = clients.work_requests.list_work_request_errors(work_id, limit=20)
                        except Exception:
                            # Keep the failure verdict even if detailed errors
                            # have expired or the diagnostic grant is missing.
                            error_codes.append("DiagnosticsUnavailable")
                            continue
                        for item in _mapping_value(errors, "data", []):
                            code = _mapping_value(item, "code", "Unknown")
                            # Error messages may include customer data. Return
                            # only a bounded code; full details stay in OCI.
                            error_codes.append(code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else "Unknown")
            if any(value in {"FAILED", "CANCELED", "CANCELLED"} for value in statuses):
                return result("launch_failed", work_request_ids=pending["workRequestIds"], intervention_required=True, retryable=False,
                              pending_reason="inspect_oci_work_request_errors", work_request_error_codes=error_codes, reserved_target=pending["target"])
            observed, _, _ = _pool_converged(scope, clients, pending["target"])
            if observed and (not statuses or all(value == "SUCCEEDED" for value in statuses)):
                capacity = {**capacity, "pending": {key: value for key, value in capacity["pending"].items() if key != profile.key}}
                if not config.dry_run:
                    fence()
                    capacity_etag = _put_capacity(config, clients, capacity, capacity_etag)
            else:
                try:
                    age = (now - datetime.fromisoformat(pending["createdAt"].replace("Z", "+00:00"))).total_seconds()
                except (ValueError, TypeError):
                    raise HarnessError(502, "invalid_ledger_record", "invalid launch reservation timestamp")
                overdue = age >= config.launch_timeout_seconds
                # A successful API response is different from an unknown
                # outcome. Its unmaterialized slots already satisfy demand;
                # reserve only a monotonic increment above that target. Old
                # ledger records lacking the marker remain conservative.
                planned_usable = current - len(member_ids & set(retiring))
                extending = (
                    not overdue and pending.get("accepted") is True
                    and current == pending["target"] and len(member_ids) <= current
                    and desired > planned_usable
                    and _mapping_value(scope.pool, "lifecycle_state") in {"RUNNING", "SCALING"}
                )
                if not extending:
                    return result("launch_verification_required" if overdue else "launch_pending",
                                  reserved_target=pending["target"], work_request_ids=pending["workRequestIds"],
                                  intervention_required=overdue, retryable=not overdue)

        if converged and not retiring:
            return result("already_at_target", complete=True)
        if config.dry_run:
            usage = _capacity_usage(config, clients, capacity)
            return result("bounded_growth_preview_only", complete=True, counted_vms=sum(usage.values()),
                          counted_ocpus=sum(usage[key] * config.scale_test_profiles[key].ocpus for key in usage))

        scope = _resolve_scale_test_scope(profile, config, clients)
        if _mapping_value(scope.pool, "lifecycle_state") not in {"RUNNING", "SCALING"}:
            return result("pool_busy", pending_reason="pool_lifecycle_not_mutable")
        # OCI can report RUNNING and a decremented target before the member
        # list drops the detached identity. Mixing those observations creates
        # a false deficit (and an unnecessary replacement during scale-down).
        # A short list is allowed only for our acknowledged pending growth:
        # its reserved target, not just materialized members, satisfies demand.
        fresh_member_ids = {_mapping_value(member, "id") for member in _members(scope, clients)}
        if (_pool_size(scope.pool) != current or fresh_member_ids != member_ids
                or (len(member_ids) != current and not extending)):
            return result("pool_state_changed")

        # Target means usable capacity. Attached commitments remain physical
        # capacity but must not satisfy fresh demand. Launch replacements first
        # when there is headroom; otherwise release one pool slot below.
        planned_usable = current - len(member_ids & set(retiring)) if extending else usable
        deficit = desired - planned_usable
        if deficit > 0:
            matches, message = _scale_test_configuration_check(scope, profile, clients)
            if not matches:
                raise HarnessError(409, "instance_configuration_mismatch", message)
            usage = _capacity_usage(config, clients, capacity)
            total_vms = sum(usage.values())
            total_ocpus = sum(usage[key] * config.scale_test_profiles[key].ocpus for key in usage)
            active = sum(count > 0 for count in usage.values())
            slots = max(0, min(deficit, profile.max_size - usage[profile.key],
                               config.max_total_vms - total_vms,
                               (config.max_scale_test_total_ocpus - total_ocpus) // profile.ocpus))
            if not usage[profile.key] and active >= config.max_active_scale_test_profiles:
                slots = 0
            base.update(counted_vms=total_vms, counted_ocpus=total_ocpus)
            if not slots and (extending or not (member_ids & set(retiring))):
                return result("capacity_pending", pending_reason="configured_capacity_guard")
            if not slots:
                return _bounded_detach_one(config, clients, scope, profile, exclusions, request_id, now, fence, result)
            target = current + slots
            sequence = capacity["sequence"] + 1
            previous_pending = capacity["pending"].get(profile.key)
            prior_work_ids = previous_pending["workRequestIds"] if previous_pending else []
            pending = {"target": target, "requestId": request_id, "generation": generation,
                       "token": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{config.harness_id}:{scope.pool_id}:{sequence}:{request_id}")),
                       "createdAt": previous_pending["createdAt"] if previous_pending else _timestamp(now),
                       "workRequestIds": list(prior_work_ids), "accepted": False}
            capacity = {**capacity, "sequence": sequence, "pending": {**capacity["pending"], profile.key: pending}}
            fence()
            # Global CAS makes competing reservations conflict even if a lease
            # expires during a slow capacity scan. Persist BEFORE the OCI call.
            capacity_etag = _put_capacity(config, clients, capacity, capacity_etag)
            fresh = _resolve_scale_test_scope(profile, config, clients)
            if (_pool_size(fresh.pool) != current
                    or _mapping_value(fresh.pool, "lifecycle_state") not in {"RUNNING", "SCALING"}
                    or {_mapping_value(member, "id") for member in _members(fresh, clients)} != member_ids):
                # No API submission has happened: this increment is known
                # unsent, unlike a lost response. Preserve the prior launch.
                restored = {**capacity, "pending": {key: value for key, value in capacity["pending"].items() if key != profile.key}}
                if previous_pending is not None:
                    restored["pending"][profile.key] = previous_pending
                fence()
                _put_capacity(config, clients, restored, capacity_etag)
                return result("pool_state_changed")
            fence()
            try:
                response = clients.pools.update_instance_pool(fresh.pool_id, _model("UpdateInstancePoolDetails", size=target),
                                                             if_match=fresh.pool_etag, opc_retry_token=pending["token"])
            except Exception as error:
                converted = _service_error(error)
                # Refund only explicit pre-admission rejections. Transport/5xx
                # and unclassified failures retain their reservation indefinitely.
                if getattr(error, "status", None) in {400, 401, 403, 404, 409, 412, 429}:
                    cleared = {**capacity, "pending": {key: value for key, value in capacity["pending"].items() if key != profile.key}}
                    if previous_pending is not None:
                        cleared["pending"][profile.key] = previous_pending
                    _put_capacity(config, clients, cleared, capacity_etag)
                    raise converted from error
                return result("scale_out_outcome_unknown", target_size=target, reserved_target=target,
                              pending_reason=converted.reason)
            work_ids = list(dict.fromkeys([*prior_work_ids, *[value for value in (_work_request_id(response),) if value]]))
            capacity["pending"][profile.key] = {**pending, "workRequestIds": work_ids, "accepted": True}
            _put_capacity(config, clients, capacity, capacity_etag)
            return result("scale_out_submitted", target_size=target, reserved_target=target, work_request_ids=work_ids)

        if retiring or current > desired:
            if not config.enable_termination:
                return result("termination_disabled", intervention_required=True, retryable=False)
            return _bounded_detach_one(config, clients, scope, profile, exclusions, request_id, now, fence, result)
        return result("convergence_pending")
    finally:
        if budget_etag is not None:
            _release_scale_test_budget_lock(config, clients, request_id, budget_etag)
        _release_pool_mutation_lease(config, clients, lease)


def _advance_detached_retirement(config, clients, scope, profile, retiring, member_ids, exclusions, fence):
    """Advance one committed detached identity; scope, tag and ETag fail closed."""
    if not config.enable_termination:
        return []
    for instance_id in sorted(set(retiring) - member_ids):
        if instance_id in exclusions:
            continue
        instance, instance_etag = _instance(scope, clients, instance_id)
        if _mapping_value(instance, "lifecycle_state") in {"TERMINATING", "TERMINATED"}:
            continue
        tags = _tags(instance)
        if tags.get(PROTECTION_TAG) != "0" or tags.get(HARNESS_TAG) != config.harness_id or tags.get(SCALE_TEST_PROFILE_TAG) != profile.key:
            continue
        if _member_by_id(_members(scope, clients), instance_id) is not None:
            continue
        fence()
        response = clients.compute.terminate_instance(instance_id, preserve_boot_volume=False, if_match=instance_etag)
        return [value for value in (_work_request_id(response),) if value]
    return []


def _bounded_detach_one(config, clients, scope, profile, exclusions, request_id, now, fence, result):
    """One exact irreversible retirement per replay; never bulk shrink."""
    if not config.enable_termination:
        return result("termination_disabled", intervention_required=True, retryable=False)
    if _mapping_value(scope.pool, "lifecycle_state") != "RUNNING":
        return result("pool_busy", pending_reason="detach_requires_running")
    candidates = _eligible_scale_in_candidates(scope, clients, profile, exclusions, _members(scope, clients))
    if candidates:
        instance_id = candidates[0][1]
        registry = _write_retirement(scope, clients, instance_id, request_id, now)
        # Protection is not an atomic precondition of OCI detach. Exclusive
        # tag/membership writers remain mandatory despite this final re-read.
        fresh = _resolve_scale_test_scope(profile, config, clients)
        _assert_autoscaling_removed(fresh, clients)
        candidates = _eligible_scale_in_candidates(fresh, clients, profile, exclusions, _members(fresh, clients))
        if _mapping_value(fresh.pool, "lifecycle_state") == "RUNNING" and _pool_size(fresh.pool) > 0 and instance_id in {value for _, value in candidates}:
            fence()
            response = _detach(fresh, clients, instance_id, registry["instances"][instance_id]["detachRetryToken"], auto_terminate=False)
            return result("retirement_detached", detached_instance_ids=[instance_id],
                          work_request_ids=[value for value in (_work_request_id(response),) if value])
    return result("retirement_pending")


def _reconcile_once(
    config: Config,
    clients: Clients,
    profile: ScaleTestProfile,
    desired: int,
    exclusions: Sequence[str],
    request_id: str,
    generation: int,
    attempt_id: str,
    now: datetime,
    previous_state: str | None,
    clock: Callable[[], datetime] | None,
) -> tuple[int, Mapping[str, Any], str]:
    """Advance one latest desired-state request by at most one safe phase.

    The function is intentionally caller-driven rather than a background loop:
    it observes convergence, submits one OCI resize or a bounded set of exact
    detaches, stores the result, and returns. A nonterminal response tells the
    caller to replay the same request ID and payload after backoff.

    Retirements finish first regardless of demand direction. Subsequent
    scale-out uses a generic *larger* absolute pool target. Scale-in never uses
    a generic smaller target; it detaches exact eligible OCIDs with pool
    decrement and auto-termination. Before every OCI mutation, pool state,
    membership, protection, provenance, the lease fence, and latest generation
    are re-read so stale invocations fail closed.
    """

    if config.bounded_growth:
        return _bounded_reconcile_once(config, clients, profile, desired, exclusions, request_id, generation, attempt_id, now, clock)
    if config.controller_only and not config.dry_run:
        # Once the new protocol owns this ledger, a configuration rollback may
        # not ignore unresolved launch reservations or detached workers.
        capacity, _ = _capacity_record(config, clients)
        if capacity is not None:
            raise HarnessError(409, "capacity_protocol_required", "this ledger uses bounded growth; do not disable the protocol or roll back its image without an operator migration")
    if not _is_latest_request(config, clients, profile, request_id, generation, desired):
        return _superseded_reconcile_result(profile, desired, request_id, generation)

    scope = _resolve_scale_test_scope(profile, config, clients)
    _assert_autoscaling_removed(scope, clients)
    current = _pool_size(scope.pool)
    converged, members, reported_current = _pool_converged(scope, clients, desired)
    registry, retiring = _retirement_snapshot(
        scope, clients, profile, members, request_id=request_id, now=now,
    )
    converged = converged and not retiring
    retiring_attached = sum(_mapping_value(member, "id") in retiring for member in members)
    base = {
        "action": "reconcile_pool",
        "request_id": request_id,
        "pool_key": profile.key,
        "pool_id": scope.pool_id,
        "generation": generation,
        "desired_generation": generation,
        "desired_size": desired,
        "size_before": current,
        "observed_current_size": reported_current,
        "observed_attached_size": len(members),
        "excluded_instance_ids": list(exclusions),
        "desired_usable_size": desired,
        "observed_usable_size": len(members) - retiring_attached,
        "retiring_instance_ids": sorted(retiring),
        "retiring_count": len(retiring),
        "replacement_policy": "retire_first",
        "readiness_scope": "infrastructure_only",
        "scheduler_ready": None,
    }

    # No-op/status polling is intentionally lock-free. This is what allows a
    # 60-request zero-target burst to exercise the durable intake path without
    # serializing on mutation leases or touching Compute.
    if current == desired and not retiring:
        if converged:
            return 200, {
                **base,
                "result": "completed",
                "outcome": "already_at_target",
                "size_after": current,
                "detached_instance_ids": [],
                "shortfall": 0,
                "work_request_ids": [],
            }, "completed"
        pending_state = "submitted" if previous_state == "submitted" else "queued"
        return 202, {
            **base,
            "result": pending_state,
            "outcome": "convergence_pending",
            "size_after": reported_current,
            "target_size": desired,
            "detached_instance_ids": [],
            "shortfall": max(0, desired - len(members)),
            "work_request_ids": [],
            "retryable": True,
        }, pending_state

    lifecycle = _mapping_value(scope.pool, "lifecycle_state")
    if lifecycle != "RUNNING":
        raise HarnessError(
            409,
            "pool_busy",
            "the pool is not RUNNING; replay the latest absolute target after backoff",
            retryable=True,
        )

    if config.dry_run:
        if desired > current and not retiring:
            configuration_matches, configuration_message = _scale_test_configuration_check(scope, profile, clients)
            if not configuration_matches:
                raise HarnessError(409, "instance_configuration_mismatch", configuration_message)
            _enforce_scale_test_budget(profile, desired, config, clients)
            return 200, {
                **base,
                "result": "dry_run",
                "outcome": "scale_out_validated",
                "size_after": current,
                "target_size": desired,
                "detached_instance_ids": [],
                "shortfall": 0,
                "work_request_ids": [],
            }, "completed"
        candidates = _eligible_scale_in_candidates(scope, clients, profile, exclusions, members)
        decrement = max(current - desired, len(retiring))
        selected = [instance_id for _, instance_id in candidates[:decrement]]
        return 200, {
            **base,
            "result": "dry_run",
            "outcome": "scale_in_validated",
            "size_after": current,
            "target_size": desired,
            "detached_instance_ids": selected,
            "shortfall": max(0, decrement - len(selected)),
            "work_request_ids": [],
        }, "completed"

    # Disable destructive submissions without deleting retirement commitments.
    # Tag-0 can still be committed while this switch is off; an operator must
    # re-enable termination and submit a fresh demand generation after review.
    if (retiring or current > desired) and not config.enable_termination:
        raise HarnessError(409, "termination_disabled", "the termination kill switch is disabled")

    try:
        lease = _acquire_pool_mutation_lease(
            config,
            clients,
            profile,
            request_id,
            attempt_id,
            generation,
            desired,
            _utcnow(clock),
        )
    except HarnessError as error:
        if error.reason == "request_superseded":
            return _superseded_reconcile_result(profile, desired, request_id, generation)
        raise
    budget_etag: str | None = None
    try:
        budget_etag = _acquire_scale_test_budget_lock(config, clients, request_id, _utcnow(clock))
        if not _is_latest_request(config, clients, profile, request_id, generation, desired):
            return _superseded_reconcile_result(profile, desired, request_id, generation)

        # Everything that determines direction is re-read after both leases.
        scope = _resolve_scale_test_scope(profile, config, clients)
        _assert_autoscaling_removed(scope, clients)
        current = _pool_size(scope.pool)
        converged, members, reported_current = _pool_converged(scope, clients, desired)
        registry, retiring = _retirement_snapshot(
            scope, clients, profile, members, request_id=request_id, now=now,
        )
        converged = converged and not retiring
        retiring_attached = sum(_mapping_value(member, "id") in retiring for member in members)
        base = {
            **base,
            "pool_id": scope.pool_id,
            "size_before": current,
            "observed_current_size": reported_current,
            "observed_attached_size": len(members),
            "observed_usable_size": len(members) - retiring_attached,
            "retiring_instance_ids": sorted(retiring),
            "retiring_count": len(retiring),
        }
        if current == desired and not retiring:
            state = "completed" if converged else ("submitted" if previous_state == "submitted" else "queued")
            return (200 if converged else 202), {
                **base,
                "result": state,
                "outcome": "already_at_target" if converged else "convergence_pending",
                "size_after": reported_current,
                "target_size": desired,
                "detached_instance_ids": [],
                "shortfall": max(0, desired - len(members)),
                "work_request_ids": [],
                "retryable": not converged,
            }, state
        if _mapping_value(scope.pool, "lifecycle_state") != "RUNNING":
            raise HarnessError(
                409,
                "pool_busy",
                "the pool changed state before mutation; replay the latest target after backoff",
                retryable=True,
            )

        if desired > current and not retiring:
            configuration_matches, configuration_message = _scale_test_configuration_check(scope, profile, clients)
            if not configuration_matches:
                raise HarnessError(409, "instance_configuration_mismatch", configuration_message)
            _enforce_scale_test_budget(profile, desired, config, clients)

            # Refresh pool identity, lifecycle, and ETag first, then make the
            # Object Storage fence and queue generation the final two reads.
            fresh_scope = _resolve_scale_test_scope(profile, config, clients)
            _assert_autoscaling_removed(fresh_scope, clients)
            if (
                _mapping_value(fresh_scope.pool, "lifecycle_state") != "RUNNING"
                or _pool_size(fresh_scope.pool) != current
            ):
                raise HarnessError(
                    409,
                    "pool_state_changed",
                    "the pool target or lifecycle changed before update; replay after backoff",
                    retryable=True,
                )
            _assert_pool_mutation_fence(config, clients, lease, _utcnow(clock))
            if not _is_latest_request(config, clients, profile, request_id, generation, desired):
                return _superseded_reconcile_result(profile, desired, request_id, generation)
            try:
                # Scale-out is the only path allowed to call UpdateInstancePool.
                # The absolute target plus stable request token makes replay
                # safe when the caller did not receive OCI's first response.
                response = clients.pools.update_instance_pool(
                    fresh_scope.pool_id,
                    _model("UpdateInstancePoolDetails", size=desired),
                    if_match=fresh_scope.pool_etag,
                    opc_retry_token=_scale_out_retry_token(request_id, registry["revision"]),
                )
            except Exception as error:
                converted = _service_error(error)
                if converted.retryable and converted.reason == "oci_transient_error":
                    return 202, {
                        **base,
                        "result": "submitted",
                        "outcome": "scale_out_outcome_unknown",
                        "size_after": reported_current,
                        "target_size": desired,
                        "detached_instance_ids": [],
                        "shortfall": max(0, desired - len(members)),
                        "work_request_ids": [],
                        "pending_reason": converted.reason,
                        "retryable": True,
                    }, "submitted"
                raise
            work_ids = [value for value in (_work_request_id(response),) if value]
            return 202, {
                **base,
                "result": "submitted",
                "outcome": "scale_out_submitted",
                # OCI accepted a target change; current/member convergence is
                # intentionally not claimed until a later caller replay sees it.
                "size_after": reported_current,
                "target_size": desired,
                "detached_instance_ids": [],
                "shortfall": max(0, desired - len(members)),
                "work_request_ids": work_ids,
                "opc_request_id": _opc_request_id(response),
                "retryable": True,
            }, "submitted"

        # Every committed retirement must finish, even if demand just increased
        # or already equals the physical pool size. Never cap retirements by
        # (current - desired): that would resurrect retired capacity. Exclusions
        # and changed protection still block unsafe detach, but not commitment.
        decrement = max(current - desired, len(retiring))
        candidates = _eligible_scale_in_candidates(scope, clients, profile, exclusions, members)
        detached: list[str] = []
        work_ids: list[str] = []
        for _, instance_id in candidates:
            if len(detached) >= decrement:
                break

            # Demand generations choose which invocation drives reconciliation;
            # they never erase worker commitments. A successor invocation will
            # continue them with the same per-worker OCI token.
            fresh_scope = _resolve_scale_test_scope(profile, config, clients)
            _assert_autoscaling_removed(fresh_scope, clients)
            fresh_members = _members(fresh_scope, clients)
            fresh_target = _pool_size(fresh_scope.pool)
            if fresh_target <= 0 or not fresh_members:
                break
            if _mapping_value(fresh_scope.pool, "lifecycle_state") != "RUNNING":
                break
            if _member_by_id(fresh_members, instance_id) is None:
                continue
            authoritative, _ = _instance(fresh_scope, clients, instance_id)
            tags = _tags(authoritative)
            if (
                tags.get(PROTECTION_TAG) != "0"
                or tags.get(HARNESS_TAG) != config.harness_id
                or tags.get(SCALE_TEST_PROFILE_TAG) != profile.key
                or _mapping_value(authoritative, "lifecycle_state") != "RUNNING"
            ):
                continue
            if _member_by_id(_members(fresh_scope, clients), instance_id) is None:
                continue
            registry = _write_retirement(fresh_scope, clients, instance_id, request_id, now)
            _assert_pool_mutation_fence(config, clients, lease, _utcnow(clock))
            if not _is_latest_request(config, clients, profile, request_id, generation, desired):
                return _superseded_reconcile_result(
                    profile,
                    desired,
                    request_id,
                    generation,
                    detached=detached,
                    work_ids=work_ids,
                )
            try:
                # decrement=true releases the pool slot; auto_terminate=true
                # destroys this exact worker instead of merely detaching it.
                response = _detach(
                    fresh_scope,
                    clients,
                    instance_id,
                    registry["instances"][instance_id]["detachRetryToken"],
                    auto_terminate=True,
                )
            except Exception as error:
                member_still_attached = True
                if getattr(error, "status", None) in {404, 409}:
                    member_still_attached = _member_by_id(
                        _members(fresh_scope, clients), instance_id
                    ) is not None
                    if not member_still_attached:
                        continue
                converted = _classify_detach_error(
                    error,
                    member_still_attached=member_still_attached,
                )
                if converted.reason == "oci_authorization_failed":
                    raise converted from error
                # After a timeout, OCI may have accepted the detach even though
                # no response arrived. Preserve submitted state and let replay
                # re-read membership instead of risking a second decrement.
                if detached or (
                    converted.retryable and converted.reason == "oci_transient_error"
                ):
                    return 202, {
                        **base,
                        "result": "submitted",
                        "outcome": "scale_in_outcome_unknown" if not detached else "scale_in_partial",
                        "size_after": max(0, current - len(detached)),
                        "target_size": desired,
                        "detached_instance_ids": detached,
                        "shortfall": max(0, decrement - len(detached)),
                        "work_request_ids": work_ids,
                        "uncertain_instance_id": instance_id,
                        "pending_reason": converted.reason,
                        "retryable": True,
                    }, "submitted"
                raise converted from error
            detached.append(instance_id)
            work_id = _work_request_id(response)
            if work_id and work_id not in work_ids:
                work_ids.append(work_id)

        shortfall = max(0, decrement - len(detached))
        if detached:
            return 202, {
                **base,
                "result": "submitted",
                "outcome": "scale_in_submitted" if shortfall == 0 else "scale_in_partial",
                "size_after": max(0, current - len(detached)),
                "target_size": desired,
                "detached_instance_ids": detached,
                "shortfall": shortfall,
                "work_request_ids": work_ids,
                "retryable": True,
            }, "submitted"
        return 202, {
            **base,
            "result": "queued",
            "outcome": "retirement_pending" if retiring else "awaiting_eligible_instances",
            "size_after": current,
            "target_size": desired,
            "detached_instance_ids": [],
            "shortfall": decrement,
            "work_request_ids": [],
            "retryable": True,
        }, "queued"
    finally:
        if budget_etag is not None:
            _release_scale_test_budget_lock(config, clients, request_id, budget_etag)
        _release_pool_mutation_lease(config, clients, lease)


def _store_request_result(
    config: Config,
    clients: Clients,
    record: Mapping[str, Any],
    etag: str,
    status: int,
    body: Mapping[str, Any],
    now: datetime,
    *,
    state: str,
) -> None:
    """Persist the observable result and release the request-processing lease."""

    if state not in {"queued", "submitted", "completed", "superseded", "failed"}:
        raise HarnessError(500, "invalid_request_state", "the reconciler produced an invalid durable state")
    updated = dict(record)
    updated.update(
        {
            "state": state,
            "updatedAt": _timestamp(now),
            "leaseUntil": None,
            "httpStatus": status,
            "response": dict(body),
        }
    )
    try:
        _put_ledger_record(
            config,
            clients,
            _request_object_name(str(record["requestId"])),
            updated,
            if_match=etag,
        )
    except Exception as error:
        if getattr(error, "status", None) == 412:
            raise HarnessError(
                409,
                "request_state_changed",
                "the durable request record changed concurrently",
                retryable=True,
            ) from error
        raise


def _merge_request_history(
    record: Mapping[str, Any],
    body: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Carry accepted detach/work-request evidence across caller replays."""

    merged = dict(body)
    previous = record.get("response")
    if not isinstance(previous, Mapping):
        return merged
    for field in ("detached_instance_ids", "work_request_ids"):
        combined: list[str] = []
        for source in (previous.get(field), body.get(field)):
            if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
                continue
            for value in source:
                if isinstance(value, str) and value not in combined:
                    combined.append(value)
        if combined:
            merged[field] = combined
    return merged


def _reconcile_pool(
    payload: Mapping[str, Any],
    config: Config,
    clients: Clients,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> tuple[int, Mapping[str, Any]]:
    """Durable API boundary for absolute desired-count reconciliation.

    Processing order is: validate and fingerprint, claim the request record,
    publish the latest pool generation, advance reconciliation once, then store
    the response. Terminal same-ID replays return the stored response unless
    the latest completed demand needs maintenance after retirement or drift.
    Active same-ID replays return ``in_progress``; changed same-ID payloads are
    rejected before any OCI mutation.
    """

    request_id = _reconcile_request_id(payload)
    profile = _reconcile_profile(payload, config)
    desired = _reconcile_desired(payload, profile)
    exclusions = _reconcile_exclusions(payload)
    desired_generation = _reconcile_desired_generation(payload)
    if config.controller_only and desired_generation is None:
        raise HarnessError(400, "invalid_desired_generation", "controller-only requests require desired_generation")
    fingerprint = _request_fingerprint(profile, desired, exclusions, desired_generation)
    request_value = {
        "poolKey": profile.key,
        "desiredSize": desired,
        "excludeInstanceIds": list(exclusions),
    }
    if desired_generation is not None:
        request_value["desiredGeneration"] = desired_generation
    record, etag, owns_claim, previous_state = _claim_request(
        config,
        clients,
        request_id,
        fingerprint,
        request_value,
        now,
    )
    # A completed demand is not a permanent assertion that the pool will never
    # change. Replaying the LATEST completed request is also the scheduler's
    # maintenance tick: later irreversible retirements can require replacements
    # without changing the desired count or minting a fake demand generation.
    # Superseded requests still replay verbatim and can never mutate OCI again.
    generation = record.get("generation")
    if (
        record.get("state") == "completed"
        and isinstance(generation, int) and not isinstance(generation, bool)
        and not config.dry_run
        and _is_latest_request(config, clients, profile, request_id, generation, desired)
    ):
        scope = _resolve_scale_test_scope(profile, config, clients)
        converged, members, _ = _pool_converged(scope, clients, desired)
        _, retiring = _retirement_snapshot(scope, clients, profile, members)
        if not converged or retiring:
            record, etag, owns_claim, previous_state = _claim_request(
                config, clients, request_id, fingerprint, request_value, now, resume_completed=True,
            )
    if record.get("state") in {"completed", "superseded", "failed"}:
        stored_status = record.get("httpStatus", 200)
        stored_body = record.get("response", {})
        if isinstance(stored_status, int) and isinstance(stored_body, Mapping):
            replay = dict(stored_body)
            replay.setdefault("request_state", record.get("state"))
            replay["idempotent_replay"] = True
            return stored_status, replay
        raise HarnessError(502, "invalid_ledger_record", "the completed request record is invalid")
    if not owns_claim:
        return 202, {
            "result": "accepted",
            "outcome": "in_progress",
            "action": "reconcile_pool",
            "request_id": request_id,
            "pool_key": profile.key,
            "desired_size": desired,
            "desired_generation": desired_generation,
            "request_state": "processing",
            "retryable": True,
        }

    generation = record.get("generation")
    try:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            generation = _register_latest_request(
                config,
                clients,
                profile,
                request_id,
                desired,
                desired_generation,
                now,
            )
            updated_record = dict(record)
            updated_record["generation"] = generation
            if desired_generation is not None:
                updated_record["desiredGeneration"] = desired_generation
            etag = _put_ledger_record(
                config,
                clients,
                _request_object_name(request_id),
                updated_record,
                if_match=etag,
            )
            record = updated_record
        elif desired_generation is not None and generation != desired_generation:
            raise HarnessError(
                502,
                "invalid_ledger_record",
                "the durable request generation does not match desired_generation",
            )

        attempt_id = record.get("attemptId")
        if not isinstance(attempt_id, str) or not UUID_RE.fullmatch(attempt_id):
            raise HarnessError(502, "invalid_ledger_record", "the durable request attempt ID is invalid")
        status, body, durable_state = _reconcile_once(
            config,
            clients,
            profile,
            desired,
            exclusions,
            request_id,
            generation,
            attempt_id,
            now,
            previous_state,
            clock,
        )
    except HarnessError as error:
        if error.reason == "request_superseded":
            # Losing to newer demand is a normal terminal outcome, including
            # races after the initial latest-generation check. Preserve prior
            # submissions; supersession never cancels or refunds their work.
            status, body, durable_state = _superseded_reconcile_result(profile, desired, request_id, generation)
            response_body = dict(_merge_request_history(record, {**body, "request_state": durable_state}))
            _store_request_result(config, clients, record, etag, status, response_body, now, state=durable_state)
            return status, response_body
        durable_state = (
            "submitted"
            if error.retryable and previous_state == "submitted"
            else ("queued" if error.retryable else "failed")
        )
        effective_generation = generation if isinstance(generation, int) and not isinstance(generation, bool) else desired_generation
        rejected = {
            "result": durable_state,
            "outcome": error.reason,
            "action": "reconcile_pool",
            "request_id": request_id,
            "pool_key": profile.key,
            "desired_size": desired,
            "desired_generation": effective_generation,
            "message": error.message,
            "retryable": error.retryable,
            "request_state": durable_state,
        }
        response_status = 202 if error.retryable else error.status
        rejected = dict(_merge_request_history(record, rejected))
        _store_request_result(
            config,
            clients,
            record,
            etag,
            response_status,
            rejected,
            now,
            state=durable_state,
        )
        return response_status, rejected
    except Exception as error:
        converted = _service_error(error)
        # Record bounded diagnostic metadata, never SDK messages/headers,
        # request payloads or credential-bearing exception representations.
        LOG.error("Reconcile exception type=%s classification=%s",
                  type(error).__name__, converted.reason)
        durable_state = (
            "submitted"
            if converted.retryable and previous_state == "submitted"
            else ("queued" if converted.retryable else "failed")
        )
        effective_generation = generation if isinstance(generation, int) and not isinstance(generation, bool) else desired_generation
        failed = {
            "result": durable_state,
            "outcome": converted.reason,
            "action": "reconcile_pool",
            "request_id": request_id,
            "pool_key": profile.key,
            "desired_size": desired,
            "desired_generation": effective_generation,
            "message": converted.message,
            "retryable": converted.retryable,
            "request_state": durable_state,
        }
        response_status = 202 if converted.retryable else converted.status
        failed = dict(_merge_request_history(record, failed))
        _store_request_result(
            config,
            clients,
            record,
            etag,
            response_status,
            failed,
            now,
            state=durable_state,
        )
        return response_status, failed

    response_body = dict(body)
    response_body["request_state"] = durable_state
    response_body = dict(_merge_request_history(record, response_body))
    _store_request_result(config, clients, record, etag, status, response_body, now, state=durable_state)
    return status, response_body


def _request_status(
    payload: Mapping[str, Any],
    config: Config,
    clients: Clients,
) -> tuple[int, Mapping[str, Any]]:
    """Read a durable request result without advancing reconciliation.

    The caller must replay the original mutation for queued/submitted work;
    polling this endpoint alone never retries an OCI operation.
    """

    request_id = _reconcile_request_id(payload)
    record, _ = _get_ledger_record(config, clients, _request_object_name(request_id))
    response = record.get("response")
    body = dict(response) if isinstance(response, Mapping) else {}
    request_value = record.get("request")
    requested_generation = (
        request_value.get("desiredGeneration") if isinstance(request_value, Mapping) else None
    )
    body.update(
        {
            "result": body.get("result", record.get("state", "unknown")),
            "action": "request_status",
            "request_id": request_id,
            "request_state": record.get("state"),
            "created_at": record.get("createdAt"),
            "updated_at": record.get("updatedAt"),
            "lease_until": record.get("leaseUntil"),
            "generation": record.get("generation"),
            "desired_generation": record.get(
                "desiredGeneration",
                requested_generation if requested_generation is not None else record.get("generation"),
            ),
        }
    )
    return 200, body


def _set_pool_protection(
    payload: Mapping[str, Any],
    config: Config,
    clients: Clients,
    now: datetime,
) -> tuple[int, Mapping[str, Any]]:
    """Commit irreversible retirement with exact ``"0"``; protect only new workers.

    Marking commits a durable retirement; a reconcile/maintenance call advances
    it independently of the desired size. It does not itself resize the pool. The
    instance must still belong to the allowlisted pool and carry matching
    harness/profile provenance; the write uses a fresh ETag and is read back.
    """

    request_id = _reconcile_request_id(payload)
    profile = _reconcile_profile(payload, config)
    instance_id = _request_field(payload, "instance_id", "instanceId")
    instance_id = _validate_ocid(instance_id, "instance", "instance_id")
    raw_value = _request_field(payload, "tag_value", "tagValue")
    if not isinstance(raw_value, str) or raw_value not in {"0", "1"}:
        raise HarnessError(400, "invalid_protection_value", "tag_value must be exactly the string 0 or 1")
    scope = _resolve_scale_test_scope(profile, config, clients)
    if _member_by_id(_members(scope, clients), instance_id) is None:
        raise HarnessError(409, "instance_not_member", "only a current allowlisted pool member can be tagged")
    instance, etag = _instance(scope, clients, instance_id)
    tags = _tags(instance)
    if tags.get(HARNESS_TAG) != config.harness_id or tags.get(SCALE_TEST_PROFILE_TAG) != profile.key:
        raise HarnessError(409, "instance_identity_changed", "the instance provenance does not match pool_key")
    registry, _ = _retirement_registry(scope, clients)
    if raw_value == "1" and (tags.get(PROTECTION_TAG) == "0" or instance_id in registry["instances"]):
        raise HarnessError(
            409, "retirement_committed",
            "retirement is irreversible; use new workers for new demand, never re-protect this instance",
        )
    if raw_value == "0" and not config.dry_run:
        _write_retirement(scope, clients, instance_id, request_id, now)
    if tags.get(PROTECTION_TAG) == raw_value:
        return 200, {
            "result": "no_op",
            "action": "set_pool_protection",
            "request_id": request_id,
            "pool_key": profile.key,
            "instance_id": instance_id,
            "tag_value": raw_value,
            "retirement_committed": raw_value == "0" and not config.dry_run,
        }
    if config.dry_run:
        return 200, {
            "result": "dry_run",
            "action": "set_pool_protection",
            "request_id": request_id,
            "pool_key": profile.key,
            "instance_id": instance_id,
            "tag_value": raw_value,
        }
    merged = dict(tags)
    merged[PROTECTION_TAG] = raw_value
    if len(merged) > OCI_MAX_FREEFORM_TAGS:
        raise HarnessError(
            409,
            "freeform_tag_limit_exceeded",
            "the protection update would exceed OCI's ten-freeform-tag limit",
        )
    response = clients.compute.update_instance(
        instance_id,
        _model("UpdateInstanceDetails", freeform_tags=merged),
        if_match=etag,
    )
    updated = _mapping_value(response, "data")
    if updated is None:
        updated, _ = _instance(scope, clients, instance_id)
    if _tags(updated).get(PROTECTION_TAG) != raw_value:
        raise HarnessError(502, "tag_update_not_confirmed", "OCI did not confirm the protection tag update")
    return 202, {
        "result": "submitted",
        "action": "set_pool_protection",
        "request_id": request_id,
        "pool_key": profile.key,
        "instance_id": instance_id,
        "tag_value": raw_value,
        "retirement_committed": raw_value == "0",
    }


def _update_scale_test_pool(
    payload: Mapping[str, Any],
    config: Config,
    clients: Clients,
    now: datetime,
    *,
    cleanup: bool,
) -> tuple[int, Mapping[str, Any]]:
    """Reject pre-ledger benchmark resize endpoints kept for API compatibility."""

    del payload, config, clients, now, cleanup
    raise HarnessError(
        410,
        "legacy_scale_action_disabled",
        "use reconcile_pool so every benchmark mutation is durable, fenced, and targeted",
    )


def _simulate_scale_burst(
    payload: Mapping[str, Any],
    config: Config,
) -> tuple[int, Mapping[str, Any]]:
    """Model latest-target coalescing without calling OCI or writing the ledger.

    This older synthetic endpoint is useful for UI explanation only. The live
    runner's 60-request test calls ``reconcile_pool`` and exercises the real
    authenticated ledger path.
    """

    request_id = _request_id(payload)
    count = payload.get("requestCount", 60)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise HarnessError(400, "invalid_request_count", "requestCount must be an integer from 1 through 1000")
    profiles = list(config.scale_test_profiles.values())
    if not profiles:
        raise HarnessError(409, "scale_test_not_configured", "no scale-test profiles are configured")

    grouped: dict[str, dict[str, Any]] = {
        profile.key: {"received": 0, "latestGeneration": 0, "latestDesiredCount": 0}
        for profile in profiles
    }
    for index in range(count):
        profile = profiles[index % len(profiles)]
        desired = 1 + ((index // len(profiles)) % profile.max_size)
        grouped[profile.key] = {
            "received": grouped[profile.key]["received"] + 1,
            "latestGeneration": index + 1,
            "latestDesiredCount": desired,
        }
    planned_updates = sum(1 for item in grouped.values() if item["received"])
    return 200, {
        "result": "simulation",
        "action": "simulate_scale_burst",
        "requestId": request_id,
        "requestsReceived": count,
        "plannedOciUpdates": planned_updates,
        "supersededRequests": count - planned_updates,
        "queueDiscipline": "latest absolute target per pool; one in-flight OCI update per pool",
        "profiles": [
            {**_scale_test_profile_payload(config.scale_test_profiles[key]), **values}
            for key, values in grouped.items()
            if values["received"]
        ],
        "mutatedOci": False,
    }


def _report_scale_test_ready(
    payload: Mapping[str, Any],
    config: Config,
    clients: Clients,
    now: datetime,
) -> tuple[int, Mapping[str, Any]]:
    """Idempotently record that a verified benchmark worker bootstrapped.

    Readiness is a harness proxy, not proof of registration with the application's
    control plane. The dedicated readiness Function may update this
    tag but has no pool-detach or instance-delete permission.
    """

    request_id = _request_id(payload)
    profile = _scale_test_profile(payload, config)
    instance_id = _instance_id(payload)
    scope = _resolve_scale_test_scope(profile, config, clients)
    if _member_by_id(_members(scope, clients), instance_id) is None:
        raise HarnessError(409, "instance_not_member", "only a current scale-test pool member can report ready")
    instance, etag = _instance(scope, clients, instance_id)
    if _mapping_value(instance, "lifecycle_state") != "RUNNING":
        raise HarnessError(409, "instance_not_running", "the reporting scale-test instance must be RUNNING")
    tags = _tags(instance)
    if tags.get(HARNESS_TAG) != config.harness_id or tags.get(SCALE_TEST_PROFILE_TAG) != profile.key:
        raise HarnessError(409, "instance_identity_changed", "the reporting instance provenance does not match")
    if not _matches_scale_test_profile(instance, profile):
        raise HarnessError(
            409,
            "instance_configuration_mismatch",
            "the reporting instance shape, OCPU count, or memory does not match its scale-test profile",
        )
    existing = tags.get(SCALE_TEST_READY_AT_TAG)
    if existing:
        _parse_timestamp(existing)
        return 200, {
            "result": "no_op",
            "action": "report_scale_test_ready",
            "profileKey": profile.key,
            "instanceId": instance_id,
            "readyAt": existing,
            "requestId": request_id,
        }
    ready_at = _timestamp(now)
    merged = dict(tags)
    for key in DRAIN_PRUNABLE_TAGS:
        merged.pop(key, None)
    merged[SCALE_TEST_READY_AT_TAG] = ready_at
    if len(merged) > OCI_MAX_FREEFORM_TAGS:
        raise HarnessError(
            409,
            "freeform_tag_limit_exceeded",
            "the readiness marker would exceed OCI's ten-freeform-tag limit",
        )
    response = clients.compute.update_instance(
        instance_id,
        _model("UpdateInstanceDetails", freeform_tags=merged),
        if_match=etag,
    )
    updated = _mapping_value(response, "data")
    if updated is None:
        updated, _ = _instance(scope, clients, instance_id)
    if _tags(updated).get(SCALE_TEST_READY_AT_TAG) != ready_at:
        raise HarnessError(502, "ready_update_not_confirmed", "OCI did not confirm the readiness marker")
    return 202, {
        "result": "submitted",
        "action": "report_scale_test_ready",
        "profileKey": profile.key,
        "instanceId": instance_id,
        "readyAt": ready_at,
        "requestId": request_id,
    }


def _scale_pool(payload: Mapping[str, Any], scope: Scope, clients: Clients) -> tuple[int, Mapping[str, Any]]:
    """Operate the small contrast pool used by the visual FIFO demonstration.

    Growth uses OCI's normal absolute pool update. Reduction is limited to one
    pre-tagged instance and is implemented as exact detach plus auto-terminate;
    this function never submits a generic smaller pool target.
    """

    request_id = _request_id(payload)
    desired = _desired_count(payload, scope.config)
    _require_pool_running(scope)
    current = _pool_size(scope.pool)

    if desired == current:
        return 200, {
            "result": "no_op",
            "action": "scale_pool",
            "currentSize": current,
            "targetSize": desired,
        }
    if desired > current:
        if scope.config.dry_run:
            return 200, {
                "result": "dry_run",
                "action": "scale_pool",
                "mode": "scale_up",
                "currentSize": current,
                "targetSize": desired,
            }
        response = clients.pools.update_instance_pool(
            scope.pool_id,
            _model("UpdateInstancePoolDetails", size=desired),
            if_match=scope.pool_etag,
            opc_retry_token=request_id,
        )
        return 202, {
            "result": "submitted",
            "action": "scale_pool",
            "mode": "scale_up",
            "currentSize": current,
            "targetSize": desired,
            "workRequestId": _work_request_id(response),
        }

    if desired != current - 1:
        raise HarnessError(409, "one_step_scale_in_required", "scale-in must decrement exactly one instance")
    instance_id = _instance_id(payload)
    operation_id = _operation_id(payload, request_id)
    if _member_by_id(_members(scope, clients), instance_id) is None:
        raise HarnessError(409, "instance_not_member", "the selected instance is not a member of the pool")
    instance, _ = _instance(scope, clients, instance_id)
    if _mapping_value(instance, "lifecycle_state") != "RUNNING":
        raise HarnessError(409, "instance_not_running", "the selected instance must be RUNNING")
    _validate_drain_tags(instance, scope, operation_id)
    if scope.config.dry_run:
        return 200, {
            "result": "dry_run",
            "action": "scale_pool",
            "mode": "targeted_detach_auto_terminate",
            "instanceId": instance_id,
            "currentSize": current,
            "targetSize": desired,
        }
    response = _detach(scope, clients, instance_id, request_id, auto_terminate=True)
    return 202, {
        "result": "submitted",
        "action": "scale_pool",
        "mode": "targeted_detach_auto_terminate",
        "instanceId": instance_id,
        "currentSize": current,
        "targetSize": desired,
        "workRequestId": _work_request_id(response),
    }


def _set_protection(
    payload: Mapping[str, Any],
    scope: Scope,
    clients: Clients,
    now: datetime,
    *,
    force_enabled: bool | None = None,
) -> tuple[int, Mapping[str, Any]]:
    """Write the contrast worker's protection and drain-provenance tags.

    Protection is represented by exact string values because your platform supplies
    the same free-form OCI tag. ``"1"`` is protected; ``"0"`` authorizes the
    selected drain operation. Existing unrelated tags are preserved.
    """

    request_id = _request_id(payload)
    operation_id = _operation_id(payload, request_id)
    instance_id = _instance_id(payload)
    supplied_enabled = payload.get("enabled")
    supplied_values = [payload[key] for key in ("value", "flagValue") if key in payload]
    if any(not isinstance(value, str) or value not in ("0", "1") for value in supplied_values) or (
        len(supplied_values) > 1 and any(value != supplied_values[0] for value in supplied_values[1:])
    ):
        raise HarnessError(400, "invalid_protection_value", "flag value must be exactly 0 or 1")
    exact_value = supplied_values[0] if supplied_values else None
    enabled = force_enabled if force_enabled is not None else supplied_enabled
    if enabled is None and exact_value is not None:
        enabled = exact_value == "1"
    if not isinstance(enabled, bool):
        raise HarnessError(
            400,
            "invalid_protection_value",
            "enabled must be a boolean or value must be exactly 0 or 1",
        )
    if supplied_enabled is not None and not isinstance(supplied_enabled, bool):
        raise HarnessError(400, "invalid_protection_value", "enabled must be a boolean")
    if supplied_enabled is not None and supplied_enabled != enabled:
        raise HarnessError(400, "invalid_protection_value", "enabled conflicts with the requested reset")
    if exact_value is not None and (exact_value == "1") != enabled:
        raise HarnessError(400, "invalid_protection_value", "enabled and flag value conflict")
    _require_pool_running(scope)
    if _member_by_id(_members(scope, clients), instance_id) is None:
        raise HarnessError(409, "instance_not_member", "only a current pool member can be tagged")
    instance, instance_etag = _instance(scope, clients, instance_id)
    if _mapping_value(instance, "lifecycle_state") != "RUNNING":
        raise HarnessError(409, "instance_not_running", "the selected instance must be RUNNING")
    if scope.config.dry_run:
        return 200, {
            "result": "dry_run",
            "action": "reset_protection" if force_enabled else "set_protection",
            "instanceId": instance_id,
            "enabled": enabled,
            "tagValue": "1" if enabled else "0",
            "drainOperationId": operation_id,
            "drainRequestedAt": _timestamp(now),
        }
    updated, _, changed = _tag_instance(
        scope,
        clients,
        instance,
        instance_etag,
        enabled=enabled,
        operation_id=operation_id,
        now=now,
    )
    tags = _tags(updated)
    expected_flag = "1" if enabled else "0"
    if tags.get(PROTECTION_TAG) != expected_flag:
        raise HarnessError(502, "tag_update_not_confirmed", "OCI did not confirm the protection tag update")
    return (202 if changed else 200), {
        "result": "submitted" if changed else "no_op",
        "action": "reset_protection" if force_enabled else "set_protection",
        "instanceId": instance_id,
        "enabled": enabled,
        "tagValue": expected_flag,
        "drainOperationId": operation_id,
        "drainRequestedAt": tags.get(REQUESTED_AT_TAG),
    }


def _drain_and_scale(
    payload: Mapping[str, Any],
    scope: Scope,
    clients: Clients,
    now: datetime,
) -> tuple[int, Mapping[str, Any]]:
    """Atomically demonstrate tag-then-detach for one selected contrast worker.

    The requested target must be exactly current minus one. Replaying after a
    successful detach returns a no-op only when the original drain provenance
    and final pool size both match, preventing a retry from selecting a second
    victim.
    """

    request_id = _request_id(payload)
    operation_id = _operation_id(payload, request_id)
    desired = _desired_count(payload, scope.config)
    instance_id = _instance_id(payload)
    if "flagValue" in payload and payload.get("flagValue") != "0":
        raise HarnessError(400, "invalid_protection_value", "targeted scale-in requires flagValue exactly 0")
    current = _pool_size(scope.pool)
    member = _member_by_id(_members(scope, clients), instance_id)
    instance, instance_etag = _instance(scope, clients, instance_id)

    # A replay after detach must never select or decrement another member.
    if member is None:
        _validate_drain_tags(instance, scope, operation_id)
        if current != desired:
            raise HarnessError(
                409,
                "detached_size_mismatch",
                "the selected instance is detached but the pool is not at desiredCount",
            )
        return 200, {
            "result": "no_op",
            "reason": "already_detached",
            "action": "drain_and_scale",
            "instanceId": instance_id,
            "currentSize": current,
            "targetSize": desired,
            "drainOperationId": operation_id,
        }

    _require_pool_running(scope)
    if desired != current - 1:
        raise HarnessError(
            409,
            "one_step_scale_in_required",
            "drain_and_scale must decrement the pool by exactly one instance",
        )
    if _mapping_value(instance, "lifecycle_state") != "RUNNING":
        raise HarnessError(409, "instance_not_running", "the selected instance must be RUNNING")

    if scope.config.dry_run:
        return 200, {
            "result": "dry_run",
            "action": "drain_and_scale",
            "mode": "tag_then_targeted_detach_auto_terminate",
            "instanceId": instance_id,
            "currentSize": current,
            "targetSize": desired,
            "tagValue": "0",
            "drainOperationId": operation_id,
            "drainRequestedAt": _timestamp(now),
        }

    tagged, _, changed = _tag_instance(
        scope,
        clients,
        instance,
        instance_etag,
        enabled=False,
        operation_id=operation_id,
        now=now,
    )
    _validate_drain_tags(tagged, scope, operation_id)
    response = _detach(scope, clients, instance_id, request_id, auto_terminate=True)
    return 202, {
        "result": "submitted",
        "action": "drain_and_scale",
        "mode": "tag_then_targeted_detach_auto_terminate",
        "instanceId": instance_id,
        "currentSize": current,
        "targetSize": desired,
        "tagUpdated": changed,
        "drainOperationId": operation_id,
        "drainRequestedAt": _tags(tagged).get(REQUESTED_AT_TAG),
        "workRequestId": _work_request_id(response),
    }


def _terminate_if_ready(
    payload: Mapping[str, Any],
    scope: Scope,
    clients: Clients,
    now: datetime,
) -> tuple[int, Mapping[str, Any]]:
    """Allow an eligible worker to reclaim its own slot in the optional path.

    The Terminator revalidates exact tag ``"0"``, harness/origin provenance,
    RUNNING state, and current membership immediately before exact detach. The
    detach decrements the pool and auto-terminates the instance; an OS shutdown
    alone would not release OCI instance-pool capacity.
    """

    request_id = _request_id(payload)
    instance_id = _instance_id(payload)
    if not scope.config.enable_termination:
        raise HarnessError(409, "termination_disabled", "the termination kill switch is disabled")
    if "observedProtection" in payload and payload.get("observedProtection") != "0":
        raise HarnessError(
            400,
            "invalid_protection_value",
            "observedProtection must be exactly 0",
        )

    instance, _ = _instance(scope, clients, instance_id)
    tags = _tags(instance)
    if tags.get(PROTECTION_TAG) != "0" or tags.get(HARNESS_TAG) != scope.config.harness_id:
        raise HarnessError(409, "instance_not_reclaimable", "the instance is not exactly unprotected in this harness")
    origin_pool = tags.get(ORIGIN_POOL_TAG)
    if origin_pool is not None and origin_pool != scope.pool_id:
        raise HarnessError(409, "instance_not_reclaimable", "the instance origin-pool tag does not match")
    state = _mapping_value(instance, "lifecycle_state")
    if state in {"TERMINATING", "TERMINATED"}:
        return 200, {
            "result": "no_op",
            "reason": "already_reclaimed",
            "action": "terminate_if_ready",
            "instanceId": instance_id,
        }
    if state != "RUNNING":
        raise HarnessError(409, "instance_not_running", "the reclaiming instance must be RUNNING")
    if _member_by_id(_members(scope, clients), instance_id) is None:
        return 200, {
            "result": "no_op",
            "reason": "already_detached",
            "action": "terminate_if_ready",
            "instanceId": instance_id,
        }
    pool_state = _mapping_value(scope.pool, "lifecycle_state")
    if pool_state not in {"RUNNING", "SCALING"}:
        raise HarnessError(409, "pool_busy", "the pool cannot accept a self-reclaim detach", retryable=True)

    # Re-read immediately before targeted detach. Protection is fail-closed;
    # a concurrent re-protect causes this invocation to skip the worker.
    authoritative, _ = _instance(scope, clients, instance_id)
    authoritative_tags = _tags(authoritative)
    if (
        authoritative_tags.get(PROTECTION_TAG) != "0"
        or authoritative_tags.get(HARNESS_TAG) != scope.config.harness_id
        or (
            authoritative_tags.get(ORIGIN_POOL_TAG) is not None
            and authoritative_tags.get(ORIGIN_POOL_TAG) != scope.pool_id
        )
    ):
        raise HarnessError(409, "instance_not_reclaimable", "the authoritative protection or provenance tag changed")
    if _mapping_value(authoritative, "lifecycle_state") != "RUNNING":
        raise HarnessError(409, "instance_not_running", "the reclaiming instance must still be RUNNING")
    if _member_by_id(_members(scope, clients), instance_id) is None:
        return 200, {
            "result": "no_op",
            "reason": "already_detached",
            "action": "terminate_if_ready",
            "instanceId": instance_id,
        }

    if scope.config.dry_run:
        return 200, {
            "result": "dry_run",
            "action": "terminate_if_ready",
            "instanceId": instance_id,
            "mode": "self_detach_auto_terminate",
            "currentSize": _pool_size(scope.pool),
            "targetSize": max(0, _pool_size(scope.pool) - 1),
        }

    current_size = _pool_size(scope.pool)
    try:
        response = _detach(
            scope,
            clients,
            instance_id,
            _stable_detach_token(request_id, instance_id),
            auto_terminate=True,
        )
    except Exception as error:
        member_still_attached = True
        if getattr(error, "status", None) in {404, 409}:
            member_still_attached = _member_by_id(
                _members(scope, clients), instance_id
            ) is not None
            if not member_still_attached:
                return 200, {
                    "result": "no_op",
                    "reason": "already_detached",
                    "action": "terminate_if_ready",
                    "instanceId": instance_id,
                    "requestId": request_id,
                }
        raise _classify_detach_error(
            error,
            member_still_attached=member_still_attached,
        ) from error
    return 202, {
        "result": "submitted",
        "action": "terminate_if_ready",
        "mode": "self_detach_auto_terminate",
        "instanceId": instance_id,
        "currentSize": current_size,
        "targetSize": max(0, current_size - 1),
        "requestId": request_id,
        "workRequestId": _work_request_id(response),
    }


def _make_clients() -> Clients:
    """Create OCI clients authenticated as the current Function resource principal."""

    if oci is None:
        raise HarnessError(500, "oci_sdk_unavailable", "OCI SDK is not installed")
    signer = oci.auth.signers.get_resource_principals_signer()
    return Clients(
        compute=oci.core.ComputeClient({}, signer=signer),
        pools=oci.core.ComputeManagementClient({}, signer=signer),
        objects=oci.object_storage.ObjectStorageClient({}, signer=signer),
        autoscaling=oci.autoscaling.AutoScalingClient({}, signer=signer),
        work_requests=oci.work_requests.WorkRequestClient({}, signer=signer),
    )


def _service_error(error: Exception) -> HarnessError:
    """Convert OCI/transport failures into sanitized retry policy.

    Busy state, stale ETags, throttling, conflicts, timeouts, connection loss,
    and transient service failures preserve retryable desired state. Auth and
    not-found failures remain terminal. Raw SDK details are never returned to
    the caller.
    """

    status = getattr(error, "status", None)
    code = str(getattr(error, "code", "") or "")
    if code in {"LimitExceeded", "QuotaExceeded"}:
        return HarnessError(409, "oci_capacity_limit", "OCI rejected capacity due to a service limit or quota; review the limit before submitting a new generation")
    if code in {"OutOfHostCapacity", "OutOfCapacity"}:
        return HarnessError(409, "oci_capacity_unavailable", "OCI has no capacity for this placement; review placement and submit a new generation")
    if status == 400:
        return HarnessError(400, "oci_invalid_request", "OCI rejected the request parameters; correct the configuration before submitting a new generation")
    if status == 412:
        return HarnessError(
            409,
            "stale_resource",
            "OCI rejected a stale ETag; re-read current state and retry the latest desired generation",
            retryable=True,
        )
    if status == 404:
        return HarnessError(404, "resource_not_found", "OCI resource was not found")
    if status in {401, 403}:
        return HarnessError(502, "oci_authorization_failed", "the Function is not authorized for the OCI operation")
    if status == 429:
        return HarnessError(
            429,
            "oci_throttled",
            "OCI throttled the request; retry with bounded jitter",
            retryable=True,
        )
    if status == 409 and code in {"IncorrectState", "ExternalServerIncorrectState", "LockConflict"}:
        return HarnessError(
            409,
            "pool_busy",
            "OCI reports the pool is busy; retain the latest absolute target and retry after reconciliation",
            retryable=True,
        )
    if status in {400, 409}:
        return HarnessError(
            409,
            "oci_conflict",
            "OCI rejected the requested state transition; re-read current state and retry with bounded jitter",
            retryable=True,
        )
    sdk_request_error = getattr(getattr(oci, "exceptions", None), "RequestException", None)
    if (isinstance(sdk_request_error, type) and isinstance(error, sdk_request_error)) or status in {408, 500, 502, 503, 504} or any(
        marker in type(error).__name__.lower() for marker in ("timeout", "connection")
    ):
        return HarnessError(502, "oci_transient_error", "OCI service request failed transiently", retryable=True)
    return HarnessError(500, "internal_error", "the harness could not complete the request")


def handle_request(
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, Any] | None = None,
    clients: Clients | None = None,
    clock: Callable[[], datetime] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, Mapping[str, Any]]:
    """Authenticate, authorize, dispatch, and sanitize one parsed API request.

    ``clients``, ``clock``, and ``environ`` are injectable so unit tests execute
    the same control logic without OCI credentials. Role and token checks occur
    before OCI clients are created or any mutation handler is selected.
    """

    try:
        if not isinstance(payload, Mapping):
            raise HarnessError(400, "invalid_request", "request body must be a JSON object")
        requested_action, action = _canonical_action(payload)
        config = _config(environ if environ is not None else os.environ)
        _authorize_role(action, config)
        _authenticate(action, payload, headers or {}, config)
        now = _utcnow(clock)
        if config.controller_only and config.allow_empty_pool_registry and not config.scale_test_profiles:
            if action != "scale_test_status":
                raise HarnessError(
                    409, "controller_not_enrolled",
                    "No pools are enrolled. Enroll pools and update the Function configuration before requesting pool operations.",
                )
            result = _awaiting_pool_enrollment_status(config, now)
        elif action == "simulate_scale_burst":
            result = _simulate_scale_burst(payload, config)
        else:
            active_clients = clients if clients is not None else _make_clients()
            if action == "scale_test_status":
                result = _scale_test_status(config, active_clients, now)
            elif action in {"scale_test_resize", "scale_test_cleanup"}:
                raise HarnessError(
                    410,
                    "legacy_scale_action_disabled",
                    "use reconcile_pool so every benchmark mutation is durable, fenced, and targeted",
                )
            elif action == "report_scale_test_ready":
                result = _report_scale_test_ready(payload, config, active_clients, now)
            elif action == "reconcile_pool":
                result = _reconcile_pool(payload, config, active_clients, now, clock)
            elif action == "request_status":
                result = _request_status(payload, config, active_clients)
            elif action == "set_pool_protection":
                result = _set_pool_protection(payload, config, active_clients, now)
            else:
                scope = _resolve_scope(config, active_clients)
                _validate_request_scope(payload, scope)
                if action != "status" and (
                    _tags(scope.pool).get(SCALE_TEST_PROFILE_TAG) in config.scale_test_profiles
                    or _mapping_value(scope.pool, "display_name") in {
                        profile.pool_name for profile in config.scale_test_profiles.values()
                    }
                ):
                    raise HarnessError(
                        409, "managed_pool_requires_reconciliation",
                        "managed pools require set_pool_protection and reconcile_pool; legacy and self-reclaim writes are isolated to the contrast pool",
                    )
                if action == "status":
                    result = _status(scope, active_clients)
                elif action == "scale_pool":
                    result = _scale_pool(payload, scope, active_clients)
                elif action == "set_protection":
                    result = _set_protection(payload, scope, active_clients, now)
                elif action == "reset_protection":
                    result = _set_protection(payload, scope, active_clients, now, force_enabled=True)
                elif action == "drain_and_scale":
                    result = _drain_and_scale(payload, scope, active_clients, now)
                else:
                    result = _terminate_if_ready(payload, scope, active_clients, now)
        if requested_action != action:
            status, body = result
            body = dict(body)
            body["requestedAction"] = requested_action
            return status, body
        return result
    except HarnessError as error:
        LOG.warning("Harness request rejected: %s (%s)", error.reason, error.message)
        return error.status, {
            "result": "rejected",
            "reason": error.reason,
            "message": error.message,
            "retryable": error.retryable,
        }
    except Exception as error:  # OCI SDK errors are intentionally sanitized.
        converted = _service_error(error)
        LOG.exception("OCI harness operation failed")
        return converted.status, {
            "result": "error",
            "reason": converted.reason,
            "message": converted.message,
            "retryable": converted.retryable,
        }


def _json(data: io.BytesIO | None) -> Mapping[str, Any]:
    raw = data.getvalue().decode("utf-8") if data else "{}"
    value: Any = json.loads(raw)
    if isinstance(value, Mapping) and isinstance(value.get("body"), str):
        value = json.loads(value["body"])
    if not isinstance(value, Mapping):
        raise ValueError("request body must be a JSON object")
    return value


def _context_headers(ctx: Any) -> Mapping[str, Any]:
    if ctx is None:
        return {}
    for name in ("Headers", "headers"):
        candidate = getattr(ctx, name, None)
        if callable(candidate):
            candidate = candidate()
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _context_method(ctx: Any) -> str:
    if ctx is None:
        return "POST"
    for name in ("Method", "method"):
        candidate = getattr(ctx, name, None)
        if callable(candidate):
            candidate = candidate()
        if isinstance(candidate, str) and candidate:
            return candidate.upper()
    return "POST"


def _raw_reply(ctx: Any, status: int, data: str, content_type: str) -> Any:
    headers = {"Content-Type": content_type, "Cache-Control": "no-store"}
    allowed_origin = os.environ.get("HARNESS_ALLOWED_ORIGIN")
    if allowed_origin:
        headers.update(
            {
                "Access-Control-Allow-Origin": allowed_origin,
                "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Harness-Token",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            }
        )
    if fdk_response is None:
        return SimpleNamespace(status_code=status, response_data=data, headers=headers)
    return fdk_response.Response(ctx, status_code=status, response_data=data, headers=headers)


def _reply(ctx: Any, status: int, body: Mapping[str, Any]) -> Any:
    # Signed synchronous InvokeFunction uses a successful transport response
    # even when the controller reports a business conflict. Keep business status
    # explicit so the SDK caller can distinguish queued work from invocation
    # failures. The existing API Gateway/browser contract remains unchanged.
    if os.environ.get("AUTH_MODE") == "oci_iam":
        return _raw_reply(ctx, 200, json.dumps({"status_code": status, "body": body}), "application/json")
    return _raw_reply(ctx, status, json.dumps(body), "application/json")


def handler(ctx: Any, data: io.BytesIO | None = None) -> Any:
    """OCI Functions entrypoint."""

    method = _context_method(ctx)
    if os.environ.get("CONTROLLER_ONLY", "false").lower() != "false" or os.environ.get("AUTH_MODE") == "oci_iam":
        try:
            config = _config(os.environ)
        except HarnessError as error:
            return _reply(ctx, error.status, {"result": "rejected", "reason": error.reason, "retryable": False})
        if config.controller_only and method != "POST":
            return _reply(ctx, 405, {"result": "rejected", "reason": "method_not_allowed", "retryable": False})
    if method == "GET":
        try:
            page = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        except OSError:
            LOG.exception("Unable to load harness frontend")
            return _reply(ctx, 500, {"result": "error", "reason": "frontend_unavailable"})
        return _raw_reply(ctx, 200, page, "text/html; charset=utf-8")
    if method == "OPTIONS":
        return _raw_reply(ctx, 204, "", "text/plain; charset=utf-8")
    if method != "POST":
        return _reply(ctx, 405, {"result": "rejected", "reason": "method_not_allowed"})
    try:
        payload = _json(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        LOG.warning("Rejecting malformed request: %s", error)
        return _reply(ctx, 400, {"result": "rejected", "reason": "invalid_json"})
    status, body = handle_request(payload, headers=_context_headers(ctx))
    return _reply(ctx, status, body)
