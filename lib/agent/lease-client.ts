type ReleaseLeaseOptions = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetchImpl?: typeof fetch;
  leaseId: string;
};

export async function releaseChatTurnLease({
  accessToken,
  baseUrl,
  extraHeaders,
  fetchImpl = fetch,
  leaseId,
}: ReleaseLeaseOptions): Promise<void> {
  const response = await fetchImpl(`${baseUrl}/api/chat/model-state/lease`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...(extraHeaders ?? {}),
    },
    body: JSON.stringify({ lease_id: leaseId }),
  });
  if (!response.ok) {
    throw new Error(`Unable to release chat turn lease (${response.status})`);
  }
}
