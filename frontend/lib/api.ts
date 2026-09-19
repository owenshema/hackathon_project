const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type EvidenceItem = {
  kind: string;
  source_label: string;
  author?: string | null;
  timestamp?: string | null;
  meeting_offset_sec?: number | null;
  meeting_offset_display?: string | null;
  excerpt: string;
  message_id?: string | null;
  meeting_id?: string | null;
  page_or_section?: string | null;
  replay_url?: string | null;
};

export type MemoryAnswer = {
  answer: string;
  confidence: string;
  decision?: string | null;
  reason?: string | null;
  evidence: EvidenceItem[];
  command?: string | null;
};

export type CatchUpData = {
  important: string[];
  decisions: string[];
  discussions: string[];
  action_items: string[];
  evidence: EvidenceItem[];
};

export type ChatResponse = {
  type: "answer" | "catchup";
  formatted: string;
  data: MemoryAnswer | CatchUpData;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export type PlatformStatus = {
  whatsapp: {
    configured: boolean;
    provider?: string;
    phone?: string | null;
    device_id?: string | null;
    webhook_url: string;
    verify_token: string;
    missing: string[];
    setup: string[];
  };
  teams: {
    configured: boolean;
    webhook_url: string;
    missing: string[];
    setup: string[];
  };
  note?: string;
};

export const api = {
  health: () =>
    request<{
      status: string;
      llm: string;
      whatsapp_configured: boolean;
      teams_configured: boolean;
    }>("/api/v1/health"),
  platformsStatus: () => request<PlatformStatus>("/api/v1/platforms/status"),
  simulatePlatform: (platform: "whatsapp" | "teams", text: string) =>
    request<{
      platform: string;
      configured: boolean;
      delivery: { mode?: string };
      formatted: string;
      data: unknown;
    }>("/api/v1/platforms/simulate", {
      method: "POST",
      body: JSON.stringify({
        platform,
        text,
        author_name: platform === "whatsapp" ? "WhatsApp User" : "Teams User",
        author_id: `${platform}-demo`,
        conversation_id: `${platform}-demo-chat`,
      }),
    }),
  chat: (question: string, opts?: { userId?: string; mode?: string }) =>
    request<ChatResponse>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({
        question,
        user_id: opts?.userId || "web-demo",
        user_name: "Web Demo",
        platform: "web",
        mode: opts?.mode,
      }),
    }),
  catchup: (userId = "web-demo") =>
    request<ChatResponse>("/api/v1/catchup", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, user_name: "Web Demo" }),
    }),
  seedDemo: () =>
    request<{ seeded: number }>("/api/v1/ingest/seed-demo", { method: "POST" }),
  clearMock: () =>
    request<{ cleared: boolean; deleted_messages: number; deleted_chunks: number }>(
      "/api/v1/ingest/clear-mock",
      { method: "POST" }
    ),
  uploadTranscript: async (title: string, file: File) => {
    const form = new FormData();
    form.append("title", title);
    form.append("file", file);
    return request<{ meeting_id: string; status: string }>(
      "/api/v1/ingest/transcript",
      { method: "POST", body: form }
    );
  },
  ingestMessage: async (text: string, authorName: string) => {
    const form = new FormData();
    form.append("text", text);
    form.append("author_name", authorName);
    return request<{ ingested: number }>("/api/v1/ingest/message", {
      method: "POST",
      body: form,
    });
  },
};
