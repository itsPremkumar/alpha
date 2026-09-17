export const GATEWAY_BASE = (process.env.NEXT_PUBLIC_GATEWAY_URL || "/api").replace(/\/+$/, "");

export type ApiFailureKind = "http" | "network" | "stopped" | "response" | "route";

export class ApiClientError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number;

  constructor(kind: ApiFailureKind, status = 0) {
    const validStatus = Number.isInteger(status) && status >= 100 && status <= 599 ? status : 0;
    super(kind === "http"
      ? `Request failed${validStatus ? ` (HTTP ${validStatus})` : ""}.`
      : kind === "stopped" ? "Request stopped locally."
      : kind === "response" ? "The server returned an unreadable response."
      : kind === "route" ? "Invalid API route."
      : "The request could not be completed. Check your connection.");
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = validStatus;
  }
}

export function apiUrl(path: string, base = GATEWAY_BASE): string {
  if (!path.startsWith("/") || path.startsWith("//") || /[\\\r\n#]/.test(path)) {
    throw new ApiClientError("route");
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(path.split("?")[0]);
  } catch {
    throw new ApiClientError("route");
  }
  if (decoded.split("/").some((part) => part === "." || part === "..") || /[\\\r\n]/.test(decoded)) {
    throw new ApiClientError("route");
  }
  const root = base.replace(/\/+$/, "");
  if (!root || (!/^https?:\/\//.test(root) && (!root.startsWith("/") || root.startsWith("//")))) {
    throw new ApiClientError("route");
  }
  const suffix = path === "/api" ? "" : path.startsWith("/api/") ? path.slice(4) : path;
  return `${root}${suffix}`;
}

export function csrfToken(cookie: string): string | undefined {
  const value = cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("csrf_token="))?.slice(11);
  if (!value) return undefined;
  try {
    const token = decodeURIComponent(value);
    return /^[A-Za-z0-9_-]+$/.test(token) ? token : undefined;
  } catch {
    return undefined;
  }
}

export function createApiClient(options: {
  baseUrl?: string;
  fetch?: typeof fetch;
  getCookie?: () => string;
} = {}) {
  return async (path: string, init: RequestInit = {}): Promise<Response> => {
    const url = apiUrl(path, options.baseUrl);
    const headers = new Headers(init.headers);
    const method = (init.method || "GET").toUpperCase();
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && !headers.has("X-CSRF-Token")) {
      const cookie = options.getCookie ? options.getCookie() : typeof document === "undefined" ? "" : document.cookie;
      const token = csrfToken(cookie);
      if (token) headers.set("X-CSRF-Token", token);
    }
    let response: Response;
    try {
      response = await (options.fetch || globalThis.fetch)(url, {
        ...init,
        method,
        headers,
        credentials: "include",
        redirect: "error",
      });
    } catch {
      throw new ApiClientError(init.signal?.aborted ? "stopped" : "network");
    }
    if (!response.ok) throw new ApiClientError("http", response.status);
    return response;
  };
}

export const apiFetch = createApiClient();
