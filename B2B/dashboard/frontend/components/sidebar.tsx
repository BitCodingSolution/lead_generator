"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  LayoutDashboard,
  Rocket,
  MessageSquareReply,
  BarChart3,
  CornerDownRight,
  Database,
  Briefcase,
  Inbox,
  Mail,
  Send,
  Trash2,
  Settings,
  Ban,
  Clock,
  FileText,
} from "lucide-react"
import { cn } from "@/lib/utils"

type NavItem = {
  href: string
  label: string
  icon: React.ComponentType<{ className?: string }>
}

type NavSection = {
  title: string
  items: NavItem[]
}

const SECTIONS: NavSection[] = [
  {
    title: "Workspace",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/sources", label: "Sources", icon: Database },
      { href: "/campaigns", label: "Campaigns", icon: Rocket },
      { href: "/replies", label: "Replies", icon: MessageSquareReply },
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "LinkedIn",
    items: [
      { href: "/linkedin", label: "Overview", icon: Briefcase },
      { href: "/linkedin/leads", label: "Leads", icon: Inbox },
      { href: "/linkedin/drafts", label: "Drafts", icon: Mail },
      { href: "/linkedin/sent", label: "Sent & Replies", icon: Send },
      { href: "/linkedin/follow-ups", label: "Follow-ups", icon: Clock },
      { href: "/linkedin/recyclebin", label: "Recyclebin", icon: Trash2 },
      { href: "/linkedin/blocklist", label: "Blocklist", icon: Ban },
      { href: "/linkedin/cvs", label: "CV library", icon: FileText },
      { href: "/linkedin/settings", label: "Settings", icon: Settings },
    ],
  },
]

function isActive(pathname: string | null, href: string): boolean {
  if (!pathname) return false
  if (href === "/" || href === "/linkedin") return pathname === href
  return pathname === href || pathname.startsWith(href + "/")
}

export function Sidebar() {
  const pathname = usePathname()
  return (
    <aside className="hidden md:flex flex-col w-[220px] shrink-0 border-r border-zinc-800/80 bg-[#0c0c0e] h-screen sticky top-0">
      <div className="px-5 py-5 border-b border-zinc-800/70">
        <Link href="/" className="flex items-center gap-2 group">
          <div className="size-7 rounded-md bg-gradient-to-br from-[hsl(250_80%_62%)] to-[hsl(270_90%_65%)] flex items-center justify-center shadow-[0_0_0_1px_rgba(255,255,255,0.05)]">
            <CornerDownRight className="size-4 text-white" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">BitCoding</div>
            <div className="text-[10px] uppercase tracking-[0.15em] text-zinc-500">
              Outreach
            </div>
          </div>
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-5">
        {SECTIONS.map((section) => (
          <div key={section.title} className="space-y-0.5">
            <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-zinc-500 px-3 mb-2">
              {section.title}
            </div>
            {section.items.map((item) => {
              const Icon = item.icon
              const active = isActive(pathname, item.href)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "relative flex items-center gap-2.5 px-3 py-1.5 rounded-md text-sm transition-colors group",
                    active
                      ? "bg-[hsl(250_80%_62%/0.12)] text-white"
                      : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/40",
                  )}
                >
                  {active && (
                    <span className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r bg-[hsl(250_80%_62%)]" />
                  )}
                  <Icon
                    className={cn(
                      "size-4",
                      active
                        ? "text-[hsl(250_80%_72%)]"
                        : "text-zinc-500 group-hover:text-zinc-300",
                    )}
                  />
                  <span className="tracking-tight">{item.label}</span>
                </Link>
              )
            })}
          </div>
        ))}
      </nav>

      <div className="p-4 border-t border-zinc-800/70">
        <div className="rounded-md border border-zinc-800/80 bg-zinc-900/40 p-3">
          <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500 mb-1">
            Shortcut
          </div>
          <div className="flex items-center justify-between text-xs text-zinc-300">
            <span>Command palette</span>
            <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">
              ⌘K
            </kbd>
          </div>
        </div>
      </div>
    </aside>
  )
}
