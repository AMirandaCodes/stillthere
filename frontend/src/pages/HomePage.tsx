import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Search } from "lucide-react";
import { verificationService } from "@/services/verificationService";
import Spinner from "@/components/ui/Spinner";

const schema = z.object({
  full_name:    z.string().min(1, "Full name is required"),
  company_name: z.string().min(1, "Company name is required"),
  work_email:   z.string().email("Enter a valid email address").or(z.literal("")).optional(),
});
type FormData = z.infer<typeof schema>;

export default function HomePage() {
  const navigate = useNavigate();
  const [serverError, setServerError] = useState<string | null>(null);

  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: "", company_name: "", work_email: "" },
  });

  async function onSubmit(data: FormData) {
    setServerError(null);
    try {
      const job = await verificationService.submitVerification({
        full_name:    data.full_name,
        company_name: data.company_name,
        work_email:   data.work_email || undefined,
      });
      navigate(`/results/${job.verification_id}`);
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Failed to submit verification");
    }
  }

  return (
    <div className="mx-auto max-w-xl pt-6">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-bold text-gray-900">Verify a Contact</h1>
        <p className="mt-1 text-sm text-gray-500">
          Enter a person's name and company to check whether they are still employed there.
        </p>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              Full name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              {...register("full_name")}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              placeholder="e.g. Jane Smith"
            />
            {errors.full_name && <p className="mt-1 text-xs text-red-600">{errors.full_name.message}</p>}
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              Company <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              {...register("company_name")}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              placeholder="e.g. Acme Corp"
            />
            {errors.company_name && <p className="mt-1 text-xs text-red-600">{errors.company_name.message}</p>}
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              Work email <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="email"
              {...register("work_email")}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              placeholder="jane@acmecorp.com"
            />
            {errors.work_email && <p className="mt-1 text-xs text-red-600">{errors.work_email.message}</p>}
          </div>

          {serverError && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{serverError}</div>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {isSubmitting ? <Spinner size="sm" className="text-white" /> : <Search className="h-4 w-4" />}
            {isSubmitting ? "Submitting…" : "Run verification"}
          </button>
        </form>
      </div>

      <p className="mt-4 text-center text-xs text-gray-400">
        Results are based on publicly available information. Processing typically takes 30–90 seconds.
      </p>
    </div>
  );
}
