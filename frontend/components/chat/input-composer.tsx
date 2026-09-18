"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Send } from "lucide-react";

interface InputComposerProps {
  value: string;
  placeholder: string;
  submitting: boolean;
  onChange: (v: string) => void;
  onSend: () => void;
  /** 递增即聚焦输入框；供外部（如空态建议卡）填入文本后定位光标。 */
  focusSignal?: number;
}

/** 输入区：圆角容器 + focus glow + 发送按钮。纯展示，submitting 由 page 持有。 */
export function InputComposer({
  value, placeholder, submitting, onChange, onSend, focusSignal = 0,
}: InputComposerProps) {
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (focusSignal === 0) return;
    wrapRef.current?.querySelector("input")?.focus();
  }, [focusSignal]);

  return (
    <div
      ref={wrapRef}
      className="rounded-2xl border border-border bg-card p-2 ring-border focus-within:glow-green transition-shadow"
    >
      <div className="flex items-center gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) onSend();
          }}
          placeholder={placeholder}
          disabled={submitting}
          className="border-0 focus-visible:ring-0 bg-transparent"
        />
        <Button
          onClick={onSend}
          disabled={submitting || !value.trim()}
          className={value.trim() && !submitting ? "wise-gradient glow-hover shrink-0" : "shrink-0"}
        >
          {submitting ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </div>
    </div>
  );
}
