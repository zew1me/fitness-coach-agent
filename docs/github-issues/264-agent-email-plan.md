# Issue 264: Agent Email for Preview Testing

## Overview

Give coding agents disposable email inboxes so they can complete OTP, magic-link,
and end-to-end preview flows without a human copying messages out of a mailbox.
The system uses the existing stack: Mailgun receives inbound email, Vercel
accepts Mailgun POSTs, Supabase stores short-lived rows, and local agent tooling
reads unconsumed rows with a service role key.

## Architecture Decisions

- Store inbound message metadata and bodies in Postgres, not object storage.
  OTP and magic-link messages are small, queryable, and should expire quickly.
- Verify Mailgun webhook signatures at the Vercel route boundary before any
  database insert.
- Keep the first deployed route broad and metadata-rich. Dynamic generated
  addresses can share one endpoint while later tooling filters by recipient.
- Defer Mailgun route provisioning and local reader tooling to follow-up PRs so
  the storage and ingestion contract can be reviewed independently.

## Task List

### Task 1: Mailgun Inbound Receiver and Storage

**Description:** Add the Supabase table and Vercel route that receives signed
Mailgun inbound POSTs and stores normalized message fields plus raw metadata.

**Acceptance criteria:**

- [x] `agent_emails` exists with recipient, sender, subject, body, raw payload,
      and consumption metadata.
- [x] `POST /api/inbound/mailgun` rejects invalid signatures.
- [x] Valid signed payloads insert one row and return `{ ok: true }`.

**Verification:**

- [x] `bun run test tests/web/inbound-mailgun-route.test.ts`

**Dependencies:** None.

**Files likely touched:**

- `app/api/inbound/mailgun/route.ts`
- `supabase/migrations/*_agent_emails.sql`
- `tests/web/inbound-mailgun-route.test.ts`

**Estimated scope:** Medium.

### Task 2: Agent Mail Reader CLI

**Description:** Add a local CLI that searches unconsumed email for a recipient,
optionally filters by content, prints a machine-readable message payload, and can
mark the row consumed.

**Acceptance criteria:**

- [ ] CLI reads from Supabase using server-only environment variables.
- [ ] CLI supports recipient and optional text search.
- [ ] CLI can mark a selected email consumed after display.

**Verification:**

- [ ] Unit tests cover query filtering and consumed update behavior.
- [ ] Manual local run against a seeded row returns the expected JSON.

**Dependencies:** Task 1.

**Files likely touched:**

- `scripts/agent-mail.ts`
- `tests/web/agent-mail.test.ts`

**Estimated scope:** Medium.

### Task 3: Mailgun Route Provisioning

**Description:** Document or automate the Mailgun receiving route that forwards
agent-domain email to the Vercel endpoint.

**Acceptance criteria:**

- [ ] Route forwards `*@agents.<domain>` to `/api/inbound/mailgun`.
- [ ] Setup records the signing key as a Vercel environment variable.
- [ ] Dynamic address naming convention is documented.

**Verification:**

- [ ] Preview route receives a Mailgun test payload.
- [ ] A generated recipient address can be queried from Supabase.

**Dependencies:** Task 1.

**Files likely touched:**

- `scripts/bootstrap/*`
- `docs/*`

**Estimated scope:** Medium.

### Task 4: Agent Skill for Reading Test Email

**Description:** Create a reusable local skill/instruction for agents to retrieve
OTP or magic-link messages during preview and browser tests.

**Acceptance criteria:**

- [ ] Skill explains when to use generated addresses.
- [ ] Skill uses the reader CLI instead of asking the user for OTPs.
- [ ] Skill includes a safe fallback when no matching email is found.

**Verification:**

- [ ] Skill dry-run against a seeded test email succeeds.

**Dependencies:** Task 2.

**Files likely touched:**

- Codex/Claude skill files outside this app repo, or docs that feed those skills.

**Estimated scope:** Small.

## Checkpoints

- After Task 1: the ingestion contract is reviewable and deployable without
  Mailgun account changes.
- After Tasks 2-3: a real Mailgun inbound test can flow into Supabase and out
  through the local reader.
- After Task 4: agents can use the flow autonomously during preview tests.

## Risks and Mitigations

| Risk                                 | Impact | Mitigation                                                                        |
| ------------------------------------ | ------ | --------------------------------------------------------------------------------- |
| Unsigned or spoofed inbound payloads | High   | Verify Mailgun HMAC before inserting.                                             |
| Email data accumulates indefinitely  | Medium | Store `consumed_at` now; add cleanup scheduling in a follow-up.                   |
| Dynamic preview URLs change          | Medium | Use one stable production/preview ingress route and metadata-filtered recipients. |
| Browser exposure of service role key | High   | Keep all storage access server-side or local trusted CLI only.                    |

## Open Questions

- Which domain or subdomain should receive agent email?
- Should the first Mailgun route target preview, production, or both?
- Should reader CLI mark messages consumed by default or only with an explicit flag?
