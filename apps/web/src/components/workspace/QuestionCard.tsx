"use client";

import { useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Check } from "lucide-react";

interface QuestionCardProps {
  question: string;
  options?: string[];
  context?: string;
  answered?: boolean;
  answerText?: string;
}

export function QuestionCard({ question, options = [], context, answered = false, answerText }: QuestionCardProps) {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isSending = useChatStore((s) => s.isSending);
  const [selected, setSelected] = useState<string | null>(null);
  const [elaborate, setElaborate] = useState("");

  const answer = async () => {
    const parts: string[] = [];
    if (selected) parts.push(selected);
    if (elaborate.trim()) parts.push(elaborate.trim());
    if (parts.length === 0) return;
    const text = parts.join("\n\n");
    setElaborate("");
    setSelected(null);
    await sendMessage(text);
  };

  // If already answered, show answered state
  if (answered) {
    const answeredOption = answerText ? answerText.split("\n\n")[0] : null;
    return (
      <div className="ws-question ws-question-answered">
        <div className="ws-q"><Check size={14} /> {question}</div>
        {context && <div className="ws-q-ctx">{context}</div>}
        {options.length > 0 && answeredOption && (
          <div className="ws-q-options">
            <div className="ws-q-option answered">
              <span className="radio checked">
                <Check size={10} />
              </span>
              <span className="otxt">{answeredOption}</span>
            </div>
          </div>
        )}
        {answerText && !options.includes(answerText.split("\n\n")[0]) && (
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
      <div className="ws-q">{question}</div>
      {context && <div className="ws-q-ctx">{context}</div>}
      {options.length > 0 && (
        <div className="ws-q-options">
          {options.map((opt, i) => (
            <div
              key={i}
              className={`ws-q-option ${selected === opt ? "selected" : ""}`}
              onClick={() => setSelected(opt)}
            >
              <span className="radio" />
              <span className="otxt">{opt}</span>
            </div>
          ))}
        </div>
      )}
      <div className="ws-q-elab">
        <label>Or elaborate / give a different answer</label>
        <textarea
          placeholder="Type your own answer or add detail..."
          value={elaborate}
          onChange={(e) => setElaborate(e.target.value)}
        />
      </div>
      <div className="ws-q-actions">
        <button className="ws-q-send" disabled={(!selected && !elaborate.trim()) || isSending} onClick={answer}>
          Send answer
        </button>
        <span className="ws-q-hint">Pick an option or type your own - the agent will resume.</span>
      </div>
    </div>
  );
}
