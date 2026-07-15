# Controlled Tab Lock Prototype

> PROTOTYPE — throw away after the visual and behavior decision is recorded.

Question: what should the Dokobot-style controlled-tab lock look like, and can its visual layer fail without blocking browser automation?

Decision: the user selected **Variant A — Dokobot double line** on 2026-07-14 because it matches the requested quiet full-page gray lock with a small bottom-center countdown between two thin lines. The diagnostic panels and variant switcher are prototype-only.

Run from the repository root:

```bash
python3 -m http.server 5180 --directory docs/prototypes/controlled-tab-lock
```

Open `http://127.0.0.1:5180/?variant=a`. Variants `a`, `b`, and `c` are also switchable from the prototype bar or with the left/right arrow keys.

The prototype is intentionally standalone. It does not connect to OpenCLI, touch Chrome tabs, or close a real page. Its controls demonstrate the interaction contract before the selected visual is tested through the real OpenCLI path.

## Real Chrome harness

`real_chrome_harness.py` is the throwaway integration proof for issue #290. It uses the local
`FrankQDWang/OpenCLI` 1.8.6 fork, discovers an existing `https://h.liepin.com/` user tab without
claiming it, and creates one inactive owned tab in the same Chrome window.

The harness never clicks or fills the user's Liepin tab. Mutating automation checks run against
`fixture.html`; the owned tab then returns to Liepin for a read-only state check. Screenshots are
written to a temporary directory and contain only the local fixture. The harness loads the production
asset at `src/seektalent/opencli_browser/controlled_tab_lock.js` so later proofs cannot drift from the
shipped overlay.

Run from the repository root:

```bash
python3 docs/prototypes/controlled-tab-lock/real_chrome_harness.py
```

Add `--verify-idle-close` for the one-time 60-second extension alarm proof. This wait exists only in
the prototype; production source runs must never wait for cleanup.

The harness deliberately refuses to run with the upstream OpenCLI extension or a mismatched fork
build. Load `/Users/frankqdwang/Agents/OpenCLI/extension` as the unpacked extension and reload it
before running the real proof.

## Validated result — 2026-07-14

Validated against bridge build `seektalent-opencli-1.8.6+prototype.1` in the user's existing Chrome
profile. Both commands completed successfully:

```bash
python3 docs/prototypes/controlled-tab-lock/real_chrome_harness.py
python3 docs/prototypes/controlled-tab-lock/real_chrome_harness.py --verify-idle-close
```

The real bridge proof established:

- the fork found the existing Liepin host without claiming or changing it;
- the controlled tab was inactive and placed in the borrowed user window;
- the overlay blocked pointer input while click, fill, scroll, and SPA updates succeeded through the
  explicit automation window;
- OpenCLI state output did not contain the overlay or countdown;
- visual screenshots showed the gray lock, while capture mode removed the gray layer and timer;
- navigation removed the page DOM and the wrapper reinstalled the lock afterward;
- an injected overlay failure did not alter the fixture's business state;
- verified close truly removed the tab; and
- after 60 idle seconds, a follow-up exact close returned `already_missing`, proving the extension
  alarm had already closed the tab instead of navigating it to `about:blank`.

Real Chrome evidence:

- `real-chrome-overlay-visible.png` — user-visible lock state;
- `real-chrome-capture-clean.png` — automation capture state with the interaction blocker retained
  but the gray layer and timer hidden.

One harness assertion originally read scroll position only after the SPA button click. OpenCLI had
correctly scrolled, then scrolled the button back into view before clicking it. The final harness
checks scroll immediately after the scroll command and checks SPA state separately.
