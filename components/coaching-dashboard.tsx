"use client";

import Link from "next/link";
import React from "react";
import { useEffect, useState } from "react";
import type { JSX } from "react";

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

import { BrandMarkGallery } from "./brand-mark-gallery";

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
    <main className="dashboard">
      <section className="hero-card">
        <p className="eyebrow">Endurance coach cockpit</p>
        <h1 className="hero-title">Train across seasons, not just a single sport.</h1>
        <p className="hero-copy">
          Sign in with your browser session, load or save your athlete profile, submit a check-in,
          request an upload target, and generate the next adaptive 14-day plan without leaving the app.
        </p>
        <div className="hero-actions">
          <Link className="button-link" href="/login?return_to=/">
            Sign in with magic link
          </Link>
          <Link className="button-link button-action" href="/consent">
            Review OAuth consent
          </Link>
        </div>
        <div className="meta-card-grid">
          <div className="meta-card">
            <strong>Light theme</strong>
            <span>Off-white and low-fatigue for long planning and chat sessions.</span>
          </div>
          <div className="meta-card">
            <strong>Dark theme</strong>
            <span>Deep navy for early mornings and late-night training check-ins.</span>
          </div>
          <div className="meta-card">
            <strong>Accent discipline</strong>
            <span>Teal for guidance, orange for effort and decisive actions.</span>
          </div>
        </div>
      </section>

      <BrandMarkGallery />

      <section className="panel">
        <h2>Browser Session</h2>
        <p>{session.status ?? "No active browser session."}</p>
        {session.token !== null ? (
          <p>
            Authenticated user: <strong>{session.token.user_id}</strong>
          </p>
        ) : (
          <p>Sign in first so the app can mint a same-origin bearer token from the browser cookie.</p>
        )}
        {session.error !== null ? <p className="error">{session.error}</p> : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Athlete Profile</h2>
            <p>Load the saved baseline or edit the profile details the planner uses for adaptation.</p>
          </div>
          <button className="button" onClick={() => void handleLoadProfile()} type="button">
            Load profile
          </button>
        </div>
        {profile !== null ? (
          <div className="grid">
            <label className="field">
              User ID
              <input className="input" onChange={(event) => setProfile({ ...profile, user_id: event.target.value })} value={profile.user_id} />
            </label>
            <label className="field">
              Age
              <input
                className="input"
                onChange={(event) => setProfile(setOptionalNumber(profile, "age", event.target.value))}
                type="number"
                value={profile.age ?? ""}
              />
            </label>
            <label className="field">
              FTP
              <input
                className="input"
                onChange={(event) =>
                  setProfile(setOptionalNumber(profile, "cycling_ftp_watts", event.target.value))
                }
                type="number"
                value={profile.cycling_ftp_watts ?? ""}
              />
            </label>
            <label className="field">
              Weight (kg)
              <input
                className="input"
                onChange={(event) =>
                  setProfile(setOptionalNumber(profile, "weight_kg", event.target.value))
                }
                step="0.1"
                type="number"
                value={profile.weight_kg ?? ""}
              />
            </label>
            <label className="field field-full">
              Goals
              <textarea
                className="textarea"
                onChange={(event) => setProfileLists((current) => ({ ...current, goals: event.target.value }))}
                rows={4}
                value={profileLists.goals}
              />
            </label>
            <label className="field field-full">
              Constraints
              <textarea
                className="textarea"
                onChange={(event) =>
                  setProfileLists((current) => ({ ...current, constraints: event.target.value }))
                }
                rows={4}
                value={profileLists.constraints}
              />
            </label>
            <label className="field field-full">
              Injuries / rehab
              <textarea
                className="textarea"
                onChange={(event) =>
                  setProfileLists((current) => ({ ...current, injuries_rehab: event.target.value }))
                }
                rows={3}
                value={profileLists.injuries_rehab}
              />
            </label>
            <label className="field field-full">
              Notes
              <textarea
                className="textarea"
                onChange={(event) => setProfile({ ...profile, notes: event.target.value })}
                rows={4}
                value={profile.notes ?? ""}
              />
            </label>
            <button className="button button-secondary" onClick={() => void handleSaveProfile()} type="button">
              Save profile
            </button>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h2>Daily Check-In</h2>
        <div className="grid">
          <label className="field field-full">
            Notes from today
            <textarea
              className="textarea"
              onChange={(event) => setCheckIn({ ...checkIn, raw_text: event.target.value })}
              rows={5}
              value={checkIn.raw_text}
            />
          </label>
          <label className="field">
            Image count
            <input
              className="input"
              min="0"
              onChange={(event) =>
                setCheckIn({
                  ...checkIn,
                  image_count: event.target.value === "" ? 0 : Number(event.target.value)
                })
              }
              type="number"
              value={checkIn.image_count}
            />
          </label>
          <label className="field">
            Effective date
            <input
              className="input"
              onChange={(event) => setCheckIn({ ...checkIn, effective_date: event.target.value })}
              type="date"
              value={checkIn.effective_date}
            />
          </label>
          <div className="button-row">
            <button className="button button-secondary" onClick={() => void handleSubmitCheckIn()} type="button">
              Save check-in
            </button>
            <button className="button button-action" onClick={() => void handleGeneratePlan()} type="button">
              Generate plan
            </button>
          </div>
        </div>
        {checkInResult !== null ? (
          <pre className="pre">{JSON.stringify(checkInResult, null, 2)}</pre>
        ) : null}
      </section>

      <section className="panel">
        <h2>Upload Intent</h2>
        <p>Request a signed upload target before sending screenshots or files directly to R2.</p>
        <div className="grid">
          <label className="field">
            Filename
            <input
              className="input"
              onChange={(event) => setUploadForm({ ...uploadForm, filename: event.target.value })}
              value={uploadForm.filename}
            />
          </label>
          <label className="field">
            Content type
            <input
              className="input"
              onChange={(event) => setUploadForm({ ...uploadForm, content_type: event.target.value })}
              value={uploadForm.content_type}
            />
          </label>
          <label className="field">
            Size (bytes)
            <input
              className="input"
              min="1"
              onChange={(event) =>
                setUploadForm({
                  ...uploadForm,
                  content_length: event.target.value === "" ? 1 : Number(event.target.value)
                })
              }
              type="number"
              value={uploadForm.content_length}
            />
          </label>
          <label className="field">
            Purpose
            <input
              className="input"
              onChange={(event) => setUploadForm({ ...uploadForm, purpose: event.target.value })}
              value={uploadForm.purpose}
            />
          </label>
          <button className="button" onClick={() => void handleCreateUploadIntent()} type="button">
            Create upload intent
          </button>
        </div>
        {uploadResult !== null ? <pre className="pre">{JSON.stringify(uploadResult, null, 2)}</pre> : null}
      </section>

      <section className="panel">
        <h2>Generated Plan</h2>
        {planResult === null ? (
          <p>Generate a plan after saving a check-in to see the adaptive 14-day output and prompt rationale.</p>
        ) : (
          <div className="stack">
            <div>
              <p><strong>Summary:</strong> {planResult.plan.summary}</p>
              <p><strong>Trend:</strong> {planResult.plan.trend}</p>
              <p><strong>Hours:</strong> {planResult.plan.hours}</p>
            </div>
            <div className="plan-days">
              {planResult.plan.days.map((day) => (
                <article className="plan-day" key={day.day_index}>
                  <strong>
                    Day {day.day_index}: {day.focus}
                  </strong>
                  <p>{day.notes}</p>
                </article>
              ))}
            </div>
            <details>
              <summary>Prompt preview</summary>
              <pre className="pre" style={{ whiteSpace: "pre-wrap" }}>{planResult.prompt_preview}</pre>
            </details>
          </div>
        )}
      </section>
    </main>
  );
}
