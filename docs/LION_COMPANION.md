# Alpha Lion Companion

Milo is Alpha's local-first lion companion. It is an original inline SVG rather
than a downloaded or remote asset, so the companion works offline and does not
add a third-party image, script, or telemetry request.

All web companion implementation is isolated under
`frontend/src/components/lion-pet/`: `LionPet.tsx` owns rendering and
interactions, `lion-pet-model.ts` owns looks/actions/settings,
`useLionPetActivity.ts` is the small lifecycle adapter, and
`lion-pet.css` owns the feature styles. `ChatView.tsx` only consumes the
adapter and does not know the animation or skin implementation. The native
window lifecycle is similarly isolated in `electron/lib/lion-pet-window.js`;
`electron/main.js` only wires the controller to the application lifecycle.

## User features

- State reactions for `thinking`, `working`, `waiting`, `success`, `error`, and
  `sleeping`, driven by bounded run lifecycle signals.
- Six original looks: Golden Mane, Midnight Moon, Ember Mane, Frostmane,
  Royal Violet, and Moss Guardian.
- An articulated four-leg rig with upper/lower leg motion, paws, joints, claws,
  a shaped torso, mane strands, cheek fur, muzzle lobes, whiskers, eye detail,
  and a nose highlight. Walk/run use alternating diagonal gaits; the native
  desktop renderer mirrors the same rig and action vocabulary.
- Action performances: walk, run, jump, roar, pounce, play, sleep, stretch,
  prowl, hunt, shake, and spin. Walk/run/prowl/hunt include bounded horizontal
  travel in the app and native desktop window, with the leg gait synchronized
  to the movement. Actions can be selected manually or run automatically during
  idle time; they are visual reactions, not autonomous tool actions.
- Click to pet, double-click for a roar, drag to reposition, right-click for
  controls, keyboard activation, and a screen-reader status bubble.
- Per-user local settings for visibility, size, optional sound cues, and
  position. Storage failures degrade to an in-memory companion.
- Reduced-motion support and responsive sizing for smaller windows.
- Optional Windows desktop mode: a transparent, always-on-top, draggable
  Electron window with return, Walk, Run, Action, and Look controls. The native window
  receives only sanitized state and a short bounded message; it never receives
  prompts, responses, thread IDs, or tool output.

## Research patterns adapted

The implementation follows patterns visible in current public pet and agent
companion projects, while keeping Alpha's safety boundaries:

- [CoPet](https://github.com/ChanceYu/CoPet) — local-first transparent desktop
  companion, agent-state reactions, drag/click interactions, sound packs, and
  custom pet packages.
- [Claude Desktop Buddy](https://github.com/anthropics/claude-desktop-buddy) —
  explicit state categories such as idle, busy, attention, celebrate, and
  heart, with local settings.
- [Petdex](https://github.com/crafter-station/petdex) — a shared companion
  package format and state-oriented animation model.
- [OpenPets](https://github.com/alvinunreal/openpets) — local event-driven pet
  integration rather than prompt-text display.
- [Clawd on Desk](https://github.com/rullerzhou-afk/clawd-on-desk) — visible
  status bubbles and small always-on-top desktop UX.

Alpha does not copy those projects' assets or blindly replay arbitrary agent
activity. Its pet observes only the existing run state and deliberately avoids
prompt content, credentials, external side effects, and hidden automation.

## Boundaries

The companion is presentation-only. It cannot approve actions, call tools,
read arbitrary files, or change run state. The in-app version is available in
the web workspace; the detached native version is an Electron-only convenience.
Neither renderer stores or emits prompts, responses, thread IDs, tool output, or
credentials, and the in-app component deliberately does not place a thread ID in
its DOM. Closing the main Alpha window closes the native companion and follows
the normal desktop shutdown path.
