"""
급등 '이전' 기사 백테스트 (Phase 2) — "오르기 전 기사 + 결정적 키워드".

1) 토스 캔들에서 급등(>=surge%) 이벤트 검출
2) 각 급등의 *이전* N일 뉴스(deep)에서 촉매 키워드 탐지
3) 측정: 급등 중 '이전 기사가 예고한' 비율(base rate), 리드타임, 결정적 키워드 빈도

CLI:
  python -m tools.presurge_scan
  python -m tools.presurge_scan --surge 12 --window 6 --max 150 --from 20260401 --until 20260613
"""
from __future__ import annotations
import argparse, json, re, sys, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
from tools.toss_client import get_candles, configured
from collectors.news_collector import collect_news_for_stock

KW_CACHE = Path("/tmp/kw_px.json")

# 촉매 키워드 (오르기 전 신호) — 정규식: 라벨
CATALYSTS = [
    (r"수주|계약 체결|공급계약|납품|공급권|독점 ?공급", "수주/계약"),
    (r"FDA|EMA|임상\s*[123]?상?|품목허가|신약 허가|승인|허가 획득", "임상/허가"),
    (r"신약|치료제|항암|면역|백신|기술이전|라이선스|특허", "신약/기술"),
    (r"흑자전환|흑자 ?전환|어닝 ?서프라이즈|실적 ?개선|최대 실적|호실적", "실적"),
    (r"인수|합병|경영권|M&A|지분 인수|최대주주 변경", "M&A"),
    (r"국책|정부 과제|과제 선정|국가과제|예타|정책 수혜", "정책/국책"),
    (r"증설|양산|착공|준공|시설투자|공장 신설", "투자/증설"),
    (r"수출|첫 수출|수출 계약|해외 진출|글로벌 공급", "수출"),
    (r"자사주|자기주식|소각|배당 확대", "주주환원"),
    (r"세계 ?최초|국내 ?최초|독점|개발 성공|상용화", "기술성과"),
]


def classify(text):
    hits = []
    for pat, label in CATALYSTS:
        if re.search(pat, text):
            hits.append(label)
    return hits


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--surge", type=float, default=12.0, help="급등 기준 %")
    p.add_argument("--window", type=int, default=6, help="이전 뉴스 윈도우(일)")
    p.add_argument("--max", type=int, default=150, help="샘플 급등 수")
    p.add_argument("--from", dest="dfrom", default="20260401")
    p.add_argument("--until", default="20260613")
    p.add_argument("--cooldown", type=int, default=5)
    p.add_argument("--sleep", type=float, default=0.25)
    args = p.parse_args()
    if not configured():
        print("❌ TOSS 키 없음"); sys.exit(1)

    # 캔들 (kw 캐시 재사용 — 시그널 종목)
    if not KW_CACHE.exists():
        print("⚠️ /tmp/kw_px.json 없음 — backtest_keywords 먼저 실행하세요"); sys.exit(1)
    px = json.loads(KW_CACHE.read_text())
    tickers = sorted({k for k in px if "#order" not in k} - set(["069500", "229200"]))
    names = {}
    try:
        sigs = json.load(open(ROOT / "state" / "signals.json"))["signals"]
        names = {t: sigs[t].get("name", t) for t in sigs}
    except Exception:
        pass

    # 급등 이벤트 검출
    surges = []
    for t in tickers:
        order = px[t + "#order"]; m = px[t]
        for i, d in enumerate(order):
            if not (args.dfrom <= d <= args.until):
                continue
            if i + 1 >= len(order):
                continue
            prev = m[order[i + 1]]["close"]
            if not prev:
                continue
            chg = (m[d]["close"] / prev - 1) * 100
            if chg >= args.surge:
                surges.append({"ticker": t, "date": d, "chg": round(chg, 1)})
    # 쿨다운 dedup (종목별 5일내 중복 급등은 첫번째만)
    surges.sort(key=lambda x: (x["ticker"], x["date"]))
    dedup, last = [], {}
    alld = sorted({d for t in tickers for d in px[t + "#order"]})
    dpos = {d: i for i, d in enumerate(alld)}
    for s in surges:
        t, d = s["ticker"], s["date"]
        if t in last and dpos.get(d, 0) - last[t] < args.cooldown:
            continue
        last[t] = dpos.get(d, 0)
        dedup.append(s)
    dedup.sort(key=lambda x: -x["chg"])
    sample = dedup[: args.max]
    print(f"📈 급등(>={args.surge}%) {len(dedup)}건 → 샘플 {len(sample)}건, 이전 {args.window}일 뉴스 스캔...\n")

    results = []
    for i, s in enumerate(sample):
        t, d = s["ticker"], s["date"]
        try:
            arts = collect_news_for_stock(t, d, articles_per_stock=30, days_before=args.window, deep=True)
        except Exception:
            arts = []
        time.sleep(args.sleep)
        # 급등일 '이전' 기사만
        dd = datetime.strptime(d, "%Y%m%d")
        pre = []
        for a in arts:
            try:
                adt = datetime.strptime(a["date"], "%Y.%m.%d %H:%M")
            except Exception:
                continue
            if adt.date() < dd.date():
                pre.append((adt, a["title"]))
        cats = set()
        lead = None
        for adt, title in pre:
            h = classify(title)
            if h:
                cats.update(h)
                ld = (dd.date() - adt.date()).days
                lead = ld if lead is None else max(lead, ld)  # 가장 이른 촉매까지 리드
        results.append({"ticker": t, "name": names.get(t, t), "date": d, "chg": s["chg"],
                        "n_pre": len(pre), "cats": sorted(cats), "lead": lead})
        if (i + 1) % 40 == 0:
            print(f"   {i + 1}/{len(sample)}")

    with_pre = [r for r in results if r["n_pre"] > 0]
    with_cat = [r for r in results if r["cats"]]
    print(f"\n=== 결과 (급등 {len(results)}건) ===")
    print(f"   이전 기사 존재: {len(with_pre)}건 ({len(with_pre)/len(results)*100:.0f}%)")
    print(f"   이전 '촉매' 기사 존재(예고됨): {len(with_cat)}건 ({len(with_cat)/len(results)*100:.0f}%)")
    leads = [r["lead"] for r in with_cat if r["lead"] is not None]
    if leads:
        print(f"   평균 리드타임: {sum(leads)/len(leads):.1f}일 (촉매 기사~급등)")

    print(f"\n=== 🔑 오르기 전 결정적 키워드 빈도 (예고된 급등 중) ===")
    cf = defaultdict(int)
    for r in with_cat:
        for c in r["cats"]:
            cf[c] += 1
    for c, n in sorted(cf.items(), key=lambda x: -x[1]):
        print(f"   {c:12} {n:>3}건 ({n/len(with_cat)*100:.0f}%)")

    print(f"\n=== 예시 (예고된 급등) ===")
    for r in sorted(with_cat, key=lambda x: -x["chg"])[:12]:
        print(f"   {r['name'][:11]:11} {r['date']} +{r['chg']:.0f}%  리드{r['lead']}일  {'/'.join(r['cats'])}")

    out = ROOT / "profitability" / "output" / "presurge_scan.json"
    json.dump({"params": vars(args), "n": len(results), "results": results}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    main()
