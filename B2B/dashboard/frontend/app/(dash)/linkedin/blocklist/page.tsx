"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import { Ban, Plus, Trash2, Loader2 } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { api, swrFetcher } from "@/lib/api"
import { cn } from "@/lib/utils"

type BlockRow = {
  id: number
  kind: "company" | "domain"
  value: string
  reason: string | null
  created_at: string
}

const ENDPOINT = "/api/linkedin/blocklist"

export default function LinkedInBlocklistPage() {
  const { data, isLoading } = useSWR<{ rows: BlockRow[] }>(ENDPOINT, swrFetcher)
  const [kind, setKind] = React.useState<"company" | "domain">("domain")
  const [value, setValue] = React.useState("")
  const [reason, setReason] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)

  const rows = data?.rows ?? []

  async function onAdd(e: React.FormEvent) {
    e.preventDefault()
    if (!value.trim()) return
    setBusy(true)
    setMsg(null)
    try {
      await api.post(ENDPOINT, {
        kind,
        value: value.trim(),
        reason: reason.trim() || null,
      })
      setValue("")
      setReason("")
      mutate(ENDPOINT)
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onDelete(id: number) {
    if (!confirm("Remove this entry from the blocklist?")) return
    await api.post(`${ENDPOINT}/${id}/delete`)
    mutate(ENDPOINT)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Blocklist"
        subtitle="Companies and email domains never ingested or sent to. Matches on ingest (silent drop) and at send (400 error)."
      />

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <form onSubmit={onAdd} className="grid grid-cols-1 md:grid-cols-[110px_1fr_1fr_auto] gap-2">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as "company" | "domain")}
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 text-sm text-zinc-100 focus:outline-none focus:border-[hsl(250_80%_62%)]"
          >
            <option value="domain">Domain</option>
            <option value="company">Company</option>
          </select>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={
              kind === "domain"
                ? "upwork.com"
                : "e.g. Deloitte Consulting"
            }
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono focus:outline-none focus:border-[hsl(250_80%_62%)]"
          />
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional)"
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
          />
          <button
            type="submit"
            disabled={!value.trim() || busy}
            className="inline-flex items-center justify-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-sm text-white hover:brightness-110 disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
            Block
          </button>
        </form>
        {msg && (
          <div className="mt-2 text-[11px] text-rose-300">{msg}</div>
        )}
      </div>

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] overflow-hidden">
        {isLoading ? (
          <div className="p-6 text-sm text-zinc-500">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center">
            <div className="mx-auto size-10 rounded-full bg-zinc-800/60 flex items-center justify-center mb-3">
              <Ban className="size-5 text-zinc-500" />
            </div>
            <div className="text-sm text-zinc-300">No entries yet</div>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-zinc-500 border-b border-zinc-800/70">
                <th className="px-3 py-2 font-medium w-24">Kind</th>
                <th className="px-3 py-2 font-medium">Value</th>
                <th className="px-3 py-2 font-medium">Reason</th>
                <th className="px-3 py-2 font-medium w-24">Added</th>
                <th className="px-3 py-2 w-10" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        "inline-block rounded px-1.5 py-0.5 text-[11px] font-medium",
                        r.kind === "domain"
                          ? "bg-sky-500/15 text-sky-300"
                          : "bg-violet-500/15 text-violet-300",
                      )}
                    >
                      {r.kind}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-zinc-200">{r.value}</td>
                  <td className="px-3 py-2 text-xs text-zinc-400">{r.reason || "—"}</td>
                  <td className="px-3 py-2 text-[11px] text-zinc-500 tnum">{fmtDate(r.created_at)}</td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => onDelete(r.id)}
                      className="p-1 rounded hover:bg-rose-500/20 text-zinc-500 hover:text-rose-300"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
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

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })
  } catch { return iso }
}
