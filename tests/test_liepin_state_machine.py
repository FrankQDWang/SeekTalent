from seektalent.providers.liepin.liepin_state_machine import (
    LiepinStateSnapshot,
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)


def test_transition_runner_observes_latest_state_before_and_after_action() -> None:
    calls: list[str] = []

    def observe_pre() -> LiepinStateSnapshot:
        calls.append("observe_pre")
        return LiepinStateSnapshot(ok=True, text="ready")

    def precondition(snapshot: LiepinStateSnapshot) -> bool:
        calls.append("pre")
        return snapshot.text == "ready"

    def action() -> TransitionResult:
        calls.append("action")
        return TransitionResult(ok=True)

    def observe_post() -> LiepinStateSnapshot:
        calls.append("observe_post")
        return LiepinStateSnapshot(ok=True, text="done")

    def postcondition(snapshot: LiepinStateSnapshot) -> bool:
        calls.append("post")
        return snapshot.text == "done"

    result = LiepinTransitionRunner().run(
        LiepinTransition(
            name="open_detail",
            phase="detail",
            observe_pre_state=observe_pre,
            precondition=precondition,
            action=action,
            observe_post_state=observe_post,
            postcondition=postcondition,
            safe_reason_code="liepin_opencli_detail_not_opened",
            trace_event="liepin.detail.open",
        )
    )

    assert calls == ["observe_pre", "pre", "action", "observe_post", "post"]
    assert result == TransitionResult(ok=True)
    assert result.safe_reason_code is None


def test_transition_runner_does_not_run_action_when_precondition_fails() -> None:
    calls: list[str] = []

    def action() -> TransitionResult:
        calls.append("action")
        return TransitionResult(ok=True)

    result = LiepinTransitionRunner().run(
        LiepinTransition(
            name="apply_filter",
            phase="search",
            observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="blocked"),
            precondition=lambda snapshot: snapshot.text == "ready",
            action=action,
            observe_post_state=lambda: LiepinStateSnapshot(ok=True, text="done"),
            postcondition=lambda snapshot: snapshot.text == "done",
            safe_reason_code="liepin_opencli_filter_not_ready",
            trace_event="liepin.filter.apply",
        )
    )

    assert calls == []
    assert result == TransitionResult(
        ok=False,
        safe_reason_code="liepin_opencli_filter_not_ready",
        debug_reason="precondition_failed",
    )


def test_transition_runner_does_not_repeat_toggle_when_postcondition_is_unknown() -> None:
    action_calls = 0

    def action() -> TransitionResult:
        nonlocal action_calls
        action_calls += 1
        return TransitionResult(ok=True, event={"clicked": True})

    result = LiepinTransitionRunner().run(
        LiepinTransition(
            name="toggle_filter",
            phase="search",
            observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="ready"),
            precondition=lambda snapshot: snapshot.text == "ready",
            action=action,
            observe_post_state=lambda: LiepinStateSnapshot(ok=True, text="unknown"),
            postcondition=lambda snapshot: snapshot.text == "enabled",
            safe_reason_code="liepin_opencli_filter_toggle_unknown",
            trace_event="liepin.filter.toggle",
            retry_policy="no_repeat_toggle",
        )
    )

    assert action_calls == 1
    assert result == TransitionResult(
        ok=False,
        safe_reason_code="liepin_opencli_filter_toggle_unknown",
        debug_reason="postcondition_failed",
        event={"clicked": True},
    )


def test_transition_runner_propagates_action_safe_reason_code() -> None:
    action_result = TransitionResult(
        ok=False,
        safe_reason_code="liepin_opencli_status_unavailable",
        debug_reason="opencli_status_failed",
        event={"status": "down"},
    )

    result = LiepinTransitionRunner().run(
        LiepinTransition(
            name="status",
            phase="preflight",
            observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="ready"),
            precondition=lambda snapshot: snapshot.text == "ready",
            action=lambda: action_result,
            observe_post_state=lambda: LiepinStateSnapshot(ok=True, text="done"),
            postcondition=lambda snapshot: snapshot.text == "done",
            safe_reason_code="liepin_opencli_preflight_failed",
            trace_event="liepin.preflight.status",
        )
    )

    assert result is action_result


def test_transition_runner_stops_when_pre_state_is_terminal() -> None:
    calls: list[str] = []

    result = LiepinTransitionRunner().run(
        LiepinTransition(
            name="open_card",
            phase="detail",
            observe_pre_state=lambda: LiepinStateSnapshot(
                ok=False,
                text="terminal",
                safe_reason_code="liepin_opencli_terminal_state",
            ),
            precondition=lambda snapshot: snapshot.text == "ready",
            action=lambda: TransitionResult(ok=True),
            observe_post_state=lambda: calls.append("observe_post")  # type: ignore[func-returns-value]
            or LiepinStateSnapshot(ok=True, text="done"),
            postcondition=lambda snapshot: snapshot.text == "done",
            safe_reason_code="liepin_opencli_detail_not_opened",
            trace_event="liepin.detail.open",
        )
    )

    assert calls == []
    assert result == TransitionResult(
        ok=False,
        safe_reason_code="liepin_opencli_terminal_state",
        debug_reason="pre_state_failed",
    )


def test_transition_runner_preserves_event_when_post_state_fails() -> None:
    result = LiepinTransitionRunner().run(
        LiepinTransition(
            name="capture_detail",
            phase="detail",
            observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="ready"),
            precondition=lambda snapshot: snapshot.text == "ready",
            action=lambda: TransitionResult(ok=True, event={"candidate_id": "123"}),
            observe_post_state=lambda: LiepinStateSnapshot(
                ok=False,
                text="missing",
                safe_reason_code="liepin_opencli_detail_missing",
            ),
            postcondition=lambda snapshot: snapshot.text == "captured",
            safe_reason_code="liepin_opencli_detail_not_captured",
            trace_event="liepin.detail.capture",
        )
    )

    assert result == TransitionResult(
        ok=False,
        safe_reason_code="liepin_opencli_detail_missing",
        debug_reason="post_state_failed",
        event={"candidate_id": "123"},
    )
