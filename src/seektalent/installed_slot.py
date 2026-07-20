from __future__ import annotations

import errno
import os
import re
import stat
import weakref
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, LiteralString, Never, Self, SupportsIndex, TypeVar

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from seektalent.installed_filesystem import (
    InstalledReleaseError,
    InstalledReleaseReason,
    is_permission_error,
    open_stable_regular_for_update,
    read_stable_regular_file,
    require_real_directory,
)
from seektalent.installed_release import (
    AuthenticatedInstalledSidecarLaunch,
    InstalledSidecarExecutableResolution,
    admit_installed_sidecar_launch,
)
from seektalent.release_manifest import ProductBuildId, Sha256
from seektalent.release_signing import ReleaseManifestTrustPolicyV1
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


INSTALLATION_ID_RELATIVE_PATH = Path("control/installation-id")
ACTIVE_SLOT_POINTER_RELATIVE_PATH = Path("control/active-slot.json")
ACTIVE_SLOT_LOCK_RELATIVE_PATH = Path("control/active-slot.lock")
SLOT_LOCK_RELATIVE_PATHS = {
    "A": Path("control/slot-A.lock"),
    "B": Path("control/slot-B.lock"),
}
SLOT_ROOT_RELATIVE_PATHS = {"A": Path("slots/A"), "B": Path("slots/B")}
MAX_ACTIVE_SLOT_POINTER_BYTES = 64 * 1024
_OPAQUE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-=]{0,127}\Z")
_UTC_RFC3339_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


class ActiveSlotPointerReason(StrEnum):
    RAW_INPUT_REQUIRED = "raw_input_required"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    ILLEGAL_NUMBER = "illegal_number"
    INVALID_UNICODE = "invalid_unicode"
    ROOT_NOT_OBJECT = "root_not_object"
    UNKNOWN_FIELD = "unknown_field"
    SCHEMA_VALIDATION = "schema_validation"
    INVALID_VALUE = "invalid_value"
    NON_CANONICAL = "non_canonical"


class ActiveSlotPointerError(ValueError):
    def __init__(self, reason: ActiveSlotPointerReason, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason.value)


class InstalledSlotReason(StrEnum):
    INSTALLED_ROOT_INVALID = "installed_root_invalid"
    ACTIVE_SLOT_POINTER_INVALID = "active_slot_pointer_invalid"
    ACTIVE_SLOT_POINTER_CHANGED = "active_slot_pointer_changed"
    SLOT_IDENTITY_MISMATCH = "slot_identity_mismatch"
    SLOT_LEASE_CONFLICT = "slot_lease_conflict"
    SLOT_LEASE_UNAVAILABLE = "slot_lease_unavailable"
    SLOT_RELEASE_FAILED = "slot_release_failed"


class InstalledSlotError(ValueError):
    def __init__(self, reason: InstalledSlotReason, path: Path | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(reason.value)


def _pointer_schema_error(reason: ActiveSlotPointerReason, message: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(reason.value, message)


def _validate_pointer_identifier(value: str) -> str:
    if _OPAQUE_TOKEN_RE.fullmatch(value) is None:
        raise _pointer_schema_error(ActiveSlotPointerReason.INVALID_VALUE, "identifier has an invalid format")
    return value


def _validate_committed_at(value: str) -> str:
    if _UTC_RFC3339_RE.fullmatch(value) is None:
        raise _pointer_schema_error(
            ActiveSlotPointerReason.INVALID_VALUE,
            "committed_at must be second-precision UTC RFC3339",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise _pointer_schema_error(
            ActiveSlotPointerReason.INVALID_VALUE,
            "committed_at is not a real UTC timestamp",
        ) from exc
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise _pointer_schema_error(
            ActiveSlotPointerReason.INVALID_VALUE,
            "committed_at must be UTC",
        )
    return value


class ActiveSlotPointerV1(BaseModel):
    """The canonical, bytes-only active installed-slot selection record."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["seektalent.active-slot/v1"]
    installation_id: Annotated[str, Field(min_length=1, max_length=128)]
    physical_slot: Literal["A", "B"]
    pointer_generation: Annotated[int, Field(gt=0)]
    product_build_id: ProductBuildId
    release_manifest_sha256: Sha256
    committed_at: Annotated[str, Field(min_length=20, max_length=20)]

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes: bool | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if not isinstance(obj, cls):
            raise ActiveSlotPointerError(ActiveSlotPointerReason.RAW_INPUT_REQUIRED)
        return BaseModel.model_validate.__func__(
            cls,
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if not isinstance(json_data, bytes):
            raise ActiveSlotPointerError(ActiveSlotPointerReason.RAW_INPUT_REQUIRED)
        return _parse_active_slot_pointer_bytes(
            cls,
            json_data,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("installation_id")
    @classmethod
    def validate_installation_id(cls, value: str) -> str:
        return _validate_pointer_identifier(value)

    @field_validator("committed_at")
    @classmethod
    def validate_committed_at(cls, value: str) -> str:
        return _validate_committed_at(value)

    @model_validator(mode="after")
    def validate_pointer(self) -> Self:
        if self.pointer_generation > (1 << 53) - 1:
            raise _pointer_schema_error(
                ActiveSlotPointerReason.INVALID_VALUE,
                "pointer_generation must be an I-JSON safe integer",
            )
        return self


PointerModel = TypeVar("PointerModel", bound=ActiveSlotPointerV1)


def canonical_active_slot_pointer_bytes(pointer: ActiveSlotPointerV1) -> bytes:
    """Return the RFC 8785 bytes required for an active-slot pointer on disk."""
    return rfc8785.dumps(pointer.model_dump(mode="json"))


def parse_active_slot_pointer(raw: bytes) -> ActiveSlotPointerV1:
    """Parse one canonical active-slot pointer without accepting mappings or text."""
    if not isinstance(raw, bytes):
        raise ActiveSlotPointerError(ActiveSlotPointerReason.RAW_INPUT_REQUIRED)
    return _parse_active_slot_pointer_bytes(ActiveSlotPointerV1, raw)


def _parse_active_slot_pointer_bytes(
    model_cls: type[PointerModel],
    raw: bytes,
    *,
    context: object | None = None,
    by_alias: bool | None = None,
    by_name: bool | None = None,
) -> PointerModel:
    try:
        payload = strict_json_object_loads(raw)
    except StrictJsonError as exc:
        raise ActiveSlotPointerError(ActiveSlotPointerReason(exc.reason.value), exc.location) from None
    unknown_fields = set(payload) - set(model_cls.model_fields)
    if unknown_fields:
        raise ActiveSlotPointerError(ActiveSlotPointerReason.UNKNOWN_FIELD, (min(unknown_fields),))
    try:
        pointer = BaseModel.model_validate_json.__func__(
            model_cls,
            raw,
            strict=True,
            extra="forbid",
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False)[0]
        error_type = str(first["type"])
        try:
            reason = ActiveSlotPointerReason(error_type)
        except ValueError:
            reason = (
                ActiveSlotPointerReason.UNKNOWN_FIELD
                if error_type == "extra_forbidden"
                else ActiveSlotPointerReason.SCHEMA_VALIDATION
            )
        raise ActiveSlotPointerError(reason, tuple(first["loc"])) from None
    if raw != canonical_active_slot_pointer_bytes(pointer):
        raise ActiveSlotPointerError(ActiveSlotPointerReason.NON_CANONICAL)
    return pointer


@dataclass(frozen=True, slots=True)
class InstalledSlotIdentity:
    """A durable identity for one concrete release ever selected for a slot."""

    installation_id: str
    physical_slot: Literal["A", "B"]
    pointer_generation: int
    product_build_id: str
    release_manifest_sha256: str


def _pointer_identity(pointer: ActiveSlotPointerV1) -> InstalledSlotIdentity:
    return InstalledSlotIdentity(
        installation_id=pointer.installation_id,
        physical_slot=pointer.physical_slot,
        pointer_generation=pointer.pointer_generation,
        product_build_id=pointer.product_build_id,
        release_manifest_sha256=pointer.release_manifest_sha256,
    )


@dataclass(slots=True)
class _NativeSlotLock:
    path: Path
    descriptor: int | None
    platform: Literal["posix", "windows"]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        failure: OSError | None = None
        try:
            _unlock_native_slot_lock(descriptor, self.platform)
        except OSError as exc:
            failure = exc
        try:
            os.close(descriptor)
        except OSError as exc:
            failure = failure or exc
        if failure is not None:
            raise InstalledSlotError(InstalledSlotReason.SLOT_RELEASE_FAILED, self.path) from failure


@dataclass(slots=True)
class _InstalledSidecarLeaseState:
    admission: AuthenticatedInstalledSidecarLaunch
    identity: InstalledSlotIdentity
    slot_lock: _NativeSlotLock
    released: bool = False
    transferred: bool = False

    def close(self) -> None:
        if self.released:
            return
        self.released = True
        self.slot_lock.close()


_LIVE_LAUNCH_LEASES: dict[
    int,
    tuple[weakref.ReferenceType["InstalledSidecarLaunchLease"], _InstalledSidecarLeaseState],
] = {}


class InstalledSidecarLaunchLease:
    """A factory-only live slot lease that can be consumed by exactly one spawn."""

    __slots__ = ("_identity", "_admission", "__weakref__")

    _identity: InstalledSlotIdentity
    _admission: AuthenticatedInstalledSidecarLaunch

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("InstalledSidecarLaunchLease is factory-only")

    @property
    def identity(self) -> InstalledSlotIdentity:
        return self._identity

    @property
    def admission(self) -> AuthenticatedInstalledSidecarLaunch:
        return self._admission

    @property
    def resolution(self) -> InstalledSidecarExecutableResolution:
        return self._admission.resolution

    @property
    def slot_root(self) -> Path:
        return self._admission.slot_root

    @property
    def manifest_path(self) -> Path:
        return self._admission.manifest_path

    @property
    def executable_path(self) -> Path:
        return self._admission.executable_path

    def close(self) -> None:
        state = _pop_live_lease_state(self)
        if state is not None:
            state.close()

    def __enter__(self) -> Self:
        if _find_live_lease_state(self) is None:
            raise TypeError("lease must be live")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> Self:
        raise TypeError("InstalledSidecarLaunchLease cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Self:
        raise TypeError("InstalledSidecarLaunchLease cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("InstalledSidecarLaunchLease cannot be serialized")

    def _take_for_spawn(self) -> _InstalledSidecarLeaseState:
        state = _pop_live_lease_state(self)
        if state is None or state.released or state.transferred:
            raise TypeError("lease must be a live factory InstalledSidecarLaunchLease")
        state.transferred = True
        return state


def _new_launch_lease(state: _InstalledSidecarLeaseState) -> InstalledSidecarLaunchLease:
    lease = object.__new__(InstalledSidecarLaunchLease)
    object.__setattr__(lease, "_identity", state.identity)
    object.__setattr__(lease, "_admission", state.admission)
    lease_id = id(lease)

    def remove_if_unclaimed(reference: weakref.ReferenceType[InstalledSidecarLaunchLease]) -> None:
        entry = _LIVE_LAUNCH_LEASES.get(lease_id)
        if entry is not None and entry[0] is reference:
            _LIVE_LAUNCH_LEASES.pop(lease_id, None)
            with suppress(InstalledSlotError):
                entry[1].close()

    reference = weakref.ref(lease, remove_if_unclaimed)
    _LIVE_LAUNCH_LEASES[lease_id] = (reference, state)
    return lease


def _find_live_lease_state(lease: object) -> _InstalledSidecarLeaseState | None:
    entry = _LIVE_LAUNCH_LEASES.get(id(lease))
    if entry is None or entry[0]() is not lease:
        return None
    return entry[1]


def _pop_live_lease_state(lease: object) -> _InstalledSidecarLeaseState | None:
    entry = _LIVE_LAUNCH_LEASES.get(id(lease))
    if entry is None or entry[0]() is not lease:
        return None
    _LIVE_LAUNCH_LEASES.pop(id(lease), None)
    return entry[1]


def acquire_installed_sidecar_launch_lease(
    installation_root: Path,
    trust_policy: ReleaseManifestTrustPolicyV1,
    verification_time: datetime,
) -> InstalledSidecarLaunchLease:
    """Lease the stable active slot, then authenticate its exact sidecar launch."""
    root = _validate_installation_root(installation_root)
    with _brief_control_lock(root):
        installation_id = _read_installation_id(root)
        initial_pointer = _read_active_slot_pointer(root)
        initial_identity = _pointer_identity(initial_pointer)
        _require_pointer_installation(initial_identity, installation_id)

    slot_root = root / SLOT_ROOT_RELATIVE_PATHS[initial_identity.physical_slot]
    _require_concrete_slot_root(root, slot_root)
    slot_lock = _acquire_slot_lock(root, initial_identity.physical_slot)
    try:
        with _brief_control_lock(root):
            current_installation_id = _read_installation_id(root)
            if current_installation_id != installation_id:
                raise InstalledSlotError(
                    InstalledSlotReason.SLOT_IDENTITY_MISMATCH,
                    root / INSTALLATION_ID_RELATIVE_PATH,
                )
            reread_pointer = _read_active_slot_pointer(root)
            reread_identity = _pointer_identity(reread_pointer)
            if reread_identity != initial_identity:
                raise InstalledSlotError(
                    InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED,
                    root / ACTIVE_SLOT_POINTER_RELATIVE_PATH,
                )
            _require_pointer_installation(reread_identity, current_installation_id)

        admission = admit_installed_sidecar_launch(slot_root, trust_policy, verification_time)
        if (
            admission.product_build_id != initial_identity.product_build_id
            or admission.manifest_sha256 != initial_identity.release_manifest_sha256
        ):
            raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH, slot_root)
        return _new_launch_lease(
            _InstalledSidecarLeaseState(
                admission=admission,
                identity=initial_identity,
                slot_lock=slot_lock,
            )
        )
    except BaseException:
        slot_lock.close()
        raise


def _validate_installation_root(installation_root: Path) -> Path:
    if (
        not isinstance(installation_root, Path)
        or not installation_root.is_absolute()
        or ".." in installation_root.parts
    ):
        raise InstalledSlotError(InstalledSlotReason.INSTALLED_ROOT_INVALID)
    try:
        value = os.lstat(installation_root)
    except OSError as exc:
        if is_permission_error(exc):
            raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, installation_root) from exc
        raise InstalledSlotError(InstalledSlotReason.INSTALLED_ROOT_INVALID, installation_root) from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise InstalledSlotError(InstalledSlotReason.INSTALLED_ROOT_INVALID, installation_root)
    return installation_root


def _read_installation_id(root: Path) -> str:
    path = root / INSTALLATION_ID_RELATIVE_PATH
    raw = read_stable_regular_file(root, path, limit=1024)
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH, path) from exc
    if _OPAQUE_TOKEN_RE.fullmatch(value) is None:
        raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH, path)
    return value


def _read_active_slot_pointer(root: Path) -> ActiveSlotPointerV1:
    path = root / ACTIVE_SLOT_POINTER_RELATIVE_PATH
    try:
        raw = read_stable_regular_file(root, path, limit=MAX_ACTIVE_SLOT_POINTER_BYTES)
    except InstalledReleaseError as exc:
        if exc.reason == InstalledReleaseReason.PATH_CHANGED:
            raise InstalledSlotError(InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED, path) from exc
        raise
    try:
        return parse_active_slot_pointer(raw)
    except ActiveSlotPointerError as exc:
        raise InstalledSlotError(InstalledSlotReason.ACTIVE_SLOT_POINTER_INVALID, path) from exc


def _require_pointer_installation(identity: InstalledSlotIdentity, installation_id: str) -> None:
    if identity.installation_id != installation_id:
        raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH)


def _require_concrete_slot_root(root: Path, slot_root: Path) -> None:
    require_real_directory(root, slot_root)


def _brief_control_lock(root: Path) -> _NativeSlotLock:
    return _acquire_native_slot_lock(root, root / ACTIVE_SLOT_LOCK_RELATIVE_PATH)


def _acquire_slot_lock(root: Path, physical_slot: Literal["A", "B"]) -> _NativeSlotLock:
    return _acquire_native_slot_lock(root, root / SLOT_LOCK_RELATIVE_PATHS[physical_slot])


def _acquire_native_slot_lock(root: Path, path: Path) -> _NativeSlotLock:
    try:
        descriptor = open_stable_regular_for_update(root, path)
    except OSError as exc:
        if is_permission_error(exc):
            raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, path) from exc
        raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path) from exc
    try:
        platform_name = _lock_native_slot_nonblocking(descriptor, path)
    except BaseException:
        os.close(descriptor)
        raise
    return _NativeSlotLock(path=path, descriptor=descriptor, platform=platform_name)


def _lock_native_slot_nonblocking(descriptor: int, path: Path) -> Literal["posix", "windows"]:
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EBUSY}:
                raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_CONFLICT, path) from exc
            raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path) from exc
        return "windows"
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_CONFLICT, path) from exc
        except OSError as exc:
            raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path) from exc
        return "posix"
    raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path)


def _unlock_native_slot_lock(descriptor: int, platform_name: Literal["posix", "windows"]) -> None:
    if platform_name == "windows":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)
