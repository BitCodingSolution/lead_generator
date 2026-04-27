"use client"

import * as React from "react"
import useSWR from "swr"
import { ShieldCheck, ShieldAlert, ShieldOff, Loader2, RefreshCw } from "lucide-react"
import { cn } from "@/lib/utils"
import { swrFetcher } from "@/lib/api"

type Verdict = "ok" | "soft" | "missing"

type RecordStatus = {
  verdict: Verdict
  value: string | null
  selector?: string | null
}

type DnsResponse = {
  domain: string
  spf: RecordStatus
  dkim: RecordStatus
  dmarc: RecordStatus
}

export function LinkedInDnsCard({
  defaultDomain = "bitcodingsolutions.com",
}: { defaultDomain?: string } = {}) {
  const [domain, setDomain] = React.useState(defaultDomain)
  const [pending, setPending] = React.useState(domain)

  const { data, error, isLoading, mutate } = useSWR<DnsResponse>(
    `/api/linkedin/dns/check?domain=${encodeURIComponent(domain)}`,
    swrFetcher,
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  )

  function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const d = pending.trim().toLowerCase()
    if (!d) return
    setDomain(d)
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center gap-2 mb-3">
        <ShieldCheck className="size-4 text-zinc-400" />
        <div className="text-sm font-medium text-zinc-200">
          Domain authentication
        </div>
        <button
          onClick={() => mutate()}
          disabled={isLoading}
          className="ml-auto inline-flex items-center gap-1 rounded border border-zinc-800 bg-zinc-900/60 px-2 py-0.5 text-[11px] text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
          title="Re-check DNS"
        >
          {isLoading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          Recheck
        </button>
      </div>
      <div className="text-[11px] text-zinc-500 mb-3">
        SPF / DKIM / DMARC lookup for the sending domain. Affects deliverability for branded sends (B2B flow). Gmail-to-Gmail sends sign automatically.
      </div>

      <form onSubmit={onSubmit} className="flex items-center gap-2 mb-3">
        <input
          value={pending}
          onChange={(e) => setPending(e.target.value)}
          placeholder="bitcodingsolutions.com"
          className="flex-1 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 font-mono focus:outline-none focus:border-[hsl(250_80%_62%)]"
        />
        <button
          type="submit"
          disabled={!pending.trim() || pending.trim() === domain}
          className="rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-xs text-white hover:brightness-110 disabled:opacity-50"
        >
          Check
        </button>
      </form>

      {error && (
        <div className="text-xs text-rose-300">
          {(error as Error).message || "Lookup failed"}
        </div>
      )}

      {data && (
        <div className="space-y-2">
          <RecordRow name="SPF"   rec={data.spf} />
          <RecordRow name="DKIM"  rec={data.dkim} selectorHint />
          <RecordRow name="DMARC" rec={data.dmarc} />
        </div>
      )}
    </div>
  )
}

function RecordRow({
  name, rec, selectorHint,
}: {
  name: string
  rec: RecordStatus
  selectorHint?: boolean
}) {
  const Icon =
    rec.verdict === "ok" ? ShieldCheck
      : rec.verdict === "soft" ? ShieldAlert
        : ShieldOff
  const tone =
    rec.verdict === "ok"
      ? "text-emerald-300 border-emerald-500/30 bg-emerald-500/10"
      : rec.verdict === "soft"
        ? "text-amber-300 border-amber-500/30 bg-amber-500/10"
        : "text-rose-300 border-rose-500/30 bg-rose-500/10"

  return (
    <div className={cn("flex items-start gap-2 rounded-md border p-2 text-xs", tone)}>
      <Icon className="size-4 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium uppercase tracking-wide">{name}</span>
          <span className="text-[10px] uppercase opacity-80">{rec.verdict}</span>
          {selectorHint && rec.selector && (
            <span className="text-[10px] font-mono opacity-70">
              selector: {rec.selector}
            </span>
          )}
        </div>
        <div className="mt-0.5 font-mono text-[11px] break-all opacity-90">
          {rec.value ?? "—"}
        </div>
      </div>
    </div>
  )
}
