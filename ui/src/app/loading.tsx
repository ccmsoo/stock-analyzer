export default function Loading() {
  return (
    <main className="container max-w-4xl py-12">
      <div className="space-y-3">
        <div className="h-4 w-32 animate-pulse rounded bg-secondary" />
        <div className="h-3 w-64 animate-pulse rounded bg-secondary/60" />
        <div className="mt-8 space-y-6">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="space-y-2 border-b border-border pb-6">
              <div className="flex justify-between">
                <div className="h-4 w-40 animate-pulse rounded bg-secondary" />
                <div className="h-4 w-12 animate-pulse rounded bg-secondary" />
              </div>
              <div className="h-3 w-3/4 animate-pulse rounded bg-secondary/60" />
              <div className="grid grid-cols-4 gap-2 pt-2">
                {[...Array(4)].map((_, j) => (
                  <div key={j} className="h-8 animate-pulse rounded bg-secondary/40" />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
