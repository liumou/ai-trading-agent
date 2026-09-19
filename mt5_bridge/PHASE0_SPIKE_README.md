# Phase 0 — MT5 SDK 同终端切换账号验证（spike）

## 为什么先做这个

计划的核心假设：**MT5 Python SDK 可在已连接的终端内直接 `mt5.login(新账号)` 切换账号**。
这个假设从未在生产代码走过（现网 `ensure_connected()` 只在断线时才 login），官方文档也没有明确说明。
它不成立则 Phase 3-7 全部白做。因此**先花 10 分钟真实验证**，结论通过才继续。

## 运行步骤（在 Windows VPS 上）

1. 把 `mt5_bridge/spike_switch_account.py` 传到 VPS 的 `mt5_bridge/` 目录。

2. 设置两个（最好三个：含一个错密码的）真实账号环境变量，然后运行：

```bat
cd mt5_bridge

set MT5_PATH=C:\Program Files\XM Global MT5\terminal64.exe
set ACCOUNT_A_LOGIN=336773771
set ACCOUNT_A_PASSWORD=***
set ACCOUNT_A_SERVER=XMGlobal-MT5 9
set ACCOUNT_B_LOGIN=<第二个账号login>
set ACCOUNT_B_PASSWORD=<第二个账号password>
set ACCOUNT_B_SERVER=XMGlobal-MT5 9
set ACCOUNT_C_LOGIN=336773771
set ACCOUNT_C_PASSWORD=WrongPassword123
set ACCOUNT_C_SERVER=XMGlobal-MT5 9

python spike_switch_account.py
```

> `***` 处填你自己的密码。**不要把带密码的命令贴回对话**——提结果即可。

3. **请肉眼观察 VPS 屏幕**：脚本运行期间 MT5 终端是否有弹窗（账号切换确认框）。这是无人值守 VPS 上的阻断性风险，脚本只能靠"login 挂起 >30s"间接推断，弹窗必须靠人确认。

## 脚本会做什么（不下任何订单）

| 步骤 | 验证点 |
|------|--------|
| 1. initialize + login(A) | 基线账号能登录 |
| 2. 已连接 A 下直接 login(B) | **核心**：能否同终端切换？挂了多久？ |
| 2b. 若直接登录失败 | 备选：shutdown → reinitialize → login(B) |
| 3. 切换后 symbols/positions | 是否立即反映 B 账号 |
| 4. 错密码 login(C) | 失败返回码；能否回滚回 B |
| 5. 耗时检测 | 是否有 login 挂起 >30s（疑似弹窗） |

## 输出

脚本打印 JSON。把**末尾的 `"conclusion"` 和 `"checks"` 里几行关键结果**贴回对话即可：
- `login_B_while_connected` 的 ok/耗时
- `login_B_after_shutdown`（若有）
- `data_reflects_new_account` 的 symbols/positions 数量
- `rollback_to_B_after_failure` 的 ok
- 肉眼是否看到弹窗

## 判定标准

- **通过**：直接 login(B) 成功 + 无弹窗挂起 + 数据反映 B + 失败可回滚 → 进入 Phase 3。
- **不通过**：直接 login(B) 失败且需 shutdown（可接受但实现要调顺序）/出现弹窗 → 与你重新选型。