// lion-pet-close.test.mjs — the companion can be dismissed from the companion
// itself, and that dismissal cannot misfire on the pet.
//
// Hiding previously lived two clicks deep (pet -> settings menu -> Hide), which
// is a long way to go for the one action you want when the lion is in the way.
// The properties worth pinning are the ways a close control on a widget usually
// goes wrong: nesting a button in a button, firing the widget's own click on the
// way out, enlarging the box that placement measures, and staying clickable
// while invisible.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const component = read("../components/lion-pet/LionPet.tsx");
const style = read("../components/lion-pet/lion-pet.css");

test("the pet renders its own close control", () => {
  assert.match(component, /className="lion-pet-close-button"/);
  assert.match(component, /aria-label="Hide the Alpha lion companion"/);
});

test("closing the pet goes through the existing hide path", () => {
  // Not a second hide implementation: it must reuse the toggle that persists
  // `visible`, or the setting and the desktop-overlay bridge would drift apart.
  const close = component.match(
    /className="lion-pet-close-button"[\s\S]*?<\/button>/,
  );
  assert.ok(close, "close control not found");
  assert.match(close[0], /toggleVisible\(\)/);
});

test("the close control is a sibling of the pet, never nested inside it", () => {
  // A button inside a button is invalid HTML, and the inner click would also
  // bubble into the pet's own "pet the lion" handler on the way out.
  const hitArea = component.match(
    /className=\{?`?[^`]*lion-pet-hit-area[\s\S]*?<\/button>/,
  );
  assert.ok(hitArea, "pet hit area not found");
  assert.ok(
    !/lion-pet-close-button/.test(hitArea[0]),
    "the close control must not be nested inside the pet's hit-area button",
  );
});

test("dismissing the pet does not also pet it", () => {
  const close = component.match(
    /className="lion-pet-close-button"[\s\S]*?<\/button>/,
  );
  assert.ok(close);
  assert.match(close[0], /onClick=\{\(event\) => \{\s*event\.stopPropagation\(\);/);
  assert.match(close[0], /onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/);
});

test("the close control is absolutely positioned, so placement geometry is unchanged", () => {
  // Placement escapes the keep-out regions by measuring the shell's box; a close
  // button that grew that box would push the companion out of legal positions.
  assert.match(style, /\.lion-pet-close-button\s*\{[^}]*position:\s*absolute;/s);
});

test("the invisible close control cannot steal clicks", () => {
  // `opacity: 0` alone leaves a button fully clickable. Without the guard an
  // invisible control sits over the pet's corner swallowing clicks.
  assert.match(
    style,
    /\.lion-pet-close-button\s*\{[^}]*opacity:\s*0;[^}]*pointer-events:\s*none;/s,
  );
  assert.match(
    style,
    /\.lion-pet-close-button:focus-visible\s*\{[^}]*pointer-events:\s*auto;/s,
  );
});

test("the close control meets the target-size floor and is reachable by keyboard", () => {
  // 24px floor; 1.65rem is 26.4px.
  assert.match(
    style,
    /\.lion-pet-close-button\s*\{[^}]*width:\s*1\.65rem;[^}]*height:\s*1\.65rem;/s,
  );
  // Revealed on hover AND on focus-visible, so it is not mouse-only.
  assert.match(style, /\.lion-pet-shell:hover \.lion-pet-close-button/);
  assert.match(style, /\.lion-pet-close-button:focus-visible/);
});

test("hiding the pet still leaves a way to bring it back", () => {
  // A close control with no restore path would be a one-way door.
  assert.match(component, /aria-label="Show Alpha lion companion"/);
});

test("the settings hint no longer sits under the close control", () => {
  assert.match(style, /\.lion-pet-settings-hint\s*\{[^}]*right:\s*2\.15rem;/s);
});