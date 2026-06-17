/**
 * GET /api/quotes?tickers=000650,454910,...
 *  종목별 현재가 batch.
 */
import { NextResponse, type NextRequest } from "next/server";
import { fetchManyQuotes } from "@/lib/price";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const param = url.searchParams.get("tickers") || "";
  const tickers = param
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  if (tickers.length === 0) {
    return NextResponse.json([]);
  }
  const quotes = await fetchManyQuotes(tickers);
  const result = tickers.map((t) => {
    const q = quotes.get(t);
    return {
      ticker: t,
      current_price: q?.current_price ?? null,
      today_change_pct: q?.today_change_pct ?? null,
      today_from_open_pct: q?.today_from_open_pct ?? null,
    };
  });
  return NextResponse.json(result, {
    headers: { "Cache-Control": "public, s-maxage=60" },
  });
}
