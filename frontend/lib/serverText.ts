// Translates backend-generated plain-text messages (activity feed, bot events,
// notifications) into the current locale using pattern matching. English
// text is returned unchanged when no pattern matches.

type Pattern = { re: RegExp; zh: string };

const PATTERNS: Pattern[] = [
  { re: /^Bot started \((.+?)\)$/, zh: "机器人已启动（{0}）" },
  { re: /^Bot stopped$/, zh: "机器人已停止" },
  { re: /^Emergency stop: (.+?)$/, zh: "紧急停止：{0}" },
  { re: /^Auto-resumed after circuit breaker cooldown$/, zh: "熔断冷却后自动恢复" },
  { re: /^Bot engine error: (.+?)$/, zh: "机器人引擎错误：{0}" },
  { re: /^Circuit breaker triggered$/, zh: "熔断器已触发" },
  { re: /^⚡ Circuit breaker triggered — bot paused$/, zh: "⚡ 熔断器已触发——机器人已暂停" },
  { re: /^⚡ Portfolio circuit breaker — ALL symbols paused$/, zh: "⚡ 组合熔断——所有品种已暂停" },
  { re: /^Absolute drawdown limit reached — trading halted$/, zh: "已达最大回撤限制——交易已停止" },
  { re: /^(.+?) signal on (.+?)$/, zh: "{1} 出现 {0} 信号" },
  { re: /^(.+?) blocked: (.+?)$/, zh: "{0} 已拦截：{1}" },
  { re: /^Regime: (.+?) → (.+?)$/, zh: "市场状态：{0} → {1}" },
  { re: /^Peak reset to \$(.+?)$/, zh: "峰值已重置为 ${0}" },
  { re: /^Login successful$/, zh: "登录成功" },
  { re: /^Login failed$/, zh: "登录失败" },
  { re: /^take_profit$/, zh: "止盈" },
  { re: /^stop_loss$/, zh: "止损" },
  { re: /^manual_close$/, zh: "手动平仓" },
  { re: /^unknown$/, zh: "未知" },
];

export function translateServerText(text: string | null | undefined, locale: string): string {
  if (!text || locale === "en") return text ?? "";
  for (const p of PATTERNS) {
    const m = text.match(p.re);
    if (m) {
      return p.zh.replace(/\{(\d)\}/g, (_, i) => m[Number(i)] ?? "");
    }
  }
  return text;
}
