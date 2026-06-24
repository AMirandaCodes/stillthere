import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { verificationService } from "@/services/verificationService";
import Spinner from "@/components/ui/Spinner";
import TriStateBadge from "@/components/ui/TriStateBadge";
import StatusBadge from "@/components/ui/StatusBadge";
import ConfidenceScore from "@/components/ui/ConfidenceScore";
import type { TriState } from "@/types/verification";

const triStateRows: { key: keyof { person_found: TriState; appears_associated: TriState; found_on_website: TriState; company_active: TriState; email_match: TriState }; label: string }[] = [
  { key: "person_found",       label: "Person found online" },
  { key: "appears_associated", label: "Appears associated with company" },
  { key: "found_on_website",   label: "Found on company website" },
  { key: "company_active",     label: "Company active / still trading" },
  { key: "email_match",        label: "Email match found" },
];

const SOURCE_TYPE_LABELS: Record<string, string> = {
  search_result:        "Search result",
  company_website:      "Company website",
  professional_profile: "Professional profile",
  business_directory:   "Business directory",
  other:                "Other",
};

export default function VerificationResultPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["verification", id],
    queryFn: () => verificationService.getVerification(id!),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "complete" || status === "failed" ? false : 2000;
    },
    enabled: !!id,
  });

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Spinner size="lg" />
        <p className="text-sm text-gray-500">Loading verification…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-red-200 bg-red-50 p-6 text-center">
        <p className="text-sm text-red-700">{error instanceof Error ? error.message : "Verification not found."}</p>
        <Link to="/" className="mt-4 inline-block text-sm text-brand-600 hover:underline">← New verification</Link>
      </div>
    );
  }

  const isPending = data.status === "pending" || data.status === "running";

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link to="/history" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeft className="h-4 w-4" /> Back to history
      </Link>

      {/* Header card */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{data.full_name}</h1>
            <p className="mt-0.5 text-sm text-gray-500">{data.company_name}</p>
            {data.work_email && <p className="text-sm text-gray-500">{data.work_email}</p>}
          </div>
          <StatusBadge status={data.status} />
        </div>
        <p className="mt-3 text-xs text-gray-400">
          Submitted {format(new Date(data.created_at), "d MMM yyyy, HH:mm")}
        </p>
      </div>

      {/* Polling state */}
      {isPending && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-blue-100 bg-blue-50 py-10">
          <Spinner size="lg" />
          <p className="font-medium text-blue-700">Verifying…</p>
          <p className="text-sm text-blue-500">This typically takes 30–90 seconds. The page updates automatically.</p>
        </div>
      )}

      {/* Failed state */}
      {data.status === "failed" && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6">
          <p className="font-medium text-red-700">Verification failed</p>
          {data.error_message && <p className="mt-1 text-sm text-red-600">{data.error_message}</p>}
        </div>
      )}

      {/* Complete results */}
      {data.status === "complete" && (
        <>
          {/* Tri-state results */}
          <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-100 px-6 py-4">
              <h2 className="font-semibold text-gray-800">Verification Results</h2>
            </div>
            <ul className="divide-y divide-gray-100">
              {triStateRows.map(({ key, label }) => (
                <li key={key} className="flex items-center justify-between px-6 py-3">
                  <span className="text-sm text-gray-700">{label}</span>
                  <TriStateBadge value={data[key] as TriState} />
                </li>
              ))}
            </ul>
          </div>

          {/* Confidence score */}
          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="mb-4 font-semibold text-gray-800">Confidence Score</h2>
            <ConfidenceScore score={data.confidence_score} level={data.confidence_level} />
          </div>

          {/* Evidence sources */}
          {data.evidence_sources.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
              <div className="border-b border-gray-100 px-6 py-4">
                <h2 className="font-semibold text-gray-800">Evidence Sources</h2>
              </div>
              <div className="divide-y divide-gray-100">
                {data.evidence_sources.map((src) => (
                  <div key={src.id} className="px-6 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <a
                          href={src.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:underline"
                        >
                          {src.title ?? src.url}
                          <ExternalLink className="h-3 w-3 shrink-0" />
                        </a>
                        {src.explanation && (
                          <p className="mt-1 text-sm text-gray-600">{src.explanation}</p>
                        )}
                      </div>
                      <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
                        {SOURCE_TYPE_LABELS[src.source_type] ?? src.source_type}
                      </span>
                    </div>
                    <p className="mt-1.5 text-xs text-gray-400">
                      Collected {format(new Date(src.collected_at), "d MMM yyyy, HH:mm")}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Useful links */}
          {Object.keys(data.useful_links).length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
              <h2 className="mb-4 font-semibold text-gray-800">Useful Links</h2>
              <ul className="space-y-2">
                {Object.entries(data.useful_links).map(([label, url]) => (
                  <li key={label}>
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:underline"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
