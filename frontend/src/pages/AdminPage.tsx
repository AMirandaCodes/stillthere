import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { adminService } from "@/services/adminService";
import PageState from "@/components/ui/PageState";
import StatusBadge from "@/components/ui/StatusBadge";
import ConfidenceScore from "@/components/ui/ConfidenceScore";
import Pagination from "@/components/ui/Pagination";

export default function AdminPage() {
  const [page, setPage] = useState(1);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-verifications", page],
    queryFn: () => adminService.listAllVerifications(page),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Admin — All Verifications</h1>
        {data && (
          <p className="mt-1 text-sm text-gray-500">
            {data.total} verification{data.total !== 1 ? "s" : ""} across all users
          </p>
        )}
      </div>

      <PageState
        isLoading={isLoading}
        error={error}
        isEmpty={!!data && data.items.length === 0}
        errorFallback="Failed to load verifications."
        emptySlot={<p className="text-gray-500">No verifications yet.</p>}
      />

      {data && data.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-5 py-3">Name</th>
                <th className="px-5 py-3">Company</th>
                <th className="px-5 py-3">Submitted email</th>
                <th className="px-5 py-3">User account</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Confidence</th>
                <th className="px-5 py-3">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.items.map((v) => (
                <tr key={v.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <Link
                      to={`/results/${v.id}`}
                      className="font-medium text-brand-600 hover:underline"
                    >
                      {v.full_name}
                    </Link>
                  </td>
                  <td className="px-5 py-3 text-gray-700">{v.company_name}</td>
                  <td className="px-5 py-3 text-gray-500">
                    {v.work_email ?? <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-5 py-3">
                    {v.user_email ? (
                      <span className="text-gray-700">{v.user_email}</span>
                    ) : (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
                        Guest
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <StatusBadge status={v.status} />
                  </td>
                  <td className="px-5 py-3">
                    {v.status === "complete" ? (
                      <ConfidenceScore
                        score={v.confidence_score}
                        level={v.confidence_level}
                        className="min-w-[130px]"
                      />
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-gray-500">
                    {format(new Date(v.created_at), "d MMM yyyy, HH:mm")}
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
