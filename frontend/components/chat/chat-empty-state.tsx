"use client";

import { useTranslations } from "next-intl";
import { Lightbulb, MessageSquare } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";

interface ChatEmptyStateProps {
  /** 点击建议卡：把问题文本填入输入框（不直接发送，留用户确认）。 */
  onSuggest: (text: string) => void;
}

/** 首次进入的空态：引导语 + 可点击的建议提问卡。
 *
 * preset 按钮（交易计划/报告）由 ChatToolbar 常驻提供，此处不重复，
 * 避免同一页面上出现两组语义相同的入口。
 */
export function ChatEmptyState({ onSuggest }: ChatEmptyStateProps) {
  const t = useTranslations("agentChat");
  // 缺失 key 时 t.raw 返回 fallback 字符串而非 undefined，故用 Array.isArray 而非 ?? 守卫。
  const rawSuggestions = t.raw("suggestItems");
  const suggestions: string[] = Array.isArray(rawSuggestions) ? rawSuggestions : [];

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-8 animate-fade-in">
      <EmptyState
        icon={MessageSquare}
        heading={t("emptyHeading")}
        description={t("emptyDesc")}
        className="py-0 mb-0"
      />
      {suggestions.length > 0 && (
        <div className="w-full">
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <Lightbulb className="size-3.5" />
            {t("suggestTitle")}
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {suggestions.map((text) => (
              <button
                key={text}
                type="button"
                onClick={() => onSuggest(text)}
                className="card-hover rounded-xl border border-border bg-card px-3 py-2.5 text-left text-xs text-foreground hover:text-primary"
              >
                {text}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
