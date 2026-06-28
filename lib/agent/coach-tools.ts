import { tool, type Tool } from "@openai/agents";
import { type ToolSet } from "ai";

import { coachingMemoryToolSchema } from "./coaching-memory";
import type { SupabaseAgentSession } from "./supabase-agent-session";
import { coachToolDefinitions } from "./tools";

export type CoachToolContext = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetchImpl?: typeof fetch;
  modelSession?: SupabaseAgentSession;
};

const ENGINE_TIMEOUT_MS = 65_000;

async function postEngine<TInput extends object>(
  context: CoachToolContext,
  path: string,
  input: TInput,
): Promise<unknown> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ENGINE_TIMEOUT_MS);

  try {
    const response = await (context.fetchImpl ?? fetch)(
      `${context.baseUrl}${path}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${context.accessToken}`,
          "Content-Type": "application/json",
          ...(context.extraHeaders ?? {}),
        },
        body: JSON.stringify(input),
        signal: controller.signal,
      },
    );

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(
        `Engine request failed for ${path}: HTTP ${response.status} ${response.statusText}${body ? `. ${body}` : ""}`,
      );
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

async function getAthleteSummary(
  context: CoachToolContext,
): Promise<Record<string, unknown>> {
  const summary = await postEngine(
    context,
    "/api/engine/get-athlete-summary",
    {},
  );

  return summary !== null &&
    typeof summary === "object" &&
    !Array.isArray(summary)
    ? (summary as Record<string, unknown>)
    : {};
}

function engineInput(input: unknown): Record<string, unknown> {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    return {};
  }

  // Strip any user_id the LLM might include — the backend derives user identity from the bearer token.
  return Object.fromEntries(
    Object.entries(input as Record<string, unknown>).filter(
      ([key]) => key !== "user_id",
    ),
  );
}

function stringField(
  input: Record<string, unknown>,
  key: string,
): string | null {
  const value = input[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isActivityFile(
  contentType: string | null,
  filename: string | null,
): boolean {
  const lowerFilename = filename?.toLowerCase() ?? "";
  return (
    contentType === "application/gpx+xml" ||
    contentType === "application/vnd.garmin.fit" ||
    contentType === "application/vnd.garmin.tcx+xml" ||
    lowerFilename.endsWith(".gpx") ||
    lowerFilename.endsWith(".fit") ||
    lowerFilename.endsWith(".tcx")
  );
}

function inferActivityContentType(filename: string | null): string | null {
  const lowerFilename = filename?.toLowerCase() ?? "";

  if (lowerFilename.endsWith(".gpx")) {
    return "application/gpx+xml";
  }

  if (lowerFilename.endsWith(".fit")) {
    return "application/vnd.garmin.fit";
  }

  if (lowerFilename.endsWith(".tcx")) {
    return "application/vnd.garmin.tcx+xml";
  }

  return null;
}

function isGenericOrInvalidContentType(contentType: string | null): boolean {
  if (contentType === null) {
    return true;
  }

  const normalizedContentType = contentType.trim().toLowerCase();

  return (
    normalizedContentType.length === 0 ||
    normalizedContentType === "application/octet-stream" ||
    !normalizedContentType.includes("/")
  );
}

function resolveContentType(
  contentType: string | null,
  filename: string | null,
): string | null {
  if (contentType !== null && !isGenericOrInvalidContentType(contentType)) {
    return contentType;
  }

  return inferActivityContentType(filename) ?? contentType;
}

function shouldAnalyzeScreenshot(
  contentType: string | null,
  publicUrl: string | null,
): boolean {
  return publicUrl !== null && contentType?.startsWith("image/") === true;
}

function isValidActivityUpload(
  contentType: string | null,
  filename: string | null,
  objectKey: string | null,
): boolean {
  return (
    contentType !== null &&
    filename !== null &&
    objectKey !== null &&
    isActivityFile(contentType, filename)
  );
}

function processUploadedFile(
  input: unknown,
  context: CoachToolContext,
): unknown {
  const payload = engineInput(input);
  const contentType = stringField(payload, "content_type");
  const filename = stringField(payload, "filename");
  const objectKey = stringField(payload, "object_key");
  const publicUrl = stringField(payload, "public_url");

  const resolvedContentType = resolveContentType(contentType, filename);

  if (shouldAnalyzeScreenshot(resolvedContentType, publicUrl)) {
    return postEngine(context, "/api/engine/analyze-screenshot", {
      image_url: publicUrl,
    });
  }

  if (isValidActivityUpload(resolvedContentType, filename, objectKey)) {
    const payload = {
      content_type: resolvedContentType,
      filename,
      object_key: objectKey,
      public_url: publicUrl,
    };

    return postEngine(context, "/api/engine/process-uploaded-file", payload);
  }

  return null;
}

function updateAthleteProfile(
  input: unknown,
  context: CoachToolContext,
): unknown {
  const payload = engineInput(input);
  const inputFields = payload["fields"];
  const fields =
    inputFields !== null &&
    typeof inputFields === "object" &&
    !Array.isArray(inputFields)
      ? (inputFields as Record<string, unknown>)
      : payload;

  return postEngine(context, "/api/engine/update-athlete-profile", {
    fields,
  });
}

function executeDeterministicEngineTool(
  name: string,
  input: unknown,
  context: CoachToolContext,
): unknown {
  const paths: Record<string, string> = {
    calculate_zones: "/api/engine/calculate-zones",
    estimate_thresholds: "/api/engine/estimate-thresholds",
    generate_training_plan: "/api/engine/generate-plan-structure",
    update_goals: "/api/engine/update-goals",
    update_schedule: "/api/engine/update-schedule",
  };
  const path = paths[name];
  if (path) {
    return postEngine(context, path, engineInput(input));
  }

  return null;
}

export function executeCoachTool(
  name: string,
  input: unknown,
  context: CoachToolContext,
): unknown {
  if (name === "get_athlete_context") {
    return getAthleteSummary(context);
  }

  if (name === "get_active_plan") {
    return getAthleteSummary(context).then((summary) => ({
      active_plan: summary["active_plan"] ?? null,
    }));
  }

  if (name === "get_recent_activities") {
    return postEngine(
      context,
      "/api/engine/get-recent-activities",
      engineInput(input),
    );
  }

  if (name === "save_activity_from_text") {
    return postEngine(
      context,
      "/api/engine/save-activity-from-text",
      engineInput(input),
    );
  }

  if (name === "update_athlete_profile") {
    return updateAthleteProfile(input, context);
  }

  if (name === "process_uploaded_file") {
    const result = processUploadedFile(input, context);
    if (result !== null) {
      return result;
    }
  }

  const engineResult = executeDeterministicEngineTool(name, input, context);
  if (engineResult !== null) {
    return engineResult;
  }

  return {
    input,
    status: "pending_implementation",
    tool: name,
  };
}

export type CoachAgentRunContext = {
  toolCalled: boolean;
};

export function createAgentCoachTools(
  context: CoachToolContext,
): Tool<CoachAgentRunContext>[] {
  const tools: Tool<CoachAgentRunContext>[] = Object.entries(
    coachToolDefinitions,
  ).map(([name, definition]) =>
    tool<typeof definition.inputSchema, CoachAgentRunContext>({
      name,
      description: definition.description,
      parameters: definition.inputSchema,
      isEnabled: ({ runContext }) => !runContext.context.toolCalled,
      execute: (input: unknown) => executeCoachTool(name, input, context),
    }),
  );
  if (context.modelSession) {
    tools.push(
      tool<typeof coachingMemoryToolSchema, CoachAgentRunContext>({
        name: "update_coaching_memory",
        description:
          "Maintain non-authoritative coaching commitments, preferences, follow-ups, insights, and outcomes. Never duplicate profile, goal, schedule, plan, threshold, load, or recovery data.",
        parameters: coachingMemoryToolSchema,
        isEnabled: ({ runContext }) => !runContext.context.toolCalled,
        execute: async (input) => {
          await context.modelSession?.updateCoachingMemory(input.operation);
          return { status: "updated" };
        },
      }),
    );
  }
  return tools;
}

export function createCoachTools(context: CoachToolContext): ToolSet {
  return Object.fromEntries(
    Object.entries(coachToolDefinitions).map(([name, definition]) => [
      name,
      {
        description: definition.description,
        inputSchema: definition.inputSchema,
        execute: (input: unknown): unknown =>
          executeCoachTool(name, input, context),
      },
    ]),
  ) as ToolSet;
}
