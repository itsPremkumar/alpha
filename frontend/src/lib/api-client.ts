function normalizeGatewayBase(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) return "/api";
  // Deployments commonly set NEXT_PUBLIC_GATEWAY_URL to the Gateway origin
  // (for example http://127.0.0.1:8001). API clients still address /api/*.
  if (/^https?:\/\//i.test(trimmed)) {
    try {
      const url = new URL(trimmed);
      if (!url.pathname.replace(/\/+$/, "").endsWith("/api")) url.pathname = `${url.pathname.replace(/\/+$/, "")}/api`;
      return url.toString().replace(/\/+$/, "");
    } catch {
      return trimmed;
    }
  }
  return trimmed.endsWith("/api") ? trimmed : `${trimmed}/api`;
}

export const GATEWAY_BASE = normalizeGatewayBase(process.env.NEXT_PUBLIC_GATEWAY_URL || "/api");

export type ApiFailureKind = "http" | "network" | "stopped" | "response" | "route";

export class ApiClientError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number;
  /**
   * The server's own failure text when the response carried one (FastAPI
   * `detail`, or a legacy `error` string). Kept verbatim so the UI can show
   * the REAL reason — file + line for a corrupt journal, the matched policy
   * rule, a validation list — instead of a bare status sentence.
   */
  readonly detail: string | null;

  constructor(kind: ApiFailureKind, status = 0, detail: string | null = null) {
    const validStatus = Number.isInteger(status) && status >= 100 && status <= 599 ? status : 0;
    super(kind === "http"
      ? `Request failed${validStatus ? ` (HTTP ${validStatus})` : ""}.${detail ? ` ${detail}` : ""}`
      : kind === "stopped" ? "Request stopped locally."
      : kind === "response" ? "The server returned an unreadable response."
      : kind === "route" ? "Invalid API route."
      : "The request could not be completed. Check your connection.");
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = validStatus;
    this.detail = detail;
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
    if (!response.ok) {
      // Carry the server's own failure text (FastAPI `detail`) into the error so
      // every caller can surface the real reason instead of a bare status line.
      let detail: string | null = null;
      try {
        const data: unknown = await response.clone().json();
        if (data && typeof data === "object") {
          const record = data as Record<string, unknown>;
          const candidate = record.detail ?? record.error;
          if (typeof candidate === "string" && candidate !== "") {
            detail = candidate;
          } else if (Array.isArray(candidate)) {
            detail = candidate.map((item) => String(item)).join("; ");
          }
        }
      } catch {
        // Unreadable or non-JSON body: keep the status sentence only.
      }
      throw new ApiClientError("http", response.status, detail);
    }
    return response;
  };
}

export const apiFetch = createApiClient();
