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

DEFAULT_REBALANCE_RULES = {
    "category_abs_band_percent": 5.0,
    "category_rel_band_ratio": 0.25,
    "stock_abs_band_percent": 3.0,
    "stock_rel_band_ratio": 0.20,
    "min_band_percent": 1.0,
    "watch_ratio": 0.8,
    "cash_floor_percent": 15.0,
    "min_trade_usd": 150.0,
    "drawdown_lookback_range": "3mo",
    "drawdown_warn_percent": -15.0,
    "drawdown_strong_percent": -25.0,
    "daily_drop_warn_percent": -5.0
}


def get_rebalance_rules(config):
    """설정의 rebalance_rules를 기본값과 병합하여 반환"""
    rules = dict(DEFAULT_REBALANCE_RULES)
    user_rules = config.get("rebalance_rules") or {}
    for k, v in user_rules.items():
        if v is not None:
            rules[k] = v
    # 하위 호환: rebalance_threshold_percent만 수정한 경우 반영
    if "rebalance_threshold_percent" in config and "category_abs_band_percent" not in user_rules:
        rules["category_abs_band_percent"] = float(config["rebalance_threshold_percent"])
    return rules


def calc_band(target_weight, abs_cap, rel_ratio, min_band):
    """5/25 또는 3/20 허용 이탈폭(Band) 계산 (최소 하한 보장)"""
    if target_weight <= 0:
        return 0.0
    raw = min(float(abs_cap), float(target_weight) * float(rel_ratio))
    return round(max(float(min_band), raw), 1)


def classify_weight_status(weight_diff, band, watch_ratio=0.8):
    """정규장 이탈 상태 3단계 판정 (NORMAL / WATCH / BREACH)"""
    if band <= 0:
        return "NORMAL"
    abs_diff = abs(weight_diff)
    watch_band = round(band * watch_ratio, 2)
    if abs_diff >= band:
        return "BREACH_OVER" if weight_diff > 0 else "BREACH_UNDER"
    if abs_diff >= watch_band:
        return "WATCH_OVER" if weight_diff > 0 else "WATCH_UNDER"
    return "NORMAL"


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


def fetch_yahoo_recent_high(symbol, range_str="3mo"):
    """최근 N개월 일봉 기준 최고가 조회 (낙폭/Drawdown 감지용)"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={urllib.parse.quote(range_str)}&interval=1d"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode('utf-8'))
            result = data['chart']['result'][0]
            quotes = result.get('indicators', {}).get('quote', [{}])
            if not quotes:
                return None
            highs = [float(h) for h in (quotes[0].get('high') or []) if h is not None and h > 0]
            closes = [float(c) for c in (quotes[0].get('close') or []) if c is not None and c > 0]
            candidates = highs if highs else closes
            return max(candidates) if candidates else None
    except Exception as e:
        print(f"[{symbol}] 최근 고점 조회 실패 (무시하고 진행): {e}")
        return None


def fetch_yahoo_market_detail(symbol, include_recent_high=False, high_range="3mo"):
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

            recent_high = None
            if include_recent_high:
                recent_high = fetch_yahoo_recent_high(symbol, high_range)
                if recent_high is not None:
                    recent_high = max(recent_high, price)

            return {
                "price": price,
                "prev_close": prev_close,
                "change": change,
                "change_pct": change_pct,
                "after_price": after_price,
                "after_change": after_change,
                "after_change_pct": after_change_pct,
                "has_after": has_after,
                "recent_high": recent_high
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
            "change_pct": change_pct,
            "after_price": price,
            "after_change": 0.0,
            "after_change_pct": 0.0,
            "has_after": False,
            "recent_high": None
        }


def evaluate_portfolio(config, market_quotes, usd_krw_rate, tnx=None, vix=None, dxy=None):
    """
    포트폴리오 전체 비중, 수익률, 5/25·3/20 허용 밴드, 2단계 이탈,
    가격 낙폭(Drawdown), 현금×VIX 통합 매트릭스, 리밸런싱 매매 금액·주수를 계산하는 단일 엔진.
    """
    portfolio = config.get("portfolio", [])
    rules = get_rebalance_rules(config)
    cash_category = config.get("cash_category", "💵 초단기채/현금")

    # 최상위 cash_usd + (하위 호환) 종목 내 cash_usd 합산
    top_cash_usd = float(config.get("cash_usd", 0.0) or 0.0)
    legacy_item_cash_usd = sum(float(item.get("cash_usd", 0.0) or 0.0) for item in portfolio)
    total_cash_usd = round(top_cash_usd + legacy_item_cash_usd, 2)

    target_weight_sum = round(sum(float(item.get("target_weight", 0.0)) for item in portfolio), 2)
    target_weight_valid = abs(target_weight_sum - 100.0) <= 0.01

    vix_val = float(vix["price"]) if (vix and vix.get("price") is not None) else 15.0

    item_results = []
    total_eval_usd = 0.0
    total_after_eval_usd = 0.0
    total_buy_usd = 0.0
    total_invested_buy_usd = 0.0

    # 1. 개별 투자 종목 계산 (예수금 완전 분리 -> 가격*수량 = 평가액 100% 일치)
    for item in portfolio:
        name = item["name"]
        symbol = item["symbol"]
        market = item.get("market", "KRX").upper()
        quantity = float(item.get("quantity", 0))
        target_weight = float(item.get("target_weight", 0))
        category = item.get("category", "기타")
        buy_price = float(item.get("buy_price", 0.0) or 0.0)

        detail = market_quotes.get(symbol)
        if not detail:
            continue

        if market == "US":
            price_usd = float(detail["price"])
            change_usd = float(detail.get("change", 0.0))
            change_pct = float(detail.get("change_pct", 0.0))
            price_krw = price_usd * usd_krw_rate

            after_price_usd = float(detail.get("after_price", price_usd))
            after_change_usd = float(detail.get("after_change", 0.0))
            after_change_pct = float(detail.get("after_change_pct", 0.0))
            has_after = bool(detail.get("has_after", False))
            after_price_krw = after_price_usd * usd_krw_rate
            recent_high_usd = detail.get("recent_high")
            if recent_high_usd is not None:
                recent_high_usd = max(float(recent_high_usd), price_usd)
        else:
            price_krw = float(detail["price"])
            price_usd = price_krw / usd_krw_rate
            change_usd = float(detail.get("change", 0.0)) / usd_krw_rate
            change_pct = float(detail.get("change_pct", 0.0))

            after_price_usd = price_usd
            after_change_usd = 0.0
            after_change_pct = 0.0
            has_after = False
            after_price_krw = price_krw
            recent_high_usd = None

        eval_usd = price_usd * quantity
        eval_krw = eval_usd * usd_krw_rate
        buy_eval_usd = (buy_price * quantity) if buy_price > 0 else eval_usd
        profit_usd = eval_usd - buy_eval_usd
        profit_krw = profit_usd * usd_krw_rate
        profit_pct = (profit_usd / buy_eval_usd * 100.0) if buy_eval_usd > 0 else 0.0

        after_eval_usd = after_price_usd * quantity
        after_eval_krw = after_eval_usd * usd_krw_rate
        after_profit_usd = after_eval_usd - buy_eval_usd
        after_profit_krw = after_profit_usd * usd_krw_rate
        after_profit_pct = (after_profit_usd / buy_eval_usd * 100.0) if buy_eval_usd > 0 else 0.0

        # 최근 고점 대비 낙폭 계산
        if recent_high_usd and recent_high_usd > 0:
            drawdown_pct = ((price_usd - recent_high_usd) / recent_high_usd) * 100.0
        else:
            drawdown_pct = 0.0

        total_eval_usd += eval_usd
        total_after_eval_usd += after_eval_usd
        total_buy_usd += buy_eval_usd
        total_invested_buy_usd += buy_eval_usd

        item_results.append({
            "name": name,
            "symbol": symbol,
            "market": market,
            "is_cash": False,
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "cash_usd": 0.0,
            "target_weight": int(target_weight) if target_weight.is_integer() else round(target_weight, 2),
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
            "after_profit_pct": after_profit_pct,
            "recent_high_usd": recent_high_usd,
            "drawdown_pct": round(drawdown_pct, 2)
        })

    # 2. USD 예수금을 초단기채/현금 카테고리 내 별도 항목으로 추가
    if total_cash_usd > 0:
        cash_krw = total_cash_usd * usd_krw_rate
        total_eval_usd += total_cash_usd
        total_after_eval_usd += total_cash_usd
        total_buy_usd += total_cash_usd

        cash_entry = {
            "name": "달러 예수금 (USD)",
            "symbol": "USD",
            "market": "CASH",
            "is_cash": True,
            "quantity": total_cash_usd,
            "cash_usd": total_cash_usd,
            "target_weight": 0,
            "category": cash_category,
            "buy_price": 1.0,
            "price_krw": usd_krw_rate,
            "price_usd": 1.0,
            "change_usd": 0.0,
            "change_pct": 0.0,
            "eval_krw": cash_krw,
            "eval_usd": total_cash_usd,
            "buy_eval_usd": total_cash_usd,
            "profit_usd": 0.0,
            "profit_krw": 0.0,
            "profit_pct": 0.0,
            "after_price_usd": 1.0,
            "after_price_krw": usd_krw_rate,
            "after_change_usd": 0.0,
            "after_change_pct": 0.0,
            "has_after": False,
            "after_eval_krw": cash_krw,
            "after_eval_usd": total_cash_usd,
            "after_profit_usd": 0.0,
            "after_profit_krw": 0.0,
            "after_profit_pct": 0.0,
            "recent_high_usd": 1.0,
            "drawdown_pct": 0.0
        }
        # SGOV 바로 다음에 위치시켜 보기 깔끔하게 정렬
        insert_idx = 0
        for idx, it in enumerate(item_results):
            if it["category"] == cash_category:
                insert_idx = idx + 1
        item_results.insert(insert_idx, cash_entry)

    if total_eval_usd <= 0:
        raise ValueError("포트폴리오 평가금액을 계산할 수 없습니다.")

    total_eval_krw = total_eval_usd * usd_krw_rate
    total_after_eval_krw = total_after_eval_usd * usd_krw_rate
    total_buy_krw = total_buy_usd * usd_krw_rate
    total_invested_buy_krw = total_invested_buy_usd * usd_krw_rate

    total_profit_usd = sum(item["profit_usd"] for item in item_results if not item["is_cash"])
    total_profit_krw = total_profit_usd * usd_krw_rate
    total_profit_pct = (total_profit_usd / total_buy_usd * 100.0) if total_buy_usd > 0 else 0.0

    total_after_profit_usd = sum(item["after_profit_usd"] for item in item_results if not item["is_cash"])
    total_after_profit_krw = total_after_profit_usd * usd_krw_rate
    total_after_profit_pct = (total_after_profit_usd / total_buy_usd * 100.0) if total_buy_usd > 0 else 0.0

    # 3. 카테고리별 집계 (5/25 허용 밴드 + 수익률 분모에서 예수금 제외로 100% 일관성 확보)
    category_summary = {}
    non_cash_counts = {}
    for item in item_results:
        cat = item["category"]
        if cat not in category_summary:
            category_summary[cat] = {
                "eval_usd": 0.0,
                "eval_krw": 0.0,
                "after_eval_usd": 0.0,
                "after_eval_krw": 0.0,
                "buy_eval_usd": 0.0,
                "invested_buy_usd": 0.0,
                "profit_usd": 0.0,
                "profit_krw": 0.0,
                "after_profit_usd": 0.0,
                "after_profit_krw": 0.0,
                "target_weight": 0.0,
                "symbols": []
            }
            non_cash_counts[cat] = 0

        c = category_summary[cat]
        c["eval_usd"] += item["eval_usd"]
        c["eval_krw"] += item["eval_krw"]
        c["after_eval_usd"] += item["after_eval_usd"]
        c["after_eval_krw"] += item["after_eval_krw"]
        c["buy_eval_usd"] += item["buy_eval_usd"]
        if not item["is_cash"]:
            c["invested_buy_usd"] += item["buy_eval_usd"]
            non_cash_counts[cat] += 1
        c["profit_usd"] += item["profit_usd"]
        c["profit_krw"] += item["profit_krw"]
        c["after_profit_usd"] += item["after_profit_usd"]
        c["after_profit_krw"] += item["after_profit_krw"]
        c["target_weight"] = round(c["target_weight"] + float(item["target_weight"]), 2)
        c["symbols"].append(item["symbol"])

    tier1_category_breaches = []
    after_breach_alerts = []

    for cat_name, cat_data in category_summary.items():
        cat_weight = (cat_data["eval_usd"] / total_eval_usd) * 100.0
        cat_after_weight = (cat_data["after_eval_usd"] / total_after_eval_usd) * 100.0 if total_after_eval_usd > 0 else cat_weight
        cat_target = round(cat_data["target_weight"], 2)
        if float(cat_target).is_integer():
            cat_target = int(cat_target)
        cat_data["target_weight"] = cat_target

        cat_diff = cat_weight - cat_target
        cat_after_diff = cat_after_weight - cat_target

        band = calc_band(
            cat_target,
            rules["category_abs_band_percent"],
            rules["category_rel_band_ratio"],
            rules["min_band_percent"]
        )
        watch_band = round(band * rules["watch_ratio"], 2)
        status = classify_weight_status(cat_diff, band, rules["watch_ratio"])
        after_breach = (band > 0) and (abs(cat_after_diff) >= band)

        rebalance_usd = total_eval_usd * (cat_target - cat_weight) / 100.0
        rebalance_krw = rebalance_usd * usd_krw_rate
        is_small_trade = abs(rebalance_usd) < rules["min_trade_usd"]

        # 투자 원금 기준 수익률 (예수금 제외 -> SGOV 단일 종목과 100% 일치)
        denom_buy = cat_data["invested_buy_usd"] if cat_data["invested_buy_usd"] > 0 else cat_data["buy_eval_usd"]
        cat_profit_pct = (cat_data["profit_usd"] / denom_buy * 100.0) if denom_buy > 0 else 0.0
        cat_after_profit_pct = (cat_data["after_profit_usd"] / denom_buy * 100.0) if denom_buy > 0 else 0.0
        cat_after_change_krw = cat_data["after_eval_krw"] - cat_data["eval_krw"]
        cat_after_change_pct = (cat_after_change_krw / cat_data["eval_krw"] * 100.0) if cat_data["eval_krw"] > 0 else 0.0

        cat_data.update({
            "profit_pct": cat_profit_pct,
            "after_profit_pct": cat_after_profit_pct,
            "after_change_krw": cat_after_change_krw,
            "after_change_pct": cat_after_change_pct,
            "current_weight": cat_weight,
            "weight_diff": cat_diff,
            "after_weight": cat_after_weight,
            "after_weight_diff": cat_after_diff,
            "band": band,
            "watch_band": watch_band,
            "lower_bound": round(max(0.0, cat_target - band), 1),
            "upper_bound": round(cat_target + band, 1),
            "status": status,
            "after_breach": after_breach,
            "rebalance_usd": round(rebalance_usd, 2),
            "rebalance_krw": round(rebalance_krw),
            "is_small_trade": is_small_trade,
            "non_cash_count": non_cash_counts[cat_name]
        })

        if status in ("BREACH_OVER", "BREACH_UNDER"):
            tier1_category_breaches.append({
                "name": cat_name,
                "status": status,
                "current_weight": round(cat_weight, 2),
                "target_weight": cat_target,
                "weight_diff": round(cat_diff, 2),
                "band": band,
                "rebalance_usd": round(rebalance_usd, 2),
                "rebalance_krw": round(rebalance_krw)
            })
        elif after_breach:
            after_breach_alerts.append({
                "level": "category",
                "name": cat_name,
                "after_weight_diff": round(cat_after_diff, 2),
                "band": band
            })

    # 4. 현금 카테고리 요약 변수
    cash_cat = category_summary.get(cash_category)
    cash_weight = cash_cat["current_weight"] if cash_cat else 0.0
    cash_target = cash_cat["target_weight"] if cash_cat else 0.0
    cash_diff = cash_cat["weight_diff"] if cash_cat else 0.0
    cash_band = cash_cat["band"] if cash_cat else 5.0
    cash_floor = float(rules["cash_floor_percent"])
    deployable_cash_pct = max(0.0, cash_weight - cash_floor)
    deployable_cash_usd = total_eval_usd * deployable_cash_pct / 100.0
    deployable_cash_krw = deployable_cash_usd * usd_krw_rate

    # 5. 개별 종목 비중, 3/20(또는 단일카테고리 5/25) 밴드, 엔진2 낙폭 감지, 2단계 액션 및 주수 계산
    tier2_stock_breaches = []
    drawdown_alerts = []

    for item in item_results:
        current_weight = (item["eval_usd"] / total_eval_usd) * 100.0
        after_weight = (item["after_eval_usd"] / total_after_eval_usd) * 100.0 if total_after_eval_usd > 0 else current_weight
        cat_info = category_summary[item["category"]]

        if item["is_cash"]:
            item.update({
                "current_weight": current_weight,
                "weight_diff": 0.0,
                "after_weight": after_weight,
                "after_weight_diff": 0.0,
                "band": 0.0,
                "watch_band": 0.0,
                "lower_bound": 0.0,
                "upper_bound": 0.0,
                "status": "CASH",
                "after_breach": False,
                "rebalance_usd": 0.0,
                "rebalance_krw": 0,
                "rebalance_shares": 0,
                "is_small_trade": True,
                "drawdown_signal": "NONE",
                "action_code": "CASH_POOL",
                "action_guide": f"초단기채/현금 카테고리 합산 관리 ({cash_weight:.1f}% / 목표 {cash_target}%)"
            })
            continue

        is_cash_cat_item = (item["category"] == cash_category)
        is_single_asset_cat = (non_cash_counts[item["category"]] == 1)

        if is_cash_cat_item:
            # SGOV는 현금 카테고리(SGOV + USD예수금) 합산 기준으로 이탈 판정하여 예수금 보유 시 오경보 방지
            weight_diff = cat_info["weight_diff"]
            after_weight_diff = cat_info["after_weight_diff"]
            band = cat_info["band"]
            watch_band = cat_info["watch_band"]
            status = cat_info["status"]
            after_breach = cat_info["after_breach"]
            rebalance_usd = cat_info["rebalance_usd"]
        else:
            weight_diff = current_weight - item["target_weight"]
            after_weight_diff = after_weight - item["target_weight"]
            if is_single_asset_cat:
                band = cat_info["band"]
            else:
                band = calc_band(
                    item["target_weight"],
                    rules["stock_abs_band_percent"],
                    rules["stock_rel_band_ratio"],
                    rules["min_band_percent"]
                )
            watch_band = round(band * rules["watch_ratio"], 2)
            status = classify_weight_status(weight_diff, band, rules["watch_ratio"])
            after_breach = (band > 0) and (abs(after_weight_diff) >= band)
            rebalance_usd = total_eval_usd * (item["target_weight"] - current_weight) / 100.0

        rebalance_krw = rebalance_usd * usd_krw_rate
        rebalance_shares = int(round(rebalance_usd / item["price_usd"])) if item["price_usd"] > 0 else 0
        is_small_trade = (abs(rebalance_usd) < rules["min_trade_usd"]) or (rebalance_shares == 0)

        # 엔진 2: 종목 가격 급락(Drawdown) 감지 (현금/초단기채 제외)
        drawdown_signal = "NONE"
        drawdown_reason = ""
        if not is_cash_cat_item:
            dd = item["drawdown_pct"]
            has_high = item.get("recent_high_usd") is not None and item["recent_high_usd"] > 0
            daily_chg = item["change_pct"]
            prof_pct = item["profit_pct"]
            reasons_dd = []
            if has_high and dd <= rules["drawdown_strong_percent"]:
                reasons_dd.append(f"고점 {dd:.1f}%")
            elif has_high and dd <= rules["drawdown_warn_percent"]:
                reasons_dd.append(f"고점 {dd:.1f}%")
            if daily_chg <= rules["daily_drop_warn_percent"]:
                reasons_dd.append(f"당일 {daily_chg:.1f}%")
            if prof_pct <= -15.0 and vix_val >= 25.0:
                reasons_dd.append(f"평단 {prof_pct:.1f}%·VIX {vix_val:.0f}")

            if (has_high and dd <= rules["drawdown_strong_percent"]) or (prof_pct <= -15.0 and vix_val >= 25.0):
                drawdown_signal = "STRONG_DIP"
            elif (has_high and dd <= rules["drawdown_warn_percent"]) or (daily_chg <= rules["daily_drop_warn_percent"]):
                drawdown_signal = "DIP"
            drawdown_reason = " · ".join(reasons_dd)

        # 2단계 액션 가이드 (현금 방어선 + 카테고리 비중 연동)
        cat_diff = cat_info["weight_diff"]
        if is_cash_cat_item:
            if cash_weight <= cash_floor:
                action_code = "CASH_FLOOR_ALERT"
                action_guide = f"현금 방어선({cash_floor:.0f}%) 도달 — 추가 매수 중단, 보충 우선"
            elif status == "BREACH_UNDER":
                action_code = "REFILL_CASH"
                action_guide = "현금 하한 이탈 — 초과 자산 익절 또는 적립금으로 보충"
            elif status == "BREACH_OVER":
                action_code = "DEPLOY_CASH"
                action_guide = "현금 상한 초과 — 미달 자산 분할 매수 여력 충분"
            else:
                action_code = "HOLD"
                action_guide = "현금 비중 정상 (단기 국채 이자 수취)"
        elif status == "BREACH_UNDER":
            if cash_weight <= cash_floor:
                action_code = "HOLD_CASH_FLOOR"
                action_guide = f"현금 방어선({cash_floor:.0f}%) 도달 — 현금 투입 중단, 교체/적립금 활용"
            elif cat_diff <= 0 or is_single_asset_cat:
                action_code = "BUY_WITH_CASH"
                action_guide = f"현금(SGOV) 분할 매수 검토 ({abs(rebalance_shares)}주, 펀더멘털 확인 후)"
            else:
                action_code = "SWAP_OR_NEW_CASH"
                action_guide = "카테고리 목표 초과 상태 — 현금 대신 내 초과 종목 교체/신규 적립금 배분"
        elif status == "BREACH_OVER":
            if is_small_trade:
                action_code = "DILUTE_WITH_NEW_CASH"
                action_guide = "소액 초과 — 매도 대신 신규 적립금/배당을 미달 자산에 투입 권장"
            elif cash_weight < cash_target:
                action_code = "SELL_TO_CASH"
                action_guide = f"초과분 {abs(rebalance_shares)}주 분할 익절 → SGOV(현금) 보충 검토"
            else:
                action_code = "SELL_TO_UNDERWEIGHT"
                action_guide = f"초과분 {abs(rebalance_shares)}주 분할 익절 → 미달 자산 이동 검토"
        elif drawdown_signal in ("DIP", "STRONG_DIP"):
            if cash_weight <= cash_floor:
                action_code = "DIP_CASH_FLOOR"
                action_guide = f"급락 감지 (단, 현금 방어선 {cash_floor:.0f}% 도달로 추격 매수 금지)"
            elif weight_diff <= 0 and cat_diff <= 0:
                action_code = "DIP_BUY_WATCH"
                action_guide = "급락 구간 감지 — 펀더멘털 이상 없으면 분할 매수 검토"
            elif weight_diff <= 0 and cat_diff > 0:
                action_code = "DIP_WAIT"
                action_guide = "급락 감지 (종목 미달이나 카테고리 목표 초과 — 현금 추격 주의, 교체/추가 급락 대기)"
            else:
                action_code = "DIP_WAIT"
                action_guide = "급락 감지 (이미 종목 목표 비중 초과 — 추격 매수 주의, 추가 급락 대기)"
        elif status in ("WATCH_OVER", "WATCH_UNDER"):
            action_code = "WATCH"
            action_guide = "허용 밴드 경계(80%) 근접 — 추이 관찰"
        else:
            action_code = "HOLD"
            action_guide = "목표 범위 내 정상 유지 (HOLD)"

        item.update({
            "current_weight": current_weight,
            "weight_diff": weight_diff,
            "after_weight": after_weight,
            "after_weight_diff": after_weight_diff,
            "band": band,
            "watch_band": watch_band,
            "lower_bound": round(max(0.0, item["target_weight"] - band), 1),
            "upper_bound": round(item["target_weight"] + band, 1),
            "status": status,
            "after_breach": after_breach,
            "rebalance_usd": round(rebalance_usd, 2),
            "rebalance_krw": round(rebalance_krw),
            "rebalance_shares": rebalance_shares,
            "is_small_trade": is_small_trade,
            "drawdown_signal": drawdown_signal,
            "drawdown_reason": drawdown_reason,
            "action_code": action_code,
            "action_guide": action_guide,
            "is_cash_category_member": is_cash_cat_item
        })

        # Tier 2는 다중 종목 카테고리의 개별 종목 이탈을 별도 추적 (단일 카테고리는 Tier 1에 이미 포함)
        if (not is_single_asset_cat) and (not is_cash_cat_item) and status in ("BREACH_OVER", "BREACH_UNDER"):
            tier2_stock_breaches.append({
                "symbol": item["symbol"],
                "name": item["name"],
                "category": item["category"],
                "status": status,
                "current_weight": round(current_weight, 2),
                "target_weight": item["target_weight"],
                "weight_diff": round(weight_diff, 2),
                "band": band,
                "rebalance_usd": round(rebalance_usd, 2),
                "rebalance_krw": round(rebalance_krw),
                "rebalance_shares": rebalance_shares,
                "action_code": action_code,
                "action_guide": action_guide
            })
        elif (not is_single_asset_cat) and (not is_cash_cat_item) and after_breach:
            after_breach_alerts.append({
                "level": "stock",
                "name": f"{item['name']}({item['symbol']})",
                "after_weight_diff": round(after_weight_diff, 2),
                "band": band
            })

        if drawdown_signal in ("DIP", "STRONG_DIP"):
            drawdown_alerts.append({
                "symbol": item["symbol"],
                "name": item["name"],
                "signal": drawdown_signal,
                "reason": drawdown_reason,
                "drawdown_pct": item["drawdown_pct"],
                "change_pct": round(item["change_pct"], 2),
                "profit_pct": round(item["profit_pct"], 2),
                "weight_diff": round(weight_diff, 2),
                "action_code": action_code,
                "action_guide": action_guide
            })

    # 6. 현금 실탄(SGOV + 예수금) × VIX 통합 의사결정 매트릭스 (모순 0%)
    risk_cat_under = [
        b for b in tier1_category_breaches
        if b["name"] != cash_category and b["status"] == "BREACH_UNDER"
    ]
    cash_reasons = []
    if cash_weight <= cash_floor:
        cash_signal = "CASH_FLOOR_ALERT"
        cash_title = "🛑 [현금 방어선 경고] 추가 실탄 투입 중단!"
        cash_reasons.append(
            f"현금 카테고리 비중이 {cash_weight:.1f}%로 최소 방어선({cash_floor:.0f}%) 이하입니다. "
            f"VIX({vix_val:.2f})와 무관하게 신규 매수를 중단하고 반등·초과 자산 익절 또는 입금으로 현금 {cash_floor:.0f}% 이상 회복을 우선하세요."
        )
    elif cash_diff <= -cash_band and vix_val < 30.0:
        cash_signal = "CASH_LOW"
        cash_title = "⚠️ [현금 비중 부족] 실탄 보충 권고"
        cash_reasons.append(
            f"현금 카테고리 비중이 {cash_weight:.1f}% (목표 {cash_target}% 대비 {cash_diff:+.1f}%p, 하한 밴드 -{cash_band:.1f}%p 이탈). "
            f"초과 자산 분할 익절 또는 신규 적립금으로 SGOV를 보충하세요."
        )
    elif vix_val >= 30.0 and cash_weight > cash_floor:
        cash_signal = "STRONG_BUY"
        cash_title = "🔥 [강력 분할 매수] 패닉 장세 실탄 집행 검토!"
        tranche_min = deployable_cash_usd * 0.30
        tranche_max = deployable_cash_usd * 0.50
        cash_reasons.append(
            f"VIX {vix_val:.2f} (30 이상 패닉 구간). 최소 방어선({cash_floor:.0f}%) 초과 가용 실탄 "
            f"${deployable_cash_usd:,.0f} (약 {deployable_cash_krw:,.0f}원 / {deployable_cash_pct:.1f}%p) 중 "
            f"30~50%(${tranche_min:,.0f}~${tranche_max:,.0f}) 분할 투입 검토 (목표 미달·급락 우량주 우선, 펀더멘털 확인 필수)."
        )
        if risk_cat_under:
            names = ", ".join(b["name"] for b in risk_cat_under)
            cash_reasons.append(f"하한 이탈 카테고리 동시 발생: {names}")
    elif (cash_diff >= cash_band) or bool(risk_cat_under):
        cash_signal = "REBALANCE_BUY"
        cash_title = "🛒 [비중 이탈 리밸런싱 매수] 미달 자산 매수 검토"
        if cash_diff >= cash_band:
            cash_reasons.append(
                f"현금 카테고리 비중 {cash_weight:.1f}% (목표 대비 +{cash_diff:.1f}%p, 상한 +{cash_band:.1f}%p 초과). "
                f"초과 현금 약 ${abs(cash_cat['rebalance_usd']):,.0f}로 미달 자산 저가 매수 검토."
            )
        for b in risk_cat_under:
            cash_reasons.append(
                f"{b['name']} 하한 이탈 ({b['current_weight']:.1f}% / 목표 {b['target_weight']}%, "
                f"필요 매수액 약 ${b['rebalance_usd']:,.0f})"
            )
    elif vix_val >= 25.0:
        if cash_weight >= cash_target:
            cash_signal = "VIX_BUY"
            cash_title = "🛒 [1차 전술적 분할 매수] VIX 25+ 투매 경계"
            tranche_min = deployable_cash_usd * 0.20
            tranche_max = deployable_cash_usd * 0.30
            cash_reasons.append(
                f"VIX {vix_val:.2f} (25 이상 경계) & 현금 충분({cash_weight:.1f}%). "
                f"가용 실탄(${deployable_cash_usd:,.0f})의 20~30%(${tranche_min:,.0f}~${tranche_max:,.0f}) 이내 1차 분할 매수 검토."
            )
        else:
            cash_signal = "DEPLOYED_WAIT"
            cash_title = "🟡 [선제 집행 중 — 신중 관망]"
            cash_reasons.append(
                f"VIX {vix_val:.2f}이나 현금이 이미 목표({cash_target}%) 대비 {cash_diff:+.1f}%p 선제 투입된 상태입니다 "
                f"(현재 {cash_weight:.1f}%, 방어선 {cash_floor:.0f}%까지 잔여 여력 {deployable_cash_pct:.1f}%p / 약 ${deployable_cash_usd:,.0f})."
            )
            cash_reasons.append(
                "추격 매수는 자제하고, VIX 30+ 패닉 또는 카테고리 하한 이탈(-5%p) 도달 시 2차 집행을 권장합니다."
            )
    else:
        cash_signal = "HOLD"
        cash_title = "🛡️ [실탄 대기 (HOLD)]"
        cash_reasons.append(
            f"현금 비중({cash_weight:.1f}% / 목표 {cash_target}%)과 시장 변동성(VIX {vix_val:.2f})이 안정적입니다. "
            f"SGOV 보유로 단기 국채 이자를 수취하며 기회를 대기하세요."
        )

    cash_guidance = {
        "signal": cash_signal,
        "title": cash_title,
        "reasons": cash_reasons,
        "cash_category": cash_category,
        "cash_weight": round(cash_weight, 2),
        "cash_target": cash_target,
        "cash_diff": round(cash_diff, 2),
        "cash_band": cash_band,
        "cash_floor": cash_floor,
        "deployable_cash_pct": round(deployable_cash_pct, 2),
        "deployable_cash_usd": round(deployable_cash_usd, 2),
        "deployable_cash_krw": round(deployable_cash_krw),
        "vix_val": round(vix_val, 2)
    }

    rebalance_summary = {
        "rebalance_needed": bool(tier1_category_breaches or tier2_stock_breaches),
        "tier1_category_breaches": tier1_category_breaches,
        "tier2_stock_breaches": tier2_stock_breaches,
        "drawdown_alerts": drawdown_alerts,
        "after_breach_alerts": after_breach_alerts,
        "target_weight_sum": target_weight_sum,
        "target_weight_valid": target_weight_valid
    }

    return {
        "usd_krw_rate": usd_krw_rate,
        "tnx": tnx,
        "vix": vix,
        "dxy": dxy,
        "rebalance_rules": rules,
        "cash_usd": total_cash_usd,
        "target_weight_sum": target_weight_sum,
        "target_weight_valid": target_weight_valid,
        "total_eval_usd": total_eval_usd,
        "total_eval_krw": total_eval_krw,
        "total_buy_usd": total_buy_usd,
        "total_buy_krw": total_buy_krw,
        "total_invested_buy_usd": total_invested_buy_usd,
        "total_invested_buy_krw": total_invested_buy_krw,
        "total_profit_usd": total_profit_usd,
        "total_profit_krw": total_profit_krw,
        "total_profit_pct": total_profit_pct,
        "total_after_eval_usd": total_after_eval_usd,
        "total_after_eval_krw": total_after_eval_krw,
        "total_after_profit_usd": total_after_profit_usd,
        "total_after_profit_krw": total_after_profit_krw,
        "total_after_profit_pct": total_after_profit_pct,
        "items": item_results,
        "categories": category_summary,
        "cash_guidance": cash_guidance,
        "rebalance_summary": rebalance_summary
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


def export_portfolio_json(eval_result, json_path="portfolio_data.json"):
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

        history = [h for h in existing_history if h.get("date") != today_date]
        history.append({
            "date": today_date,
            "total_eval_krw": round(eval_result["total_eval_krw"]),
            "total_after_eval_krw": round(eval_result["total_after_eval_krw"]),
            "total_buy_krw": round(eval_result["total_buy_krw"]),
            "total_profit_krw": round(eval_result["total_profit_krw"]),
            "total_profit_pct": round(eval_result["total_profit_pct"], 2),
            "total_after_profit_krw": round(eval_result["total_after_profit_krw"]),
            "total_after_profit_pct": round(eval_result["total_after_profit_pct"], 2),
            "usd_krw_rate": eval_result["usd_krw_rate"]
        })
        history.sort(key=lambda x: x["date"])

        data = {
            "updated_at": kst_now.strftime("%Y-%m-%d %H:%M"),
            **eval_result,
            "history": history
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("portfolio_data.json 계산 파일 및 히스토리 기록 완료!")
        return data
    except Exception as e:
        print(f"JSON 내보내기 실패: {e}")
        return None


def format_status_label(status, weight_diff, band):
    if status == "CASH":
        return "💵예수금"
    if status == "BREACH_OVER":
        return f"🔴초과({weight_diff:+.1f}%p / ±{band:.1f})"
    if status == "BREACH_UNDER":
        return f"🔴미달({weight_diff:+.1f}%p / ±{band:.1f})"
    if status == "WATCH_OVER":
        return f"🟡주의({weight_diff:+.1f}%p / ±{band:.1f})"
    if status == "WATCH_UNDER":
        return f"🟡주의({weight_diff:+.1f}%p / ±{band:.1f})"
    return f"🟢정상(±{band:.1f}%p)"


def format_trade_suggestion(rebalance_usd, rebalance_krw, rebalance_shares=None, is_small_trade=False):
    if is_small_trade:
        return "유지 (소액 생략)"
    side = "매수" if rebalance_usd > 0 else "매도"
    sign = "+" if rebalance_usd > 0 else "-"
    if rebalance_shares is not None and rebalance_shares != 0:
        return f"{sign}{abs(rebalance_shares)}주 {side} (약 ${abs(rebalance_usd):,.0f} / {abs(rebalance_krw):,.0f}원)"
    return f"{side} 약 ${abs(rebalance_usd):,.0f} ({abs(rebalance_krw):,.0f}원)"


def build_telegram_report(eval_result, rate_detail, macro_news, portfolio_news, now_str):
    usd_krw_rate = eval_result["usd_krw_rate"]
    tnx = eval_result["tnx"]
    vix = eval_result["vix"]
    dxy = eval_result["dxy"]
    total_eval_krw = eval_result["total_eval_krw"]
    total_profit_krw = eval_result["total_profit_krw"]
    total_profit_pct = eval_result["total_profit_pct"]
    total_after_eval_krw = eval_result["total_after_eval_krw"]
    total_after_profit_krw = eval_result["total_after_profit_krw"]
    total_after_profit_pct = eval_result["total_after_profit_pct"]
    total_after_diff_pct = ((total_after_eval_krw - total_eval_krw) / total_eval_krw * 100.0) if total_eval_krw > 0 else 0.0

    rate_change_str = f"({rate_detail['change_pct']:+.2f}%)" if rate_detail.get('change_pct', 0) != 0 else ""
    profit_sign = "+" if total_profit_krw >= 0 else ""
    after_profit_sign = "+" if total_after_profit_krw >= 0 else ""
    divider = "━━━━━━━━━━━━━━━━━━"

    lines = [
        "📊 *[포트폴리오 & 매크로 리포트]*",
        f"⏰ {now_str} KST",
        f"💵 환율: 1$ = {usd_krw_rate:,.1f}원 {rate_change_str}",
        "",
        "💰 *[총 자산 요약]* (USD 원금 기준)",
        f"☀️ *정규 총액: {total_eval_krw:,.0f}원* (${eval_result['total_eval_usd']:,.2f})",
        f"  └ 손익: *{profit_sign}{total_profit_krw:,.0f}원 ({total_profit_pct:+.2f}%)*",
        f"🌙 *애프터 총액: {total_after_eval_krw:,.0f}원* ({total_after_diff_pct:+.2f}%)",
        f"  └ 손익: *{after_profit_sign}{total_after_profit_krw:,.0f}원 ({total_after_profit_pct:+.2f}%)*",
        divider
    ]

    if not eval_result["target_weight_valid"]:
        lines.append(f"⚠️ *[설정 경고]* 목표 비중 합계가 {eval_result['target_weight_sum']}%입니다 (100% 확인 필요)!")
        lines.append(divider)

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

    if vix and vix['price'] >= 30:
        lines.append("💡 *진단*: VIX 30+ 패닉 장세! 방어선(15%) 내 분할 매수 검토")
    elif vix and vix['price'] >= 25:
        lines.append("💡 *진단*: VIX 25+ 변동성 급등! 현금 여력·비중 확인 후 신중 접근")
    elif tnx and tnx['price'] >= 4.5:
        lines.append("💡 *진단*: 10년물 4.5% 상회 중. 기술주 분할 접근 및 바벨 유지 권장")
    else:
        lines.append("💡 *진단*: 변동성 및 금리가 안정적 흐름 유지 중")

    lines.append(divider)

    # 3. 실시간 주요 뉴스
    if macro_news or portfolio_news:
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

    # 4. 카테고리별 자산군 비중 요약 (5/25 룰)
    lines.append("📂 *[카테고리별 비중 & 5/25 밴드]*")
    for cat_name, cat_data in eval_result["categories"].items():
        cat_status = format_status_label(cat_data["status"], cat_data["weight_diff"], cat_data["band"])
        after_note = " | 🌙장후이탈" if cat_data["after_breach"] else ""
        sym_list = ", ".join(cat_data["symbols"])
        trade_str = format_trade_suggestion(cat_data["rebalance_usd"], cat_data["rebalance_krw"], None, cat_data["is_small_trade"])
        lines.append(
            f"\n*{cat_name}* [{cat_status}{after_note}]\n"
            f"  └ 구성: {sym_list}\n"
            f"  • ☀️정규: {cat_data['eval_krw']:,.0f}원 (*{cat_data['profit_pct']:+.1f}%*)\n"
            f"  • 🌙애프터: {cat_data['after_eval_krw']:,.0f}원 ({cat_data['after_change_pct']:+.2f}%)\n"
            f"  • 📊비중: 정규 *{cat_data['current_weight']:.1f}%* ➡️ 🌙 *{cat_data['after_weight']:.1f}%* (목표 {cat_data['target_weight']}%)\n"
            f"  • ⚖️목표복귀: {trade_str}"
        )

    lines.append(divider)

    # 5. 개별 종목 현황 (3/20 룰 + 엔진2 낙폭 감지 + 주수 계산기)
    lines.append("📈 *[개별 종목 시세 · 3/20 밴드 · 낙폭]*")
    for item in eval_result["items"]:
        if item["is_cash"]:
            lines.append(
                f"\n💵 *{item['name']}*\n"
                f"  • 💰잔액: *${item['eval_usd']:,.2f}* ({item['eval_krw']:,.0f}원) | 비중 *{item['current_weight']:.1f}%* (현금 카테고리 합산)"
            )
            continue

        status_icon = "🔴" if item["status"].startswith("BREACH") else ("🟡" if item["status"].startswith("WATCH") else "🟢")
        dd_badge = ""
        dd_reason = item.get("drawdown_reason") or f"고점 {item['drawdown_pct']:.1f}%"
        if item["drawdown_signal"] == "STRONG_DIP":
            dd_badge = f" 🔥투매({dd_reason})"
        elif item["drawdown_signal"] == "DIP":
            dd_badge = f" 📉급락({dd_reason})"

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

        trade_str = format_trade_suggestion(
            item["rebalance_usd"], item["rebalance_krw"], item["rebalance_shares"], item["is_small_trade"]
        )

        if item["market"] == "US":
            lines.append(
                f"\n{status_icon} *{item['name']} ({item['symbol']})*{dd_badge}\n"
                f"  • ☀️정규가: *${item['price_usd']:,.2f}* ({change_str}) | {item['quantity']}주\n"
                f"  • 🌙애프터: *${item['after_price_usd']:,.2f}* ({after_change_str})\n"
                f"  • 💰평가액: {item['eval_krw']:,.0f}원 | 🏆수익률: *{item['profit_pct']:+.2f}%* (${item['profit_usd']:+,.0f})\n"
                f"  • 📊비중: *{item['current_weight']:.1f}%* (목표 {item['target_weight']}%, 밴드 ±{item['band']:.1f}%p)\n"
                f"  • ⚖️조정: {trade_str}"
            )
        else:
            lines.append(
                f"\n{status_icon} *{item['name']} ({item['symbol']})*{dd_badge}\n"
                f"  • ☀️현재가: *{item['price_krw']:,.0f}원* ({change_str}) | {item['quantity']}주\n"
                f"  • 💰평가액: {item['eval_krw']:,.0f}원 | 🏆수익률: *{item['profit_pct']:+.2f}%*\n"
                f"  • 📊비중: *{item['current_weight']:.1f}%* (목표 {item['target_weight']}%, 밴드 ±{item['band']:.1f}%p)\n"
                f"  • ⚖️조정: {trade_str}"
            )

    lines.append(divider)

    # 6. 현금 실탄 (SGOV + 예수금) × VIX 통합 매트릭스
    cg = eval_result["cash_guidance"]
    lines.append("💳 *[현금 실탄(SGOV+예수금) × VIX 진단]*")
    lines.append(f"*{cg['title']}*")
    lines.append(
        f"• 현금 비중: *{cg['cash_weight']:.1f}%* (목표 {cg['cash_target']}% | 방어선 {cg['cash_floor']:.0f}% | 가용 여력 {cg['deployable_cash_pct']:.1f}%p)"
    )
    for r in cg["reasons"]:
        lines.append(f"• {r}")

    lines.append(divider)

    # 7. 종합 대응 가이드 (Tier 1 카테고리 → Tier 2 종목 → 엔진2 급락)
    rs = eval_result["rebalance_summary"]
    lines.append("🎯 *[종합 대응 가이드 (2단계 + 급락 감지)]*")
    if rs["tier1_category_breaches"]:
        lines.append("🔔 *[1단계: 카테고리 자산배분 이탈]*")
        for b in rs["tier1_category_breaches"]:
            side = "초과(익절 검토)" if b["status"] == "BREACH_OVER" else "미달(매수 검토)"
            lines.append(
                f"• *{b['name']}*: {b['current_weight']:.1f}% (목표 {b['target_weight']}%, {b['weight_diff']:+.1f}%p / ±{b['band']:.1f}%p) → {side} 약 ${abs(b['rebalance_usd']):,.0f}"
            )
    if rs["tier2_stock_breaches"]:
        lines.append("📌 *[2단계: 개별 종목 비중 이탈 (3/20)]*")
        for b in rs["tier2_stock_breaches"]:
            lines.append(
                f"• *{b['name']}({b['symbol']})*: {b['current_weight']:.1f}% ({b['weight_diff']:+.1f}%p / ±{b['band']:.1f}%p) → {b['action_guide']}"
            )
    if rs["drawdown_alerts"]:
        lines.append("📉 *[엔진2: 종목 가격 급락 감지]*")
        for d in rs["drawdown_alerts"]:
            dd_desc = d.get("reason") or f"고점대비 {d['drawdown_pct']:.1f}%, 당일 {d['change_pct']:+.1f}%"
            lines.append(
                f"• *{d['name']}({d['symbol']})*: {dd_desc} → {d['action_guide']}"
            )
    if rs["after_breach_alerts"]:
        after_list = ", ".join(f"{a['name']}({a['after_weight_diff']:+.1f}%p)" for a in rs["after_breach_alerts"])
        lines.append(f"🌙 *[장후 예비 경보]* 정규장은 정상이나 애프터마켓 이탈: {after_list}")

    if not rs["tier1_category_breaches"] and not rs["tier2_stock_breaches"] and not rs["drawdown_alerts"]:
        lines.append(
            "✅ *포트폴리오 균형 유지 중*\n"
            "• 전 카테고리(5/25) 및 개별 종목(3/20) 허용 범위 내 정상 유지\n"
            "👉 별도 매매 없이 현재 포지션 유지 (Hold)"
        )

    lines.append(divider)
    lines.append("📱 *[실시간 대시보드 웹페이지]*")
    lines.append("👉 [대시보드 열기 (차트 & 상세 표)](https://hoohoon.github.io/my-stock-portfolio/)")

    return "\n".join(lines)


def main():
    config_path = os.path.join(os.path.dirname(__file__), "portfolio_config.json")
    if not os.path.exists(config_path):
        print("portfolio_config.json 파일을 찾을 수 없습니다.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    portfolio = config.get("portfolio", [])
    rules = get_rebalance_rules(config)
    cash_category = config.get("cash_category", "💵 초단기채/현금")

    rate_detail = get_krw_usd_rate_detail()
    usd_krw_rate = rate_detail["price"]
    tnx, vix, dxy = get_macro_indicators_detail()
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    now_str = kst_now.strftime("%Y-%m-%d %H:%M")

    market_quotes = {}
    for item in portfolio:
        name = item["name"]
        symbol = item["symbol"]
        market = item.get("market", "KRX").upper()
        is_cash_cat = (item.get("category") == cash_category)
        try:
            if market == "US":
                detail = fetch_yahoo_market_detail(
                    symbol,
                    include_recent_high=(not is_cash_cat),
                    high_range=rules.get("drawdown_lookback_range", "3mo")
                )
                if not detail:
                    raise ValueError("가격 조회 불가")
                market_quotes[symbol] = detail
            else:
                market_quotes[symbol] = get_krx_stock_detail(symbol)
        except Exception as e:
            print(f"[{name}({symbol})] 시세 조회 실패: {e}")

    try:
        eval_result = evaluate_portfolio(config, market_quotes, usd_krw_rate, tnx, vix, dxy)
    except Exception as e:
        print(f"포트폴리오 계산 실패: {e}")
        return

    # 실시간 한국어 뉴스 수집
    macro_news = get_google_news("Federal Reserve interest rate inflation market", max_count=2)
    portfolio_news = get_google_news("NVIDIA OR Alphabet OR AMD OR Microsoft stock market", max_count=2)

    msg_text = build_telegram_report(eval_result, rate_detail, macro_news, portfolio_news, now_str)

    try:
        print(msg_text)
    except Exception:
        print("포트폴리오 리포트 생성 완료 (콘솔 출력 중 인코딩 차이 무시)")

    export_portfolio_json(eval_result, json_path=os.path.join(os.path.dirname(__file__), "portfolio_data.json"))

    # 텔레그램 텍스트 전송
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if telegram_token and telegram_chat_id:
        send_telegram_message(telegram_token, telegram_chat_id, msg_text)
    else:
        print("\n[안내] TELEGRAM_TOKEN 및 TELEGRAM_CHAT_ID 환경변수가 설정되지 않아 콘솔 출력만 진행했습니다.")


if __name__ == "__main__":
    main()
