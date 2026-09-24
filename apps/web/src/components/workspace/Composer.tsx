"use client";

import { useEffect, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { useAnnotationStore } from "@/stores/annotation-store";
import { useAiReadiness } from "@/hooks/useAiReadiness";
import { EffortSelector } from "./EffortSelector";
import { DetailLevelSelector } from "./DetailLevelSelector";
import { SourcesToggle } from "./SourcesToggle";
import { Paperclip, AtSign, Code2, ArrowUp, X } from "lucide-react";

export function Composer() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isSending = useChatStore((s) => s.isSending);
  const activeMessages = useChatStore((s) => s.activeMessages);
  // Settings can be cleared mid-session, so the composer checks rather than
  // assuming the session it belongs to is still runnable.
  const { ready, reason } = useAiReadiness();

  const [content, setContent] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const composerInsert = useAnnotationStore((s) => s.composerInsert);

  useEffect(() => {
    if (!composerInsert) return;
    setContent((c) => (c && !c.endsWith(" ") ? `${c} ` : c) + composerInsert.text);
  }, [composerInsert]);

  // Show the most recent user attachments as context chips.
  const lastAttachments = (() => {
    for (let i = activeMessages.length - 1; i >= 0; i--) {
      const m = activeMessages[i];
      if (m.role === "user" && m.attachments?.length) return m.attachments;
    }
    return [];
  })();

  const send = async () => {
    const trimmed = content.trim();
    if (!trimmed || isSending || !activeSessionId || !ready) return;
    const text = trimmed;
    setContent("");
    const toUpload = files;
    setFiles([]);
    await sendMessage(text, toUpload.length ? toUpload : undefined);
  };

  return (
    <div className="ws-composer">
      {/* The setup alert lives in the top bar and nowhere else - see
          WorkspaceShell. Repeating it here said the same sentence twice on
          one screen. */}
      <div className="ws-composer-ctx">
        {files.map((f, i) => (
          <span className="ws-ctx-chip" key={i}>
            <Paperclip size={12} /> {f.name} <span className="x" onClick={() => setFiles((arr) => arr.filter((_, j) => j !== i))}><X size={11} /></span>
          </span>
        ))}
        {files.length === 0 && lastAttachments.map((name, i) => (
          <span className="ws-ctx-chip" key={i}><Paperclip size={12} /> {name}</span>
        ))}
        <span className="ws-ctx-attach" onClick={() => fileRef.current?.click()}>
          <Paperclip size={12} /> + Attach doc or data
        </span>
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
      </div>
      <div className="ws-composer-box">
        <textarea
          placeholder={
            ready
              ? "Ask the agent to edit, regenerate, or explain a slide... (mark a spot in the preview to target an edit)"
              : "Set up an AI model to continue this session."
          }
          value={content}
          disabled={!ready}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <div className="ws-composer-bar">
          <button className="ws-cm" onClick={() => fileRef.current?.click()} title="Attach"><Paperclip size={14} /></button>
          <button className="ws-cm" title="Mention slide"><AtSign size={14} /></button>
          <button className="ws-cm" title="Code mode"><Code2 size={14} /></button>
          <EffortSelector />
          <DetailLevelSelector />
          <SourcesToggle />
          <span className="ws-hint ws-hint-collapsible">Enter to send</span>
          <div className="ws-spacer" />
          <button
            className="ws-send"
            disabled={!content.trim() || isSending || !ready}
            title={ready ? undefined : reason}
            onClick={send}
          >
            <ArrowUp size={13} /> Send
          </button>
        </div>
      </div>
    </div>
  );
}
