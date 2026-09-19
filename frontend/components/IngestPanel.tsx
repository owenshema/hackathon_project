"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

export function IngestPanel() {
  const [title, setTitle] = useState("UniPods Hackathon Planning");
  const [author, setAuthor] = useState("Alex");
  const [message, setMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function clearMock() {
    setBusy(true);
    setStatus(null);
    try {
      const res = await api.clearMock();
      setStatus(
        `Cleared mock data (${res.deleted_messages} messages, ${res.deleted_chunks} chunks).`
      );
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Clear failed");
    } finally {
      setBusy(false);
    }
  }

  async function onMessage(e: FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    setBusy(true);
    try {
      const res = await api.ingestMessage(message, author);
      setStatus(`Ingested ${res.ingested} real message(s) into memory.`);
      setMessage("");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  async function onTranscript(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    try {
      const res = await api.uploadTranscript(title, file);
      setStatus(`Meeting indexed: ${res.meeting_id} (${res.status})`);
      setFile(null);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ingest-panel">
      <header>
        <h2>Memory intake</h2>
        <p>
          Real WhatsApp group messages are stored automatically. Optionally add
          transcripts or clear leftover mock rows.
        </p>
      </header>

      <button type="button" className="primary" onClick={() => void clearMock()} disabled={busy}>
        Clear mock / demo data
      </button>

      <form onSubmit={onMessage} className="stack">
        <h3>Add a real chat line</h3>
        <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Author" />
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Message text…"
          rows={3}
        />
        <button type="submit" disabled={busy}>
          Ingest message
        </button>
      </form>

      <form onSubmit={onTranscript} className="stack">
        <h3>Upload meeting transcript</h3>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Meeting title" />
        <input
          type="file"
          accept=".txt,.vtt,.srt,.md"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <button type="submit" disabled={busy || !file}>
          Index transcript
        </button>
      </form>

      {status && <p className="status">{status}</p>}
    </section>
  );
}
