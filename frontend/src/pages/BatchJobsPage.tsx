import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { Download, PlusCircle } from "lucide-react";
import { clsx } from "clsx";
import { batchService } from "@/services/batchService";
import Spinner from "@/components/ui/Spinner";
import PageState from "@/components/ui/PageState";
import StatusBadge from "@/components/ui/StatusBadge";
import Pagination from "@/components/ui/Pagination";
import type { BatchJob } from "@/types/batch";

function ProgressBar({ job }: { job: BatchJob }) {
  const pct = job.progress_percentage ?? 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-gray-500">
        <span>{job.processed_records} / {job.total_records} rows</span>
        <span>{pct}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-200">
        <div
          className="h-2 rounded-full bg-brand-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex gap-3 text-xs text-gray-500">
        <span className="text-green-600">{job.successful_records} ok</span>
        <span className="text-red-600">{job.failed_records} failed</span>
        <span className="text-gray-400">{job.unclear_records} unclear</span>
      </div>
    </div>
  );
}

export default function BatchJobsPage() {
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const location = useLocation();
  const highlightId: string | undefined = (location.state as { highlightId?: string })?.highlightId;

  const { data, isLoading, error } = useQuery({
    queryKey: ["batchJobs", page],
    queryFn: () => batchService.listJobs(page),
    refetchInterval: (query) => {
      const jobs = query.state.data?.items ?? [];
      return jobs.some((j) => j.status === "running" || j.status === "queued") ? 5000 : false;
    },
  });

  async function handleExport(jobId: string) {
    setExporting(jobId);
    setExportError(null);
    try {
      const blob = await batchService.exportCsv(jobId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `batch_${jobId}_results.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed. Please try again.");
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Batch Jobs</h1>
          {data && (
            <p className="mt-1 text-sm text-gray-500">{data.total} job{data.total !== 1 ? "s" : ""} total</p>
          )}
        </div>
        <Link
          to="/batch"
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
        >
          <PlusCircle className="h-4 w-4" /> New upload
        </Link>
      </div>

      {exportError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {exportError}
        </div>
      )}

      <PageState
        isLoading={isLoading}
        error={error}
        isEmpty={!!data && data.items.length === 0}
        errorFallback="Failed to load jobs."
        emptySlot={
          <>
            <p className="text-gray-500">No batch jobs yet.</p>
            <Link to="/batch" className="mt-3 inline-block text-sm text-brand-600 hover:underline">
              Upload your first CSV →
            </Link>
          </>
        }
      />

      {data && data.items.length > 0 && (
        <div className="space-y-4">
          {data.items.map((job) => (
            <div
              key={job.id}
              className={clsx(
                "rounded-xl border bg-white p-6 shadow-sm",
                highlightId === job.id ? "border-brand-400 ring-1 ring-brand-400" : "border-gray-200"
              )}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate font-medium text-gray-900">{job.filename}</p>
                    <StatusBadge status={job.status} />
                  </div>
                  <p className="mt-0.5 text-xs text-gray-400">
                    Uploaded {format(new Date(job.uploaded_at), "d MMM yyyy, HH:mm")}
                    {job.completed_at && ` · Completed ${format(new Date(job.completed_at), "d MMM yyyy, HH:mm")}`}
                  </p>
                </div>
                <button
                  onClick={() => handleExport(job.id)}
                  disabled={job.status !== "complete" || exporting === job.id}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {exporting === job.id ? <Spinner size="sm" /> : <Download className="h-4 w-4" />}
                  Export CSV
                </button>
              </div>
              <div className="mt-4">
                <ProgressBar job={job} />
              </div>
            </div>
          ))}
        </div>
      )}

      {data && (
        <Pagination page={page} totalPages={data.total_pages} onChange={setPage} />
      )}
    </div>
  );
}
