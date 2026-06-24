import { clsx } from "clsx";

interface SpinnerProps {
  className?: string;
  size?: "sm" | "md" | "lg";
}

const pxSizes = { sm: 16, md: 32, lg: 48 };

export default function Spinner({ className, size = "md" }: SpinnerProps) {
  const px = pxSizes[size];
  return (
    <svg
      className={clsx("animate-spin text-brand-500", className)}
      style={{ width: px, height: px, minWidth: px, flexShrink: 0 }}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-label="Loading"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" strokeOpacity="0.25" />
      <path
        fillOpacity="0.75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}
