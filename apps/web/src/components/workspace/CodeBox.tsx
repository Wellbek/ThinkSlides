"use client";

/**
 * The script the agent wrote, as it writes it - and, when it is fixing one, the
 * diff against the script that failed.
 *
 * Two states, from the two events. A `code_delta` entry is a script still
 * arriving, so it renders as plain streaming text with a caret: highlighting a
 * half-written line costs a full re-tokenise per chunk and flickers as the
 * lexer changes its mind about an unterminated string. A `code_ready` entry is
 * final, so it is highlighted - and if it carries a `previous`, it renders as a
 * unified diff instead, because what a reader needs on the second attempt is
 * the fix, not the whole script again.
 *
 * The diff is a short LCS over two lists of lines, written here rather than
 * pulled in as a dependency. It is thirty lines, it has no other consumer, and
 * the alternative is a package whose whole surface is this one function.
 */

import { useMemo } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import type { CodeDeltaEvent, CodeReadyEvent } from "@thinkslides/schemas";

type DiffKind = "same" | "add" | "remove";

interface DiffLine {
  kind: DiffKind;
  text: string;
}

/**
 * A unified diff of two line lists, longest-common-subsequence based.
 *
 * O(n*m) in lines, which is the right trade here: a script is capped at 20k
 * characters by the sandbox gate, so the table is small and bounded, and the
 * simple algorithm is the one whose output is obviously correct.
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");

  const lengths: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0)
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lengths[i][j] =
        a[i] === b[j]
          ? lengths[i + 1][j + 1] + 1
          : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (lengths[i + 1][j] >= lengths[i][j + 1]) {
      out.push({ kind: "remove", text: a[i] });
      i++;
    } else {
      out.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  while (i < a.length) out.push({ kind: "remove", text: a[i++] });
  while (j < b.length) out.push({ kind: "add", text: b[j++] });
  return out;
}

const MARKER: Record<DiffKind, string> = { same: " ", add: "+", remove: "-" };

function Diff({ before, after }: { before: string; after: string }) {
  const lines = useMemo(() => diffLines(before, after), [before, after]);
  const added = lines.filter((line) => line.kind === "add").length;
  const removed = lines.filter((line) => line.kind === "remove").length;

  return (
    <>
      <div className="ws-code-diffstat">
        <span className="add">+{added}</span>
        <span className="remove">-{removed}</span>
        <span className="ws-code-diffstat-note">since the last attempt</span>
      </div>
      <pre className="ws-code-pre ws-code-diff">
        {lines.map((line, index) => (
          <div key={index} className={`ws-code-line ${line.kind}`}>
            <span className="ws-code-marker">{MARKER[line.kind]}</span>
            <span className="ws-code-text">{line.text || " "}</span>
          </div>
        ))}
      </pre>
    </>
  );
}

export function CodeBox({
  event,
}: {
  event: CodeDeltaEvent | CodeReadyEvent;
}) {
  const streaming = event.type === "code_delta";
  const source = streaming ? event.text : event.source;
  const previous = streaming ? undefined : event.previous;
  const reason = streaming ? undefined : event.reason;

  return (
    <div className="ws-code-box">
      <div className="ws-code-head">
        <span className="ws-code-title">
          {streaming
            ? "Writing a script"
            : previous
              ? "Revised script"
              : "Script"}
        </span>
        <span className="ws-code-lang">python</span>
      </div>

      {reason && (
        <p className="ws-code-reason" title={reason}>
          {reason}
        </p>
      )}

      {previous ? (
        <Diff before={previous} after={source} />
      ) : streaming ? (
        // Deliberately not highlighted while it streams - see the note at the
        // top of this file.
        <pre className="ws-code-pre ws-code-streaming">
          {source}
          <span className="ws-code-caret" aria-hidden="true" />
        </pre>
      ) : (
        <SyntaxHighlighter
          language="python"
          style={vscDarkPlus}
          customStyle={{
            margin: 0,
            background: "transparent",
            fontSize: "0.75rem",
            lineHeight: 1.55,
            padding: "0.5rem 0.75rem",
          }}
          codeTagProps={{ style: { fontFamily: "var(--font-mono, monospace)" } }}
        >
          {source}
        </SyntaxHighlighter>
      )}
    </div>
  );
}
