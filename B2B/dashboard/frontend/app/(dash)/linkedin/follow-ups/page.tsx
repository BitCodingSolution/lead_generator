"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import { Clock, Send, Loader2, Mail } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { api, swrFetcher } from "@/lib/api"

type FollowupLead = {
  id: number
  company: string | null
  posted_by: string | null
  email: string | null
  gen_subject: string | null
  sent_at: string
  last_followup_at: string | null
  followup_count: number
  next_sequence: number
  days_since_last_touch: number
}

const ENDPOINT = "/api/linkedin/followups"

export default function LinkedInFollowupsPage() {
  const { data, isLoading } = useSWR<{
    rows: FollowupLead[]
    cadence: number[]
  }>(ENDPOINT, swrFetcher, { refreshInterval: 30_000 })

  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)
  const [selected, setSelected] = React.useState<Set<number>>(new Set())

  const rows = data?.rows ?? []
  const cadence = data?.cadence ?? [3, 7]

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function run(dryRun: boolean, specific: boolean) {
    setBusy(true)
    setMsg(null)
    try {
      const body: Record<string, unknown> = { dry_run: dryRun }
      if (specific) body.lead_ids = Array.from(selected)
      const res = await api.post<{
        sent?: number
        skipped?: number
        errors?: unknown[]
        total?: number
        would_send?: number
        dry_run?: boolean
        blocked_by_safety?: string
      }>("/api/linkedin/followups/run", body)
      if (res.blocked_by_safety) {
        setMsg(`Safety blocked: ${res.blocked_by_safety}`)
      } else if (res.dry_run) {
        setMsg(`Dry-run: would send ${res.would_send}`)
      } else {
        setMsg(
          `Sent ${res.sent} · skipped ${res.skipped} · errors ${(res.errors ?? []).length}`,
        )
        setSelected(new Set())
        mutate(ENDPOINT)
        mutate("/api/linkedin/overview")
      }
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Follow-ups"
        subtitle={`Cadence: ${cadence.join("d, ")}d after the last touch. Sent leads with no reply, no bounce, and no private note are eligible.`}
      />

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <Clock className="size-4 text-zinc-400" />
            <span className="text-sm text-zinc-200">
              {rows.length} lead{rows.length === 1 ? "" : "s"} due
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => run(true, false)}
              disabled={busy || rows.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
            >
              {busy ? <Loader2 className="size-3 animate-spin" /> : "Dry run"}
            </button>
            {selected.size > 0 && (
              <button
                onClick={() => run(false, true)}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1 text-xs text-white hover:brightness-110 disabled:opacity-50"
              >
                {busy ? <Loader2 className="size-3 animate-spin" /> : <Send className="size-3" />}
                Send to {selected.size}
              </button>
            )}
            <button
              onClick={() => run(false, false)}
              disabled={busy || rows.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy ? <Loader2 className="size-3 animate-spin" /> : <Send className="size-3" />}
              Send all
            </button>
          </div>
        </div>
        {msg && <div className="mt-2 text-[11px] text-zinc-400">{msg}</div>}
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] overflow-hidden">
        {isLoading ? (
          <div className="p-6 text-sm text-zinc-500">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center">
            <div className="mx-auto size-10 rounded-full bg-zinc-800/60 flex items-center justify-center mb-3">
              <Mail className="size-5 text-zinc-500" />
            </div>
            <div className="text-sm text-zinc-300">No follow-ups due</div>
            <div className="mt-1 text-xs text-zinc-500 max-w-sm mx-auto">
              Eligible leads will appear here after their {cadence[0]}-day
              window passes without a reply.
            </div>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-zinc-500 border-b border-zinc-800/70">
                <th className="px-3 py-2 w-10" />
                <th className="px-3 py-2 font-medium">Company</th>
                <th className="px-3 py-2 font-medium">Email</th>
                <th className="px-3 py-2 font-medium w-24">Last touch</th>
                <th className="px-3 py-2 font-medium w-24">Sequence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(r.id)}
                      onChange={() => toggle(r.id)}
                    />
                  </td>
                  <td className="px-3 py-2 text-zinc-200">
                    {r.company || r.posted_by || "—"}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-zinc-300">
                    {r.email || "—"}
                  </td>
                  <td className="px-3 py-2 text-[11px] text-zinc-500 tnum">
                    {r.days_since_last_touch}d ago
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center rounded bg-violet-500/15 px-1.5 py-0.5 text-[11px] font-medium text-violet-300">
                      #{r.next_sequence} of {cadence.length}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
