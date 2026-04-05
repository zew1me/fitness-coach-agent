import type { JSX } from "react";

import { CoachingDashboard } from "../../components/coaching-dashboard";

export default function ProfilePage(): JSX.Element {
  return (
    <div className="stack">
      <section className="panel">
        <h1>Athlete Profile</h1>
        <p>Use the same end-to-end dashboard here, with the profile section surfaced first.</p>
      </section>
      <CoachingDashboard />
    </div>
  );
}
