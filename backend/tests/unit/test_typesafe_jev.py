"""TypesafeJevProvider 单元测试（Phase 3 外部 JEV API）。

锁定行为：
- 有效应答 → APPROVED；risk/execution block → REJECTED；conflict+adverse → REJECTED
- 通过但置信度不足 → CAUTION；risk/execution 置信低于 min_confidence → 降级
- 畸形应答（概率和≠1 / choice≠argmax / 选项未知 / 置信非法）→ ProviderUnavailable
- 网络故障 → 降级 + 熔断（连续失败 N 次后冷却期内跳过，不再发请求）
- state payload 形状正确（action/quantity/notional/reference_price）
- 密钥绝不进 decision 的输出（审计块/reasoning/risk_flags）
"""

import json

import httpx
import pytest
from types import SimpleNamespace

from app.config import settings
from app.services.systemone import ProviderUnavailable
from app.services.typesafe_jev import JEV_CHECK_OPTIONS, TypesafeJevProvider


def _q_payload(question: str, choice: str, confidence: float = 0.8) -> dict:
    options = sorted(JEV_CHECK_OPTIONS[question])
    probs = {o: 0.01 for o in options}
    probs[choice] = 1.0 - 0.01 * (len(options) - 1)
    return {"choice": choice, "probabilities": probs, "confidence": confidence}


def _answers(**overrides) -> dict:
    base = {
        "data_quality": _q_payload("data_quality", "sufficient"),
        "signal_alignment": _q_payload("signal_alignment", "aligned"),
        "market_regime": _q_payload("market_regime", "neutral"),
        "risk_check": _q_payload("risk_check", "clear", 0.8),
        "execution_quality": _q_payload("execution_quality", "clear", 0.8),
    }
    base.update(overrides)
    return base


def _ctx(**kw):
    defaults = dict(symbol="GOLD", entry_ref=2000.0, sl=1985.0, tp=2050.0, lot=0.1,
                    spread=0.5, account={"balance": 10000.0, "equity": 10000.0, "leverage": 100},
                    positions=[], tick={"bid": 2000.0, "ask": 2000.5})
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _snapshot(**kw):
    defaults = {
        "order": {"type": "BUY", "symbol": "GOLD", "lot": 0.1, "sl": 1985.0, "tp": 2050.0},
        "market": {"bid": 2000.0, "ask": 2000.5, "spread": 0.5, "sentiment": None},
        "rule_flags": [],
        "recent_trades": [],
    }
    defaults.update(kw)
    return defaults


@pytest.fixture
def _credentials(monkeypatch):
    monkeypatch.setattr(settings, "manual_review_typesafe_api_key", "sk-test-jev-key")
    monkeypatch.setattr(settings, "manual_review_typesafe_base_url", "https://api.test/v1/systemone")
    monkeypatch.setattr(settings, "manual_review_min_confidence", 0.55)
    # 固定分诊地板：.env 里按真实模型调过（0.15），测试必须显式钉住以保证确定性
    monkeypatch.setattr(settings, "manual_review_typesafe_conf_floor", 0.30)


def _provider(handler, monkeypatch):
    """monkeypatch make_client 注入 MockTransport 的 provider 实例。"""
    def _make(proxy, timeout_s):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout_s)

    monkeypatch.setattr("app.services.typesafe_jev.make_client", _make)
    return TypesafeJevProvider()


def _json_handler(payload: dict, status: int = 200, captured: list | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append({"url": str(request.url), "json": json.loads(request.content or b"{}"),
                             "headers": dict(request.headers)})
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(200, json={"answers": payload})
    return handler


# ─── 判决映射 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_answers_approve(_credentials, monkeypatch):
    p = _provider(_json_handler(_answers()), monkeypatch)
    d = await p.evaluate(_snapshot(), _ctx())
    assert d.verdict == "APPROVED"
    assert d.provider == "typesafe_jev"
    assert len(d.checks) == 5
    assert d.confidence >= settings.manual_review_min_confidence


@pytest.mark.asyncio
async def test_risk_block_rejects(_credentials, monkeypatch):
    p = _provider(_json_handler(_answers(risk_check=_q_payload("risk_check", "block", 0.9))), monkeypatch)
    d = await p.evaluate(_snapshot(), _ctx())
    assert d.verdict == "REJECTED"
    assert "风险阻断" in d.reasoning


@pytest.mark.asyncio
async def test_signal_conflict_plus_adverse_rejects(_credentials, monkeypatch):
    p = _provider(_json_handler(_answers(
        signal_alignment=_q_payload("signal_alignment", "conflict", 0.8),
        market_regime=_q_payload("market_regime", "adverse", 0.8),
    )), monkeypatch)
    d = await p.evaluate(_snapshot(), _ctx())
    assert d.verdict == "REJECTED"
    assert "信号冲突" in d.reasoning


@pytest.mark.asyncio
async def test_caution_check_pass_caution(_credentials, monkeypatch):
    """JEV 通过但 risk_check=caution → CAUTION（人工确认兜底，不放松 firewall）。
    注：置信度 < min_confidence 是降级条件（test_confidence_below_threshold_degrades），
    不走 CAUTION——低置信通过票不可信。"""
    p = _provider(_json_handler(_answers(
        risk_check=_q_payload("risk_check", "caution", 0.8))), monkeypatch)
    d = await p.evaluate(_snapshot(), _ctx())
    assert d.verdict == "CAUTION"


@pytest.mark.asyncio
async def test_confidence_below_floor_degrades(_credentials, monkeypatch):
    """risk 置信 0.2 < 地板 0.30 → 应答近似噪声 → ProviderUnavailable（降级）。"""
    p = _provider(_json_handler(_answers(
        risk_check=_q_payload("risk_check", "clear", 0.2))), monkeypatch)
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())


@pytest.mark.asyncio
async def test_confidence_between_floor_and_min_caution(_credentials, monkeypatch):
    """置信 0.45 在地板(0.30)~min(0.55) 之间 → CAUTION 人工确认（free 模型常态）。"""
    p = _provider(_json_handler(_answers(
        risk_check=_q_payload("risk_check", "clear", 0.45))), monkeypatch)
    d = await p.evaluate(_snapshot(), _ctx())
    assert d.verdict == "CAUTION"


# ─── 严格校验 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_probabilities_sum_degrades(_credentials, monkeypatch):
    a = _answers()
    a["risk_check"]["probabilities"] = {"clear": 0.7, "caution": 0.5, "block": 0.0, "insufficient": 0.0}
    p = _provider(_json_handler(a), monkeypatch)
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())


@pytest.mark.asyncio
async def test_choice_not_argmax_degrades(_credentials, monkeypatch):
    a = _answers()
    a["risk_check"]["choice"] = "caution"  # 概率最大项仍是 clear
    p = _provider(_json_handler(a), monkeypatch)
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())


@pytest.mark.asyncio
async def test_unknown_choice_degrades(_credentials, monkeypatch):
    a = _answers()
    a["risk_check"]["choice"] = "maybe"
    p = _provider(_json_handler(a), monkeypatch)
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())


@pytest.mark.asyncio
async def test_http_error_degrades(_credentials, monkeypatch):
    p = _provider(_json_handler({}, status=500), monkeypatch)
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())


# ─── 熔断与配置 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_not_configured_degrades(monkeypatch):
    monkeypatch.setattr(settings, "manual_review_typesafe_api_key", "")
    p = TypesafeJevProvider()
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold(_credentials, monkeypatch):
    monkeypatch.setattr(settings, "manual_review_typesafe_circuit_threshold", 2)
    monkeypatch.setattr(settings, "manual_review_typesafe_circuit_cooldown_s", 60)
    calls = []

    async def boom(request):
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    p = _provider(boom, monkeypatch)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await p.evaluate(_snapshot(), _ctx())
    assert p._failures == 2
    # 熔断开启：第 3 次不再发请求
    with pytest.raises(ProviderUnavailable):
        await p.evaluate(_snapshot(), _ctx())
    assert len(calls) == 2


# ─── state 形状与密钥卫生 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_payload_shape(_credentials, monkeypatch):
    captured: list = []

    async def handler(request):
        captured.append({"url": str(request.url), "json": json.loads(request.content or b"{}")})
        return httpx.Response(200, json={"answers": _answers()})

    p = _provider(handler, monkeypatch)
    await p.evaluate(_snapshot(), _ctx())
    body = captured[0]["json"]
    assert captured[0]["url"].endswith("/systemone")
    assert body["model"]  # 非空
    assert set(body["questions"]) == set(JEV_CHECK_OPTIONS)
    state = body["state"]
    assert state["action"] == "buy"
    assert state["quantity"] == 0.1
    assert state["reference_price"] == 2000.0
    assert state["notional"] == pytest.approx(200.0)
    assert state["context"]["sl"] == 1985.0


@pytest.mark.asyncio
async def test_api_key_never_leaks_into_decision(_credentials, monkeypatch):
    p = _provider(_json_handler(_answers()), monkeypatch)
    d = await p.evaluate(_snapshot(), _ctx())
    blob = json.dumps({"audit": d.audit_block(), "shape": d.to_review_llm_shape(),
                       "flags": d.risk_flags}, ensure_ascii=False)
    assert "sk-test-jev-key" not in blob
    assert "sk-test" not in blob