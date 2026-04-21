import { PageHeader } from "@/components/page-header"
import { LinkedInLeadsTable } from "@/components/linkedin/linkedin-leads-table"

export default function LinkedInLeadsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="All LinkedIn Leads"
        subtitle="Every post the extension has captured. Filter by status or search company/role/email."
      />
      <LinkedInLeadsTable />
    </div>
  )
}
