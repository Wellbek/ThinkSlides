"use client";

import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

/**
 * Renders agent-authored text as Markdown.
 *
 * The models write their narration in Markdown (bold, numbered slide lists,
 * links), and the activity feed used to print it verbatim - so users saw raw
 * `**Establishment Timeline**` and `[Download](...)` syntax in the chat.
 *
 * Two deliberate constraints on what is rendered:
 *
 * - Headings are demoted to bold paragraphs. Feed entries sit inside a dense
 *   timeline; a real `<h1>` would tower over the surrounding UI.
 * - Links to anything other than http(s) are rendered as plain text. Models
 *   routinely invent `sandbox:/app/output/<uuid>.pptx` download links, which
 *   point at a path inside the AI container and are dead in a browser. The
 *   real download comes from the Files tab, driven by the `file_generated`
 *   event.
 */

const SAFE_LINK = /^https?:\/\//i;

const COMPONENTS: Components = {
  a({ href, children, ...props }) {
    if (!href || !SAFE_LINK.test(href)) {
      return <span className="md-dead-link">{children}</span>;
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
        {children}
      </a>
    );
  },
  h1: ({ children }) => <p className="md-heading">{children}</p>,
  h2: ({ children }) => <p className="md-heading">{children}</p>,
  h3: ({ children }) => <p className="md-heading">{children}</p>,
  h4: ({ children }) => <p className="md-heading">{children}</p>,
  h5: ({ children }) => <p className="md-heading">{children}</p>,
  h6: ({ children }) => <p className="md-heading">{children}</p>,
  // Images would let generated text pull in remote content; the feed has no
  // use for them.
  img: () => null,
};

export function MarkdownText({ children, className }: { children: string; className?: string }) {
  return (
    <div className={className ? `ws-md ${className}` : "ws-md"}>
      <ReactMarkdown components={COMPONENTS}>{children}</ReactMarkdown>
    </div>
  );
}
