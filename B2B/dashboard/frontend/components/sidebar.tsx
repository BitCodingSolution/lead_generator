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
} from "lucide-react"
import { cn } from "@/lib/utils"

type NavItem = {
  href: string
  label: string
  icon: React.ComponentType<{ className?: string }>
}

const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/sources", label: "Sources", icon: Database },
  { href: "/linkedin", label: "LinkedIn", icon: Briefcase },
  { href: "/campaigns", label: "Campaigns", icon: Rocket },
  { href: "/replies", label: "Replies", icon: MessageSquareReply },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
]

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

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-zinc-500 px-3 mb-2">
          Workspace
        </div>
        {NAV.map((item) => {
          const Icon = item.icon
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href || pathname?.startsWith(item.href + "/")
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "relative flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors group",
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
                  active ? "text-[hsl(250_80%_72%)]" : "text-zinc-500 group-hover:text-zinc-300",
                )}
              />
              <span className="tracking-tight">{item.label}</span>
            </Link>
          )
        })}
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
