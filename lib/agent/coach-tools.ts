import { type ToolSet } from "ai";

import { coachToolDefinitions } from "./tools";

export type CoachToolContext = {
  accessToken: string;
  baseUrl: string;
  fetchImpl?: typeof fetch;
  userId: string;
};

async function postEngine<TInput extends object>(
  context: CoachToolContext,
  path: string,
  input: TInput
): Promise<unknown> {
  const response = await (context.fetchImpl ?? fetch)(`${context.baseUrl}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${context.accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(input),
  });

  if (!response.ok) {
    throw new Error(`Engine request failed for ${path}.`);
  }

  return response.json();
}

async function getAthleteSummary(context: CoachToolContext): Promise<Record<string, unknown>> {
  const summary = await postEngine(context, "/api/engine/get-athlete-summary", {
    user_id: context.userId,
  });

  return summary !== null && typeof summary === "object" && !Array.isArray(summary)
    ? (summary as Record<string, unknown>)
    : {};
}

function engineInput(input: unknown): Record<string, unknown> {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    return {};
  }

  return Object.fromEntries(Object.entries(input as Record<string, unknown>).filter(([key]) => key !== "user_id"));
}

function stringField(input: Record<string, unknown>, key: string): string | null {
  const value = input[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isActivityFile(contentType: string | null, filename: string | null): boolean {
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

function processUploadedFile(input: unknown, context: CoachToolContext): unknown {
  const payload = engineInput(input);
  const contentType = stringField(payload, "content_type");
  const filename = stringField(payload, "filename");
  const objectKey = stringField(payload, "object_key");
  const publicUrl = stringField(payload, "public_url");

  if (contentType?.startsWith("image/") && publicUrl !== null) {
    return postEngine(context, "/api/engine/analyze-screenshot", {
      image_url: publicUrl,
    });
  }

  if (isActivityFile(contentType, filename) && contentType !== null && filename !== null && objectKey !== null) {
    return postEngine(context, "/api/engine/process-uploaded-file", {
      content_type: contentType,
      filename,
      object_key: objectKey,
      public_url: publicUrl,
      user_id: context.userId,
    });
  }

  return null;
}

function executeDeterministicEngineTool(
  name: string,
  input: unknown,
  context: CoachToolContext
): unknown {
  if (name === "calculate_zones") {
    return postEngine(context, "/api/engine/calculate-zones", engineInput(input));
  }

  if (name === "estimate_thresholds") {
    return postEngine(context, "/api/engine/estimate-thresholds", engineInput(input));
  }

  if (name === "generate_training_plan") {
    return postEngine(context, "/api/engine/generate-plan-structure", {
      ...engineInput(input),
      user_id: context.userId,
    });
  }

  return null;
}

function executeCoachTool(name: string, input: unknown, context: CoachToolContext): unknown {
  if (name === "get_athlete_context") {
    return getAthleteSummary(context);
  }

  if (name === "get_active_plan") {
    return getAthleteSummary(context).then((summary) => ({
      active_plan: summary["active_plan"] ?? null,
    }));
  }

  if (name === "get_recent_activities") {
    return postEngine(context, "/api/engine/get-recent-activities", {
      ...engineInput(input),
      user_id: context.userId,
    });
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

export function createCoachTools(context: CoachToolContext): ToolSet {
  return Object.fromEntries(
    Object.entries(coachToolDefinitions).map(([name, definition]) => [
      name,
      {
        description: definition.description,
        inputSchema: definition.inputSchema,
        execute: (input: unknown): unknown => executeCoachTool(name, input, context),
      }
    ])
  ) as ToolSet;
}
