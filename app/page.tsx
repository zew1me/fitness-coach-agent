import type { JSX } from "react";

import { StatusCard } from "../components/status-card";

export default function HomePage(): JSX.Element {
  return (
    <main>
      <h1>Exercise Training Plan GPT</h1>
      <p>
        A ChatGPT-first coaching app scaffold for adaptive cyclocross and endurance training
        plans.
      </p>
      <div>
        <StatusCard
          title="Auth"
          body="Supabase handles the human login session. ChatGPT connects through our own OAuth flow."
        />
        <StatusCard
          title="Planning"
          body="Python composes a 14-day plan from the athlete baseline, recent signals, and constraints."
        />
        <StatusCard
          title="Uploads"
          body="Screenshots and future raw files are written to Cloudflare R2 and summarized in Postgres."
        />
      </div>
    </main>
  );
}
