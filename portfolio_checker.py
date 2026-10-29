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
    """야후 파이낸스 API 가격, 전일종가, 변동폭, 등락률 및 시간외(애프터/프리) 시세 조회"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=1d&interval=5m&includePrePost=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode('utf-8'))
            result = data['chart']['result'][0]
            meta = result['meta']
            price = float(meta['regularMarketPrice'])
            prev_close = float(meta.get('chartPreviousClose', meta.get('previousClose', price)))
            change = price - prev_close
            change_pct = (change / prev_close) * 100 if prev_close != 0 else 0

            # 시간외(애프터마켓/프리마켓) 최신 체결가 추출 (정규장 종가 대비 변동 계산)
            after_price = price
            has_after = False
            reg_time = meta.get('regularMarketTime', 0)
            post_time = meta.get('postMarketTime', 0)
            pre_time = meta.get('preMarketTime', 0)

            if meta.get('postMarketPrice') and post_time >= reg_time:
                after_price = float(meta['postMarketPrice'])
                has_after = True
            elif meta.get('preMarketPrice') and pre_time >= reg_time:
                after_price = float(meta['preMarketPrice'])
                has_after = True
            else:
                timestamps = result.get('timestamp') or []
                quotes = result.get('indicators', {}).get('quote', [{}])
                closes = quotes[0].get('close', []) if quotes else []
                reg_end = meta.get('currentTradingPeriod', {}).get('regular', {}).get('end', reg_time)

                for ts, c in zip(reversed(timestamps), reversed(closes)):
                    if c is not None:
                        if (reg_end and ts >= reg_end) or (reg_time and ts > reg_time):
                            after_price = float(c)
                            has_after = True
                        break

            after_change = after_price - price
            after_change_pct = (after_change / price) * 100 if price != 0 else 0.0

            return {
                "price": price,
                "prev_close": prev_close,
                "change": change,
                "change_pct": change_pct,
                "after_price": after_price,
                "after_change": after_change,
                "after_change_pct": after_change_pct,
                "has_after": has_after
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
    """텔레그램 텍스트 메시지 전송"""
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
            print("텔레그램 텍스트 리포트 전송 성공!")
    except Exception as e:
        print(f"텔레그램 텍스트 전송 실패: {e}")

def export_portfolio_json(
    usd_krw_rate, tnx, vix, dxy,
    total_eval_krw, total_buy_krw, total_profit_krw, total_profit_pct,
    total_after_eval_krw, total_after_profit_krw, total_after_profit_pct,
    item_results, category_summary, json_path="portfolio_data.json"
):
    """대시보드가 즉각 로딩할 수 있도록 데이터 저장 및 일별 누적 데이터 업데이트"""
    try:
        kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
        today_date = kst_now.strftime("%Y-%m-%d")

        existing_history = []
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    existing_history = old_data.get("history", [])
            except Exception:
                existing_history = []

        # 오늘 날짜 기존 히스토리 갱신
        history = [h for h in existing_history if h.get("date") != today_date]
        history.append({
            "date": today_date,
            "total_eval_krw": round(total_eval_krw),
            "total_after_eval_krw": round(total_after_eval_krw),
            "total_buy_krw": round(total_buy_krw),
            "total_profit_krw": round(total_profit_krw),
            "total_profit_pct": round(total_profit_pct, 2),
            "total_after_profit_krw": round(total_after_profit_krw),
            "total_after_profit_pct": round(total_after_profit_pct, 2),
            "usd_krw_rate": usd_krw_rate
        })
        history.sort(key=lambda x: x["date"])

        data = {
            "updated_at": kst_now.strftime("%Y-%m-%d %H:%M"),
            "usd_krw_rate": usd_krw_rate,
            "tnx": tnx,
            "vix": vix,
            "dxy": dxy,
            "total_eval_krw": total_eval_krw,
            "total_buy_krw": total_buy_krw,
            "total_profit_krw": total_profit_krw,
            "total_profit_pct": total_profit_pct,
            "total_after_eval_krw": total_after_eval_krw,
            "total_after_profit_krw": total_after_profit_krw,
            "total_after_profit_pct": total_after_profit_pct,
            "items": item_results,
            "categories": category_summary,
            "history": history
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("portfolio_data.json 계산 파일 및 히스토리 기록 완료!")
    except Exception as e:
        print(f"JSON 내보내기 실패: {e}")

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
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    now_str = kst_now.strftime("%Y-%m-%d %H:%M")

    total_eval_krw = 0
    total_after_eval_krw = 0
    total_buy_usd = 0
    item_results = []

    for item in portfolio:
        name = item["name"]
        symbol = item["symbol"]
        market = item.get("market", "KRX").upper()
        quantity = item["quantity"]
        target_weight = item["target_weight"]
        category = item.get("category", "기타")
        buy_price = item.get("buy_price", 0.0)

        try:
            if market == "US":
                detail = fetch_yahoo_market_detail(symbol)
                if not detail:
                    raise ValueError("가격 조회 불가")
                price_usd = detail["price"]
                change_usd = detail["change"]
                change_pct = detail["change_pct"]
                price_krw = price_usd * usd_krw_rate

                after_price_usd = detail.get("after_price", price_usd)
                after_change_usd = detail.get("after_change", 0.0)
                after_change_pct = detail.get("after_change_pct", 0.0)
                has_after = detail.get("has_after", False)
                after_price_krw = after_price_usd * usd_krw_rate
            else:
                detail = get_krx_stock_detail(symbol)
                price_krw = detail["price"]
                change_usd = detail["change"] / usd_krw_rate
                change_pct = detail["change_pct"]
                price_usd = price_krw / usd_krw_rate

                after_price_usd = price_usd
                after_change_usd = 0.0
                after_change_pct = 0.0
                has_after = False
                after_price_krw = price_krw

            eval_krw = price_krw * quantity
            eval_usd = price_usd * quantity
            buy_eval_usd = buy_price * quantity if buy_price > 0 else eval_usd
            profit_usd = eval_usd - buy_eval_usd
            profit_krw = profit_usd * usd_krw_rate
            profit_pct = (profit_usd / buy_eval_usd * 100) if buy_eval_usd > 0 else 0.0

            after_eval_krw = after_price_krw * quantity
            after_eval_usd = after_price_usd * quantity
            after_profit_usd = after_eval_usd - buy_eval_usd
            after_profit_krw = after_profit_usd * usd_krw_rate
            after_profit_pct = (after_profit_usd / buy_eval_usd * 100) if buy_eval_usd > 0 else 0.0

            total_eval_krw += eval_krw
            total_after_eval_krw += after_eval_krw
            total_buy_usd += buy_eval_usd

            item_results.append({
                "name": name,
                "symbol": symbol,
                "market": market,
                "quantity": quantity,
                "target_weight": target_weight,
                "category": category,
                "buy_price": buy_price,
                "price_krw": price_krw,
                "price_usd": price_usd,
                "change_usd": change_usd,
                "change_pct": change_pct,
                "eval_krw": eval_krw,
                "eval_usd": eval_usd,
                "buy_eval_usd": buy_eval_usd,
                "profit_usd": profit_usd,
                "profit_krw": profit_krw,
                "profit_pct": profit_pct,
                "after_price_usd": after_price_usd,
                "after_price_krw": after_price_krw,
                "after_change_usd": after_change_usd,
                "after_change_pct": after_change_pct,
                "has_after": has_after,
                "after_eval_krw": after_eval_krw,
                "after_eval_usd": after_eval_usd,
                "after_profit_usd": after_profit_usd,
                "after_profit_krw": after_profit_krw,
                "after_profit_pct": after_profit_pct
            })
        except Exception as e:
            print(f"[{name}({symbol})] 시세 조회 실패: {e}")

    if total_eval_krw == 0:
        print("포트폴리오 평가금액을 계산할 수 없습니다.")
        return

    total_buy_krw = total_buy_usd * usd_krw_rate
    total_profit_usd = sum(item["profit_usd"] for item in item_results)
    total_profit_krw = total_profit_usd * usd_krw_rate
    total_profit_pct = (total_profit_usd / total_buy_usd * 100) if total_buy_usd > 0 else 0.0

    total_after_profit_usd = sum(item["after_profit_usd"] for item in item_results)
    total_after_profit_krw = total_after_profit_usd * usd_krw_rate
    total_after_profit_pct = (total_after_profit_usd / total_buy_usd * 100) if total_buy_usd > 0 else 0.0
    total_after_diff_krw = total_after_eval_krw - total_eval_krw
    total_after_diff_pct = (total_after_diff_krw / total_eval_krw * 100) if total_eval_krw > 0 else 0.0

    # 실시간 한국어 뉴스 수집
    macro_news = get_google_news("Federal Reserve interest rate inflation market", max_count=2)
    portfolio_news = get_google_news("NVIDIA OR Alphabet OR AMD OR Microsoft stock market", max_count=2)

    # 1. 헤더 및 종합 자산 정보 (모바일 한 줄 폭에 맞춰 깔끔하게 정렬)
    rate_change_str = f"({rate_detail['change_pct']:+.2f}%)" if rate_detail['change_pct'] != 0 else ""
    profit_sign = "+" if total_profit_krw >= 0 else ""
    after_profit_sign = "+" if total_after_profit_krw >= 0 else ""
    divider = "━━━━━━━━━━━━━━━━━━"
    lines = [
        "📊 *[포트폴리오 & 매크로 리포트]*",
        f"⏰ {now_str} KST",
        f"💵 환율: 1$ = {usd_krw_rate:,.1f}원 {rate_change_str}",
        "",
        "💰 *[총 자산 요약]*",
        f"☀️ *정규 총액: {total_eval_krw:,.0f}원*",
        f"  └ 손익: *{profit_sign}{total_profit_krw:,.0f}원 ({total_profit_pct:+.2f}%)*",
        f"🌙 *애프터 총액: {total_after_eval_krw:,.0f}원* ({total_after_diff_pct:+.2f}%)",
        f"  └ 손익: *{after_profit_sign}{total_after_profit_krw:,.0f}원 ({total_after_profit_pct:+.2f}%)*",
        divider
    ]

    # 2. 매크로 동향
    lines.append("🌐 *[매크로 핵심 지표]*")
    if tnx:
        tnx_sign = "+" if tnx['change'] >= 0 else ""
        lines.append(f"• 🇺🇸 미 10년물: *{tnx['price']:.2f}%* ({tnx_sign}{tnx['change']:.2f}%p)")
    if vix:
        vix_status = "안정" if vix['price'] < 20 else ("경계" if vix['price'] < 25 else ("경고" if vix['price'] < 30 else "🚨 공포"))
        lines.append(f"• ⚡ VIX 지수: *{vix['price']:.2f}* ({vix['change_pct']:+.2f}%) [{vix_status}]")
    if dxy:
        lines.append(f"• 💵 달러(DXY): *{dxy['price']:.2f}* ({dxy['change_pct']:+.2f}%)")

    if vix and vix['price'] >= 25:
        lines.append("💡 *진단*: VIX 급등! 과매도 진입 가능성 높음 (실탄 준비)")
    elif tnx and tnx['price'] >= 4.5:
        lines.append("💡 *진단*: 10년물 4.5% 상회 중. 기술주 분할 접근 권장")
    else:
        lines.append("💡 *진단*: 변동성 및 금리가 안정적 흐름 유지 중")

    lines.append(divider)

    # 3. 실시간 주요 뉴스 (한국어 번역)
    lines.append("📰 *[실시간 주요 뉴스]*")
    if macro_news:
        lines.append("*[매크로/금리]*")
        for news in macro_news:
            lines.append(f"• {news}")
    if portfolio_news:
        lines.append("*[보유 종목/빅테크]*")
        for news in portfolio_news:
            lines.append(f"• {news}")
    
    lines.append(divider)

    # 4. 카테고리별 자산군 비중 요약
    lines.append("📂 *[카테고리별 비중 & 수익]*")
    category_summary = {}
    for item in item_results:
        cat = item["category"]
        if cat not in category_summary:
            category_summary[cat] = {
                "eval_krw": 0,
                "after_eval_krw": 0,
                "buy_eval_usd": 0,
                "profit_usd": 0,
                "profit_krw": 0,
                "after_profit_usd": 0,
                "after_profit_krw": 0,
                "target_weight": 0.0,
                "symbols": []
            }
        category_summary[cat]["eval_krw"] += item["eval_krw"]
        category_summary[cat]["after_eval_krw"] += item["after_eval_krw"]
        category_summary[cat]["buy_eval_usd"] += item["buy_eval_usd"]
        category_summary[cat]["profit_usd"] += item["profit_usd"]
        category_summary[cat]["profit_krw"] += item["profit_krw"]
        category_summary[cat]["after_profit_usd"] += item["after_profit_usd"]
        category_summary[cat]["after_profit_krw"] += item["after_profit_krw"]
        category_summary[cat]["target_weight"] += item["target_weight"]
        category_summary[cat]["symbols"].append(item["symbol"])

    for cat_name, cat_data in category_summary.items():
        cat_weight = (cat_data["eval_krw"] / total_eval_krw) * 100
        cat_after_weight = (cat_data["after_eval_krw"] / total_after_eval_krw) * 100 if total_after_eval_krw > 0 else cat_weight
        cat_target = cat_data["target_weight"]
        cat_diff = cat_weight - cat_target
        cat_after_diff = cat_after_weight - cat_target

        cat_profit_pct = (cat_data["profit_usd"] / cat_data["buy_eval_usd"] * 100) if cat_data["buy_eval_usd"] > 0 else 0.0
        cat_after_profit_pct = (cat_data["after_profit_usd"] / cat_data["buy_eval_usd"] * 100) if cat_data["buy_eval_usd"] > 0 else 0.0
        cat_after_change_krw = cat_data["after_eval_krw"] - cat_data["eval_krw"]
        cat_after_change_pct = (cat_after_change_krw / cat_data["eval_krw"] * 100) if cat_data["eval_krw"] > 0 else 0.0

        cat_data["profit_pct"] = cat_profit_pct
        cat_data["after_profit_pct"] = cat_after_profit_pct
        cat_data["after_change_krw"] = cat_after_change_krw
        cat_data["after_change_pct"] = cat_after_change_pct
        cat_data["current_weight"] = cat_weight
        cat_data["after_weight"] = cat_after_weight
        cat_data["after_weight_diff"] = cat_after_diff
        
        cat_status = "🟢정상"
        if abs(cat_diff) >= threshold:
            cat_status = f"⚠️초과({cat_diff:+.1f}%)" if cat_diff > 0 else f"⚠️미달({cat_diff:.1f}%)"
            
        sym_list = ", ".join(cat_data["symbols"])
        lines.append(
            f"\n*{cat_name}* [{cat_status}]\n"
            f"  └ 종목: {sym_list}\n"
            f"  • ☀️정규: {cat_data['eval_krw']:,.0f}원 (*{cat_profit_pct:+.1f}%*)\n"
            f"  • 🌙애프터: {cat_data['after_eval_krw']:,.0f}원 (*{cat_after_profit_pct:+.1f}%* | {cat_after_change_pct:+.2f}%)\n"
            f"  • 📊비중: 정규 *{cat_weight:.1f}%* ➡️ 🌙 *{cat_after_weight:.1f}%* (목표 {cat_target:.1f}%)"
        )

    lines.append(divider)

    # 5. 개별 종목 전일 대비 등락 & 비중 현황
    lines.append("📈 *[개별 종목 시세 & 비중]*")
    rebalance_needed = False
    over_weighted = []
    under_weighted = []
    sgov_item = None

    for item in item_results:
        current_weight = (item["eval_krw"] / total_eval_krw) * 100
        weight_diff = current_weight - item["target_weight"]
        after_weight = (item["after_eval_krw"] / total_after_eval_krw) * 100 if total_after_eval_krw > 0 else current_weight
        after_weight_diff = after_weight - item["target_weight"]

        item["current_weight"] = current_weight
        item["weight_diff"] = weight_diff
        item["after_weight"] = after_weight
        item["after_weight_diff"] = after_weight_diff

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
            change_str = f"🔺+{change_pct:.2f}% (+${item['change_usd']:.2f})"
        elif change_pct < 0:
            change_str = f"🔹{change_pct:.2f}% (-${abs(item['change_usd']):.2f})"
        else:
            change_str = "➖0.00%"

        after_pct = item["after_change_pct"]
        after_usd = item["after_change_usd"]
        if after_pct > 0:
            after_change_str = f"🔺+{after_pct:.2f}% (+${after_usd:.2f})"
        elif after_pct < 0:
            after_change_str = f"🔹{after_pct:.2f}% (-${abs(after_usd):.2f})"
        else:
            after_change_str = "➖0.00%"

        if item["market"] == "US":
            lines.append(
                f"\n{status_icon} *{item['name']} ({item['symbol']})*\n"
                f"  • ☀️정규가: *${item['price_usd']:,.2f}* ({change_str})\n"
                f"  • 🌙애프터: *${item['after_price_usd']:,.2f}* ({after_change_str})\n"
                f"  • 💰평가액: {item['eval_krw']:,.0f}원 (🌙 {item['after_eval_krw']:,.0f}원)\n"
                f"  • 🏆수익률: *{item['profit_pct']:+.2f}%* (${item['profit_usd']:+,.0f}) ➡️ 🌙 *{item['after_profit_pct']:+.2f}%*\n"
                f"  • 📊비중: 정규 *{current_weight:.1f}%* ➡️ 🌙 *{after_weight:.1f}%* (목표 {item['target_weight']:.1f}%)"
            )
        else:
            lines.append(
                f"\n{status_icon} *{item['name']} ({item['symbol']})*\n"
                f"  • ☀️현재가: *{item['price_krw']:,.0f}원* ({change_str})\n"
                f"  • 💰평가액: {item['eval_krw']:,.0f}원\n"
                f"  • 🏆수익률: *{item['profit_pct']:+.2f}%* ({item['profit_krw']:+,.0f}원)\n"
                f"  • 📊비중: *{current_weight:.1f}%* (목표 {item['target_weight']:.1f}%)"
            )

    lines.append(divider)

    # 6. 현금 실탄 (SGOV) 사용 시점 진단
    lines.append("💳 *[현금 실탄(SGOV) 타이밍 진단]*")
    
    vix_val = vix['price'] if vix else 15.0
    cash_signal = "HOLD"
    cash_reason = []

    if vix_val >= 30:
        cash_signal = "STRONG_BUY"
        cash_reason.append("🚨 *VIX 30 이상 (패닉 장세)*: SGOV 20~30% 매도 후 주도주 저가 매수 고려")
    elif vix_val >= 25:
        cash_signal = "BUY"
        cash_reason.append("⚠️ *VIX 25 이상 (투매 발생)*: 조정 기술주 분할 매수를 위해 SGOV 일부 집행")

    if sgov_item and sgov_item["weight_diff"] >= threshold:
        cash_signal = "BUY"
        cash_reason.append(f"📊 *SGOV 비중 초과 (+{sgov_item['weight_diff']:.1f}%)*: 하락 종목 저가 매수 고려")
    elif sgov_item and sgov_item["weight_diff"] <= -threshold:
        cash_reason.append(f"📊 *SGOV 비중 부족 ({sgov_item['weight_diff']:.1f}%)*: 상승 종목 일부 익절 후 SGOV 충전")

    if cash_signal == "STRONG_BUY":
        lines.append("🔥 *[강력 매수 시그널] 실탄 집행 권장!*")
        for r in cash_reason:
            lines.append(f"• {r}")
    elif cash_signal == "BUY":
        lines.append("🛒 *[분할 매수 시그널] 실탄 일부 사용 고려*")
        for r in cash_reason:
            lines.append(f"• {r}")
    else:
        lines.append("🛡️ *[실탄 대기] SGOV 이자 수취 중 (HOLD)*")
        lines.append("• 시장 및 현금 비중 안정적 (연 5%대 이자 수취)")

    lines.append(divider)

    # 7. 종합 대응 가이드
    lines.append("🎯 *[종합 대응 가이드]*")
    if rebalance_needed:
        over_names = ", ".join([name for name, _ in over_weighted]) if over_weighted else "없음"
        under_names = ", ".join([name for name, _ in under_weighted]) if under_weighted else "없음"
        lines.append(
            f"🔔 *비중 이탈 발생! (기준 {threshold}% 이상)*\n"
            f"• *비중 초과*: {over_names}\n"
            f"• *비중 미달*: {under_names}\n"
            f"👉 초과 종목 일부 매도 ➡️ 미달 종목 분할 매수 검토"
        )
    else:
        lines.append(
            "✅ *포트폴리오 균형 유지 중*\n"
            "• 전 자산군 목표 비중 범위 내 정상 유지 중\n"
            "👉 별도 매매 없이 현재 포지션 유지 (Hold)"
        )

    lines.append(divider)
    lines.append("📱 *[실시간 대시보드 웹페이지]*")
    lines.append("👉 [대시보드 열기 (차트 & 상세 표)](https://hoohoon.github.io/my-stock-portfolio/)")

    msg_text = "\n".join(lines)
    
    try:
        print(msg_text)
    except Exception:
        print("포트폴리오 리포트 생성 완료 (콘솔 출력 중 인코딩 차이 무시)")

    # JSON 데이터 내보내기 (대시보드 즉시 로딩용)
    export_portfolio_json(
        usd_krw_rate, tnx, vix, dxy,
        total_eval_krw, total_buy_krw, total_profit_krw, total_profit_pct,
        total_after_eval_krw, total_after_profit_krw, total_after_profit_pct,
        item_results, category_summary
    )

    # 텔레그램 텍스트 전송
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if telegram_token and telegram_chat_id:
        send_telegram_message(telegram_token, telegram_chat_id, msg_text)
    else:
        print("\n[안내] TELEGRAM_TOKEN 및 TELEGRAM_CHAT_ID 환경변수가 설정되지 않아 콘솔 출력만 진행했습니다.")

if __name__ == "__main__":
    main()
