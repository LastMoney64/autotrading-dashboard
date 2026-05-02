"""
Realistic Simulation — Paper 모드를 Live와 일치시키는 마찰 시뮬레이터

실전과 똑같이 만들기 위해 다음을 시뮬:
1. 유동성 기반 동적 슬리피지 (LP 작을수록 큰 슬리피지)
2. 추적자 프리미엄 (우리가 따라 살 때 가격 이미 위)
3. 가스비
4. MEV sandwich attack

목표: Paper 결과 ≈ Live 결과
"""

import random


def calculate_slippage(liquidity_usd: float) -> float:
    """
    유동성 기반 동적 슬리피지 (한 방향, 매수 또는 매도)

    솔라나 밈코인 평균 슬리피지 (Jupiter aggregator 통계 기반):
    - LP < $5K:    10% (매우 작은 풀, 큰 손실)
    - $5K~$20K:    7%
    - $20K~$100K:  5% (default)
    - $100K~$500K: 3%
    - $500K+:      2% (큰 풀, 안전)

    Returns: 슬리피지 비율 (0.02 = 2%)
    """
    if liquidity_usd <= 0:
        return 0.10  # 알 수 없으면 보수적
    if liquidity_usd < 5000:
        return 0.10
    if liquidity_usd < 20000:
        return 0.07
    if liquidity_usd < 100000:
        return 0.05
    if liquidity_usd < 500000:
        return 0.03
    return 0.02


def calculate_tracker_premium(signal_type: str = None, mint: str = None) -> float:
    """
    추적자 프리미엄 (SmartMoney 봇 전용)

    추적자가 매수한 직후 우리가 따라 매수하면 가격이 이미 +X% 위.
    CONSENSUS (여러 명 동시 매수): 더 큰 프리미엄
    SOLO (1명 매수): 작은 프리미엄

    mint가 주어지면 그 토큰별 일정한 프리미엄 (재현 가능, paper/live 일치성 향상)

    Returns: 프리미엄 비율 (0.05 = 5% 더 비싸게 매수)
    """
    # mint 기반 seed로 재현성 확보 (같은 토큰은 항상 같은 프리미엄)
    rng = random.Random(hash(mint)) if mint else random

    if signal_type == "CONSENSUS":
        return rng.uniform(0.05, 0.15)  # 5~15%
    elif signal_type == "SOLO_HIGH":
        return rng.uniform(0.02, 0.08)  # 2~8%
    else:
        return rng.uniform(0.0, 0.05)  # 0~5%


GAS_FEE_SOL = 0.0005  # 거래당 가스비 (Solana 평균)


def apply_buy_friction(
    raw_token_amount: int,
    liquidity_usd: float,
    signal_type: str = None,
    is_smart_money_copy: bool = False,
    mint: str = None,
) -> tuple[int, float]:
    """
    매수 시 현실 마찰 적용

    Args:
        raw_token_amount: Jupiter 견적 받은 토큰 양 (이상적)
        liquidity_usd: 토큰의 LP 유동성
        signal_type: "CONSENSUS" / "SOLO_HIGH" / None
        is_smart_money_copy: SmartMoney 카피인지 (추적자 프리미엄 적용)
        mint: 토큰 주소 (seed 고정용 — 재현 가능 시뮬)

    Returns: (실제 받는 토큰 양, 적용된 총 손실 비율)
    """
    slippage = calculate_slippage(liquidity_usd)

    if is_smart_money_copy:
        premium = calculate_tracker_premium(signal_type, mint=mint)
    else:
        premium = 0.0

    total_loss = slippage + premium  # 슬리피지 + 추적자 프리미엄
    actual_token = int(raw_token_amount * (1 - total_loss))
    return actual_token, total_loss


def apply_sell_friction(
    raw_sol_received: float,
    liquidity_usd: float,
) -> tuple[float, float]:
    """
    매도 시 현실 마찰 적용 (슬리피지 + 가스비)

    Args:
        raw_sol_received: 견적 받을 SOL (이상적)
        liquidity_usd: 토큰의 LP 유동성

    Returns: (실제 받는 SOL, 슬리피지 비율)
    """
    slippage = calculate_slippage(liquidity_usd)
    sol_after_slippage = raw_sol_received * (1 - slippage)
    sol_received = max(0, sol_after_slippage - GAS_FEE_SOL)
    return sol_received, slippage
