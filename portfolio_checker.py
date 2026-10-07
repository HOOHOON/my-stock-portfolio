import os
import sys
import json
import urllib.request
import datetime

# 윈도우 콘솔 출력 한글 및 이모지 깨짐 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def get_krw_usd_rate():
    """환율 (USD/KRW) 조회 (야후 파이낸스)"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/KRW=X"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode('utf-8'))
            return float(data['chart']['result'][0]['meta']['regularMarketPrice'])
    except Exception as e:
        print(f"환율 조회 실패 (기본값 1,350원 적용): {e}")
    return 1350.0

def get_krx_stock_price(symbol):
    """한국 주식 실시간 가격 조회 (네이버 금융 API)"""
    url = f"https://m.stock.naver.com/api/stock/{symbol}/integration"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode('utf-8'))
        price_str = data['totalInfos'][0]['value'].replace(',', '')
        return float(price_str)

def get_us_stock_price(symbol):
    """미국 주식 실시간 가격 조회 (야후 파이낸스 API)"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode('utf-8'))
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        return float(price)

def send_telegram_message(token, chat_id, text):
    """텔레그램 메시지 전송"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as res:
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

    usd_krw_rate = get_krw_usd_rate()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    total_eval_krw = 0
    item_results = []

    for item in portfolio:
        name = item["name"]
        symbol = item["symbol"]
        market = item.get("market", "KRX").upper()
        quantity = item["quantity"]
        target_weight = item["target_weight"]

        try:
            if market == "US":
                price_usd = get_us_stock_price(symbol)
                price_krw = price_usd * usd_krw_rate
            else:
                price_krw = get_krx_stock_price(symbol)
                price_usd = price_krw / usd_krw_rate

            eval_krw = price_krw * quantity
            total_eval_krw += eval_krw

            item_results.append({
                "name": name,
                "symbol": symbol,
                "market": market,
                "quantity": quantity,
                "target_weight": target_weight,
                "price_krw": price_krw,
                "price_usd": price_usd,
                "eval_krw": eval_krw
            })
        except Exception as e:
            print(f"[{name}({symbol})] 시세 조회 실패: {e}")

    if total_eval_krw == 0:
        print("포트폴리오 평가금액을 계산할 수 없습니다.")
        return

    # 비중 계산 및 메시지 작성
    rebalance_needed = False
    lines = [
        "📊 *[실시간 주식 포트폴리오 비중 리포트]*",
        f"⏰ 기준시각: {now_str} KST",
        f"💵 적용 환율: $1 = {usd_krw_rate:,.1f}원",
        f"💰 *총 평가금액: {total_eval_krw:,.0f}원*",
        "----------------------------------------"
    ]

    for item in item_results:
        current_weight = (item["eval_krw"] / total_eval_krw) * 100
        weight_diff = current_weight - item["target_weight"]
        
        status_icon = "🟢"
        status_text = "정상"

        if abs(weight_diff) >= threshold:
            rebalance_needed = True
            if weight_diff > 0:
                status_icon = "⚠️"
                status_text = f"비중 초과 (+{weight_diff:.1f}%)"
            else:
                status_icon = "⚠️"
                status_text = f"비중 미달 ({weight_diff:.1f}%)"

        if item["market"] == "US":
            price_disp = f"${item['price_usd']:,.2f} ({item['price_krw']:,.0f}원)"
        else:
            price_disp = f"{item['price_krw']:,.0f}원"

        lines.append(
            f"{status_icon} *{item['name']}* ({item['symbol']})\n"
            f"  • 현재가: {price_disp}\n"
            f"  • 평가금액: {item['eval_krw']:,.0f}원\n"
            f"  • 현재비중: *{current_weight:.1f}%* (목표 {item['target_weight']:.1f}%)\n"
            f"  • 상태: {status_text}\n"
        )

    lines.append("----------------------------------------")
    if rebalance_needed:
        lines.append("🔔 *[알림] 목표 비중 이탈 종목이 있습니다. 리밸런싱을 검토하세요!*")
    else:
        lines.append("✅ 모든 종목의 비중이 목표범위 내에서 안정적입니다.")

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
