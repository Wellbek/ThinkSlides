"use client";

import { useState } from "react";
import { ActivityFeed } from "./ActivityFeed";
import { Composer } from "./Composer";
import { TimerHeader } from "@/components/chat/TimerHeader";
import { useChatStore, type GeneratedFile } from "@/stores/chat-store";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { PanelRightClose, Download, ExternalLink, Presentation, FileDown } from "lucide-react";

export function ChatFlow({ onCollapse }: { collapsed: boolean; onCollapse: () => void }) {
  const [tab, setTab] = useState<"activity" | "messages" | "files">("activity");
  const activeFiles = useChatStore((s) => s.activeFiles);

  return (
    <>
      <div className="ws-tabs">
        <div className={`ws-tab ${tab === "activity" ? "active" : ""}`} onClick={() => setTab("activity")}>Activity</div>
        <div className={`ws-tab ${tab === "messages" ? "active" : ""}`} onClick={() => setTab("messages")}>Messages</div>
        <div className={`ws-tab ${tab === "files" ? "active" : ""}`} onClick={() => setTab("files")}>
          Files{activeFiles.length > 0 && <span className="ws-tab-count">{activeFiles.length}</span>}
        </div>
        <div className="ws-spacer" />
        <button className="ws-collapse" onClick={onCollapse} title="Collapse chat"><PanelRightClose size={15} /></button>
      </div>

      <TimerHeader />

      {tab === "activity" && <ActivityFeed />}
      {tab === "messages" && (
        <div className="ws-feed">
          <div className="ws-empty-note">The full transcript lives in the Activity tab. This view will list raw messages.</div>
        </div>
      )}
      {tab === "files" && (
        <div className="ws-feed">
          {activeFiles.length === 0 && (
            <div className="ws-empty-note">Generated files appear here once the deck is rendered.</div>
          )}
          {activeFiles.map((f) => (
            <FileCard key={f.id} file={f} />
          ))}
        </div>
      )}

      <Composer />
    </>
  );
}

function FileCard({ file }: { file: GeneratedFile }) {
  const name = file.filename || "presentation.pptx";
  const downloadUrl = buildDownloadUrl(file.file_url);
  const size = formatFileSize(file.file_size);
  return (
    <div className="ws-file-card">
      <div className="ws-file-head">
        <div className="ws-file-ic"><Presentation size={20} /></div>
        <div className="ws-file-meta">
          <div className="ws-file-name">{name}</div>
          <div className="ws-file-sub">
            <FileDown size={10} /> PPTX
            {file.slide_count ? ` - ${file.slide_count} slide${file.slide_count === 1 ? "" : "s"}` : ""}
            {size ? ` - ${size}` : ""}
          </div>
        </div>
        <span className="ws-file-ready">Ready</span>
      </div>
      <div className="ws-file-actions">
        <a className="ws-tb-btn primary" href={downloadUrl} download>
          <Download size={13} /> Download
        </a>
        <button className="ws-tb-btn" onClick={() => window.open(downloadUrl, "_blank")} title="Open in new tab">
          <ExternalLink size={13} /> Open
        </button>
      </div>
    </div>
  );
}

