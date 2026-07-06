import type { ReactNode } from "react";
import Spinner from "@/components/ui/Spinner";
import WakeupHint from "@/components/ui/WakeupHint";

interface Props {
  isLoading: boolean;
  error: unknown;
  isEmpty: boolean;
  errorFallback?: string;
  emptySlot?: ReactNode;
}

export default function PageState({ isLoading, error, isEmpty, errorFallback, emptySlot }: Props) {
  if (isLoading) {
    return (
      <div className="flex flex-col items-center py-20">
        <Spinner size="lg" />
        <WakeupHint />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {error instanceof Error ? error.message : (errorFallback ?? "Something went wrong.")}
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-12 text-center">
        {emptySlot ?? <p className="text-gray-500">No items yet.</p>}
      </div>
    );
  }

  return null;
}
