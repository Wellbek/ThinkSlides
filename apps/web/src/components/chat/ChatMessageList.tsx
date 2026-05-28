"use client";

import { useChatStore } from "@/stores/chat-store";
import { Loader2 } from "lucide-react";

export function ChatMessageList() {
  const messages = useChatStore((s) => s.activeMessages);
  const isSending = useChatStore((s) => s.isSending);

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          <div
            className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${
              msg.role === 'user'
                ? 'bg-brand-600 text-white'
                : 'bg-surface-100 text-gray-200'
            }`}
          >
            {msg.content ? (
              <p className="whitespace-pre-wrap">{msg.content}</p>
            ) : (
              msg.role === 'assistant' && (
                <div className="flex items-center gap-2 text-gray-400">
                  <Loader2 size={14} className="animate-spin" />
                  <span>Thinking...</span>
                </div>
              )
            )}
          </div>
        </div>
      ))}

      {/* Extra typing indicator if the last assistant message is empty and isSending is true */}
      {isSending &&
        messages.length > 0 &&
        messages[messages.length - 1]?.role === 'assistant' &&
        messages[messages.length - 1]?.content === '' && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-lg px-4 py-2 text-sm bg-surface-100 text-gray-400 flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" />
              <span>Thinking...</span>
            </div>
          </div>
        )}
    </div>
  );
}