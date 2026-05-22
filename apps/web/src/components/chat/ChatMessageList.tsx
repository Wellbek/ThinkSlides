"use client";

import { useEffect, useRef } from "react";
import { useChatStore } from "@/stores/chat-store";
import { User, Bot, Paperclip } from "lucide-react";
import { formatTime } from "@/lib/date";

export function ChatMessageList() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const activeMessages = useChatStore((s) => s.activeMessages);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = activeMessages ?? [];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          {msg.role === "assistant" && (
            <div className="w-8 h-8 rounded-full bg-brand-600/20 flex items-center justify-center shrink-0 mt-0.5">
              <Bot size={16} className="text-brand-400" />
            </div>
          )}

          <div
            className={`max-w-[70%] rounded-xl px-4 py-3 ${
              msg.role === "user"
                ? "bg-brand-600 text-white"
                : "bg-surface-50 text-gray-200"
            }`}
          >
            <p className="text-sm whitespace-pre-wrap">{msg.content}</p>

            {/* Attachments */}
            {msg.attachments && msg.attachments.length > 0 && (
              <div className="mt-2 space-y-1">
                {msg.attachments.map((att, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 text-xs opacity-80"
                  >
                    <Paperclip size={10} />
                    <span>{typeof att === "string" ? att : (att as { fileName?: string }).fileName ?? "attachment"}</span>
                  </div>
                ))}
              </div>
            )}

            <p
              className={`text-xs mt-1 ${
                msg.role === "user" ? "text-brand-200" : "text-gray-500"
              }`}
            >
              {formatTime(msg.created_at)}
            </p>
          </div>

          {msg.role === "user" && (
            <div className="w-8 h-8 rounded-full bg-surface-100 flex items-center justify-center shrink-0 mt-0.5">
              <User size={16} className="text-gray-400" />
            </div>
          )}
        </div>
      ))}

      <div ref={bottomRef} />
    </div>
  );
}