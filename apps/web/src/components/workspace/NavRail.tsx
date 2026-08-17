"use client";

import { useEffect, useMemo } from "react";
import { useChatStore } from "@/stores/chat-store";
import { useConnectionsStore } from "@/stores/connections-store";
import { ConnectionsPanel } from "@/components/workspace/ConnectionsPanel";
import { Home, History, Plug, Settings, Plus, MessageSquare } from "lucide-react";

/** Which extended panel the rail is showing, if any. */
export type RailPanel = "history" | "connections";

interface NavRailProps {
  extended: boolean;
  panel: RailPanel;
  view: "home" | "workspace";
  onOpenPanel: (panel: RailPanel) => void;
  onHome: () => void;
  onPickSession: (id: string) => void;
  onNewChat: () => void;
}

export function NavRail({
  extended,
  panel,
  view,
  onOpenPanel,
  onHome,
  onPickSession,
  onNewChat,
}: NavRailProps) {
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setSettingsOpen = useChatStore((s) => s.setSettingsOpen);
  const refreshConnections = useConnectionsStore((s) => s.refresh);
  const degradedCount = useConnectionsStore((s) => s.degradedCount());

  const grouped = useMemo(() => groupByDate(sessions), [sessions]);

  // The count and the status dot are visible without the panel being open, so
  // they cannot wait for the panel to fetch them.
  useEffect(() => {
    refreshConnections();
  }, [refreshConnections]);

  return (
    <nav className="ws-nav">
      <div className="ws-nav-icons">
        <div className="ws-logo">T</div>
        <button
          className={`ws-nav-icon ${view === "home" ? "active" : ""}`}
          onClick={onHome}
          title="Home"
        >
          <Home size={16} />
        </button>
        <button
          className={`ws-nav-icon ${extended && panel === "history" ? "active" : ""}`}
          onClick={() => onOpenPanel("history")}
          title="History"
        >
          <History size={16} />
        </button>
        <button
          className={`ws-nav-icon ${extended && panel === "connections" ? "active" : ""}`}
          onClick={() => onOpenPanel("connections")}
          title="Connections"
        >
          <Plug size={16} />
          {degradedCount > 0 && <span className="ws-nav-badge" aria-hidden />}
        </button>
        <div className="ws-nav-sep" />
        <button className="ws-nav-icon" onClick={() => setSettingsOpen(true)} title="Settings">
          <Settings size={16} />
        </button>
      </div>

      {extended && panel === "connections" && <ConnectionsPanel />}

      {extended && panel === "history" && (
        <div className="ws-nav-panel">
          <div className="ws-nav-panel-head">
            <button className="ws-new-chat" onClick={onNewChat}>
              <Plus size={14} /> New chat
            </button>
          </div>
          <div className="ws-history">
            {sessions.length === 0 && (
              <div className="ws-empty-note">No chats yet. Start a new one.</div>
            )}
            {Object.entries(grouped).map(([label, items]) => (
              <div key={label}>
                <div className="ws-history-group">{label}</div>
                {items.map((s) => (
                  <div
                    key={s.id}
                    className={`ws-history-item ${activeSessionId === s.id ? "active" : ""}`}
                    onClick={() => onPickSession(s.id)}
                    title={s.title}
                  >
                    <MessageSquare size={13} style={{ flexShrink: 0, color: "#444" }} />
                    <span className="nm">{s.title}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </nav>
  );
}

function groupByDate(sessions: { id: string; title: string; updated_at: string }[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);
  const groups: Record<string, typeof sessions> = {};
  for (const session of sessions) {
    const date = new Date(session.updated_at);
    let label: string;
    if (date >= today) label = "Today";
    else if (date >= yesterday) label = "Yesterday";
    else if (date >= weekAgo) label = "This Week";
    else label = "Older";
    (groups[label] ||= []).push(session);
  }
  return groups;
}
