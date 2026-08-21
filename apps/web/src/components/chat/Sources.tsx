"use client";

import { useState } from "react";
import { ExternalLink, Globe, Brain, ChevronDown, ChevronUp } from "lucide-react";
import type { SourceInfo } from "@thinkslides/schemas";

interface SourcesProps {
  sources?: SourceInfo[];
}

export function Sources({ sources = [] }: SourcesProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!sources || sources.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 pt-3 border-t border-iron/30">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 text-caption text-fog hover:text-paper transition-colors"
      >
        <Globe size={13} />
        <span className="font-medium">{sources.length} source{sources.length !== 1 ? 's' : ''}</span>
        {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>

      {isExpanded && (
        <div className="mt-2 space-y-2">
          {sources.map((source, idx) => (
            <SourceItem key={`${source.url}-${idx}`} source={source} />
          ))}
        </div>
      )}
    </div>
  );
}

function SourceItem({ source }: { source: SourceInfo }) {
  const getFaviconUrl = (url: string) => {
    try {
      const domain = new URL(url).hostname;
      return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`;
    } catch {
      return null;
    }
  };

  const faviconUrl = getFaviconUrl(source.url);

  return (
    <a
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex items-center gap-2 px-2 py-1.5 rounded-md bg-graphite/30 hover:bg-graphite/50 transition-colors text-caption text-fog hover:text-paper"
    >
      {source.type === "web" && faviconUrl ? (
        // next/image would need this third-party host allow-listed in
        // next.config and buys nothing for a 16px favicon.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={faviconUrl}
          alt=""
          className="w-4 h-4 flex-shrink-0 rounded-sm"
          onError={(e) => {
            const target = e.target as HTMLImageElement;
            target.style.display = "none";
            const fallback = document.createElement("div");
            fallback.className = "w-4 h-4 flex-shrink-0 flex items-center justify-center rounded-sm bg-ice/20";
            fallback.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-ice"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`;
            target.parentNode?.replaceChild(fallback, target);
          }}
        />
      ) : (
        <Brain size={14} className="flex-shrink-0 text-ice" />
      )}
      <span className="flex-1 truncate font-medium">{source.title}</span>
      <ExternalLink size={11} className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
    </a>
  );
}