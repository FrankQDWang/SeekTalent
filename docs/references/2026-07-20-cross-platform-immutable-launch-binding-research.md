# Cross-platform immutable slot / launch binding research

Status: decision input for #364; production-unreachable  
Evidence date: 2026-07-20  
Scope: Windows x64 and macOS launch binding, slot lifecycle, and native-runner evidence  
Truth order: live SeekTalent code, official platform documentation/source, then native probe

## Executive decision

The current code correctly authenticates and hashes the installed manifest, detached signature, and sidecar while their file descriptors are open, but it closes those descriptors before `spawn_owned_sidecar()` later calls path-based `subprocess.Popen`. The remaining admission-to-image-open TOCTOU is therefore real.

The decision for the next implementation slice should be:

1. Introduce a main-owned **installed-slot lease** that starts before admission reads any bytes and remains alive until the owned child exits. An updater may atomically activate another immutable slot, but it must never mutate, reuse, roll back over, or delete a leased slot.
2. On Windows, add a native handle lease: open every path component and every admitted file without delete sharing, and open the sidecar executable for read sharing but without write/delete sharing. Hash and authenticate the already-open handles; hold them while calling `CreateProcessW` with an explicit absolute `lpApplicationName`, initially suspended; compare the path and file identity before and after creation. This is an OS-enforced **path-immutability bridge**, not a nonexistent `CreateProcess(HANDLE)` API.
3. On macOS, do not attempt fd execution. CPython and `subprocess` expose no macOS fd-exec path, while Darwin `execve` and `posix_spawn` both name the executable by path. Use a never-mutated concrete slot plus an updater lease as the v1 reliability boundary. A per-user advisory lease is not a security boundary against a non-cooperating same-UID process.
4. Keep production unreachable until later slices add platform authenticity and child-image evidence. For macOS, the strong candidate is a small native launcher using `POSIX_SPAWN_START_SUSPENDED`, followed by a Security.framework dynamic-code check against an exact release requirement before any child user-space instruction runs. For Windows, Authenticode can be verified against the already-open file handle, but the exact launched-child claim still needs the Windows lease evidence and the future authenticated handshake.
5. GitHub-hosted `windows-2025` and `macos-15-intel` / `macos-26-intel` are suitable for this decision spike and for lightweight required semantic probes today. They are not substitutes for packaged release tests on end-user Windows 10/11 and real installed macOS layouts. Intel macOS CI must have an exit plan because GitHub has announced the eventual removal of macOS x86_64 support.

The rest of this note deliberately marks each statement as one of:

- **Official fact** — directly stated by the platform owner.
- **Code fact** — observed in this repository at `main@4af1f8b0`.
- **Engineering inference** — the design conclusion drawn from official facts and code facts.
- **Probe requirement** — behavior that must be recorded on a native runner before implementation is accepted.

## 1. Current code fact and exact gap

**Code fact.** `admit_installed_sidecar_launch()` reads the fixed manifest and signature, verifies the release signature, then resolves and hashes the sidecar. `_read_stable_regular_file()` and `_inspect_executable()` verify descriptor snapshots and close each descriptor before returning. `spawn_owned_sidecar()` later passes `resolution.executable_path` to `subprocess.Popen`.

**Engineering inference.** The returned admission proves the bytes that were inspected. It does not prove that the later path lookup opens the same filesystem object. A rename/replace/write between those calls can make the child differ from the admitted object.

This spike must not weaken the existing properties: main-owned direct child, three anonymous binary pipes, empty/bounded environment, no shell, no `PATH` lookup, no extra arguments or secrets, and production caller count zero.

## 2. Can GitHub-hosted machines supply the evidence?

### 2.1 Availability, labels, cost, and limits

| Runner | Official fact on 2026-07-20 | Suitability |
| --- | --- | --- |
| Windows x64 | Standard public-repository runners provide x64 Windows with `windows-latest`, `windows-2025`, and `windows-2022`; a public-repository VM has 4 CPUs, 16 GB RAM, and 14 GB SSD. GitHub says each non-slim job receives a new VM and standard runners are free and unlimited for public repositories. [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories) | Use the fixed `windows-2025` label for this spike. It is suitable for Win32/NTFS native probes and a lightweight required job. Avoid `windows-latest` because `-latest` can migrate. |
| macOS Intel | Standard public-repository runners provide native x64 labels `macos-15-intel` and `macos-26-intel`, with 4 CPUs, 14 GB RAM, and 14 GB SSD. [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories) GitHub declared macOS 26 Intel generally available on 2026-02-26. [GA announcement](https://github.blog/changelog/2026-02-26-macos-26-is-now-generally-available-for-github-hosted-runners/) | Reuse the repository's existing `macos-15-intel` label for the minimum spike; optionally add `macos-26-intel` as a second OS sample. It is real x86_64 evidence, not Rosetta or mocked `os.name`. |
| macOS arm64 | `macos-15`, `macos-26`, and `macos-latest` are arm64 standard labels. [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories) | Keep an arm64 row in the eventual release matrix. Architecture-independent Darwin API conclusions can be shared, but final release evidence cannot be inferred from Intel alone. |

**Official fact.** GitHub-hosted jobs are limited to six hours. A Free-plan account has up to 20 concurrent standard jobs and five concurrent macOS jobs. [Actions limits](https://docs.github.com/en/actions/reference/limits#job-concurrency-limits-for-github-hosted-runners)

**Official fact.** This repository is public, so its standard hosted-runner use has no billable minutes. If the repository became private, current baseline rates are USD 0.010/minute for Windows x64 and USD 0.062/minute for macOS Intel/arm64 after included minutes. [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions#baseline-minute-costs)

**Official fact.** Runner images are updated weekly; GitHub supports at most two GA images and one beta per OS and starts deprecating the oldest label when a new OS image reaches GA. A pinned OS label avoids `-latest` migration but does not freeze the installed image. [runner-images support policy](https://github.com/actions/runner-images#software-and-image-support)

**Official fact and lifecycle risk.** GitHub announced that macOS x86_64 support will end after the Intel image retirement in Fall 2027; the earlier runner announcement names August 2027 for `macos-15-intel`. [GitHub changelog](https://github.blog/changelog/2025-09-19-github-actions-macos-13-runner-image-is-closing-down/#notice-of-macos-x86_64-intel-architecture-deprecation), [runner-images announcement](https://github.com/actions/runner-images/issues/13045). The later macOS 26 Intel GA announcement makes Intel usable now but does not explicitly rescind that sunset.

### 2.2 Recommendation: one-time probe versus required CI

**Engineering inference.** GitHub-hosted machines are appropriate for:

- a manually dispatched one-time native probe that captures OS version, filesystem type, architecture, compiler, every operation result, Win32 error/`errno`, and a bounded race-loop result;
- a small deterministic required CI job that guards the chosen primitive on `windows-2025`, `macos-15-intel`, and arm64 macOS;
- repeating the probe whenever the runner image or minimum supported OS changes.

They are not sufficient as the only release evidence because:

- `windows-2025` is Windows Server, not an end-user Windows 10/11 install with Defender, third-party antivirus, OneDrive, enterprise policy, junctions, or non-NTFS media;
- a hosted macOS VM does not reproduce every installer ownership, quarantine, notarization, or user-volume condition;
- image contents change weekly;
- GitHub's Intel macOS capacity has an announced end of life.

Therefore: use GitHub runners now, make Windows semantic probes required, make Intel macOS probes required while the label exists, and maintain a documented migration to owned/self-hosted Intel hardware or explicitly end Intel product support before GitHub's sunset. Packaged release acceptance still needs real-user-OS rows.

## 3. Windows: what the platform can and cannot bind

### 3.1 Official facts

1. **Share modes are enforced for the lifetime of an open handle.** `CreateFileW` says a subsequent open fails with `ERROR_SHARING_VIOLATION` when its requested access conflicts with an existing handle. Omitting `FILE_SHARE_DELETE` blocks later opens requesting delete access, and Windows defines delete access as permitting both delete and rename. Omitting `FILE_SHARE_WRITE` blocks later write opens or writable mappings. Sharing options remain effective until the handle closes. [CreateFileW `dwShareMode`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew#parameters)
2. **Directories can be held as objects.** `CreateFileW` can open a directory when `FILE_FLAG_BACKUP_SEMANTICS` is used. `FILE_FLAG_OPEN_REPARSE_POINT` opens a link/reparse point itself instead of silently following it. [CreateFileW directory and symbolic-link behavior](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew#directories)
3. **Open-handle identity is queryable.** `GetFinalPathNameByHandleW` returns the resolved final path of a file or directory. `FILE_ID_INFO` states that its 128-bit file ID plus volume serial number uniquely identify a file on one computer. [GetFinalPathNameByHandleW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew), [FILE_ID_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info)
4. **CreateProcess is path-based.** `CreateProcessW` accepts `lpApplicationName` and a command line; no public parameter accepts an already-open executable file handle. Supplying an explicit absolute `lpApplicationName` avoids ambiguous command-line parsing and executable search. [CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw)
5. **A child can be created before it runs user code.** `CREATE_SUSPENDED` creates the primary thread suspended until `ResumeThread`. [Process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags#create_suspended)
6. **Running-image evidence is path evidence, not a returned file handle.** `QueryFullProcessImageNameW` retrieves the executable image's full name from a process handle. [QueryFullProcessImageNameW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew)
7. **Authenticode may validate the already-open object.** `WINTRUST_FILE_INFO` includes both a mandatory full path and an optional read-capable file handle; `WINTRUST_ACTION_GENERIC_VERIFY_V2` invokes the Authenticode policy provider. [WINTRUST_FILE_INFO](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/ns-wintrust-wintrust_file_info), [WinVerifyTrustEx](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/nf-wintrust-winverifytrustex)
8. **CPython does not add an object-exec primitive.** On Windows `subprocess.Popen` calls `_winapi.CreateProcess`, and CPython's native implementation calls `CreateProcessW`. [CPython `subprocess.py`](https://github.com/python/cpython/blob/main/Lib/subprocess.py), [CPython `_winapi.c`](https://github.com/python/cpython/blob/main/Modules/_winapi.c)

### 3.2 Candidate evaluation

| Candidate | Decision | Reason |
| --- | --- | --- |
| Pass the admitted executable handle directly to process creation | Reject / unavailable | Public `CreateProcessW` has no executable-handle parameter. Do not name a wrapper around a path call “handle exec.” |
| Hold only the executable handle with `FILE_SHARE_READ` | Insufficient alone | It should deny write/delete/rename of that file while allowing the image reader, but an ancestor directory or reparse point could still redirect path lookup. The actual `CreateProcessW` compatibility must be probed because its internal open/share request is not documented as a contract. |
| Hold every concrete directory/reparse component plus every admitted file; hash the open executable handle; call explicit-path `CreateProcessW(CREATE_SUSPENDED)`; compare file ID/path before and after | Select for v1, conditional on native green evidence | Windows share-deny semantics provide an OS-enforced immutability interval. If all components remain the same objects and the leaf cannot be replaced or written, the path resolved during that interval must name the admitted leaf under the supported local-filesystem boundary. This is an indirect bridge, not direct object execution. |
| Static Authenticode check only, then ordinary `Popen` | Reject alone | Signature validity is valuable authenticity evidence, and WinTrust can inspect an open handle, but a later path replacement is still possible without the handle lease. It also does not by itself prove the child is the expected release build. |
| Create suspended and trust only `QueryFullProcessImageNameW` | Reject alone | The API returns a name, not a durable file object or digest. Use it as corroborating evidence while the share-deny chain is still held, not as the root of identity. |

### 3.3 Windows v1 selected primitive

**Engineering inference, pending probe.** Implement one native `WindowsInstalledSlotLease` with this order:

1. Resolve the already-selected concrete slot, never an activation alias.
2. Open the slot root and every directory component with `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT`, read/query access, and no delete sharing. Reject every reparse point not explicitly allowed by policy.
3. Open manifest, signature, and every manifest-listed component file without write/delete sharing. Open the executable with read access and `FILE_SHARE_READ` only.
4. Record final paths and `FILE_ID_INFO`; authenticate and hash the open handles. Authenticode is a later gate but should use the same executable handle.
5. Call `CreateProcessW` with the exact normalized absolute executable in `lpApplicationName`, no shell or search, a bounded environment, inherited anonymous-pipe handles only, and `CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP`.
6. While all slot handles remain held, query the child image path, re-open that path under compatible sharing, compare its `FILE_ID_INFO`, and fail closed before `ResumeThread` if any evidence differs.
7. Resume only after platform evidence passes. Do not grant workflow authority until the future `MainHello -> SidecarHello -> MainReady` gate succeeds.
8. Retain the slot lifecycle lease until child exit; closing launch-only verification handles earlier is allowed only if all later-loaded sidecar files are covered by immutable ownership/platform signing and the lifecycle contract.

**Residual risk.** Microsoft does not document a “child image file ID” return value, nor the exact internal image-open share flags. The proof therefore depends on the native probe, supported local NTFS/ReFS semantics, complete path-component coverage, and an honest threat boundary. Network filesystems, unusual filter drivers, administrator/kernel attackers, and unlisted runtime-loaded files remain outside this primitive.

## 4. macOS: no CPython/subprocess fd-exec

### 4.1 Official facts

1. Darwin documents `execve(const char *path, ...)`: the new image file is named by `path`. [Apple `execve(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/execve.2.html)
2. Darwin documents `posix_spawn(..., const char *path, ...)`: it creates the child from the executable specified by an absolute or relative pathname. [Apple `posix_spawn(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/posix_spawn.2.html)
3. Current XNU's public syscall table contains path-taking `execve` and `posix_spawn`; it contains no `fexecve` syscall. The kernel's execution path copies the pathname and performs `namei` lookup to obtain the executable vnode. [Apple XNU `syscalls.master`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/syscalls.master), [Apple XNU `kern_exec.c`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_exec.c)
4. Python documents that `os.execve(fd, ...)` exists only on platforms where `os.execve in os.supports_fd`; unsupported use raises `NotImplementedError`. CPython compiles that branch only when the platform provides `HAVE_FEXECVE`. [Python `os.execve`](https://docs.python.org/3/library/os.html#os.execve), [CPython `posixmodule.c`](https://github.com/python/cpython/blob/main/Modules/posixmodule.c)
5. Python's `os.posix_spawn` still requires `path`, and its exposed keyword arguments do not include Apple's `POSIX_SPAWN_START_SUSPENDED`. [Python `os.posix_spawn`](https://docs.python.org/3/library/os.html#os.posix_spawn)
6. CPython deliberately uses `posix_spawn` on macOS when its `Popen` option set permits it; otherwise it reaches fork/exec. Both branches remain path-based. [CPython `subprocess.py`](https://github.com/python/cpython/blob/main/Lib/subprocess.py)
7. macOS `flock`, `O_EXLOCK`, and `fcntl` locks are advisory. Apple explicitly says a non-cooperating process may still access the file and cause inconsistency. [Apple `flock(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/flock.2.html), [Apple `open(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/open.2.html)
8. Renaming replaces directory links; directory write permission controls whether rename can proceed. An open read descriptor does not freeze the pathname. [Apple `rename(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/rename.2.html)
9. Static code-signing verification has the same concurrency caveat. Apple says `SecStaticCodeCheckValidity` is secure only if code is not concurrently modified, and its result lasts only while the code remains unmodified. [SecStaticCodeCheckValidity](https://developer.apple.com/documentation/security/secstaticcodecheckvalidity(_:_:_:))
10. Code signing can detect alteration and express an exact requirement, but it is one part of a complete solution. [Apple Code Signing Guide](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Introduction/Introduction.html), [Applying Code Requirements](https://developer.apple.com/documentation/security/applying-code-requirements)
11. Apple provides a dynamic code object for a specific running PID through `SecCodeCopyGuestWithAttributes`, and code requirements can then be evaluated against that running code. [SecCodeCopyGuestWithAttributes](https://developer.apple.com/documentation/security/seccodecopyguestwithattributes(_:_:_:_:)), [guest PID attribute](https://developer.apple.com/documentation/security/ksecguestattributepid)
12. Apple's `POSIX_SPAWN_START_SUSPENDED` extension creates the child's task suspended before it begins execution in user space. [Apple spawn attributes](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/posix_spawnattr_getflags.3.html)

**Native observation, not cross-machine proof.** On the current macOS 15.6.1 arm64 development host with CPython 3.14.4, `os.execve in os.supports_fd` is false, `os.fexecve` is absent, and `os.posix_spawn` exists. The native Intel probe must record the same feature check rather than inferring it from this arm64 host.

### 4.2 Candidate evaluation

| Candidate | Decision | Reason |
| --- | --- | --- |
| `os.fexecve` or `os.execve(fd, ...)` on macOS | Reject | The platform and CPython build do not provide the required fd-exec capability. |
| Execute `/dev/fd/N` while passing the descriptor | Reject | That is another pathname lookup with deployment/mount/interpreter behavior; it is not a documented Darwin object-exec contract and must not be advertised as one. |
| Keep an open read fd across ordinary `Popen` | Reject as security binding | POSIX permits another same-UID process to write the inode or replace its directory entry. The old descriptor continues to identify the old object while path execution may select the replacement. |
| `flock` / `O_EXLOCK` updater lease | Select for cooperative lifecycle only | It serializes a cooperating updater and launcher and is valuable for reliability. Apple explicitly says it does not stop a non-cooperating process. |
| Root/admin-owned, non-writable immutable concrete slot + path spawn | Select where installer ownership is available | If the launching user lacks write/delete authority on the entire path chain, it cannot perform the replacement. This protects against unprivileged same-user code only when that code truly lacks the relevant authority. A per-user owner can change permissions and is not equivalent. |
| Static `codesign`/`SecStaticCodeCheckValidity`, then `Popen` | Reject alone | Apple explicitly warns the result is only valid while the object cannot change. It supplies authenticity but not the missing object-to-path binding. |
| Native `posix_spawn` with `POSIX_SPAWN_START_SUSPENDED`, then dynamic `SecCode` check for the PID against an exact requirement | Select for the later platform-authenticity slice | This checks the actual running code before child user space executes. CPython does not expose the required flag, so it needs a small auditable native shim. It still does not make arbitrary sidecar resources immutable; bundle signing/library validation and the slot lease remain necessary. |

### 4.3 macOS v1 selected primitive

**Engineering inference.** The decision spike should explicitly record **no strict pre-spawn opened-object execution on supported macOS APIs**. The implementable v1 is a layered guarantee:

1. Create a unique inactive slot; verify the signed release and all bytes; never mutate that slot in place.
2. Seal it with installer ownership/permissions where product packaging permits. If the install remains per-user, declare same-UID malicious replacement outside the v1 pre-spawn guarantee.
3. Acquire a cooperative slot lifecycle lease before admission and keep it until child exit. Atomic activation and rollback only change a separate active-slot pointer; running children continue using their concrete leased slot.
4. In the later packaged-sidecar slice, code sign the complete sidecar/bundle and validate the exact signer/designated requirement. Static validation is defense in depth, not the launch-binding proof.
5. In the later native-launch slice, spawn suspended and dynamically validate the PID's running code before resuming it.
6. The later four-step handshake must bind the signed manifest identity, build ID, protocol identity, and fresh session to the child before `MainReady`. Kill/reap on any mismatch.

This combination is honest: slot immutability handles updater races; platform signing handles altered/foreign code; dynamic PID evidence handles “which code did the OS start”; the handshake handles application-level release/protocol/session identity. None alone replaces the others.

## 5. Frozen v1 threat boundary

| Actor/race | Windows v1 | macOS v1 | Rationale |
| --- | --- | --- | --- |
| Cooperating updater activation, rollback, cleanup | In scope and prevented by lifecycle lease plus immutable slot | In scope and prevented by lifecycle lease plus immutable slot | This is the primary reliability boundary. Updater lock/lease is sufficient because both participants cooperate. |
| Accidental file replacement or write by ordinary product processes | In scope for all leased/listed objects | In scope when permissions deny it; otherwise detected at later platform/child gates | Windows share modes enforce conflicts. macOS advisory locks do not. |
| Non-cooperating same-UID process | In scope on Windows only for the complete, locally stored path/file set covered by share-deny handles; later Authenticode/handshake still required | Out of strict pre-spawn v1 unless the slot is owned by another authority and non-writable; later suspended dynamic code check plus handshake reduces the residual risk | Do not relabel an updater lease as a same-user security boundary. |
| Administrator/root, kernel, malicious filesystem/filter driver, physical-disk attacker | Out of scope | Out of scope | These authorities can bypass permissions, inject into the process, alter the kernel/filesystem, or disable trust policy. |
| Network/removable/nonstandard filesystem | Out of v1 until separately probed and allowed | Out of v1 until separately probed and allowed | Initial production policy should require a local supported filesystem and fail closed otherwise. |
| Vulnerable but correctly signed sidecar, malicious signed old release, unsafe runtime plug-in | Not solved by launch binding | Not solved by launch binding | Require manifest anti-rollback policy, platform signing policy, library/dependency closure, and normal vulnerability controls. |

**Production consequence.** This ticket can choose and prove primitives but cannot make the sidecar production-reachable. Real packaged artifacts, platform trust evidence, and the four-step handshake remain ordered prerequisites.

## 6. Slot authority and lifecycle contract

The next implementation ticket should encode this state machine, not scatter checks around `Popen`:

```text
staged -> verified -> sealed -> active
                    \-> rejected

active --atomic pointer change--> rollback-retained or retired
leased slot --------------------> cannot mutate/reuse/delete
retired + zero leases ----------> deletable
```

Rules:

1. The installer/updater alone creates a fresh inactive slot. A slot identifier is never reused and should include release identity or strong random/content identity.
2. Verification reads the fixed manifest/signature and the complete declared file closure from that concrete slot.
3. Sealing makes the slot read-only to the greatest authority available. No updater writes into a sealed slot.
4. Activation is one atomic pointer/name update after sealing. Admission resolves the pointer once, then operates only on the concrete slot.
5. Main acquires the slot lease before admission. The lease owns native path/file handles where the platform supports them.
6. Rollback activates a previous sealed slot; it never copies bytes over the current slot.
7. An update may activate a new slot while an old child runs, but cannot delete the old slot until the child is reaped and its lease is released.
8. Crash recovery treats persisted lease files as advisory metadata and validates owner PID/process-start identity before cleanup. Native handles disappear when the main process dies; stale lease metadata cannot by itself grant authority.

Suggested public boundary for the next ticket:

```python
@dataclass(frozen=True, slots=True)
class InstalledSidecarLaunchLease:
    admission: AuthenticatedInstalledSidecarLaunch
    slot_identity: InstalledSlotIdentity
    executable_identity: PlatformFileIdentity
    platform_binding: PlatformLaunchBinding


def acquire_installed_sidecar_launch_lease(...) -> InstalledSidecarLaunchLease: ...

def spawn_owned_sidecar(
    lease: InstalledSidecarLaunchLease,
) -> PendingOwnedSidecarProcess: ...

def establish_owned_sidecar_authority(
    pending: PendingOwnedSidecarProcess,
    child_evidence: ChildImageEvidence,
) -> OwnedSidecarProcess: ...
```

The concrete native handles remain private and non-serializable. An admission without a live lease is not spawnable.

Suggested causal reasons:

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

Failures before OS process creation remain no-child/no-`Popen`. Failures after suspended creation must terminate and reap the still-unauthorized child before returning.

## 7. Minimum native probe matrix

The probe must be tiny, deterministic, production-unreachable, and report structured results. Every artifact records native architecture, OS release/build, and Python implementation/version/build so a result remains attributable to its runner. It should compile on the native runner rather than emulate a host.

### 7.1 Windows x64 (`windows-2025`, local NTFS)

| Probe | Expected selected-design result |
| --- | --- |
| Baseline explicit `CreateProcessW` with admitted executable handle open under `FILE_SHARE_READ` | Child is created suspended; if this fails, the proposed share mode is not compatible and the candidate is no-go. |
| Open for write / truncate executable while lease held | `ERROR_SHARING_VIOLATION`. |
| `MoveFileExW` rename executable while lease held | Fails; record exact error. |
| Delete executable while lease held | Fails; record exact error. |
| Replace target with a different valid executable while lease held | Fails before the path can name the replacement. |
| Rename/delete each ancestor while its directory handle is held without delete sharing | Fails; record behavior for every component. |
| Swap a junction/reparse component | Fails or is rejected/detected before spawn. |
| `GetFinalPathNameByHandleW` and `FILE_ID_INFO` before and after suspended creation | Same volume/file ID and expected normalized path. |
| `QueryFullProcessImageNameW` while suspended | Matches the expected final image path; use as corroboration only. |
| Release lease, then perform write/rename/delete controls | Controls succeed, proving the red/green distinction came from the lease. |
| 1,000 bounded start-versus-replace races | No unauthorized child and no unexplained result; keep logs and exact error histogram. |
| Child exit while updater waits to retire slot | Deletion remains blocked until lease close, then succeeds. |

Repeat the essential matrix on a Windows 11 x64 self-hosted or release-test machine before claiming end-user release support. A Windows Server runner alone is sufficient for the decision spike, not for the full release matrix.

### 7.2 macOS Intel (`macos-15-intel`) and arm64

| Probe | Expected selected-design result |
| --- | --- |
| Print `uname -m`, OS build, Python build, `os.execve in os.supports_fd`, `hasattr(os, "fexecve")` | Native architecture; fd exec false/absent. Any different result reopens the decision. |
| Hash open fd, atomically replace pathname, compare old `fstat`/new `lstat`, then path-spawn | Red reproducer: old fd remains old inode while path selects replacement. |
| Keep read fd open while a non-cooperating process writes the file | Write succeeds under owner permissions, demonstrating that an open fd is not a lease. |
| Hold `flock`/`O_EXLOCK`, then have an attacker ignore the lock and replace/write | Attacker succeeds, proving advisory scope. |
| Cooperating updater honors slot lease during activation/rollback/delete | New activation succeeds; leased old slot is retained; deletion succeeds only after release. |
| Non-writable installer-owned slot, unprivileged mutation attempts | Write/rename/delete fail. Run under a genuinely unprivileged identity; GitHub runner passwordless admin capability must not be confused with the tested child identity. |
| Native `posix_spawn` with `POSIX_SPAWN_START_SUSPENDED`; sidecar's first instruction writes a marker | Marker is absent before explicit resume. |
| Suspended dynamic `SecCode` check: expected signer/requirement versus altered, ad-hoc, wrong-signer binary | Expected child passes; every mismatch is killed/reaped before the user-space marker appears. |
| Raw concurrent path replace/start under a per-user writable slot | **No-go as required CI automation.** A 2026-07-20 arm64 macOS 15.6.1 observation left one child in `_dyld_start`, PPID 1, state `UE`, after a raw concurrent replace/start; `SIGKILL` did not reap it. Do not repeat this probe or claim a process group/supervisor solves an uninterruptible kernel wait. The deterministic single replacement reproducer remains required; it proves the path-based risk without creating an unreapable child. |

Run API-semantic rows on both Intel and arm64. The immediate decision requires GitHub Intel evidence; the final release matrix retains separate `macos-x86_64` and `macos-arm64` artifact rows.

### 7.3 2026-07-20 macOS race correction

The prior 1,000-iteration raw concurrent macOS path race is withdrawn from the required automated matrix because the native observation above made the probe itself non-deterministic and capable of polluting a developer machine or hosted runner. This is a **no-go finding**, not a claim that the race is safe or a weakening of the threat boundary: a per-user writable macOS slot has no strict pre-spawn binding guarantee. Required deterministic macOS evidence is instead the open-FD/path replacement red reproducer, advisory-lock limit, cooperative immutable-slot activation/rollback/delete lease, and suspended Security.framework PID gate. Strict macOS assurance remains ordered behind installer ownership/non-writability plus exact signed-PID evidence; production remains unreachable.

## 8. Acceptance matrix for the next implementation slice

| Scenario | Required outcome |
| --- | --- |
| Admission without a live platform lease | Type/capability rejection before process creation. |
| Concrete slot changes during handle acquisition or hashing | Causal fail-closed reason; no child. |
| Unsupported filesystem or uninspectable reparse/mount | `slot_filesystem_unsupported` / `slot_reparse_point_rejected`; no child. |
| Windows write/rename/delete/replace race | Operation blocked or identity mismatch; no authorized child. |
| macOS cooperating updater race | Old concrete slot retained, new slot may activate, child remains bound to old release. |
| macOS same-UID non-cooperating race before platform evidence exists | Explicitly outside strict v1 guarantee; production remains unreachable. |
| Suspended child image evidence mismatch | Terminate, reap, close pipes/handles, release lease, return causal failure. |
| Correct native evidence | Resume child but do not grant workflow authority until future handshake reaches `MainReady`. |
| Child crash/start failure | Reap and release every native handle/lease without FD or process leak. |
| Active child plus update/rollback | Activation pointer may change; leased slot may not be overwritten or deleted. |

## 9. Final recommendation and ordering

The resource answer is **yes**: use GitHub's Windows x64 and macOS Intel machines for #364. The existing `build-macos-intel-offline.yml` already proves that this repository can obtain `macos-15-intel`; add only the bounded native probe necessary for this ticket, not another workflow runtime.

The implementation order after the probe should be:

1. immutable concrete-slot state machine and lifecycle lease;
2. Windows share-deny/path-component native binding, if and only if the full native probe is green;
3. macOS explicit no-fd-exec decision plus cooperative lease/ownership enforcement;
4. real packaged sidecar and platform authenticity (Windows Authenticode; macOS code signing/notarization, exact requirement, library/dependency closure);
5. suspended child-image evidence, especially dynamic `SecCode` validation on macOS;
6. `MainHello -> SidecarHello -> MainReady -> SidecarReady`, with all workflow authority withheld until `MainReady`;
7. authenticated history admission and production composition.

Do not add Redis, a remote service, a second workflow engine, an unsigned fallback, or a generic cross-platform abstraction that hides the different guarantees. The useful abstraction is a small lease/capability boundary with explicit Windows and macOS implementations and causal errors.
