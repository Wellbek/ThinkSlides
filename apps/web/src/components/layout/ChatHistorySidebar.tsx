"use client";

import { useChatStore } from "@/stores/chat-store";
import { formatDistanceToNow } from "@/lib/date";
import { MessageSquare, Search, Trash2, Plus, Presentation } from "lucide-react";
import { useState, useMemo } from "react";

export function ChatHistorySidebar() {
  const {
    sessions,
    activeSessionId,
    setActiveSession,
    createSession,
    deleteSession,
    templates,
  } = useChatStore();

  const [searchQuery, setSearchQuery] = useState("");

  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const query = searchQuery.toLowerCase();
    return sessions.filter((s) => s.title.toLowerCase().includes(query));
  }, [sessions, searchQuery]);

  const transformedSessions = useMemo(() => {
    return filteredSessions.map((session) => ({
      id: session.id,
      title: session.title,
      updatedAt: session.updated_at,
      slideCount: session.slide_count,
      templateBadge: session.template_id
        ? templates.find((t) => t.id === session.template_id)?.name
        : undefined,
    }));
  }, [filteredSessions, templates]);

  const grouped = groupByDate(transformedSessions);

  const handleNewChat = async () => {
    await createSession();
  };

  return (
    <aside className="w-[280px] min-w-[280px] bg-obsidian border-r border-iron flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-iron">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-heading-sm font-medium text-paper">ThinkSlides</h1>
          <button
            onClick={handleNewChat}
            className="p-1.5 rounded-md hover:bg-slate-dark text-fog hover:text-paper transition-colors"
            title="New Chat"
          >
            <Plus size={20} />
          </button>
        </div>

        {/* Search */}
        <div className="relative">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-fog"
          />
          <input
            type="text"
            placeholder="Search sessions..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-charcoal border border-iron rounded-md pl-9 pr-3 py-2 text-caption text-paper placeholder-fog focus:outline-none focus:border-ice"
          />
        </div>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto p-2">
        {Object.entries(grouped).map(([dateLabel, items]) => (
          <div key={dateLabel} className="mb-4">
            <p className="px-3 py-1 text-caption font-medium text-fog uppercase tracking-wider">
              {dateLabel}
            </p>
            {items.map((session) => (
              <div
                key={session.id}
                onClick={() => setActiveSession(session.id)}
                className={`group flex items-start gap-2 px-3 py-2.5 rounded-md cursor-pointer transition-colors ${
                  activeSessionId === session.id
                    ? "bg-ice/10 text-paper border-l-2 border-ice"
                    : "hover:bg-slate-dark text-fog hover:text-paper"
                }`}
              >
                <MessageSquare size={16} className="mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-caption truncate">{session.title}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {session.templateBadge && (
                      <span className="inline-flex items-center gap-1 text-caption text-ice">
                        <Presentation size={10} />
                        {session.templateBadge}
                      </span>
                    )}
                    {session.slideCount > 0 && (
                      <span className="text-caption text-fog font-mono">
                        {session.slideCount} slides
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteSession(session.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-500/20 hover:text-red-400 transition-all"
                  title="Delete session"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        ))}

        {transformedSessions.length === 0 && (
          <div className="text-center py-8 text-fog text-caption">
            {searchQuery ? "No sessions found" : "No chats yet"}
          </div>
        )}
      </div>

      {/* Settings link at bottom */}
      <div className="p-4 border-t border-iron">
        <button
          onClick={() => (window.location.href = '/settings')}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-fog hover:text-paper hover:bg-slate-dark transition-colors text-caption"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H5.78a1.65 1.65 0 0 0-1.51 1 1.65 1.65 0 0 0 .33 1.82l.06.06A10 10 0 0 0 12 17.66a10 10 0 0 0 6.62-2.6l.06-.06Z"/><path d="M2 12a10 10 0 0 0 20 0"/><path d="M2 12h20"/></svg>
          <span>Settings</span>
        </button>
      </div>
    </aside>
  );
}

function groupByDate(
  sessions: Array<{ id: string; title: string; updatedAt: string; templateBadge?: string; slideCount: number }>
): Record<string, typeof sessions> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const groups: Record<string, typeof sessions> = {};

  for (const session of sessions) {
    const date = new Date(session.updatedAt);
    let label: string;

    if (date >= today) {
      label = "Today";
    } else if (date >= yesterday) {
      label = "Yesterday";
    } else if (date >= weekAgo) {
      label = "This Week";
    } else {
      label = "Older";
    }

    if (!groups[label]) groups[label] = [];
    groups[label].push(session);
  }

  return groups;
}
