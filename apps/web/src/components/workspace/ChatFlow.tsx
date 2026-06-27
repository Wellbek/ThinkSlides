"use client";

import { useState } from "react";
import { ActivityFeed } from "./ActivityFeed";
import { Composer } from "./Composer";
import { PanelRightClose } from "lucide-react";

export function ChatFlow({ onCollapse }: { collapsed: boolean; onCollapse: () => void }) {
  const [tab, setTab] = useState<"activity" | "messages" | "files">("activity");

  return (
    <>
      <div className="ws-tabs">
        <div className={`ws-tab ${tab === "activity" ? "active" : ""}`} onClick={() => setTab("activity")}>Activity</div>
        <div className={`ws-tab ${tab === "messages" ? "active" : ""}`} onClick={() => setTab("messages")}>Messages</div>
        <div className={`ws-tab ${tab === "files" ? "active" : ""}`} onClick={() => setTab("files")}>Files</div>
        <div className="ws-spacer" />
        <button className="ws-collapse" onClick={onCollapse} title="Collapse chat"><PanelRightClose size={15} /></button>
      </div>

      {tab === "activity" && <ActivityFeed />}
      {tab === "messages" && (
        <div className="ws-feed">
          <div className="ws-empty-note">The full transcript lives in the Activity tab. This view will list raw messages.</div>
        </div>
      )}
      {tab === "files" && (
        <div className="ws-feed">
          <div className="ws-empty-note">Generated files appear here once the deck is rendered.</div>
        </div>
      )}

      <Composer />
    </>
  );
}
