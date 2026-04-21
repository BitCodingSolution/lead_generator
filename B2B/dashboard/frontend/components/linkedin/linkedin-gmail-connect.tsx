"use client"

import * as React from "react"
import useSWR, { mutate } from "swr"
import {
  Mail, CheckCircle2, XCircle, Loader2, ShieldCheck, Unlink, ExternalLink,
} from "lucide-react"
import { api, swrFetcher } from "@/lib/api"

type GmailStatus = {
  connected: boolean
  email: string | null
  connected_at: string | null
  last_verified_at?: string | null
}

const ENDPOINT = "/api/linkedin/gmail/status"

export function LinkedInGmailConnect() {
  const { data } = useSWR<GmailStatus>(ENDPOINT, swrFetcher, {
    refreshInterval: 30_000,
  })
  const connected = !!data?.connected

  const [email, setEmail] = React.useState("")
  const [pw, setPw] = React.useState("")
  const [busy, setBusy] = React.useState<"" | "connect" | "test" | "disconnect">("")
  const [msg, setMsg] = React.useState<{ kind: "ok" | "err"; text: string } | null>(null)

  async function onConnect(e: React.FormEvent) {
    e.preventDefault()
    setBusy("connect")
    setMsg(null)
    try {
      const res = await api.post<{ smtp_ok: boolean; imap_ok: boolean }>(
        "/api/linkedin/gmail/connect",
        { email: email.trim(), app_password: pw.replace(/\s+/g, "") },
      )
      setMsg({
        kind: "ok",
        text: `Connected. SMTP ${res.smtp_ok ? "✓" : "✗"} · IMAP ${res.imap_ok ? "✓" : "✗"}`,
      })
      setPw("")
      mutate(ENDPOINT)
      mutate("/api/linkedin/overview")
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message })
    } finally {
      setBusy("")
    }
  }

  async function onTest() {
    setBusy("test")
    setMsg(null)
    try {
      const res = await api.post<{ smtp_ok: boolean; imap_ok: boolean }>(
        "/api/linkedin/gmail/test",
      )
      setMsg({
        kind: "ok",
        text: `SMTP ${res.smtp_ok ? "✓" : "✗"} · IMAP ${res.imap_ok ? "✓" : "✗"}`,
      })
      mutate(ENDPOINT)
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message })
    } finally {
      setBusy("")
    }
  }

  async function onDisconnect() {
    if (!confirm("Disconnect Gmail? Sending will stop until you reconnect.")) return
    setBusy("disconnect")
    try {
      await api.post("/api/linkedin/gmail/disconnect")
      mutate(ENDPOINT)
      setMsg(null)
    } finally {
      setBusy("")
    }
  }

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Mail className="size-4 text-zinc-400" />
          <div className="text-sm font-medium text-zinc-200">Gmail</div>
        </div>
        {connected ? (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
            <CheckCircle2 className="size-3" /> Connected
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-zinc-700/30 px-2 py-0.5 text-[11px] font-medium text-zinc-400">
            <XCircle className="size-3" /> Not connected
          </span>
        )}
      </div>

      {connected ? (
        <div className="mt-3 space-y-3">
          <div className="text-xs text-zinc-400">
            Signed in as{" "}
            <span className="text-zinc-200 font-mono">{data?.email}</span>
          </div>
          {data?.last_verified_at && (
            <div className="text-[11px] text-zinc-500">
              Last verified {fmtRelative(data.last_verified_at)}
            </div>
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={onTest}
              disabled={!!busy}
              className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
            >
              {busy === "test" ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <ShieldCheck className="size-3" />
              )}
              Test connection
            </button>
            <button
              onClick={onDisconnect}
              disabled={!!busy}
              className="inline-flex items-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-2.5 py-1 text-xs text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
            >
              <Unlink className="size-3" />
              Disconnect
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={onConnect} className="mt-3 space-y-2">
          <div>
            <label className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
              Gmail address
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="jaydipnakrani888@gmail.com"
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
          <button
            type="submit"
            disabled={!email.trim() || !pw.trim() || !!busy}
            className="w-full inline-flex items-center justify-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-sm text-white hover:brightness-110 disabled:opacity-50"
          >
            {busy === "connect" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="size-3.5" />
            )}
            Connect &amp; verify
          </button>
        </form>
      )}

      {msg && (
        <div
          className={`mt-3 rounded-md border px-2.5 py-2 text-xs ${
            msg.kind === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/10 text-rose-200"
          }`}
        >
          {msg.text}
        </div>
      )}
    </div>
  )
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
