"use client";

import { useState } from "react";
import { ActivityFeed } from "./ActivityFeed";
import { Composer } from "./Composer";
import { TimerHeader } from "@/components/chat/TimerHeader";
import { RetryStrip } from "@/components/chat/RetryStrip";
import { useChatStore, type GeneratedFile } from "@/stores/chat-store";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { PanelRightClose, Download, ExternalLink, Presentation, FileDown } from "lucide-react";

export function ChatFlow({ onCollapse }: { collapsed: boolean; onCollapse: () => void }) {
  const [tab, setTab] = useState<"activity" | "files">("activity");
  const activeFiles = useChatStore((s) => s.activeFiles);
  // activeFiles also carries individual chart/image assets now (#187) -
  // this tab is the rendered deck only, same as before. Assets live in the
  // details sidebar's Assets section; listing them here too would just
  // duplicate that panel and inflate this tab's count badge.
  const deckFiles = activeFiles.filter((f) => !f.file_type || f.file_type === "pptx");

  return (
    <>
      <div className="ws-tabs">
        <div className={`ws-tab ${tab === "activity" ? "active" : ""}`} onClick={() => setTab("activity")}>Activity</div>
        <div className={`ws-tab ${tab === "files" ? "active" : ""}`} onClick={() => setTab("files")}>
          Files{deckFiles.length > 0 && <span className="ws-tab-count">{deckFiles.length}</span>}
        </div>
        <div className="ws-spacer" />
        <button className="ws-collapse" onClick={onCollapse} title="Collapse chat"><PanelRightClose size={15} /></button>
      </div>

      <TimerHeader />

      {/* The tab bodies share one positioning context so a provider back-off
          can be pinned over the bottom of whichever one is showing, without
          entering its layout - a wait that pushed the feed would move the
          content the user is reading, twice per wait. */}
      <div className="ws-flow-body">
        {tab === "activity" && <ActivityFeed />}
        {tab === "files" && (
          <div className="ws-feed">
            {deckFiles.length === 0 && (
              <div className="ws-empty-note">Generated files appear here once the deck is rendered.</div>
            )}
            {deckFiles.map((f) => (
              <FileCard key={f.id} file={f} />
            ))}
          </div>
        )}
        <RetryStrip />
      </div>

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

