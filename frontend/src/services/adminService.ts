import api from "./api";
import type { PaginatedResponse } from "@/types/common";
import type { AdminVerificationSummary } from "@/types/verification";

export const adminService = {
  async listAllVerifications(
    page = 1,
    pageSize = 20,
  ): Promise<PaginatedResponse<AdminVerificationSummary>> {
    const res = await api.get<PaginatedResponse<AdminVerificationSummary>>(
      "/v1/admin/verifications",
      { params: { page, page_size: pageSize } },
    );
    return res.data;
  },
};
