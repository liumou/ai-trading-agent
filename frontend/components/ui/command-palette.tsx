"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import {
  LayoutDashboard,
  BarChart3,
  History,
  Brain,
  Activity,
  Cpu,
  Globe,
  Shield,
  Settings2,
  Plug,
  Bell,
  Settings,
  Search,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslations } from "next-intl";

interface CommandItem {
  labelKey: string;
  href: string;
  icon: typeof LayoutDashboard;
  group: "navigation" | "system";
}

const navItems: CommandItem[] = [
  { labelKey: "dashboard", href: "/dashboard", icon: LayoutDashboard, group: "navigation" },
  { labelKey: "backtest", href: "/backtest", icon: BarChart3, group: "navigation" },
  { labelKey: "history", href: "/history", icon: History, group: "navigation" },
  { labelKey: "insights", href: "/insights", icon: Brain, group: "navigation" },
  { labelKey: "activity", href: "/activity", icon: Activity, group: "navigation" },
  { labelKey: "ml", href: "/ml", icon: Cpu, group: "navigation" },
  { labelKey: "macro", href: "/macro", icon: Globe, group: "navigation" },
  { labelKey: "quant", href: "/quant", icon: Shield, group: "navigation" },
  { labelKey: "agentPrompts", href: "/agent-prompts", icon: Settings2, group: "system" },
  { labelKey: "integration", href: "/integration", icon: Plug, group: "system" },
  { labelKey: "notifications", href: "/notifications", icon: Bell, group: "system" },
  { labelKey: "settings", href: "/settings", icon: Settings, group: "system" },
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const tNav = useTranslations("nav");
  const t = useTranslations("ui");

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      setOpen((prev) => !prev);
    }
  }, []);

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  const handleSelect = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/20 backdrop-blur-xs animate-in fade-in-0 duration-100"
        onClick={() => setOpen(false)}
      />

      {/* Command dialog */}
      <div className="absolute left-1/2 top-[20%] -translate-x-1/2 w-full max-w-lg animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-2 duration-150">
        <Command
          className={cn(
            "rounded-xl border border-border bg-card shadow-2xl overflow-hidden",
            "dark:shadow-[0_25px_50px_-12px_rgba(0,0,0,0.5)]"
          )}
          loop
        >
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search className="size-4 text-muted-foreground shrink-0" />
            <Command.Input
              placeholder={t("searchPages")}
              className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              autoFocus
            />
            <kbd className="hidden sm:inline-flex h-5 items-center gap-0.5 rounded border border-border bg-muted px-1.5 text-[10px] font-medium text-muted-foreground">
              ESC
            </kbd>
          </div>

          <Command.List className="max-h-72 overflow-y-auto p-2">
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              {t("noResultsFound")}
            </Command.Empty>

            {(["navigation", "system"] as const).map((group) => (
              <Command.Group
                key={group}
                heading={t(group)}
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:text-muted-foreground/60"
              >
                {navItems
                  .filter((item) => item.group === group)
                  .map((item) => {
                    const Icon = item.icon;
                    return (
                      <Command.Item
                        key={item.href}
                        value={item.labelKey}
                        onSelect={() => handleSelect(item.href)}
                        className="flex items-center gap-3 rounded-lg px-2 py-2 text-sm cursor-pointer text-muted-foreground data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground transition-colors"
                      >
                        <Icon className="size-4" />
                        <span>{tNav(item.labelKey)}</span>
                      </Command.Item>
                    );
                  })}
              </Command.Group>
            ))}
          </Command.List>

          <div className="border-t border-border px-3 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <span>{t("navigate")}</span>
              <kbd className="inline-flex h-4 items-center rounded border border-border bg-muted px-1 text-[10px]">
                ↑↓
              </kbd>
              <span>{t("select")}</span>
              <kbd className="inline-flex h-4 items-center rounded border border-border bg-muted px-1 text-[10px]">
                ↵
              </kbd>
            </div>
          </div>
        </Command>
      </div>
    </div>
  );
}
