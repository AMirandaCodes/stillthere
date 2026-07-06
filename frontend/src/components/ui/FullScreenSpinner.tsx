import Spinner from "@/components/ui/Spinner";

export default function FullScreenSpinner() {
  return (
    <div className="flex h-screen items-center justify-center">
      <Spinner size="lg" />
    </div>
  );
}
