import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadCloud, FileText } from "lucide-react";
import { clsx } from "clsx";
import { batchService } from "@/services/batchService";
import Spinner from "@/components/ui/Spinner";

export default function BatchUploadPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function selectFile(f: File | null) {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".csv")) {
      setError("Only CSV files are accepted.");
      return;
    }
    setError(null);
    setFile(f);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    selectFile(e.dataTransfer.files[0] ?? null);
  }

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const job = await batchService.uploadCsv(file);
      navigate("/jobs", { state: { highlightId: job.id } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setUploading(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Batch Upload</h1>
        <p className="mt-1 text-sm text-gray-500">
          Upload a CSV file to verify multiple contacts at once.
        </p>
      </div>

      {/* Drop zone */}
      <div
        className={clsx(
          "cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition-colors",
          dragging ? "border-brand-400 bg-brand-50" : "border-gray-300 bg-white hover:border-brand-300 hover:bg-gray-50"
        )}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => selectFile(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <div className="flex flex-col items-center gap-2">
            <FileText className="h-10 w-10 text-brand-500" />
            <p className="font-medium text-gray-800">{file.name}</p>
            <p className="text-sm text-gray-500">{(file.size / 1024).toFixed(1)} KB · Click to change</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 text-gray-400">
            <UploadCloud className="h-10 w-10" />
            <p className="font-medium text-gray-600">Drag & drop a CSV file here</p>
            <p className="text-sm">or click to browse</p>
          </div>
        )}
      </div>

      {/* Format hint */}
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-600">
        <p className="font-medium text-gray-700">CSV format</p>
        <ul className="mt-2 list-inside list-disc space-y-1 text-gray-500">
          <li>Required columns: <code className="rounded bg-gray-200 px-1">Name</code>, <code className="rounded bg-gray-200 px-1">Company</code></li>
          <li>Optional column: <code className="rounded bg-gray-200 px-1">Email</code></li>
          <li>Maximum 50 rows per upload</li>
          <li>Rows missing Name or Company are skipped automatically</li>
        </ul>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <button
        onClick={handleUpload}
        disabled={!file || uploading}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
      >
        {uploading ? <Spinner size="sm" className="text-white" /> : <UploadCloud className="h-4 w-4" />}
        {uploading ? "Uploading…" : "Upload and start verification"}
      </button>
    </div>
  );
}
