"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useTranslations } from "next-intl";
import {
  LayoutDashboard,
  Settings,
  Settings2,
  BarChart3,
  History,
  Brain,
  Cpu,
  Globe,
  Menu,
  X,
  Sun,
  Moon,
  TrendingUp,
  Bell,
  Plug,
  LogOut,
  Activity,
  Shield,
  Zap,
  Database,
  CandlestickChart,
  MessagesSquare,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { ConnectionStatus } from "@/components/ui/connection-status";
import { stopWebSocket } from "@/lib/websocket";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";


interface NavItem { href: string; labelKey: string; icon: typeof LayoutDashboard; }
interface NavGroup { labelKey: string; items: NavItem[]; }

const navGroups: NavGroup[] = [
  {
    labelKey: "overview",
    items: [
      { href: "/dashboard", labelKey: "dashboard", icon: LayoutDashboard },
    ],
  },
  {
    labelKey: "trading",
    items: [
      { href: "/symbols", labelKey: "symbols", icon: CandlestickChart },
      { href: "/chat", labelKey: "agentChat", icon: MessagesSquare },
      { href: "/backtest", labelKey: "backtest", icon: BarChart3 },
      { href: "/history", labelKey: "history", icon: History },
    ],
  },
  {
    labelKey: "analytics",
    items: [
      { href: "/insights", labelKey: "insights", icon: Brain },
      { href: "/activity", labelKey: "activity", icon: Activity },
      { href: "/ai-usage", labelKey: "aiUsage", icon: Zap },
      { href: "/ml", labelKey: "ml", icon: Cpu },
      { href: "/macro", labelKey: "macro", icon: Globe },
      { href: "/quant", labelKey: "quant", icon: Shield },
    ],
  },
  {
    labelKey: "system",
    items: [
      { href: "/agent-prompts", labelKey: "agentPrompts", icon: Settings2 },
      { href: "/integration", labelKey: "integration", icon: Plug },
      { href: "/db-health", labelKey: "dbHealth", icon: Database },
      { href: "/notifications", labelKey: "notifications", icon: Bell },
      { href: "/settings", labelKey: "settings", icon: Settings },
    ],
  },
];

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const t = useTranslations("nav");
  return (
    <button
      type="button"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      className="relative size-8 rounded-full flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-sidebar-accent transition-colors"
      aria-label={t("toggleTheme")}
      title={t("toggleTheme")}
    >
      <Sun className="size-4 rotate-0 scale-100 transition-transform dark:-rotate-90 dark:scale-0" />
      <Moon className="absolute size-4 rotate-90 scale-0 transition-transform dark:rotate-0 dark:scale-100" />
    </button>
  );
}

function LogoutButton() {
  const router = useRouter();
  const t = useTranslations("nav");
  const handleLogout = () => {
    stopWebSocket();
    localStorage.removeItem("token");
    router.push("/login");
  };
  return (
    <button
      type="button"
      onClick={handleLogout}
      className="size-8 rounded-full flex items-center justify-center text-muted-foreground hover:text-red-500 hover:bg-sidebar-accent transition-colors"
      aria-label={t("logout")}
      title={t("logout")}
    >
      <LogOut className="size-4" />
    </button>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const t = useTranslations("nav");

  return (
    <>
      {/* Header */}
      <div className="p-5">
        <div className="flex items-center gap-3">
          <div className="size-9 rounded-full bg-primary flex items-center justify-center">
            <TrendingUp className="size-4 text-primary-foreground" />
          </div>
          <div>
            <h1 className="text-base font-black tracking-tight text-foreground">
              {t("brand")}
            </h1>
            <p className="text-xs text-muted-foreground font-medium">
              {t("brandSub")}
            </p>
          </div>
        </div>
      </div>

      <div className="mx-4 h-px bg-sidebar-border" />

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-4 overflow-y-auto">
        {navGroups.map((group) => (
          <div key={group.labelKey} className="space-y-0.5">
            <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
              {t(group.labelKey)}
            </p>
            {group.items.map((item) => {
              const isActive = pathname === item.href || pathname?.startsWith(item.href + "/");
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-2xl text-sm font-semibold transition-all duration-150",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-sidebar-accent"
                  )}
                >
                  <Icon className="size-4" />
                  <span>{t(item.labelKey)}</span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="mx-4 h-px bg-sidebar-border" />

      {/* Footer */}
      <div className="p-3 flex items-center justify-between gap-2">
        <ConnectionStatus />
        <div className="flex items-center gap-1">
          <LanguageSwitcher />
          <ThemeToggle />
          <LogoutButton />
          <span className="text-[10px] text-muted-foreground/50 font-medium ml-1">v2.0.0</span>
        </div>
      </div>
    </>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden lg:flex w-60 flex-col bg-sidebar border-r border-sidebar-border h-screen sticky top-0">
      <SidebarContent />
    </aside>
  );
}

export function MobileHeader() {
  const [open, setOpen] = useState(false);
  const t = useTranslations("nav");

  return (
    <>
      <header className="lg:hidden flex items-center justify-between p-3 border-b border-sidebar-border bg-sidebar">
        <div className="flex items-center gap-2">
          <div className="size-7 rounded-full bg-primary flex items-center justify-center">
            <TrendingUp className="size-3.5 text-primary-foreground" />
          </div>
          <span className="text-sm font-black text-foreground">{t("brand")}</span>
        </div>
        <button type="button" aria-label={t("openMenu")} onClick={() => setOpen(true)} className="p-1.5 text-muted-foreground hover:text-foreground">
          <Menu className="size-5" />
        </button>
      </header>

      {open && (
        <div className="lg:hidden fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute left-0 top-0 bottom-0 w-60 bg-sidebar border-r border-sidebar-border flex flex-col animate-in slide-in-from-left duration-200">
            <div className="flex justify-end p-2">
              <button type="button" aria-label={t("closeMenu")} onClick={() => setOpen(false)} className="p-1.5 text-muted-foreground hover:text-foreground">
                <X className="size-5" />
              </button>
            </div>
            <SidebarContent onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
    </>
  );
}
