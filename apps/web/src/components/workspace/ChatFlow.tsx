"use client";

import { useState } from "react";
import { ActivityFeed, FeedItem, fmtTime } from "./ActivityFeed";
import { MarkdownText } from "./MarkdownText";
import { Composer } from "./Composer";
import { TimerHeader } from "@/components/chat/TimerHeader";
import { RetryStrip } from "@/components/chat/RetryStrip";
import { useChatStore, type GeneratedFile } from "@/stores/chat-store";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { PanelRightClose, Download, ExternalLink, Presentation, FileDown } from "lucide-react";

export function ChatFlow({ onCollapse }: { collapsed: boolean; onCollapse: () => void }) {
  const [tab, setTab] = useState<"activity" | "messages" | "files">("activity");
  const activeFiles = useChatStore((s) => s.activeFiles);
  const activeMessages = useChatStore((s) => s.activeMessages);

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

      {/* The tab bodies share one positioning context so a provider back-off
          can be pinned over the bottom of whichever one is showing, without
          entering its layout - a wait that pushed the feed would move the
          content the user is reading, twice per wait. */}
      <div className="ws-flow-body">
        {tab === "activity" && <ActivityFeed />}
        {tab === "messages" && (
          <div className="ws-feed">
            {activeMessages.length === 0 && (
              <div className="ws-empty-note">Send a message to start the agent workflow.</div>
            )}
            {activeMessages.map((m) =>
              m.role === "user" ? (
                <FeedItem key={m.id} dot={<span className="ws-av indigo">WK</span>} dotClass="ws-dot-user">
                  <div className="ws-feed-header"><span className="lbl">You</span><span className="ts">{fmtTime(m.created_at)}</span></div>
                  <div className="ws-feed-text">{m.content}</div>
                </FeedItem>
              ) : (
                <FeedItem key={m.id} dot="◆" dotClass="ws-dot-fact">
                  <div className="ws-feed-header"><span className="lbl">Assistant</span><span className="ts">{fmtTime(m.created_at)}</span></div>
                  {m.content ? (
                    <MarkdownText className="ws-feed-text">{m.content}</MarkdownText>
                  ) : (
                    <div className="ws-empty-note">Working…</div>
                  )}
                </FeedItem>
              )
            )}
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

