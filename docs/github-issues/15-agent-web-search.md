# Add web search tool to the coaching agent

## Summary

The coaching agent sometimes needs to look up exercise science research, specific
training protocols, physiological concepts, or race/event information. Without a search
tool, it falls back to training-cutoff knowledge. Adding a constrained web search tool
lets the agent fetch authoritative, up-to-date information when the user asks about
specific topics.

## Desired behavior

When the user asks something like:
- "What does research say about polarized training for masters athletes?"
- "What are the specific criteria for Boston Marathon qualification?"
- "How does altitude affect VO2max adaptation timelines?"

…the agent can call a `web_search` tool to retrieve relevant snippets before answering.

## Implementation notes

- Use the [Tavily](https://tavily.com/) search API (research-optimized, returns clean
  snippets). Requires `TAVILY_API_KEY` env var.
- Tool definition in `lib/agent/tools.ts`: `web_search` with input `{ query: string,
  max_results?: number }`.
- Tool execution in `lib/agent/coach-tools.ts`: POST to `https://api.tavily.com/search`
  with `search_depth: "basic"`, `include_answer: true`, `max_results` (default 5).
- Constrain the tool description to exercise science, training, physiology, and
  sports nutrition — the agent should not use it for general browsing.
- Add `TAVILY_API_KEY` to `.env.example` (optional; tool returns empty results gracefully
  if key is absent).

## Acceptance criteria

- [ ] `web_search` tool appears in `coachToolDefinitions`
- [ ] Tool routes to Tavily search API in `coach-tools.ts`
- [ ] Returns `{ results: [{ title, url, content }], answer? }` shape
- [ ] Gracefully returns `{ results: [] }` when `TAVILY_API_KEY` is absent
- [ ] Unit test verifies the tool calls the correct URL with correct headers
- [ ] `bun run check` passes
