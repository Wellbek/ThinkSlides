"use client";

import { useRef, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Paperclip, ArrowUp, Sparkles } from "lucide-react";

const SUGGESTIONS = [
  "Investor pitch from my Q3 financials",
  "10-slide sales enablement deck",
  "Quarterly business review",
  "Product launch announcement",
];

export function HomeView({ onStarted }: { onStarted: () => void }) {
  const createSession = useChatStore((s) => s.createSession);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const [content, setContent] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const start = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    try {
      const id = await createSession();
      await sendMessage(trimmed, files.length ? files : undefined);
      onStarted();
    } catch (err) {
      console.error("Failed to start chat:", err);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="ws-home">
      <div className="ws-home-greet">What deck shall we build today?</div>
      <div className="ws-home-sub">Describe your presentation, attach docs or data, and the agent will draft a branded, template-faithful deck.</div>

      <div className="ws-home-card">
        <textarea
          placeholder="e.g. Generate a 10-slide investor pitch using the Series A template. I have attached Q3 financials - pull real numbers."
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              start(content);
            }
          }}
        />
        <div className="ws-home-bar">
          <button className="ws-cm" onClick={() => fileRef.current?.click()} title="Attach">
            <Paperclip size={15} />
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          {files.length > 0 && (
            <span className="ws-hint">{files.length} file(s) attached</span>
          )}
          <div className="ws-spacer" />
          <button className="ws-send" disabled={!content.trim() || sending} onClick={() => start(content)}>
            <ArrowUp size={14} /> {sending ? "Starting..." : "Start"}
          </button>
        </div>
      </div>

      <div className="ws-home-sugg">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="ws-sugg" onClick={() => start(s)}>
            <Sparkles size={12} style={{ display: "inline", marginRight: 5, verticalAlign: "-2px" }} />
            {s}
          </button>
        ))}
      </div>

      <div className="ws-home-foot">Enter to start - Shift+Enter for a newline</div>
    </div>
  );
}
