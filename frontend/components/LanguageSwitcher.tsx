"use client";

import { useLocale, useTranslations } from "next-intl";
import { Languages, Check } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { LOCALE_COOKIE, locales, type Locale } from "@/i18n/config";

export function LanguageSwitcher() {
  const locale = useLocale() as Locale;
  const t = useTranslations("app");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const switchTo = (next: Locale) => {
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
    localStorage.setItem(LOCALE_COOKIE, next);
    setOpen(false);
    router.refresh();
    window.location.reload();
  };

  const labels: Record<Locale, string> = { zh: "中文", en: "English" };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="size-8 rounded-full flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-sidebar-accent transition-colors"
        aria-label={t("language")}
        title={t("language")}
      >
        <Languages className="size-4" />
      </button>
      {open && (
        <div className="absolute bottom-full mb-2 left-0 w-32 rounded-xl border border-sidebar-border bg-sidebar shadow-lg p-1 z-50">
          {locales.map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => switchTo(l)}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                l === locale
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-sidebar-accent"
              }`}
            >
              <span>{labels[l]}</span>
              {l === locale && <Check className="size-3.5" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
