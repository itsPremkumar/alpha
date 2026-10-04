import ChatView from "@/components/ChatView";
import { isWorkspaceView } from "@/lib/workspace-view";

/**
 * Resolve the requested workspace view **on the server**.
 *
 * `ChatView` used to read `window.location.search` inside its state
 * initialiser, guarded with `typeof window === "undefined" ? "" : …`. That guard
 * makes the two renders disagree by construction: the server saw `""` and
 * rendered `chat`, the client saw `?view=reliability` and rendered the
 * reliability section, and React threw a hydration mismatch on **every** deep
 * link to any non-chat view. Measured directly: the SSR responses for
 * `?view=chat`, `?view=overview` and `?view=reliability` were byte-identical
 * (64480 characters each), so the server was ignoring the parameter entirely and
 * every non-chat URL began with a thrown-away server tree.
 *
 * The view is derived once, here, where the URL is actually available on both
 * sides, and handed down as a prop. The client's first render then matches the
 * server's exactly.
 *
 * An unrecognised or absent `view` resolves to `chat`, which is what
 * `workspaceViewFromSearch` already does for the client — one rule, both sides.
 */
export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const requested = params?.view;
  const candidate = Array.isArray(requested) ? requested[0] : requested;
  const initialView = isWorkspaceView(candidate) ? candidate : "chat";
  return <ChatView initialView={initialView} />;
}