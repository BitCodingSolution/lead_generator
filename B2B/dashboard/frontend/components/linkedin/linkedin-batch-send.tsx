"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  Rocket, StopCircle, Loader2, Mail, CheckCircle2, XCircle,
  SkipForward, Clock, AlertTriangle, Zap,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api, swrFetcher } from "@/lib/api"

type BatchState = {
  running: boolean
  total: number
  sent: number
  failed: number
  skipped: number
  started_at: string | null
  finished_at: string | null
  current_lead_id: number | null
  current_email: string | null
  last_error: string | null
  source: "manual" | "autopilot" | null
}

type Overview = {
  drafted: number
  quota_used: number
  quota_cap: number
  gmail_connected: boolean
}

const STATE_URL = "/api/linkedin/send/batch/status"
const OVERVIEW_URL = "/api/linkedin/overview"

// Mean inter-send jitter: 75s. Used for the ETA preview in the idle panel.
const AVG_SECS_PER_SEND = 75

export function LinkedInBatchSend() {
  const { data } = useSWR<BatchState>(STATE_URL, swrFetcher, {
    refreshInterval: 2_000,
  })
  const { data: ov } = useSWR<Overview>(OVERVIEW_URL, swrFetcher, {
    refreshInterval: 5_000,
  })
  const running = !!data?.running

  const drafted = ov?.drafted ?? 0
  const quotaLeft = Math.max(0, (ov?.quota_cap ?? 0) - (ov?.quota_used ?? 0))
  const safeMax = Math.max(0, Math.min(drafted, quotaLeft))

  const [count, setCount] = React.useState(5)
  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)

  // Clamp user-typed count to the live safe max so the Start button never
  // fires with a number the backend will immediately shrink. We depend
  // only on safeMax (the external signal) and use a functional setter so
  // we don't need `count` in deps — avoids an extra re-render on every
  // clamp.
  React.useEffect(() => {
    if (safeMax > 0) setCount((n) => (n > safeMax ? safeMax : n))
  }, [safeMax])

  async function onStart() {
    setBusy(true)
    setMsg(null)
    try {
      const res = await api.post<{ started: boolean; total: number }>(
        "/api/linkedin/send/batch",
        { count, source: "manual" },
      )
      setMsg(`Started · ${res.total} leads queued (60-90s jitter between sends)`)
      mutate(STATE_URL)
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onStop() {
    setBusy(true)
    try {
      await api.post("/api/linkedin/send/batch/stop")
      mutate(STATE_URL)
    } finally {
      setBusy(false)
    }
  }

  const doneCount =
    (data?.sent ?? 0) + (data?.failed ?? 0) + (data?.skipped ?? 0)
  const pct =
    data && data.total > 0 ? Math.round((doneCount / data.total) * 100) : 0

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Rocket className="size-4 text-zinc-400" />
          <div className="text-sm font-medium text-zinc-200">Batch send</div>
        </div>
        {running ? (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-violet-500/15 px-2 py-0.5 text-[11px] font-medium text-violet-300">
            <Loader2 className="size-3 animate-spin" />
            Running
          </span>
        ) : (
          <span className="inline-flex items-center rounded-md bg-zinc-700/30 px-2 py-0.5 text-[11px] font-medium text-zinc-400">
            Idle
          </span>
        )}
      </div>

      {!running ? (
        <IdlePanel
          drafted={drafted}
          quotaLeft={quotaLeft}
          safeMax={safeMax}
          count={count}
          setCount={setCount}
          onStart={onStart}
          busy={busy}
          last={data}
        />
      ) : (
        <RunningPanel
          data={data}
          doneCount={doneCount}
          pct={pct}
          onStop={onStop}
          busy={busy}
        />
      )}

      {msg && (
        <div
          className={cn(
            "mt-3 rounded-md px-2.5 py-1.5 text-[11px]",
            msg.toLowerCase().includes("started")
              ? "bg-emerald-500/10 text-emerald-200"
              : "bg-rose-500/10 text-rose-200",
          )}
        >
          {msg}
        </div>
      )}

      {data?.last_error && !running && (
        <div className="mt-2 text-[11px] text-rose-300 truncate">
          Last error: {data.last_error}
        </div>
      )}
    </div>
  )
}

function IdlePanel({
  drafted, quotaLeft, safeMax, count, setCount, onStart, busy, last,
}: {
  drafted: number
  quotaLeft: number
  safeMax: number
  count: number
  setCount: (n: number) => void
  onStart: () => void
  busy: boolean
  last: BatchState | undefined
}) {
  const blocked =
    drafted === 0
      ? {
          tone: "neutral" as const,
          icon: <Mail className="size-3.5" />,
          msg: "No drafts ready. Generate drafts first, then come back.",
        }
      : quotaLeft === 0
        ? {
            tone: "warn" as const,
            icon: <AlertTriangle className="size-3.5" />,
            msg: "Daily cap reached across all accounts. Resumes at midnight.",
          }
        : null

  // Preset chips — cap each to safeMax so "All" doesn't overpromise.
  const presets = React.useMemo(() => {
    const out: { label: string; value: number }[] = []
    for (const p of [5, 10, 20]) {
      if (p <= safeMax) out.push({ label: String(p), value: p })
    }
    if (safeMax > 0 && !out.some((p) => p.value === safeMax)) {
      out.push({ label: `All (${safeMax})`, value: safeMax })
    }
    return out
  }, [safeMax])

  const etaSecs = count * AVG_SECS_PER_SEND

  return (
    <div className="space-y-3">
      {/* budget row: drafted + quota left */}
      <div className="grid grid-cols-2 gap-2">
        <BudgetCell
          label="Drafts ready"
          value={drafted}
          tone={drafted > 0 ? "ok" : "dim"}
          hint={drafted === 0 ? "none yet" : undefined}
        />
        <BudgetCell
          label="Quota left today"
          value={quotaLeft}
          tone={quotaLeft > 0 ? "ok" : "warn"}
          hint={quotaLeft === 0 ? "cap reached" : undefined}
        />
      </div>

      {blocked ? (
        <div
          className={cn(
            "flex items-center gap-2 rounded-md border px-2.5 py-2 text-[11px]",
            blocked.tone === "warn"
              ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
              : "border-zinc-800 bg-zinc-900/40 text-zinc-400",
          )}
        >
          {blocked.icon}
          <span>{blocked.msg}</span>
        </div>
      ) : (
        <>
          {/* count picker */}
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-zinc-500">Send</label>
            <input
              type="number"
              min={1}
              max={safeMax}
              value={count}
              onChange={(e) =>
                setCount(
                  Math.max(1, Math.min(safeMax, parseInt(e.target.value, 10) || 1)),
                )
              }
              className="w-16 rounded border border-zinc-800 bg-zinc-900/60 px-1.5 py-0.5 text-sm text-zinc-100 tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
            />
            <span className="text-[11px] text-zinc-500">
              of {safeMax} available
            </span>
            <div className="ml-auto flex items-center gap-1">
              {presets.map((p) => (
                <button
                  key={p.label}
                  onClick={() => setCount(p.value)}
                  className={cn(
                    "rounded border px-1.5 py-0.5 text-[11px] transition",
                    count === p.value
                      ? "border-[hsl(250_80%_62%)] bg-[hsl(250_80%_62%/0.15)] text-violet-200"
                      : "border-zinc-800 bg-zinc-900/40 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200",
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* ETA preview + Start */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <Zap className="size-3" />
              <span>
                ~{fmtDuration(etaSecs)} · rotated across active Gmail accounts
              </span>
            </div>
            <button
              onClick={onStart}
              disabled={busy || count < 1}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1 text-xs text-white hover:brightness-110 disabled:opacity-50"
            >
              {busy ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Rocket className="size-3" />
              )}
              Start batch
            </button>
          </div>
        </>
      )}

      {/* last run compact pill row */}
      {last?.finished_at && (
        <div className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px]">
          <span className="text-zinc-500">Last run</span>
          <Pill
            icon={<CheckCircle2 className="size-3" />}
            tone="ok"
            label={`${last.sent} sent`}
          />
          {last.failed > 0 && (
            <Pill
              icon={<XCircle className="size-3" />}
              tone="err"
              label={`${last.failed} failed`}
            />
          )}
          {last.skipped > 0 && (
            <Pill
              icon={<SkipForward className="size-3" />}
              tone="dim"
              label={`${last.skipped} skipped`}
            />
          )}
          <Pill
            icon={<Clock className="size-3" />}
            tone="dim"
            label={fmtRelative(last.finished_at)}
          />
          {last.source && <Pill tone="dim" label={last.source} />}
        </div>
      )}
    </div>
  )
}

function RunningPanel({
  data, doneCount, pct, onStop, busy,
}: {
  data: BatchState | undefined
  doneCount: number
  pct: number
  onStop: () => void
  busy: boolean
}) {
  return (
    <div className="space-y-2">
      <div className="h-1.5 bg-zinc-800 rounded overflow-hidden">
        <div
          className="h-full bg-[hsl(250_80%_62%)] transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-xs text-zinc-400 tnum">
        <span>
          {doneCount} / {data?.total ?? 0} ({pct}%)
        </span>
        <span className="flex items-center gap-3">
          <span className="text-emerald-400">✓ {data?.sent ?? 0}</span>
          <span className="text-rose-400">× {data?.failed ?? 0}</span>
          <span className="text-zinc-500">↷ {data?.skipped ?? 0}</span>
        </span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-zinc-500 tnum">
        <span>Elapsed {fmtDuration(elapsedSec(data?.started_at))}</span>
        <span>ETA {fmtDuration(etaSec(data, doneCount))}</span>
      </div>
      {data?.current_email && (
        <div className="flex items-center gap-1.5 text-[11px] text-zinc-500 font-mono truncate">
          <Mail className="size-3" />
          Sending to {data.current_email}…
        </div>
      )}
      <button
        onClick={onStop}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-2.5 py-1 text-xs text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
      >
        <StopCircle className="size-3" />
        Stop
      </button>
    </div>
  )
}

function BudgetCell({
  label, value, tone, hint,
}: {
  label: string
  value: number
  tone: "ok" | "warn" | "dim"
  hint?: string
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-1.5",
        tone === "warn"
          ? "border-amber-500/30 bg-amber-500/5"
          : "border-zinc-800 bg-zinc-900/40",
      )}
    >
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span
          className={cn(
            "text-lg font-semibold tnum",
            tone === "ok"
              ? "text-zinc-100"
              : tone === "warn"
                ? "text-amber-200"
                : "text-zinc-500",
          )}
        >
          {value}
        </span>
        {hint && (
          <span className="text-[10px] text-zinc-500">{hint}</span>
        )}
      </div>
    </div>
  )
}

function Pill({
  icon, tone, label,
}: {
  icon?: React.ReactNode
  tone: "ok" | "err" | "dim"
  label: string
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px]",
        tone === "ok" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
        tone === "err" && "border-rose-500/30 bg-rose-500/10 text-rose-200",
        tone === "dim" && "border-zinc-800 bg-zinc-900/40 text-zinc-400",
      )}
    >
      {icon}
      {label}
    </span>
  )
}

function elapsedSec(iso: string | null | undefined): number {
  if (!iso) return 0
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
}

function etaSec(data: BatchState | undefined, done: number): number {
  if (!data || !data.started_at || done <= 0 || data.total === 0) return 0
  const elapsed = elapsedSec(data.started_at)
  const avgPerLead = elapsed / done
  const remaining = data.total - done
  return Math.max(0, Math.floor(avgPerLead * remaining))
}

function fmtDuration(sec: number): string {
  if (!sec) return "—"
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m === 0) return `${s}s`
  if (m < 60) return `${m}m ${s.toString().padStart(2, "0")}s`
  const h = Math.floor(m / 60)
  return `${h}h ${(m % 60).toString().padStart(2, "0")}m`
}

function fmtRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 0) return "—"
  const m = Math.floor(diff / 60_000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
