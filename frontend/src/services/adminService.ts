import { getPaginated } from "./api";
import type { PaginatedResponse } from "@/types/common";
import type { AdminVerificationSummary } from "@/types/verification";

export const adminService = {
  listAllVerifications(
    page = 1,
    pageSize = 20,
  ): Promise<PaginatedResponse<AdminVerificationSummary>> {
    return getPaginated<AdminVerificationSummary>("/v1/admin/verifications", page, pageSize);
  },
};
