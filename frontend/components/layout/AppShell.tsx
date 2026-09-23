"use client";

import { useEffect, useState, Suspense } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Sidebar, MobileHeader } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CommandPalette } from "@/components/ui/command-palette";
import { RouteProgress } from "@/components/ui/route-progress";
import api, { getSymbols } from "@/lib/api";
import { useBotStore } from "@/store/botStore";
import { startWebSocket } from "@/lib/websocket";

const AUTH_BYPASS_PAGES = ["/login"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isAuthPage = AUTH_BYPASS_PAGES.includes(pathname);
  const [authChecked, setAuthChecked] = useState(false);
  const symbolsLoaded = useBotStore((s) => s.symbols.length > 0);
  const setSymbols = useBotStore((s) => s.setSymbols);

  useEffect(() => {
    if (isAuthPage) {
      setAuthChecked(true);
      return;
    }

    // Dev-only auth bypass: must opt in via NEXT_PUBLIC_DEV_NOAUTH=1 on a local host
    // (same gate as app/login/page.tsx). Ungated it wrote the "__noauth__" sentinel into
    // localStorage even when the backend had auth enabled — every request then 401s
    // ("Invalid or expired token"), the api interceptor clears the token and bounces to
    // /login, and this effect re-injects the sentinel: an endless 400/401 loop, and the
    // dashboard never loads any data (MT5 shows as disconnected while it is actually up).
    const isLocal =
      typeof window !== "undefined" &&
      (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1");
    const bypassEnabled = process.env.NEXT_PUBLIC_DEV_NOAUTH === "1";
    if (isLocal && bypassEnabled) {
      if (!localStorage.getItem("token")) localStorage.setItem("token", "__noauth__");
      setAuthChecked(true);
      return;
    }

    const storedToken = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    // A leftover "__noauth__" sentinel is not a session — it only ever worked while the
    // backend ran with auth disabled. Drop it so the user lands on the login form instead
    // of being stuck in the 401 redirect loop.
    if (storedToken === "__noauth__") {
      localStorage.removeItem("token");
    }
    const token = storedToken === "__noauth__" ? null : storedToken;
    if (!token) {
      router.replace("/login");
      return;
    }
    api.get("/api/auth/me")
      .then(() => setAuthChecked(true))
      .catch(() => setAuthChecked(true));
  }, [isAuthPage, router, pathname]);

  // Prefetch symbols into global store once authed, so pages that read
  // symbols from Zustand (insights, quant, etc.) work on direct load
  // without requiring a dashboard visit first.
  useEffect(() => {
    if (!authChecked || isAuthPage || symbolsLoaded) return;
    getSymbols()
      .then((res) => {
        if (res.data?.symbols) {
          setSymbols(res.data.symbols);
        }
      })
      .catch(() => {});
  }, [authChecked, isAuthPage, symbolsLoaded, setSymbols]);

  // Start singleton WebSocket once after auth. Lives for entire session —
  // survives route changes so every page sees live updates.
  useEffect(() => {
    if (!authChecked || isAuthPage) return;
    startWebSocket();
  }, [authChecked, isAuthPage]);

  if (isAuthPage) {
    return <>{children}</>;
  }

  if (!authChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-muted-foreground text-sm">Loading...</p>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <Suspense fallback={null}>
        <RouteProgress />
      </Suspense>
      <CommandPalette />
      <div className="flex flex-col lg:flex-row min-h-full">
        <MobileHeader />
        <Sidebar />
        <main className="flex-1 overflow-auto">
          <div className="max-w-400 mx-auto">{children}</div>
        </main>
      </div>
    </TooltipProvider>
  );
}
