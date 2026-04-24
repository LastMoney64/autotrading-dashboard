"""
WeatherOracle — Visual Crossing API 기반 날씨 예측

기능:
- 도시별 일일 예측 가져오기
- 온도 분포 확률 계산 (Polymarket 마켓에 매칭)
- 캐싱 (1시간)
"""

import logging
import time
from typing import Optional
import aiohttp
import math

logger = logging.getLogger(__name__)

VC_BASE = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"


class WeatherOracle:
    """Visual Crossing 날씨 예측 + 온도 확률 계산"""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("VC_API_KEY 필수")
        self.api_key = api_key
        self._cache: dict = {}  # {city_date: (data, timestamp)}
        self._cache_ttl = 3600  # 1시간

    async def get_forecast(self, city: str, days: int = 7) -> Optional[dict]:
        """
        도시 N일 예측

        Returns: {
            "city": str,
            "forecast": [
                {"date": "2026-04-26", "temp_max": 75, "temp_min": 60,
                 "temp_avg": 67, "feels_like_avg": 65,
                 "precip": 0.1, "humidity": 60, "wind": 8,
                 "conditions": "Partly cloudy"},
                ...
            ]
        }
        """
        cache_key = f"{city}_{days}"
        if cache_key in self._cache:
            data, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return data

        try:
            url = f"{VC_BASE}/{city}/next{days}days"
            params = {
                "key": self.api_key,
                "unitGroup": "us",  # F (Polymarket이 F 사용)
                "include": "days",
                "elements": "datetime,tempmax,tempmin,temp,feelslike,precip,humidity,windspeed,conditions",
                "contentType": "json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning(f"VC API HTTP {resp.status} for {city}")
                        return None
                    raw = await resp.json()

            forecast = []
            for d in raw.get("days", []):
                forecast.append({
                    "date": d.get("datetime"),
                    "temp_max": d.get("tempmax"),
                    "temp_min": d.get("tempmin"),
                    "temp_avg": d.get("temp"),
                    "feels_like_avg": d.get("feelslike"),
                    "precip": d.get("precip", 0),
                    "humidity": d.get("humidity", 0),
                    "wind": d.get("windspeed", 0),
                    "conditions": d.get("conditions", ""),
                })

            result = {"city": city, "forecast": forecast}
            self._cache[cache_key] = (result, time.time())
            return result
        except Exception as e:
            logger.warning(f"날씨 조회 실패 {city}: {e}")
            return None

    def estimate_probability(
        self, predicted_temp: float, target_min: float, target_max: float,
        std_dev: float = 2.5,
    ) -> float:
        """
        예측 온도가 [target_min, target_max] 범위에 들어갈 확률

        가우시안 분포 가정 (날씨 예측 표준편차 보통 2~4F)

        Returns: 0.0 ~ 1.0
        """
        if not predicted_temp:
            return 0.5

        try:
            # Z-scores
            z_low = (target_min - 0.5 - predicted_temp) / std_dev
            z_high = (target_max + 0.5 - predicted_temp) / std_dev

            # 누적 정규분포 확률 (math.erf 사용)
            def normal_cdf(z):
                return 0.5 * (1 + math.erf(z / math.sqrt(2)))

            return max(0.0, min(1.0, normal_cdf(z_high) - normal_cdf(z_low)))
        except Exception:
            return 0.5


# ═══════════════════════════════════════════════════════
# 추적 도시 리스트 (Polymarket Weather 마켓에 자주 등장)
# ═══════════════════════════════════════════════════════

DEFAULT_CITIES = [
    "New York,NY",
    "Chicago,IL",
    "Los Angeles,CA",
    "Miami,FL",
    "Seattle,WA",
    "Denver,CO",
    "Boston,MA",
    "Houston,TX",
    "Atlanta,GA",
    "Phoenix,AZ",
    "Dallas,TX",
    "San Francisco,CA",
    "Washington,DC",
    "Philadelphia,PA",
    "Detroit,MI",
    "Minneapolis,MN",
    "St. Louis,MO",
    "Las Vegas,NV",
    "Portland,OR",
    "Orlando,FL",
]
