"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, PlatformStatus } from "@/lib/api";

export function PlatformsPanel() {
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [platform, setPlatform] = useState<"whatsapp" | "teams">("whatsapp");
  const [text, setText] = useState("What did they decide about the database?");
  const [sendLive, setSendLive] = useState(false);
  const [destination, setDestination] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .platformsStatus()
      .then(setStatus)
      .catch((e) => setError(e instanceof Error ? e.message : "Status failed"));
  }, []);

  async function onSimulate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    if (sendLive && !destination.trim()) {
      setError(
        platform === "whatsapp"
          ? "Enter the WhatsApp phone number or group id to send live."
          : "Enter the Teams conversation id to send live."
      );
      setBusy(false);
      return;
    }
    try {
      const res = await api.simulatePlatform(platform, text, {
        sendLive,
        conversationId: destination.trim() || undefined,
      });
      const delivery = res.delivery || {};
      setResult(
        [
          `[${res.platform}] delivery=${delivery.mode || "n/a"} ok=${delivery.ok ?? "n/a"}`,
          delivery.status ? `status=${delivery.status}` : "",
          delivery.error ? `error=${delivery.error}` : "",
          "",
          res.formatted,
        ]
          .filter(Boolean)
          .join("\n")
      );
      const fresh = await api.platformsStatus();
      setStatus(fresh);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Simulate failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="platforms-panel">
      <header>
        <h2>WhatsApp &amp; Teams</h2>
        <p>
          Same memory brain on both platforms. Live delivery needs Meta / Azure
          credentials; simulate works now.
        </p>
      </header>

      <div className="platform-status-grid">
        <div className={`plat-card ${status?.whatsapp.configured ? "on" : "off"}`}>
          <strong>WhatsApp</strong>
          <span>
            {status?.whatsapp.configured
              ? `Connected (${status.whatsapp.provider || "live"})`
              : "Not connected"}
          </span>
          {status?.whatsapp.phone && (
            <small>Number: {status.whatsapp.phone}</small>
          )}
          {!!status?.whatsapp.missing?.length && (
            <small>Missing: {status.whatsapp.missing.join(", ")}</small>
          )}
          {status?.whatsapp.webhook_url && (
            <code>{status.whatsapp.webhook_url}</code>
          )}
        </div>
        <div className={`plat-card ${status?.teams.configured ? "on" : "off"}`}>
          <strong>Teams</strong>
          <span>{status?.teams.configured ? "Connected" : "Not connected"}</span>
          {!!status?.teams.missing?.length && (
            <small>Missing: {status.teams.missing.join(", ")}</small>
          )}
          {status?.teams.webhook_url && <code>{status.teams.webhook_url}</code>}
        </div>
      </div>

      <form className="stack" onSubmit={onSimulate}>
        <h3>{sendLive ? "Send live platform reply" : "Simulate inbound message"}</h3>
        <select
          value={platform}
          onChange={(e) => setPlatform(e.target.value as "whatsapp" | "teams")}
        >
          <option value="whatsapp">WhatsApp</option>
          <option value="teams">Teams</option>
        </select>
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask as if from WhatsApp / Teams…"
        />
        <label className="check-row">
          <input
            type="checkbox"
            checked={sendLive}
            onChange={(e) => setSendLive(e.target.checked)}
          />
          Send the generated reply live
        </label>
        {sendLive && (
          <input
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            placeholder={
              platform === "whatsapp"
                ? "+250... or 123456789@g.us"
                : "Teams conversation id"
            }
          />
        )}
        <button type="submit" disabled={busy || !text.trim()}>
          {sendLive ? `Send live via ${platform}` : `Simulate ${platform}`}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {result && <pre className="sim-result">{result}</pre>}
    </section>
  );
}
