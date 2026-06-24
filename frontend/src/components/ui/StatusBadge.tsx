import { clsx } from "clsx";
import Spinner from "./Spinner";
import type { VerificationStatus } from "@/types/verification";
import type { BatchJobStatus } from "@/types/batch";

type Status = VerificationStatus | BatchJobStatus;

const config: Record<string, { label: string; className: string; spin?: boolean }> = {
  pending:  { label: "Pending",    className: "bg-gray-100 text-gray-600" },
  queued:   { label: "Queued",     className: "bg-gray-100 text-gray-600" },
  running:  { label: "Running",    className: "bg-blue-100 text-blue-700", spin: true },
  complete: { label: "Complete",   className: "bg-green-100 text-green-800" },
  failed:   { label: "Failed",     className: "bg-red-100 text-red-800" },
};

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

export default function StatusBadge({ status, className }: StatusBadgeProps) {
  const { label, className: colorClass, spin } = config[status] ?? config.pending;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium", colorClass, className)}>
      {spin && <Spinner size="sm" className="h-3 w-3" />}
      {label}
    </span>
  );
}
