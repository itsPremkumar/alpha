export type ChatRequestFailure = {
  kind: "http" | "network" | "stream" | "empty" | "stopped";
  status?: number;
};

export function chatRequestErrorMessage(failure: ChatRequestFailure): string {
  switch (failure.kind) {
    case "http": {
      const status = failure.status;
      const label = typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599
        ? ` (HTTP ${status})`
        : "";
      return `Request failed${label}. No assistant response was received. Review your draft and try again.`;
    }
    case "network":
      return "The request could not be completed. No assistant response was received. Check your connection before retrying; the server may still be processing the request and its durable run can continue after reconnect.";
    case "stream":
      return "The response stream was interrupted. Any partial response below is incomplete and has not been saved. The server may still be running; reconnect or reload Runs before retrying.";
    case "empty":
      return "The server returned no response content. No assistant answer was saved. Check Runs before retrying.";
    case "stopped":
      return "The response stream was stopped locally. Any partial response below is incomplete and has not been saved. Server cancellation is not confirmed here; check Runs before retrying.";
  }
}
