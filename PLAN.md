# 멀티 에이전트 자동매매 AI 시스템 — 구현 계획

> 작성일: 2026-03-19
> 다음 세션에서 Claude Code에게: "AutoTrading PLAN.md 읽고 PHASE 1부터 시작해줘"

---

## 핵심 개념

- 에이전트 팀이 **독립 분석 → 토론 → 최종 판단** 순으로 매매 결정
- 성과 저조 에이전트는 **격리 → 전략 수정 → 복귀**
- Recruiter Agent가 **필요 시 새 에이전트 자동 설계/영입**
- 모든 거래 결과가 메모리에 쌓이며 **자기진화**

---

## 에이전트 구성

### 분석 에이전트 (`claude-haiku-4-5`) — 총 9개
| 에이전트 | 지표 |
|----------|------|
| Trend Agent | EMA 20/50/200, MACD, ADX |
| Momentum Agent | RSI, 스토캐스틱, CCI |
| Volatility Agent | 볼린저밴드, ATR, Keltner |
| Volume Agent | OBV, VWAP, OI, 펀딩비 |
| Macro Agent | 뉴스 감성, 공포탐욕지수, 피보나치 |
| Pattern Agent | 헤드앤숄더, 삼각수렴, 지지/저항 |
| **Whale Agent** | 고래 이체, 거래소 입출금, 상위 지갑 변화 |
| **CopyTrade Agent** | 탑트레이더 포지션, 리더보드, 스마트머니 |
| **OnChain Agent** | MVRV, SOPR, NUPL, 거래소 보유량, 채굴자 |

### 특수 에이전트 (`claude-sonnet-4-6`)
| 에이전트 | 역할 |
|----------|------|
| Moderator | 토론 사회, 핵심 쟁점 정리 |
| Judge | 최종 매매 결정 + 포지션 사이징 |
| Risk | 리스크 검토, **거부권 보유** |
| Memory | 과거 패턴 검색 |
| Evolution | 주간 전략 진화 |
| Recruiter | 신규 에이전트 설계/영입 |

---

## 폴더 구조

```
AutoTrading/
├── core/
│   ├── base_agent.py
│   ├── agent_registry.py
│   └── message_bus.py
├── agents/
│   ├── analysts/
│   │   ├── trend_agent.py
│   │   ├── momentum_agent.py
│   │   ├── volatility_agent.py
│   │   ├── volume_agent.py
│   │   ├── macro_agent.py
│   │   ├── pattern_agent.py
│   │   ├── whale_agent.py
│   │   ├── copytrade_agent.py
│   │   └── onchain_agent.py
│   └── special/
│       ├── moderator_agent.py
│       ├── judge_agent.py
│       ├── risk_agent.py
│       ├── memory_agent.py
│       ├── evolution_agent.py
│       └── recruiter_agent.py
├── debate/
│   ├── debate_room.py
│   └── debate_record.py
├── memory/
│   ├── episode_memory.py
│   ├── pattern_memory.py
│   └── performance_memory.py
├── evolution/
│   ├── performance_tracker.py
│   ├── weight_adjuster.py
│   └── strategy_evolver.py
├── data/
│   ├── market_data.py
│   ├── indicators.py
│   └── news_fetcher.py
├── execution/
│   └── mock_executor.py
├── monitoring/
│   └── telegram_monitor.py
├── db/
│   ├── models.py
│   └── database.py
├── config/
│   ├── settings.py
│   └── agent_configs.py
└── main.py
```

---

## 구현 단계

| Phase | 내용 | 상태 |
|-------|------|------|
| **PHASE 1** | 기반 구조 (BaseAgent, Registry, MessageBus, Config) | ✅ 완료 |
| **PHASE 2** | 데이터 레이어 (Mock 데이터, 지표 엔진, 뉴스) | ✅ 완료 |
| **PHASE 3** | 분석 에이전트 9개 (기본6 + Whale/CopyTrade/OnChain) | ✅ 완료 |
| **PHASE 4** | Debate Room (토론 엔진, Moderator) | ✅ 완료 |
| **PHASE 5** | 판단 에이전트 (Judge, Risk) | ✅ 완료 |
| **PHASE 6** | Memory 시스템 (SQLite, Episode/Performance/Pattern) | ✅ 완료 |
| **PHASE 7** | Evolution 시스템 (가중치 조정, 전략 진화) | ✅ 완료 |
| **PHASE 8** | Recruiter Agent (에이전트 자동 영입) | ✅ 완료 (PHASE 7에 포함) |
| **PHASE 9** | Telegram 모니터링 | ✅ 완료 |
| **PHASE 10** | 거래소 연결 (ccxt, Hyperliquid) | ⬜ 미시작 |

---

## 의사결정 1 사이클 흐름

```
T+0:00  시장 데이터 수집
T+0:05  9개 분석 에이전트 병렬 분석 (asyncio)
T+0:15  분석 완료 → Debate Room 공유
T+0:20  2라운드 토론 (반론/보완)
T+0:25  Memory Agent: 유사 과거 상황 검색
T+0:27  Judge Agent: 최종 결정
T+0:28  Risk Agent: 거부권 검토
T+0:30  주문 실행 또는 SKIP
T+0:31  결과 기록
```

---

## 자기진화 흐름

```
매 거래 후    → 에이전트별 점수 업데이트
50거래마다    → 가중치 자동 재조정
3주 연속 40%↓ → ISOLATED 상태 (시뮬만 참여)
매주 일요일   → Evolution Agent 전략 재검토
필요 시       → Recruiter Agent 신규 에이전트 영입
```

---

## 다음 세션 시작 방법

노트북에서 Claude Code 열고:

```
"PLAN.md 읽고 PHASE 10부터 시작해줘"
```
