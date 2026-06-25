"""
밸류체인 전파 백테스트 (가설 A) + 익절/손절 전략 + 승리체인 필터.

가설: 날짜 D 에 어떤 체인의 종목이 급등(리더)하면,
      같은 체인의 *그날 조용했던 동료*를 D+1 시초에 사면 이후 시장보다 오른다.
추가: 익절(TP)/손절(SL) 룰 + 알파 양수 체인만 필터 → 승률 최적화.

캔들은 /tmp 에 캐시 → 파라미터 반복이 빠름 (--refresh 로 강제 재수집).

CLI:
  python -m tools.backtest_propagation
  python -m tools.backtest_propagation --leader 7 --quiet 3 --refresh
"""
from __future__ import annotations
import argparse, json, re, sys, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
from tools.toss_client import get_candles, configured

BENCH = {"KOSPI": "069500", "KOSDAQ": "229200"}
FUND = {"disclosure", "contract", "earnings", "policy"}
CACHE = Path("/tmp/prop_px.json")
# 1차 백테스트에서 alpha 양수로 확인된 체인
WIN_CHAINS = {"반도체 전공정·소재", "반도체 후공정", "2차전지", "정밀화학", "콘텐츠·미디어", "백신·CDMO"}

COARSE_RULES = [
    (r"후공정|패키징|본딩|OSAT|반도체.*(테스트|검사|계측)", "반도체 후공정"),
    (r"반도체|웨이퍼|증착|팹리스|HBM|소부장|소부품", "반도체 전공정·소재"),
    (r"로봇|휴머노이드|감속기|액추에이터|모션", "로봇"),
    (r"진단|POCT|검사키트|자가진단|NGS|혈당", "체외진단"),
    (r"백신|CDMO|CMO|위탁생산|항체|바이오의약품", "백신·CDMO"),
    (r"신약|치료제|항암|면역|유전자치료|알츠하이머|관절염|탈모", "바이오 신약"),
    (r"전선|케이블|전력|변압기", "전력·전선설비"),
    (r"방산|방위|우주|항공|미사일|위성|로켓|MRO", "방산·우주항공"),
    (r"뷰티|화장품", "K-뷰티·화장품"),
    (r"건설|부동산|재개발|분양|도시|플랜트|재건", "건설·플랜트"),
    (r"콘텐츠|미디어|OTT|영상|엔터", "콘텐츠·미디어"),
    (r"2차전지|배터리|양극재", "2차전지"),
    (r"태양광|폴리실리콘|재생에너지|ESS", "태양광·에너지"),
    (r"특수강|강관|철강|알루미늄|압연|봉강|스틸", "철강·금속"),
    (r"정밀화학|화학|페인트|코팅|염료", "정밀화학"),
]
JUNK = re.compile(r"^(서비스|기타|장비|부품|최종재|원소재|조립/제조|유통/판매|지주사)$|테마|수급|소형주|저유동성")


def coarse(ind: str):
    for pat, name in COARSE_RULES:
        if re.search(pat, ind):
            return name
    if not ind or JUNK.search(ind):
        return None
    return ind


POSITION_FLOW = {"원소재": 0, "장비": 1, "부품": 2, "조립/제조": 3, "최종재": 4, "유통/판매": 5, "서비스": 5}


def relation(cp, lp):
    ci, li = POSITION_FLOW.get(cp), POSITION_FLOW.get(lp)
    if ci is None or li is None:
        return "other"
    d = abs(ci - li)
    return "same" if d == 0 else "adjacent" if d == 1 else "far"


def agg(rows, key):
    rs = [r[key] for r in rows if r.get(key) is not None]
    if not rs:
        return (0.0, 0.0, 0)
    return (sum(rs) / len(rs), sum(1 for x in rs if x > 0) / len(rs) * 100, len(rs))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--leader", type=float, default=5.0)
    p.add_argument("--quiet", type=float, default=3.0)
    p.add_argument("--cooldown", type=int, default=5)
    p.add_argument("--from", dest="dfrom", default="20260501")
    p.add_argument("--until", default="20260613")
    p.add_argument("--sleep", type=float, default=0.06)
    p.add_argument("--refresh", action="store_true", help="캔들 캐시 무시하고 재수집")
    args = p.parse_args()
    if not configured():
        print("❌ TOSS 키 없음"); sys.exit(1)

    chains = json.load(open(ROOT / "state" / "chains.json"))["by_ticker"]
    signals = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    meta = {}
    for t, e in chains.items():
        g = coarse(e.get("industry_chain", ""))
        if not g:
            continue
        sig = signals.get(t, {})
        meta[t] = {"name": e.get("name", t), "group": g,
                   "market": "KOSPI" if sig.get("market") == "KOSPI" else "KOSDAQ",
                   "fund": sig.get("trigger_type") in FUND,
                   "pos": e.get("chain_position", "")}
    groups = defaultdict(list)
    for t, m in meta.items():
        groups[m["group"]].append(t)
    groups = {g: ts for g, ts in groups.items() if len(ts) >= 2}
    tickers = sorted({t for ts in groups.values() for t in ts})

    # 캔들 (캐시)
    need = list(BENCH.values()) + tickers
    px = {}
    if CACHE.exists() and not args.refresh:
        px = json.loads(CACHE.read_text())
        miss = [s for s in need if s not in px]
        print(f"🗃️  캐시 로드 ({len(px)//2}종목), 추가 수집 {len(miss)}")
    else:
        miss = need
        print(f"📦 체인 {len(groups)}개 / 종목 {len(tickers)} — 캔들 수집...")
    for sym in miss:
        cs = get_candles(sym); time.sleep(args.sleep)
        px[sym] = {c["date"]: c for c in cs}
        px[sym + "#order"] = [c["date"] for c in cs]
    if miss:
        CACHE.write_text(json.dumps(px))

    def change_on(sym, d):
        order = px.get(sym + "#order", []); m = px.get(sym, {})
        if d not in m:
            return None
        i = order.index(d)
        if i + 1 >= len(order):
            return None
        prev = m[order[i + 1]]["close"]
        return (m[d]["close"] / prev - 1) * 100 if prev else None

    def metrics(sym, d):
        """D+1 시초 진입 기준 raw 수익률 + maxgain/maxdd(D+1~5)"""
        order = px.get(sym + "#order", []); m = px.get(sym, {})
        if d not in m:
            return None
        i = order.index(d)
        if i - 1 < 0:
            return None
        entry = m[order[i - 1]]["open"]
        if not entry:
            return None
        out = {"entry": entry}
        for k in (1, 3, 5, 10):
            j = i - k
            out["r%d" % k] = (m[order[j]]["close"] / entry - 1) * 100 if j >= 0 else None
        hs = [m[order[i - k]]["high"] for k in range(1, 6) if i - k >= 0]
        ls = [m[order[i - k]]["low"] for k in range(1, 6) if i - k >= 0]
        out["maxgain5"] = (max(hs) / entry - 1) * 100 if hs else None
        out["maxdd5"] = (min(ls) / entry - 1) * 100 if ls else None
        return out

    bench_dates = sorted(d for d in px[BENCH["KOSPI"] + "#order"] if args.dfrom <= d <= args.until)
    all_sorted = sorted(set(px[BENCH["KOSPI"] + "#order"]))
    dpos = {d: i for i, d in enumerate(all_sorted)}

    def bench_r(market, d, k):
        bm = metrics(BENCH[market], d)
        return bm["r%d" % k] if bm else None

    peers, leaders_all = [], []
    last = {}
    for d in bench_dates:
        for g, ts in groups.items():
            chgs = {t: change_on(t, d) for t in ts}
            leaders = [t for t in ts if (chgs[t] or -99) >= args.leader]
            if not leaders:
                continue
            for t in leaders:
                mt = metrics(t, d)
                if mt:
                    leaders_all.append({"x5": (mt["r5"] - bench_r(meta[t]["market"], d, 5)) if mt["r5"] is not None and bench_r(meta[t]["market"], d, 5) is not None else None})
            quiets = [t for t in ts if t not in leaders and chgs[t] is not None and abs(chgs[t]) < args.quiet]
            for t in quiets:
                if t in last and dpos.get(d, 0) - last[t] < args.cooldown:
                    continue
                last[t] = dpos.get(d, 0)
                mt = metrics(t, d)
                if not mt:
                    continue
                m = meta[t]
                _pref = {"same": 0, "adjacent": 1, "far": 2, "other": 3}
                _rels = [relation(m["pos"], meta[ld]["pos"]) for ld in leaders]
                rel = min(_rels, key=lambda x: _pref[x]) if _rels else "other"
                rec = {"ticker": t, "name": m["name"], "group": g, "date": d, "fund": m["fund"],
                       "win_chain": g in WIN_CHAINS, "n_leaders": len(leaders), "relation": rel,
                       "maxgain5": mt["maxgain5"], "maxdd5": mt["maxdd5"]}
                for k in (1, 3, 5, 10):
                    rec["r%d" % k] = mt["r%d" % k]
                    b = bench_r(m["market"], d, k)
                    rec["x%d" % k] = (mt["r%d" % k] - b) if mt["r%d" % k] is not None and b is not None else None
                peers.append(rec)

    print(f"\n전파후보 {len(peers)} / 리더 {len(leaders_all)}  (leader>={args.leader}% quiet<{args.quiet}%)\n")

    # 보유전략 (시장대비)
    print("=== 전파후보 보유 (시장대비 alpha) ===")
    for k in (1, 3, 5, 10):
        a = agg(peers, "x%d" % k)
        print(f"   D+{k:<2} {a[0]:>+5.1f}%  승률 {a[1]:>4.0f}%")
    a = agg(leaders_all, "x5")
    print(f"   (리더추격 D+5 {a[0]:+.1f}% 승률 {a[1]:.0f}%)")

    # 익절/손절 전략 시뮬 (raw 실현)
    def sim(rows, tp, sl):
        rets = []
        for r in rows:
            mg, md, c5 = r.get("maxgain5"), r.get("maxdd5"), r.get("r5")
            if md is not None and md <= -sl:
                rets.append(-sl)          # 손절 먼저(보수적)
            elif mg is not None and mg >= tp:
                rets.append(tp)           # 익절
            elif c5 is not None:
                rets.append(c5)           # D+5 종가 청산
        if not rets:
            return (0, 0, 0)
        return (sum(rets) / len(rets), sum(1 for x in rets if x > 0) / len(rets) * 100, len(rets))

    print("\n=== 익절/손절 전략 (raw 실현수익, 전체 전파후보) ===")
    print(f"   {'전략':16} {'평균':>7} {'승률':>6}")
    for tp, sl in [(5, 5), (7, 5), (7, 7), (10, 5), (10, 7), (10, 10), (15, 7)]:
        s = sim(peers, tp, sl)
        print(f"   TP+{tp}/SL-{sl:<3}     {s[0]:>+6.1f}% {s[1]:>5.0f}%  (n={s[2]})")

    print("\n=== 승리체인 필터 ONLY (반도체/2차전지/정밀화학/콘텐츠/백신) ===")
    wc = [r for r in peers if r["win_chain"]]
    a5 = agg(wc, "x5")
    print(f"   보유 D+5 alpha {a5[0]:+.1f}% 승률 {a5[1]:.0f}% (n={a5[2]})")
    print(f"   {'전략':16} {'평균':>7} {'승률':>6}")
    for tp, sl in [(5, 5), (7, 5), (7, 7), (10, 7), (10, 10), (15, 7)]:
        s = sim(wc, tp, sl)
        print(f"   TP+{tp}/SL-{sl:<3}     {s[0]:>+6.1f}% {s[1]:>5.0f}%  (n={s[2]})")

    print("\n=== [최적 조합] 승리체인 전파후보 — 구간별 alpha ===")
    best = [r for r in peers if r["win_chain"]]
    for k in (1, 3, 5):
        a = agg(best, "x%d" % k)
        print(f"   D+{k} alpha {a[0]:>+5.1f}%  승률 {a[1]:>4.0f}%  n={a[2]}")
    m2 = [r for r in best if r.get("n_leaders", 1) >= 2]
    a = agg(m2, "x5")
    print(f"   +리더2개이상 체인: D+5 {a[0]:+.1f}% 승률 {a[1]:.0f}% n={a[2]}")
    print("   예시 (승리체인 전파후보, D+5 alpha 상위):")
    for r in sorted([x for x in best if x.get("x5") is not None], key=lambda x: -x["x5"])[:6]:
        print(f"     {r['name'][:11]:11} {r['group'][:12]:12} {r['date']}  D+5 {r['x5']:+.0f}%")

    print("\n=== 단계 관계별 전파 alpha (D+5, 전체) — 엔진 가정 검증 ===")
    byr = defaultdict(list)
    for r in peers:
        byr[r.get("relation", "other")].append(r)
    for rk in ["same", "adjacent", "far", "other"]:
        a = agg(byr.get(rk, []), "x5")
        if a[2]:
            print(f"   {rk:9} n={a[2]:>3}  D+5 {a[0]:>+5.1f}%  승률 {a[1]:>4.0f}%")

    out = ROOT / "profitability" / "output" / "propagation_backtest.json"
    json.dump({"params": vars(args), "n_peer": len(peers), "peers": peers}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    main()
