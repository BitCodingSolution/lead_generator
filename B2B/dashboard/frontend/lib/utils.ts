import { clsx, type ClassValue } from "clsx"

// Lightweight cn() — clsx only (tailwind-merge is optional and not installed).
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs)
}

const nf = new Intl.NumberFormat("en-US")
export function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—"
  return nf.format(n)
}

export function pct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—"
  return `${n.toFixed(digits)}%`
}

export function relTime(iso?: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return `${Math.max(1, Math.round(diff))}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)}d ago`
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
}

export function absTime(iso?: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function truncate(s: string | null | undefined, n = 120): string {
  if (!s) return ""
  return s.length > n ? s.slice(0, n - 1) + "…" : s
}
