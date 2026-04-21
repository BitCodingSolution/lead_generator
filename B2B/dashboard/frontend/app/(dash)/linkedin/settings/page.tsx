import { PageHeader } from "@/components/page-header"
import { LinkedInSafetyCard } from "@/components/linkedin/linkedin-safety-card"
import { LinkedInGmailConnect } from "@/components/linkedin/linkedin-gmail-connect"
import { LinkedInExtensionKeys } from "@/components/linkedin/linkedin-extension-keys"
import { Puzzle } from "lucide-react"

export default function LinkedInSettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="LinkedIn Settings"
        subtitle="Gmail connection, extension keys, safety mode, and autopilot schedule."
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <LinkedInGmailConnect />
        <LinkedInSafetyCard />
      </div>

      <LinkedInExtensionKeys />

      <div className="rounded-xl border border-zinc-800/80 bg-[#18181b] p-4">
        <div className="flex items-center gap-2 mb-2">
          <Puzzle className="size-4 text-zinc-400" />
          <div className="text-sm font-medium text-zinc-200">
            Install the extension
          </div>
        </div>
        <ol className="list-decimal list-inside text-xs text-zinc-400 space-y-1">
          <li>Open <span className="font-mono">chrome://extensions</span></li>
          <li>Enable Developer mode</li>
          <li>
            Click <span className="font-mono">Load unpacked</span> and select
            <span className="font-mono"> B2B/linkedin_extension</span>
          </li>
          <li>Paste your API key into the extension side panel</li>
        </ol>
      </div>
    </div>
  )
}
