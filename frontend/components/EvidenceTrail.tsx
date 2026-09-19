import type { EvidenceItem } from "@/lib/api";

type Props = {
  items: EvidenceItem[];
  onReplay?: (item: EvidenceItem) => void;
};

const kindIcon: Record<string, string> = {
  whatsapp_message: "💬",
  whatsapp_voice: "🎙️",
  teams_message: "💬",
  meeting_timestamp: "🎙️",
  document_section: "📄",
};

export function EvidenceTrail({ items, onReplay }: Props) {
  if (!items?.length) return null;

  return (
    <div className="evidence-trail">
      <h3>Evidence</h3>
      <ul>
        {items.map((ev, i) => (
          <li key={`${ev.message_id || ev.excerpt}-${i}`} className="evidence-item">
            <div className="evidence-meta">
              <span className="evidence-kind">
                {kindIcon[ev.kind] || "•"} {ev.source_label}
              </span>
              {ev.author && <span> · {ev.author}</span>}
              {ev.meeting_offset_display && (
                <span className="evidence-time"> · {ev.meeting_offset_display}</span>
              )}
              {!ev.meeting_offset_display && ev.timestamp && (
                <span className="evidence-time">
                  {" "}
                  · {new Date(ev.timestamp).toLocaleString()}
                </span>
              )}
            </div>
            <blockquote>“{ev.excerpt}”</blockquote>
            {(ev.replay_url || onReplay) && (
              <button
                type="button"
                className="replay-btn"
                onClick={() => onReplay?.(ev)}
              >
                ▶️ Replay evidence
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
