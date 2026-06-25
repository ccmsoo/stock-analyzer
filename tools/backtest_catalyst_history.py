"""
과거(2025년) 촉매 재검증 (3번) — 다른 장세에서도 'AI 촉매점수 → 급등' 성립하나.

hist_px(네이버 sise_day 장기) 급등 + news_search(날짜범위 검색)로 *이전* 뉴스 →
AI가 결과 모르고 촉매 평가 → positive(급등 직전) vs control(안 오름) 정밀도 비교.

CLI:
  python -m tools.backtest_catalyst_history --from 20250801 --until 20251215 --n 80
"""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
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
from tools.news_search import search_titles
from tools.presurge_ai import rate

HIST = Path("/tmp/hist_px.json")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--surge", type=float, default=12.0)
    p.add_argument("--from", dest="dfrom", default="20250801")
    p.add_argument("--until", default="20251215")
    p.add_argument("--n", type=int, default=80)
    p.add_argument("--window", type=int, default=7)
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 없음"); sys.exit(1)
    if not HIST.exists():
        print("⚠️ /tmp/hist_px.json 없음 — fetch_history_naver 먼저"); sys.exit(1)

    px = json.loads(HIST.read_text())
    names = px.get("#names", {})
    tickers = [k for k in px if not k.endswith("#order") and not k.startswith("#") and k not in ("069500", "229200")]

    def chg(t, d):
        order = px[t + "#order"]; m = px[t]
        if d not in m:
            return None
        i = order.index(d)
        if i + 1 >= len(order):
            return None
        prev = m[order[i + 1]]["close"]
        return (m[d]["close"] / prev - 1) * 100 if prev else None

    def maxfwd(t, d, k=5):
        order = px[t + "#order"]; m = px[t]
        i = order.index(d)
        hs = [m[order[i - j]]["high"] for j in range(1, k + 1) if i - j >= 0]
        base = m[d]["close"]
        return (max(hs) / base - 1) * 100 if hs and base else None

    pos, ctrl = [], []
    for t in tickers:
        for i, d in enumerate(px[t + "#order"]):
            if not (args.dfrom <= d <= args.until):
                continue
            c = chg(t, d)
            if c is None:
                continue
            if c >= args.surge:
                pos.append((t, d))
            elif 2 < abs(c) < 8 and i - 5 >= 0:
                # 움직였지만 급등 아님 → 뉴스 있을 가능성↑ (비교 가능한 control)
                fw = maxfwd(t, d, 5)
                if fw is not None and fw < 8:
                    ctrl.append((t, d))

    def sample(lst, n):
        lst = sorted(set(lst))
        if len(lst) <= n:
            return lst
        step = len(lst) / n
        return [lst[int(i * step)] for i in range(n)]
    pos = sample(pos, args.n); ctrl = sample(ctrl, args.n)
    print(f"📊 [2025 검증] positive(급등) {len(pos)} / control {len(ctrl)} — 검색뉴스+AI...\n")

    client = OpenAI(timeout=60, max_retries=1)

    def work(group, t, d):
        nm = names.get(t, t)
        dd = datetime.strptime(d, "%Y%m%d")
        ds = (dd - timedelta(days=args.window)).strftime("%Y%m%d")
        de = (dd - timedelta(days=1)).strftime("%Y%m%d")
        titles = search_titles(nm, ds, de, pages=1, sleep=0.8)
        if not titles:
            return None
        r = rate(client, nm, titles)
        if not r or r["score"] is None:
            return None
        return {"group": group, "ticker": t, "name": nm, "date": d, "score": r["score"],
                "keyword": r["keyword"], "n_titles": len(titles)}

    jobs = [("pos", t, d) for t, d in pos] + [("ctrl", t, d) for t, d in ctrl]
    out = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, g, t, d) for g, t, d in jobs]
        done = 0
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                out.append(r)
            if done % 30 == 0:
                print(f"   {done}/{len(jobs)} (유효 {len(out)})")

    P = [r for r in out if r["group"] == "pos"]
    C = [r for r in out if r["group"] == "ctrl"]
    ap = sum(r["score"] for r in P) / len(P) if P else 0
    ac = sum(r["score"] for r in C) / len(C) if C else 0
    print(f"\n=== [2025 H2] AI 촉매점수 (결과 모르고) ===")
    print(f"   positive(급등직전) 평균 {ap:.1f} (n={len(P)})")
    print(f"   control(안오름)    평균 {ac:.1f} (n={len(C)})")
    for thr in (6, 7):
        ph = sum(1 for r in P if r["score"] >= thr); ch = sum(1 for r in C if r["score"] >= thr)
        prec = ph / (ph + ch) * 100 if (ph + ch) else 0
        rec = ph / len(P) * 100 if P else 0
        print(f"   임계 {thr}+ : 정밀도 {prec:.0f}% (급등 {ph} vs 오인 {ch}) · 재현율 {rec:.0f}%")

    from collections import Counter
    kc = Counter(r["keyword"] for r in P if r["score"] >= 6 and r["keyword"])
    print(f"\n=== 2025 급등 예고 키워드 ===")
    for k, n in kc.most_common(10):
        print(f"   {k[:26]:26} {n}")
    print(f"\n=== 예시 (2025 positive 고득점) ===")
    for r in sorted(P, key=lambda x: -x["score"])[:8]:
        print(f"   {r['name'][:11]:11} {r['date']} score{r['score']:.0f} · {r['keyword'][:24]}")

    json.dump({"results": out}, open(ROOT / "profitability" / "output" / "catalyst_history.json", "w"), ensure_ascii=False, indent=1)
    print("\n💾 저장: profitability/output/catalyst_history.json")


if __name__ == "__main__":
    main()
