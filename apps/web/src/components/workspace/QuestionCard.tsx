"use client";

import { useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Check } from "lucide-react";

export interface AgentQuestion {
  question: string;
  options?: string[];
}

interface QuestionCardProps {
  /** Every question asked in this pause. */
  questions: AgentQuestion[];
  context?: string;
  answered?: boolean;
  answerText?: string;
}

/**
 * The agent's consultation, as a form the user answers in one go.
 *
 * A pause can carry several questions - audience, focus, length, style - so
 * they are answered together and sent as a single reply. Asking them one at a
 * time meant a round trip each, which is what made the agent avoid asking at
 * all.
 */
export function QuestionCard({ questions, context, answered = false, answerText }: QuestionCardProps) {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isSending = useChatStore((s) => s.isSending);
  const [selected, setSelected] = useState<Record<number, string>>({});
  const [elaborate, setElaborate] = useState("");

  const answeredCount = Object.keys(selected).length;
  const canSend = answeredCount > 0 || elaborate.trim().length > 0;

  const answer = async () => {
    if (!canSend) return;
    const lines: string[] = [];
    questions.forEach((q, i) => {
      const choice = selected[i];
      if (choice) {
        // Pair each answer with its question: the agent resumes from the chat
        // history, where a bare list of choices would be ambiguous.
        lines.push(questions.length > 1 ? `${q.question} ${choice}` : choice);
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
            <div className="ws-q-answer-text">{answerText}</div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ws-question">
      {context && <div className="ws-q-ctx">{context}</div>}
      {questions.map((q, i) => (
        <div className="ws-q-block" key={i}>
          <div className="ws-q">
            {questions.length > 1 && <span className="ws-q-num">{i + 1}</span>}
            {q.question}
          </div>
          {!!q.options?.length && (
            <div className="ws-q-options">
              {q.options.map((opt, oi) => (
                <div
                  key={oi}
                  className={`ws-q-option ${selected[i] === opt ? "selected" : ""}`}
                  onClick={() =>
                    setSelected((s) => {
                      // Tapping the chosen option again clears it, so a
                      // mis-tap does not force an answer the user did not mean.
                      const next = { ...s };
                      if (next[i] === opt) delete next[i];
                      else next[i] = opt;
                      return next;
                    })
                  }
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected((s) => ({ ...s, [i]: opt }));
                    }
                  }}
                >
                  <span className={`radio ${selected[i] === opt ? "checked" : ""}`}>
                    {selected[i] === opt && <Check size={10} />}
                  </span>
                  <span className="otxt">{opt}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
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
    </div>
  );
}
