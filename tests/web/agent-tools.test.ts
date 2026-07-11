import { describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { createCoachTools } from "../../lib/agent/coach-tools";
import { coachToolDefinitions } from "../../lib/agent/tools";

type RecordedRequest = { body: unknown; url: string };

function createRecordingFetch(responseBody: unknown): {
  fetchImpl: typeof fetch;
  requests: RecordedRequest[];
} {
  const requests: RecordedRequest[] = [];
  const fetchImpl = (
    url: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    requests.push({
      body: JSON.parse(String(init?.body)),
      url: String(url),
    });

    return Promise.resolve(
      new Response(JSON.stringify(responseBody), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
    );
  };

  return { fetchImpl, requests };
}

function assertTypedAdditionalProperties(schema: unknown, path = "$"): void {
  if (schema === null || typeof schema !== "object") {
    return;
  }

  if (Array.isArray(schema)) {
    schema.forEach((item, index) =>
      assertTypedAdditionalProperties(item, `${path}[${index}]`),
    );
    return;
  }

  const record = schema as Record<string, unknown>;
  const additionalProperties = record["additionalProperties"];
  if (
    additionalProperties !== undefined &&
    additionalProperties !== false &&
    additionalProperties !== true
  ) {
    expect(additionalProperties, `${path}.additionalProperties`).toEqual(
      expect.objectContaining({ type: expect.anything() }),
    );
  }

  for (const [key, value] of Object.entries(record)) {
    assertTypedAdditionalProperties(value, `${path}.${key}`);
  }
}

describe("coachToolDefinitions", () => {
  it("exposes the planned coaching tool surface", () => {
    expect(Object.keys(coachToolDefinitions)).toEqual([
      "get_athlete_context",
      "get_recent_activities",
      "get_active_plan",
      "get_compliance_summary",
      "find_plan_workout",
      "resolve_plan_workout",
      "save_activity_from_text",
      "process_uploaded_file",
      "save_recovery_data",
      "update_schedule",
      "update_goals",
      "update_athlete_profile",
      "calculate_zones",
      "estimate_thresholds",
      "generate_training_plan",
      "adjust_plan",
      "recalibrate_thresholds",
    ]);
  });

  it("validates a goal update payload with course details", () => {
    const parsed = coachToolDefinitions.update_goals.inputSchema.parse({
      action: "create",
      goal: {
        title: "Hill climb",
        goal_type: "event",
        sport: "running",
        target_date: "2026-07-01",
        course_distance_meters: 14000,
        course_elevation_gain_meters: 700,
        course_profile_notes: null,
        improvement_baseline_value: null,
        improvement_metric: null,
        improvement_target_value: null,
      },
    });

    expect(parsed.goal?.title).toBe("Hill climb");
    expect(parsed.goal?.course_elevation_gain_meters).toBe(700);
  });

  it("rejects unsupported goal_type values before tool execution", () => {
    const parsed = coachToolDefinitions.update_goals.inputSchema.safeParse({
      action: "create",
      goal: {
        title: "Half marathon",
        goal_type: "race",
        sport: "running",
        target_date: "2026-08-29",
        course_distance_meters: 21097,
        course_elevation_gain_meters: null,
        course_profile_notes: null,
        improvement_baseline_value: null,
        improvement_metric: null,
        improvement_target_value: null,
      },
    });

    expect(parsed.success).toBe(false);
  });

  it("accepts explicit training model policy for plan generation", () => {
    const parsed =
      coachToolDefinitions.generate_training_plan.inputSchema.parse({
        goal_id: "goal-1",
        training_model: "longevity",
      });

    expect(parsed).toEqual({
      goal_id: "goal-1",
      training_model: "longevity",
    });
  });

  it("accepts complete and abandon actions without a goal payload", () => {
    for (const action of ["complete", "abandon"] as const) {
      const parsed = coachToolDefinitions.update_goals.inputSchema.safeParse({
        action,
        goal_id: "goal-1",
      });

      expect(
        parsed.success,
        `${action} should be a valid update_goals call`,
      ).toBe(true);
    }
  });

  it("validates update_goals payloads by action", () => {
    const partialUpdate =
      coachToolDefinitions.update_goals.inputSchema.safeParse({
        action: "update",
        goal_id: "goal-1",
        goal: { title: "Sharpen for Leadville" },
      });
    const incompleteCreate =
      coachToolDefinitions.update_goals.inputSchema.safeParse({
        action: "create",
        goal: { title: "Sharpen for Leadville" },
      });
    const missingGoalId =
      coachToolDefinitions.update_goals.inputSchema.safeParse({
        action: "complete",
      });
    const terminalGoalPayload =
      coachToolDefinitions.update_goals.inputSchema.safeParse({
        action: "complete",
        goal_id: "goal-1",
        goal: { title: "Ignored title" },
      });

    expect(partialUpdate.success).toBe(true);
    expect(incompleteCreate.success).toBe(false);
    expect(missingGoalId.success).toBe(false);
    expect(terminalGoalPayload.success).toBe(false);
  });

  it("emits OpenAI-compatible schemas for all coach tools", () => {
    for (const [name, definition] of Object.entries(coachToolDefinitions)) {
      const jsonSchema = z.toJSONSchema(definition.inputSchema);

      expect(() =>
        assertTypedAdditionalProperties(jsonSchema, name),
      ).not.toThrow();
    }
  });

  it("validates profile onboarding, recovery, and schedule domain field names", () => {
    const fullProfileFields = {
      biological_sex: "not_specified" as const,
      birth_date: null,
      coaching_state: null,
      constraints: null,
      dietary_restrictions: ["vegetarian"],
      display_name: null,
      height_cm: null,
      hormone_status: "not_specified" as const,
      injuries_rehab: null,
      max_hr_bpm: null,
      notes: null,
      nutrition_notes: null,
      onboarding_collected: { nutrition: true },
      primary_sports: null,
      resting_hr_bpm: null,
      specialization_pct: null,
      weekly_available_hours: null,
      weight_kg: null,
    };
    expect(
      coachToolDefinitions.update_athlete_profile.inputSchema.parse({
        fields: fullProfileFields,
      }),
    ).toMatchObject({
      fields: {
        biological_sex: "not_specified",
        hormone_status: "not_specified",
        onboarding_collected: { nutrition: true },
      },
    });

    expect(() =>
      coachToolDefinitions.update_athlete_profile.inputSchema.parse({
        fields: { ...fullProfileFields, hormone_status: "not_provided" },
      }),
    ).toThrow();

    expect(
      coachToolDefinitions.save_recovery_data.inputSchema.parse({
        entries: [
          {
            body_battery: 55,
            hrv_ms: 48,
            log_date: "2026-05-30",
            notes: null,
            resting_hr_bpm: null,
            sleep_consistency_pct: null,
            sleep_duration_hours: 7.5,
            sleep_score: null,
            stress_score: 22,
            subjective_energy: 4,
          },
        ],
      }),
    ).toMatchObject({
      entries: [{ hrv_ms: 48, sleep_duration_hours: 7.5 }],
    });

    expect(
      coachToolDefinitions.update_schedule.inputSchema.parse({
        overrides: [
          {
            available: false,
            max_hours: 0,
            override_date: "2026-06-01",
            reason: "travel",
          },
        ],
        weekly_pattern: {
          monday: { available: true, max_hours: 1.5, notes: null },
        },
      }),
    ).toMatchObject({
      weekly_pattern: {
        monday: { available: true, max_hours: 1.5 },
      },
    });
  });

  it("routes athlete profile updates to the engine with nutrition fields", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    await (
      tools["update_athlete_profile"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      fields: {
        dietary_restrictions: ["vegetarian"],
        onboarding_collected: { nutrition: true },
      },
      user_id: "ignored-client-user",
    });

    expect(requests).toEqual([
      {
        body: {
          fields: {
            dietary_restrictions: ["vegetarian"],
            onboarding_collected: { nutrition: true },
          },
        },
        url: "https://coach.test/api/engine/update-athlete-profile",
      },
    ]);
  });

  it("executes save_activity_from_text by calling the engine text activity endpoint", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(
          JSON.stringify({
            activity: {
              activity_summary: {
                estimates: { estimated_duration_moving_s: 1140 },
              },
              id: "activity-1",
              source: "text_extract",
            },
            status: "saved",
          }),
          {
            headers: { "Content-Type": "application/json" },
            status: 200,
          },
        ),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["save_activity_from_text"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      activity_id: "activity-1",
      text: "Add RPE 9 and two gels.",
      user_id: "ignored-client-user",
    });

    expect(result).toEqual({
      activity: {
        activity_summary: {
          estimates: { estimated_duration_moving_s: 1140 },
        },
        id: "activity-1",
        source: "text_extract",
      },
      status: "saved",
    });
    expect(requests).toEqual([
      {
        body: {
          activity_id: "activity-1",
          text: "Add RPE 9 and two gels.",
        },
        url: "https://coach.test/api/engine/save-activity-from-text",
      },
    ]);
  });

  it.each([
    ["application/vnd.garmin.fit", "ride.fit"],
    ["application/gpx+xml", "run.gpx"],
    ["application/vnd.garmin.tcx+xml", "run.tcx"],
  ])(
    "redirects a %s upload stub passed to save_activity_from_text to process_uploaded_file",
    async (contentType, filename) => {
      const { fetchImpl, requests } = createRecordingFetch({
        activity: { duration_seconds: 5880, sport: "cycling" },
        status: "saved",
      });
      const tools = createCoachTools({
        accessToken: "token",
        baseUrl: "https://coach.test",
        fetchImpl,
      });

      const objectKey = `users/athlete-1/chat-attachment/2026/07/06/${filename}`;
      const publicUrl = `https://cdn.example.com/${filename}`;
      const stub =
        `Uploaded file: ${filename}\r\n` +
        `content_type=${contentType}\r\n` +
        `public_url=${publicUrl}\r\n` +
        `object_key=${objectKey}`;

      const result = await (
        tools["save_activity_from_text"] as {
          execute: (input: unknown) => Promise<unknown>;
        }
      ).execute({ text: stub });

      expect(result).toEqual({
        activity: { duration_seconds: 5880, sport: "cycling" },
        status: "saved",
      });
      expect(requests).toEqual([
        {
          body: {
            content_type: contentType,
            filename,
            object_key: objectKey,
            public_url: publicUrl,
          },
          url: "https://coach.test/api/engine/process-uploaded-file",
        },
      ]);
    },
  );

  it("does not redirect ordinary free-text activity descriptions", async () => {
    const { fetchImpl, requests } = createRecordingFetch({ status: "saved" });
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    await (
      tools["save_activity_from_text"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({ text: "Add RPE 9 and two gels." });

    expect(requests).toEqual([
      {
        body: { text: "Add RPE 9 and two gels." },
        url: "https://coach.test/api/engine/save-activity-from-text",
      },
    ]);
  });

  it("does not redirect a stub-shaped text for a non-activity content_type", async () => {
    const { fetchImpl, requests } = createRecordingFetch({ status: "saved" });
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const stub =
      "Uploaded file: notes.pdf\n" +
      "content_type=application/pdf\n" +
      "public_url=https://cdn.example.com/notes.pdf\n" +
      "object_key=users/athlete-1/chat-attachment/2026/07/06/notes.pdf";

    await (
      tools["save_activity_from_text"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({ text: stub });

    expect(requests).toEqual([
      {
        body: { text: stub },
        url: "https://coach.test/api/engine/save-activity-from-text",
      },
    ]);
  });

  it("routes get_compliance_summary to the engine compliance endpoint", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ compliance_pct: 66.7, status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["get_compliance_summary"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({ user_id: "ignored-client-user" });

    expect(result).toEqual({ compliance_pct: 66.7, status: "ok" });
    expect(requests).toEqual([
      {
        body: {},
        url: "https://coach.test/api/engine/get-compliance-summary",
      },
    ]);
  });

  it("routes resolve_plan_workout to the engine with coach provenance", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ workout: { status: "skipped" } }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["resolve_plan_workout"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      activity_id: null,
      outcome: "skipped",
      plan_workout_id: "workout-1",
      user_id: "ignored-client-user",
    });

    expect(result).toEqual({ workout: { status: "skipped" } });
    expect(requests).toEqual([
      {
        body: {
          activity_id: null,
          outcome: "skipped",
          plan_workout_id: "workout-1",
          source: "coach",
        },
        url: "https://coach.test/api/engine/resolve-plan-workout",
      },
    ]);
  });

  it("routes adjust_plan to the engine adjust endpoint and strips client user_id", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(
          JSON.stringify({ plan_id: "plan-1", status: "adjusted" }),
          {
            headers: { "Content-Type": "application/json" },
            status: 200,
          },
        ),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["adjust_plan"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      plan_id: "plan-1",
      reason: "Ease off next week",
      user_id: "ignored-client-user",
    });

    // The mapping must resolve — no pending_implementation fallthrough.
    expect(result).toEqual({ plan_id: "plan-1", status: "adjusted" });
    expect(requests).toEqual([
      {
        body: { plan_id: "plan-1", reason: "Ease off next week" },
        url: "https://coach.test/api/engine/adjust-plan",
      },
    ]);
  });

  it("routes recalibrate_thresholds to the engine recalibrate endpoint", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(
          JSON.stringify({
            results: [
              {
                candidate_id: "candidate-1",
                confidence: "high",
                explanation: "…",
                sport: "running",
                status: "candidate_queued",
              },
            ],
          }),
          {
            headers: { "Content-Type": "application/json" },
            status: 200,
          },
        ),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["recalibrate_thresholds"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({});

    // The mapping must resolve — no pending_implementation fallthrough.
    expect(result).not.toEqual({
      input: {},
      status: "pending_implementation",
      tool: "recalibrate_thresholds",
    });
    expect(requests).toEqual([
      {
        body: {},
        url: "https://coach.test/api/engine/recalibrate-thresholds",
      },
    ]);
  });

  it("routes save_recovery_data to the engine and strips any client-sent user_id", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ count: 1, saved: [{ id: "rec-1" }] }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["save_recovery_data"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      entries: [
        {
          hrv_ms: 48,
          log_date: "2026-05-30",
          sleep_duration_hours: 7.5,
        },
      ],
      user_id: "ignored-client-user",
    });

    expect(result).toEqual({ count: 1, saved: [{ id: "rec-1" }] });
    expect(requests).toEqual([
      {
        body: {
          entries: [
            {
              hrv_ms: 48,
              log_date: "2026-05-30",
              sleep_duration_hours: 7.5,
            },
          ],
        },
        url: "https://coach.test/api/engine/save-recovery-data",
      },
    ]);
  });

  it("routes goal updates to the engine and strips any client-sent user_id", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ id: "goal-new", status: "active" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const result = await (
      tools["update_goals"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      action: "create",
      goal: {
        goal_type: "event",
        title: "Leadville 100",
        sport: "cycling",
      },
      user_id: "ignored-client-user",
    });
    const completeResult = await (
      tools["update_goals"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      action: "complete",
      goal_id: "goal-new",
      user_id: "ignored-client-user",
    });

    expect(result).toEqual({ id: "goal-new", status: "active" });
    expect(completeResult).toEqual({ id: "goal-new", status: "active" });
    expect(requests).toEqual([
      {
        body: {
          action: "create",
          goal: {
            goal_type: "event",
            title: "Leadville 100",
            sport: "cycling",
          },
        },
        url: "https://coach.test/api/engine/update-goals",
      },
      {
        body: {
          action: "complete",
          goal_id: "goal-new",
        },
        url: "https://coach.test/api/engine/update-goals",
      },
    ]);
  });

  it("keeps long activity text extraction requests alive past 30 seconds", async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    let resolveResponse:
      | ((response: Response | PromiseLike<Response>) => void)
      | undefined;
    const fetchImpl = (
      _url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requestSignal = init?.signal ?? undefined;
      return new Promise<Response>((resolve) => {
        resolveResponse = resolve;
      });
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    try {
      const resultPromise = (
        tools["save_activity_from_text"] as {
          execute: (input: unknown) => Promise<unknown>;
        }
      ).execute({ text: "Hard ride with two gels." });

      await vi.advanceTimersByTimeAsync(30_001);
      expect(requestSignal?.aborted).toBe(false);

      resolveResponse?.(
        new Response(JSON.stringify({ status: "saved" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
      await expect(resultPromise).resolves.toEqual({ status: "saved" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("forwards extra internal headers to engine tool calls", async () => {
    const requests: Array<{ headers: Headers; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        headers: new Headers(init?.headers),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      extraHeaders: { "x-vercel-protection-bypass": "preview-bypass" },
      fetchImpl,
    });

    await (
      tools["get_recent_activities"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({ limit: 1 });

    expect(requests).toHaveLength(1);
    const request = requests[0];
    if (request === undefined) {
      throw new Error("Expected one engine request.");
    }
    expect(request.url).toBe(
      "https://coach.test/api/engine/get-recent-activities",
    );
    expect(request.headers.get("authorization")).toBe("Bearer token");
    expect(request.headers.get("content-type")).toBe("application/json");
    expect(request.headers.get("x-vercel-protection-bypass")).toBe(
      "preview-bypass",
    );
  });
});
