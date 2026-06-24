import { clsx } from "clsx";
import type { ConfidenceLevel } from "@/types/verification";

const levelConfig: Record<ConfidenceLevel, { label: string; scoreClass: string; barClass: string }> = {
  high:   { label: "High",   scoreClass: "text-green-700",  barClass: "bg-green-500" },
  medium: { label: "Medium", scoreClass: "text-yellow-700", barClass: "bg-yellow-500" },
  low:    { label: "Low",    scoreClass: "text-red-700",    barClass: "bg-red-500" },
};

interface ConfidenceScoreProps {
  score: number;
  level: ConfidenceLevel;
  className?: string;
}

export default function ConfidenceScore({ score, level, className }: ConfidenceScoreProps) {
  const { label, scoreClass, barClass } = levelConfig[level] ?? levelConfig.low;
  return (
    <div className={clsx("flex flex-col gap-1", className)}>
      <div className="flex items-baseline gap-2">
        <span className={clsx("text-2xl font-bold", scoreClass)}>{score}</span>
        <span className="text-sm text-gray-500">/ 100</span>
        <span className={clsx("text-sm font-medium", scoreClass)}>{label}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-200">
        <div
          className={clsx("h-2 rounded-full transition-all", barClass)}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  );
}
