"use client";

import { useState, useEffect } from "react";
import { useChatStore } from "@/stores/chat-store";
import { ChevronDown, Presentation } from "lucide-react";

export function TemplateSelector() {
  const { templates, activeSessionId, setSessionTemplate } = useChatStore();
  const activeSession = useChatStore((s) =>
    s.activeSessionId ? s.sessions.find((ses) => ses.id === s.activeSessionId) : null
  );
  const [isOpen, setIsOpen] = useState(false);

  const currentTemplateId = activeSession?.templateBadge
    ? undefined
    : undefined; // resolved from session detail

  const handleSelect = (templateId: string | null) => {
    if (activeSessionId) {
      setSessionTemplate(activeSessionId, templateId);
    }
    setIsOpen(false);
  };

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 bg-surface-50 border border-surface-100 rounded-lg text-xs text-gray-300 hover:border-brand-500/30 transition-colors"
      >
        <Presentation size={14} />
        <span>
          {activeSession?.templateBadge || "Select template"}
        </span>
        <ChevronDown size={12} />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-64 bg-surface-50 border border-surface-100 rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto">
          {/* No template option */}
          <button
            onClick={() => handleSelect(null)}
            className="w-full text-left px-3 py-2 text-sm text-gray-300 hover:bg-surface-100 rounded-t-lg"
          >
            No template (default theme)
          </button>

          {/* Template list */}
          {templates.map((tpl) => (
            <button
              key={tpl.id}
              onClick={() => handleSelect(tpl.id)}
              className="w-full text-left px-3 py-2 text-sm text-gray-300 hover:bg-surface-100 flex items-center gap-2"
            >
              <Presentation size={12} className="text-brand-400 shrink-0" />
              <div className="min-w-0">
                <p className="truncate">{tpl.name}</p>
                <p className="text-xs text-gray-500">
                  v{tpl.version} · {tpl.tags.join(", ")}
                </p>
              </div>
            </button>
          ))}

          {templates.length === 0 && (
            <p className="px-3 py-4 text-xs text-gray-500 text-center">
              No templates yet. Upload one in Settings.
            </p>
          )}
        </div>
      )}
    </div>
  );
}