from __future__ import annotations

import pytest

from seektalent import domi_workbench


JWT_MISSING_MESSAGE = "未获取到 Domi 大模型授权。请在当前终端设置 SEEKTALENT_DOMI_JWT 后重试。"
NODE_MISSING_MESSAGE = "未找到 Domi Node 运行时。请在当前终端设置 SEEKTALENT_DOMI_NODE 或 DOMI_NODE 后重试。"


def test_prepare_domi_env_requires_domi_jwt() -> None:
    assert domi_workbench.prepare_domi_env({}) == ("seektalent_domi_jwt_missing", JWT_MISSING_MESSAGE)


def test_prepare_domi_env_requires_domi_node() -> None:
    assert domi_workbench.prepare_domi_env({"SEEKTALENT_DOMI_JWT": "jwt"}) == (
        "domi_node_missing",
        NODE_MISSING_MESSAGE,
    )


def test_prepare_domi_env_normalizes_domi_env_from_domi_node() -> None:
    env = {
        "SEEKTALENT_DOMI_JWT": "jwt",
        "DOMI_NODE": "/opt/domi/bin/node",
    }

    assert domi_workbench.prepare_domi_env(env) is None

    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert env["SEEKTALENT_OPENCLI_NODE_POLICY"] == "domi"
    assert env["SEEKTALENT_OPENCLI_NODE"] == "/opt/domi/bin/node"
    assert env["SEEKTALENT_DOMI_LLM_CHANNEL"] == "seek_talent"


def test_main_delegates_to_workbench_with_domi_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("SEEKTALENT_DOMI_JWT", "jwt")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", "/opt/domi/bin/node")
    monkeypatch.delenv("DOMI_NODE", raising=False)
    monkeypatch.setattr(domi_workbench, "seektalent_main", lambda argv: calls.append(list(argv)) or 7)

    assert domi_workbench.main(["--port", "8022"]) == 7

    captured = capsys.readouterr()
    assert captured.err == ""
    assert calls == [["workbench", "--port", "8022"]]


def test_main_reports_missing_domi_jwt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("SEEKTALENT_DOMI_JWT", raising=False)
    monkeypatch.delenv("SEEKTALENT_DOMI_NODE", raising=False)
    monkeypatch.delenv("DOMI_NODE", raising=False)

    assert domi_workbench.main([]) == 1

    captured = capsys.readouterr()
    assert "reason_code=seektalent_domi_jwt_missing" in captured.err
    assert JWT_MISSING_MESSAGE in captured.err
