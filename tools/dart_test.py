"""공시 가설 검정 — 특정 공시가 이후 수익을 예측하는가.

가격·수급 가설 17개가 전부 기각된 뒤 여는 세 번째 축(docs/verdict_20260828.md).
검정 규칙은 앞과 **동일하게** 유지한다. 여기서 기준을 풀면 지금까지 한 게 무의미해진다:
  · 같은 (날짜, 시장) 안에서 해당 공시가 난 종목 − 그날 그 시장 전체 평균 (레짐 중립)
  · 거래일 클러스터 t (유효 표본 = 공시가 난 거래일 수)
  · 본페로니 보정 · 홀드아웃 · 널 테스트
  · 진입은 **공시 다음날 시가** (장중·장후 공시를 당일 종가로 잡으면 look-ahead)
  · ETF/ETN 제외 · 왕복비용 0.4%

공시 → 종목 매칭은 **회사명**으로 한다 (DART 목록엔 종목코드가 없다).

사용:  venv/bin/python -m tools.dart_test
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import statistics as st
from collections import defaultdict

from tools.universe_filter import filter_meta

COST = 0.4
MIN_GROUP = 40        # 그날 그 시장 비교군이 이보다 적으면 버린다


# 검정할 공시 유형 — 보고서명에 이 문자열이 들어가면 해당 유형.
# "호재로 보이는 것"을 넣되, 판정은 데이터에 맡긴다.
EVENTS = {
    "공급계약": ["단일판매·공급계약체결", "단일판매ㆍ공급계약체결", "공급계약"],
    "자사주취득": ["자기주식취득결정", "자기주식취득신탁계약체결결정"],
    "자사주소각": ["주식소각결정"],
    "무상증자": ["무상증자결정"],
    "유상증자": ["유상증자결정"],
    "타법인취득": ["타법인주식및출자증권취득결정"],
    "합병": ["회사합병결정", "주식교환·이전"],
    "최대주주변경": ["최대주주변경"],
    "실적공시": ["매출액또는손익구조", "영업(잠정)실적"],
    "기술이전": ["기술이전", "라이선스"],
    "전환사채": ["전환사채권발행결정"],
    "특허취득": ["특허권취득"],
    "소송": ["소송등의제기"],
    "유형자산취득": ["유형자산취득결정"],
}


def session(t: str) -> str:
    """공시 시각 → 세션. **장중 공시는 그날 이미 반응이 나온 뒤**라 장후와 섞으면 안 된다.
    장후(15:30~)가 깨끗한 케이스: 시장이 아직 반응하지 않았고 다음날 시가 진입이 정확하다."""
    try:
        h, m = int(t[:2]), int(t[3:5])
    except Exception:
        return "?"
    x = h * 60 + m
    if x < 9 * 60:
        return "pre"
    if x < 15 * 60 + 30:
        return "intra"
    return "post"


def is_correction(report: str) -> bool:
    """[기재정정]·[첨부정정] 등은 **이전 공시의 재제출**이다. 정보는 이미 공개됐고
    원공시는 몇 주 전일 수도 있다. 이걸 새 이벤트로 세면 검정이 통째로 오염된다.
    실측: 장후 분류 이벤트의 **43.5%**가 정정공시였다."""
    return report.lstrip().startswith("[") and "정정" in report[:10]


def classify(report: str) -> list[str]:
    r = report.replace(" ", "")
    return [k for k, pats in EVENTS.items() if any(p.replace(" ", "") in r for p in pats)]


def build(dart, px, meta, sessions=None, corrections=False):
    """공시일 → {종목코드: [유형]}. sessions로 시각대, corrections로 정정공시 포함 여부."""
    name2code = {}
    for c, v in meta.items():
        n = (v.get("name") or "").strip()
        if n:
            name2code.setdefault(n, c)
    hit = defaultdict(lambda: defaultdict(set))
    unmatched = 0
    for ymd, items in dart.items():
        for it in items:
            if sessions and session(it.get("time", "")) not in sessions:
                continue
            if is_correction(it["report"]) != corrections:
                continue
            kinds = classify(it["report"])
            if not kinds:
                continue
            code = name2code.get(it["corp"].strip())
            if not code or code not in px:
                unmatched += 1
                continue
            hit[ymd][code].update(kinds)
    return hit, unmatched


def precompute(px, holds):
    """(종목, 공시일) → 미래수익을 한 번에 계산해 캐시한다.

    날짜마다 전 종목을 다시 정렬하면 400일 × 600종목에서 견딜 수 없이 느려진다.
    공시일 **다음 거래일 시가** 진입 · hold 거래일 보유 · 왕복비용 차감.
    공시일이 휴장일이어도 '그 다음 거래일'로 자연스럽게 이어진다."""
    fw = {h: {} for h in holds}
    for code, s in px.items():
        days = sorted(s)
        idx_of = {d: i for i, d in enumerate(days)}
        for i, d in enumerate(days):
            e = s[days[i]]["open"]
            if not e:
                continue
            for h in holds:
                if i + h < len(days):
                    fw[h][(code, d)] = (s[days[i + h]]["close"] / e - 1) * 100 - COST
        # 공시일 → 다음 거래일 매핑 (휴장일 공시도 처리)
        px[code]["__days__"] = days
    return fw


def next_day_map(px):
    """모든 달력일 → 그 다음 거래일. 공시일이 휴장/장후여도 진입일을 정확히 잡는다."""
    alld = sorted({d for c, s in px.items() for d in s if not d.startswith("__")})
    return alld


def fwd(px, code, ymd, hold, fw=None, alld=None):
    """캐시(fw)가 있으면 O(1). 없으면 예전 경로로 계산."""
    s = px.get(code) or {}
    days = s.get("__days__") or sorted(d for d in s if not d.startswith("__"))
    nxt = None
    for d in days:
        if d > ymd:
            nxt = d
            break
    if nxt is None:
        return None
    if fw is not None:
        return fw[hold].get((code, nxt))
    i = days.index(nxt)
    if i + hold >= len(days):
        return None
    e = s[days[i]]["open"]
    if not e:
        return None
    return (s[days[i + hold]]["close"] / e - 1) * 100 - COST


def tstat(by_date):
    dm = [sum(v) / len(v) for v in by_date.values()]
    if len(dm) < 8:
        return None
    m = sum(dm) / len(dm)
    se = st.stdev(dm) / math.sqrt(len(dm))
    return m, (m / se if se else 0.0), len(dm)


def baselines(hit, px, meta, hold, fw):
    """(공시일, 시장) → 그날 그 시장 전체 평균 수익. 유형마다 다시 계산하지 않는다."""
    out = {}
    for ymd in hit:
        base = defaultdict(list)
        for c, v in meta.items():
            r = fwd(px, c, ymd, hold, fw)
            if r is not None:
                base[v.get("market")].append(r)
        for mk, arr in base.items():
            if len(arr) >= MIN_GROUP:
                out[(ymd, mk)] = sum(arr) / len(arr)
    return out


def evaluate(hit, px, meta, kind, hold, fw, base, dates=None):
    """해당 공시가 난 종목 − 그날 그 시장 전체 평균."""
    by_date = defaultdict(list)
    n_ev = [0]
    for ymd, codes in hit.items():
        if dates is not None and ymd not in dates:
            continue
        per_mk = defaultdict(list)
        for c, ks in codes.items():
            if kind not in ks:
                continue
            mk = (meta.get(c) or {}).get("market")
            if (ymd, mk) not in base:
                continue
            r = fwd(px, c, ymd, hold, fw)
            if r is not None:
                per_mk[mk].append(r)
        for mk, sel in per_mk.items():
            by_date[ymd].append(sum(sel) / len(sel) - base[(ymd, mk)])
            n_ev[0] += len(sel)
    # 반환하는 n은 **이벤트 수**다. (날짜×시장) 셀 수를 쓰면 유형이 부당하게 탈락한다 —
    # 실제로 자사주취득·소각·전환사채가 이 버그로 검정에서 빠졌었다.
    return tstat(by_date), n_ev[0]


def null_check(hit, px, meta, hold, fw, base, n_trials=20, seed0=0):
    """가짜 이벤트를 실제와 같은 빈도로 무작위 배치 → t 분포.

    |t|>2가 5% 안팎이 아니라 훨씬 많이 나오면 이 검정 틀 자체가 거짓 양성을 만든다는 뜻이고,
    그 위에서 내린 판정은 전부 무효다. **새 검정 방식마다 여기부터 통과시킨다.**"""
    import random
    codes = list(meta)
    sizes = {ymd: len(c) for ymd, c in hit.items()}
    ts = []
    for trial in range(n_trials):
        rnd = random.Random(seed0 + trial * 7919)
        fake = {ymd: {c: {"NULL"} for c in rnd.sample(codes, min(k, len(codes)))}
                for ymd, k in sizes.items()}
        r, _ = evaluate(fake, px, meta, "NULL", hold, fw, base)
        if r:
            ts.append(r[1])
    return sorted(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="*", default=[3, 10])
    ap.add_argument("--dart", default="state/dart_days.pkl")
    ap.add_argument("--null", action="store_true", help="널 테스트만 실행")
    ap.add_argument("--corrections", action="store_true",
                    help="정정공시만 검정 (기본은 원공시만). 정정은 정보가 이미 나간 뒤다")
    ap.add_argument("--session", nargs="*", default=["post"],
                    choices=["pre", "intra", "post"],
                    help="장후(post)가 기본이자 깨끗한 케이스. 장중(intra)은 이미 반응한 뒤다")
    a = ap.parse_args()

    dart = pickle.load(open(a.dart, "rb"))
    meta = filter_meta(json.load(open("state/deep_px.pkl.meta.json")))
    px = {t: v for t, v in pickle.load(open("state/deep_px.pkl", "rb")).items() if t in meta}
    meta = {c: v for c, v in meta.items() if c in px}
    hit, unmatched = build(dart, px, meta, set(a.session), a.corrections)
    ndays = len(hit)
    total = sum(len(c) for c in hit.values())
    print(f"공시 {len(dart)}일 · 유니버스 {len(meta)}종목 · 세션 {','.join(a.session)} · "
          f"{'정정공시만' if a.corrections else '원공시만'}")
    print(f"  매칭된 이벤트 {total:,}건 / {ndays}일")
    print(f"  (유니버스 밖이라 버린 공시 {unmatched:,}건)")

    alld = sorted(hit)
    cut = alld[int(len(alld) * 0.6)]
    tr = {d for d in alld if d < cut}
    te = {d for d in alld if d >= cut}
    n_h = len(EVENTS)
    crit = 2.87 if n_h >= 10 else 2.5
    print(f"  탐색 {len(tr)}일 / 검증 {len(te)}일 · 가설 {n_h}개 → 본페로니 |t| ≥ {crit}\n")

    fw = precompute(px, a.hold)
    if a.null:
        for hold in a.hold:
            base = baselines(hit, px, meta, hold, fw)
            ts = null_check(hit, px, meta, hold, fw, base)
            if len(ts) < 5:
                print(f"보유 {hold}일: 표본 부족")
                continue
            n2 = sum(1 for t in ts if abs(t) > 2)
            print(f"보유 {hold}일 · 무작위 이벤트 {len(ts)}회 — "
                  f"t 최소 {ts[0]:+.2f} 중앙 {ts[len(ts)//2]:+.2f} 최대 {ts[-1]:+.2f} | "
                  f"|t|>2 {n2}/{len(ts)} ({100*n2/len(ts):.0f}%) "
                  f"{'✅ 정상' if n2 <= len(ts)*0.15 else '❌ 거짓양성 과다'}")
        return
    for hold in a.hold:
        base = baselines(hit, px, meta, hold, fw)
        print("=" * 92)
        print(f"보유 {hold}거래일 — 공시 다음날 시가 진입 · 비용 {COST}%")
        print("=" * 92)
        print(f"{'공시 유형':<14}{'건수':>7}{'전구간 초과':>12}{'t':>8}{'탐색 t':>9}{'검증 t':>9}{'판정':>7}")
        print("-" * 92)
        rows = []
        for kind in EVENTS:
            full, n = evaluate(hit, px, meta, kind, hold, fw, base)
            if not full or n < 60 or full[2] < 20:   # 이벤트 60건 + 거래일 20일
                continue
            x, _ = evaluate(hit, px, meta, kind, hold, fw, base, tr)
            y, _ = evaluate(hit, px, meta, kind, hold, fw, base, te)
            if not x or not y:
                continue
            ok = (abs(full[1]) >= crit and abs(y[1]) >= 2.0 and x[1] * y[1] > 0)
            rows.append((kind, n, full, x, y, ok))
            print(f"{kind:<14}{n:>7,}{full[0]:>+11.2f}%{full[1]:>8.2f}{x[1]:>9.2f}{y[1]:>9.2f}"
                  f"{'  ✅' if ok else '':>7}")
        print("-" * 92)
        surv = [r[0] for r in rows if r[5]]
        print(f"생존: {', '.join(surv) if surv else '없음'}\n")


if __name__ == "__main__":
    main()
