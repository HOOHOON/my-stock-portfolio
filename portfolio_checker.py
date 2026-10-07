import os
import sys
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import datetime
import html

# 윈도우 콘솔 출력 한글 및 이모지 깨짐 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def translate_to_ko(text):
    """영문 뉴스를 한국어로 즉시 번역"""
    if not text:
        return ""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ko&dt=t&q={urllib.parse.quote(text)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode('utf-8'))
            translated = ''.join([item[0] for item in data[0] if item[0]])
            return translated
    except Exception as e:
        print(f"번역 오류: {e}")
        return text

def fetch_yahoo_market_detail(symbol):
    """야후 파이낸스 API 가격, 전일종가, 변동폭 및 등락률 조회"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode('utf-8'))
            meta = data['chart']['result'][0]['meta']
            price = float(meta['regularMarketPrice'])
            prev_close = float(meta.get('chartPreviousClose', meta.get('previousClose', price)))
            change = price - prev_close
            change_pct = (change / prev_close) * 100 if prev_close != 0 else 0
            return {
                "price": price,
                "prev_close": prev_close,
                "change": change,
                "change_pct": change_pct
            }
    except Exception as e:
        print(f"[{symbol}] 상세 정보 조회 실패: {e}")
        return None

def get_krw_usd_rate_detail():
    """환율 (USD/KRW) 상세 조회"""
    detail = fetch_yahoo_market_detail("KRW=X")
    if detail:
        return detail
    return {"price": 1350.0, "prev_close": 1350.0, "change": 0.0, "change_pct": 0.0}

def get_macro_indicators_detail():
    """매크로 지표 조회 (10년물 국채 금리, VIX 공포지수, 달러 인덱스)"""
    tnx = fetch_yahoo_market_detail("^TNX")     # 미국 10년물 국채금리 (%)
    vix = fetch_yahoo_market_detail("^VIX")     # CBOE 변동성 지수
    dxy = fetch_yahoo_market_detail("DX-Y.NYB") # 달러 인덱스
    return tnx, vix, dxy

def get_google_news(query, max_count=2):
    """구글 뉴스 RSS 조회 및 한국어 번역"""
    articles = []
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            xml_data = res.read()
            root = ET.fromstring(xml_data)
            for item in root.findall('.//item')[:max_count]:
                raw_title = item.find('title').text if item.find('title') is not None else ""
                title_clean = html.unescape(raw_title).split(" - ")[0]
                # 한국어로 번역
                title_ko = translate_to_ko(title_clean)
                articles.append(title_ko)
    except Exception as e:
        print(f"뉴스 조회 실패 ({query}): {e}")
    return articles

def get_krx_stock_detail(symbol):
    """한국 주식 실시간 가격 및 전일 대비 변동 조회 (네이버 금융 API)"""
    url = f"https://m.stock.naver.com/api/stock/{symbol}/integration"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read().decode('utf-8'))
        total_info = data['totalInfos'][0]
        price = float(total_info['value'].replace(',', ''))
        change_str = total_info.get('compareToPreviousPrice', '0').replace(',', '')
        change = float(change_str)
        symbol_code = total_info.get('compareToPreviousPriceSymbol', '3')
        if symbol_code in ['4', '5']:
            change = -abs(change)
        prev_close = price - change
        change_pct = (change / prev_close) * 100 if prev_close != 0 else 0
        return {
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct
        }

def send_telegram_message(token, chat_id, text):
    """텔레그램 메시지 전송"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            print("텔레그램 알림 전송 성공!")
    except Exception as e:
        print(f"텔레그램 알림 전송 실패: {e}")

def main():
    config_path = os.path.join(os.path.dirname(__file__), "portfolio_config.json")
    if not os.path.exists(config_path):
        print("portfolio_config.json 파일을 찾을 수 없습니다.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    portfolio = config.get("portfolio", [])
    threshold = config.get("rebalance_threshold_percent", 5.0)

    rate_detail = get_krw_usd_rate_detail()
    usd_krw_rate = rate_detail["price"]
    tnx, vix, dxy = get_macro_indicators_detail()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    total_eval_krw = 0
    item_results = []

    for item in portfolio:
        name = item["name"]
        symbol = item["symbol"]
        market = item.get("market", "KRX").upper()
        quantity = item["quantity"]
        target_weight = item["target_weight"]
        category = item.get("category", "기타")

        try:
            if market == "US":
                detail = fetch_yahoo_market_detail(symbol)
                if not detail:
                    raise ValueError("가격 조회 불가")
                price_usd = detail["price"]
                change_usd = detail["change"]
                change_pct = detail["change_pct"]
                price_krw = price_usd * usd_krw_rate
            else:
                detail = get_krx_stock_detail(symbol)
                price_krw = detail["price"]
                change_usd = detail["change"] / usd_krw_rate
                change_pct = detail["change_pct"]
                price_usd = price_krw / usd_krw_rate

            eval_krw = price_krw * quantity
            total_eval_krw += eval_krw

            item_results.append({
                "name": name,
                "symbol": symbol,
                "market": market,
                "quantity": quantity,
                "target_weight": target_weight,
                "category": category,
                "price_krw": price_krw,
                "price_usd": price_usd,
                "change_usd": change_usd,
                "change_pct": change_pct,
                "eval_krw": eval_krw
            })
        except Exception as e:
            print(f"[{name}({symbol})] 시세 조회 실패: {e}")

    if total_eval_krw == 0:
        print("포트폴리오 평가금액을 계산할 수 없습니다.")
        return

    # 실시간 한국어 뉴스 수집
    macro_news = get_google_news("Federal Reserve interest rate inflation market", max_count=2)
    portfolio_news = get_google_news("NVIDIA OR Alphabet OR AMD OR Microsoft stock market", max_count=2)

    # 1. 헤더 및 종합 자산 정보
    rate_change_str = f"({rate_detail['change_pct']:+.2f}%)" if rate_detail['change_pct'] != 0 else ""
    lines = [
        "📊 *[포트폴리오 & 매크로 종합 리포트]*",
        f"⏰ 기준시각: {now_str} KST",
        f"💵 적용 환율: $1 = {usd_krw_rate:,.1f}원 {rate_change_str}",
        f"💰 *총 평가금액: {total_eval_krw:,.0f}원*",
        "----------------------------------------"
    ]

    # 2. 매크로 동향
    lines.append("🌐 *[매크로 핵심 지표 & 변동]*")
    if tnx:
        tnx_sign = "+" if tnx['change'] >= 0 else ""
        lines.append(f"• 🇺🇸 미국 10년물 금리: *{tnx['price']:.2f}%* ({tnx_sign}{tnx['change']:.2f}%p / {tnx['change_pct']:+.2f}%)")
    if vix:
        vix_status = "안정" if vix['price'] < 20 else ("경계" if vix['price'] < 25 else ("경고" if vix['price'] < 30 else "🚨 공포"))
        lines.append(f"• ⚡ VIX 공포지수: *{vix['price']:.2f}* ({vix['change_pct']:+.2f}%) [{vix_status}]")
    if dxy:
        lines.append(f"• 💵 달러 인덱스 (DXY): *{dxy['price']:.2f}* ({dxy['change_pct']:+.2f}%)")

    if vix and vix['price'] >= 25:
        lines.append("💡 *매크로 진단*: VIX 공포지수 급등 중! 주가 과매도 구간 진입 가능성이 높으므로 현금 실탄 집행 준비.")
    elif tnx and tnx['price'] >= 4.5:
        lines.append("💡 *매크로 진단*: 10년물 금리 4.5% 이상 유지 중. 기술주 멀티플 압박에 유의하며 분할 접근 권장.")
    else:
        lines.append("💡 *매크로 진단*: 변동성 및 금리가 안정적 흐름을 유지하고 있습니다.")

    lines.append("----------------------------------------")

    # 3. 실시간 주요 뉴스 (한국어 번역)
    lines.append("📰 *[실시간 주요 뉴스 (한국어 번역)]*")
    if macro_news:
        lines.append("*[매크로/금리]*")
        for news in macro_news:
            lines.append(f"• {news}")
    if portfolio_news:
        lines.append("*[보유 종목/빅테크]*")
        for news in portfolio_news:
            lines.append(f"• {news}")
    
    lines.append("----------------------------------------")

    # 4. 카테고리별 자산군 비중 요약
    lines.append("📂 *[자산 카테고리별 비중 요약]*")
    category_summary = {}
    for item in item_results:
        cat = item["category"]
        if cat not in category_summary:
            category_summary[cat] = {
                "eval_krw": 0,
                "target_weight": 0.0,
                "symbols": []
            }
        category_summary[cat]["eval_krw"] += item["eval_krw"]
        category_summary[cat]["target_weight"] += item["target_weight"]
        category_summary[cat]["symbols"].append(item["symbol"])

    for cat_name, cat_data in category_summary.items():
        cat_weight = (cat_data["eval_krw"] / total_eval_krw) * 100
        cat_target = cat_data["target_weight"]
        cat_diff = cat_weight - cat_target
        
        cat_status = "🟢 정상"
        if abs(cat_diff) >= threshold:
            cat_status = f"⚠️ 초과 (+{cat_diff:.1f}%)" if cat_diff > 0 else f"⚠️ 미달 ({cat_diff:.1f}%)"
            
        sym_list = ", ".join(cat_data["symbols"])
        lines.append(
            f"• *{cat_name}* ({sym_list})\n"
            f"  • 평가금액: {cat_data['eval_krw']:,.0f}원 | 현재비중: *{cat_weight:.1f}%* (목표 {cat_target:.1f}%) [{cat_status}]"
        )

    lines.append("----------------------------------------")

    # 5. 개별 종목 전일 대비 등락 & 비중 현황
    lines.append("📈 *[개별 종목 전일대비 등락 & 비중]*")
    rebalance_needed = False
    over_weighted = []
    under_weighted = []
    sgov_item = None

    for item in item_results:
        current_weight = (item["eval_krw"] / total_eval_krw) * 100
        weight_diff = current_weight - item["target_weight"]
        item["current_weight"] = current_weight
        item["weight_diff"] = weight_diff

        if item["symbol"] == "SGOV":
            sgov_item = item
        
        status_icon = "🟢"
        if abs(weight_diff) >= threshold:
            rebalance_needed = True
            status_icon = "⚠️"
            if weight_diff > 0:
                over_weighted.append((item['name'], weight_diff))
            else:
                under_weighted.append((item['name'], abs(weight_diff)))

        change_pct = item["change_pct"]
        if change_pct > 0:
            change_str = f"🔺 +{change_pct:.2f}% (+${item['change_usd']:.2f})"
        elif change_pct < 0:
            change_str = f"🔹 {change_pct:.2f}% (-${abs(item['change_usd']):.2f})"
        else:
            change_str = "➖ 0.00%"

        if item["market"] == "US":
            price_disp = f"${item['price_usd']:,.2f}"
        else:
            price_disp = f"{item['price_krw']:,.0f}원"

        lines.append(
            f"{status_icon} *{item['name']}* ({item['symbol']})\n"
            f"  • 현재가: {price_disp} | 전일대비: {change_str}\n"
            f"  • 평가금액: {item['eval_krw']:,.0f}원 | 비중: *{current_weight:.1f}%* (목표 {item['target_weight']:.1f}%)\n"
        )

    lines.append("----------------------------------------")

    # 6. 현금 실탄 (SGOV) 사용 시점 진단
    lines.append("💳 *[현금 실탄(SGOV) 집행 타이밍 진단]*")
    
    vix_val = vix['price'] if vix else 15.0
    cash_signal = "HOLD"
    cash_reason = []

    if vix_val >= 30:
        cash_signal = "STRONG_BUY"
        cash_reason.append("🚨 **VIX 30 이상 (패닉 장세)**: 시장 극심한 공포 구간! SGOV의 20~30%를 매도하여 주도주(NVDA/GOOGL/AMD) 저가 매수 고려.")
    elif vix_val >= 25:
        cash_signal = "BUY"
        cash_reason.append("⚠️ **VIX 25 이상 (투매 발생)**: 조정을 받는 기술주의 분할 매수를 위해 SGOV 현금 실탄 일부 사용 진입 구간.")

    if sgov_item and sgov_item["weight_diff"] >= threshold:
        cash_signal = "BUY"
        cash_reason.append(f"📊 **SGOV 비중 초과 (+{sgov_item['weight_diff']:.1f}%)**: 주식 주가 하락으로 현금 비중이 커졌습니다. 하락 종목 저가 매수 고려.")
    elif sgov_item and sgov_item["weight_diff"] <= -threshold:
        cash_reason.append(f"📊 **SGOV 비중 부족 ({sgov_item['weight_diff']:.1f}%)**: 현금 자산이 줄었으므로 상승 종목 털어 SGOV 충전 필요.")

    if cash_signal == "STRONG_BUY":
        lines.append("🔥 **[강력 매수 시그널] 현금 실탄 집행 권장!**")
        for r in cash_reason:
            lines.append(f"• {r}")
    elif cash_signal == "BUY":
        lines.append("🛒 **[분할 매수 시그널] 현금 실탄 일부 사용 고려**")
        for r in cash_reason:
            lines.append(f"• {r}")
    else:
        lines.append("🛡️ **[실탄 대기] SGOV 이자 수취 중 (HOLD)**")
        lines.append("현재 시장이 안정적이며 SGOV 비중이 정상이므로 현금 실탄을 온전히 보존하며 연 5%대 이자를 챙기세요.")

    lines.append("----------------------------------------")

    # 7. 종합 대응 가이드
    lines.append("🎯 *[고금리·고변동성 장세 종합 대응 가이드]*")
    if rebalance_needed:
        over_names = ", ".join([name for name, _ in over_weighted]) if over_weighted else "없음"
        under_names = ", ".join([name for name, _ in under_weighted]) if under_weighted else "없음"
        lines.append(
            f"🔔 *비중 이탈 발생! (기준 {threshold}% 이상)*\n"
            f"• **비중 초과 종목**: {over_names}\n"
            f"• **비중 미달 종목**: {under_names}\n"
            f"👉 **액션 플랜**: 비중 초과 종목 일부 매도 ➡️ 미달 종목/방어자산 분할 매수로 리밸런싱을 검토하세요."
        )
    else:
        lines.append(
            "✅ **포트폴리오 안정 유지 중**\n"
            "현재 기술주, 배당주, 방어자산의 비중이 목표 범위 내에서 균형을 이루고 있습니다.\n"
            "👉 **액션 플랜**: 별도의 매매 없이 현재 비중 포지션을 그대로 유지(Hold)하세요."
        )

    msg_text = "\n".join(lines)
    
    try:
        print(msg_text)
    except Exception:
        print("포트폴리오 리포트 생성 완료 (콘솔 출력 중 인코딩 차이 무시)")

    # 텔레그램 전송
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if telegram_token and telegram_chat_id:
        send_telegram_message(telegram_token, telegram_chat_id, msg_text)
    else:
        print("\n[안내] TELEGRAM_TOKEN 및 TELEGRAM_CHAT_ID 환경변수가 설정되지 않아 콘솔 출력만 진행했습니다.")

if __name__ == "__main__":
    main()
