type ReleaseLeaseOptions = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetchImpl?: typeof fetch;
  leaseId: string;
  timeoutMs?: number;
};

const RELEASE_TIMEOUT_MS = 2_000;

export async function releaseChatTurnLease({
  accessToken,
  baseUrl,
  extraHeaders,
  fetchImpl = fetch,
  leaseId,
  timeoutMs = RELEASE_TIMEOUT_MS,
}: ReleaseLeaseOptions): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${baseUrl}/api/chat/model-state/lease`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
        ...(extraHeaders ?? {}),
      },
      body: JSON.stringify({ lease_id: leaseId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Unable to release chat turn lease (${response.status})`);
    }
  } finally {
    clearTimeout(timeout);
  }
}
