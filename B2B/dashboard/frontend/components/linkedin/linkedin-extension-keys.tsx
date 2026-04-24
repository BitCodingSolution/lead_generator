"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import { KeyRound, Copy, Trash2, Plus, Check } from "lucide-react"
import { api, swrFetcher } from "@/lib/api"

type KeyRow = {
  key: string
  label: string
  created_at: string
  last_used_at: string | null
}

const ENDPOINT = "/api/linkedin/extension/keys"

export function LinkedInExtensionKeys() {
  const { data, isLoading } = useSWR<{ rows: KeyRow[] }>(ENDPOINT, swrFetcher)
  const [label, setLabel] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const [justCopied, setJustCopied] = React.useState<string | null>(null)
  const [justCreated, setJustCreated] = React.useState<string | null>(null)

  const rows = data?.rows ?? []

  async function onCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!label.trim() || busy) return
    setBusy(true)
    try {
      const res = await api.post<{ key: string }>(ENDPOINT, {
        label: label.trim(),
      })
      setJustCreated(res.key)
      setLabel("")
      mutate(ENDPOINT)
    } finally {
      setBusy(false)
    }
  }

  async function onRevoke(key: string) {
    if (!confirm("Revoke this key? Extensions using it will stop working.")) return
    await api.post(`${ENDPOINT}/${encodeURIComponent(key)}/revoke`)
    mutate(ENDPOINT)
  }

  async function onCopy(k: string) {
    await navigator.clipboard.writeText(k)
    setJustCopied(k)
    setTimeout(() => setJustCopied(null), 1500)
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center gap-2 mb-3">
        <KeyRound className="size-4 text-zinc-400" />
        <div className="text-sm font-medium text-zinc-200">
          Extension API keys
        </div>
      </div>

      <form onSubmit={onCreate} className="flex items-center gap-2 mb-3">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Label (e.g., Laptop Chrome)"
          className="flex-1 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
        <button
          type="submit"
          disabled={!label.trim() || busy}
          className="inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-sm text-white disabled:opacity-50 hover:brightness-110"
        >
          <Plus className="size-3.5" />
          Issue key
        </button>
      </form>

      {justCreated && (
        <div className="mb-3 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-2.5 text-xs text-emerald-200">
          Key created. Copy it now — full value only shown once.
          <div className="mt-1.5 flex items-center gap-2">
            <code className="flex-1 font-mono text-[11px] break-all">
              {justCreated}
            </code>
            <button
              onClick={() => onCopy(justCreated)}
              className="rounded bg-emerald-500/20 p-1 hover:bg-emerald-500/30"
            >
              {justCopied === justCreated ? (
                <Check className="size-3" />
              ) : (
                <Copy className="size-3" />
              )}
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="text-xs text-zinc-500">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-xs text-zinc-500">
          No keys yet. Issue one above, then paste it into the extension side
          panel.
        </div>
      ) : (
        <div className="divide-y divide-zinc-800/60">
          {rows.map((r) => (
            <div
              key={r.key}
              className="flex items-center justify-between py-2"
            >
              <div className="min-w-0">
                <div className="text-sm text-zinc-200 truncate">{r.label}</div>
                <div className="text-[11px] text-zinc-500 tnum">
                  <code className="font-mono">{maskKey(r.key)}</code>
                  <span className="mx-1.5">·</span>
                  Created {fmtDate(r.created_at)}
                  {r.last_used_at && (
                    <>
                      <span className="mx-1.5">·</span>
                      Last used {fmtDate(r.last_used_at)}
                    </>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => onCopy(r.key)}
                  className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                  title="Copy key"
                >
                  {justCopied === r.key ? (
                    <Check className="size-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="size-3.5" />
                  )}
                </button>
                <button
                  onClick={() => onRevoke(r.key)}
                  className="p-1.5 rounded hover:bg-rose-500/20 text-zinc-500 hover:text-rose-300"
                  title="Revoke"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function maskKey(k: string): string {
  if (k.length <= 12) return k
  return `${k.slice(0, 6)}…${k.slice(-4)}`
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
