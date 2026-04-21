import { PageHeader } from "@/components/page-header"
import { LinkedInKpiRow } from "@/components/linkedin/linkedin-kpi-row"
import { LinkedInSafetyCard } from "@/components/linkedin/linkedin-safety-card"
import { LinkedInGmailConnect } from "@/components/linkedin/linkedin-gmail-connect"
import { LinkedInBatchSend } from "@/components/linkedin/linkedin-batch-send"

export default function LinkedInOverviewPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="LinkedIn Outreach"
        subtitle="Scan posts, generate drafts, send via Gmail. All native in the dashboard."
      />

      <LinkedInKpiRow />

      <LinkedInBatchSend />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <LinkedInSafetyCard />
        <LinkedInGmailConnect />
      </div>
    </div>
  )
}
