import { cn } from "@/lib/utils"

export function EmptyState({
  icon,
  title,
  hint,
  className,
}: {
  icon?: React.ReactNode
  title: string
  hint?: string
  className?: string
}) {
  return (
    <div
      className={cn(
        "relative grid-illustration rounded-lg border border-dashed border-zinc-800 py-10 px-6 flex flex-col items-center justify-center text-center",
        className,
      )}
    >
      <div className="size-10 rounded-md border border-zinc-800/90 bg-zinc-900/60 flex items-center justify-center text-zinc-500 mb-3">
        {icon}
      </div>
      <div className="text-sm font-medium tracking-tight text-zinc-200">{title}</div>
      {hint && <div className="mt-1 text-xs text-zinc-500 max-w-sm">{hint}</div>}
    </div>
  )
}
