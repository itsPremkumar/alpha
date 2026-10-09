import { branding } from "./branding";

/**
 * The DEFAULT agent: who answers when no specialist bot is selected.
 *
 * An operator who never picked a bot is still talking to somebody, and that
 * somebody used to be labelled `Lead Agent` — an internal architecture term
 * (the orchestrating agent of `alpha.agents.lead_agent`) leaking into the
 * product as if it were a name. It is now the product's own name and face: the
 * default agent is named **Alpha** and carries the lion mark, because on a
 * fresh install, for every user of this project, that is exactly who they are
 * talking to.
 *
 * Deliberate choices:
 *
 * - **Its own constant, not an alias of `branding.name`.** They happen to be
 *   the same word today. Aliasing would make renaming the product silently
 *   rename the agent, or vice versa — two facts that may well want to diverge.
 *   `default-agent.test.mjs` pins that they currently agree, so a rename that
 *   forgets the other shows up as a failing test rather than as a half-renamed
 *   UI.
 * - **A path out of `branding.icons`, not an import of
 *   `assets/images/alpha.png`.** That poster carries the "ALPHA" wordmark and
 *   the AUTONOMOUS · INTELLIGENT · EVOLVING tagline, which are illegible at a
 *   24px avatar; the generated marks are crops of that same poster with the
 *   type removed, so this is the same lion. It also keeps this module free of
 *   asset imports, which the client test transpiler does not resolve — every
 *   value here must be readable by `node --test`.
 */
export const DEFAULT_AGENT_NAME = "Alpha";

/**
 * The default agent's avatar: the lion mark, type-free, generated from the one
 * brand poster by `scripts/generate-brand-assets.mjs`.
 */
export const DEFAULT_AGENT_AVATAR = branding.icons.pwa512;

/**
 * The tooltip on the default agent's glyph. It answers the question the
 * un-labelled state otherwise poses — *why is there no bot here?* — and names
 * who is handling the conversation instead.
 */
export const DEFAULT_AGENT_HINT = `No specialist selected. ${DEFAULT_AGENT_NAME} auto-routes.`;

/**
 * The sentence for a conversation owned by nobody in particular.
 * Kept beside the name so the copy cannot drift from it.
 */
export const DEFAULT_AGENT_ROUTING_LINE = `${DEFAULT_AGENT_NAME} auto-routes`;
