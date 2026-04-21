import { PageHeader } from "@/components/page-header"
import { LinkedInRecyclebinList } from "@/components/linkedin/linkedin-recyclebin-list"

export default function LinkedInRecyclebinPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Recyclebin"
        subtitle="Auto-skipped, rejected, or archived leads. Restore any row back into the active list."
      />
      <LinkedInRecyclebinList />
    </div>
  )
}
