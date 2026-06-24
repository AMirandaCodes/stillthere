import api from "./api";
import type { PaginatedResponse } from "@/types/common";
import type {
  VerificationCreateRequest,
  VerificationJobResponse,
  VerificationResult,
  VerificationSummary,
} from "@/types/verification";

export const verificationService = {
  async submitVerification(data: VerificationCreateRequest): Promise<VerificationJobResponse> {
    const res = await api.post<VerificationJobResponse>("/v1/verifications", data);
    return res.data;
  },

  async getVerification(id: string): Promise<VerificationResult> {
    const res = await api.get<VerificationResult>(`/v1/verifications/${id}`);
    return res.data;
  },

  async listVerifications(page = 1, pageSize = 20): Promise<PaginatedResponse<VerificationSummary>> {
    const res = await api.get<PaginatedResponse<VerificationSummary>>("/v1/verifications", {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
};
