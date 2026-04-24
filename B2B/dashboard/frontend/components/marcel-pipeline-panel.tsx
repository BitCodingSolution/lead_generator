"use client"

import * as React from "react"
import useSWR, { useSWRConfig } from "swr"
import { motion } from "framer-motion"
import { toast } from "sonner"
import { api, swrFetcher } from "@/lib/api"
import { useJob } from "@/hooks/useJob"
import type { IndustryRow, Stats } from "@/lib/types"
import { StatusChip } from "@/components/status-chip"
import { Terminal } from "@/components/terminal"
import { Stepper, type StepState, type StepperNode } from "@/components/stepper"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  ArrowRight,
  MailCheck,
  Inbox,
  Rocket,
} from "lucide-react"
import { cn, fmt } from "@/lib/utils"

// ---------------- Types ----------------

type ScheduleInfo = {
  in_window: boolean
  seconds_until_open?: number
  seconds_remaining?: number
  next_window_opens_at?: string
  next_window_closes_at?: string
}

type SendMode = "now" | "schedule" | "draft"
type StageKey = "pick" | "generate" | "outlook" | "send"

const STAGES: { key: StageKey; label: string }[] = [
  { key: "pick", label: "Pick" },
  { key: "generate", label: "Generate" },
  { key: "outlook", label: "Outlook" },
  { key: "send", label: "Send" },
]

// ---------------- Panel ----------------
// Marcel-specific outreach pipeline: pick leads from the pre-loaded Marcel
// DB (industry/tier), draft via Claude, stage in Outlook, send. Rendered on
// /sources/marcel so all source-specific controls live with their source.

export function MarcelPipelinePanel() {
  // Data
  const { data: stats } = useSWR<Stats>("/api/stats", swrFetcher, {
    refreshInterval: 30000,
  })
  const { data: industries } = useSWR<IndustryRow[]>(
    "/api/industries",
    swrFetcher,
  )
  const { data: schedule } = useSWR<ScheduleInfo>("/api/schedule", swrFetcher, {
    refreshInterval: 30000,
  })

  // Form state
  const [industry, setIndustry] = React.useState("")
  const [count, setCount] = React.useState<number>(20)
  const [tier, setTier] = React.useState<string>("")  // "" = auto/any
  const [sendMode, setSendMode] = React.useState<SendMode>("schedule")
  const [noJitter, setNoJitter] = React.useState(false)

  // Jobs
  const [activeJobId, setActiveJobId] = React.useState<string | null>(null)
  const [lastCompletedJobId, setLastCompletedJobId] = React.useState<string | null>(null)
  const [secondaryLabel, setSecondaryLabel] = React.useState<string | null>(null)

  const displayedJobId = activeJobId || lastCompletedJobId
  const { job } = useJob(displayedJobId)

  const jobRunning =
    job?.status === "running" || job?.status === "queued"

  const { mutate: swrMutate } = useSWRConfig()

  // When a running job finishes, demote to lastCompleted + refresh affected data
  React.useEffect(() => {
    if (!activeJobId) return
    if (job?.status === "done" || job?.status === "error") {
      setLastCompletedJobId(activeJobId)
      setActiveJobId(null)
      if (job.status === "done") {
        toast.success("Pipeline finished", { description: activeJobId })
      } else {
        toast.error("Pipeline failed", { description: activeJobId })
      }
      // Revalidate anything the pipeline may have changed
      swrMutate("/api/stats")
      swrMutate("/api/pending-drafts")
      swrMutate("/api/industries")
      swrMutate("/api/recent-sent")
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status])

  // Derived
  const daily_quota = stats?.daily_quota ?? 25
  const remaining = stats?.remaining_today ?? 0
  const used_today = Math.max(0, daily_quota - remaining)

  const selectedIndustry = React.useMemo(
    () => industries?.find((i) => i.industry === industry),
    [industries, industry],
  )

  const countExceedsQuota = sendMode === "now" && count > remaining
  const countExceedsAvailable =
    selectedIndustry ? count > selectedIndustry.available : false

  const canRun =
    !!industry &&
    count > 0 &&
    !jobRunning &&
    !countExceedsQuota &&
    !countExceedsAvailable

  // Parse stages from logs
  const stageNodes = React.useMemo<StepperNode[]>(() => {
    const lines: string[] = Array.isArray(job?.logs)
      ? job.logs
      : (typeof job?.logs === "string" ? job.logs.split("\n") : [])
    const seen: StageKey[] = []
    let done = false

    // Segment lines by current stage so we can count per-stage progress
    const segments: Record<StageKey, string[]> = {
      pick: [], generate: [], outlook: [], send: [],
    }
    let cur: StageKey | null = null

    for (const line of lines) {
      const m = line.match(/\[STAGE\]\s+(\w+)/)
      if (m) {
        const tag = m[1].toLowerCase()
        let key: StageKey | null = null
        if (tag === "pick") key = "pick"
        else if (tag === "generate") key = "generate"
        else if (tag === "outlook") key = "outlook"
        else if (tag === "send" || tag === "schedule" || tag === "skip")
          key = "send"
        if (key) {
          cur = key
          if (seen[seen.length - 1] !== key) seen.push(key)
        }
        continue
      }
      if (/\[DONE\]\s+pipeline\s+complete/i.test(line)) done = true
      if (cur) segments[cur].push(line)
    }

    const total = Number(count) || 0

    // pick: "Picked N leads" → done=N once seen, else 0 while running
    let pickDone = 0
    for (const ln of segments.pick) {
      const pm = ln.match(/Picked\s+(\d+)\s+leads/i)
      if (pm) pickDone = Math.max(pickDone, Number(pm[1]))
    }
    // generate / outlook: count "] OK" markers within segment
    const countOk = (seg: string[]) =>
      seg.reduce((n, ln) => n + (/\]\s+OK\b/.test(ln) || /\]\s+\S+\s+OK\b/.test(ln) ? 1 : 0), 0)
    const genDone = countOk(segments.generate)
    const outDone = countOk(segments.outlook)
    // send: prefer "[n/m] SENT" latest, else count SENT lines
    let sendDone = 0
    for (const ln of segments.send) {
      const sm = ln.match(/\[(\d+)\/\d+\]\s+SENT\b/)
      if (sm) sendDone = Math.max(sendDone, Number(sm[1]))
      else if (/\bSENT\b/.test(ln)) sendDone += 1
    }

    const counts: Record<StageKey, number> = {
      pick: pickDone,
      generate: genDone,
      outlook: outDone,
      send: sendDone,
    }

    const isError = job?.status === "error"
    const current = seen[seen.length - 1]
    const currentIdx = current ? STAGES.findIndex((s) => s.key === current) : -1

    return STAGES.map((s, idx) => {
      let state: StepState = "pending"
      if (done) {
        state = "done"
      } else if (currentIdx === -1) {
        state = "pending"
      } else if (idx < currentIdx) {
        state = "done"
      } else if (idx === currentIdx) {
        if (isError) state = "error"
        else if (job?.status === "done") state = "done"
        else state = "active"
      }
      const node: StepperNode = { key: s.key, label: s.label, state }
      if (total > 0) {
        const d = state === "done"
          ? total
          : state === "pending"
            ? 0
            : Math.min(counts[s.key], total)
        node.count = { done: d, total }
      }
      return node
    })
  }, [job?.logs, job?.status, count])

  // ---- Smooth pipeline progress ------------------------------------------
  // Top bar jumps 0→25→50→75→100 as stages flip. Smooth it out by including
  // the active stage's sub-progress (from log-parsed counts) and, when those
  // haven't ticked yet, a time-based extrapolation against typical per-stage
  // durations.
  const TYPICAL_STAGE_SECS: Record<StageKey, number> = {
    pick: 3,
    generate: 30,
    outlook: 15,
    send: 60,
  }
  const [smoothPct, setSmoothPct] = React.useState(0)
  const activeStageRef = React.useRef<{ key: StageKey; at: number } | null>(
    null,
  )

  React.useEffect(() => {
    const activeNode = stageNodes.find((n) => n.state === "active")
    const doneCount = stageNodes.filter((n) => n.state === "done").length
    const total = stageNodes.length
    const errored = stageNodes.some((n) => n.state === "error")
    if (!activeNode || errored || job?.status === "done") {
      // Settle to the discrete target
      setSmoothPct(
        total > 0
          ? Math.min(100, (doneCount / total) * 100) + (errored ? 0 : 0)
          : 0,
      )
      activeStageRef.current = null
      return
    }
    const key = activeNode.key as StageKey
    if (!activeStageRef.current || activeStageRef.current.key !== key) {
      activeStageRef.current = { key, at: Date.now() }
    }
    const tick = () => {
      const base = (doneCount / total) * 100
      const stageSize = 100 / total
      // Prefer parsed sub-progress if we have any events yet.
      const parsedFrac =
        activeNode.count && activeNode.count.total > 0
          ? activeNode.count.done / activeNode.count.total
          : 0
      const elapsed =
        (Date.now() - (activeStageRef.current?.at ?? Date.now())) / 1000
      const timeFrac = Math.min(
        0.95,
        elapsed / TYPICAL_STAGE_SECS[key],
      )
      // Whichever is further along — cap at 95% until actual completion.
      const frac = Math.min(0.95, Math.max(parsedFrac, timeFrac))
      setSmoothPct(Math.min(100, base + frac * stageSize))
    }
    tick()
    const iv = setInterval(tick, 200)
    return () => clearInterval(iv)
  }, [stageNodes, job?.status])

  // ---------------- Actions ----------------

  async function runPipeline() {
    if (!canRun) return
    // Pre-flight: verify Bridge/Outlook/DB before leads get marked 'Picked'.
    // Backend also enforces this, but failing here gives a specific toast
    // per missing dependency instead of a generic 503.
    try {
      const pf = await api.get<{
        ok: boolean
        checks: { key: string; ok: boolean; error?: string | null }[]
      }>("/api/actions/preflight")
      if (!pf.ok) {
        const bad = pf.checks.filter((c) => !c.ok)
        const summary = bad.map((c) => c.error || c.key).join(" · ")
        toast.error("Pre-flight failed", { description: summary })
        return
      }
    } catch (e) {
      toast.error("Pre-flight check failed", {
        description: e instanceof Error ? e.message : String(e),
      })
      return
    }
    try {
      const body: {
        industry: string
        count: number
        tier?: number
        send_mode: SendMode
        no_jitter: boolean
      } = {
        industry,
        count: Number(count) || 0,
        send_mode: sendMode,
        no_jitter: noJitter,
      }
      if (tier) body.tier = Number(tier)
      const res = await api.post<{ job_id: string }>(
        "/api/actions/run-pipeline",
        body,
      )
      if (!res.job_id) throw new Error("No job_id returned")
      setSecondaryLabel(null)
      setActiveJobId(res.job_id)
      setLastCompletedJobId(null)
      toast.success("Pipeline started", { description: res.job_id })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error("Failed to start pipeline", { description: msg })
    }
  }

  async function runSecondary(path: string, label: string) {
    if (jobRunning) return
    try {
      const res = await api.post<{ job_id: string }>(path, {})
      if (!res.job_id) throw new Error("No job_id returned")
      setSecondaryLabel(label)
      setActiveJobId(res.job_id)
      setLastCompletedJobId(null)
      toast.success(`${label} started`, { description: res.job_id })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      toast.error(`Failed: ${label}`, { description: msg })
    }
  }

  // ---------------- Render ----------------

  return (
    <div className="space-y-6">
      <PendingDraftsBanner onAction={runSecondary} jobRunning={jobRunning} />

      {/* ===== MARCEL HERO CARD (DB-picked pipeline) ===== */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22 }}
        className="relative overflow-hidden rounded-2xl border border-zinc-800/80 bg-[#18181b] p-6"
      >
        {/* Violet wash */}
        <div
          className="pointer-events-none absolute -top-24 -right-24 size-64 rounded-full opacity-60 blur-3xl"
          style={{
            background:
              "radial-gradient(closest-side, hsl(250 80% 55% / 0.35), transparent 70%)",
          }}
        />

        <div className="relative z-10">
          <div className="flex items-center gap-2 mb-5">
            <div className="size-8 rounded-md bg-gradient-to-br from-[hsl(250_80%_62%/0.25)] to-transparent border border-zinc-800 flex items-center justify-center text-[hsl(250_80%_80%)]">
              <Rocket className="size-4" />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
                Run a campaign
              </div>
              <div className="text-sm font-semibold text-zinc-100">
                One click · Pick → Generate → Outlook → Send
              </div>
            </div>
          </div>

          {/* Inputs */}
          <div className="grid grid-cols-1 md:grid-cols-[2fr_1fr_1fr_1.2fr] gap-3">
            <Field label="Industry">
              <Select
                value={industry || "__none"}
                onValueChange={(v) => {
                  const picked = v === "__none" ? "" : v
                  setIndustry(picked)
                  // Auto-sync tier to whatever tier the chosen industry belongs to
                  const row = (industries || []).find((i) => i.industry === picked)
                  if (row?.tier) setTier(String(row.tier))
                  else setTier("")
                }}
              >
                <SelectTrigger className="!w-full bg-zinc-900/60 border-zinc-800 h-10">
                  <SelectValue placeholder="Choose an industry…" />
                </SelectTrigger>
                <SelectContent className="max-h-[320px]">
                  {(industries || []).map((i) => (
                    <SelectItem key={i.industry} value={i.industry}>
                      <span className="inline-flex items-center gap-2">
                        <span>{i.industry}</span>
                        <span className="text-zinc-500 text-xs">
                          — {fmt(i.available)} available
                        </span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Count">
              <Input
                type="number"
                min={1}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                className="bg-zinc-900/60 border-zinc-800 h-10 tnum"
              />
            </Field>

            <Field label="Tier">
              <div className="flex gap-1.5">
                {[
                  { v: "", l: "Auto" },
                  { v: "1", l: "Tier 1" },
                  { v: "2", l: "Tier 2" },
                ].map((t) => (
                  <button
                    key={t.v || "auto"}
                    type="button"
                    onClick={() => setTier(t.v)}
                    className={cn(
                      "flex-1 h-10 rounded-md text-xs border transition-colors font-medium",
                      tier === t.v
                        ? "border-[hsl(250_80%_62%)] bg-[hsl(250_80%_62%/0.15)] text-[hsl(250_80%_85%)]"
                        : "border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:text-zinc-200",
                    )}
                  >
                    {t.l}
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Send mode">
              <Select
                value={sendMode}
                onValueChange={(v) => setSendMode(v as SendMode)}
              >
                <SelectTrigger className="!w-full bg-zinc-900/60 border-zinc-800 h-10">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="schedule">
                    Schedule · wait for window
                  </SelectItem>
                  <SelectItem value="now">Send now</SelectItem>
                  <SelectItem value="draft">
                    Draft only · stop after Outlook
                  </SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          {/* Inline stat row */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <QuotaBadge used={used_today} total={daily_quota} />
            <WindowBadge schedule={schedule} />
            {selectedIndustry && (
              <Badge tone="zinc">
                <span className="text-zinc-500">Available:</span>{" "}
                <span className="text-zinc-200 tnum">
                  {fmt(selectedIndustry.available)}
                </span>
              </Badge>
            )}
            <label className="ml-auto inline-flex items-center gap-2 text-[11px] text-zinc-400 select-none">
              <input
                type="checkbox"
                checked={noJitter}
                onChange={(e) => setNoJitter(e.target.checked)}
                className="accent-[hsl(250_80%_62%)]"
              />
              No jitter between sends
            </label>
          </div>

          {/* Warnings */}
          {(countExceedsQuota || countExceedsAvailable) && (
            <div className="mt-3 text-[11px] text-amber-300/90">
              {countExceedsQuota && (
                <div>
                  · Count exceeds today&apos;s quota ({remaining} remaining).
                </div>
              )}
              {countExceedsAvailable && (
                <div>
                  · Count exceeds available leads in this industry (
                  {fmt(selectedIndustry?.available ?? 0)}).
                </div>
              )}
            </div>
          )}

          {/* Run button */}
          <div className="mt-5 flex items-center justify-end">
            <Button
              size="lg"
              onClick={runPipeline}
              disabled={!canRun}
              className={cn(
                "h-11 px-6 text-sm font-semibold tracking-tight",
                "bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white",
                "shadow-[0_0_20px_-4px_hsl(250_80%_62%/0.6)]",
                "disabled:opacity-50 disabled:shadow-none",
              )}
            >
              {jobRunning ? "Pipeline running…" : "Run Pipeline"}
              <ArrowRight className="size-4" />
            </Button>
          </div>
        </div>
      </motion.div>

      {/* ===== STATUS BAR + STEPPER ===== */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22, delay: 0.05 }}
        className="rounded-xl border border-zinc-800/80 bg-[#18181b] overflow-hidden"
      >
        {(() => {
          const doneCount = stageNodes.filter((n) => n.state === "done").length
          const activeNode = stageNodes.find((n) => n.state === "active")
          const errorNode = stageNodes.find((n) => n.state === "error")
          const total = stageNodes.length
          // Smooth pct (time + log-parsed sub-progress interpolation).
          const pct = Math.round(smoothPct)
          const currentLabel = errorNode
            ? `Error at ${errorNode.label}`
            : activeNode
              ? `Working: ${activeNode.label}`
              : doneCount === total && job?.status === "done"
                ? "Pipeline complete"
                : job?.status === "running"
                  ? "Starting..."
                  : "Idle — configure above and click Run"
          const barColor = errorNode
            ? "bg-rose-500"
            : doneCount === total && job?.status === "done"
              ? "bg-emerald-500"
              : "bg-[hsl(250_80%_62%)]"
          return (
            <>
              <div className="flex items-center justify-between px-5 pt-4 pb-2">
                <div className="flex items-center gap-2">
                  <div
                    className={cn(
                      "size-2 rounded-full",
                      errorNode
                        ? "bg-rose-400"
                        : activeNode
                          ? "bg-[hsl(250_80%_62%)] animate-pulse"
                          : doneCount === total && job?.status === "done"
                            ? "bg-emerald-400"
                            : "bg-zinc-600",
                    )}
                  />
                  <span className="text-sm font-medium text-zinc-200">
                    {currentLabel}
                  </span>
                </div>
                <span className="text-xs text-zinc-500 tnum">
                  {doneCount} / {total} stages · {pct}%
                </span>
              </div>
              <div className="h-1 bg-zinc-800/70">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${pct}%` }}
                  transition={{ duration: 0.4, ease: "easeOut" }}
                  className={cn("h-full", barColor)}
                />
              </div>
              <div className="px-5 py-6">
                <Stepper nodes={stageNodes} />
              </div>
            </>
          )
        })()}
      </motion.div>

      {/* ===== TERMINAL ===== */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22, delay: 0.09 }}
        className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4"
      >
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-[0.15em] text-zinc-500">
              {secondaryLabel ? secondaryLabel : "Pipeline"} · live logs
            </span>
            {job?.status && <StatusChip value={job.status} />}
          </div>
          <div className="text-[11px] text-zinc-600 font-mono truncate max-w-[60%]">
            {displayedJobId || "no job yet"}
          </div>
        </div>
        <Terminal
          logs={job?.logs || ""}
          status={job?.status}
          label={job?.label || "pipeline.log"}
          height={400}
        />
      </motion.div>

      {/* ===== SECONDARY FOOTER ===== */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button
          onClick={() => runSecondary("/api/actions/sync-sent", "Sync Outlook Sent")}
          disabled={jobRunning}
          size="sm"
          variant="outline"
          className="border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900"
        >
          <MailCheck className="size-3.5" />
          Sync Outlook Sent
        </Button>
        <Button
          onClick={() => runSecondary("/api/actions/scan-replies", "Scan Inbox replies")}
          disabled={jobRunning}
          size="sm"
          variant="outline"
          className="border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900"
        >
          <Inbox className="size-3.5" />
          Scan Inbox replies
        </Button>
      </div>
    </div>
  )
}

// ---------------- Small widgets ----------------

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.15em] text-zinc-500 mb-1.5">
        {label}
      </div>
      {children}
    </div>
  )
}

function Badge({
  children,
  tone = "zinc",
}: {
  children: React.ReactNode
  tone?: "zinc" | "emerald" | "amber"
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px]",
        tone === "zinc" && "border-zinc-800 bg-zinc-900/40 text-zinc-300",
        tone === "emerald" &&
          "border-emerald-600/40 bg-emerald-500/10 text-emerald-300",
        tone === "amber" &&
          "border-amber-600/40 bg-amber-500/10 text-amber-300",
      )}
    >
      {children}
    </div>
  )
}

function PendingDraftsBanner({
  onAction,
  jobRunning,
}: {
  onAction: (path: string, label: string) => Promise<void> | void
  jobRunning: boolean
}) {
  const { data, mutate } = useSWR<{ count: number }>(
    "/api/pending-drafts",
    swrFetcher,
    { refreshInterval: 15000 },
  )
  const count = data?.count ?? 0
  if (count === 0) return null

  const handleSend = async () => {
    try {
      const res = await api.post<{ job_id: string; count: number }>(
        "/api/actions/send-all-drafts",
        { mode: "now", no_jitter: false },
      )
      toast.success(`Sending ${res.count} drafts`, { description: res.job_id })
      setTimeout(() => mutate(), 3000)
    } catch (e) {
      toast.error(
        "Send failed",
        { description: e instanceof Error ? e.message : String(e) },
      )
    }
  }

  const handleClear = async () => {
    if (!confirm(`Delete ${count} pending drafts from Outlook? (not sent)`)) return
    try {
      const res = await api.post<{ deleted_outlook: number }>(
        "/api/actions/clear-drafts",
        {},
      )
      toast.success(`Cleared ${res.deleted_outlook} drafts`)
      mutate()
    } catch (e) {
      toast.error(
        "Clear failed",
        { description: e instanceof Error ? e.message : String(e) },
      )
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-amber-500/40 bg-amber-500/5 px-4 py-3 flex items-center justify-between gap-4"
    >
      <div className="flex items-center gap-3">
        <div className="size-8 rounded-md bg-amber-500/15 border border-amber-500/30 flex items-center justify-center">
          <MailCheck className="size-4 text-amber-400" />
        </div>
        <div>
          <div className="text-sm font-medium text-amber-100">
            {count} draft{count === 1 ? "" : "s"} pending in Outlook
          </div>
          <div className="text-xs text-amber-300/70">
            These are ready to go — send them, or clear them to start fresh.
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button
          onClick={handleSend}
          disabled={jobRunning}
          size="sm"
          className="bg-[hsl(250_80%_62%)] hover:bg-[hsl(250_80%_58%)] text-white h-8"
        >
          Send all
        </Button>
        <Button
          onClick={handleClear}
          disabled={jobRunning}
          size="sm"
          variant="outline"
          className="border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900 h-8"
        >
          Clear
        </Button>
      </div>
    </motion.div>
  )
}

function QuotaBadge({ used, total }: { used: number; total: number }) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900/40 px-2.5 py-1">
      <span className="text-[10px] uppercase tracking-[0.15em] text-zinc-500">
        Quota
      </span>
      <span className="tnum text-[11px] text-zinc-200 font-medium">
        {used}
        <span className="text-zinc-500">/{total}</span>
      </span>
      <span className="w-16 h-1 rounded-full bg-zinc-800 overflow-hidden">
        <span
          className="block h-full bg-[hsl(250_80%_62%)] transition-all"
          style={{ width: `${pct}%` }}
        />
      </span>
    </div>
  )
}

function WindowBadge({ schedule }: { schedule?: ScheduleInfo }) {
  if (!schedule) {
    return <Badge tone="zinc">Window: …</Badge>
  }
  const isOpen = !!schedule.in_window
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px]",
        isOpen
          ? "border-emerald-600/40 bg-emerald-500/10 text-emerald-300"
          : "border-amber-600/40 bg-amber-500/10 text-amber-300",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          isOpen
            ? "bg-emerald-400 shadow-[0_0_6px_1px_rgba(16,185,129,0.6)]"
            : "bg-amber-400",
        )}
      />
      <span className="uppercase tracking-[0.12em] text-[10px] opacity-80">
        Window
      </span>
      {isOpen ? (
        <span>open · closes in {fmtDuration(schedule.seconds_remaining)}</span>
      ) : (
        <span>
          closed · opens in {fmtDuration(schedule.seconds_until_open)}
        </span>
      )}
    </div>
  )
}


function fmtDuration(secs?: number): string {
  if (secs === undefined || secs === null || Number.isNaN(secs)) return "—"
  const s = Math.max(0, Math.floor(secs))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}
