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
    """BTC/ETH 가격 + 펀딩비 크로스 비교 (공식 HTTP API 직접 호출)"""
    results = {"BTC": {}, "ETH": {}}

    # ── OKX (기존 클라이언트) ─────────────────────────
    for sym, key in [("BTC/USDT:USDT", "BTC"), ("ETH/USDT:USDT", "ETH")]:
        try:
            ticker = await okx_exchange.get_ticker(sym) if okx_exchange else None
            if ticker:
                results[key]["price"] = ticker.get("last", 0)
                results[key]["change_24h"] = ticker.get("change_24h_pct", 0)
                results[key]["high_24h"] = ticker.get("high_24h", 0)
                results[key]["low_24h"] = ticker.get("low_24h", 0)
                funding = await okx_exchange.get_funding_rate(sym)
                if funding:
                    results[key]["funding_okx"] = funding.get("funding_rate", 0)
        except Exception as e:
            logger.warning(f"OKX {sym} 실패: {e}")

    # Binance는 Railway(미국 서버) 차단되니 Bitget + Hyperliquid로 대체
    # 모두 지역 제한 없음
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"}

    async with aiohttp.ClientSession(headers=headers) as session:
        # ── Bitget 펀딩비 ────────────────────────────
        # https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol=BTCUSDT&productType=USDT-FUTURES
        for sym_bg, sym_key in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]:
            try:
                async with session.get(
                    "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
                    params={"symbol": sym_bg, "productType": "USDT-FUTURES"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rlist = data.get("data", [])
                        if rlist and isinstance(rlist, list) and len(rlist) > 0:
                            rate = float(rlist[0].get("fundingRate", 0))
                            results[sym_key]["funding_bitget"] = rate
                            logger.debug(f"  ✅ Bitget {sym_bg}: {rate*100:.4f}%")
                        else:
                            results[sym_key]["funding_bitget"] = 0
                            logger.warning(f"  ⚠️ Bitget {sym_bg} 빈 응답: {data}")
                    else:
                        logger.warning(f"  ❌ Bitget {sym_bg} HTTP {resp.status}")
                        results[sym_key]["funding_bitget"] = 0
            except Exception as e:
                logger.warning(f"  ❌ Bitget {sym_bg}: {type(e).__name__}: {e}")
                results[sym_key]["funding_bitget"] = 0

        # ── Hyperliquid 펀딩비 ──────────────────────
        # https://api.hyperliquid.xyz/info (POST)
        try:
            async with session.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "metaAndAssetCtxs"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # data = [meta, assetCtxs]
                    if isinstance(data, list) and len(data) >= 2:
                        meta, ctxs = data[0], data[1]
                        universe = meta.get("universe", [])
                        for i, asset in enumerate(universe):
                            name = asset.get("name", "")
                            if name in ("BTC", "ETH") and i < len(ctxs):
                                ctx = ctxs[i]
                                rate = float(ctx.get("funding", 0))
                                results[name]["funding_hyperliquid"] = rate
                                logger.debug(f"  ✅ Hyperliquid {name}: {rate*100:.4f}%")
                else:
                    logger.warning(f"  ❌ Hyperliquid HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"  ❌ Hyperliquid: {type(e).__name__}: {e}")

    return results


# ══════════════════════════════════════════════════════
# 3. 거래소 유입/유출 (DeFiLlama — 무료, 키 없음)
# ══════════════════════════════════════════════════════

async def fetch_onchain_flows() -> dict:
    """스테이블코인 마켓캡 변화 + DeFi TVL"""
    try:
        total_mc = 0
        stable_change_24h = 0
        today_tvl = 0
        tvl_change_24h = 0
        tvl_change_7d = 0

        async with aiohttp.ClientSession() as session:
            # 스테이블코인 시총 + 24H 변화 — CoinGecko 사용 (더 안정적)
            # USDT + USDC + DAI 3대장만 집계
            try:
                async with session.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "ids": "tether,usd-coin,dai,binance-usd,first-digital-usd",
                        "order": "market_cap_desc",
                        "per_page": 10,
                        "page": 1,
                        "price_change_percentage": "24h",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status == 200:
                        coins = await resp.json()
                        for c in coins:
                            mc = c.get("market_cap", 0) or 0
                            total_mc += mc
                        # 가중 평균 24H 변화
                        if total_mc > 0:
                            weighted = 0
                            for c in coins:
                                mc = c.get("market_cap", 0) or 0
                                ch = c.get("market_cap_change_percentage_24h", 0) or 0
                                weighted += (mc / total_mc) * ch
                            stable_change_24h = weighted
                        logger.debug(f"  ✅ 스테이블코인 시총: ${total_mc/1e9:.1f}B, 24H {stable_change_24h:+.2f}%")
                    else:
                        logger.warning(f"  ❌ CoinGecko 스테이블 HTTP {resp.status}")
            except Exception as e:
                logger.warning(f"  ❌ 스테이블코인 CoinGecko 실패: {type(e).__name__}: {e}")
                # 폴백: DeFiLlama
                try:
                    async with session.get(
                        "https://stablecoins.llama.fi/stablecoins",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        stables = await resp.json()
                        total_mc = stables.get("totalCirculating", {}).get("peggedUSD", 0)
                except Exception:
                    pass

            # DeFi 전체 TVL
            try:
                async with session.get(
                    "https://api.llama.fi/v2/historicalChainTvl",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    tvl_data = await resp.json()
                    if tvl_data and len(tvl_data) > 0:
                        today_tvl = tvl_data[-1].get("tvl", 0)

                        # 24H: 하루 전 (1일)
                        if len(tvl_data) >= 2:
                            day_ago_tvl = tvl_data[-2].get("tvl", today_tvl)
                            if day_ago_tvl and day_ago_tvl != today_tvl:
                                tvl_change_24h = (today_tvl - day_ago_tvl) / day_ago_tvl * 100

                        # 7D: 7일 전
                        if len(tvl_data) >= 8:
                            week_ago_tvl = tvl_data[-8].get("tvl", today_tvl)
                            if week_ago_tvl:
                                tvl_change_7d = (today_tvl - week_ago_tvl) / week_ago_tvl * 100
            except Exception as e:
                logger.debug(f"TVL 차트 실패: {e}")

        return {
            "stablecoin_mc_usd": total_mc,
            "stablecoin_change_24h_pct": stable_change_24h,
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

# 유명 고래/스마트머니 지갑 (검증된 주소 — 2026년 기준)
WHALE_WALLETS = {
    "Binance 14": "0xF977814e90dA44bFA03b6295A0616a897441aceC",       # 수십만 ETH
    "Binance 7": "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",        # 대량 ETH
    "Beacon Deposit": "0x00000000219ab540356cBB839Cbe05303d7705Fa",   # ETH 2.0 staking (최대)
    "Arbitrum Bridge": "0x8315177aB297bA92A06054cE80a67Ed4DBd7ed3a",  # L2 브리지
    "Vitalik": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",          # Vitalik.eth
}

async def fetch_whale_activity(etherscan_key: str) -> dict:
    """추적 지갑들의 24H 큰 움직임 (Etherscan V2 API 사용)"""
    if not etherscan_key:
        logger.warning("Etherscan API 키 없음 (ETHERSCAN_API_KEY 환경변수 확인)")
        return {"error": "no key"}

    logger.info(f"🐋 고래 추적 시작 (키 {etherscan_key[:8]}...)")
    whales = []

    try:
        async with aiohttp.ClientSession() as session:
            for name, addr in list(WHALE_WALLETS.items())[:4]:
                try:
                    # Etherscan V2 API (chainid=1은 이더리움)
                    url = "https://api.etherscan.io/v2/api"
                    params = {
                        "chainid": 1,
                        "module": "account",
                        "action": "balance",
                        "address": addr,
                        "tag": "latest",
                        "apikey": etherscan_key,
                    }
                    async with session.get(
                        url, params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        text = await resp.text()
                        try:
                            data = await resp.json(content_type=None)
                        except Exception:
                            import json
                            data = json.loads(text) if text else {}

                        # V2 API는 status/result 또는 바로 잔고 반환
                        if data.get("status") == "1" or "result" in data:
                            result_val = data.get("result", "0")
                            try:
                                balance_wei = int(result_val) if result_val else 0
                                balance_eth = balance_wei / 1e18
                                whales.append({
                                    "name": name,
                                    "address": addr[:10] + "...",
                                    "eth_balance": round(balance_eth, 2),
                                })
                                logger.debug(f"  ✅ {name}: {balance_eth:,.2f} ETH")
                            except (ValueError, TypeError) as e:
                                logger.warning(f"  ❌ {name} 파싱 실패: result={result_val}, e={e}")
                        else:
                            msg = data.get("message", "unknown")
                            logger.warning(f"  ❌ {name} API 에러: status={data.get('status')}, msg={msg}")

                    await asyncio.sleep(0.3)  # V2도 5/sec 제한
                except Exception as e:
                    logger.warning(f"  ❌ {name} 조회 실패: {type(e).__name__}: {e}")
                    continue

        logger.info(f"🐋 고래 추적 완료: {len(whales)}개 조회 성공")
        return {"whales": whales}
    except Exception as e:
        logger.warning(f"고래 수집 전체 실패: {type(e).__name__}: {e}")
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
