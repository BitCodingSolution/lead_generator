"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"

const TABS = [
  { href: "/linkedin", label: "Overview" },
  { href: "/linkedin/leads", label: "Leads" },
  { href: "/linkedin/drafts", label: "Drafts" },
  { href: "/linkedin/sent", label: "Sent" },
  { href: "/linkedin/recyclebin", label: "Recyclebin" },
  { href: "/linkedin/settings", label: "Settings" },
]

export default function LinkedInLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 border-b border-zinc-800/70 -mx-6 px-6 overflow-x-auto">
        {TABS.map((t) => {
          const active =
            t.href === "/linkedin"
              ? pathname === "/linkedin"
              : pathname === t.href || pathname?.startsWith(t.href + "/")
          return (
            <Link
              key={t.href}
              href={t.href}
              className={cn(
                "relative px-3 py-2.5 text-sm tracking-tight transition-colors whitespace-nowrap",
                active
                  ? "text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300",
              )}
            >
              {t.label}
              {active && (
                <span className="absolute left-2 right-2 bottom-0 h-[2px] rounded-t bg-[hsl(250_80%_62%)]" />
              )}
            </Link>
          )
        })}
      </div>
      {children}
    </div>
  )
}
