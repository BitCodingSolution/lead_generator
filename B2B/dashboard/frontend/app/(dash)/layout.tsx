import { Sidebar } from "@/components/sidebar"
import { Topbar } from "@/components/topbar"
import { PendingTitleUpdater } from "@/components/pending-title-updater"
import { ReplyNotifications } from "@/components/reply-notifications"

export default function DashLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <Topbar />
        <main className="flex-1 px-6 lg:px-8 py-6 max-w-[1500px] w-full mx-auto">
          {children}
        </main>
      </div>
      <PendingTitleUpdater />
      <ReplyNotifications />
    </div>
  )
}
