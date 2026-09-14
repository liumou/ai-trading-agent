"""Backend message translation.

Translates user-facing error messages (HTTPException detail and similar
plain-text messages) from English into the client's preferred language,
selected via the Accept-Language header. Messages are matched against
regex templates so interpolated values (symbol names, modes, ...) are
preserved and re-inserted into the translated string.
"""
from __future__ import annotations

import re
from typing import NamedTuple

SUPPORTED = ("zh", "en")
DEFAULT = "en"


class Msg(NamedTuple):
    pattern: re.Pattern[str]
    zh: str


def _p(en: str, zh: str) -> Msg:
    """Build a message entry. Placeholders in the English template use
    {name}; they are converted to named regex groups so interpolated
    runtime values can be captured and re-inserted."""
    parts = re.split(r"\{(\w+)\}", en)
    pattern = ""
    for i, part in enumerate(parts):
        if i % 2 == 0:
            pattern += re.escape(part)
        else:
            pattern += f"(?P<{part}>.+?)"
    return Msg(re.compile("^" + pattern + "$"), zh)


CATALOG: list[Msg] = [
    _p("Agent '{agent_id}' not found", "未找到 Agent「{agent_id}」"),
    _p("Authentication is not configured", "认证未配置"),
    _p("Authentication required", "需要认证"),
    _p("Bot manager not initialized", "机器人管理器未初始化"),
    _p("Bot not initialized", "机器人未初始化"),
    _p("Cannot apply optimization in AI Autonomous mode", "AI 自主模式下无法应用优化"),
    _p("Cannot get account info", "无法获取账户信息"),
    _p("Cannot optimize in AI Autonomous mode — select a strategy first", "AI 自主模式下无法优化——请先选择策略"),
    _p("Credential payload too large", "凭证数据过大"),
    _p("Data collector not initialized", "数据采集器未初始化"),
    _p("Event calendar not initialized", "事件日历未初始化"),
    _p("Failed to remove runner", "移除 Runner 失败"),
    _p("Invalid 'since' datetime format", "'since' 日期时间格式无效"),
    _p("Invalid credentials", "凭证无效"),
    _p("Invalid date format. Use YYYY-MM-DD", "日期格式无效，请使用 YYYY-MM-DD"),
    _p("Invalid or expired token", "令牌无效或已过期"),
    _p("Invalid session", "会话无效"),
    _p("Invalid setup token", "安装令牌无效"),
    _p("Invalid webhook key", "Webhook 密钥无效"),
    _p("Job not found", "未找到任务"),
    _p("Job queue not initialized", "任务队列未初始化"),
    _p("Login challenge expired", "登录质询已过期"),
    _p("Macro service not initialized", "宏观服务未初始化"),
    _p("Market data service not available", "行情数据服务不可用"),
    _p("Missing or invalid challenge_key", "缺少或无效的 challenge_key"),
    _p("Missing X-Setup-Token header", "缺少 X-Setup-Token 请求头"),
    _p("ML dependencies not initialized", "ML 依赖未初始化"),
    _p("MT5 connector unavailable", "MT5 连接器不可用"),
    _p("No session cookie", "无会话 Cookie"),
    _p("Not authenticated", "未认证"),
    _p("Not initialized", "未初始化"),
    _p("Optimization failed", "优化失败"),
    _p("Optimization log not found", "未找到优化日志"),
    _p("Optimizer not configured", "未配置优化器"),
    _p("Registration challenge expired", "注册质询已过期"),
    _p("Runner manager not initialized", "Runner 管理器未初始化"),
    _p("Runner not found", "未找到 Runner"),
    _p("Scheduler or engine unavailable", "调度器或引擎不可用"),
    _p("Secret '{key}' not found", "未找到密钥「{key}」"),
    _p("Session revoked", "会话已撤销"),
    _p("Setup not complete. Register a passkey first.", "安装未完成，请先注册通行密钥。"),
    _p("Stop the bot before applying optimization", "应用优化前请先停止机器人"),
    _p("Symbol '{req.symbol}' already exists", "品种「{req.symbol}」已存在"),
    _p("Symbol '{symbol}' already training", "品种「{symbol}」已在训练中"),
    _p("Symbol '{symbol}' not found", "未找到品种「{symbol}」"),
    _p("Symbol {symbol} not configured", "品种 {symbol} 未配置"),
    _p("Symbol {symbol} not active. Available: {available}", "品种 {symbol} 未激活。可用：{available}"),
    _p("TradingView webhook key not configured", "TradingView Webhook 密钥未配置"),
    _p("Unknown credential", "未知凭证"),
    _p("WebAuthn registration disabled. Set WEBAUTHN_SETUP_TOKEN env var to enable initial setup.",
      "WebAuthn 注册已禁用。设置 WEBAUTHN_SETUP_TOKEN 环境变量以启用初始安装。"),
    _p("replay detected", "检测到重放攻击"),
    _p("webhook expired", "Webhook 已过期"),
    _p("timestamp and nonce required", "需要时间戳和 nonce"),
    _p("passkey_registered", "通行密钥已注册"),
    _p("session_check_unavailable", "会话检查不可用"),
]

# Patterns for messages built at runtime with f-strings that could not be
# matched literally (contain code like join(...) or str(e)); matched best-effort.
_FSTRING_CATALOG: list[Msg] = [
    Msg(re.compile(r"^Invalid action: (.+?)\. Must be BUY or SELL$"), "无效操作：{0}。必须是 BUY 或 SELL"),
    Msg(re.compile(r"^Invalid status: (.+?)$"), "无效状态：{0}"),
    Msg(re.compile(r"^Authentication failed: (.+?)$"), "认证失败：{0}"),
    Msg(re.compile(r"^Registration failed: (.+?)$"), "注册失败：{0}"),
    Msg(re.compile(r"^Cannot jump from '(.+?)' to '(.+?)'\. Must transition sequentially\.$"), "无法从「{0}」跳转到「{1}」，必须按顺序切换。"),
    # ML 障碍护栏（app/ml/barrier_validation.py）。该文案里同一个参数名出现多次，
    # 无法用 _p()（会重复定义命名组），故用反向引用 \1 的正则模板。
    Msg(
        re.compile(
            r"^(ml_tp_pips|ml_sl_pips) × pip_value = (.+?) is (.+?)× the symbol's "
            r"mean bar range \((.+?)\)\. Barriers this far from typical bar volatility "
            r"cannot produce a 3-class \(BUY/SELL/HOLD\) training set\. Keep \1 within "
            r"roughly \[(.+?), (.+?)\]× mean bar range, i\.e\. \1 ≈ \[(.+?), (.+?)\] "
            r"\(recommended (.+?)\) for pip_value=(.+?)\. Note: this parameter only "
            r"shapes training labels — it does NOT set live SL/TP; use tp_atr_mult / "
            r"sl_atr_mult for execution\.$"
        ),
        "「{0}」× 点值 = {1}，是该品种平均单根K线波幅（{3}）的 {2} 倍。"
        "屏障离典型波动过远，无法生成 BUY/SELL/HOLD 三类训练标签。"
        "请把 {0} 保持在波幅的约 [{4}, {5}] 倍以内，即 {0} ≈ [{6}, {7}]"
        "（建议 {8}）——按当前点值 {9} 换算。"
        "注意：该参数只影响训练标签，不控制实盘止盈止损；实盘请改用 tp_atr_mult / sl_atr_mult。",
    ),
    Msg(
        re.compile(
            r"^(ml_tp_pips|ml_sl_pips) × pip_value = (.+?) is implausible\. "
            r"Check ml_tp_pips / ml_sl_pips and pip_value\.$"
        ),
        "「{0}」× 点值 = {1} 明显不合理。请检查 ml_tp_pips / ml_sl_pips 与点值。",
    ),
]


def pick_language(accept_language: str | None, default: str = DEFAULT) -> str:
    """挑选请求语言（zh/en）。

    ``default`` 用于无 Accept-Language 或未匹配时的回退。错误文案翻译沿用
    默认 "en"（历史行为不变）；LLM 输出语言经 app/ai/language.py 用
    settings.llm_response_lang 作为默认传入。
    """
    if not accept_language:
        return default
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("zh"):
            return "zh"
        if tag.startswith("en"):
            return "en"
    return default


def translate_detail(detail: str, lang: str = "zh") -> str:
    """Translate an English message into `lang`; returns the original text
    when no translation is available."""
    if lang == "en" or not isinstance(detail, str):
        return detail
    for msg in CATALOG:
        m = msg.pattern.match(detail)
        if m:
            try:
                return msg.zh.format(**m.groupdict())
            except (KeyError, IndexError):
                return detail
    for msg in _FSTRING_CATALOG:
        m = msg.pattern.match(detail)
        if m:
            try:
                return msg.zh.format(*m.groups())
            except (KeyError, IndexError):
                return detail
    return detail
