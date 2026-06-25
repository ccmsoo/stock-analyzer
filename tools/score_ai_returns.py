"""
presurge_ai 촉매점수 → 실현수익 채점 (정밀도 아닌 '진입수익'으로 평가).

presurge_ai.py는 촉매점수→급등 정밀도(비율)만 봤다. 이 도구는 그 저장결과
(profitability/output/presurge_ai.json)에 가격캐시(univ_px 등)를 붙여,
촉매>=6 픽의 **실제 3일 실현수익**을 승자/듣보로 분해한다. (메모리 교훈: 비율 말고 진입수익)

진입 = 탐지일(앵커-LEAD) 다음날 시초 → HOLD일 보유 → 넓은손절 -STOP%.
  (라이브 장부 score_ledger와 동일한 진입 규칙)

⚠️ 두 낙관편향 (절대값은 상한, 실전은 더 낮음 — 라이브 장부가 진짜 심판):
  1. pos 앵커=급등일 → 승자는 '급등을 알고' 보유한 조건부 상단.
  2. 정밀도는 50/50 균형표본 값 → 실전 base-rate(급등 드묾)선 더 낮음.
  (반대로 ctrl=향후 무급등으로 골라낸 표본이라 듣보 수익은 다소 눌려있음 → 일부 상쇄)

CLI:
  python -m tools.score_ai_returns
  python -m tools.score_ai_returns --cache /tmp/univ_px.json --lead 3 --hold 3 --stop 10 --min-score 6
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_OUT = ROOT / "profitability" / "output" / "presurge_ai.json"


def pre_entry(px, t, anchor, lead, hold, stop):
    """탐지일(anchor-lead) 다음날 시초 진입 → hold일 보유 → -stop% 손절. 반환 %|None."""
    key = t + "#order"
    if key not in px or anchor not in px[t]:
        return None
    days = list(reversed(px[key]))  # oldest→newest
    m = px[t]
    if anchor not in days:
        return None
    i = days.index(anchor)
    ei = i - lead + 1
    xi = ei + hold - 1
    if ei < 0 or xi >= len(days):
        return None
    entry = m[days[ei]]["open"]
    if not entry:
        return None
    stop_px = entry * (1 - stop / 100)
    for j in range(ei, xi + 1):
        if m[days[j]]["low"] <= stop_px:
            return -stop
    return (m[days[xi]]["close"] / entry - 1) * 100


def st(x):
    return (len(x), sum(x) / len(x), sum(1 for r in x if r > 0) / len(x) * 100) if x else (0, 0.0, 0.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ai", default=str(AI_OUT), help="presurge_ai 저장결과")
    p.add_argument("--cache", default="/tmp/univ_px.json", help="가격 캐시(univ/hist)")
    p.add_argument("--lead", type=int, default=3, help="촉매→급등 리드(거래일)")
    p.add_argument("--hold", type=int, default=3, help="보유 거래일")
    p.add_argument("--stop", type=float, default=10.0, help="넓은 손절 %")
    p.add_argument("--min-score", type=float, default=6.0)
    args = p.parse_args()

    ai_path, cache_path = Path(args.ai), Path(args.cache)
    if not ai_path.exists():
        print(f"⚠️ {ai_path} 없음 — presurge_ai 먼저 실행"); sys.exit(1)
    if not cache_path.exists():
        print(f"⚠️ {cache_path} 없음 — fetch_universe / backtest_keywords 먼저"); sys.exit(1)

    rows = json.loads(ai_path.read_text())["results"]
    px = json.loads(cache_path.read_text())

    pos, ctrl, miss = [], [], 0
    for r in rows:
        if r.get("score") is None or r["score"] < args.min_score:
            continue
        ret = pre_entry(px, r["ticker"], r["date"], args.lead, args.hold, args.stop)
        if ret is None:
            miss += 1
            continue
        (pos if r["group"] == "pos" else ctrl).append(ret)

    sp, sc = st(pos), st(ctrl)
    print(f"📊 촉매>={args.min_score:.0f} 실현수익 (탐지일 진입=앵커-{args.lead - 1}일 시초, "
          f"{args.hold}일보유, -{args.stop:.0f}%손절) — 캐시미스 {miss}건\n")
    print(f"   승자(pos, 실제급등):           n={sp[0]:>3}  평균 {sp[1]:+6.1f}%  승률 {sp[2]:>3.0f}%")
    print(f"   듣보(ctrl, 촉매강했지만 안오름): n={sc[0]:>3}  평균 {sc[1]:+6.1f}%  승률 {sc[2]:>3.0f}%")
    print()
    for prec in (0.70, 0.60, 0.50):
        print(f"   EV @ 정밀도 {prec * 100:.0f}% = {prec * sp[1] + (1 - prec) * sc[1]:+.1f}%")
    print("\n   ⚠️ 절대값은 상한(승자=급등일 커닝 + 정밀도 균형표본). 라이브 장부(score_ledger)가 진짜 심판.")


if __name__ == "__main__":
    main()
