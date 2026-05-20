"""
DART 공시 실시간 모니터 — "오르기 전" 시그널 발견 시스템.

흐름:
  1. DART 일별 공시 페이지 fetch (오늘 + 어제)
  2. 우리 누적 시그널 종목 (state) 와 매칭
  3. 보고서명에서 호재 키워드 분류
     - 수주/계약 / 실적 / M&A / 신약 / 자사주매입 / 정책 등
  4. 매칭 종목의 현재 가격 fetch (저점 매수 후보 발견)
  5. reports/alerts.json 에 저장 (UI 가 read)
  6. 텔레그램 알림 (env 키 있으면)

매 N분 cron 또는 수동 실행 — 새벽/장 마감 후 권장.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup
from state_manager import load_state
from monitor.live_radar import send_telegram


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://dart.fss.or.kr/",
    "Accept": "text/html,application/xhtml+xml",
}

ALERTS_PATH = ROOT / "reports" / "alerts.json"


# 호재 카테고리별 보고서명 키워드
GOOD_PATTERNS: dict[str, list[str]] = {
    "수주/계약": [
        "단일판매·공급계약체결",
        "단일판매ㆍ공급계약체결",
        "수주",
        "공급계약",
        "라이선스",
        "기술이전계약",
        "기술수출",
    ],
    "M&A/경영권": [
        "타법인주식및출자증권취득",
        "타법인 주식 및 출자증권 취득",
        "영업양수",
        "최대주주변경",
        "지분취득",
        "주식교환",
        "합병",
        "분할합병",
    ],
    "실적": [
        "분기보고서",
        "반기보고서",
        "사업보고서",
        "잠정실적",
        "공정공시(실적)",
        "영업(잠정)실적",
    ],
    "자본정책_호재": [
        "자기주식취득결정",
        "자기주식취득신탁",
        "유상감자",  # 일반적으로 주주환원
        "주식소각",
        "현금배당",
    ],
    "투자/시설": [
        "유형자산 양수",
        "신규시설투자",
        "유형자산양수",
    ],
    "신약/임상": [
        "임상시험",
        "신약",
        "임상결과",
        "품목허가",
    ],
}

# 악재 — 알림 안 보냄 (참고용)
BAD_PATTERNS = [
    "감자",
    "관리종목지정",
    "상장폐지",
    "회생절차",
    "거래정지",
    "회계처리위반",
]


def fetch_dart_day(date_str: str) -> list[dict]:
    """DART 일별 공시 리스트 (yyyymmdd 또는 yyyy.mm.dd 형식). 모든 페이지 자동 순회."""
    if len(date_str) == 8:
        date_str = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"

    all_items = []
    page = 1
    while True:
        params = {
            "selectDate": date_str,
            "currentPage": str(page),
            "maxResults": "100",
            "maxLinks": "10",
            "publicYn": "Y",
            "search_type": "AS",
        }
        url = "https://dart.fss.or.kr/dsac001/search.ax?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as r:
                html = r.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  ⚠️ DART fetch {date_str} p{page}: {e}")
            break

        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("table.tbList tbody tr")
        if not rows:
            break

        added = 0
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            time_text = tds[0].get_text(strip=True)
            # 회사 이름 + ticker
            corp_link = tds[1].select_one("a")
            corp_name = corp_link.get_text(strip=True) if corp_link else tds[1].get_text(strip=True)
            corp_href = corp_link.get("href", "") if corp_link else ""
            # ticker 추출 — 보통 onclick 안 or href 에 있음
            ticker_match = re.search(r"openCorpInfo[\(\'](\d{8})", corp_href) or re.search(r"(\d{6})", corp_href)
            ticker = None
            # DART corp_code 와 종목코드는 다름 → corp_name 으로 매칭 권장
            # 일부 케이스: 종목코드는 다른 td 또는 attr
            # 일단 corp_name + tag (KS/KQ) 로 매칭
            sosok_span = tds[1].select_one("span.tag")
            sosok = sosok_span.get_text(strip=True) if sosok_span else ""

            # 보고서명 + 링크
            report_link = tds[2].select_one("a")
            report_name = report_link.get_text(strip=True) if report_link else tds[2].get_text(strip=True)
            report_href = report_link.get("href", "") if report_link else ""
            # rcpNo 추출
            rcp_match = re.search(r"rcpNo=(\d+)", report_href) or re.search(r"\((\d+)[,)]", report_href)
            rcp_no = rcp_match.group(1) if rcp_match else ""
            dart_url = (
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp_no}" if rcp_no else ""
            )

            submitter = tds[3].get_text(strip=True)
            submitted_at = tds[4].get_text(strip=True)

            all_items.append({
                "time": time_text,
                "submitted_at": submitted_at,
                "corp_name": corp_name,
                "sosok": sosok,  # 코, 유 등
                "report_name": report_name,
                "submitter": submitter,
                "dart_url": dart_url,
                "rcp_no": rcp_no,
            })
            added += 1

        if added == 0 or added < 100:
            break
        page += 1
        if page > 5:  # 안전 한계
            break

    return all_items


def classify_report(report_name: str) -> tuple[str, int]:
    """보고서명 → (카테고리, 점수). 매칭 안 되면 ("", 0)."""
    # 악재 먼저
    for bad in BAD_PATTERNS:
        if bad in report_name:
            return ("", 0)
    # 호재 분류
    for category, patterns in GOOD_PATTERNS.items():
        for p in patterns:
            if p in report_name:
                # 카테고리별 점수
                base = {
                    "수주/계약": 85,
                    "M&A/경영권": 80,
                    "실적": 75,
                    "자본정책_호재": 70,
                    "투자/시설": 70,
                    "신약/임상": 78,
                }.get(category, 60)
                return (category, base)
    return ("", 0)


def fetch_price(ticker: str) -> dict | None:
    """현재가 fetch (Naver polling)."""
    try:
        url = (
            "https://polling.finance.naver.com/api/realtime?query="
            + urllib.parse.quote(f"SERVICE_ITEM:{ticker}")
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://m.stock.naver.com/",
        })
        with urllib.request.urlopen(req, timeout=4) as r:
            raw = r.read()
        try:
            txt = raw.decode("euc-kr")
        except UnicodeDecodeError:
            txt = raw.decode("utf-8", errors="replace")
        data = json.loads(txt)
        datas = data.get("result", {}).get("areas", [{}])[0].get("datas", [])
        if not datas:
            return None
        d = datas[0]
        return {
            "current": d.get("nv"),
            "change_pct": d.get("cr"),
            "open": d.get("ov"),
        }
    except Exception:
        return None


def load_existing_alerts() -> list[dict]:
    if not ALERTS_PATH.exists():
        return []
    try:
        return json.loads(ALERTS_PATH.read_text(encoding="utf-8")).get("alerts", [])
    except Exception:
        return []


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="DART 공시 모니터 — 우리 시그널 종목 매칭")
    p.add_argument("--days", type=int, default=2, help="며칠치 DART 공시 fetch (default 2)")
    p.add_argument("--all-tickers", action="store_true",
                   help="state 의 모든 시그널 종목 (default high/medium만)")
    args = p.parse_args()

    state = load_state()
    signals = state["signals"]
    if args.all_tickers:
        ticker_to_name = {t: s.get("name", "") for t, s in signals.items()}
    else:
        ticker_to_name = {
            t: s.get("name", "")
            for t, s in signals.items()
            if s.get("confidence") in ("high", "medium")
        }
    # 이름 → ticker 역 매핑
    name_to_ticker = {}
    for t, n in ticker_to_name.items():
        if n:
            name_to_ticker[n] = t
    print(f"📦 시그널 종목 매칭 대상: {len(name_to_ticker)}건")

    # 최근 N일 DART 공시
    all_disc = []
    for i in range(args.days):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        print(f"   - DART {d} fetch...")
        items = fetch_dart_day(d)
        print(f"     ({len(items)}건)")
        all_disc.extend(items)
    print(f"📥 총 {len(all_disc)}건 공시\n")

    existing = load_existing_alerts()
    existing_keys = {(a.get("ticker"), a.get("rcp_no")) for a in existing}

    # 매칭
    new_alerts = []
    for item in all_disc:
        ticker = name_to_ticker.get(item["corp_name"])
        if not ticker:
            continue
        category, score = classify_report(item["report_name"])
        if not category:
            continue
        key = (ticker, item.get("rcp_no", ""))
        if key in existing_keys:
            continue

        sig = signals.get(ticker, {})
        price = fetch_price(ticker)
        new_alerts.append({
            "ticker": ticker,
            "name": item["corp_name"],
            "sosok": item["sosok"],
            "report_name": item["report_name"],
            "submitter": item["submitter"],
            "submitted_at": item["submitted_at"],
            "time": item["time"],
            "dart_url": item["dart_url"],
            "rcp_no": item["rcp_no"],
            "category": category,
            "score": score,
            "current_price": price.get("current") if price else None,
            "today_change_pct": price.get("change_pct") if price else None,
            "signal_last_seen": sig.get("last_seen", ""),
            "signal_confidence": sig.get("confidence", ""),
            "signal_main_theme": sig.get("main_theme", ""),
            "matched_at": datetime.now().isoformat(timespec="seconds"),
        })

    print(f"🎯 신규 매칭: {len(new_alerts)}건\n")
    for a in new_alerts:
        chg = f" ({a['today_change_pct']:+.1f}%)" if a.get("today_change_pct") is not None else ""
        print(f"  [{a['ticker']}] {a['name']:15} — {a['category']:10} | {a['report_name'][:50]}")
        print(f"     submitted {a['submitted_at']} {a['time']} · 현재가 {a.get('current_price','-')}{chg}")
        print(f"     {a['dart_url']}")

    # 저장 — 기존 + 신규 (최근 30일만 유지)
    merged = new_alerts + existing
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    merged = [a for a in merged if a.get("matched_at", "") >= cutoff]
    # 신규 + 기존 dedup
    seen = set()
    dedup = []
    for a in merged:
        k = (a.get("ticker"), a.get("rcp_no"))
        if k in seen:
            continue
        seen.add(k)
        dedup.append(a)
    # 시간 역순
    dedup.sort(key=lambda a: a.get("submitted_at", "") + " " + a.get("time", ""), reverse=True)

    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_PATH.write_text(
        json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "total": len(dedup),
            "new_today": len(new_alerts),
            "alerts": dedup,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ 저장: {ALERTS_PATH}  (누적 {len(dedup)} / 신규 {len(new_alerts)})")

    # 텔레그램 알림 — 신규 매칭만, 최대 5건씩 묶어서 전송 (스팸 방지)
    if new_alerts:
        # 점수 높은 순 + 카테고리별 다양성
        sorted_new = sorted(new_alerts, key=lambda a: -a["score"])
        for batch_start in range(0, len(sorted_new), 5):
            chunk = sorted_new[batch_start:batch_start + 5]
            lines = [f"🚨 DART 호재 공시 매칭 ({len(chunk)}건)\n"]
            for a in chunk:
                chg_str = (
                    f" ({a['today_change_pct']:+.1f}%)"
                    if a.get("today_change_pct") is not None
                    else ""
                )
                price_str = (
                    f"{a['current_price']:,}원" if a.get("current_price") else "—"
                )
                lines.append(
                    f"• [{a['category']}] {a['name']} {price_str}{chg_str}\n"
                    f"  └ {a['report_name'][:60]}\n"
                    f"  └ {a['dart_url']}"
                )
            try:
                ok = send_telegram("\n".join(lines))
                if ok:
                    print(f"📲 텔레그램 알림 전송 ({len(chunk)}건)")
                else:
                    print(f"📲 텔레그램 키 없음/실패 (silent)")
                    break
            except Exception as e:
                print(f"📲 텔레그램 오류: {e}")
                break


if __name__ == "__main__":
    main()
