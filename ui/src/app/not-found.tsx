import Link from "next/link";

export default function NotFound() {
  return (
    <main className="container max-w-4xl py-24 text-center">
      <h1 className="text-base font-medium">존재하지 않는 페이지</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        시그널 종목이 아닐 수 있습니다.
      </p>
      <Link
        href="/"
        className="mt-6 inline-block text-sm underline underline-offset-4 hover:text-foreground"
      >
        매수 후보로 →
      </Link>
    </main>
  );
}
