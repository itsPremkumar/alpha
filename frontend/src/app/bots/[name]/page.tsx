/**
 * Dedicated single-bot detail route: `/bots/[name]`.
 *
 * Deliberately separate from the existing `chat-shell/BotDetailPanel`, which is
 * an *editor* that PATCHes profile fields. This route is a read-only survey of
 * everything the Gateway reports about one bot.
 *
 * The `name` segment is percent-encoded before it reaches the client so a bot
 * name containing a reserved character (or a slash) cannot break out of the
 * route or inject a query string.
 */

import { BotDetailView } from "@/components/bots/BotDetailView";

export default async function BotDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const decoded = decodeURIComponent(name);
  return <BotDetailView name={decoded} />;
}