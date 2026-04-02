import type { JSX } from "react";

import { CoachingDashboard } from "../../components/coaching-dashboard";

export default function ProfilePage(): JSX.Element {
  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div>
        <h1>Athlete Profile</h1>
        <p>Use the same end-to-end dashboard here, with the profile section surfaced first.</p>
      </div>
      <CoachingDashboard />
    </div>
  );
}
