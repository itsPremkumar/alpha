"use client";

import Image from "next/image";
import React from "react";

import alphaLogo from "@/assets/images/alpha.png";
import { branding } from "@/lib/branding";

/**
 * The product mark: the alpha logo rendered together with the "alpha" wordmark
 * as a single branding element.
 *
 * Used in the app's most prominent surfaces — the sidebar header and the chat
 * landing hero — so the branding is visible on the main screens rather than
 * only on a splash or settings page.
 *
 * The logo is a square raster, so width and height are always equal and the
 * image scales with the surrounding text instead of stretching.
 */
export function BrandLogo({
  logoSize = 28,
  textClassName = "text-sm",
  className = "",
  priority = false,
}: {
  /** Rendered size of the logo in pixels (square). */
  logoSize?: number;
  /** Classes for the wordmark text, so it can match its surface. */
  textClassName?: string;
  /** Classes for the wrapping element. */
  className?: string;
  /** Set for above-the-fold instances to skip lazy loading. */
  priority?: boolean;
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <Image
        src={alphaLogo}
        alt={branding.logoAlt}
        width={logoSize}
        height={logoSize}
        priority={priority}
        className="shrink-0 rounded-lg object-contain"
        style={{ width: logoSize, height: logoSize }}
      />
      <span className={`font-semibold tracking-tight ${textClassName}`}>{branding.wordmark}</span>
    </span>
  );
}

/** Logo-only mark for tight spaces (e.g. the collapsed sidebar). */
export function BrandMark({
  size = 24,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <Image
      src={alphaLogo}
      alt={branding.logoAlt}
      width={size}
      height={size}
      className={`shrink-0 rounded-lg object-contain ${className}`}
      style={{ width: size, height: size }}
    />
  );
}
