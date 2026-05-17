from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from seektalent.providers.liepin.pi_executor import HmacProviderKeyHasher, PiLiepinExecutor
from seektalent.providers.pi_agent.pi_external import PiRpcAgentClient, PiRpcTaskResult, PiRpcTaskStatus
from tests.test_pi_external_agent import FakeRpcTransport


@dataclass(frozen=True)
class FakeProviderKeyHasher:
    def provider_candidate_hash(self, *, provider: str, material_ref: str) -> str:
        return f"hmac:{provider}:{material_ref.rsplit('/', 1)[-1]}"

    def provider_account_hash(self, *, provider: str, material_ref: str) -> str:
        return f"acct:{provider}:{material_ref.rsplit('/', 1)[-1]}"


@dataclass(frozen=True)
class FakeArtifactRefRegistry:
    refs: frozenset[str]
    materials: dict[str, bytes] | None = None

    def contains_public_artifact_ref(self, ref: str) -> bool:
        return ref in self.refs

    def resolve_material(self, ref: str) -> bytes:
        if ref not in self.refs:
            raise ValueError("artifact ref is not registered")
        if self.materials and ref in self.materials:
            return self.materials[ref]
        return ref.encode("utf-8")


def _registry(*refs: str, materials: dict[str, bytes] | None = None) -> FakeArtifactRefRegistry:
    return FakeArtifactRefRegistry(frozenset(refs), materials)


def _client(
    final_text: str = "",
    *,
    observed_tool_names: tuple[str, ...] = (),
    rpc_status: PiRpcTaskStatus = PiRpcTaskStatus.SUCCEEDED,
) -> PiRpcAgentClient:
    skill_path = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md")
    events = tuple({"type": "tool_execution_start", "toolName": tool_name} for tool_name in observed_tool_names)
    return PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=Path("artifacts/pi-agent"),
        transport=FakeRpcTransport(PiRpcTaskResult(status=rpc_status, final_text=final_text, events=events)),
    )


def _valid_cards_json(*, action_trace_ref: str = "artifact://protected/pi-trace/run-1") -> str:
    return f"""
{{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","stop_reason":"completed","source_run_id":"run-1","query":"python ranking","cards_seen":1,"cards_returned":1,"pages_visited":1,"action_trace_ref":"{action_trace_ref}","safe_summary_refs":[],"protected_snapshot_refs":["artifact://protected/pi-page/run-1"],"cards":[{{"provider_rank":1,"provider_candidate_key_material_ref":"artifact://protected/pi-provider-key/run-1/1","candidate_resume_id":"liepin-1","display_name_masked":true,"safe_card_summary":{{"display_title":"Senior Backend Engineer","current_or_recent_company":"Example","current_or_recent_title":"Senior Backend Engineer","work_years":8,"age":33,"city":"Shanghai","expected_city":"Shanghai","education_level":"master","school_names":["SJTU"],"major_names":["CS"],"skill_tags":["Python","Ranking"],"job_intention":"Backend Engineer","recent_experience_text":"Built ranking systems","normalized_card_text":"senior backend python ranking"}},"safe_card_summary_ref":"artifact://public-summary/pi-card/run-1/1","protected_snapshot_ref":"artifact://protected/pi-card/run-1/1"}}]}}
""".strip()


def test_hmac_provider_key_hasher_resolves_protected_material_inside_runtime() -> None:
    registry = _registry("artifact://protected/pi-provider-key/run-1/1")
    hasher = HmacProviderKeyHasher("runtime-secret", material_resolver=registry)

    candidate_hash = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    )
    account_hash = hasher.provider_account_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    )

    assert candidate_hash != "artifact://protected/pi-provider-key/run-1/1"
    assert candidate_hash != account_hash
    assert hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    ) == candidate_hash


def test_hmac_provider_key_hasher_is_stable_across_artifact_refs_for_same_material() -> None:
    registry = _registry(
        "artifact://protected/pi-provider-key/run-a/1",
        "artifact://protected/pi-provider-key/run-b/1",
        "artifact://protected/pi-provider-key/run-b/2",
        materials={
            "artifact://protected/pi-provider-key/run-a/1": b"same-provider-key",
            "artifact://protected/pi-provider-key/run-b/1": b"same-provider-key",
            "artifact://protected/pi-provider-key/run-b/2": b"different-provider-key",
        },
    )
    hasher = HmacProviderKeyHasher("runtime-secret", material_resolver=registry)

    first = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-a/1",
    )
    retry = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-b/1",
    )
    different = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-b/2",
    )

    assert retry == first
    assert different != first
    assert hasher.provider_account_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-a/1",
    ) != first


def test_pi_liepin_executor_maps_valid_cards_with_runtime_owned_hash() -> None:
    executor = PiLiepinExecutor(
        client=_client(_valid_cards_json()),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "succeeded"
    assert result.card_search is not None
    assert result.card_search.cards[0].payload["providerCandidateKeyHash"] == "hmac:liepin:1"
    assert result.card_search.cards[0].safe_card_summary is not None
    assert result.card_search.cards[0].safe_card_summary.masked_name is True
    assert result.card_search.cards[0].provider_subject_id is None
    assert result.card_search.cards[0].identity_confidence == "synthetic_fingerprint"
    assert result.card_search.cards[0].payload["sourceRunId"] == "run-1"


def test_pi_liepin_executor_sends_non_secret_session_context_to_pi_task() -> None:
    skill_path = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md")
    transport = FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=_valid_cards_json()))
    executor = PiLiepinExecutor(
        client=PiRpcAgentClient(
            command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
            skill_path=skill_path,
            dokobot_tool_name="dokobot",
            timeout_seconds=120,
            artifact_root=Path("artifacts/pi-agent"),
            transport=transport,
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
        connection_id="connection-1",
        provider_account_hash="account-hmac-1",
    )

    assert result.status == "succeeded"
    prompt = transport.prompts[0]
    assert '"connection_id": "connection-1"' in prompt
    assert '"provider_account_hash": "account-hmac-1"' in prompt
    assert "session_id" not in prompt
    assert "provider_account_lock_key" not in prompt


def test_pi_liepin_executor_rejects_business_invariant_violations() -> None:
    payload = _valid_cards_json().replace('"cards_returned":1', '"cards_returned":2')
    executor = PiLiepinExecutor(
        client=_client(payload),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-trace/run-1"),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_rejects_inconsistent_status_and_stop_reason() -> None:
    payload = _valid_cards_json().replace('"stop_reason":"completed"', '"stop_reason":"failed_provider_error"')
    executor = PiLiepinExecutor(
        client=_client(payload),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_rejects_unsafe_external_candidate_resume_id() -> None:
    payload = _valid_cards_json().replace('"candidate_resume_id":"liepin-1"', '"candidate_resume_id":"13800138000"')
    executor = PiLiepinExecutor(
        client=_client(payload),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_treats_rpc_timeout_without_final_cards_as_failed_provider_error() -> None:
    executor = PiLiepinExecutor(
        client=_client(rpc_status=PiRpcTaskStatus.TIMEOUT),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python",
        query_terms=("python",),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.stop_reason == "failed_provider_error"
    assert result.card_search is None


def test_card_mode_rejects_harmless_ref_when_trace_material_shows_detail_route() -> None:
    trace_ref = "artifact://protected/pi-trace/run-1"
    executor = PiLiepinExecutor(
        client=_client(_valid_cards_json(action_trace_ref=trace_ref)),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            trace_ref,
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
            materials={trace_ref: b'{"route_kind":"detail","action_kind":"read"}'},
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_capability_probe_accepts_only_observed_dokobot_tool_evidence() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            '{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}',
            observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is True


def test_session_probe_rejects_non_ready_account_material() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            '{"schema_version":"seektalent.pi_liepin_session_probe.v1","status":"login_required","connection_id":"liepin-pi-agent","provider_account_material_ref":"artifact://protected/pi-account/run-1/current","page_origin":"https://www.liepin.com","stop_reason":"blocked_login_required"}'
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-account/run-1/current"),
    )

    result = executor.probe_session(connection_id="liepin-pi-agent")

    assert result.status == "failed"
    assert result.provider_account_hash is None


def test_runtime_and_workbench_do_not_import_old_dokobot_action_surface() -> None:
    result = subprocess.run(
        [
            "rg",
            "-n",
            "DokoBotActionSurface|DokoBotActionTransportSession|DokoBotLiepinSearchCardsExecutor|DOKOBOT_ACTION|LEGACY_WORKER_COMPAT|dokobot_action|legacy_worker_compat",
            "src/seektalent",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stdout
