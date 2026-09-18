"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, User } from "lucide-react";
import type { AgentChatMessage as ChatMsg } from "@/lib/api";
import { formatChatTime } from "@/lib/chat-utils";

/** 助手消息 markdown 渲染：仅白名单安全标签，映射到 globals.css token。 */
function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h3 className="heading-3 wise-gradient-text mt-2 mb-1">{children}</h3>,
          h2: ({ children }) => <h4 className="heading-3 mt-2 mb-1">{children}</h4>,
          h3: ({ children }) => <h4 className="heading-3 mt-2 mb-1">{children}</h4>,
          ul: ({ children, className }) => (
            <ul className={`${className ? "space-y-0.5" : "list-disc pl-5 space-y-0.5"} text-sm`}>{children}</ul>
          ),
          ol: ({ children }) => <ol className="list-decimal pl-5 space-y-0.5 text-sm">{children}</ol>,
          // remark-gfm 开启表格后必须同时有 th/td 映射，否则单元格无 padding/边框即破图。
          // 外层包裹横向滚动：AI 生成的宽表会撑破气泡 max-w-[85%]。
          table: ({ children }) => (
            <div className="my-2 w-fit max-w-full overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-xs border-collapse">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-border bg-muted/70 px-2 py-1 text-left font-semibold">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border-t border-border px-2 py-1 align-top">{children}</td>
          ),
          // gfm 任务列表生成的 disabled checkbox：去掉默认勾选框外观，保持只读语义
          input: ({ type, checked }) =>
            type === "checkbox" ? (
              <span className={`mr-1 inline-flex size-3.5 items-center justify-center rounded border border-border align-middle ${checked ? "bg-primary text-primary-foreground" : ""}`}>
                {checked ? "✓" : ""}
              </span>
            ) : (
              <input type={type} />
            ),
          del: ({ children }) => <del className="text-muted-foreground line-through">{children}</del>,
          code: ({ children }) => (
            <code className="font-mono text-[12px] bg-muted px-1 py-0.5 rounded">{children}</code>
          ),
          p: ({ children }) => <p className="text-sm leading-relaxed mb-1 last:mb-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function MessageBubbleInner({ msg }: { msg: ChatMsg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex items-end gap-2 animate-fade-in ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="size-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 ring-border">
          <Bot className="size-3.5 text-primary" />
        </div>
      )}
      <div className={`flex flex-col gap-0.5 ${isUser ? "items-end" : "items-start"} max-w-[85%]`}>
        <span className="text-[10px] text-muted-foreground caption">
          {formatChatTime(msg.created_at)}
        </span>
        <div
          className={
            isUser
              ? "rounded-2xl rounded-br-sm px-4 py-2.5 wise-gradient text-primary-foreground text-sm leading-relaxed"
              : "rounded-2xl rounded-bl-sm bg-card border border-border px-4 py-3 text-sm leading-relaxed"
          }
        >
          {isUser ? (
            <span className="whitespace-pre-wrap">{msg.content}</span>
          ) : (
            <MarkdownContent content={msg.content} />
          )}
        </div>
      </div>
      {isUser && (
        <div className="size-7 rounded-full bg-muted flex items-center justify-center shrink-0 ring-border">
          <User className="size-3.5 text-muted-foreground" />
        </div>
      )}
    </div>
  );
}

/** memo：轮询期 run detail 变化会让 page 重渲染，消息对象引用不变时跳过整条消息重渲。 */
export const MessageBubble = memo(MessageBubbleInner);
