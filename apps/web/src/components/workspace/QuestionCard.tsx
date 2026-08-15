"use client";

import { useEffect, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Check, X } from "lucide-react";

/**
 * A design direction offered as something to look at rather than read.
 *
 * A direction is a whole design system - ground, type pairing, motif,
 * density - and five words of text cannot convey the difference between two
 * of them.
 */
export interface PreviewOption {
  kind: "preview";
  id: string;
  label: string;
  description?: string;
  previewUrls: string[];
}

/**
 * One answer a user can pick.
 *
 * A bare string is every question the agent has ever asked, and stays exactly
 * that: sessions persisted before previews existed hold strings, and they must
 * keep rendering and answering unchanged.
 */
export type QuestionOption = string | PreviewOption;

export interface AgentQuestion {
  question: string;
  options?: QuestionOption[];
}

interface QuestionCardProps {
  /** Every question asked in this pause. */
  questions: AgentQuestion[];
  context?: string;
  answered?: boolean;
  answerText?: string;
}

function isPreview(option: QuestionOption): option is PreviewOption {
  return typeof option === "object" && option !== null && option.kind === "preview";
}

/** What goes in the reply - the label, with the id so the agent can resolve it. */
function answerFor(option: QuestionOption): string {
  return isPreview(option) ? `${option.label} (direction: ${option.id})` : option;
}

/** What identifies a selected option. */
function keyFor(option: QuestionOption): string {
  return isPreview(option) ? option.id : option;
}

/**
 * The colour an option is offering, when it is offering one.
 *
 * The design pause asks two questions - which direction, and which colour -
 * and the second arrives as text ("Forest green (#1B4332)"). Picking a colour
 * by reading its name is the thing the rendered directions exist to replace,
 * so the hex in the label is drawn as the colour it names.
 */
const HEX_IN_LABEL = /#[0-9A-Fa-f]{6}\b/;

function swatchOf(option: QuestionOption): string | null {
  if (isPreview(option)) return null;
  return option.match(HEX_IN_LABEL)?.[0] ?? null;
}

/**
 * The agent's consultation, as a form the user answers in one go.
 *
 * A pause can carry several questions - audience, focus, length, style - so
 * they are answered together and sent as a single reply. Asking them one at a
 * time meant a round trip each, which is what made the agent avoid asking at
 * all.
 *
 * An option is usually a few words. It can also be a whole design direction,
 * in which case it is shown as its rendered specimen slides: the difference
 * between a dark analyst look and an editorial serif one is not something
 * five words convey, and picking a look by reading adjectives is what this
 * replaces.
 */
export function QuestionCard({ questions, context, answered = false, answerText }: QuestionCardProps) {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isSending = useChatStore((s) => s.isSending);
  const [selected, setSelected] = useState<Record<number, string>>({});
  const [elaborate, setElaborate] = useState("");
  const [lightbox, setLightbox] = useState<{ urls: string[]; at: number; label: string } | null>(null);

  const answeredCount = Object.keys(selected).length;
  const canSend = answeredCount > 0 || elaborate.trim().length > 0;

  const pick = (qi: number, option: QuestionOption) =>
    setSelected((s) => {
      // Tapping the chosen option again clears it, so a mis-tap does not
      // force an answer the user did not mean.
      const next = { ...s };
      const key = keyFor(option);
      if (next[qi] === key) delete next[qi];
      else next[qi] = key;
      return next;
    });

  const answer = async () => {
    if (!canSend) return;
    const lines: string[] = [];
    questions.forEach((q, i) => {
      const chosen = (q.options || []).find((o) => keyFor(o) === selected[i]);
      if (chosen) {
        // Pair each answer with its question: the agent resumes from the chat
        // history, where a bare list of choices would be ambiguous.
        const text = answerFor(chosen);
        lines.push(questions.length > 1 ? `${q.question} ${text}` : text);
      }
    });
    if (elaborate.trim()) lines.push(elaborate.trim());
    const text = lines.join("\n");
    setElaborate("");
    setSelected({});
    await sendMessage(text);
  };

  if (answered) {
    return (
      <div className="ws-question ws-question-answered">
        {context && <div className="ws-q-ctx">{context}</div>}
        {questions.map((q, i) => (
          <div className="ws-q-block" key={i}>
            <div className="ws-q"><Check size={14} /> {q.question}</div>
          </div>
        ))}
        {answerText && (
          <div className="ws-q-elab">
            <label>Your answer:</label>
            {/* The direction's name, never the id it was resolved by. */}
            <div className="ws-q-answer-text">{prettyAnswer(answerText)}</div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ws-question">
      {context && <div className="ws-q-ctx">{context}</div>}
      {questions.map((q, i) => {
        const options = q.options || [];
        const previews = options.filter(isPreview);
        const showPreviews = previews.length > 0 && previews.length === options.length;
        return (
          <div className="ws-q-block" key={i}>
            <div className="ws-q">
              {questions.length > 1 && <span className="ws-q-num">{i + 1}</span>}
              {q.question}
            </div>
            {showPreviews ? (
              <div className="ws-q-directions">
                {previews.map((opt) => {
                  const chosen = selected[i] === opt.id;
                  return (
                    <div
                      key={opt.id}
                      className={`ws-q-direction ${chosen ? "selected" : ""}`}
                      role="button"
                      tabIndex={0}
                      aria-pressed={chosen}
                      onClick={() => pick(i, opt)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          pick(i, opt);
                        }
                      }}
                    >
                      <div className="ws-q-dir-shot">
                        {opt.previewUrls[0] ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={opt.previewUrls[0]} alt={`${opt.label} title slide`} />
                        ) : (
                          <div className="ws-q-dir-missing">No preview</div>
                        )}
                        <span className={`radio ${chosen ? "checked" : ""}`}>
                          {chosen && <Check size={10} />}
                        </span>
                      </div>
                      <div className="ws-q-dir-name">{opt.label}</div>
                      {opt.description && (
                        <div className="ws-q-dir-why">{opt.description}</div>
                      )}
                      {opt.previewUrls.length > 1 && (
                        <div className="ws-q-dir-thumbs">
                          {opt.previewUrls.map((url, ti) => (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              key={url}
                              src={url}
                              alt={`${opt.label} slide ${ti + 1}`}
                              className="ws-q-dir-thumb"
                              role="button"
                              tabIndex={0}
                              onClick={(e) => {
                                // Looking at a slide is not choosing the
                                // direction it belongs to.
                                e.stopPropagation();
                                setLightbox({ urls: opt.previewUrls, at: ti, label: opt.label });
                              }}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  setLightbox({ urls: opt.previewUrls, at: ti, label: opt.label });
                                }
                              }}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              !!options.length && (
                <div className="ws-q-options">
                  {options.map((opt, oi) => {
                    const key = keyFor(opt);
                    const text = isPreview(opt) ? opt.label : opt;
                    const swatch = swatchOf(opt);
                    return (
                      <div
                        key={oi}
                        className={`ws-q-option ${selected[i] === key ? "selected" : ""}`}
                        onClick={() => pick(i, opt)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            pick(i, opt);
                          }
                        }}
                      >
                        <span className={`radio ${selected[i] === key ? "checked" : ""}`}>
                          {selected[i] === key && <Check size={10} />}
                        </span>
                        {swatch && (
                          <span
                            className="ws-q-swatch"
                            style={{ background: swatch }}
                            aria-hidden
                          />
                        )}
                        <span className="otxt">{text}</span>
                      </div>
                    );
                  })}
                </div>
              )
            )}
          </div>
        );
      })}
      <div className="ws-q-elab">
        <label>Anything else, or a different answer</label>
        <textarea
          placeholder="Type your own answer or add detail..."
          value={elaborate}
          onChange={(e) => setElaborate(e.target.value)}
        />
      </div>
      <div className="ws-q-actions">
        <button className="ws-q-send" disabled={!canSend || isSending} onClick={answer}>
          {questions.length > 1 ? `Send ${answeredCount || ""} answer${answeredCount === 1 ? "" : "s"}`.replace("  ", " ") : "Send answer"}
        </button>
        <span className="ws-q-hint">
          {questions.length > 1
            ? `Pick an option for each (${answeredCount} of ${questions.length} chosen) - the agent resumes once you send.`
            : "Pick an option or type your own - the agent will resume."}
        </span>
      </div>
      {lightbox && (
        <Lightbox
          urls={lightbox.urls}
          at={lightbox.at}
          label={lightbox.label}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}

/** The id is how the agent resolves a choice; the name is what the user picked. */
function prettyAnswer(text: string): string {
  return text.replace(/\s*\(direction:\s*[^)]+\)/g, "");
}

function Lightbox({
  urls,
  at,
  label,
  onClose,
}: {
  urls: string[];
  at: number;
  label: string;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(at);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setIndex((i) => Math.min(urls.length - 1, i + 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, urls.length]);

  return (
    <div className="ws-q-lightbox" role="dialog" aria-label={`${label} specimens`} onClick={onClose}>
      <div className="ws-q-lightbox-inner" onClick={(e) => e.stopPropagation()}>
        <div className="ws-q-lightbox-bar">
          <span>{label} - slide {index + 1} of {urls.length}</span>
          <button aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={urls[index]} alt={`${label} slide ${index + 1}`} />
        {urls.length > 1 && (
          <div className="ws-q-lightbox-thumbs">
            {urls.map((url, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={url}
                src={url}
                alt={`${label} slide ${i + 1}`}
                className={i === index ? "active" : ""}
                onClick={() => setIndex(i)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
