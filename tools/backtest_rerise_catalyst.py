"""
2차 상승 × 촉매 강도 — "강한 촉매면 눌림이 재진입 기회인가" 검증.

path_backtest 의 급등(+경로) × presurge_ai 의 AI 촉매점수 결합:
  급등 종목을 촉매점수(>=6 강 / <6 약)로 나눠 → 2차상승률·D+20·기간내최대 비교.
  강한 촉매 쪽이 더 재상승/회복하면 → "촉매 종목 눌림목 재진입" 전략 근거.

CLI:
  python -m tools.backtest_rerise_catalyst --max 90
"""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import os
from openai import OpenAI
from tools.presurge_ai import rate, pre_titles

PATH_JSON = ROOT / "profitability" / "output" / "path_backtest.json"


def agg(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return (0.0, 0)
    return (sum(vals) / len(vals), len(vals))


def rate_pct(rows, key):
    rows = [r for r in rows if r.get(key) is not None]
    if not rows:
        return 0.0
    return sum(1 for r in rows if r[key]) / len(rows) * 100


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=90)
    p.add_argument("--window", type=int, default=6)
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 없음"); sys.exit(1)
    if not PATH_JSON.exists():
        print("⚠️ path_backtest 먼저 실행"); sys.exit(1)

    rows = json.load(open(PATH_JSON))["rows"]
    # 분산 샘플
    if len(rows) > args.max:
        step = len(rows) / args.max
        rows = [rows[int(i * step)] for i in range(args.max)]
    print(f"📊 급등 {len(rows)}건 × 이전뉴스 AI 촉매평가...\n")

    client = OpenAI(timeout=60, max_retries=1)

    def work(r):
        titles = pre_titles(r["ticker"], r["date"], args.window)
        if not titles:
            return {**r, "cat": None}
        s = rate(client, r["name"], titles)
        return {**r, "cat": (s["score"] if s and s["score"] is not None else None), "kw": s.get("keyword") if s else ""}

    out = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, r) for r in rows]
        done = 0
        for f in as_completed(futs):
            done += 1
            out.append(f.result())
            if done % 30 == 0:
                print(f"   {done}/{len(rows)}")

    scored = [r for r in out if r.get("cat") is not None]
    hi = [r for r in scored if r["cat"] >= 6]
    lo = [r for r in scored if r["cat"] < 6]
    print(f"\n유효(뉴스+점수) {len(scored)}건 — 강한촉매(>=6) {len(hi)} / 약(<6) {len(lo)}\n")

    def show(name, g):
        if not g:
            print(f"   {name}: n=0"); return
        print(f"   {name} (n={len(g)}):")
        print(f"      2차상승률 {rate_pct(g,'rerise'):.0f}%  ·  D+5 {agg([x['r5'] for x in g])[0]:+.1f}%  "
              f"D+20 {agg([x['r20'] for x in g])[0]:+.1f}%  ·  D+20내최대 {agg([x['mg20'] for x in g])[0]:+.1f}%")

    print("=== 촉매 강도별 경로 ===")
    show("🟢 강한 촉매(>=6)", hi)
    show("⚪ 약/무촉매(<6)", lo)

    # D+5 손실 → D+20 회복을 촉매별로
    def recov(g):
        l5 = [x for x in g if x["r5"] is not None and x["r5"] < 0]
        rc = [x for x in l5 if x["r20"] is not None and x["r20"] > 0]
        return len(rc), len(l5)
    rh, lh = recov(hi); rl, ll = recov(lo)
    print("\n=== D+5 손실 → D+20 회복률 (눌림목 재진입 가치) ===")
    print(f"   강한촉매: {rh}/{lh} ({rh/max(lh,1)*100:.0f}%)  ·  약촉매: {rl}/{ll} ({rl/max(ll,1)*100:.0f}%)")

    print("\n=== 강한촉매 2차상승 예시 ===")
    for x in sorted([r for r in hi if r["rerise"]], key=lambda z: -(z["mg20"] or 0))[:8]:
        print(f"   {x['name'][:11]:11} {x['date']} 촉매{x['cat']:.0f} {x.get('kw','')[:16]} → D+5 {x['r5']:+.0f}% D+20 {x['r20']:+.0f}% (최대{x['mg20']:+.0f}%)")

    json.dump({"results": out}, open(ROOT / "profitability" / "output" / "rerise_catalyst.json", "w"), ensure_ascii=False, indent=1)
    print("\n💾 저장: profitability/output/rerise_catalyst.json")


if __name__ == "__main__":
    main()
