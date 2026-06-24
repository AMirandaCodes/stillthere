import api from "./api";
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

  async listJobs(page = 1, pageSize = 20): Promise<PaginatedResponse<BatchJob>> {
    const res = await api.get<PaginatedResponse<BatchJob>>("/v1/batch", {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },

  async getJobResults(jobId: string, page = 1, pageSize = 50): Promise<PaginatedResponse<JobResult>> {
    const res = await api.get<PaginatedResponse<JobResult>>(`/v1/batch/${jobId}/results`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
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
