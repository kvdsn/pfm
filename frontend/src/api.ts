export type ApiInit = Omit<RequestInit, "body"> & {
  body?: BodyInit;
  /** Sent as a JSON request body. */
  json?: unknown;
};

export const api = async <T,>(url: string, init: ApiInit = {}): Promise<T> => {
  const { json, ...rest } = init;
  const request: RequestInit = json === undefined
    ? rest
    : {
        ...rest,
        headers: { ...rest.headers, "Content-Type": "application/json" },
        body: JSON.stringify(json),
      };
  const response = await fetch(url, request);
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(
      `Unexpected response from ${url}. The local server may need to be restarted to load the latest API routes.`,
    );
  }
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return body as T;
};

export const errorMessage = (cause: unknown, fallback: string) =>
  cause instanceof Error ? cause.message : fallback;
