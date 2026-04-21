import { PageHeader } from "@/components/page-header"
import { LinkedInLeadsTable } from "@/components/linkedin/linkedin-leads-table"
import { LinkedInRepliesPanel } from "@/components/linkedin/linkedin-replies-panel"

export default function LinkedInSentPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Sent & Replies"
        subtitle="Everything that went out via Gmail. Inbox polls every 5 minutes for replies and bounces."
      />

      <LinkedInRepliesPanel />

      <div>
        <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-500 mb-2">
          Sent leads
        </div>
        <LinkedInLeadsTable initialStatus="Sent" />
      </div>
    </div>
  )
}
