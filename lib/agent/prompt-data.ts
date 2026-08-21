function escapedJson(value: unknown): string {
  return JSON.stringify(value)
    .replaceAll("```", "\\`\\`\\`")
    .replaceAll("</data-block>", "<\\/data-block>");
}

export function formatDataBlock(name: string, value: unknown): string {
  return [
    `<data-block name="${name}">`,
    "```json",
    escapedJson(value),
    "```",
    "</data-block>",
  ].join("\n");
}
