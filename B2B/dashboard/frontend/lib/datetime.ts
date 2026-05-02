/**
 * Date/time formatting helpers shared across the dashboard.
 *
 * Replaces ~11 near-duplicate fmt* functions that lived inside individual
 * components. All functions are forgiving — bad / null input returns a
 * placeholder ("—" or empty string) instead of throwing, so caller JSX
 * stays simple.
 */

const DASH = "—"

/** Relative-to-now ("just now", "5m ago", "3h ago", "2d ago"). */
export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return DASH
  try {
    const diff = Date.now() - new Date(iso).getTime()
    if (Number.isNaN(diff) || diff < 0) return DASH
    const m = Math.floor(diff / 60_000)
    if (m < 1) return "just now"
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    return `${Math.floor(h / 24)}d ago`
  } catch {
    return DASH
  }
}

/** Short date: "Mar 5". */
export function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return ""
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    })
  } catch {
    return iso
  }
}

/** Short time: "14:32" (24h). */
export function fmtTimeShort(iso: string | null | undefined): string {
  if (!iso) return ""
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    })
  } catch {
    return ""
  }
}

/** Date + time: "Mar 5, 14:32" (locale-formatted). */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}

/**
 * Smart format: today's timestamps collapse to just the time; other
 * days show month+day+time. Pass `prefixToday: true` to get
 * "today 14:32" instead of bare "14:32" — useful when the user might
 * mistake the time for a duration or another field.
 */
export function fmtWhen(
  iso: string | null | undefined,
  options: { prefixToday?: boolean } = {},
): string {
  if (!iso) return ""
  try {
    const d = new Date(iso)
    const now = new Date()
    if (d.toDateString() === now.toDateString()) {
      const t = d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      })
      return options.prefixToday ? `today ${t}` : t
    }
    return fmtDateTime(iso)
  } catch {
    return iso
  }
}

/**
 * Human duration from seconds: "45s", "5m 03s", "2h 15m".
 * Returns "—" for missing / NaN inputs.
 */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec === undefined || sec === null || Number.isNaN(sec)) return DASH
  const total = Math.max(0, Math.floor(sec))
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m === 0) return `${s}s`
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`
  const h = Math.floor(m / 60)
  return `${h}h ${(m % 60).toString().padStart(2, "0")}m`
}

/**
 * Seconds elapsed since `iso`, clamped at 0. Used by progress / "elapsed
 * since started" displays. Bad input returns 0 so JSX math doesn't break.
 */
export function elapsedSec(iso: string | null | undefined): number {
  if (!iso) return 0
  try {
    return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  } catch {
    return 0
  }
}
