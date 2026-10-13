/**
 * Pure derivations behind the composer's insert menu.
 *
 * The composer used to expose three controls in a row: a paperclip, an image
 * icon, and a code glyph. Two of them were the *same button* — both called
 * `fileRef.current?.click()` on a single `<input type="file" multiple>` with
 * no `accept` attribute, so the control labelled **"Upload images or media"**
 * opened an unfiltered picker offering `report.pdf` and `archive.tar.gz`
 * exactly as readily as a PNG. A label naming a file class is a claim about
 * what the picker offers, and nothing was enforcing it.
 *
 * This module is where that claim becomes enforceable: `accept` is data here,
 * the menu renders it onto the input it owns, and `insert-menu.test.mjs` pins
 * each row's filter against its own label. Keeping it out of JSX matters for a
 * second reason — a menu that must grow (the request was explicitly "add more
 * things here later") needs its rows to be a list, not a sequence of copied
 * `<button>` blocks that drift apart.
 *
 * **A row that cannot act is not offered.** Upload rows exist only when the
 * surface actually wired `onAttach`; a composer without it renders no upload
 * section rather than a disabled-looking entry that can never open a picker.
 */

/** Every row the menu can ever offer. Adding an action means adding a member here and a row in `INSERT_ACTIONS`. */
export type InsertActionKey = "upload-image" | "upload-file" | "code-block";

/** Sections in render order. Upload comes first because it is the reason the menu exists. */
export const INSERT_GROUP_ORDER = ["upload", "insert"] as const;
export type InsertGroupKey = (typeof INSERT_GROUP_ORDER)[number];

export const INSERT_GROUP_LABELS: Record<InsertGroupKey, string> = {
  upload: "From this device",
  insert: "Insert into the message",
};

export interface InsertAction {
  key: InsertActionKey;
  /** Sentence-case label ending in an ellipsis when it opens a picker. */
  label: string;
  /** What it will actually do — shown under the label, because "upload" alone does not say what is accepted. */
  hint: string;
  group: InsertGroupKey;
  /**
   * The `accept` attribute for a file-picking row: a MIME filter, or `null`
   * meaning *every* file. Only meaningful when `isFilePicker` is true — a row
   * that inserts text has no picker to filter, and reporting its `accept` as
   * `null` beside a genuine "any file" row would make the two indistinguishable.
   */
  accept: string | null;
  /** Separates "the picker accepts everything" from "this row opens no picker". */
  isFilePicker: boolean;
  /** True when the row needs `onAttach` to exist. */
  requiresUpload: boolean;
}

/**
 * The menu's rows, in the order they render.
 *
 * The image row's `accept` is `image/*` — the fix, not the status quo. It was
 * written to match the label the button already carried.
 */
export const INSERT_ACTIONS: InsertAction[] = [
  {
    key: "upload-image",
    label: "Upload image…",
    hint: "The picker only offers image files (PNG, JPEG, WebP, GIF)",
    group: "upload",
    accept: "image/*",
    isFilePicker: true,
    requiresUpload: true,
  },
  {
    key: "upload-file",
    label: "Upload file or document…",
    hint: "Any file: PDF, text, code, spreadsheets, archives",
    group: "upload",
    accept: null,
    isFilePicker: true,
    requiresUpload: true,
  },
  {
    key: "code-block",
    label: "Insert code block",
    hint: "Adds an empty ``` fence at the cursor",
    group: "insert",
    accept: null,
    isFilePicker: false,
    requiresUpload: false,
  },
];

export interface InsertAvailability {
  /** False when the surface passed no `onAttach`. */
  canUpload: boolean;
}

/**
 * The rows this composer can actually offer.
 *
 * An action whose dependency is absent is *removed*, never rendered disabled.
 * A greyed "Upload image…" is a control implying a pending state that does not
 * exist — this composer simply was not wired for attachments — and it invites
 * a click that can only do nothing.
 */
export function visibleInsertActions(availability: InsertAvailability): InsertAction[] {
  return INSERT_ACTIONS.filter((a) => !(a.requiresUpload && !availability.canUpload));
}

export interface InsertMenuSection {
  key: InsertGroupKey;
  label: string;
  actions: InsertAction[];
}

/**
 * The same rows, grouped, with empty sections dropped.
 *
 * A heading over nothing reads as "this deployment offers no inserts", which is
 * a different claim from "there was nothing in that group" — so a section whose
 * rows all filtered out disappears rather than rendering bare.
 */
export function insertMenuSections(availability: InsertAvailability): InsertMenuSection[] {
  const actions = visibleInsertActions(availability);
  return INSERT_GROUP_ORDER.map((key) => ({
    key,
    label: INSERT_GROUP_LABELS[key],
    actions: actions.filter((a) => a.group === key),
  })).filter((s) => s.actions.length > 0);
}

/**
 * The `accept` attribute to write on a row's own file input, or `undefined`
 * when the row is not a picker.
 *
 * `undefined` rather than `null` at this boundary: React omits the attribute
 * for `undefined`, whereas `null` would also omit it but the distinction
 * matters to the caller — `null` is a *real* "any file" answer and must reach
 * the input as an absent attribute, not as `accept=""`, which some browsers
 * treat as matching nothing at all.
 */
export function fileAcceptAttribute(action: InsertAction): string | undefined {
  return action.isFilePicker ? (action.accept ?? undefined) : undefined;
}

/** The row for a key, or `undefined` if the menu does not offer it. */
export function findInsertAction(key: InsertActionKey): InsertAction | undefined {
  return INSERT_ACTIONS.find((a) => a.key === key);
}

/**
 * The `accept` for a row the menu is expected to carry, keyed by name.
 *
 * An unknown key **raises** rather than returning "any file". Returning
 * `undefined` would be the tempting reading — no action, no filter — and it
 * would silently hand the image input an unfiltered picker, which is precisely
 * the defect this module replaced. A renamed row is a programming error and
 * should fail at the boundary, not ship as "Upload images" offering PDFs.
 */
export function acceptForActionKey(key: InsertActionKey): string | undefined {
  const action = findInsertAction(key);
  if (!action) throw new Error(`insert menu: no action named "${key}"`);
  return fileAcceptAttribute(action);
}

/**
 * The sentence a row's `title` carries — its label and its hint together.
 *
 * A tooltip showing only the label would repeat the visible text and answer
 * none of the question the hint exists for: *which* files will this offer.
 */
export function insertActionTitle(action: InsertAction): string {
  return `${action.label} — ${action.hint}`;
}

/**
 * Names every key the menu would offer for these availability conditions.
 *
 * Exported so the suite can assert the *set* of rows (a new action must be a
 * deliberate addition) instead of only the shape of one.
 */
export function visibleInsertActionKeys(availability: InsertAvailability): InsertActionKey[] {
  return visibleInsertActions(availability).map((a) => a.key);
}
