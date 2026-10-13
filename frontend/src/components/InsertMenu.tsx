"use client";

/**
 * The composer's insert menu — one control where the paperclip, the image
 * glyph and the code glyph used to sit in a row.
 *
 * Why consolidate them: those three were not three capabilities. The paperclip
 * and the image button pointed at the *same* `fileRef` and the *same*
 * `<input type="file" multiple>`, which carried no `accept` attribute — so
 * "Upload images or media" offered `Q3-report.pdf` with exactly the same
 * prominence as a PNG. Two identical controls with one dishonest label is a
 * worse answer to "where do I upload?" than a single menu that says what each
 * row will do. `lib/insert-menu.ts` holds the rows and their `accept` filters,
 * so the label and the picker cannot disagree: the test pins them against each
 * other rather than trusting the markup.
 *
 * The structural rules are the ones `ModelMenu` and `FreeCatalogMenu` already
 * established, because this panel sits in the same clipped composer:
 *
 * - **Portal to `document.body`, place with `placeFloatingPanel`** — an
 *   in-place panel is cut off by the sticky header and `<main
 *   class="overflow-hidden">`.
 * - **The outside-click check tests both refs**, since the panel has left the
 *   trigger's wrapper.
 * - **Escape returns focus to the trigger.**
 *
 * And the behavioural one this menu specifically owes its user:
 *
 * - **One input per file class.** The image row clicks an input that declares
 *   `accept="image/*"`; the file row clicks one that declares nothing. They are
 *   separate elements precisely because sharing one is how the old pair ended
 *   up both doing everything.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Code2, FileText, ImageIcon, Paperclip } from "lucide-react";
import { placeFloatingPanel } from "@/lib/workspace-menu-geometry";
import {
  acceptForActionKey,
  insertActionTitle,
  insertMenuSections,
  type InsertAction,
  type InsertActionKey,
} from "@/lib/insert-menu";

const MENU_ID = "composer-insert-menu";
const PANEL_WIDTH = 300;

/** Measured in a layout effect before paint, so no flash at the top-left. */
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const ACTION_ICONS: Record<InsertActionKey, React.ReactNode> = {
  "upload-image": <ImageIcon className="size-3.5 shrink-0" aria-hidden="true" />,
  "upload-file": <FileText className="size-3.5 shrink-0" aria-hidden="true" />,
  "code-block": <Code2 className="size-3.5 shrink-0" aria-hidden="true" />,
};

export interface InsertMenuProps {
  /** False when the surface passed no `onAttach` — the upload section then does not render. */
  canUpload: boolean;
  uploading: boolean;
  onAttach?: (files: FileList) => void;
  /** Inserts the fence into the draft; the caller owns focus, since it owns the textarea. */
  onInsertCode: () => void;
  className?: string;
}

export function InsertMenu(props: InsertMenuProps) {
  const { canUpload, uploading, onAttach, onInsertCode } = props;

  const [open, setOpen] = useState(false);
  const [portalReady, setPortalReady] = useState(false);
  const [panel, setPanel] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  /** One input per file class — sharing one is what made the old pair identical. */
  const imageInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setPortalReady(true);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setPanel(null);
  }, []);

  useEffect(() => {
    if (!open) return;
    triggerRef.current?.focus();
  }, [open]);

  const placePanel = useCallback(() => {
    const trigger = triggerRef.current;
    const content = panelRef.current;
    if (!trigger || !content) return;
    const rect = trigger.getBoundingClientRect();
    const box = placeFloatingPanel({
      trigger: { top: rect.top, bottom: rect.bottom, left: rect.left },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      naturalHeight: content.scrollHeight,
      panelWidth: PANEL_WIDTH,
    });
    setPanel({ top: box.top, left: box.left, maxHeight: box.maxHeight });
  }, []);

  useIsomorphicLayoutEffect(() => {
    if (!open) return;
    placePanel();

    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", placePanel);
    window.addEventListener("scroll", placePanel, true);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", placePanel);
      window.removeEventListener("scroll", placePanel, true);
    };
  }, [open, placePanel]);

  /** A row that opens a picker closes the menu first, so it is not left floating behind the OS dialog. */
  const runAction = useCallback(
    (action: InsertAction) => {
      if (action.isFilePicker) {
        close();
        (action.key === "upload-image" ? imageInputRef : fileInputRef).current?.click();
        return;
      }
      if (action.key === "code-block") {
        close();
        onInsertCode();
      }
    },
    [close, onInsertCode],
  );

  const sections = insertMenuSections({ canUpload });

  const handlePicked = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) onAttach?.(e.target.files);
      // Reset so picking the same file twice still fires `onChange`.
      e.target.value = "";
    },
    [onAttach],
  );

  return (
    <div className={`relative inline-flex ${props.className ?? ""}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        disabled={uploading && !open}
        aria-expanded={open}
        aria-haspopup="true"
        aria-controls={open ? MENU_ID : undefined}
        className="inline-flex items-center gap-0.5 p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40 cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40 shrink-0"
        title={uploading ? "Uploading…" : "Attach a file or insert content"}
        aria-label="Attach or insert"
      >
        <Paperclip className="size-4 shrink-0" />
        <ChevronDown
          className={`size-2.5 shrink-0 align-middle transition-transform dur-fast ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {canUpload ? (
        <>
          <input
            ref={imageInputRef}
            type="file"
            multiple
            accept={acceptForActionKey("upload-image")}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
            onChange={handlePicked}
          />
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={acceptForActionKey("upload-file")}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
            onChange={handlePicked}
          />
        </>
      ) : null}

      {open && portalReady
        ? createPortal(
            <div
              ref={panelRef}
              id={MENU_ID}
              role="dialog"
              aria-label="Attach a file or insert content"
              style={{
                position: "fixed",
                top: panel?.top ?? 0,
                left: panel?.left ?? 0,
                width: PANEL_WIDTH,
                maxHeight: panel?.maxHeight ?? undefined,
                visibility: panel ? undefined : "hidden",
              }}
              className="z-[100] overflow-y-auto overscroll-contain rounded-2xl border border-border/80 bg-card elev-3 p-2.5 space-y-1.5 focus:outline-none animate-in fade-in zoom-in-95 dur-fast"
            >
              {sections.map((section) => (
                <div key={section.key} className="space-y-0.5">
                  <div className="px-2 pt-1 pb-0.5 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {section.label}
                  </div>
                  <ul className="space-y-0.5">
                    {section.actions.map((action) => (
                      <li key={action.key}>
                        <button
                          type="button"
                          onClick={() => runAction(action)}
                          title={insertActionTitle(action)}
                          className="w-full flex items-start gap-2 px-2 py-1.5 rounded-lg text-left hover:bg-muted/50 transition-colors"
                        >
                          <span className="mt-0.5 text-muted-foreground">{ACTION_ICONS[action.key]}</span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-[11px] font-medium text-foreground">{action.label}</span>
                            <span className="block text-[9px] leading-snug text-muted-foreground">{action.hint}</span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
