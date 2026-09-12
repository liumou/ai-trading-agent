import type { Metadata } from "next";
import { Noto_Sans_Thai, Noto_Sans_SC } from "next/font/google";
import { cookies } from "next/headers";
import { getTranslations } from "next-intl/server";
import { NextIntlClientProvider } from "next-intl";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { AppShell } from "@/components/layout/AppShell";
import { Toaster } from "@/components/ui/toaster";
import { defaultLocale, isLocale, LOCALE_COOKIE } from "@/i18n/config";

const notoSansThai = Noto_Sans_Thai({
  variable: "--font-sans-thai",
  subsets: ["latin", "thai"],
  weight: ["400", "500", "600", "700", "900"],
  display: "swap",
});

const notoSansSC = Noto_Sans_SC({
  variable: "--font-sans-cjk",
  weight: ["400", "500", "700", "900"],
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(cookieLocale) ? cookieLocale : defaultLocale;
  const t = await getTranslations({ locale, namespace: "app" });
  return {
    title: t("title"),
    description: t("description"),
    icons: { icon: "/icon.svg" },
  };
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(cookieLocale) ? cookieLocale : defaultLocale;
  return (
    <html
      lang={locale}
      className={`${notoSansThai.variable} ${notoSansSC.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full bg-background text-foreground">
        <NextIntlClientProvider>
          <ThemeProvider
            attribute="class"
            defaultTheme="light"
            enableSystem
            disableTransitionOnChange
          >
            <AppShell>{children}</AppShell>
            <Toaster />
          </ThemeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
