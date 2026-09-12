
const TZ = "Asia/Bangkok";

export function formatDate(value: string | number | Date, locale: string = "zh"): string {
  if (!value) return "-";
  const d = value instanceof Date ? value : new Date(value);
  if (isNaN(d.getTime())) return "-";
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: TZ,
  }).format(d);
}

export function formatDateOnly(value: string | number | Date, locale: string = "zh"): string {
  if (!value) return "-";
  const d = value instanceof Date ? value : new Date(value);
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
