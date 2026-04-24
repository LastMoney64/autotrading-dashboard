"""
PolymarketWeatherEngine — 날씨 트레이딩 메인 엔진

매 60분 사이클:
1. Polymarket Weather 마켓 목록 가져오기
2. 각 마켓에 대해 우리 날씨 예측으로 확률 추정
3. EV (Expected Value) 계산
4. EV >= min_ev면 Kelly Criterion으로 포지션 사이징
5. 주문 실행
6. 텔레그램 알림
"""

import logging
import re
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from polymarket_bot.polygon_client import PolygonClient
from polymarket_bot.polymarket_client import PolymarketClient
from polymarket_bot.weather_oracle import WeatherOracle, DEFAULT_CITIES

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


# 마켓 질문에서 도시명 + 온도 범위 추출
TEMP_RANGE_PATTERN = re.compile(
    r"(?:between|from)?\s*(\d+)(?:°F|F)?\s*(?:to|-|–|and)\s*(\d+)(?:°F|F)?",
    re.IGNORECASE,
)
CITY_KEYWORDS = {
    "new york": "New York,NY",
    "nyc": "New York,NY",
    "chicago": "Chicago,IL",
    "los angeles": "Los Angeles,CA",
    "la ": "Los Angeles,CA",
    "miami": "Miami,FL",
    "seattle": "Seattle,WA",
    "denver": "Denver,CO",
    "boston": "Boston,MA",
    "houston": "Houston,TX",
    "atlanta": "Atlanta,GA",
    "phoenix": "Phoenix,AZ",
    "dallas": "Dallas,TX",
    "san francisco": "San Francisco,CA",
    "washington": "Washington,DC",
    "philadelphia": "Philadelphia,PA",
    "detroit": "Detroit,MI",
    "minneapolis": "Minneapolis,MN",
    "vegas": "Las Vegas,NV",
    "portland": "Portland,OR",
    "orlando": "Orlando,FL",
}


class PolymarketWeatherEngine:
    """Polymarket 날씨 트레이딩 메인 엔진"""

    def __init__(
        self, settings, telegram, db,
        polygon: PolygonClient,
        polymarket: PolymarketClient,
        oracle: WeatherOracle,
    ):
        self.settings = settings
        self.telegram = telegram
        self.db = db
        self.polygon = polygon
        self.polymarket = polymarket
        self.oracle = oracle

        # 트레이딩 파라미터
        self.min_ev = 0.10              # 최소 10% EV
        self.max_bet_per_trade = 2.0    # 거래당 최대 $2
        self.kelly_fraction = 0.25       # Kelly의 1/4 (보수적)
        self.scan_interval = 3600        # 60분마다 스캔
        self.mode = "paper"              # "paper" or "live"

        # 통계
        self.scan_count = 0
        self.trades_count = 0

    # ──────────────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────────────

    async def run_cycle(self):
        """1회 트레이딩 사이클"""
        self.scan_count += 1
        logger.info(f"🌤️ [Polymarket #{self.scan_count}] 날씨 트레이딩 사이클 시작")

        try:
            # 1. Weather 마켓 목록
            markets = await self.polymarket.get_active_markets("Weather", limit=50)
            if not markets:
                logger.info("Weather 마켓 없음 — 스킵")
                return
            logger.info(f"  {len(markets)}개 Weather 마켓 발견")

            # 2. 마켓별 분석
            opportunities = []
            for m in markets[:30]:  # 상위 30개만
                opp = await self._analyze_market(m)
                if opp and opp.get("ev_pct", 0) >= self.min_ev * 100:
                    opportunities.append(opp)

            logger.info(f"  EV ≥ {self.min_ev*100:.0f}% 기회: {len(opportunities)}개")

            # 3. 잔고 확인
            usdc_balance = self.polygon.get_usdc_balance()
            logger.info(f"  잔고: ${usdc_balance:.2f} USDC.e")

            if usdc_balance < 1:
                logger.warning("  잔고 부족 ($1 미만) — 매매 중지")
                return

            # 4. 우선순위 정렬 (EV 높은 순)
            opportunities.sort(key=lambda x: x["ev_pct"], reverse=True)

            # 5. 매매 실행
            for opp in opportunities[:5]:  # 상위 5개만
                await self._execute_trade(opp, usdc_balance)
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Polymarket 사이클 에러: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # 마켓 분석
    # ──────────────────────────────────────────────

    async def _analyze_market(self, market: dict) -> Optional[dict]:
        """단일 마켓 분석 → EV 계산"""
        question = market.get("question", "").lower()

        # 도시 추출
        city = None
        for keyword, city_name in CITY_KEYWORDS.items():
            if keyword in question:
                city = city_name
                break
        if not city:
            return None

        # 온도 범위 추출
        match = TEMP_RANGE_PATTERN.search(question)
        if not match:
            return None
        try:
            t_min = float(match.group(1))
            t_max = float(match.group(2))
            if t_min > t_max:
                t_min, t_max = t_max, t_min
        except Exception:
            return None

        # 우리 예측 가져오기
        forecast = await self.oracle.get_forecast(city, days=3)
        if not forecast or not forecast.get("forecast"):
            return None

        # 종료일 매칭 (가장 가까운 미래 일자)
        end_date = market.get("end_date", "")
        target_temp = None
        target_date_str = None
        for f in forecast["forecast"]:
            if not end_date:
                # end_date 없으면 내일 사용
                target_temp = f.get("temp_avg")
                target_date_str = f.get("date")
                break
            try:
                if f["date"][:10] in end_date[:10]:
                    target_temp = f.get("temp_avg")
                    target_date_str = f.get("date")
                    break
            except Exception:
                continue

        if target_temp is None:
            # 내일 예측 사용
            target_temp = forecast["forecast"][0].get("temp_avg")
            target_date_str = forecast["forecast"][0].get("date")

        if target_temp is None:
            return None

        # 우리 추정 확률
        our_prob = self.oracle.estimate_probability(target_temp, t_min, t_max)

        # 시장 가격 (YES 토큰 가격)
        token_ids = market.get("token_ids", [])
        if not token_ids:
            return None

        yes_token = token_ids[0]
        market_price = await self.polymarket.get_midpoint(yes_token)
        if market_price is None or market_price <= 0:
            return None

        # EV 계산: (우리 확률 × 1) - (시장 가격) = 매수 시 기대값
        # YES 매수 → 우리 확률만큼 $1 받음, 비용은 market_price
        ev = our_prob - market_price
        ev_pct = ev * 100

        return {
            "market_id": market.get("id"),
            "question": market.get("question"),
            "city": city,
            "temp_range": f"{t_min:.0f}-{t_max:.0f}F",
            "predicted_temp": target_temp,
            "our_prob": our_prob,
            "market_price": market_price,
            "ev": ev,
            "ev_pct": ev_pct,
            "token_id": yes_token,
            "side": "BUY" if ev > 0 else "SELL",
            "target_date": target_date_str,
            "liquidity": market.get("liquidity", 0),
        }

    # ──────────────────────────────────────────────
    # 매매 실행
    # ──────────────────────────────────────────────

    async def _execute_trade(self, opp: dict, balance: float):
        """포지션 사이징 + 주문"""
        # Kelly Criterion
        # f = (bp - q) / b, where b = (1-p)/p, p = our_prob, q = 1-p
        p = opp["our_prob"]
        b = (1 - opp["market_price"]) / opp["market_price"] if opp["market_price"] > 0 else 0
        q = 1 - p

        if b <= 0:
            return

        kelly_full = (b * p - q) / b
        kelly_size = max(0, kelly_full * self.kelly_fraction)

        # 포지션 크기 (USD)
        position_usd = min(
            balance * kelly_size,
            self.max_bet_per_trade,
        )

        if position_usd < 0.5:  # Polymarket 최소 매매
            return

        # 로그
        emoji = "📝" if self.mode == "paper" else "🚀"
        msg = (
            f"{emoji} [{self.mode.upper()}] BUY {opp['city']} "
            f"{opp['temp_range']} @ ${opp['market_price']:.3f} "
            f"| EV +{opp['ev_pct']:.1f}% | ${position_usd:.2f}"
        )
        logger.info(msg)

        # DB 기록 (페이퍼 모드도 기록)
        try:
            self._save_trade_log(opp, position_usd)
        except Exception as e:
            logger.debug(f"거래 로그 저장 실패: {e}")

        # 텔레그램 알림
        try:
            await self.telegram.send(
                f"🌤️ <b>Polymarket 매매</b> [{self.mode}]\n\n"
                f"<b>마켓:</b> {opp['question'][:80]}\n"
                f"<b>도시:</b> {opp['city']}\n"
                f"<b>예측 온도:</b> {opp['predicted_temp']:.1f}F (목표 {opp['temp_range']})\n"
                f"<b>우리 확률:</b> {opp['our_prob']*100:.1f}%\n"
                f"<b>시장 가격:</b> ${opp['market_price']:.3f}\n"
                f"<b>EV:</b> +{opp['ev_pct']:.1f}%\n"
                f"<b>배팅:</b> ${position_usd:.2f}"
            )
        except Exception as e:
            logger.debug(f"텔레그램 알림 실패: {e}")

        # 라이브 매매
        if self.mode == "live":
            try:
                result = await self.polymarket.place_market_order(
                    opp["token_id"], "BUY", position_usd
                )
                if result:
                    self.trades_count += 1
                    logger.info(f"  ✅ 주문 체결: {result}")
                else:
                    logger.warning(f"  ❌ 주문 실패")
            except Exception as e:
                logger.error(f"  ❌ 주문 에러: {e}")

    def _save_trade_log(self, opp: dict, position_usd: float):
        """거래 로그 DB 저장"""
        try:
            self.db.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS polymarket_trades (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    mode TEXT,
                    market_id TEXT,
                    question TEXT,
                    city TEXT,
                    temp_range TEXT,
                    our_prob REAL,
                    market_price REAL,
                    ev_pct REAL,
                    position_usd REAL,
                    token_id TEXT
                )
                """
            )
            self.db.conn.execute(
                """
                INSERT INTO polymarket_trades
                (timestamp, mode, market_id, question, city, temp_range,
                 our_prob, market_price, ev_pct, position_usd, token_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(KST).isoformat(),
                    self.mode,
                    opp.get("market_id"),
                    opp.get("question", "")[:200],
                    opp.get("city"),
                    opp.get("temp_range"),
                    opp.get("our_prob"),
                    opp.get("market_price"),
                    opp.get("ev_pct"),
                    position_usd,
                    opp.get("token_id"),
                ),
            )
            self.db.conn.commit()
        except Exception as e:
            logger.debug(f"DB 저장 실패: {e}")

    # ──────────────────────────────────────────────
    # 초기 설정 (1회)
    # ──────────────────────────────────────────────

    async def initialize(self):
        """봇 시작 시 1회 실행: 컨트랙트 승인"""
        # 잔고 확인
        status = self.polygon.get_status()
        logger.info(
            f"💼 Polygon 지갑: {status['address']}\n"
            f"  POL: {status['pol_balance']:.4f}\n"
            f"  USDC.e: {status['usdc_balance']:.2f}"
        )

        if status["pol_balance"] < 0.1:
            logger.warning("⚠️ POL 부족 (0.1 미만) — 가스비 충전 필요")

        if status["usdc_balance"] < 1:
            logger.warning("⚠️ USDC.e 부족 ($1 미만) — Polymarket 입금 필요")

        # 컨트랙트 승인
        if status["pol_balance"] >= 0.05:
            logger.info("🔐 Polymarket 컨트랙트 승인 시작...")
            result = await self.polygon.setup_approvals()
            logger.info(
                f"✅ 승인 완료: {len(result['approved'])}개 신규, "
                f"{len(result['skipped'])}개 기존, "
                f"{len(result['failed'])}개 실패"
            )

            await self.telegram.send(
                f"🌤️ <b>Polymarket 봇 초기화</b>\n\n"
                f"<b>지갑:</b> <code>{status['address'][:10]}...{status['address'][-6:]}</code>\n"
                f"<b>POL:</b> {status['pol_balance']:.4f}\n"
                f"<b>USDC.e:</b> ${status['usdc_balance']:.2f}\n"
                f"<b>컨트랙트 승인:</b> {len(result['approved'])}개 신규\n"
                f"<b>모드:</b> {self.mode}"
            )
