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
    templates, // We need this to map template_id -> template name
  } = useChatStore();

  const [searchQuery, setSearchQuery] = useState("");

  // Client-side filtering (no API call)
  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const query = searchQuery.toLowerCase();
    return sessions.filter((s) => s.title.toLowerCase().includes(query));
  }, [sessions, searchQuery]);

  // Transform session data to what the UI expects
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
    // createSession expects optional templateId, not an object
    await createSession();
  };

  return (
    <aside className="w-[280px] min-w-[280px] bg-surface border-r border-surface-100 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-surface-100">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-lg font-semibold text-gray-100">SlideAgent</h1>
          <button
            onClick={handleNewChat}
            className="p-1.5 rounded-md hover:bg-surface-50 text-gray-400 hover:text-gray-200 transition-colors"
            title="New Chat"
          >
            <Plus size={20} />
          </button>
        </div>

        {/* Search */}
        <div className="relative">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500"
          />
          <input
            type="text"
            placeholder="Search sessions..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-surface-50 border border-surface-100 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-300 placeholder-gray-500 focus:outline-none focus:border-brand-500/50"
          />
        </div>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto p-2">
        {Object.entries(grouped).map(([dateLabel, items]) => (
          <div key={dateLabel} className="mb-4">
            <p className="px-3 py-1 text-xs font-medium text-gray-500 uppercase tracking-wider">
              {dateLabel}
            </p>
            {items.map((session) => (
              <div
                key={session.id}
                onClick={() => setActiveSession(session.id)}
                className={`group flex items-start gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${
                  activeSessionId === session.id
                    ? "bg-brand-600/20 text-gray-100"
                    : "hover:bg-surface-50 text-gray-400 hover:text-gray-200"
                }`}
              >
                <MessageSquare size={16} className="mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm truncate">{session.title}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {session.templateBadge && (
                      <span className="inline-flex items-center gap-1 text-xs text-brand-400">
                        <Presentation size={10} />
                        {session.templateBadge}
                      </span>
                    )}
                    {session.slideCount > 0 && (
                      <span className="text-xs text-gray-500">
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
          <div className="text-center py-8 text-gray-500 text-sm">
            {searchQuery ? "No sessions found" : "No chats yet"}
          </div>
        )}
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