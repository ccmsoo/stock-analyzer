"""
오르기 전 촉매 레이더 (Phase 2 → 실전) — 궁극 프로그램의 코어.

백테스트 검증(presurge_ai): AI 촉매점수 >=6 → 급등 정밀도 88%, 리드 ~3일.
이 도구는 매일: 워치리스트 최근 뉴스 → AI 촉매 평가 → (점수>=6 AND 아직 안 오른) 후보.

CLI:
  python -m tools.presurge_radar                 # high/medium 전체
  python -m tools.presurge_radar --min-score 6 --days 3 --max 200
"""
from __future__ import annotations
import argparse, json, sys, time
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
from collectors.news_collector import collect_news_for_stock
from tools.toss_client import get_prices, get_candles

# 촉매 신선도 가드: 최근 SPIKE_DAYS 거래일 중 하루라도 +SPIKE_PCT% 이상 급등이면
# 촉매가 이미 발화한 뒤(스파이크 후 페이드)로 보고 chart_ok 탈락시킨다.
# 3→5 확장(2026-07-22): 장부 605건 A/B — 통과알파/승률 3일 +1.57%/51% < 5일 +1.67%/53%
# (7일 +1.92%/55%로 더 좋았으나 파라미터 과적합 우려로 보류). 4거래일 전 스파이크
# 페이드(롯데손보 7/15 +12.8% → 3일 연속 하락)를 3일 창이 놓친 실사례가 계기.
SPIKE_DAYS = 5
SPIKE_PCT = 12.0


def candles_any(ticker: str) -> list[dict]:
    """일봉 최신순. 토스 우선, 실패 시 네이버 폴백.
    cron 환경엔 토스키가 없어 토스는 빈값 → 네이버로 차트필터/가드가 동작하게 한다."""
    try:
        cs = get_candles(ticker)
    except Exception:
        cs = []
    if cs and len(cs) > 21 and cs[0].get("close"):
        return cs
    try:
        from tools.fetch_history_naver import fetch_one
        px = fetch_one(ticker, 4)[1]  # ~40 거래일
        rows = [px[k] for k in sorted(px)]  # 과거→최신
        return list(reversed(rows))  # 최신순으로
    except Exception:
        return cs or []

SYS = """너는 한국 주식 단기 촉매 분석가다. 종목의 '최근 기사 제목들'만 보고
앞으로 주가를 급등시킬 촉매가 있는지 평가한다. (향후 결과는 모름. 기사만으로 판단)
강한 촉매: 대형 수주/계약, 임상/FDA 승인, M&A/경영권, 흑자전환/어닝서프라이즈, 국책과제 선정, 신약/기술이전, 대규모 수출.
약/무촉매: 단순 시황, 주가 등락 보도, 일반 동향, IR 일정, 반복 홍보, 유상증자/희석.
JSON만: {"score":0-10,"keyword":"결정적 키워드 1개","reason":"한 줄"}"""


_anthropic_client = None


def _rate_anthropic(name: str, txt: str):
    """OpenAI 장애 시 폴백 — ANTHROPIC_API_KEY 있으면 Claude Haiku로 같은 평가.
    (2026-07-28~08-06 OpenAI 크레딧 소진으로 레이더 10일 장님 — 단일 의존 제거)"""
    global _anthropic_client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        if _anthropic_client is None:
            import anthropic
            _anthropic_client = anthropic.Anthropic(timeout=60, max_retries=1)
        m = _anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=300,
            system=SYS,
            messages=[{"role": "user", "content": f"종목: {name}\n최근 기사:\n{txt}\n\nJSON:"}])
        raw = m.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        d = json.loads(raw)
        return {"score": float(d.get("score", 0)), "keyword": d.get("keyword", ""), "reason": d.get("reason", "")}
    except Exception:
        return None


def rate(client, name, titles):
    txt = "\n".join(f"- {t}" for t in titles[:12])
    try:
        r = client.chat.completions.create(
            model="gpt-5-mini", max_completion_tokens=300, reasoning_effort="minimal",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYS},
                      {"role": "user", "content": f"종목: {name}\n최근 기사:\n{txt}\n\nJSON:"}])
        d = json.loads(r.choices[0].message.content)
        return {"score": float(d.get("score", 0)), "keyword": d.get("keyword", ""), "reason": d.get("reason", "")}
    except Exception:
        return _rate_anthropic(name, txt)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="기준일 YYYYMMDD (기본 오늘)")
    p.add_argument("--days", type=int, default=3, help="최근 N일 뉴스")
    p.add_argument("--min-score", type=float, default=6.0)
    p.add_argument("--max-move", type=float, default=5.0, help="이 이상 이미 오른 건 제외(%)")
    p.add_argument("--min-value", type=float, default=5.0, help="유동성 하한: 평균 거래대금 억원(기본 5억)")
    p.add_argument("--max", type=int, default=None, help="워치리스트 상한(테스트)")
    p.add_argument("--all", action="store_true", help="low 포함")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--telegram", action="store_true", help="결과를 텔레그램으로 발송")
    args = p.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 없음"); sys.exit(1)

    from datetime import datetime
    date_str = args.date or datetime.now().strftime("%Y%m%d")
    sigs = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    wl = [(t, v) for t, v in sigs.items()
          if args.all or v.get("confidence") in ("high", "medium")]
    if args.max:
        wl = wl[: args.max]
    print(f"📡 오르기 전 촉매 레이더 — 워치 {len(wl)}종목, 최근 {args.days}일 뉴스 스캔...")

    # 1) 뉴스 수집 (스레드) — 최근 뉴스 있는 종목만 추림
    def fetch(t, v):
        try:
            arts = collect_news_for_stock(t, date_str, articles_per_stock=15, days_before=args.days)
        except Exception:
            arts = []
        titles = [a["title"] for a in arts]
        return (t, v.get("name", t), v.get("market", ""), titles) if titles else None

    have_news = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch, t, v) for t, v in wl]
        for i, f in enumerate(as_completed(futs)):
            r = f.result()
            if r:
                have_news.append(r)
    print(f"   뉴스 있는 종목: {len(have_news)} → AI 촉매 평가...")

    # 2) AI 촉매 평가 (스레드)
    client = OpenAI(timeout=60, max_retries=1)
    scored = []
    err_count = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(rate, client, nm, titles): (t, nm, mk) for t, nm, mk, titles in have_news}
        done = 0
        for f in as_completed(futs):
            done += 1
            t, nm, mk = futs[f]
            r = f.result()
            if r is None:
                err_count += 1
            elif r["score"] >= args.min_score:
                scored.append({"ticker": t, "name": nm, "market": mk, **r})
            if done % 50 == 0:
                print(f"   {done}/{len(have_news)}")

    # AI 호출이 절반 이상 실패면 '촉매 없음'이 아니라 'AI 장애' — 구분해서 실패로 종료.
    # (실사례 2026-07-28~08-06: OpenAI 크레딧 소진 429를 rate()가 조용히 삼켜
    #  10일간 '촉매 후보 없음'으로 위장 → 레이더가 장님인 걸 아무도 모름)
    if have_news and err_count > len(have_news) * 0.5:
        from datetime import datetime as _dt
        out = ROOT / "reports" / "presurge_radar.json"
        json.dump({"date": date_str, "generated_at": _dt.now().isoformat(timespec="seconds"),
                   "candidates": [], "error": f"AI 평가 실패 {err_count}/{len(have_news)} — OpenAI 크레딧/키 확인 필요"},
                  open(out, "w"), ensure_ascii=False, indent=1)
        print(f"\n❌ AI 평가 {err_count}/{len(have_news)} 실패 — OpenAI 크레딧/키 확인 (레이더 무효)")
        if args.telegram:
            try:
                from monitor.live_radar import send_telegram
                send_telegram(f"⚠️ 레이더 장애: AI 평가 {err_count}/{len(have_news)} 실패 — OpenAI 크레딧/키 확인 필요")
            except Exception:
                pass
        sys.exit(1)

    if not scored:
        # 후보 0건이어도 json은 날짜 갱신해서 저장 — 안 그러면 UI가 며칠 전
        # 후보를 오늘 것처럼 계속 보여줌.
        print(f"\n촉매점수 {args.min_score}+ 종목 없음.")
        from datetime import datetime as _dt
        out = ROOT / "reports" / "presurge_radar.json"
        json.dump({"date": date_str, "generated_at": _dt.now().isoformat(timespec="seconds"),
                   "candidates": [], "note": "촉매 후보 없음 (시황성 뉴스만 감지)"},
                  open(out, "w"), ensure_ascii=False, indent=1)
        print(f"💾 저장: {out} (후보 0건)")
        return

    # 3) 현재가 — 이미 오른 것 제외 (toss prices)
    prices = get_prices([s["ticker"] for s in scored])
    # 오늘 등락은 toss lastPrice만으론 모르니, 폴링으로 보강
    import urllib.request, urllib.parse
    chg = {}
    tics = [s["ticker"] for s in scored]
    for i in range(0, len(tics), 30):
        try:
            url = "https://polling.finance.naver.com/api/realtime?query=" + urllib.parse.quote("SERVICE_ITEM:" + ",".join(tics[i:i+30]))
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}), timeout=8).read()
            for d in json.loads(raw.decode("euc-kr", errors="replace")).get("result", {}).get("areas", [{}])[0].get("datas", []):
                chg[d["cd"]] = d.get("cr")
        except Exception:
            pass

    for s in scored:
        s["today"] = chg.get(s["ticker"])
        s["price"] = prices.get(s["ticker"])

    # 4) 차트 매칭 — 이미 extended면 늦음. 5일변동/고점대비/거래량.
    for s in scored:
        cs = candles_any(s["ticker"])  # newest-first (토스 → 네이버 폴백)
        if len(cs) > 21 and cs[0]["close"]:
            last = cs[0]["close"]
            s["chg5"] = (last / cs[5]["close"] - 1) * 100 if cs[5]["close"] else None
            hi20 = max(c["high"] for c in cs[:20])
            s["from_high"] = (last / hi20 - 1) * 100 if hi20 else None
            volavg = sum(c["volume"] for c in cs[1:21]) / 20
            s["volratio"] = cs[0]["volume"] / volavg if volavg else None
            s["value_traded"] = last * volavg  # 평균 거래대금 근사(원)
            # 촉매 신선도 가드: 최근 SPIKE_DAYS일 중 하루라도 +SPIKE_PCT% 이상 급등 =
            #   촉매가 이미 발화(상한가 등)한 뒤라 지금 chg5가 평평해도 '가짜 조용함'.
            #   → 스파이크 후 페이드는 검증상 무버 추격(-12%) 구간이므로 chart_ok 탈락.
            spike = 0.0
            for i in range(SPIKE_DAYS):
                if i + 1 < len(cs) and cs[i + 1]["close"]:
                    spike = max(spike, (cs[i]["close"] / cs[i + 1]["close"] - 1) * 100)
            s["max_spike3"] = round(spike, 1)
            s["stale_catalyst"] = spike >= SPIKE_PCT  # 이미 급등후 페이드
            # 차트 양호 = 아직 안 extended (5일<15%) & 최근 급등후 페이드 아님
            s["chart_ok"] = (s["chg5"] is None or s["chg5"] < 15) and not s["stale_catalyst"]
        else:
            s["chg5"] = s["from_high"] = s["volratio"] = s["value_traded"] = None
            s["max_spike3"] = None
            s["stale_catalyst"] = False
            s["chart_ok"] = True

    # 촉매 유형/문구 분류 — UI가 track_record.json의 유형별 실적과 조인
    from tools.catalyst_events import cat_of, wording
    for s in scored:
        s["cat"] = cat_of(s.get("keyword"))
        s["wording"] = wording(s)

    # 유동성 하한 — 못 빠져나오는 초저유동만 제외 (우량주 필터 아님)
    min_val = args.min_value * 1e8
    scored = [s for s in scored if s.get("value_traded") is None or s["value_traded"] >= min_val]

    # 오늘 이미 오른 것 + 최근 급등후 페이드(촉매 이미 발화) 둘 다 제외
    fresh = [s for s in scored
             if (s["today"] is None or s["today"] < args.max_move)
             and not s.get("stale_catalyst")]
    stale = [s for s in scored if s.get("stale_catalyst")
             and (s["today"] is None or s["today"] < args.max_move)]
    # 정렬: 촉매점수 → 차트양호 → 거래량
    fresh.sort(key=lambda x: (-x["score"], not x["chart_ok"], -(x.get("volratio") or 0)))

    print(f"\n=== 🎯 오르기 전 촉매 후보 ({date_str}) — 점수{args.min_score}+ & 아직<{args.max_move}% ===")
    print(f"   {'종목':12} {'점수':>4} {'오늘':>6} {'5일':>6} {'고점':>6} {'거래량':>5} 차트 키워드")
    for s in fresh[:25]:
        td = f"{s['today']:+.1f}%" if s["today"] is not None else "-"
        c5 = f"{s['chg5']:+.0f}%" if s.get("chg5") is not None else "-"
        fh = f"{s['from_high']:+.0f}%" if s.get("from_high") is not None else "-"
        vr = f"{s['volratio']:.1f}x" if s.get("volratio") is not None else "-"
        mark = "✓" if s["chart_ok"] else "⚠"
        print(f"   {s['name'][:12]:12} {s['score']:>4.0f} {td:>6} {c5:>6} {fh:>6} {vr:>5}  {mark}  {s['keyword'][:18]}")

    already = [s for s in scored if s["today"] is not None and s["today"] >= args.max_move]
    if already:
        print(f"\n   (오늘 이미 오름 제외 {len(already)}: " + ", ".join(f"{s['name'][:8]}+{s['today']:.0f}%" for s in sorted(already, key=lambda x: -x['today'])[:6]) + ")")
    if stale:
        print(f"   (급등후 페이드 제외 {len(stale)} [최근{SPIKE_DAYS}일 +{SPIKE_PCT:.0f}%↑=촉매 이미 발화]: "
              + ", ".join(f"{s['name'][:8]}(스파이크+{s['max_spike3']:.0f}%)" for s in sorted(stale, key=lambda x: -(x.get('max_spike3') or 0))[:6]) + ")")

    # (눌림목 재진입/2차상승은 백테스트 결과 진입수익 음수(-5.8%)로 미채택 — 촉매 선진입만)
    print("\n📌 단기 스윙 플레이북 (백테스트 검증): 촉매 선진입 → 3일 보유(승률 최고) → "
          "넓은 손절(−10%)·추격 금지. 꽉 조인 +8%/−5%는 오히려 손해.")

    if args.telegram and fresh:
        try:
            from monitor.live_radar import send_telegram
            lines = [f"📡 오르기 전 촉매 {date_str}", "강한 촉매 + 아직 안 오름 (≤7일 스윙)", ""]
            for i, s in enumerate(fresh[:12], 1):
                td = f"{s['today']:+.0f}%" if s.get("today") is not None else "-"
                lines.append(f"{i}. {s['name']} (촉매{s['score']:.0f}) {td}\n   · {s['keyword'][:30]}")
            lines.append("\n▶ 3일 보유·넓은손절(−10%)·추격금지. 촉매≥6=정밀도~70%(검증)")
            ok = send_telegram("\n".join(lines))
            print(f"\n📨 텔레그램 발송: {'성공' if ok else '실패(키 없음/오류)'}")
        except Exception as e:
            print(f"\n📨 텔레그램 실패: {str(e)[:80]}")

    out = ROOT / "reports" / "presurge_radar.json"
    json.dump({"date": date_str, "generated_at": datetime.now().isoformat(timespec="seconds"),
               "candidates": fresh}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out}")

    # 포워드 성적표용 장부에 누적(append-only, 중복방지) — 며칠 뒤 score_ledger가 채점
    try:
        from tools.radar_ledger import append_picks
        n_added = append_picks(date_str, fresh)
        print(f"📒 장부 추가: {n_added}건 (state/radar_ledger.jsonl)")
    except Exception as e:
        print(f"📒 장부 기록 실패: {str(e)[:80]}")


if __name__ == "__main__":
    main()
