"use client";
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="container max-w-4xl py-24 text-center">
      <h1 className="text-base font-medium">오류가 발생했습니다</h1>
      <p className="mt-2 text-sm text-muted-foreground">{error.message || "Unknown error"}</p>
      <button
        onClick={reset}
        className="mt-6 rounded border border-border px-4 py-1.5 text-sm hover:bg-secondary"
      >
        다시 시도
      </button>
    </main>
  );
}
