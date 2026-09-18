"use client";

import { useLocale, useTranslations } from "next-intl";
import { Skeleton } from "@/components/ui/skeleton";
import { Trash2 } from "lucide-react";
import { formatSessionRelative } from "@/lib/chat-utils";

export interface SessionListItem {
  id: number;
  title: string;
  symbol: string;
  updated_at: string | null;
}

interface SessionListProps {
  sessions: SessionListItem[];
  activeId: number | null;
  loading: boolean;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
}

/** 会话列表（桌面侧栏 / 移动抽屉复用）。纯展示，状态由 page 持有。 */
export function SessionList({ sessions, activeId, loading, onSelect, onDelete }: SessionListProps) {
  const t = useTranslations("agentChat");
  const locale = useLocale();
  if (loading) return <Skeleton className="h-16" />;

  return (
    <>
      {sessions.map((s) => (
        <div
          key={s.id}
          className={`group flex items-center justify-between rounded-lg px-3 py-2 cursor-pointer text-sm ${
            activeId === s.id
              ? "bg-primary/10 font-medium border-l-2 border-primary"
              : "hover:bg-muted card-hover"
          }`}
          onClick={() => onSelect(s.id)}
        >
          <div className="min-w-0">
            <div className="truncate">{s.title}</div>
            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span>{s.symbol}</span>
              {s.updated_at && <span>· {formatSessionRelative(s.updated_at, locale)}</span>}
            </div>
          </div>
          <button
            type="button"
            aria-label={t("delete")}
            className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-red-500 shrink-0"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(s.id);
            }}
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      ))}
    </>
  );
}
