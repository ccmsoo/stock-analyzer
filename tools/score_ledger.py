"""
레이더 장부 채점기 — 라이브 출력의 '진짜 손익' 측정(포워드 페이퍼-트레이드).

검증된 플레이북대로 채점:
  진입 = 신호일 '다음 거래일' 시초(보수적·미래참조 없음)
  보유 = 3거래일 (진입일 시초 → 3일째 종가)
  손절 = 넓게 -10% (보유중 저가가 -10% 터치 시 -10%로 청산)

합성표본(급등 D-3 가정) 아닌 실제 픽 전부를 채점 → 실전 base-rate 반영.
분해: 촉매점수별 / 이평선(MA20) 상태별 / 유동성별 → '못생긴 차트 픽이 정말
돈을 까먹나' 같은 질문을 추측이 아니라 데이터로 답한다.

CLI:
  python -m tools.score_ledger                 # 전체 통계
  python -m tools.score_ledger --telegram      # 주간 리포트 발송
  python -m tools.score_ledger --hold 3 --stop 10
"""
from __future__ import annotations
import argparse, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.radar_ledger import load as load_ledger
from tools.fetch_history_naver import fetch_one


def score_one(series: dict, flag_date: str, hold: int, stop: float):
    """series={ymd:ohlcv}. 반환 (result%|None, entry_date, ma_state, status)."""
    days = sorted(series)
    if not days:
        return None, None, None, "no_data"
    # 진입 = 신호일 이후 첫 거래일
    fwd = [d for d in days if d > flag_date]
    if not fwd:
        return None, None, None, "pending"
    entry_date = fwd[0]
    ei = days.index(entry_date)
    exit_i = ei + hold - 1
    if exit_i >= len(days):
        return None, entry_date, None, "pending"

    entry = series[entry_date]["open"]
    if not entry:
        return None, entry_date, None, "no_open"

    # MA20 상태(신호일 기준): 신호일 이하 마지막 거래일까지 20종가
    upto = [d for d in days if d <= flag_date]
    ma_state = None
    if len(upto) >= 20:
        last_d = upto[-1]
        ma20 = sum(series[d]["close"] for d in upto[-20:]) / 20
        ma_state = "above" if series[last_d]["close"] >= ma20 else "below"

    # 손절 우선 평가(보수): 보유 구간 저가가 -10% 터치 시 -10%
    stop_px = entry * (1 - stop / 100)
    for j in range(ei, exit_i + 1):
        if series[days[j]]["low"] <= stop_px:
            return -stop, entry_date, ma_state, "ok"
    ret = (series[days[exit_i]]["close"] / entry - 1) * 100
    return ret, entry_date, ma_state, "ok"


def stat(rows):
    """rows = [ret%]. 반환 (n, 평균, 승률%)."""
    if not rows:
        return (0, 0.0, 0.0)
    return (len(rows), sum(rows) / len(rows), sum(1 for r in rows if r > 0) / len(rows) * 100)


def fmt(label, s):
    return f"   {label:18} n={s[0]:>3}  평균 {s[1]:+6.1f}%  승률 {s[2]:>3.0f}%"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hold", type=int, default=3, help="보유 거래일")
    p.add_argument("--stop", type=float, default=10.0, help="넓은 손절 %")
    p.add_argument("--pages", type=int, default=16, help="네이버 일봉 페이지(10일/페이지)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--telegram", action="store_true")
    args = p.parse_args()

    rows = load_ledger()
    if not rows:
        print("장부 비어있음 — 레이더를 먼저 돌리세요(state/radar_ledger.jsonl)"); return

    tickers = sorted({r["ticker"] for r in rows})
    print(f"📒 장부 {len(rows)}건 / {len(tickers)}종목 — 네이버 일봉으로 채점(진입=신호 다음날 시초, "
          f"{args.hold}일 보유, 손절 -{args.stop:.0f}%)...\n")

    # 종목별 일봉 한 번씩
    px = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, t, args.pages): t for t in tickers}
        for f in as_completed(futs):
            t = futs[f]
            try:
                _, series = f.result()
            except Exception:
                series = {}
            px[t] = series

    scored, pending, detail = [], [], []
    for r in rows:
        series = px.get(r["ticker"], {})
        ret, edate, ma_state, status = score_one(series, r["date"], args.hold, args.stop)
        if status != "ok" or ret is None:
            if status == "pending":
                pending.append(r)
            continue
        rec = {**r, "ret": ret, "entry_date": edate, "ma_state": ma_state}
        scored.append(rec)
        detail.append(rec)

    if not scored:
        print(f"채점 가능한 픽 없음(아직 {args.hold}거래일 미경과). 대기 {len(pending)}건.")
        if pending:
            print("   대기:", ", ".join(f"{r['name']}({r['date']})" for r in pending[:12]))
        return

    rets = [s["ret"] for s in scored]
    overall = stat(rets)
    print("=" * 56)
    print(fmt("전체", overall))
    print("-" * 56)
    # 촉매점수별
    for lo, hi, lab in [(8, 99, "촉매 8+"), (7, 8, "촉매 7"), (6, 7, "촉매 6")]:
        s = stat([x["ret"] for x in scored if x.get("score") is not None and lo <= x["score"] < hi])
        if s[0]:
            print(fmt(lab, s))
    print("-" * 56)
    # 이평선 상태별 (차트 질문의 데이터 답)
    for st, lab in [("above", "MA20 위(추세양호)"), ("below", "MA20 아래(하락)")]:
        s = stat([x["ret"] for x in scored if x.get("ma_state") == st])
        if s[0]:
            print(fmt(lab, s))
    unk = stat([x["ret"] for x in scored if x.get("ma_state") is None])
    if unk[0]:
        print(fmt("MA20 불명", unk))
    print("-" * 56)
    # 유동성별
    big = stat([x["ret"] for x in scored if (x.get("value_traded") or 0) >= 50e8])
    sml = stat([x["ret"] for x in scored if (x.get("value_traded") or 0) < 50e8])
    if big[0]:
        print(fmt("거래대금 50억+", big))
    if sml[0]:
        print(fmt("거래대금 <50억", sml))
    print("=" * 56)
    print(f"   (대기 {len(pending)}건 — {args.hold}거래일 더 지나면 채점)")

    # 베스트/워스트
    detail.sort(key=lambda x: -x["ret"])
    print("\n   베스트:", ", ".join(f"{x['name']}{x['ret']:+.0f}%" for x in detail[:4]))
    print("   워스트:", ", ".join(f"{x['name']}{x['ret']:+.0f}%" for x in detail[-4:]))

    if args.telegram:
        try:
            from monitor.live_radar import send_telegram
            L = [f"📊 레이더 성적표(포워드)", f"채점 {overall[0]}건 · 진입=신호익일 · {args.hold}일보유 · 손절-{args.stop:.0f}%", ""]
            L.append(f"전체: 평균 {overall[1]:+.1f}% · 승률 {overall[2]:.0f}%")
            for lo, hi, lab in [(8, 99, "촉매8+"), (7, 8, "촉매7"), (6, 7, "촉매6")]:
                s = stat([x["ret"] for x in scored if x.get("score") is not None and lo <= x["score"] < hi])
                if s[0]:
                    L.append(f"· {lab}: {s[1]:+.1f}% (승{s[2]:.0f}%, n{s[0]})")
            sa = stat([x["ret"] for x in scored if x.get("ma_state") == "above"])
            sb = stat([x["ret"] for x in scored if x.get("ma_state") == "below"])
            if sa[0] and sb[0]:
                L.append(f"· MA20위 {sa[1]:+.1f}% vs 아래 {sb[1]:+.1f}%")
            ok = send_telegram("\n".join(L))
            print(f"\n📨 텔레그램: {'성공' if ok else '실패'}")
        except Exception as e:
            print(f"\n📨 텔레그램 실패: {str(e)[:80]}")


if __name__ == "__main__":
    main()
