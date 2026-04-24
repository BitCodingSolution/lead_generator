import { cn } from "@/lib/utils"

type Tone =
  | "violet"
  | "emerald"
  | "amber"
  | "rose"
  | "sky"
  | "zinc"
  | "indigo"

const TONES: Record<Tone, string> = {
  violet: "bg-[hsl(250_80%_62%/0.12)] text-[hsl(250_80%_75%)] ring-1 ring-inset ring-[hsl(250_80%_62%/0.2)]",
  emerald: "bg-emerald-500/10 text-emerald-400 ring-1 ring-inset ring-emerald-500/20",
  amber: "bg-amber-500/10 text-amber-400 ring-1 ring-inset ring-amber-500/20",
  rose: "bg-rose-500/10 text-rose-400 ring-1 ring-inset ring-rose-500/20",
  sky: "bg-sky-500/10 text-sky-400 ring-1 ring-inset ring-sky-500/20",
  zinc: "bg-zinc-500/10 text-zinc-300 ring-1 ring-inset ring-zinc-500/20",
  indigo: "bg-indigo-500/10 text-indigo-300 ring-1 ring-inset ring-indigo-500/20",
}

function resolveTone(value: string): Tone {
  const v = (value || "").toLowerCase()
  if (["done", "sent", "positive", "delivered", "ok", "healthy"].includes(v)) return "emerald"
  if (["running", "queued", "in_progress", "drafted", "picked"].includes(v)) return "violet"
  if (["objection", "warn", "warning", "neutral", "ooo"].includes(v)) return "amber"
  if (["error", "failed", "negative", "bounce"].includes(v)) return "rose"
  if (["new", "info", "draft"].includes(v)) return "sky"
  if (["replied"].includes(v)) return "indigo"
  return "zinc"
}

export function StatusChip({
  value,
  tone,
  className,
  children,
}: {
  value?: string
  tone?: Tone
  className?: string
  children?: React.ReactNode
}) {
  const t = tone ?? resolveTone(value || "")
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium tracking-tight capitalize",
        TONES[t],
        className,
      )}
    >
      {children ?? value}
    </span>
  )
}
