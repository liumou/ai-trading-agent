"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown } from "lucide-react";

interface ConversationThreadProps {
  showEmpty: boolean;
  /** 空态内容（ChatEmptyState）。 */
  empty: ReactNode;
  thinking: boolean;
  thinkingText: string;
  messages: ReactNode[];
  /** 轮询驱动的滚动信号：每次 run 状态更新递增，触发自动滚（不引起其他副作用）。 */
  scrollSignal: number;
  children?: ReactNode;
}

/** 消息流容器：滚动区 + 消息列表 + 空态 + 等待指示。纯展示，状态由 page 持有。 */
export function ConversationThread({
  showEmpty, empty, thinking, thinkingText, messages, scrollSignal, children,
}: ConversationThreadProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 用户上滚阅读时暂停自动滚，出现新消息改为显示「回到底部」按钮。
  const [atBottom, setAtBottom] = useState(true);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    // 30px 容差：避免 1px 偏差导致按钮反复出现
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 30);
  };

  // 新消息或轮询信号变化时，仅在用户本就在底部时才自动滚。
  useEffect(() => {
    if (!atBottom) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [scrollSignal, messages.length, thinking, atBottom]);

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    setAtBottom(true);
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-y-auto space-y-3 rounded-2xl border border-border bg-card/50 p-4"
      >
        {showEmpty && empty}
        {messages}
        {children}
        {thinking && (
          <div className="flex items-end gap-2 justify-start animate-fade-in">
            <div className="size-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 ring-border">
              <span className="size-1.5 rounded-full bg-primary animate-pulse" />
            </div>
            <div className="rounded-2xl rounded-bl-sm bg-card border border-border px-4 py-3 flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-primary animate-pulse" />
              <span className="size-1.5 rounded-full bg-primary animate-pulse" style={{ animationDelay: "150ms" }} />
              <span className="size-1.5 rounded-full bg-primary animate-pulse" style={{ animationDelay: "300ms" }} />
              <span className="ml-1.5 text-[10px] text-muted-foreground caption">{thinkingText}</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      {!atBottom && (
        <button
          type="button"
          aria-label="scroll to bottom"
          onClick={scrollToBottom}
          className="absolute bottom-2 right-4 flex size-8 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-md transition-colors hover:text-primary animate-fade-in"
        >
          <ArrowDown className="size-4" />
        </button>
      )}
    </div>
  );
}
