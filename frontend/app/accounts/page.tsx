"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import api from "@/lib/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";

// ─── Types ──────────────────────────────────────────────────────────────────

interface Account {
  id: number;
  login: number;
  server: string;
  broker_name: string | null;
  is_active: boolean;
  is_enabled: boolean;
  last_switched_at: string | null;
  created_at: string;
}

function showError(msg: string) {
  alert(msg);
}
function showSuccess(msg: string) {
  alert(msg);
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function AccountsPage() {
  const t = useTranslations("accounts");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [switching, setSwitching] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);

  const [newLogin, setNewLogin] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newServer, setNewServer] = useState("");
  const [newBroker, setNewBroker] = useState("");

  const fetchAccounts = useCallback(async () => {
    try {
      const res = await api.get("/api/accounts");
      setAccounts(res.data);
    } catch { /* handled */ } finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchAccounts(); }, [fetchAccounts]);

  const handleSwitch = async (account: Account) => {
    if (!window.confirm(t("switchConfirm", { login: String(account.login) }))) return;
    setSwitching(account.id);
    try {
      await api.post(`/api/accounts/${account.id}/switch`, { actor: "owner" });
      showSuccess(t("switched", { login: String(account.login) }));
      await fetchAccounts();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || "error";
      showError(t("switchFailed", { detail }));
    } finally { setSwitching(null); }
  };

  const handleAdd = async () => {
    if (!newLogin.trim() || !newPassword.trim()) return;
    setAdding(true);
    try {
      await api.post("/api/accounts", {
        login: Number(newLogin),
        password: newPassword,
        server: newServer,
        broker_name: newBroker || null,
      });
      showSuccess(t("added", { login: newLogin }));
      setNewLogin(""); setNewPassword(""); setNewServer(""); setNewBroker("");
      setShowAdd(false);
      await fetchAccounts();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || "error";
      showError(t("addFailed") + (detail !== "error" ? `: ${detail}` : ""));
    } finally { setAdding(false); }
  };

  const handleDelete = async (account: Account) => {
    if (!window.confirm(t("delete"))) return;
    try {
      await api.delete(`/api/accounts/${account.id}`);
      showSuccess(t("deleted", { login: String(account.login) }));
      await fetchAccounts();
    } catch {
      showError(t("deleteFailed"));
    }
  };

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <button
          type="button"
          onClick={() => setShowAdd((v) => !v)}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          {t("addAccount")}
        </button>
      </PageHeader>

      <PageInstructions items={[t("switchNote")]} />

      {showAdd && (
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("login")}</label>
              <input
                type="number"
                value={newLogin}
                onChange={(e) => setNewLogin(e.target.value)}
                placeholder="1234567"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("password")}</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder={t("passwordPlaceholder")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("server")}</label>
              <input
                type="text"
                value={newServer}
                onChange={(e) => setNewServer(e.target.value)}
                placeholder={t("serverPlaceholder")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("broker")}</label>
              <input
                type="text"
                value={newBroker}
                onChange={(e) => setNewBroker(e.target.value)}
                placeholder="XM"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          </div>
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowAdd(false)}
              className="rounded-md px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
            >
              {t("cancel")}
            </button>
            <button
              type="button"
              onClick={handleAdd}
              disabled={adding || !newLogin.trim() || !newPassword.trim()}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {adding ? t("adding") : t("add")}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-center text-muted-foreground py-8">{t("loading")}</div>
      ) : accounts.length === 0 ? (
        <div className="text-center text-muted-foreground py-12 border border-dashed border-border rounded-xl">
          {t("empty")}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                <th className="px-4 py-3 font-medium">{t("login")}</th>
                <th className="px-4 py-3 font-medium">{t("server")}</th>
                <th className="px-4 py-3 font-medium">{t("broker")}</th>
                <th className="px-4 py-3 font-medium">{t("actions")}</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id} className="border-b border-border/40 last:border-b-0">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-medium">{account.login}</span>
                      {account.is_active && (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-green-500 bg-green-500/10 border border-green-500/20 rounded-full px-2 py-0.5">
                          <span className="size-1.5 rounded-full bg-green-500" />
                          {t("current")}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground font-mono text-xs">{account.server || "—"}</td>
                  <td className="px-4 py-3 text-muted-foreground">{account.broker_name || "—"}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {!account.is_active && (
                        <button
                          type="button"
                          onClick={() => handleSwitch(account)}
                          disabled={switching === account.id}
                          className="rounded-md bg-primary/90 px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary disabled:opacity-50"
                        >
                          {switching === account.id ? t("switching") : t("switch")}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDelete(account)}
                        className="rounded-md px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10"
                      >
                        {t("delete")}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
