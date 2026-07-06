const HEALTH_URL = "/api/v1/health";

export default function WakeupHint() {
  return (
    <p className="mt-2 max-w-xs text-center text-xs text-gray-400">
      Taking longer than expected? The server may be waking up after a period of
      inactivity.{" "}
      <a
        href={HEALTH_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="underline hover:text-gray-600"
      >
        Check server status
      </a>
      .
    </p>
  );
}
