"use client";

/**
 * The code block — how fenced code in an answer is shown.
 *
 * `ReactMarkdown` renders fenced code as a bare
 * `<pre><code>`, so a command or snippet had no
 * language label and no copy affordance — the user
 * had to triple-click and pray. react-markdown v10
 * no longer passes an `inline` flag, so the two
 * cases are split the way v10 intends:
 *
 * - `pre` is overridden for **block** code. Its
 *   `children` is the `<code>` element the fence
 *   produced; that element's `className` carries the
 *   `language-*` tag and its `children` the code
 *   text. The block renders a header (language chip +
 *   one-click copy) over the styled code body.
 * - `code` is overridden for **inline** code, which
 *   keeps the plain prose treatment.
 *
 * Rendered through ReactMarkdown's `components` map;
 * see `MessageItem`.
 */

import React, { useState } from "react";
import { Check, Copy } from "lucide-react";
import type { Components, ExtraProps } from "react-markdown";

/** The props react-markdown hands a `code` renderer. */
type CodeProps = React.JSX.IntrinsicElements["code"] & ExtraProps;
/** The props react-markdown hands a `pre` renderer. */
type PreProps = React.JSX.IntrinsicElements["pre"] & ExtraProps;

/** Inline code (`like this`) — the plain prose treatment. */
function InlineCode({ className, children, ...props }: CodeProps) {
  return (
    <code className={className} {...props}>
      {children}
    </code>
  );
}

/** Block code — the card with a language chip and a copy button. */
function CodeBlock({ children }: PreProps) {
  const [copied, setCopied] = useState(false);

  // The fence's `<code>` element is the block's only child.
  const codeElement = React.Children.toArray(children).find((child) =>
    React.isValidElement(child),
  );
  const codeProps = (
    React.isValidElement(codeElement) ? codeElement.props : {}
  ) as { className?: string; children?: React.ReactNode };

  const language =
    /language-(\w+)/.exec(codeProps.className || "")?.[1] || "text";
  const raw = typeof codeProps.children === "string" ? codeProps.children : "";
  // A trailing newline is fence markup, not content.
  const text = raw.replace(/\n$/, "");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — the button simply stays un-checked */
    }
  };

  return (
    <div
      className="my-2 overflow-hidden rounded-lg border border-border/60 bg-muted/40"
      data-code-block
    >
      <div className="flex items-center gap-2 border-b border-border/50 bg-muted/60 px-3 py-1">
        <span className="rounded bg-primary/10 px-1.5 py-px font-mono text-[9px] font-semibold uppercase tracking-wider text-primary">
          {language}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={copy}
          title={copied ? "Copied" : "Copy code"}
          aria-label="Copy code"
          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors hover:bg-muted/70 ${
            copied ? "text-emerald-500" : "text-muted-foreground"
          }`}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="max-h-96 overflow-auto p-3 font-mono text-[11px] leading-relaxed">
        <code>{text}</code>
      </pre>
    </div>
  );
}

/**
 * The ReactMarkdown `components` map that swaps in the
 * code-block card for fences and the plain treatment for
 * inline code. Kept beside the components so the wiring
 * stays with the things it wires.
 */
export const markdownComponents: Components = {
  code: InlineCode,
  pre: CodeBlock,
};
