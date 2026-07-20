# T2a2d: immutable slot and launch binding

Status: accepted decision spike; production-unreachable
Issue: [#364](https://github.com/FrankQDWang/SeekTalent/issues/364)
Parent: [#319](https://github.com/FrankQDWang/SeekTalent/issues/319)

## Context

`admit_installed_sidecar_launch()` verifies the fixed manifest, signature, and sidecar bytes, but its descriptor reads finish before `spawn_owned_sidecar()` later invokes path-based `subprocess.Popen`. The returned admission therefore proves the inspected bytes, not the later image open. `tools/native_probes/launch_binding_probe.py` deterministically reproduces that replacement window without importing product code.

The platform research is intentionally kept in one source, not copied here: [cross-platform immutable launch-binding research](../references/2026-07-20-cross-platform-immutable-launch-binding-research.md). It distinguishes official facts from inferences, records the primary Apple, Microsoft, CPython, and GitHub sources, and defines the full native matrix.

This decision preserves main-owned direct children, three anonymous binary pipes, bounded environment, no shell/PATH lookup, and production caller count zero. It does not add a runtime/provider/browser/CLI path, a handshake, a remote service, Redis, or a second workflow engine.

## Decision

1. Introduce an immutable, concrete installed slot and a main-owned lifecycle lease before any future launch binding implementation. The updater creates and verifies a fresh inactive slot, seals it, then atomically changes only an activation pointer. Launch resolves that pointer once and operates on the concrete slot. A running child's slot is never mutated, reused, rolled back over, or deleted.
2. Windows has no public executable-handle `CreateProcessW` API. The next Windows implementation is conditional on the required `windows-2025` native probe: hold share-deny handles for every concrete directory component and admitted file, authenticate/hash those open handles, call explicit absolute `lpApplicationName` `CreateProcessW` suspended, and compare path/file identity before resuming. This is a **conditional path-immutability bridge**, never “handle execution.” If the native job cannot show compatible image open plus blocked write/delete/rename/replace, it is a no-go and no abstraction lands.
3. macOS has no supported CPython/subprocess fd-exec route; ordinary FDs, `flock`, and `O_EXLOCK` are advisory. The pre-spawn security guarantee is therefore unavailable for a writable per-user slot. The v1 reliability boundary is immutable concrete slots plus a cooperative lifecycle lease; strict same-UID hostile-process protection requires installer ownership/permissions or later evidence.
4. The later macOS platform-authenticity implementation uses a small native `POSIX_SPAWN_START_SUSPENDED` shim. Before resume, it evaluates the running PID with Security.framework against the exact signed release requirement. Any lookup, signature, requirement, or identity failure kills and reaps the suspended child before user-space execution or authority.
5. No child obtains workflow authority until the later `MainHello -> SidecarHello -> MainReady -> SidecarReady` gate. Dynamic image evidence and the handshake complement, rather than replace, immutable slots and platform signing.

## Native evidence recorded by this spike

The deterministic probe has no production import or caller and runs only on the real host:

| Host | Required evidence | Current result |
| --- | --- | --- |
| macOS 15 arm64 | Path replacement changes later `Popen`; normal FD/`flock` do not prevent write/replace/rename; no CPython `fexecve`; suspended Security.framework dynamic requirement can reject/kill/reap before the child marker | Local native pass on macOS 15.6.1 arm64 / CPython 3.14.4 |
| macOS 15 Intel x86_64 | Same Darwin API rows on native Intel, never Rosetta or `os.name` simulation | Required GitHub Actions row |
| Windows Server 2025 x64 | `CreateFileW` share-deny file and path-component behavior; explicit suspended `CreateProcessW`; `FILE_ID_INFO` and image-path corroboration; pre-existing writer limitation | Required GitHub Actions row |

The probe's macOS Security.framework case validates an Apple-signed `/bin/sleep` PID, then starts a local unsigned helper suspended. The exact Apple requirement rejects that PID; the helper is killed/reaped while its first user-space marker remains absent. This is API-semantic evidence only, not a release sidecar signature/notarization claim.

`.github/workflows/native-launch-binding-probe.yml` pins `windows-2025`, `macos-15`, and `macos-15-intel`. It must remain a required semantic gate while Intel is supported. GitHub has announced Intel runner retirement after Fall/August 2027; before that sunset, product owners must either move the Intel row to owned/self-hosted hardware or explicitly retire Intel support. GitHub VMs prove API semantics, not Windows 10/11 installer behavior, SmartScreen, macOS ownership/quarantine, Gatekeeper, notarization, or clean-user release acceptance.

## Threat boundary

| Actor or race | Windows conditional bridge | macOS v1 | Result |
| --- | --- | --- | --- |
| Cooperating update, rollback, cleanup | Lease plus immutable slot | Same | In scope |
| Normal product process starts a new write/delete/rename | Covered for all lease-held local components | Covered only when installer ownership denies it | In scope as stated |
| Non-cooperating same-UID process | Limited to Windows objects protected by complete share-deny coverage; a pre-existing writer yields `executable_share_mode_conflict` and no child, while destructive denial of service remains possible | Not a strict pre-spawn guarantee for a per-user writable slot | Never claim updater locking is a security boundary |
| Admin/root, kernel/filter driver, physical disk | Out of scope | Out of scope | Authority exceeds the process boundary |
| Network/removable/nonstandard filesystem | Not supported until separately probed | Not supported until separately probed | Fail closed |

## Slot lifecycle

```text
staged -> verified -> sealed -> active
                    \-> rejected

active --atomic pointer update--> rollback-retained or retired
leased slot --------------------> cannot mutate, reuse, or delete
retired + zero leases ----------> deletable
```

- Only the installer/updater creates a new inactive slot; identifiers are never reused.
- Admission resolves activation once and receives the concrete slot, never an activation alias.
- Main acquires the lease before manifest/signature/content reads and keeps the lifecycle lease until the direct child exits and is reaped.
- A new slot may activate while an old child runs. Rollback selects another sealed slot; it never overwrites the old one.
- Crash-recovery metadata is advisory. Native handles disappear with the main process, and stale metadata alone grants no cleanup authority.

## Next implementation contract

```python
@dataclass(frozen=True, slots=True)
class InstalledSidecarLaunchLease:
    admission: AuthenticatedInstalledSidecarLaunch
    slot_identity: InstalledSlotIdentity
    executable_identity: PlatformFileIdentity
    platform_binding: PlatformLaunchBinding


def acquire_installed_sidecar_launch_lease(...) -> InstalledSidecarLaunchLease: ...


def spawn_owned_sidecar(lease: InstalledSidecarLaunchLease) -> PendingOwnedSidecarProcess: ...


def establish_owned_sidecar_authority(
    pending: PendingOwnedSidecarProcess,
    child_evidence: ChildImageEvidence,
) -> OwnedSidecarProcess: ...
```

Native handles remain private, live, non-serializable state. An admission without a live lease is not spawnable. `PendingOwnedSidecarProcess` has pipes but no workflow authority; post-creation failures kill/reap it before returning.

Stable causal reasons are:

- `slot_lease_conflict`
- `slot_filesystem_unsupported`
- `slot_path_component_changed`
- `slot_reparse_point_rejected`
- `slot_identity_changed`
- `executable_identity_changed`
- `executable_share_mode_conflict`
- `platform_authenticity_failed`
- `child_image_evidence_unavailable`
- `child_image_evidence_mismatch`
- `launch_binding_unsupported`

## Acceptance for the next implementation ticket

| Case | Required outcome |
| --- | --- |
| No live lease | Reject before creating a child. |
| Slot component or content changes during acquisition/hash | Causal failure, no child. |
| Windows file/component write, rename, delete, or replace | Blocked by lease or identity mismatch; no authorized child. |
| Windows image-share compatibility failure | No-go; do not weaken shares or use a path-only fallback. |
| macOS cooperating updater | Old leased concrete slot remains; a new sealed slot may activate. |
| macOS same-UID non-cooperating pre-spawn race | Explicitly outside per-user v1 strict guarantee; production remains unreachable. |
| Suspended child evidence mismatch | Kill, reap, close handles/pipes, release lease, and return causal failure. |
| Valid image evidence | Resume only; withhold workflow authority until future `MainReady`. |
| Child exit/creation failure | Reap and release every native resource with no process or descriptor leak. |
