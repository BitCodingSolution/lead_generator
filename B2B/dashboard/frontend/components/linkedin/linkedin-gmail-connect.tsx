"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  Mail, CheckCircle2, XCircle, Loader2, ShieldCheck, Trash2, Plus,
  Pause, Play, ExternalLink, Flame, RotateCcw, Settings2, Save,
} from "lucide-react"
import { api, swrFetcher } from "@/lib/api"
import { cn } from "@/lib/utils"
import { fmtRelative } from "@/lib/datetime"

type Account = {
  id: number
  email: string
  display_name: string | null
  daily_cap: number
  sent_today: number
  sent_date: string | null
  last_sent_at: string | null
  status: "active" | "paused"
  warmup_enabled: number
  warmup_start_date: string | null
  warmup_day: number
  effective_cap: number
  consecutive_failures: number
  bounce_count_today: number
  paused_reason: string | null
  connected_at: string
  last_verified_at: string | null
  health_score?: number
  health_30d?: {
    sent: number
    replied: number
    bounced: number
    bounce_rate_pct: number
  }
}

type AccountsResp = {
  rows: Account[]
  total_sent_today: number
  total_daily_cap: number
}

const ENDPOINT = "/api/linkedin/gmail/accounts"

export function LinkedInGmailConnect() {
  const { data } = useSWR<AccountsResp>(ENDPOINT, swrFetcher, {
    refreshInterval: 30_000,
  })
  const accounts = data?.rows ?? []
  const [showAdd, setShowAdd] = React.useState(false)

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Mail className="size-4 text-zinc-400" />
          <div className="text-sm font-medium text-zinc-200">Gmail accounts</div>
          <span className="text-[11px] text-zinc-500 tnum">
            · {data?.total_sent_today ?? 0} / {data?.total_daily_cap ?? 0} today
          </span>
        </div>
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
        >
          <Plus className="size-3" />
          Add Gmail
        </button>
      </div>

      {accounts.length === 0 && !showAdd && (
        <div className="text-xs text-zinc-500 pt-1">
          No Gmail accounts connected. Click <strong>Add Gmail</strong> to
          connect one. You can add up to multiple accounts — sends rotate
          round-robin so your daily volume scales with account count.
        </div>
      )}

      <div className="space-y-2">
        {accounts.map((a) => (
          <AccountRow key={a.id} account={a} />
        ))}
      </div>

      {showAdd && <AddAccountForm onDone={() => setShowAdd(false)} />}

      <WarmupCurveEditor />
    </div>
  )
}

type WarmupCurve = { stages: [number, number][]; default: [number, number][] }

function WarmupCurveEditor() {
  const { data } = useSWR<WarmupCurve>(
    "/api/linkedin/gmail/warmup/curve",
    swrFetcher,
  )
  const [open, setOpen] = React.useState(false)
  const [stages, setStages] = React.useState<[number, number][]>([])
  const [saving, setSaving] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (data?.stages) setStages(data.stages)
  }, [data])

  function updateStage(idx: number, which: 0 | 1, val: number) {
    setStages((prev) => {
      const next = prev.map((p) => [...p] as [number, number])
      next[idx][which] = Math.max(1, val || 1)
      return next
    })
  }
  function addStage() {
    setStages((prev) => {
      const last = prev[prev.length - 1] ?? [1, 5]
      return [...prev, [last[0] + 7, last[1] + 10]]
    })
  }
  function removeStage(idx: number) {
    setStages((prev) => prev.filter((_, i) => i !== idx))
  }
  function resetDefault() {
    if (data?.default) setStages(data.default)
  }

  async function save() {
    setSaving(true)
    setMsg(null)
    try {
      await api.post("/api/linkedin/gmail/warmup/curve", { stages })
      mutate("/api/linkedin/gmail/warmup/curve")
      mutate(ENDPOINT)
      setMsg("Saved")
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!data) return null

  const defaultStr = JSON.stringify(data.default)
  const currentStr = JSON.stringify(stages)
  const isDefault = defaultStr === currentStr
  const dirty = defaultStr !== JSON.stringify(data.stages) || currentStr !== JSON.stringify(data.stages)

  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/40 mt-1">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200"
      >
        <span className="inline-flex items-center gap-1.5">
          <Settings2 className="size-3.5" />
          Warmup curve
          {!isDefault && (
            <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-300">
              custom
            </span>
          )}
        </span>
        <span className="text-zinc-600">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="border-t border-zinc-800/80 p-3 space-y-2">
          <div className="text-[11px] text-zinc-500">
            Each stage: <strong>up to N sends/day</strong> until day D. Applies
            to all accounts with warmup enabled. Last stage&apos;s cap ends
            when day D is reached, then the account&apos;s own daily_cap takes
            over.
          </div>
          <div className="space-y-1">
            {stages.map((s, idx) => (
              <div key={idx} className="flex items-center gap-2 text-[11px]">
                <span className="text-zinc-500 w-14">Until day</span>
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={s[0]}
                  onChange={(e) => updateStage(idx, 0, Number(e.target.value))}
                  className="w-16 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-zinc-200 text-right tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
                />
                <span className="text-zinc-500">cap</span>
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={s[1]}
                  onChange={(e) => updateStage(idx, 1, Number(e.target.value))}
                  className="w-16 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-zinc-200 text-right tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
                />
                <span className="text-zinc-600 text-[10px]">/day</span>
                <button
                  onClick={() => removeStage(idx)}
                  disabled={stages.length <= 1}
                  className="ml-auto rounded border border-zinc-800 p-1 text-zinc-500 hover:text-rose-300 disabled:opacity-30"
                  title="Remove stage"
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={addStage}
              className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-zinc-800"
            >
              <Plus className="size-3" /> Add stage
            </button>
            <button
              onClick={resetDefault}
              disabled={isDefault}
              className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
            >
              <RotateCcw className="size-3" /> Default
            </button>
            <button
              onClick={save}
              disabled={saving || !dirty}
              className="ml-auto inline-flex items-center gap-1 rounded-md bg-[hsl(250_80%_62%)] px-2 py-0.5 text-[11px] text-white hover:brightness-110 disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Save className="size-3" />
              )}
              Save curve
            </button>
          </div>
          {msg && (
            <div className="text-[11px] text-zinc-400">{msg}</div>
          )}
        </div>
      )}
    </div>
  )
}

function AccountRow({ account }: { account: Account }) {
  const [busy, setBusy] = React.useState<"" | "pause" | "resume" | "test" | "remove" | "cap" | "warmup">("")
  const [msg, setMsg] = React.useState<string | null>(null)
  const [cap, setCap] = React.useState(account.daily_cap)

  const warmupActive = !!account.warmup_enabled && account.effective_cap < account.daily_cap
  const shownCap = warmupActive ? account.effective_cap : account.daily_cap
  const pct = shownCap > 0
    ? Math.min(100, (account.sent_today / shownCap) * 100)
    : 0

  async function toggle() {
    setBusy(account.status === "active" ? "pause" : "resume")
    try {
      const path = account.status === "active" ? "pause" : "resume"
      await api.post(`/api/linkedin/gmail/accounts/${account.id}/${path}`)
      mutate(ENDPOINT)
    } finally {
      setBusy("")
    }
  }

  async function test() {
    setBusy("test")
    setMsg(null)
    try {
      const res = await api.post<{ smtp_ok: boolean; imap_ok: boolean }>(
        `/api/linkedin/gmail/test?account_id=${account.id}`,
      )
      setMsg(`SMTP ${res.smtp_ok ? "✓" : "✗"} · IMAP ${res.imap_ok ? "✓" : "✗"}`)
      mutate(ENDPOINT)
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy("")
    }
  }

  async function remove() {
    if (!confirm(`Remove ${account.email}? Sending from this inbox will stop.`)) return
    setBusy("remove")
    try {
      await api.delete(`/api/linkedin/gmail/accounts/${account.id}`)
      mutate(ENDPOINT)
    } finally {
      setBusy("")
    }
  }

  async function saveCap() {
    if (cap === account.daily_cap) return
    setBusy("cap")
    try {
      await api.post(`/api/linkedin/gmail/accounts/${account.id}/cap`, {
        daily_cap: cap,
      })
      mutate(ENDPOINT)
    } finally {
      setBusy("")
    }
  }

  async function toggleWarmup() {
    setBusy("warmup")
    try {
      await api.post(`/api/linkedin/gmail/accounts/${account.id}/warmup`, {
        enabled: !account.warmup_enabled,
      })
      mutate(ENDPOINT)
    } finally {
      setBusy("")
    }
  }

  async function resetWarmup() {
    if (!confirm(
      `Reset warmup clock to day 0 for ${account.email}?\n\n` +
      `The cap will drop back to the first stage and ramp up again over 14 days. ` +
      `Only do this if deliverability dropped and you want a gentle restart.`,
    )) return
    setBusy("warmup")
    try {
      await api.post(`/api/linkedin/gmail/accounts/${account.id}/warmup`, {
        enabled: true,
        reset_start: true,
      })
      mutate(ENDPOINT)
    } finally {
      setBusy("")
    }
  }

  const isPaused = account.status === "paused"

  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/40 p-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          {isPaused ? (
            <XCircle className="size-3.5 text-zinc-500" />
          ) : (
            <CheckCircle2 className="size-3.5 text-emerald-400" />
          )}
          <div className="text-sm font-mono text-zinc-200">{account.email}</div>
          {account.health_score != null && (
            <HealthBadge
              score={account.health_score}
              stats={account.health_30d}
            />
          )}
          {isPaused && (
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px]",
                account.paused_reason
                  ? "bg-rose-500/20 text-rose-300"
                  : "bg-zinc-700/40 text-zinc-300",
              )}
              title={account.paused_reason || "Manually paused"}
            >
              {account.paused_reason ? "auto-paused" : "paused"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={toggle}
            disabled={!!busy}
            title={isPaused ? "Resume" : "Pause"}
            className="rounded-md border border-zinc-700 bg-zinc-800/60 p-1 text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
          >
            {busy === "pause" || busy === "resume" ? (
              <Loader2 className="size-3 animate-spin" />
            ) : isPaused ? (
              <Play className="size-3" />
            ) : (
              <Pause className="size-3" />
            )}
          </button>
          <button
            onClick={test}
            disabled={!!busy}
            title="Verify SMTP + IMAP"
            className="rounded-md border border-zinc-700 bg-zinc-800/60 p-1 text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
          >
            {busy === "test" ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <ShieldCheck className="size-3" />
            )}
          </button>
          <button
            onClick={remove}
            disabled={!!busy}
            title="Remove this account"
            className="rounded-md border border-rose-500/40 bg-rose-500/10 p-1 text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
          >
            {busy === "remove" ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Trash2 className="size-3" />
            )}
          </button>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-3 text-[11px] text-zinc-500">
        <div className="flex-1">
          <div className="flex items-center justify-between mb-1">
            <span className="tnum">
              {account.sent_today} / {shownCap} today
              {warmupActive && (
                <span className="text-zinc-600"> (cap {account.daily_cap})</span>
              )}
            </span>
            <span className="tnum">{Math.round(pct)}%</span>
          </div>
          <div className="h-1 rounded bg-zinc-800 overflow-hidden">
            <div
              className={cn(
                "h-full",
                warmupActive
                  ? "bg-amber-500/80"
                  : "bg-[hsl(250_80%_62%)]",
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <div className="flex items-center gap-1">
          <span>cap</span>
          <input
            type="number"
            min={1}
            max={500}
            value={cap}
            onChange={(e) => setCap(Math.max(1, Number(e.target.value) || 1))}
            onBlur={saveCap}
            className="w-14 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-[11px] text-zinc-200 text-right tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
          />
        </div>
      </div>

      <div className="mt-2 flex items-center justify-between text-[11px]">
        <button
          onClick={toggleWarmup}
          disabled={!!busy}
          className={cn(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors",
            account.warmup_enabled
              ? "bg-amber-500/15 text-amber-300 hover:bg-amber-500/25"
              : "bg-zinc-800/60 text-zinc-400 hover:bg-zinc-800",
          )}
          title={
            account.warmup_enabled
              ? "Warmup ON — cap ramps 5→10→20→35→full over 14 days"
              : "Warmup OFF — full cap used immediately"
          }
        >
          {busy === "warmup" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Flame className="size-3" />
          )}
          {account.warmup_enabled
            ? warmupActive
              ? `Warmup day ${account.warmup_day + 1}/14`
              : "Warmed up"
            : "Warmup off"}
        </button>
        <div className="flex items-center gap-2">
          {account.warmup_enabled && (
            <button
              onClick={resetWarmup}
              disabled={!!busy}
              title="Reset warmup clock to day 0"
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50"
            >
              <RotateCcw className="size-3" />
              reset
            </button>
          )}
          {account.last_sent_at && (
            <span className="text-zinc-600 tnum">
              last send {fmtRelative(account.last_sent_at)}
            </span>
          )}
        </div>
      </div>

      {account.paused_reason && (
        <div className="mt-2 rounded border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          Auto-paused: {account.paused_reason}. Click ▶ to resume (clears the
          failure counter).
        </div>
      )}

      {!account.paused_reason && (account.consecutive_failures > 0
        || account.bounce_count_today > 0) && (
        <div className="mt-2 text-[11px] text-amber-300/90">
          ⚠ {account.consecutive_failures} consecutive send failure
          {account.consecutive_failures === 1 ? "" : "s"}
          {account.bounce_count_today > 0 && (
            <>
              {" · "}
              {account.bounce_count_today} bounce
              {account.bounce_count_today === 1 ? "" : "s"} today
            </>
          )}{" "}
          (auto-pause at 3)
        </div>
      )}

      {msg && (
        <div className="mt-2 text-[11px] text-zinc-400">{msg}</div>
      )}
    </div>
  )
}

function AddAccountForm({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = React.useState("")
  const [pw, setPw] = React.useState("")
  const [cap, setCap] = React.useState(50)
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      await api.post("/api/linkedin/gmail/connect", {
        email: email.trim(),
        app_password: pw.replace(/\s+/g, ""),
        daily_cap: cap,
      })
      mutate(ENDPOINT)
      onDone()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-lg border border-dashed border-zinc-700 bg-zinc-900/40 p-3 space-y-2"
    >
      <div>
        <label className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
          Gmail address
        </label>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@gmail.com"
          className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
      </div>
      <div>
        <label className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
          App password (16 chars)
        </label>
        <input
          type="password"
          required
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="xxxx xxxx xxxx xxxx"
          className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
        <a
          href="https://myaccount.google.com/apppasswords"
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-flex items-center gap-1 text-[11px] text-[hsl(250_80%_72%)] hover:underline"
        >
          Generate App Password
          <ExternalLink className="size-3" />
        </a>
      </div>
      <div>
        <label className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
          Daily cap
        </label>
        <input
          type="number"
          min={1}
          max={500}
          value={cap}
          onChange={(e) => setCap(Math.max(1, Number(e.target.value) || 50))}
          className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 tnum focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
        <div className="mt-1 text-[10px] text-zinc-500">
          New accounts: start at 5–10/day, ramp up over 1–2 weeks to avoid spam flags.
        </div>
      </div>
      {err && (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2.5 py-2 text-xs text-rose-200">
          {err}
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={busy || !email.trim() || !pw.trim()}
          className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-sm text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <CheckCircle2 className="size-3.5" />
          )}
          Connect &amp; verify
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}


function HealthBadge({ score, stats }: {
  score: number
  stats?: { sent: number; replied: number; bounced: number; bounce_rate_pct: number }
}) {
  const tone =
    score >= 80 ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
    : score >= 50 ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
    : "bg-rose-500/15 text-rose-300 border-rose-500/30"
  const title = stats
    ? `30d: ${stats.sent} sent, ${stats.replied} replied, ${stats.bounced} bounced (${stats.bounce_rate_pct}% bounce)`
    : `Health ${score}/100`
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium tnum",
        tone,
      )}
    >
      {score}
    </span>
  )
}
