# SeekTalent delivery directories

`dist/` has exactly three delivery states. Each non-empty state directory
contains exactly one ZIP for macOS arm64, macOS Intel, and Windows x64.

- `tmp/` contains the candidate currently under internal testing.
- `active/` contains the release approved for production rollout.
- `last-version/` contains the production release replaced by `active/` and is
  the immediate rollback source.

The promotion sequence is:

```text
tmp -> active -> last-version
```

During the current transition, `last-version/` remains the production `0.7.47`
release, `active/` is intentionally empty, and `tmp/` holds the `0.8.3`
candidate until acceptance is complete.

Do not place wheels, sdists, checksum sidecars, screenshots, archives, extracted
packages, build directories, or historical candidates in `dist/`. Build
intermediates belong outside `dist/`; only the three final platform ZIPs enter a
delivery state directory.
