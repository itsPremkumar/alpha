/**
 * Capability badges for one model card.
 *
 * The rule this component exists to enforce: **a capability nobody reported is
 * not a capability the model lacks.** Every chip is tri-state — supported,
 * explicitly unsupported, or not reported — and the not-reported state is drawn
 * with a dashed border so it cannot be read at a glance as either of the other
 * two. Rendering it as "no" would tell the user the agent cannot see images
 * when in fact nobody asked the provider.
 *
 * Ordering is by modality the way a user scans for one (input first, then
 * generation, then effort), not by the order capabilities happen to be declared.
 */

import { Brain, Eye, Image, Mic, Sparkles, Video, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  capabilityBadges,
  formatContextWindow,
  formatPrice,
  type ModelCapabilities,
} from "@/lib/model-capabilities";

/** Icons in scan order: what it accepts, what it produces, how it reasons. */
const BADGE_ICONS: Readonly<Record<string, LucideIcon>> = {
  vision: Eye,
  tools: Wrench,
  thinking: Brain,
  reasoningEffort: Sparkles,
  imageGeneration: Image,
  video: Video,
  speech: Mic,
};

export interface ModelCapabilityBadgesProps {
  capabilities: ModelCapabilities;
  /** Hide the dashed "not reported" chips; keeps a dense variant for lists. */
  compact?: boolean;
}

/** One chip's worth of state, pre-resolved for rendering. */
interface ResolvedChip {
  key: string;
  label: string;
  Icon: LucideIcon;
  state: boolean | null;
  className: string;
  title: string;
}

export function ModelCapabilityBadges({ capabilities, compact = false }: ModelCapabilityBadgesProps) {
  const chips: ResolvedChip[] = capabilityBadges(capabilities).map((badge) => ({
    key: badge.key,
    label: badge.label,
    Icon: BADGE_ICONS[badge.key] ?? Sparkles,
    state: badge.state,
    className: badge.className,
    title: badge.title,
  }));

  // In compact mode only real answers are shown, so a row is not dominated by
  // dashed placeholders. The full mode keeps them: the point is to be able to
  // tell "no" from "never asked".
  const visible = compact ? chips.filter((chip) => chip.state !== null) : chips;
  const context = formatContextWindow(capabilities.contextWindow);
  const price = formatPrice(capabilities.inputPrice, capabilities.outputPrice);

  if (!visible.length && context === null && price === null) return null;

  return (
    <div className="flex flex-wrap items-center gap-1" data-testid="model-capability-badges">
      {visible.map((chip) => (
        <span
          key={chip.key}
          title={chip.title}
          data-testid={`capability-${chip.key}`}
          data-state={chip.state === null ? "unknown" : chip.state ? "yes" : "no"}
          className={`inline-flex items-center gap-0.5 rounded-md border px-1 py-px text-[10px] leading-tight ${
            chip.state === null ? "border-dashed border-border/60" : "border-transparent"
          } ${chip.className}`}
        >
          <chip.Icon className="size-2.5" aria-hidden />
          {chip.label}
        </span>
      ))}
      {context !== null && (
        <span
          className="text-[10px] text-muted-foreground"
          title="Effective input context window in tokens"
          data-testid="capability-context"
        >
          • {context} ctx
        </span>
      )}
      {price !== null && (
        <span className="text-[10px] text-muted-foreground" title="Price per 1M tokens (USD)" data-testid="capability-price">
          • {price}
        </span>
      )}
    </div>
  );
}
