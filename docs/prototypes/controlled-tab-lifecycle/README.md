# Controlled Tab Lifecycle Prototype

> PROTOTYPE — throwaway evidence for issue #295, not production browser integration.

Question: can one complete browser-control path combine the OpenCLI 1.8.6 fork, a scratch owned-tab
registry, the selected controlled-tab lock, non-blocking background reclamation, the independent
60-second extension alarm, fencing, and failure isolation without touching the user's host tab or
letting cleanup delay or rewrite business results?

The prototype deliberately assumes one unambiguous eligible `https://h.liepin.com/` host tab. The
production rule for multiple eligible host tabs remains issue #291. Production rollout, feature
flags, packaging gates, and ownership migrations remain issue #296.

Run the full real-Chrome proof from this worktree:

```bash
python3 docs/prototypes/controlled-tab-lifecycle/lifecycle_harness.py
```

The command uses a fixed 60-second idle deadline. For development-only iteration:

```bash
python3 docs/prototypes/controlled-tab-lifecycle/lifecycle_harness.py --idle-seconds 30
```

Add `--tui` to render the complete reducer state after each lifecycle event. The harness writes a
sanitized JSON report to a temporary evidence directory and prints its path. It never screenshots,
clicks, fills, navigates, binds, or closes the user's existing Liepin tab.

The proof creates several inactive owned sibling tabs so correctness cannot accidentally depend on
a two-tab limit. Mutating commands run only against the existing local fixture and use controlled
DOM actions, because native coordinate input is not reliable before Chrome has rendered an inactive
tab. It injects overlay, countdown, scratch-registry, background-reclaimer, exact-close,
close-verification, and telemetry faults independently. The local process remains alive so the
harness can inspect outcomes, but the "crash" case intentionally performs no local close and relies
only on the extension's persisted idle alarm; killing the user's Chrome would not be a safe
prototype technique.

## Validated run

The default 60-second scenario passed against `seektalent-opencli-1.8.6+prototype.1` on
2026-07-14:

- four owned tabs were created; scope B held three at once;
- every owned tab was inactive in the existing user window, and the host tab stayed unchanged;
- critical-path cleanup submission took at most 1.232 ms;
- the next scope activated in 196.161 ms and the old fence was rejected;
- seven injected lifecycle faults produced diagnostics without changing either source result;
- all three failed local reclaims were already gone when checked after the 60-second fallback;
- no owned target remained as `about:blank`.
