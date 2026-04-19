"""
Morning Brief Data Collectors — 무료 API 기반

전부 무료, 대부분 API 키 불필요
실패해도 전체 브리핑 안 깨지도록 각각 try/except
"""

import logging
import asyncio
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# 1. 공포탐욕지수 (alternative.me — 무료, 키 없음)
# ══════════════════════════════════════════════════════

async def fetch_fear_greed_index() -> dict:
    """공포탐욕지수 + 역사적 비교"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.alternative.me/fng/?limit=30",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if not data.get("data"):
                    return {"error": "no data"}

                today = data["data"][0]
                week_ago = data["data"][7] if len(data["data"]) > 7 else today
                month_ago = data["data"][-1]

                value = int(today["value"])
                classification = today.get("value_classification", "")

                # 한국어 분류
                if value <= 25:
                    kr_class = "극단적 공포"
                elif value <= 45:
                    kr_class = "공포"
                elif value <= 55:
                    kr_class = "중립"
                elif value <= 75:
                    kr_class = "탐욕"
                else:
                    kr_class = "극단적 탐욕"

                # 역사적 해석
                interpretation = ""
                if value < 15:
                    interpretation = "역사적 매수 구간 (90일 후 평균 +48% 수익)"
                elif value < 30:
                    interpretation = "공포 구간 — 컨트리언 매수 관심"
                elif value > 80:
                    interpretation = "과열 구간 — 현금화 고려"
                elif value > 70:
                    interpretation = "탐욕 구간 — 신규 진입 주의"

                return {
                    "value": value,
                    "classification": classification,
                    "kr_class": kr_class,
                    "week_ago": int(week_ago["value"]),
                    "month_ago": int(month_ago["value"]),
                    "interpretation": interpretation,
                }
    except Exception as e:
        logger.warning(f"공포탐욕지수 수집 실패: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════
# 2. 가격/펀딩비 비교 (ccxt — 기존 okx 재사용 + 타 거래소)
# ══════════════════════════════════════════════════════

async def fetch_price_and_funding(okx_exchange) -> dict:
    """BTC/ETH 가격 + 펀딩비 크로스 비교"""
    try:
        import ccxt.async_support as ccxt

        results = {"BTC": {}, "ETH": {}}

        # OKX (기존 클라이언트 재사용)
        for sym, key in [("BTC/USDT:USDT", "BTC"), ("ETH/USDT:USDT", "ETH")]:
            try:
                ticker = await okx_exchange.get_ticker(sym) if okx_exchange else None
                if ticker:
                    results[key]["price"] = ticker.get("last", 0)
                    results[key]["change_24h"] = ticker.get("change_24h_pct", 0)
                    results[key]["high_24h"] = ticker.get("high_24h", 0)
                    results[key]["low_24h"] = ticker.get("low_24h", 0)

                    # 펀딩비 (OKX)
                    funding = await okx_exchange.get_funding_rate(sym)
                    if funding:
                        results[key]["funding_okx"] = funding.get("funding_rate", 0)
            except Exception as e:
                logger.warning(f"OKX {sym} 데이터 실패: {e}")

        # Binance / Bybit 펀딩비 (공개 API, 키 불필요)
        binance = ccxt.binance({"options": {"defaultType": "swap"}})
        bybit = ccxt.bybit({"options": {"defaultType": "swap"}})

        try:
            for sym_bin, sym_key in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]:
                # Binance
                try:
                    bn_fund = await binance.fetch_funding_rate(sym_bin)
                    results[sym_key]["funding_binance"] = bn_fund.get("fundingRate", 0)
                except Exception:
                    pass
                # Bybit
                try:
                    bb_fund = await bybit.fetch_funding_rate(sym_bin)
                    results[sym_key]["funding_bybit"] = bb_fund.get("fundingRate", 0)
                except Exception:
                    pass
        finally:
            await binance.close()
            await bybit.close()

        return results

    except Exception as e:
        logger.warning(f"가격/펀딩비 수집 실패: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════
# 3. 거래소 유입/유출 (DeFiLlama — 무료, 키 없음)
# ══════════════════════════════════════════════════════

async def fetch_onchain_flows() -> dict:
    """스테이블코인 마켓캡 변화 + DeFi TVL"""
    try:
        async with aiohttp.ClientSession() as session:
            # 스테이블코인 전체 마켓캡
            async with session.get(
                "https://stablecoins.llama.fi/stablecoins",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                stables = await resp.json()
                total_mc = stables.get("totalCirculating", {}).get("peggedUSD", 0)

            # DeFi 전체 TVL
            async with session.get(
                "https://api.llama.fi/v2/historicalChainTvl",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                tvl_data = await resp.json()
                if tvl_data:
                    today_tvl = tvl_data[-1].get("tvl", 0)
                    day_ago_tvl = tvl_data[-2].get("tvl", today_tvl) if len(tvl_data) > 1 else today_tvl
                    week_ago_tvl = tvl_data[-8].get("tvl", today_tvl) if len(tvl_data) > 7 else today_tvl
                    tvl_change_24h = ((today_tvl - day_ago_tvl) / day_ago_tvl * 100) if day_ago_tvl else 0
                    tvl_change_7d = ((today_tvl - week_ago_tvl) / week_ago_tvl * 100) if week_ago_tvl else 0
                else:
                    today_tvl = tvl_change_24h = tvl_change_7d = 0

            return {
                "stablecoin_mc_usd": total_mc,
                "defi_tvl_usd": today_tvl,
                "tvl_change_24h_pct": tvl_change_24h,
                "tvl_change_7d_pct": tvl_change_7d,
            }
    except Exception as e:
        logger.warning(f"온체인 수집 실패: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════
# 4. 고래 지갑 추적 (Etherscan — 무료 API)
# ══════════════════════════════════════════════════════

# 유명 고래/스마트머니 지갑 (공개 리스트)
WHALE_WALLETS = {
    "Binance Hot 1": "0x28c6c06298d514db089934071355e5743bf21d60",
    "Coinbase 10": "0xa090e606e30bd747d4e6245a1517ebe430f0057e",
    "Kraken 13": "0xdA9dfA130Df4dE4673b89022EE50ff26f6EA73Cf",
}

async def fetch_whale_activity(etherscan_key: str) -> dict:
    """추적 지갑들의 24H 큰 움직임"""
    if not etherscan_key:
        return {"error": "no key"}
    try:
        whales = []
        async with aiohttp.ClientSession() as session:
            for name, addr in list(WHALE_WALLETS.items())[:3]:  # rate limit 고려
                try:
                    # ETH 잔고
                    url = (
                        f"https://api.etherscan.io/api"
                        f"?module=account&action=balance&address={addr}"
                        f"&tag=latest&apikey={etherscan_key}"
                    )
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        data = await resp.json()
                        if data.get("status") == "1":
                            balance_wei = int(data.get("result", 0))
                            balance_eth = balance_wei / 1e18
                            whales.append({
                                "name": name,
                                "address": addr[:10] + "...",
                                "eth_balance": round(balance_eth, 2),
                            })
                    await asyncio.sleep(0.3)  # 5/sec rate limit
                except Exception as e:
                    logger.debug(f"고래 {name} 조회 실패: {e}")
                    continue
        return {"whales": whales}
    except Exception as e:
        logger.warning(f"고래 수집 실패: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════
# 5. 밈코인 트렌딩 (DexScreener — 무료, 키 없음)
# ══════════════════════════════════════════════════════

async def fetch_trending_memes() -> dict:
    """솔라나 거래량 급등 토큰 TOP 5"""
    try:
        async with aiohttp.ClientSession() as session:
            # DexScreener 트렌딩 (솔라나)
            async with session.get(
                "https://api.dexscreener.com/token-profiles/latest/v1",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                profiles = await resp.json()

            # 상위 5개 (솔라나만 필터)
            solana_tokens = []
            if isinstance(profiles, list):
                for p in profiles[:20]:
                    if p.get("chainId") == "solana":
                        token_addr = p.get("tokenAddress")
                        if token_addr:
                            solana_tokens.append({
                                "address": token_addr,
                                "url": p.get("url", ""),
                                "description": (p.get("description", "") or "")[:80],
                            })
                    if len(solana_tokens) >= 5:
                        break

            # 각 토큰의 가격/거래량 조회
            detailed = []
            for tok in solana_tokens[:5]:
                try:
                    async with session.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{tok['address']}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        td = await resp.json()
                        pairs = td.get("pairs", [])
                        if pairs:
                            p0 = pairs[0]
                            detailed.append({
                                "name": p0.get("baseToken", {}).get("symbol", "?"),
                                "price_usd": float(p0.get("priceUsd", 0) or 0),
                                "change_24h": float(p0.get("priceChange", {}).get("h24", 0) or 0),
                                "volume_24h": float(p0.get("volume", {}).get("h24", 0) or 0),
                                "liquidity_usd": float(p0.get("liquidity", {}).get("usd", 0) or 0),
                            })
                except Exception:
                    continue

            # 거래량 순 정렬
            detailed.sort(key=lambda x: x["volume_24h"], reverse=True)
            return {"trending": detailed[:5]}

    except Exception as e:
        logger.warning(f"밈코인 트렌딩 수집 실패: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════
# 6. 봇 성과 요약 (우리 DB에서)
# ══════════════════════════════════════════════════════

async def fetch_bot_status(db, feedback, okx_exchange) -> dict:
    """우리 봇 현재 상태"""
    try:
        # 잔고
        balance = 0
        if okx_exchange:
            try:
                bal = await okx_exchange.get_balance()
                balance = bal.get("total", 0) if bal else 0
            except Exception:
                pass

        # 피드백 통계
        stats = feedback.get_stats() if feedback else {}

        # 활성 포지션
        positions = []
        if okx_exchange:
            try:
                positions = await okx_exchange.get_positions()
            except Exception:
                pass

        return {
            "balance_usd": balance,
            "win_rate": stats.get("win_rate", 0),
            "total_trades": stats.get("total_trades", 0),
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "total_pnl_pct": stats.get("total_pnl_pct", 0),
            "consecutive_losses": stats.get("consecutive_losses", 0),
            "open_positions": len(positions),
            "positions": [
                {
                    "symbol": p.get("symbol", ""),
                    "side": p.get("side", ""),
                    "size": p.get("size", 0),
                    "unrealized_pnl": p.get("unrealized_pnl", 0),
                }
                for p in positions[:3]
            ],
        }
    except Exception as e:
        logger.warning(f"봇 상태 수집 실패: {e}")
        return {"error": str(e)}
