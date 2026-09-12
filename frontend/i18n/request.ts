import { getRequestConfig } from "next-intl/server";
import { cookies } from "next/headers";
import fs from "fs";
import path from "path";
import { defaultLocale, isLocale, LOCALE_COOKIE, type Locale } from "./config";

function loadMessages(locale: Locale): Record<string, unknown> {
  const dir = path.join(process.cwd(), "messages", locale);
  const messages: Record<string, unknown> = {};
  for (const file of fs.readdirSync(dir).filter((f) => f.endsWith(".json"))) {
    const ns = path.basename(file, ".json");
    messages[ns] = JSON.parse(fs.readFileSync(path.join(dir, file), "utf-8"));
  }
  return messages;
}

export default getRequestConfig(async () => {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale: Locale = isLocale(cookieLocale) ? cookieLocale : defaultLocale;
  return {
    locale,
    messages: loadMessages(locale),
  };
});
