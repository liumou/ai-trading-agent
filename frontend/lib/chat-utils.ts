"use client";

/** 聊天页时区与时间格式化工具。
 *
 * 统一三类 created_at 语义（MAJOR-4）：
 * - 后端消息 naive isoformat（无 Z，视作 UTC）
 * - 事件带 Z（UTC aware）
 * - 前端乐观消息 new Date().toISOString()（带 Z）
 */

export const CHAT_TIME_ZONE = "Asia/Bangkok";

/** 把任意 created_at 字符串解析为 Date。naive 串补 Z 视作 UTC，避免 7 小时偏差。 */
export function parseChatDate(value: string): Date {
  const normalized = /(Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return new Date(normalized);
}

const timeFmt = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit", minute: "2-digit", timeZone: CHAT_TIME_ZONE, hour12: false,
});
const dateDayFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit", month: "2-digit", timeZone: CHAT_TIME_ZONE,
});
const fullFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  timeZone: CHAT_TIME_ZONE, hour12: false,
});

/** 消息时间戳：当天只显 HH:mm，跨天加日期 dd/MM HH:mm（Bangkok）。 */
export function formatChatTime(value: string, now: Date = new Date()): string {
  const date = parseChatDate(value);
  const datePart = dateDayFmt.format(date);
  const todayPart = dateDayFmt.format(now);
  return datePart === todayPart ? timeFmt.format(date) : `${datePart} ${timeFmt.format(date)}`;
}

/** 完整日期时间（跨多天会话）。 */
export function formatChatDateTime(value: string): string {
  return fullFmt.format(parseChatDate(value));
}

/** 「刚刚」的兜底文案。RelativeTimeFormat 无法自然表达 0 单位，
 *  且本工具在非 React 文件里拿不到 next-intl，故保留语言映射常量。 */
const JUST_NOW: Record<string, string> = { zh: "刚刚", en: "Just now" };

/** RelativeTimeFormat 实例缓存：与会话列表的 timeFmt/dateDayFmt 一样模块级单例，
 *  避免每次调用（SessionList 逐条渲染 + 2s 轮询）反复实例化。 */
const rtfCache: Record<string, Intl.RelativeTimeFormat> = {};
function getRtf(locale: string): Intl.RelativeTimeFormat {
  return rtfCache[locale] ??= new Intl.RelativeTimeFormat(locale, { numeric: "always" });
}

/** 会话列表相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 完整日期。
 *
 * locale 由调用方（useLocale）传入，交由 Intl.RelativeTimeFormat 本地化，
 * 避免在此硬编码中文导致英文环境显示中文。 */
export function formatSessionRelative(value: string | null, locale: string = "zh"): string {
  if (!value) return "";
  const diff = Date.now() - parseChatDate(value).getTime();
  const minutes = Math.floor(diff / 60000);
  const rtf = getRtf(locale);
  if (minutes < 1) return JUST_NOW[locale] ?? JUST_NOW.zh;
  if (minutes < 60) return rtf.format(-minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return rtf.format(-hours, "hour");
  const days = Math.floor(hours / 24);
  if (days < 30) return rtf.format(-days, "day");
  return fullFmt.format(parseChatDate(value));
}
