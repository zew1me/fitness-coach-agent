"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import type { CSSProperties, JSX } from "react";

import {
  createUploadIntent,
  fetchBrowserToken,
  generatePlan,
  loadProfile,
  parseListInput,
  saveProfile,
  submitCheckIn
} from "../lib/coach-api";
import type {
  AthleteProfile,
  BrowserTokenResponse,
  CheckInResponse,
  GeneratedPlanResponse,
  PresignUploadResponse
} from "../lib/types";

const sectionStyle: CSSProperties = {
  background: "rgba(255, 255, 255, 0.88)",
  border: "1px solid rgba(15, 23, 42, 0.08)",
  borderRadius: "24px",
  boxShadow: "0 18px 44px rgba(15, 23, 42, 0.08)",
  padding: "1.5rem"
};

const inputStyle: CSSProperties = {
  border: "1px solid rgba(15, 23, 42, 0.16)",
  borderRadius: "12px",
  font: "inherit",
  padding: "0.75rem 0.9rem",
  width: "100%"
};

const labelStyle: CSSProperties = {
  display: "grid",
  gap: "0.45rem",
  fontSize: "0.95rem",
  fontWeight: 600
};

const buttonStyle: CSSProperties = {
  background: "#0f172a",
  border: "none",
  borderRadius: "999px",
  color: "#fff",
  cursor: "pointer",
  font: "inherit",
  fontWeight: 700,
  padding: "0.8rem 1.15rem"
};

function emptyProfile(userId: string): AthleteProfile {
  return {
    user_id: userId,
    constraints: [],
    goals: [],
    injuries_rehab: []
  };
}

type DashboardState = {
  token: BrowserTokenResponse | null;
  error: string | null;
  status: string | null;
};

function setOptionalNumber(
  profile: AthleteProfile,
  field: "age" | "cycling_ftp_watts" | "weight_kg",
  rawValue: string
): AthleteProfile {
  if (rawValue === "") {
    const nextProfile = { ...profile };
    delete nextProfile[field];
    return nextProfile;
  }

  return {
    ...profile,
    [field]: Number(rawValue)
  };
}

// eslint-disable-next-line complexity
export function CoachingDashboard(): JSX.Element {
  const [session, setSession] = useState<DashboardState>({
    token: null,
    error: null,
    status: "Checking browser session…"
  });
  const [profile, setProfile] = useState<AthleteProfile | null>(null);
  const [profileLists, setProfileLists] = useState({
    constraints: "",
    goals: "",
    injuries_rehab: ""
  });
  const [checkIn, setCheckIn] = useState({
    effective_date: "",
    image_count: 0,
    raw_text: ""
  });
  const [uploadForm, setUploadForm] = useState({
    content_length: 512000,
    content_type: "image/png",
    filename: "check-in.png",
    purpose: "check-in-image"
  });
  const [checkInResult, setCheckInResult] = useState<CheckInResponse | null>(null);
  const [planResult, setPlanResult] = useState<GeneratedPlanResponse | null>(null);
  const [uploadResult, setUploadResult] = useState<PresignUploadResponse | null>(null);
  const isAuthenticated = session.token !== null;
  const authenticatedToken = session.token;

  useEffect(() => {
    async function bootstrap(): Promise<void> {
      try {
        const token = await fetchBrowserToken();
        setSession({
          token,
          error: null,
          status: `Browser session connected for ${token.user_id}.`
        });
        setProfile((current) => current ?? emptyProfile(token.user_id));
      } catch (error) {
        setSession({
          token: null,
          error: error instanceof Error ? error.message : "Unable to connect browser session.",
          status: null
        });
      }
    }

    void bootstrap();
  }, []);

  async function handleLoadProfile(): Promise<void> {
    if (session.token === null) {
      return;
    }

    try {
      const loaded = await loadProfile(session.token.user_id);
      setProfile(loaded);
      setProfileLists({
        constraints: loaded.constraints.join("\n"),
        goals: loaded.goals.join("\n"),
        injuries_rehab: loaded.injuries_rehab.join("\n")
      });
      setSession((current) => ({ ...current, status: "Loaded athlete profile.", error: null }));
    } catch (error) {
      setProfile(emptyProfile(session.token.user_id));
      setSession((current) => ({
        ...current,
        status: "No saved profile yet. Fill out the form to create one.",
        error: error instanceof Error ? error.message : "Unable to load athlete profile."
      }));
    }
  }

  async function handleSaveProfile(): Promise<void> {
    if (profile === null) {
      return;
    }

    const payload: AthleteProfile = {
      ...profile,
      constraints: parseListInput(profileLists.constraints),
      goals: parseListInput(profileLists.goals),
      injuries_rehab: parseListInput(profileLists.injuries_rehab)
    };

    try {
      const saved = await saveProfile(payload);
      setProfile(saved);
      setSession((current) => ({ ...current, status: "Saved athlete profile.", error: null }));
    } catch (error) {
      setSession((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to save athlete profile."
      }));
    }
  }

  async function handleSubmitCheckIn(): Promise<void> {
    if (session.token === null) {
      return;
    }

    try {
      const result = await submitCheckIn({
        ...checkIn,
        user_id: session.token.user_id
      });
      setCheckInResult(result);
      setSession((current) => ({ ...current, status: "Saved athlete check-in.", error: null }));
    } catch (error) {
      setSession((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to save check-in."
      }));
    }
  }

  async function handleGeneratePlan(): Promise<void> {
    if (session.token === null) {
      return;
    }

    try {
      const result = await generatePlan({
        ...checkIn,
        user_id: session.token.user_id
      });
      setPlanResult(result);
      setSession((current) => ({ ...current, status: "Generated adaptive 14-day plan.", error: null }));
    } catch (error) {
      setSession((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to generate plan."
      }));
    }
  }

  async function handleCreateUploadIntent(): Promise<void> {
    try {
      const result = await createUploadIntent(uploadForm);
      setUploadResult(result);
      setSession((current) => ({ ...current, status: "Created upload intent.", error: null }));
    } catch (error) {
      setSession((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to create upload intent."
      }));
    }
  }

  return (
    <main style={{ display: "grid", gap: "1.5rem" }}>
      <section
        style={{
          background:
            "linear-gradient(135deg, rgba(14, 116, 144, 0.16), rgba(249, 115, 22, 0.14), rgba(255, 255, 255, 0.96))",
          borderRadius: "28px",
          padding: "2rem"
        }}
      >
        <p style={{ color: "#0f766e", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Athlete Workflow
        </p>
        <h1 style={{ fontSize: "clamp(2rem, 5vw, 3.5rem)", lineHeight: 1.05, margin: "0.5rem 0 1rem" }}>
          Run the full coaching loop in one place.
        </h1>
        <p style={{ fontSize: "1.05rem", maxWidth: "60ch" }}>
          Sign in with your browser session, load or save your athlete profile, submit a check-in,
          request an upload target, and generate the next adaptive 14-day plan without leaving the app.
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", marginTop: "1rem" }}>
          <Link href="/login?return_to=/" style={{ ...buttonStyle, display: "inline-flex", textDecoration: "none" }}>
            Sign in with magic link
          </Link>
          <Link href="/consent" style={{ ...buttonStyle, background: "#f97316", display: "inline-flex", textDecoration: "none" }}>
            Review OAuth consent
          </Link>
        </div>
      </section>

      <section style={sectionStyle}>
        <h2>Browser Session</h2>
        <p>{session.status ?? "No active browser session."}</p>
        {authenticatedToken !== null ? (
          <p>
            Authenticated user: <strong>{authenticatedToken.user_id}</strong>
          </p>
        ) : (
          <p>Sign in first so the app can mint a same-origin bearer token from the browser cookie.</p>
        )}
        {session.error !== null ? <p style={{ color: "#b91c1c" }}>{session.error}</p> : null}
      </section>

      {!isAuthenticated ? (
        <section style={sectionStyle}>
          <h2>Get Started</h2>
          <p>
            The coaching workspace unlocks after sign-in. Start with the magic-link flow, then come back
            here to load your athlete profile, submit a check-in, and generate the next plan.
          </p>
          <p style={{ marginBottom: 0 }}>
            If this deployment is missing the signed-in API route or browser auth config, use the login
            page first and then retry from the same browser session.
          </p>
        </section>
      ) : null}

      {isAuthenticated ? (
        <>
      <section style={sectionStyle}>
        <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.75rem", justifyContent: "space-between" }}>
          <div>
            <h2>Athlete Profile</h2>
            <p>Load the saved baseline or edit the profile details the planner uses for adaptation.</p>
          </div>
          <button onClick={() => void handleLoadProfile()} style={buttonStyle} type="button">
            Load profile
          </button>
        </div>
        {profile !== null ? (
          <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            <label style={labelStyle}>
              User ID
              <input onChange={(event) => setProfile({ ...profile, user_id: event.target.value })} style={inputStyle} value={profile.user_id} />
            </label>
            <label style={labelStyle}>
              Age
              <input
                onChange={(event) => setProfile(setOptionalNumber(profile, "age", event.target.value))}
                style={inputStyle}
                type="number"
                value={profile.age ?? ""}
              />
            </label>
            <label style={labelStyle}>
              FTP
              <input
                onChange={(event) =>
                  setProfile(setOptionalNumber(profile, "cycling_ftp_watts", event.target.value))
                }
                style={inputStyle}
                type="number"
                value={profile.cycling_ftp_watts ?? ""}
              />
            </label>
            <label style={labelStyle}>
              Weight (kg)
              <input
                onChange={(event) =>
                  setProfile(setOptionalNumber(profile, "weight_kg", event.target.value))
                }
                style={inputStyle}
                step="0.1"
                type="number"
                value={profile.weight_kg ?? ""}
              />
            </label>
            <label style={{ ...labelStyle, gridColumn: "1 / -1" }}>
              Goals
              <textarea
                onChange={(event) => setProfileLists((current) => ({ ...current, goals: event.target.value }))}
                rows={4}
                style={inputStyle}
                value={profileLists.goals}
              />
            </label>
            <label style={{ ...labelStyle, gridColumn: "1 / -1" }}>
              Constraints
              <textarea
                onChange={(event) =>
                  setProfileLists((current) => ({ ...current, constraints: event.target.value }))
                }
                rows={4}
                style={inputStyle}
                value={profileLists.constraints}
              />
            </label>
            <label style={{ ...labelStyle, gridColumn: "1 / -1" }}>
              Injuries / rehab
              <textarea
                onChange={(event) =>
                  setProfileLists((current) => ({ ...current, injuries_rehab: event.target.value }))
                }
                rows={3}
                style={inputStyle}
                value={profileLists.injuries_rehab}
              />
            </label>
            <label style={{ ...labelStyle, gridColumn: "1 / -1" }}>
              Notes
              <textarea
                onChange={(event) => setProfile({ ...profile, notes: event.target.value })}
                rows={4}
                style={inputStyle}
                value={profile.notes ?? ""}
              />
            </label>
            <button onClick={() => void handleSaveProfile()} style={buttonStyle} type="button">
              Save profile
            </button>
          </div>
        ) : null}
      </section>

      <section style={sectionStyle}>
        <h2>Daily Check-In</h2>
        <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
          <label style={{ ...labelStyle, gridColumn: "1 / -1" }}>
            Notes from today
            <textarea
              onChange={(event) => setCheckIn({ ...checkIn, raw_text: event.target.value })}
              rows={5}
              style={inputStyle}
              value={checkIn.raw_text}
            />
          </label>
          <label style={labelStyle}>
            Image count
            <input
              min="0"
              onChange={(event) =>
                setCheckIn({
                  ...checkIn,
                  image_count: event.target.value === "" ? 0 : Number(event.target.value)
                })
              }
              style={inputStyle}
              type="number"
              value={checkIn.image_count}
            />
          </label>
          <label style={labelStyle}>
            Effective date
            <input
              onChange={(event) => setCheckIn({ ...checkIn, effective_date: event.target.value })}
              style={inputStyle}
              type="date"
              value={checkIn.effective_date}
            />
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
            <button onClick={() => void handleSubmitCheckIn()} style={buttonStyle} type="button">
              Save check-in
            </button>
            <button onClick={() => void handleGeneratePlan()} style={{ ...buttonStyle, background: "#f97316" }} type="button">
              Generate plan
            </button>
          </div>
        </div>
        {checkInResult !== null ? (
          <pre style={{ background: "#0f172a", borderRadius: "16px", color: "#f8fafc", overflowX: "auto", padding: "1rem" }}>
            {JSON.stringify(checkInResult, null, 2)}
          </pre>
        ) : null}
      </section>

      <section style={sectionStyle}>
        <h2>Upload Intent</h2>
        <p>Request a signed upload target before sending screenshots or files directly to R2.</p>
        <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
          <label style={labelStyle}>
            Filename
            <input
              onChange={(event) => setUploadForm({ ...uploadForm, filename: event.target.value })}
              style={inputStyle}
              value={uploadForm.filename}
            />
          </label>
          <label style={labelStyle}>
            Content type
            <input
              onChange={(event) => setUploadForm({ ...uploadForm, content_type: event.target.value })}
              style={inputStyle}
              value={uploadForm.content_type}
            />
          </label>
          <label style={labelStyle}>
            Size (bytes)
            <input
              min="1"
              onChange={(event) =>
                setUploadForm({
                  ...uploadForm,
                  content_length: event.target.value === "" ? 1 : Number(event.target.value)
                })
              }
              style={inputStyle}
              type="number"
              value={uploadForm.content_length}
            />
          </label>
          <label style={labelStyle}>
            Purpose
            <input
              onChange={(event) => setUploadForm({ ...uploadForm, purpose: event.target.value })}
              style={inputStyle}
              value={uploadForm.purpose}
            />
          </label>
          <button onClick={() => void handleCreateUploadIntent()} style={buttonStyle} type="button">
            Create upload intent
          </button>
        </div>
        {uploadResult !== null ? (
          <pre style={{ background: "#0f172a", borderRadius: "16px", color: "#f8fafc", overflowX: "auto", padding: "1rem" }}>
            {JSON.stringify(uploadResult, null, 2)}
          </pre>
        ) : null}
      </section>

      <section style={sectionStyle}>
        <h2>Generated Plan</h2>
        {planResult === null ? (
          <p>Generate a plan after saving a check-in to see the adaptive 14-day output and prompt rationale.</p>
        ) : (
          <div style={{ display: "grid", gap: "1rem" }}>
            <div>
              <p><strong>Summary:</strong> {planResult.plan.summary}</p>
              <p><strong>Trend:</strong> {planResult.plan.trend}</p>
              <p><strong>Hours:</strong> {planResult.plan.hours}</p>
            </div>
            <div style={{ display: "grid", gap: "0.75rem" }}>
              {planResult.plan.days.map((day) => (
                <article key={day.day_index} style={{ border: "1px solid rgba(15, 23, 42, 0.08)", borderRadius: "16px", padding: "0.9rem 1rem" }}>
                  <strong>
                    Day {day.day_index}: {day.focus}
                  </strong>
                  <p style={{ marginBottom: 0 }}>{day.notes}</p>
                </article>
              ))}
            </div>
            <details>
              <summary>Prompt preview</summary>
              <pre style={{ whiteSpace: "pre-wrap" }}>{planResult.prompt_preview}</pre>
            </details>
          </div>
        )}
      </section>
        </>
      ) : null}
    </main>
  );
}
