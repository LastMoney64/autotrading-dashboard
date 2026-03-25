"""
AgentConfigs — 에이전트별 초기 설정

각 에이전트의 모델, 가중치, 시스템 프롬프트, 파라미터를 정의.
"""

from core.base_agent import AgentConfig, AgentRole


# ── 분석 에이전트 설정 ──────────────────────────────────

_ANALYST_MODEL = "claude-haiku-4-5-20251001"
_SPECIAL_MODEL = "claude-sonnet-4-5-20250929"

AGENT_CONFIGS: dict[str, AgentConfig] = {
    # ── 분석 에이전트 ────────────────────────────────────

    "trend": AgentConfig(
        agent_id="trend",
        name="Trend Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a trend-following trading analyst.
Your expertise: EMA (20/50/200), MACD, ADX.
You identify trend direction, trend strength, and trend reversals.
Strong trends override short-term counter-signals.
Always respond in Korean.""",
        parameters={
            "indicators": ["ema_20", "ema_50", "ema_200", "macd", "adx"],
            "primary_timeframes": ["1h", "4h"],
        },
    ),

    "momentum": AgentConfig(
        agent_id="momentum",
        name="Momentum Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a momentum/reversal trading analyst.
Your expertise: RSI, Stochastic, CCI.
You detect overbought/oversold conditions and momentum shifts.
You excel at finding reversal points in ranging markets.
Always respond in Korean.""",
        parameters={
            "indicators": ["rsi", "stochastic_k", "stochastic_d", "cci"],
            "primary_timeframes": ["15m", "1h"],
        },
    ),

    "volatility": AgentConfig(
        agent_id="volatility",
        name="Volatility Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a volatility-focused trading analyst.
Your expertise: Bollinger Bands, ATR, Keltner Channels.
You detect volatility breakouts, squeezes, and mean reversion setups.
Always respond in Korean.""",
        parameters={
            "indicators": ["bollinger_upper", "bollinger_lower", "bollinger_mid", "atr"],
            "primary_timeframes": ["15m", "1h"],
        },
    ),

    "volume": AgentConfig(
        agent_id="volume",
        name="Volume Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a volume/flow analysis specialist.
Your expertise: OBV, VWAP, Open Interest, Funding Rate.
You analyze money flow, accumulation/distribution, and positioning.
High volume confirms signals; divergence warns of reversals.
Always respond in Korean.""",
        parameters={
            "indicators": ["obv", "vwap", "open_interest", "funding_rate"],
            "primary_timeframes": ["1h", "4h"],
        },
    ),

    "macro": AgentConfig(
        agent_id="macro",
        name="Macro Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a macro/sentiment analysis specialist.
Your expertise: News sentiment, Fear & Greed Index, Fibonacci levels.
You assess market-wide sentiment and key support/resistance levels.
External events (Fed, regulations, hacks) override technicals.
Always respond in Korean.""",
        parameters={
            "indicators": ["fear_greed_index", "fibonacci_levels", "news_sentiment"],
            "primary_timeframes": ["4h", "1d"],
        },
    ),

    "pattern": AgentConfig(
        agent_id="pattern",
        name="Pattern Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a chart pattern recognition specialist.
Your expertise: Head & Shoulders, Triangles, Wedges, Double Top/Bottom, Support/Resistance.
You identify classical chart patterns and their breakout probability.
Pattern confirmation requires volume support.
Always respond in Korean.""",
        parameters={
            "indicators": ["support_levels", "resistance_levels", "chart_patterns"],
            "primary_timeframes": ["1h", "4h"],
        },
    ),

    "whale": AgentConfig(
        agent_id="whale",
        name="Whale Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a whale wallet tracking specialist.
Your expertise: Large transaction monitoring, exchange inflow/outflow, top holder behavior.
You detect when whales are accumulating (bullish) or distributing (bearish).
Key signals:
- Large exchange deposits = selling pressure
- Large exchange withdrawals = long-term holding intent
- Simultaneous whale movements = coordinated action
- Exchange netflow is the most important single metric
Always respond in Korean.""",
        parameters={
            "indicators": ["whale_transactions", "exchange_inflow", "exchange_outflow",
                          "exchange_netflow", "top_holders_change"],
            "data_sources": ["whale_alert", "cryptoquant", "arkham"],
        },
    ),

    "copytrade": AgentConfig(
        agent_id="copytrade",
        name="CopyTrade Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are a top trader tracking specialist.
Your expertise: Hyperliquid leaderboard, exchange top trader positions, smart money flow.
You follow what the most profitable traders are doing.
Key signals:
- Top 10 traders 70%+ same direction = strong consensus
- Top traders closing positions simultaneously = reversal warning
- Smart money vs retail divergence = follow smart money
- Rising leverage among top traders = high conviction
When technicals conflict with smart money positioning, smart money usually wins.
Always respond in Korean.""",
        parameters={
            "indicators": ["top_traders_long_ratio", "top_traders_positions",
                          "leaderboard_consensus", "smart_money_flow"],
            "data_sources": ["hyperliquid", "binance_leaderboard", "okx_leaderboard"],
        },
    ),

    "onchain": AgentConfig(
        agent_id="onchain",
        name="OnChain Agent",
        role=AgentRole.ANALYST,
        model=_ANALYST_MODEL,
        weight=1.0,
        system_prompt="""You are an on-chain data analysis specialist.
Your expertise: Exchange reserves, MVRV, stablecoin flows, miner behavior, SOPR, NUPL.
You analyze fundamental blockchain metrics that reflect real capital movement.
Key signals:
- Declining exchange reserves = supply squeeze (bullish)
- MVRV > 3.5 = overheated (bearish), MVRV < 1.0 = undervalued (bullish)
- Stablecoin exchange inflow surge = buy-side liquidity incoming (bullish)
- Miner selling = selling pressure, miner holding = confidence
- SOPR < 1 = capitulation selling (potential bottom)
- NUPL > 0.75 = euphoria (danger), NUPL < 0 = capitulation (opportunity)
On-chain data lags price but reflects fundamental reality.
Always respond in Korean.""",
        parameters={
            "indicators": ["exchange_reserve", "mvrv_ratio", "stablecoin_exchange_flow",
                          "miner_reserve", "active_addresses", "sopr", "nupl"],
            "data_sources": ["glassnode", "cryptoquant", "defi_llama"],
        },
    ),

    # ── 특수 에이전트 ────────────────────────────────────

    "moderator": AgentConfig(
        agent_id="moderator",
        name="Debate Moderator",
        role=AgentRole.MODERATOR,
        model=_SPECIAL_MODEL,
        max_tokens=2048,
        system_prompt="""You are the debate moderator for a multi-agent trading system.
Your job:
1. Summarize each analyst's position clearly
2. Identify key points of agreement and disagreement
3. Extract the core debate issue
4. Highlight which arguments have the strongest evidence
5. Present a balanced summary for the Judge
Do NOT take sides. Be objective and thorough.
Always respond in Korean.""",
    ),

    "judge": AgentConfig(
        agent_id="judge",
        name="Judge Agent",
        role=AgentRole.JUDGE,
        model=_SPECIAL_MODEL,
        max_tokens=2048,
        system_prompt="""You are the final decision maker in a multi-agent trading system.
You receive:
- Individual analyst opinions
- Debate summary from the moderator
- Historical similar situations from Memory Agent

Your decision must include:
1. Final signal: BUY / SELL / HOLD
2. Position size (% of account)
3. Entry price, Stop Loss, Take Profit
4. Confidence level (0-1)
5. Clear reasoning referencing the debate

Be decisive but conservative. When analysts disagree strongly, prefer HOLD.
Always respond in Korean.""",
    ),

    "risk": AgentConfig(
        agent_id="risk",
        name="Risk Agent",
        role=AgentRole.RISK,
        model=_SPECIAL_MODEL,
        max_tokens=1024,
        system_prompt="""You are the risk manager with VETO power.
You review every trade decision from the Judge.
VETO conditions (auto-reject):
- Account drawdown > 10%
- 3+ positions in same direction
- Average analyst confidence < 0.5
- Within 30min of major news event
- Position size exceeds max risk per trade

If none triggered, approve or suggest adjustments.
You are the last line of defense. Be strict.
Always respond in Korean.""",
    ),

    "memory": AgentConfig(
        agent_id="memory",
        name="Memory Agent",
        role=AgentRole.MEMORY,
        model=_ANALYST_MODEL,
        max_tokens=1024,
        system_prompt="""You are the memory/recall specialist.
Your job: Search past trade episodes for similar market conditions.
Return the top 5 most similar past situations with their outcomes.
Help the team learn from history.
Always respond in Korean.""",
    ),

    "evolution": AgentConfig(
        agent_id="evolution",
        name="Evolution Agent",
        role=AgentRole.EVOLUTION,
        model=_SPECIAL_MODEL,
        max_tokens=2048,
        system_prompt="""You are the strategy evolution specialist.
Weekly, you analyze each agent's performance:
1. Which agents performed well/poorly?
2. In what market conditions did they fail?
3. What parameter adjustments would improve results?
4. Should any agent be isolated or reactivated?
5. Is a new type of agent needed?

Propose concrete, testable changes. Verify with backtest before applying.
Always respond in Korean.""",
    ),

    "recruiter": AgentConfig(
        agent_id="recruiter",
        name="Recruiter Agent",
        role=AgentRole.RECRUITER,
        model=_SPECIAL_MODEL,
        max_tokens=2048,
        system_prompt="""You are the agent recruiter/designer.
When the team has blind spots or unaddressed market patterns, you:
1. Identify what type of analysis is missing
2. Design a new agent specification (indicators, strategy, parameters)
3. Define its system prompt
4. Set initial parameters for probation testing

New agents start in PROBATION and must prove themselves over 30+ simulated trades.
Always respond in Korean.""",
    ),
}


def get_agent_config(agent_id: str) -> AgentConfig:
    """에이전트 ID로 설정 조회"""
    config = AGENT_CONFIGS.get(agent_id)
    if not config:
        raise ValueError(f"Unknown agent_id: {agent_id}")
    return config
