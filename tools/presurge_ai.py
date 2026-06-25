"""
AI 촉매 탐지 백테스트 (Phase 2b) — "본문 기사 → 결정적 키워드" 예측력 검증.

엄밀한 평가: AI가 *결과를 모른 채* 기사만 보고 촉매 강도(0-10)+결정적 키워드 판단 →
  positive(급등 직전 기사) vs control(안 오른 종목 기사) 점수 분포 비교.
  → 분리되면 AI 촉매탐지가 예측력 있음 (regex 10% 한계 극복).

CLI:
  python -m tools.presurge_ai --n 50 --surge 12
"""
from __future__ import annotations
import argparse, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
from collectors.news_collector import collect_news_for_stock

KW_CACHE = Path("/tmp/kw_px.json")

SYS = """너는 한국 주식 단기 촉매 분석가다. 종목의 '최근 기사 제목들'만 보고,
앞으로 주가를 급등시킬 만한 촉매가 있는지 평가한다. (향후 주가 결과는 모른다. 기사만으로 판단)
강한 촉매: 대형 수주/계약, 임상/FDA 승인, M&A/경영권, 흑자전환/어닝서프라이즈, 국책과제 선정, 신약/기술이전, 대규모 수출.
약한/무촉매: 단순 시황, 주가 등락 보도, 일반 동향, IR 일정, 반복 홍보.
JSON만 출력: {"score": 0-10, "keyword": "결정적 키워드 1개(없으면 빈칸)", "reason": "한 줄"}"""


def rate(client, name, titles):
    txt = "\n".join(f"- {t}" for t in titles[:12])
    try:
        r = client.chat.completions.create(
            model="gpt-5-mini", max_completion_tokens=300, reasoning_effort="minimal",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYS},
                      {"role": "user", "content": f"종목: {name}\n최근 기사 제목:\n{txt}\n\nJSON 평가:"}])
        d = json.loads(r.choices[0].message.content)
        return {"score": float(d.get("score", 0)), "keyword": d.get("keyword", ""), "reason": d.get("reason", "")}
    except Exception as e:
        return {"score": None, "keyword": "", "reason": str(e)[:60]}


def pre_titles(ticker, d, window):
    """급등일(d) 이전 window일 기사 제목."""
    try:
        arts = collect_news_for_stock(ticker, d, articles_per_stock=30, days_before=window, deep=True)
    except Exception:
        return []
    dd = datetime.strptime(d, "%Y%m%d")
    out = []
    for a in arts:
        try:
            adt = datetime.strptime(a["date"], "%Y.%m.%d %H:%M")
        except Exception:
            continue
        if adt.date() < dd.date():
            out.append(a["title"])
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--surge", type=float, default=12.0)
    p.add_argument("--window", type=int, default=6)
    p.add_argument("--n", type=int, default=50, help="positive/control 각 표본수")
    p.add_argument("--from", dest="dfrom", default="20260401")
    p.add_argument("--until", default="20260610")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--cache", default="/tmp/kw_px.json", help="캔들 캐시(유니버스 확대 시 /tmp/univ_px.json)")
    args = p.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 없음"); sys.exit(1)
    cache_path = Path(args.cache)
    if not cache_path.exists():
        print(f"⚠️ {cache_path} 없음 — fetch_universe / backtest_keywords 먼저"); sys.exit(1)

    px = json.loads(cache_path.read_text())
    tickers = sorted({k for k in px if not k.endswith("#order") and not k.startswith("#")} - {"069500", "229200"})
    sigs = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    cache_names = px.get("#names", {})
    names = {t: cache_names.get(t) or sigs.get(t, {}).get("name", t) for t in tickers}

    def chg(t, d):
        order = px[t + "#order"]; m = px[t]
        if d not in m:
            return None
        i = order.index(d)
        if i + 1 >= len(order):
            return None
        prev = m[order[i + 1]]["close"]
        return (m[d]["close"] / prev - 1) * 100 if prev else None

    def max_fwd(t, d, k=5):
        order = px[t + "#order"]; m = px[t]
        i = order.index(d)
        hs = [m[order[i - j]]["high"] for j in range(1, k + 1) if i - j >= 0]
        base = m[d]["close"]
        return (max(hs) / base - 1) * 100 if hs and base else None

    # positives: 급등일들 / controls: 안 오른 날들
    pos, ctrl = [], []
    for t in tickers:
        order = px[t + "#order"]; m = px[t]
        for i, d in enumerate(order):
            if not (args.dfrom <= d <= args.until):
                continue
            c = chg(t, d)
            if c is None:
                continue
            if c >= args.surge:
                pos.append((t, d))
            elif -2 < c < 4:
                # 향후 5일 급등 없어야 control (i 가 작을수록 최신)
                fwd = max_fwd(t, d, 5)
                if fwd is not None and fwd < 6:
                    ctrl.append((t, d))
    # 표본 (분산: 종목 다양하게 — 간단히 stride 샘플)
    def sample(lst, n):
        if len(lst) <= n:
            return lst
        step = len(lst) / n
        return [lst[int(i * step)] for i in range(n)]
    pos = sample(sorted(set(pos)), args.n)
    ctrl = sample(sorted(set(ctrl)), args.n)
    print(f"📊 positive(급등직전) {len(pos)} / control(안오름) {len(ctrl)} — 이전 뉴스+AI 평가...\n")

    client = OpenAI(timeout=60, max_retries=1)

    def work(group, t, d):
        titles = pre_titles(t, d, args.window)
        if not titles:
            return None
        r = rate(client, names.get(t, t), titles)
        if r["score"] is None:
            return None
        return {"group": group, "ticker": t, "name": names.get(t, t), "date": d,
                "n_titles": len(titles), **r}

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
                print(f"   {done}/{len(jobs)}")

    P = [r for r in out if r["group"] == "pos"]
    C = [r for r in out if r["group"] == "ctrl"]

    def stats(rows):
        sc = [r["score"] for r in rows]
        return (sum(sc) / len(sc) if sc else 0, len(sc))

    ap, np_ = stats(P); ac, nc = stats(C)
    print(f"\n=== AI 촉매점수 (결과 모르고 평가) ===")
    print(f"   positive(급등직전): 평균 {ap:.1f}점  (n={np_}, 뉴스있음만)")
    print(f"   control(안오른):    평균 {ac:.1f}점  (n={nc})")
    print(f"   차이: {ap-ac:+.1f}점  → {'✅ 분리됨(예측력 O)' if ap-ac>=1.5 else '△ 약함' if ap-ac>=0.7 else '❌ 분리안됨'}")

    for thr in (6, 7, 8):
        ph = sum(1 for r in P if r["score"] >= thr); ch = sum(1 for r in C if r["score"] >= thr)
        prec = ph / (ph + ch) * 100 if (ph + ch) else 0
        rec = ph / np_ * 100 if np_ else 0
        print(f"   임계 {thr}+ : 정밀도 {prec:.0f}% (급등 {ph} vs 오인 {ch}) · 재현율 {rec:.0f}%")

    print(f"\n=== 🔑 급등 예고 결정적 키워드 (positive, score>=6) ===")
    from collections import Counter
    kc = Counter(r["keyword"] for r in P if r["score"] >= 6 and r["keyword"])
    for k, n in kc.most_common(12):
        print(f"   {k[:24]:24} {n}건")

    print(f"\n=== 예시 (positive 고득점) ===")
    for r in sorted(P, key=lambda x: -x["score"])[:10]:
        print(f"   {r['name'][:11]:11} {r['date']} score {r['score']:.0f} · {r['keyword'][:18]} · {r['reason'][:34]}")

    outp = ROOT / "profitability" / "output" / "presurge_ai.json"
    json.dump({"params": vars(args), "results": out}, open(outp, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {outp}")


if __name__ == "__main__":
    main()
