"""
NewsFetcher — 뉴스/감성 데이터 수집

추상 인터페이스 + Mock 구현.
실제 뉴스 API (CryptoPanic, CoinGecko 등) 연동은 추후.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import random


@dataclass
class NewsItem:
    """뉴스 아이템"""
    title: str
    source: str
    timestamp: datetime
    sentiment: float              # -1.0 (극부정) ~ +1.0 (극긍정)
    relevance: float              # 0.0 ~ 1.0 (관련도)
    category: str = "general"     # market, regulation, hack, macro, general

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "sentiment": self.sentiment,
            "relevance": self.relevance,
            "category": self.category,
        }


@dataclass
class SentimentData:
    """시장 감성 종합 데이터"""
    fear_greed_index: int          # 0 (극도 공포) ~ 100 (극도 탐욕)
    fear_greed_label: str          # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    news_sentiment_avg: float      # 뉴스 평균 감성 (-1 ~ +1)
    recent_news: list[NewsItem]
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "fear_greed_index": self.fear_greed_index,
            "fear_greed_label": self.fear_greed_label,
            "news_sentiment_avg": self.news_sentiment_avg,
            "recent_news": [n.to_dict() for n in self.recent_news],
            "timestamp": self.timestamp.isoformat(),
        }


# ── 추상 인터페이스 ─────────────────────────────────────

class BaseNewsFetcher(ABC):

    @abstractmethod
    async def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        ...

    @abstractmethod
    async def get_fear_greed_index(self) -> int:
        ...

    async def get_sentiment(self, symbol: str) -> SentimentData:
        """종합 감성 데이터"""
        news = await self.get_news(symbol)
        fgi = await self.get_fear_greed_index()

        avg_sentiment = 0.0
        if news:
            avg_sentiment = sum(n.sentiment for n in news) / len(news)

        label = self._fgi_to_label(fgi)

        return SentimentData(
            fear_greed_index=fgi,
            fear_greed_label=label,
            news_sentiment_avg=round(avg_sentiment, 3),
            recent_news=news,
        )

    @staticmethod
    def _fgi_to_label(index: int) -> str:
        if index <= 20:
            return "Extreme Fear"
        elif index <= 40:
            return "Fear"
        elif index <= 60:
            return "Neutral"
        elif index <= 80:
            return "Greed"
        else:
            return "Extreme Greed"


# ── Mock 구현 ───────────────────────────────────────────

class MockNewsFetcher(BaseNewsFetcher):
    """테스트용 가짜 뉴스/감성 데이터"""

    _MOCK_NEWS = [
        # 긍정적
        ("Bitcoin ETF sees record inflows of $1.2B", "Bloomberg", 0.8, "market"),
        ("Major bank announces crypto custody service", "Reuters", 0.6, "market"),
        ("Ethereum upgrade successfully deployed", "CoinDesk", 0.5, "general"),
        ("Institutional adoption continues to grow", "Forbes", 0.4, "market"),
        # 부정적
        ("SEC files lawsuit against major exchange", "WSJ", -0.7, "regulation"),
        ("$200M hack hits DeFi protocol", "CoinTelegraph", -0.8, "hack"),
        ("Fed signals higher rates for longer", "CNBC", -0.5, "macro"),
        ("China renews crypto mining crackdown", "Reuters", -0.6, "regulation"),
        # 중립
        ("Bitcoin consolidates around key support", "CoinDesk", 0.0, "general"),
        ("Crypto market volume remains steady", "CoinGecko", 0.1, "market"),
        ("New stablecoin regulation proposed", "Bloomberg", -0.2, "regulation"),
        ("Mining difficulty reaches new ATH", "BitInfoCharts", 0.1, "general"),
    ]

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

    async def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        selected = self._rng.sample(
            self._MOCK_NEWS, min(limit, len(self._MOCK_NEWS))
        )
        return [
            NewsItem(
                title=title,
                source=source,
                timestamp=datetime.utcnow(),
                sentiment=sentiment + self._rng.uniform(-0.1, 0.1),
                relevance=self._rng.uniform(0.5, 1.0),
                category=category,
            )
            for title, source, sentiment, category in selected
        ]

    async def get_fear_greed_index(self) -> int:
        return self._rng.randint(15, 85)
