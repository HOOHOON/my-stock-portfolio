"""
포트폴리오 비중 & 리밸런싱 주수 계산기 (portfolio_config.json 자동 연동)

사용법:
  1) 실시간 시세 기준 계산:
       python calc_weights.py
  2) 오프라인(최근 저장된 portfolio_data.json 시세) 기준 즉시 계산:
       python calc_weights.py --offline
  3) 매수/매도 시뮬레이션 (실제 파일 수정 없이 매매 후 비중·이탈 미리보기):
       python calc_weights.py --offline --sim NVDA:+5 --sim SGOV:-9
       python calc_weights.py --offline --sim AMD:+3 --cash-delta -1200
"""

import argparse
import copy
import json
import os
import sys
import portfolio_checker as pc

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_cached_quotes(data_path):
    """portfolio_data.json에서 마지막 저장된 시세·환율·매크로 로드"""
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    quotes = {}
    for item in data.get("items", []):
        if item.get("is_cash") or item.get("symbol") == "USD":
            continue
        quotes[item["symbol"]] = {
            "price": item["price_usd"],
            "change": item.get("change_usd", 0.0),
            "change_pct": item.get("change_pct", 0.0),
            "after_price": item.get("after_price_usd", item["price_usd"]),
            "after_change": item.get("after_change_usd", 0.0),
            "after_change_pct": item.get("after_change_pct", 0.0),
            "has_after": item.get("has_after", False),
            "recent_high": item.get("recent_high_usd")
        }
    return quotes, float(data.get("usd_krw_rate", 1350.0)), data.get("tnx"), data.get("vix"), data.get("dxy"), data.get("updated_at")


def apply_simulations(config, quotes, sim_args, cash_delta):
    """--sim SYMBOL:+/-QTY 옵션을 복사본 config에 적용 (원본 파일 미변경)"""
    sim_cfg = copy.deepcopy(config)
    cash_usd = float(sim_cfg.get("cash_usd", 0.0)) + float(cash_delta)
    sim_notes = []

    for expr in sim_args or []:
        if ":" not in expr:
            raise ValueError(f"잘못된 시뮬레이션 형식: '{expr}' (예: NVDA:+5 또는 SGOV:-10)")
        sym, qty_str = expr.split(":", 1)
        sym = sym.strip().upper()
        delta_qty = float(qty_str.strip())

        matched = None
        for item in sim_cfg.get("portfolio", []):
            if item["symbol"].upper() == sym:
                matched = item
                break
        if not matched:
            raise ValueError(f"포트폴리오에 없는 종목입니다: {sym}")

        new_qty = matched["quantity"] + delta_qty
        if new_qty < 0:
            raise ValueError(f"{sym} 매도 수량이 보유 수량({matched['quantity']})을 초과합니다.")

        price_usd = quotes[sym]["price"] if sym in quotes else matched.get("buy_price", 0.0)
        trade_usd = delta_qty * price_usd

        # 평단가 업데이트 (매수 시 가중평균, 매도 시 평단 유지)
        if delta_qty > 0 and new_qty > 0:
            old_cost = matched["quantity"] * matched.get("buy_price", price_usd)
            matched["buy_price"] = round((old_cost + trade_usd) / new_qty, 4)

        matched["quantity"] = int(new_qty) if float(new_qty).is_integer() else new_qty
        cash_usd -= trade_usd
        side = "매수" if delta_qty > 0 else "매도"
        sim_notes.append(f"{sym} {abs(delta_qty):g}주 {side} (@${price_usd:,.2f}, ${abs(trade_usd):,.2f})")

    if cash_usd < -0.01:
        sim_notes.append(f"⚠️ 시뮬레이션 후 예수금 부족: ${cash_usd:,.2f} (추가 입금 또는 SGOV 매도 필요)")
    sim_cfg["cash_usd"] = round(max(0.0, cash_usd), 2)
    return sim_cfg, sim_notes


def print_report(result, source_label, sim_notes=None):
    rate = result["usd_krw_rate"]
    print("=" * 72)
    print(f"📊 포트폴리오 비중 & 리밸런싱 계산기 ({source_label})")
    print(f"💵 기준 환율: 1$ = {rate:,.2f}원 | 총 평가액: ${result['total_eval_usd']:,.2f} ({result['total_eval_krw']:,.0f}원)")
    print(f"🏆 총 평가손익 (USD 원금 기준): ${result['total_profit_usd']:+,.2f} ({result['total_profit_krw']:+,.0f}원 / {result['total_profit_pct']:+.2f}%)")
    if not result["target_weight_valid"]:
        print(f"⚠️ 경고: 목표 비중 합계가 {result['target_weight_sum']}%입니다! (100% 확인 필요)")
    if sim_notes:
        print("🧪 [매매 시뮬레이션 적용]: " + " | ".join(sim_notes))
    print("=" * 72)

    print("\n[1] 카테고리별 비중 & 5/25 허용 밴드")
    for cat_name, c in result["categories"].items():
        status = pc.format_status_label(c["status"], c["weight_diff"], c["band"])
        trade = pc.format_trade_suggestion(c["rebalance_usd"], c["rebalance_krw"], None, c["is_small_trade"])
        print(
            f"• {cat_name:16s} | 현재 {c['current_weight']:5.1f}% (목표 {c['target_weight']:2d}%, 차이 {c['weight_diff']:+5.1f}%p) "
            f"| [{status}] | 목표복귀: {trade}"
        )

    print("\n[2] 개별 종목 비중 · 3/20 밴드 · 낙폭(엔진2) · 필요 주수")
    for it in result["items"]:
        if it["is_cash"]:
            print(
                f"• {it['name']:18s} | 잔액 ${it['eval_usd']:9,.2f} ({it['eval_krw']:10,.0f}원) "
                f"| 비중 {it['current_weight']:5.1f}% (현금 카테고리 합산)"
            )
            continue
        status = pc.format_status_label(it["status"], it["weight_diff"], it["band"])
        trade = pc.format_trade_suggestion(it["rebalance_usd"], it["rebalance_krw"], it["rebalance_shares"], it["is_small_trade"])
        dd_reason = it.get("drawdown_reason") or f"고점 {it['drawdown_pct']:.1f}%"
        dd_icon = "🔥투매" if it["drawdown_signal"] == "STRONG_DIP" else "📉급락"
        dd_str = f" | {dd_icon}({dd_reason})" if it["drawdown_signal"] != "NONE" else ""
        print(
            f"• {it['symbol']:5s} ({it['quantity']:3g}주 @${it['price_usd']:7.2f}) | "
            f"비중 {it['current_weight']:5.1f}% (목표 {it['target_weight']:2d}%, {it['weight_diff']:+5.1f}%p) | "
            f"[{status}]{dd_str}"
        )
        print(f"    └ 평가: ${it['eval_usd']:9,.2f} ({it['eval_krw']:10,.0f}원) | 수익률 {it['profit_pct']:+6.2f}% | 조정: {trade}")
        if it["action_code"] not in ("HOLD", "CASH_POOL"):
            print(f"    └ 가이드: {it['action_guide']}")

    cg = result["cash_guidance"]
    print("\n[3] 현금 실탄(SGOV+예수금) × VIX 통합 판단")
    print(f"• {cg['title']}")
    print(
        f"  현금 비중 {cg['cash_weight']:.1f}% (목표 {cg['cash_target']}% | 방어선 {cg['cash_floor']:.0f}% | "
        f"가용 실탄 {cg['deployable_cash_pct']:.1f}%p ≈ ${cg['deployable_cash_usd']:,.0f})"
    )
    for r in cg["reasons"]:
        print(f"  - {r}")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="포트폴리오 비중 & 리밸런싱 주수 계산기")
    parser.add_argument("--offline", action="store_true", help="실시간 조회 대신 portfolio_data.json 최신 시세 사용")
    parser.add_argument("--sim", action="append", help="매매 시뮬레이션 (예: --sim NVDA:+5 --sim SGOV:-9)")
    parser.add_argument("--cash-delta", type=float, default=0.0, help="추가 입금(+)/출금(-) USD 시뮬레이션")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "portfolio_config.json")
    data_path = os.path.join(base_dir, "portfolio_data.json")

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    quotes = {}
    source_label = ""
    if not args.offline:
        try:
            rate = pc.get_krw_usd_rate_detail()["price"]
            tnx, vix, dxy = pc.get_macro_indicators_detail()
            rules = pc.get_rebalance_rules(config)
            cash_cat = config.get("cash_category", "💵 초단기채/현금")
            for item in config.get("portfolio", []):
                sym = item["symbol"]
                if item.get("market", "US").upper() == "US":
                    d = pc.fetch_yahoo_market_detail(
                        sym,
                        include_recent_high=(item.get("category") != cash_cat),
                        high_range=rules.get("drawdown_lookback_range", "3mo")
                    )
                    if d:
                        quotes[sym] = d
                else:
                    quotes[sym] = pc.get_krx_stock_detail(sym)
            if len(quotes) == len(config.get("portfolio", [])):
                source_label = "실시간 시세"
            else:
                raise RuntimeError("일부 종목 실시간 조회 실패")
        except Exception as e:
            print(f"[안내] 실시간 시세 조회 실패({e}) -> 저장된 portfolio_data.json 시세로 대체합니다.\n")
            quotes, rate, tnx, vix, dxy, updated_at = load_cached_quotes(data_path)
            source_label = f"캐시 시세 ({updated_at})"
    else:
        quotes, rate, tnx, vix, dxy, updated_at = load_cached_quotes(data_path)
        source_label = f"캐시 시세 ({updated_at})"

    sim_notes = []
    if args.sim or args.cash_delta != 0.0:
        config, sim_notes = apply_simulations(config, quotes, args.sim, args.cash_delta)

    result = pc.evaluate_portfolio(config, quotes, rate, tnx, vix, dxy)
    print_report(result, source_label, sim_notes)


if __name__ == "__main__":
    main()
