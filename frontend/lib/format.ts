
/** 展示时区：用户已冻结纪律时区 Asia/Shanghai（2026-10-10），
 *  替代原 Asia/Bangkok（UTC+7，与北京差 1 小时）。 */
const TZ = "Asia/Shanghai";

/** naive 串补 Z 视作 UTC（后端落库口径），带偏移串原样解析 —— 消除
 *  new Date(naive) 按浏览器本地时区解析造成的 5-8 小时偏差（F3）。 */
export function toDate(value: string | number | Date): Date {
  if (value instanceof Date) return value;
  if (typeof value === "number") return new Date(value);
  const normalized = /(Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return new Date(normalized);
}

export function formatDate(value: string | number | Date, locale: string = "zh"): string {
  if (!value) return "-";
  const d = toDate(value);
  if (isNaN(d.getTime())) return "-";
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: TZ,
  }).format(d);
}

export function formatDateOnly(value: string | number | Date, locale: string = "zh"): string {
  if (!value) return "-";
  const d = toDate(value);
  if (isNaN(d.getTime())) return "-";
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-GB", {
    dateStyle: "medium",
    timeZone: TZ,
  }).format(d);
}

export function formatNumber(value: number | null | undefined, decimals = 2, locale: string = "zh"): string {
  if (value === null || value === undefined || isNaN(value)) return "-";
  return new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function formatCurrency(value: number | null | undefined, decimals = 2, locale: string = "zh"): string {
  if (value === null || value === undefined || isNaN(value)) return "-";
  return new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function formatPercent(value: number | null | undefined, decimals = 2, locale: string = "zh"): string {
  if (value === null || value === undefined || isNaN(value)) return "-";
  return new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", {
    style: "percent",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value / 100);
}
