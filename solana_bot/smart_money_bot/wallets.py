"""
스마트머니 추적 지갑 리스트

GMGN.AI에서 수익률 Top 검증된 지갑들.
사용자가 추가/제거 가능.

영구 저장: Railway Volume의 wallets.json에 저장됨.
재배포 시 자동 복원 (자동 발굴 + 자기학습 결과 보존).
"""

import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _wallets_file() -> Path:
    """추적 지갑 영구 저장 파일 경로 (Railway Volume)"""
    db_path = os.getenv("DB_PATH", "").strip()
    if db_path:
        return Path(db_path).parent / "tracked_wallets.json"
    return Path(__file__).parent.parent.parent / "data" / "tracked_wallets.json"


# 시드 지갑 (초기 배포 시 사용 — JSON 파일 없을 때)
_SEED_WALLETS = [
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
]


def _load_wallets() -> list[dict]:
    """JSON에서 추적 지갑 로드 — 없으면 시드 사용"""
    path = _wallets_file()
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                wallets = json.load(f)
            if isinstance(wallets, list) and wallets:
                logger.info(f"📂 추적 지갑 {len(wallets)}개 복원 (JSON: {path})")
                return wallets
    except Exception as e:
        logger.warning(f"추적 지갑 로드 실패: {e}")

    # 시드로 초기화
    logger.info(f"🌱 추적 지갑 시드 {len(_SEED_WALLETS)}개로 초기화")
    return [dict(w) for w in _SEED_WALLETS]  # 깊은 복사


def save_wallets():
    """추적 지갑을 JSON으로 저장 (자동 발굴/자기학습 결과 보존)"""
    path = _wallets_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(TRACKED_WALLETS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"추적 지갑 저장 실패: {e}")


# 모듈 로드 시점에 JSON에서 자동 로드 (재배포 시 보존)
TRACKED_WALLETS = _load_wallets()


def get_active_wallets() -> list[dict]:
    """활성 지갑만 반환"""
    return [w for w in TRACKED_WALLETS if w.get("active", False)]


def add_wallet(wallet_dict: dict, save: bool = True) -> bool:
    """추적 지갑 추가 (중복 체크 + 자동 영구 저장)"""
    addr = wallet_dict.get("address", "")
    if not addr:
        return False
    # 중복 체크
    for w in TRACKED_WALLETS:
        if w["address"] == addr:
            return False
    TRACKED_WALLETS.append(wallet_dict)
    if save:
        save_wallets()
    return True


def update_wallet_stats(address: str, won: bool, save: bool = True):
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

            # 자동 영구 저장 (재배포 시 학습 결과 보존)
            if save:
                save_wallets()
            return True
    return False
