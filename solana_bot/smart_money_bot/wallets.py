"""
스마트머니 추적 지갑 리스트

GMGN.AI에서 수익률 Top 검증된 지갑들.
사용자가 추가/제거 가능.
"""

# 검증된 솔라나 스마트머니 지갑 (공개 데이터 기반)
# 출처: GMGN.AI Top Traders, Cielo Finance leaderboard, Twitter alpha groups
TRACKED_WALLETS = [
    # ── Top 수익률 지갑 (참고용 시작 리스트) ──
    # win_rate, avg_pnl_30d, tag 등은 추후 자기학습으로 자동 업데이트
    {
        "address": "BCagckXeMChUKrHEfehxdMZQK2zX4Lu9rkRtkV3xmgyq",
        "tag": "smart_trader",
        "win_rate": 0.65,  # 초기 추정값
        "weight": 1.0,
        "active": True,
    },
    {
        "address": "CyJj5ejJAUveDXnLduJbkvwjxcmWJNqCuB9DR7AExpkc",
        "tag": "kol_alpha",
        "win_rate": 0.62,
        "weight": 1.0,
        "active": True,
    },
    {
        "address": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "tag": "early_buyer",
        "win_rate": 0.60,
        "weight": 1.0,
        "active": True,
    },
    {
        "address": "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE",
        "tag": "memecoin_sniper",
        "win_rate": 0.58,
        "weight": 0.9,
        "active": True,
    },
    {
        "address": "DfMxre4cKmvogbLrPigxmibVTTQDuzjdXojWzjCXXhzj",
        "tag": "whale_trader",
        "win_rate": 0.55,
        "weight": 0.9,
        "active": True,
    },
    # 사용자 추가 지갑은 여기 추가
    # {
    #     "address": "...",
    #     "tag": "custom_alpha",
    #     "win_rate": 0.50,
    #     "weight": 1.0,
    #     "active": True,
    # },
]


def get_active_wallets() -> list[dict]:
    """활성 지갑만 반환"""
    return [w for w in TRACKED_WALLETS if w.get("active", False)]


def update_wallet_stats(address: str, won: bool):
    """매매 결과로 지갑 통계 자동 업데이트 (자기학습)

    이긴 매매: weight ↑, win_rate ↑
    진 매매: weight ↓, win_rate ↓
    win_rate가 0.4 이하로 떨어지면 자동 비활성화
    """
    for w in TRACKED_WALLETS:
        if w["address"] == address:
            old_wr = w.get("win_rate", 0.5)
            # EMA 방식 업데이트 (최근 매매 가중)
            new_wr = old_wr * 0.9 + (1.0 if won else 0.0) * 0.1
            w["win_rate"] = round(new_wr, 3)

            if won:
                w["weight"] = min(2.0, w.get("weight", 1.0) * 1.05)
            else:
                w["weight"] = max(0.3, w.get("weight", 1.0) * 0.92)

            # 5번 연속 손실급 (win_rate 0.4 이하) → 비활성화
            if new_wr < 0.40:
                w["active"] = False

            return True
    return False
