export type TriState = "yes" | "no" | "unclear";
export type ConfidenceLevel = "high" | "medium" | "low";
export type VerificationStatus = "pending" | "running" | "complete" | "failed";
export type EvidenceSourceType =
  | "search_result"
  | "company_website"
  | "professional_profile"
  | "business_directory"
  | "other";

export interface VerificationCreateRequest {
  full_name: string;
  company_name: string;
  work_email?: string;
}

export interface VerificationJobResponse {
  search_id: string;
  verification_id: string;
  status: VerificationStatus;
  message: string;
}

export interface EvidenceSource {
  id: string;
  url: string;
  title: string | null;
  snippet: string | null;
  explanation: string | null;
  source_type: EvidenceSourceType;
  collected_at: string;
}

export interface VerificationResult {
  id: string;
  search_id: string;
  status: VerificationStatus;
  full_name: string;
  company_name: string;
  work_email: string | null;

  person_found: TriState;
  appears_associated: TriState;
  found_on_website: TriState;
  company_active: TriState;
  email_match: TriState;
  confidence_score: number;
  confidence_level: ConfidenceLevel;

  evidence_sources: EvidenceSource[];
  useful_links: Record<string, string>;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface VerificationSummary {
  id: string;
  search_id: string;
  status: VerificationStatus;
  full_name: string;
  company_name: string;
  confidence_score: number;
  confidence_level: ConfidenceLevel;
  created_at: string;
}
