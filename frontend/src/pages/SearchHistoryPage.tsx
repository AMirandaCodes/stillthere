import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { PlusCircle } from "lucide-react";
import { verificationService } from "@/services/verificationService";
import PageState from "@/components/ui/PageState";
import StatusBadge from "@/components/ui/StatusBadge";
import ConfidenceScore from "@/components/ui/ConfidenceScore";
import Pagination from "@/components/ui/Pagination";

export default function SearchHistoryPage() {
  const [page, setPage] = useState(1);

  const { data, isLoading, error } = useQuery({
    queryKey: ["verifications", page],
    queryFn: () => verificationService.listVerifications(page),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Verification History</h1>
          {data && (
            <p className="mt-1 text-sm text-gray-500">{data.total} verification{data.total !== 1 ? "s" : ""} total</p>
          )}
        </div>
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
        >
          <PlusCircle className="h-4 w-4" /> New verification
        </Link>
      </div>

      <PageState
        isLoading={isLoading}
        error={error}
        isEmpty={!!data && data.items.length === 0}
        errorFallback="Failed to load history."
        emptySlot={
          <>
            <p className="text-gray-500">No verifications yet.</p>
            <Link to="/" className="mt-3 inline-block text-sm text-brand-600 hover:underline">
              Run your first verification →
            </Link>
          </>
        }
      />

      {data && data.items.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-6 py-3">Name</th>
                <th className="px-6 py-3">Company</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3">Confidence</th>
                <th className="px-6 py-3">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.items.map((v) => (
                <tr key={v.id} className="hover:bg-gray-50">
                  <td className="px-6 py-3">
                    <Link to={`/results/${v.id}`} className="font-medium text-brand-600 hover:underline">
                      {v.full_name}
                    </Link>
                  </td>
                  <td className="px-6 py-3 text-gray-700">{v.company_name}</td>
                  <td className="px-6 py-3">
                    <StatusBadge status={v.status} />
                  </td>
                  <td className="px-6 py-3">
                    {v.status === "complete" ? (
                      <ConfidenceScore score={v.confidence_score} level={v.confidence_level} className="min-w-[140px]" />
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-6 py-3 text-gray-500">
                    {format(new Date(v.created_at), "d MMM yyyy")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <Pagination page={page} totalPages={data.total_pages} onChange={setPage} />
      )}
    </div>
  );
}
