import api, { getPaginated } from "./api";
import type { PaginatedResponse } from "@/types/common";
import type { BatchJob, JobResult } from "@/types/batch";

export const batchService = {
  async uploadCsv(file: File): Promise<BatchJob> {
    const form = new FormData();
    form.append("file", file);
    const res = await api.post<BatchJob>("/v1/batch/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.data;
  },

  async getJob(id: string): Promise<BatchJob> {
    const res = await api.get<BatchJob>(`/v1/batch/${id}`);
    return res.data;
  },

  listJobs(page = 1, pageSize = 20): Promise<PaginatedResponse<BatchJob>> {
    return getPaginated<BatchJob>("/v1/batch", page, pageSize);
  },

  getJobResults(jobId: string, page = 1, pageSize = 50): Promise<PaginatedResponse<JobResult>> {
    return getPaginated<JobResult>(`/v1/batch/${jobId}/results`, page, pageSize);
  },

  async exportCsv(jobId: string): Promise<void> {
    const res = await api.get(`/v1/batch/${jobId}/export`, { responseType: "blob" });
    const url = URL.createObjectURL(res.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `batch_${jobId}_results.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },
};
