import { NextResponse } from "next/server";
import { getHoldings } from "@/lib/toss";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const holdings = await getHoldings();
    return NextResponse.json(holdings);
  } catch (e) {
    return NextResponse.json(
      { connected: false, items: [], error: String(e) },
      { status: 200 },
    );
  }
}
