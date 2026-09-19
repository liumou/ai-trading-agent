"""
Phase 0 SDK spike — 验证 MT5 Python SDK 在同终端内切换账号的真实行为。

这是"阻断项"验证：计划的 Phase 3-7 全部建立在此结论之上。
**必须在真实 Windows VPS 上、用真实账号运行（MetaTrader5 仅 Windows 可 import）。**

用法（在 VPS 上，mt5_bridge/ 目录）：
    set MT5_PATH=<terminal64.exe 路径>
    set ACCOUNT_A_LOGIN=...
    set ACCOUNT_A_PASSWORD=...
    set ACCOUNT_B_LOGIN=...
    set ACCOUNT_B_PASSWORD=...
    python spike_switch_account.py

脚本会尝试多种调用顺序，记录每一步的返回码 / last_error() / 时间，
输出 JSON 结果。运行本身不会下任何订单（只查询）。

关键验证点（对应 task_plan Phase 0 清单）：
1. initialize() 后 login(A) 成功？
2. 已连接 A 状态下直接 login(B) 是否成功 / 返回码 / 是否需先 shutdown()
3. account_info() 切换延迟；切换后 symbols_get()/positions_get() 是否立即反映 B
4. 无人值守 VPS 上是否弹出账户切换确认框（脚本无法直接检测弹窗，但
   通过"login() 是否长时间挂起 / 返回超时码"推断；请同时肉眼观察屏幕）
5. 失败场景（错密码）返回码；能否回滚到 A
"""

import json
import os
import time
from datetime import datetime

# 延迟 import：MetaTrader5 仅 Windows 可 import，本脚本只在 VPS 上跑
import MetaTrader5 as mt5

MT5_PATH = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")
A_LOGIN = int(os.getenv("ACCOUNT_A_LOGIN", "0"))
A_PASSWORD = os.getenv("ACCOUNT_A_PASSWORD", "")
A_SERVER = os.getenv("ACCOUNT_A_SERVER", "")
B_LOGIN = int(os.getenv("ACCOUNT_B_LOGIN", "0"))
B_PASSWORD = os.getenv("ACCOUNT_B_PASSWORD", "")
B_SERVER = os.getenv("ACCOUNT_B_SERVER", "")
# 可选的第三个"失败"账号（错密码），用于验证失败/回滚
C_LOGIN = int(os.getenv("ACCOUNT_C_LOGIN", "0"))
C_PASSWORD = os.getenv("ACCOUNT_C_PASSWORD", "")

results: dict = {"checks": []}


def record(name: str, ok: bool, detail: str, extra: dict | None = None) -> None:
    entry = {
        "check": name,
        "ok": ok,
        "detail": detail,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if extra:
        entry.update(extra)
    results["checks"].append(entry)
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")


def account_snapshot() -> dict:
    """当前账号关键信息快照，用于对比切换前后。"""
    info = mt5.account_info()
    if info is None:
        return {"login": None, "server": None, "balance": None}
    return {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
    }


def timed(fn, timeout: float = 120.0):
    """执行 fn，捕获超时（login 挂起常见于弹窗等待）。"""
    start = time.monotonic()
    try:
        val = fn()
        return val, time.monotonic() - start, None
    except Exception as e:  # noqa: BLE001
        return None, time.monotonic() - start, str(e)


def main() -> None:
    print(f"MT5_PATH = {MT5_PATH}")
    print(f"A = {A_LOGIN} @ {A_SERVER or '(默认)'}")
    print(f"B = {B_LOGIN} @ {B_SERVER or '(默认)'}")
    print(f"C (失败账号) = {C_LOGIN or '(未设置)'}")
    print(f"MT5 版本: {mt5.__version__}\n")

    # ── 步骤 1: initialize + login(A) ─────────────────────────────────────
    ok, dt, err = timed(lambda: mt5.initialize(MT5_PATH))
    record("initialize", ok is True, f"ret={ok!r} err={err} dt={dt:.2f}s")
    if ok is not True:
        record("initialize_last_error", False, str(mt5.last_error()))
        results["fatal"] = "initialize failed — cannot proceed"
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    t0 = time.monotonic()
    ok_a, dt_a, err_a = timed(lambda: mt5.login(A_LOGIN, password=A_PASSWORD, server=A_SERVER or None))
    record(
        "login_A_after_initialize",
        ok_a is True,
        f"ret={ok_a!r} err={err_a} dt={dt_a:.2f}s",
        {"snapshot_after": account_snapshot()},
    )
    if ok_a is not True:
        record("login_A_last_error", False, str(mt5.last_error()))
    time.sleep(1)
    results["baseline_A"] = account_snapshot()

    # ── 步骤 2: 已连接 A 状态下直接 login(B)（核心验证点） ──────────────
    print("\n── 步骤 2: 已连接 A 状态下直接 login(B) ──")
    t0 = time.monotonic()
    ok_b, dt_b, err_b = timed(lambda: mt5.login(B_LOGIN, password=B_PASSWORD, server=B_SERVER or None))
    record(
        "login_B_while_connected",
        ok_b is True,
        f"ret={ok_b!r} err={err_b} dt={dt_b:.2f}s (挂了 {dt_b:.1f}s)",
        {"snapshot_after": account_snapshot(), "elapsed_from_A": time.monotonic() - t0},
    )
    if ok_b is not True:
        record("login_B_last_error", False, str(mt5.last_error()))
        # 若直接 login 失败，尝试先 shutdown 再 initialize+login（备选顺序）
        print("  → 直接 login(B) 失败，尝试 shutdown+reinit+login 备选顺序...")
        ok_sh, dt_sh, err_sh = timed(lambda: mt5.shutdown())
        record("shutdown", ok_sh is True, f"ret={ok_sh!r} err={err_sh} dt={dt_sh:.2f}s")
        time.sleep(2)
        ok_re, dt_re, err_re = timed(lambda: mt5.initialize(MT5_PATH))
        record("reinitialize", ok_re is True, f"ret={ok_re!r} err={err_re} dt={dt_re:.2f}s")
        ok_b2, dt_b2, err_b2 = timed(lambda: mt5.login(B_LOGIN, password=B_PASSWORD, server=B_SERVER or None))
        record(
            "login_B_after_shutdown",
            ok_b2 is True,
            f"ret={ok_b2!r} err={err_b2} dt={dt_b2:.2f}s",
            {"snapshot_after": account_snapshot()},
        )

    # 当前是否落在 B
    cur = account_snapshot()
    results["after_login_B"] = cur
    on_b = cur["login"] == B_LOGIN

    # ── 步骤 3: 切换后数据反映 ─────────────────────────────────────────
    print("\n── 步骤 3: 切换后数据反映 ──")
    if on_b:
        time.sleep(3)  # 等待终端同步
        syms = mt5.symbols_get()
        pos = mt5.positions_get()
        n_syms = len(syms) if syms else 0
        n_pos = len(pos) if pos else 0
        record(
            "data_reflects_new_account",
            n_syms > 0,
            f"symbols={n_syms} positions={n_pos}",
            {"symbols_count": n_syms, "positions_count": n_pos},
        )
    else:
        record("data_reflects_new_account", False, "当前不在账号 B，跳过数据检查", {"symbols_count": None})

    # ── 步骤 4: 失败场景 + 回滚 ─────────────────────────────────────────
    print("\n── 步骤 4: 失败场景 + 回滚 ──")
    if C_LOGIN:
        ok_c, dt_c, err_c = timed(lambda: mt5.login(C_LOGIN, password=C_PASSWORD, server=B_SERVER or None))
        record(
            "login_C_bad_password",
            ok_c is not True,
            f"ret={ok_c!r} err={err_c} dt={dt_c:.2f}s (预期 False=被拒绝)",
            {"snapshot_after": account_snapshot()},
        )
        if ok_c is not True:
            record("login_C_last_error", False, str(mt5.last_error()))
        # 回滚到 B（最近可用账号）
        ok_roll, dt_roll, err_roll = timed(lambda: mt5.login(B_LOGIN, password=B_PASSWORD, server=B_SERVER or None))
        record(
            "rollback_to_B_after_failure",
            ok_roll is True,
            f"ret={ok_roll!r} err={err_roll} dt={dt_roll:.2f}s",
            {"snapshot_after": account_snapshot()},
        )

    # ── 步骤 5: 弹窗检测提示（无法自动，靠人工观察 + 挂起推断） ──────
    # 上面每步都打了耗时；若某步 login() 挂起 >30s 后返回 False，通常是
    # 终端弹了确认框在等人点。无人值守 VPS 上这是阻断性风险。
    slow = [c for c in results["checks"] if "dt=" in c.get("detail", "") and any(
        float(s) > 30 for s in _extract_durations(c.get("detail", ""))
    )]
    record(
        "hang_detection",
        len(slow) == 0,
        f"检测到 {len(slow)} 个 login 挂起 >30s（若有，可能弹窗等待；请肉眼确认终端屏幕）",
        {"slow_checks": [c["check"] for c in slow]},
    )

    results["conclusion"] = _conclude(on_b)
    print("\n\n=== 结论 ===")
    print(results["conclusion"])
    print("\n=== 完整 JSON ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def _extract_durations(detail: str) -> list[float]:
    import re

    return [float(x) for x in re.findall(r"dt=([\d.]+)s", detail)]


def _conclude(on_b: bool) -> str:
    lines = []
    if on_b:
        lines.append(
            "直接 login(B) 切换成功（或经 shutdown 备选顺序成功）→ 同终端切换可行，"
            "Phase 3-7 可继续。请确认：无弹窗挂起、data 反映新账号、失败回滚可用。"
        )
    else:
        lines.append(
            "未能切到 B → 同终端直接 login 切换可能不可行（或需其它顺序）。"
            "请人工核对上面各步的 last_error 与耗时，若弹窗挂起则需换方案（配置+重启 / 多实例）。"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
