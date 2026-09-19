import { ChatPanel } from "@/components/ChatPanel";
import { IngestPanel } from "@/components/IngestPanel";
import { PlatformsPanel } from "@/components/PlatformsPanel";

export default function HomePage() {
  return (
    <main className="shell">
      <div className="atmosphere" aria-hidden />
      <header className="hero">
        <p className="brand">UniPods Memory AI</p>
        <h1>Don&apos;t just tell me what happened. Show me where it happened.</h1>
        <p className="lede">
          Community memory across WhatsApp, Teams meetings, and documents — with an
          evidence trail you can replay.
        </p>
      </header>

      <div className="workspace">
        <ChatPanel />
        <div className="side-stack">
          <PlatformsPanel />
          <IngestPanel />
        </div>
      </div>

      <footer className="footer">
        <span>Commands: /ask · /catchup · /decisions · /tasks · /meeting · /find</span>
        <span>WhatsApp + Teams webhooks → same RAG brain</span>
      </footer>
    </main>
  );
}
