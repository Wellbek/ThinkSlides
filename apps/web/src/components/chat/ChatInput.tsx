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
  const [showTemplatePrompt, setShowTemplatePrompt] = useState(false);
  const [pendingTemplateFile, setPendingTemplateFile] = useState<File | null>(null);
  const uploadTemplate = useChatStore((s) => s.uploadTemplate);

  const handleSend = useCallback(async () => {
    if (!text.trim() && attachments.length === 0) return;
    if (!activeSessionId) return;

    setIsSending(true);
    try {
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

    const templateFile = newAttachments.find(att => /\.(pptx|potx|pdf)$/i.test(att.file.name));
    if (templateFile && !showTemplatePrompt) {
      setPendingTemplateFile(templateFile.file);
      setShowTemplatePrompt(true);
    }

    setAttachments((prev) => [...prev, ...newAttachments]);
    e.target.value = "";
  };

  const handleSaveAsTemplate = async () => {
    if (pendingTemplateFile) {
      await uploadTemplate(pendingTemplateFile);
      setAttachments(prev => prev.filter(a => a.file !== pendingTemplateFile));
      setPendingTemplateFile(null);
      setShowTemplatePrompt(false);
    }
  };

  const handleSkipTemplate = () => {
    setPendingTemplateFile(null);
    setShowTemplatePrompt(false);
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
    <div className="border-t border-iron px-6 py-4">
      {/* Attachment pills */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {attachments.map((att) => (
            <span
              key={att.id}
              className="pill text-ice"
            >
              <Paperclip size={10} />
              {att.file.name}
              <button
                onClick={() => removeAttachment(att.id)}
                className="ml-1 text-fog hover:text-red-400 transition-colors"
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Template prompt — Ice Signal outlined style */}
      {showTemplatePrompt && pendingTemplateFile && (
        <div className="mb-3 p-2 bg-ice/5 border border-ice/20 rounded-md text-sm flex items-center justify-between">
          <span className="text-paper">Save &quot;{pendingTemplateFile.name}&quot; as a template?</span>
          <div className="flex gap-2">
            <button
              onClick={handleSaveAsTemplate}
              className="btn-outlined text-xs !py-1 !px-2"
            >
              Yes, save as template
            </button>
            <button
              onClick={handleSkipTemplate}
              className="btn-ghost text-xs"
            >
              No, just attach
            </button>
          </div>
        </div>
      )}

      <div className="flex items-end gap-3">
        {/* File attach button */}
        <button
          onClick={() => fileInputRef.current?.click()}
          className="btn-ghost"
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

        {/* Text input — Charcoal fill, Iron border, Fog placeholder */}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe your presentation or upload documents..."
          rows={1}
          className="flex-1 bg-charcoal border border-iron rounded-md px-4 py-2.5 text-body text-paper placeholder-fog resize-none focus:outline-none focus:border-ice/50 max-h-32"
          style={{ minHeight: "44px" }}
        />

        {/* Send button — Outlined action style */}
        <button
          onClick={handleSend}
          disabled={isSending || (!text.trim() && attachments.length === 0)}
          className="btn-outlined !p-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}
