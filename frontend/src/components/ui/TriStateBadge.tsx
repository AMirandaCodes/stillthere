import { clsx } from "clsx";
import type { TriState } from "@/types/verification";

const config: Record<TriState, { label: string; className: string }> = {
  yes:     { label: "Yes",     className: "bg-green-100 text-green-800" },
  no:      { label: "No",      className: "bg-red-100 text-red-800" },
  unclear: { label: "Unclear", className: "bg-gray-100 text-gray-600" },
};

interface TriStateBadgeProps {
  value: TriState;
  className?: string;
}

export default function TriStateBadge({ value, className }: TriStateBadgeProps) {
  const { label, className: colorClass } = config[value] ?? config.unclear;
  return (
    <span className={clsx("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", colorClass, className)}>
      {label}
    </span>
  );
}
