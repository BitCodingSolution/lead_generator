import { PageHeader } from "@/components/page-header"
import { LinkedInLeadsTable } from "@/components/linkedin/linkedin-leads-table"
import { LinkedInBatchSend } from "@/components/linkedin/linkedin-batch-send"

export default function LinkedInDraftsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Drafts"
        subtitle="Claude-generated outreach drafts ready for review and send. Start a batch to send multiple with safe 60-90s jitter."
      />
      <LinkedInBatchSend />
      <LinkedInLeadsTable initialStatus="Drafted" />
    </div>
  )
}
