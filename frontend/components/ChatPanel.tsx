"use client";

import { FormEvent, useState } from "react";
import { api, CatchUpData, ChatResponse, EvidenceItem, MemoryAnswer } from "@/lib/api";
import { EvidenceTrail } from "@/components/EvidenceTrail";

type Bubble = {
  role: "user" | "assistant";
  text: string;
  answer?: MemoryAnswer;
  catchup?: CatchUpData;
};

export function ChatPanel() {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<string>("detailed");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replay, setReplay] = useState<EvidenceItem | null>(null);
  const [messages, setMessages] = useState<Bubble[]>([
    {
      role: "assistant",
      text: "Ask what happened. Find what was decided. Know what you missed.",
    },
  ]);

  async function send(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", text: question }]);
    setInput("");
    try {
      const res: ChatResponse = await api.chat(question, { mode });
      if (res.type === "answer") {
        const data = res.data as MemoryAnswer;
        setMessages((m) => [
          ...m,
          { role: "assistant", text: res.formatted, answer: data },
        ]);
      } else {
        const data = res.data as CatchUpData;
        setMessages((m) => [
          ...m,
          { role: "assistant", text: res.formatted, catchup: data },
        ]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(input);
  }

  return (
    <section className="chat-panel">
      <div className="chat-toolbar">
        <label>
          Mode
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="detailed">Detailed</option>
            <option value="30sec">30 sec</option>
            <option value="decisions">Decisions only</option>
            <option value="tasks">Tasks only</option>
          </select>
        </label>
        <div className="quick-actions">
          <button type="button" onClick={() => void send("/catchup")} disabled={busy}>
            Catch Me Up
          </button>
          <button type="button" onClick={() => void send("/decisions")} disabled={busy}>
            Decisions
          </button>
        </div>
      </div>

      <div className="chat-log" aria-live="polite">
        {messages.map((m, i) => (
          <article key={i} className={`bubble ${m.role}`}>
            <pre className="bubble-text">{m.text}</pre>
            {m.answer?.evidence && (
              <EvidenceTrail items={m.answer.evidence} onReplay={setReplay} />
            )}
            {m.catchup?.evidence && (
              <EvidenceTrail items={m.catchup.evidence} onReplay={setReplay} />
            )}
          </article>
        ))}
        {busy && <p className="muted">Searching community memory…</p>}
        {error && <p className="error">{error}</p>}
      </div>

      <form className="chat-form" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="/ask What was decided about PostgreSQL?"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Ask
        </button>
      </form>

      {replay && (
        <div className="replay-modal" role="dialog">
          <div className="replay-card">
            <h3>Evidence Replay</h3>
            <p>
              {replay.source_label}
              {replay.author ? ` · ${replay.author}` : ""}
              {replay.meeting_offset_display
                ? ` · ${replay.meeting_offset_display}`
                : ""}
            </p>
            <blockquote>“{replay.excerpt}”</blockquote>
            {replay.replay_url && (
              <p className="muted">
                Endpoint: <code>{replay.replay_url}</code>
              </p>
            )}
            <button type="button" onClick={() => setReplay(null)}>
              Close
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
