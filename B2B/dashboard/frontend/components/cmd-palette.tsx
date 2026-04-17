"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  LayoutDashboard,
  Users,
  Rocket,
  MessageSquareReply,
  BarChart3,
} from "lucide-react"

const ITEMS = [
  { href: "/", label: "Overview", icon: LayoutDashboard, keywords: "home dashboard kpi" },
  { href: "/leads", label: "Leads", icon: Users, keywords: "companies contacts" },
  { href: "/campaigns", label: "Campaigns", icon: Rocket, keywords: "send drafts batch" },
  { href: "/replies", label: "Replies", icon: MessageSquareReply, keywords: "inbox responses" },
  { href: "/analytics", label: "Analytics", icon: BarChart3, keywords: "charts metrics" },
]

export function CmdPalette() {
  const [open, setOpen] = React.useState(false)
  const router = useRouter()

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  return (
    <CommandDialog open={open} onOpenChange={setOpen} title="Navigate" description="Jump to any page.">
      <CommandInput placeholder="Type a page or command..." />
      <CommandList>
        <CommandEmpty>No results.</CommandEmpty>
        <CommandGroup heading="Navigate">
          {ITEMS.map((i) => {
            const Icon = i.icon
            return (
              <CommandItem
                key={i.href}
                value={`${i.label} ${i.keywords}`}
                onSelect={() => {
                  setOpen(false)
                  router.push(i.href)
                }}
              >
                <Icon className="size-4 text-zinc-400" />
                <span>{i.label}</span>
              </CommandItem>
            )
          })}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
