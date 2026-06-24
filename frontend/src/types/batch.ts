export type BatchJobStatus = "queued" | "running" | "complete" | "failed";
export type JobResultStatus = "pending" | "success" | "failed" | "skipped";

export interface BatchJob {
  id: string;
  filename: string;
  status: BatchJobStatus;
  total_records: number;
  processed_records: number;
  successful_records: number;
  failed_records: number;
  unclear_records: number;
  progress_percentage: number;
  celery_task_id: string | null;
  uploaded_at: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface JobResult {
  id: string;
  row_number: number;
  status: JobResultStatus;
  error_message: string | null;
  raw_csv_row: Record<string, string>;
  verification: {
    id: string;
    search_id: string;
    status: string;
    full_name: string;
    company_name: string;
    confidence_score: number;
    confidence_level: string;
    created_at: string;
  } | null;
}
