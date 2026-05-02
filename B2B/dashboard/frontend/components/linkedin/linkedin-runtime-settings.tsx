"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import { Sliders, Loader2 } from "lucide-react"
import { api, swrFetcher } from "@/lib/api"
import { cn } from "@/lib/utils"

type Setting = {
  key: string
  label: string
  type: "bool" | "int" | "string"
  env_key?: string
  default: boolean | number | string
  help?: string
  value: boolean | number | string
}

const ENDPOINT = "/api/linkedin/runtime-settings"

export function LinkedInRuntimeSettings() {
  const { data, isLoading } = useSWR<{ settings: Setting[] }>(ENDPOINT, swrFetcher)
  // Track which key is mid-flight so the toggle disables only that row
  // instead of the whole panel — multiple toggles can be flipped quickly
  // without UI lockout.
  const [pending, setPending] = React.useState<Set<string>>(new Set())
  const [error, setError] = React.useState<string | null>(null)

  async function update(key: string, value: boolean | number | string) {
    setError(null)
    setPending((cur) => new Set(cur).add(key))
    try {
      await api.post(ENDPOINT, { key, value })
      mutate(ENDPOINT)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending((cur) => {
        const next = new Set(cur)
        next.delete(key)
        return next
      })
    }
  }

  const settings = data?.settings ?? []

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center gap-2 mb-3">
        <Sliders className="size-4 text-zinc-400" />
        <div className="text-sm font-medium text-zinc-200">Runtime toggles</div>
      </div>
      <p className="text-xs text-zinc-500 mb-3 max-w-2xl">
        Backend behaviour switches that previously required restarting the
        backend with an env var. Changes take effect immediately on the
        next request.
      </p>

      {isLoading ? (
        <div className="text-xs text-zinc-500 py-4">Loading…</div>
      ) : settings.length === 0 ? (
        <div className="text-xs text-zinc-500 py-4">No runtime settings exposed.</div>
      ) : (
        <ul className="divide-y divide-zinc-800/60">
          {settings.map((s) => (
            <li key={s.key} className="py-3 flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="text-sm text-zinc-200">{s.label}</div>
                {s.help && (
                  <div className="mt-0.5 text-[11px] text-zinc-500 leading-relaxed">
                    {s.help}
                  </div>
                )}
                <div className="mt-1 text-[10px] text-zinc-600 font-mono">
                  {s.key}
                  {s.env_key && <span className="ml-2 text-zinc-700">env: {s.env_key}</span>}
                </div>
              </div>
              <div className="shrink-0">
                {s.type === "bool" ? (
                  <Toggle
                    checked={Boolean(s.value)}
                    busy={pending.has(s.key)}
                    onChange={(next) => update(s.key, next)}
                  />
                ) : (
                  <span className="text-xs text-zinc-500">
                    (input type "{s.type}" not yet rendered)
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <div className="mt-3 text-xs text-rose-400">Update failed: {error}</div>
      )}
    </div>
  )
}

function Toggle({
  checked,
  busy,
  onChange,
}: {
  checked: boolean
  busy: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={busy}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
        checked ? "bg-emerald-500/80" : "bg-zinc-700",
        busy && "opacity-60",
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
      {busy && (
        <Loader2 className="absolute -right-5 size-3 animate-spin text-zinc-500" />
      )}
    </button>
  )
}
