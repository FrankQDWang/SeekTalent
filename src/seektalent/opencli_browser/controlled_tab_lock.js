(() => {
  const lockName = "__seektalentControlledTabLockV1";
  const deadlineName = "__seektalentControlledTabLockDeadlineAt";
  const requestedDeadline = Number(window[deadlineName]);
  delete window[deadlineName];
  const initialDeadline = Number.isFinite(requestedDeadline)
    ? requestedDeadline
    : Date.now() + 60_000;

  const existing = window[lockName];
  if (existing && typeof existing.updateDeadline === "function") {
    existing.updateDeadline(initialDeadline);
    existing.setAutomationActive(false);
    return existing.snapshot();
  }

  const host = document.createElement("div");
  host.id = "seektalent-controlled-tab-lock-v1";
  host.setAttribute("aria-hidden", "true");
  host.tabIndex = -1;
  Object.assign(host.style, {
    all: "initial",
    display: "block",
    position: "fixed",
    inset: "0",
    zIndex: "2147483647",
    pointerEvents: "auto",
  });

  const shadow = host.attachShadow({ mode: "closed" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      .veil {
        position: absolute;
        inset: 0;
        overflow: hidden;
        color: #fff;
        background: rgb(29 34 39 / 58%);
        backdrop-filter: grayscale(.9) blur(1px);
        cursor: not-allowed;
        user-select: none;
      }
      .veil::before {
        position: absolute;
        inset: 0;
        background: linear-gradient(180deg, rgb(9 13 16 / 8%), rgb(9 13 16 / 20%));
        content: "";
      }
      .timer {
        position: absolute;
        bottom: 88px;
        left: 50%;
        display: flex;
        width: min(620px, 62vw);
        align-items: center;
        gap: 14px;
        transform: translateX(-50%);
      }
      .rail {
        position: relative;
        flex: 1;
        height: 1px;
        overflow: hidden;
        background: rgb(255 255 255 / 35%);
      }
      .rail::after {
        position: absolute;
        inset: 0;
        background: rgb(255 255 255 / 88%);
        content: "";
        transform: scaleX(var(--progress));
        transition: transform .25s linear;
      }
      .rail:first-child::after { transform-origin: right; }
      .rail:last-child::after { transform-origin: left; }
      .seconds {
        min-width: 48px;
        color: rgb(255 255 255 / 94%);
        font: 13px/1.2 Inter, ui-sans-serif, system-ui, sans-serif;
        font-variant-numeric: tabular-nums;
        letter-spacing: .08em;
        text-align: center;
        text-shadow: 0 1px 8px rgb(0 0 0 / 35%);
      }
      :host(.capture) .veil {
        background: transparent;
        backdrop-filter: none;
        cursor: default;
      }
      :host(.capture) .veil::before,
      :host(.capture) .timer { display: none; }
    </style>
    <div class="veil">
      <div class="timer" aria-hidden="true">
        <span class="rail"></span>
        <span class="seconds">60s</span>
        <span class="rail"></span>
      </div>
    </div>
  `;

  const timer = shadow.querySelector(".timer");
  const seconds = shadow.querySelector(".seconds");
  let deadlineAt = initialDeadline;
  let automationActive = false;
  let captureMode = false;
  let destroyed = false;

  function render() {
    if (destroyed) return;
    const remaining = Math.max(0, Math.ceil((deadlineAt - Date.now()) / 1000));
    seconds.textContent = `${remaining}s`;
    timer.style.setProperty("--progress", String(Math.min(1, remaining / 60)));
    host.classList.toggle("capture", captureMode);
    host.style.pointerEvents = automationActive ? "none" : "auto";
  }

  function blockTrustedInput(event) {
    if (destroyed || automationActive || event.isTrusted !== true) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  const blockedEvents = [
    "beforeinput",
    "copy",
    "cut",
    "dragstart",
    "drop",
    "keydown",
    "keypress",
    "keyup",
    "paste",
    "touchmove",
    "wheel",
  ];
  for (const eventName of blockedEvents) {
    window.addEventListener(eventName, blockTrustedInput, {
      capture: true,
      passive: false,
    });
  }
  host.addEventListener(
    "pointerdown",
    (event) => {
      if (automationActive) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      try {
        host.focus({ preventScroll: true });
      } catch {
        host.focus();
      }
    },
    true,
  );
  host.addEventListener("contextmenu", blockTrustedInput, true);
  host.addEventListener("wheel", blockTrustedInput, {
    capture: true,
    passive: false,
  });

  const observer = new MutationObserver(() => {
    if (!destroyed && !host.isConnected && document.documentElement) {
      document.documentElement.append(host);
    }
  });
  observer.observe(document.documentElement, { childList: true });
  document.documentElement.append(host);
  const interval = window.setInterval(render, 250);

  const api = {
    updateDeadline(nextDeadline) {
      if (Number.isFinite(Number(nextDeadline))) deadlineAt = Number(nextDeadline);
      render();
      return this.snapshot();
    },
    setAutomationActive(active) {
      automationActive = active === true;
      render();
      return this.snapshot();
    },
    setCaptureMode(active) {
      captureMode = active === true;
      render();
      return this.snapshot();
    },
    snapshot() {
      return {
        installed: !destroyed && host.isConnected,
        automationActive,
        captureMode,
        remainingSeconds: Math.max(0, Math.ceil((deadlineAt - Date.now()) / 1000)),
      };
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      observer.disconnect();
      window.clearInterval(interval);
      for (const eventName of blockedEvents) {
        window.removeEventListener(eventName, blockTrustedInput, true);
      }
      host.remove();
      delete window[lockName];
    },
  };

  window[lockName] = api;
  render();
  return api.snapshot();
})()
