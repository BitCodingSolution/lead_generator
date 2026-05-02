"use client"

import * as React from "react"
import { PageHeader } from "@/components/page-header"
import { LinkedInSafetyCard } from "@/components/linkedin/linkedin-safety-card"
import { LinkedInRuntimeSettings } from "@/components/linkedin/linkedin-runtime-settings"
import { LinkedInGmailConnect } from "@/components/linkedin/linkedin-gmail-connect"
import { LinkedInExtensionKeys } from "@/components/linkedin/linkedin-extension-keys"
import { LinkedInMaintenance } from "@/components/linkedin/linkedin-maintenance"
import { LinkedInAutopilotStatus } from "@/components/linkedin/linkedin-autopilot-status"
import { LinkedInBlocklistCard } from "@/components/linkedin/linkedin-blocklist-card"
import { LinkedInCVsCard } from "@/components/linkedin/linkedin-cvs-card"
import { LinkedInDnsCard } from "@/components/linkedin/linkedin-dns-card"
import { Puzzle, Mail, Shield, Ban, FileText, Wrench, KeyRound, Download, ShieldCheck, Sliders } from "lucide-react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"

type SectionId =
  | "gmail" | "safety" | "runtime" | "keys" | "blocklist" | "cvs" | "dns" | "maintenance" | "install"

const SECTIONS: { id: SectionId; label: string; icon: React.ReactNode }[] = [
  { id: "gmail", label: "Gmail", icon: <Mail className="size-3.5" /> },
  { id: "safety", label: "Safety & Autopilot", icon: <Shield className="size-3.5" /> },
  { id: "runtime", label: "Runtime toggles", icon: <Sliders className="size-3.5" /> },
  { id: "cvs", label: "CV library", icon: <FileText className="size-3.5" /> },
  { id: "blocklist", label: "Blocklist", icon: <Ban className="size-3.5" /> },
  { id: "dns", label: "Domain auth", icon: <ShieldCheck className="size-3.5" /> },
  { id: "keys", label: "Extension keys", icon: <KeyRound className="size-3.5" /> },
  { id: "maintenance", label: "Maintenance", icon: <Wrench className="size-3.5" /> },
  { id: "install", label: "Install", icon: <Puzzle className="size-3.5" /> },
]

export default function LinkedInSettingsPage() {
  const [active, setActive] = React.useState<SectionId>("gmail")

  React.useEffect(() => {
    if (typeof window === "undefined") return
    function onScroll() {
      // Pick the section whose top is nearest the viewport top (with offset).
      const offset = 120
      let current: SectionId = SECTIONS[0].id
      for (const s of SECTIONS) {
        const el = document.getElementById(`sec-${s.id}`)
        if (!el) continue
        if (el.getBoundingClientRect().top - offset <= 0) current = s.id
      }
      setActive(current)
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    onScroll()
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  function scrollTo(id: SectionId) {
    document.getElementById(`sec-${id}`)?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    })
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="LinkedIn Settings"
        subtitle="Gmail, safety rails, autopilot, blocklist, CVs, extension keys, and maintenance — all in one place."
      />

      <nav className="sticky top-0 z-10 -mx-6 px-6 py-2 bg-[#0c0c0e]/90 backdrop-blur border-b border-zinc-800/70 overflow-x-auto">
        <div className="flex items-center gap-1">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => scrollTo(s.id)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs whitespace-nowrap transition-colors",
                active === s.id
                  ? "bg-[hsl(250_80%_62%/0.18)] text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/40",
              )}
            >
              {s.icon}
              {s.label}
            </button>
          ))}
        </div>
      </nav>

      <Section id="gmail" title="Gmail connection">
        <LinkedInGmailConnect />
      </Section>

      <Section id="safety" title="Safety & Autopilot">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <LinkedInSafetyCard />
          <LinkedInAutopilotStatus />
        </div>
      </Section>

      <Section id="runtime" title="Runtime toggles">
        <LinkedInRuntimeSettings />
      </Section>

      <Section id="cvs" title="CV library">
        <LinkedInCVsCard />
      </Section>

      <Section id="blocklist" title="Blocklist">
        <LinkedInBlocklistCard />
      </Section>

      <Section id="dns" title="Domain authentication">
        <LinkedInDnsCard />
      </Section>

      <Section id="keys" title="Extension keys">
        <LinkedInExtensionKeys />
      </Section>

      <Section id="maintenance" title="Maintenance">
        <LinkedInMaintenance />
      </Section>

      <Section id="install" title="Install the Chrome extension">
        <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <Puzzle className="size-4 text-zinc-400" />
              <div className="text-sm font-medium text-zinc-200">
                Unpacked install
              </div>
            </div>
            <a
              href={`${api.base}/api/linkedin/extension/download`}
              download
              className="inline-flex items-center gap-1.5 rounded-md bg-[hsl(250_80%_62%)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[hsl(250_80%_70%)]"
            >
              <Download className="size-3.5" />
              Download extension (.zip)
            </a>
          </div>
          <ol className="list-decimal list-inside text-xs text-zinc-400 space-y-1">
            <li>Download the zip above and extract it somewhere permanent</li>
            <li>Open <span className="font-mono">chrome://extensions</span></li>
            <li>Enable <span className="font-mono">Developer mode</span> (top right)</li>
            <li>
              Click <span className="font-mono">Load unpacked</span> and select
              the extracted <span className="font-mono">linkedin_extension</span> folder
            </li>
            <li>
              Open the extension popup, paste your API key (from the
              Extension keys section above) and set the Backend API base
              to <span className="font-mono">https://api.bitcodingsolutions.com</span>
            </li>
          </ol>
        </div>
      </Section>
    </div>
  )
}

function Section({
  id, title, children,
}: {
  id: SectionId
  title: string
  children: React.ReactNode
}) {
  return (
    <section id={`sec-${id}`} className="scroll-mt-24 space-y-3">
      <h2 className="text-sm font-semibold text-zinc-200 tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  )
}
