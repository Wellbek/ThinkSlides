"use client";

import { useState, useRef, useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Send, Paperclip, X } from "lucide-react";

interface AttachedFile {
  file: File;
  id: string;
}

export function ChatInput() {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const [isSending, setIsSending] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSend = useCallback(async () => {
    if (!text.trim() && attachments.length === 0) return;
    if (!activeSessionId) return;

    setIsSending(true);
    try {
      // Store sendMessage signature is (content: string, files?: File[])
      await sendMessage(
        text.trim(),
        attachments.length > 0 ? attachments.map((a) => a.file) : undefined
      );
      setText("");
      setAttachments([]);
    } catch (err) {
      console.error("Failed to send message:", err);
    } finally {
      setIsSending(false);
    }
  }, [text, attachments, activeSessionId, sendMessage]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const newAttachments = Array.from(files).map((file) => ({
      file,
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    }));
    setAttachments((prev) => [...prev, ...newAttachments]);
    e.target.value = "";
  };

  const removeAttachment = (id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-surface-100 px-6 py-4">
      {/* Attachment pills */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {attachments.map((att) => (
            <span
              key={att.id}
              className="inline-flex items-center gap-1.5 px-3 py-1 bg-surface-50 rounded-full text-xs text-gray-300"
            >
              <Paperclip size={10} />
              {att.file.name}
              <button
                onClick={() => removeAttachment(att.id)}
                className="ml-1 hover:text-red-400"
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-end gap-3">
        {/* File attach button */}
        <button
          onClick={() => fileInputRef.current?.click()}
          className="p-2 rounded-lg hover:bg-surface-50 text-gray-400 hover:text-gray-200 transition-colors"
          title="Attach files"
        >
          <Paperclip size={20} />
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.txt,.json,.pptx,.potx,.odp,.png,.jpg,.jpeg,.gif"
          onChange={handleFileSelect}
          className="hidden"
        />

        {/* Text input */}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe your presentation or upload documents..."
          rows={1}
          className="flex-1 bg-surface-50 border border-surface-100 rounded-xl px-4 py-2.5 text-sm text-gray-200 placeholder-gray-500 resize-none focus:outline-none focus:border-brand-500/50 max-h-32"
          style={{ minHeight: "44px" }}
        />

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={isSending || (!text.trim() && attachments.length === 0)}
          className="p-2.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}