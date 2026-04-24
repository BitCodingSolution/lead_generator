"use client"

import useSWR, { mutate } from "swr"
import { Trash2, RotateCcw, ExternalLink } from "lucide-react"
import { api, swrFetcher } from "@/lib/api"

type BinRow = {
  id: number
  original_id: number | null
  post_url: string | null
  reason: string
  moved_at: string
  company: string | null
  posted_by: string | null
  role: string | null
  email: string | null
}

const ENDPOINT = "/api/linkedin/recyclebin"

export function LinkedInRecyclebinList() {
  const { data, isLoading } = useSWR<{ rows: BinRow[] }>(ENDPOINT, swrFetcher)
  const rows = data?.rows ?? []

  async function onRestore(id: number) {
    await api.post(`/api/linkedin/leads/${id}/restore`)
    mutate(ENDPOINT)
    mutate("/api/linkedin/overview")
    mutate((k) => typeof k === "string" && k.startsWith("/api/linkedin/leads?"))
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-8 text-sm text-zinc-500">
        Loading…
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-12 text-center">
        <div className="mx-auto size-10 rounded-full bg-zinc-800/60 flex items-center justify-center mb-3">
          <Trash2 className="size-5 text-zinc-500" />
        </div>
        <div className="text-sm text-zinc-300">Empty</div>
        <div className="mt-1 text-xs text-zinc-500 max-w-sm mx-auto">
          Auto-skipped, archived, or bounced leads will appear here.
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-[0.08em] text-zinc-500 border-b border-zinc-800/70">
            <th className="px-3 py-2 font-medium">Company</th>
            <th className="px-3 py-2 font-medium">Posted by</th>
            <th className="px-3 py-2 font-medium">Role</th>
            <th className="px-3 py-2 font-medium">Reason</th>
            <th className="px-3 py-2 font-medium">Moved</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              className="border-b border-zinc-800/50 hover:bg-zinc-800/30"
            >
              <td className="px-3 py-2 text-zinc-200">{r.company || "—"}</td>
              <td className="px-3 py-2 text-zinc-400">{r.posted_by || "—"}</td>
              <td className="px-3 py-2 text-zinc-400">{r.role || "—"}</td>
              <td className="px-3 py-2">
                <span className="inline-block rounded bg-zinc-800/70 px-1.5 py-0.5 text-[11px] text-zinc-400">
                  {r.reason}
                </span>
              </td>
              <td className="px-3 py-2 text-xs text-zinc-500 tnum">
                {fmtDate(r.moved_at)}
              </td>
              <td className="px-3 py-2 text-right">
                <div className="inline-flex items-center gap-1">
                  {r.post_url && (
                    <a
                      href={r.post_url}
                      target="_blank"
                      rel="noreferrer"
                      className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-200"
                      title="Open post"
                    >
                      <ExternalLink className="size-3.5" />
                    </a>
                  )}
                  <button
                    onClick={() => onRestore(r.id)}
                    className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-xs text-zinc-300 hover:bg-zinc-800"
                    title="Restore to active list"
                  >
                    <RotateCcw className="size-3" />
                    Restore
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    })
  } catch {
    return iso
  }
}
