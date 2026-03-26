"""
TradeFeedback — 거래 완료 후 피드백 & 자기발전 엔진

매 거래 청산 후:
1. 결과 기록 (승/패, PnL, 진입 조건)
2. 패턴 분석 (어떤 조건에서 이기고 지는지)
3. 전략 파라미터 자동 조정
4. 에이전트 가중치 업데이트

전부 코드 기반 — 비용 $0
"""

import logging
from datetime import datetime
from typing import Optional
from db.database import Database

logger = logging.getLogger(__name__)


class TradeFeedback:
    """거래 완료 후 피드백 & 자기발전"""

    def __init__(self, db: Database, fee_taker_pct: float = 0.05):
        self.db = db
        self.fee_taker_pct = fee_taker_pct  # Taker 수수료 %
        self.trade_history: list[dict] = []
        self.stats = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl_pct": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "consecutive_losses": 0,
            "max_consecutive_losses": 0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
        }
        # 조건별 승률 추적
        self.condition_stats: dict[str, dict] = {}

    def record_trade(self, trade: dict) -> dict:
        """
        거래 완료 기록 + 피드백 생성

        trade = {
            "symbol": "BTC/USDT:USDT",
            "side": "buy" / "sell",
            "entry_price": 69000,
            "exit_price": 70000,
            "pnl_pct": 1.45,  # 레버리지 적용 전
            "pnl_pct_leveraged": 50.7,  # 레버리지 적용 후
            "pnl_usd": 20.5,
            "leverage": 35,
            "margin": 40.0,
            "entry_signals": ["RSI 과매도(20)", "강한 상승추세(ADX 36)"],
            "entry_direction": "BUY",
            "entry_confidence": 0.82,
            "exit_reason": "tp" / "sl" / "active_exit" / "manual",
            "hold_duration_seconds": 3600,
            "agents_correct": ["trend", "momentum"],  # 맞춘 에이전트
            "agents_wrong": ["whale", "onchain"],  # 틀린 에이전트
        }
        """
        # 수수료 차감 (진입 + 청산 = 2회 Taker)
        leverage = trade.get("leverage", 1)
        fee_impact_pct = self.fee_taker_pct * 2 * leverage / 100  # 마진 대비 수수료 %
        raw_pnl = trade.get("pnl_pct_leveraged", 0)
        pnl = raw_pnl - (fee_impact_pct * 100)  # 수수료 차감
        is_win = pnl > 0

        trade["fee_pct"] = fee_impact_pct * 100
        trade["pnl_after_fee"] = pnl

        # 기본 통계 업데이트
        self.stats["total_trades"] += 1
        self.stats["total_pnl_pct"] += pnl

        if is_win:
            self.stats["wins"] += 1
            self.stats["consecutive_losses"] = 0
            self.stats["best_trade"] = max(self.stats["best_trade"], pnl)
        else:
            self.stats["losses"] += 1
            self.stats["consecutive_losses"] += 1
            self.stats["max_consecutive_losses"] = max(
                self.stats["max_consecutive_losses"],
                self.stats["consecutive_losses"]
            )
            self.stats["worst_trade"] = min(self.stats["worst_trade"], pnl)

        # 평균 승/패 PnL
        wins = self.stats["wins"]
        losses = self.stats["losses"]
        if is_win and wins > 0:
            prev_avg = self.stats["avg_win_pct"]
            self.stats["avg_win_pct"] = prev_avg + (pnl - prev_avg) / wins
        elif not is_win and losses > 0:
            prev_avg = self.stats["avg_loss_pct"]
            self.stats["avg_loss_pct"] = prev_avg + (pnl - prev_avg) / losses

        # 조건별 승률 추적
        for signal in trade.get("entry_signals", []):
            key = self._normalize_signal(signal)
            if key not in self.condition_stats:
                self.condition_stats[key] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
            self.condition_stats[key]["total_pnl"] += pnl
            if is_win:
                self.condition_stats[key]["wins"] += 1
            else:
                self.condition_stats[key]["losses"] += 1

        # 히스토리에 추가
        trade["timestamp"] = datetime.utcnow().isoformat()
        trade["is_win"] = is_win
        self.trade_history.append(trade)

        # 최근 100개만 유지
        if len(self.trade_history) > 100:
            self.trade_history = self.trade_history[-100:]

        # 피드백 생성
        feedback = self._generate_feedback(trade)

        logger.info(
            f"📊 거래 피드백 [{trade.get('symbol', '?')}] "
            f"{'✅ 승' if is_win else '❌ 패'} {pnl:+.2f}% | "
            f"누적 {self.stats['total_trades']}거래 "
            f"승률 {self.win_rate:.0%} | "
            f"{feedback['lesson']}"
        )

        return feedback

    def _generate_feedback(self, trade: dict) -> dict:
        """거래 결과 기반 피드백 생성"""
        is_win = trade.get("is_win", False)
        pnl = trade.get("pnl_pct_leveraged", 0)
        exit_reason = trade.get("exit_reason", "unknown")
        confidence = trade.get("entry_confidence", 0)

        lessons = []
        adjustments = {}

        # 1. 손절 패턴 분석
        if exit_reason == "sl":
            if confidence >= 0.7:
                lessons.append("높은 확신도에서도 손절 → 진입 타이밍 재검토 필요")
                adjustments["reduce_confidence_weight"] = True
            else:
                lessons.append("낮은 확신도에서 손절 → 진입 기준 강화 필요")
                adjustments["increase_min_confidence"] = True

        # 2. 연속 손실
        if self.stats["consecutive_losses"] >= 3:
            lessons.append(f"연속 {self.stats['consecutive_losses']}패 — 시장 국면 변화 가능")
            adjustments["reduce_position_size"] = True
            adjustments["increase_cooldown"] = True

        # 3. 승률 기반
        if self.stats["total_trades"] >= 10:
            wr = self.win_rate
            if wr < 0.4:
                lessons.append(f"승률 {wr:.0%} — 진입 기준 강화 필요")
                adjustments["tighten_entry"] = True
            elif wr > 0.65:
                lessons.append(f"승률 {wr:.0%} — 전략 효과적, 유지")

        # 4. 손익비 체크
        if self.stats["avg_win_pct"] and self.stats["avg_loss_pct"]:
            rr_ratio = abs(self.stats["avg_win_pct"] / (self.stats["avg_loss_pct"] or -1))
            if rr_ratio < 1.5:
                lessons.append(f"손익비 {rr_ratio:.1f} — 익절 확대 또는 손절 축소 필요")
                adjustments["improve_risk_reward"] = True
            elif rr_ratio > 3.0:
                lessons.append(f"손익비 {rr_ratio:.1f} — 우수한 리스크 관리")

        # 5. 능동 청산 효과
        if exit_reason == "active_exit" and is_win:
            lessons.append("능동 청산으로 수익 확보 — 포지션 관리 효과적")

        # 6. 조건별 성과
        best_condition = self._get_best_condition()
        worst_condition = self._get_worst_condition()
        if best_condition:
            lessons.append(f"최고 조건: {best_condition[0]} (승률 {best_condition[1]:.0%})")
        if worst_condition:
            lessons.append(f"최악 조건: {worst_condition[0]} (승률 {worst_condition[1]:.0%})")

        lesson_text = " | ".join(lessons) if lessons else "데이터 수집 중"

        return {
            "is_win": is_win,
            "pnl": pnl,
            "lesson": lesson_text,
            "adjustments": adjustments,
            "stats": self.get_stats(),
        }

    def get_adjustments(self) -> dict:
        """현재 상태 기반 전략 조정값"""
        adj = {}

        # 연속 손실 시 포지션 축소
        if self.stats["consecutive_losses"] >= 3:
            adj["position_size_multiplier"] = 0.5
            adj["reason"] = f"연속 {self.stats['consecutive_losses']}패 — 포지션 50% 축소"
        elif self.stats["consecutive_losses"] >= 5:
            adj["position_size_multiplier"] = 0.25
            adj["reason"] = f"연속 {self.stats['consecutive_losses']}패 — 포지션 75% 축소"
        else:
            adj["position_size_multiplier"] = 1.0

        # 승률 기반 확신도 임계값 조정
        if self.stats["total_trades"] >= 15:
            wr = self.win_rate
            if wr < 0.35:
                adj["min_confidence_override"] = 0.6  # 더 엄격
            elif wr < 0.45:
                adj["min_confidence_override"] = 0.5
            # 승률 좋으면 기본값 유지

        # 최악 조건 필터링
        worst = self._get_worst_condition()
        if worst and worst[1] < 0.3 and worst[2] >= 5:
            adj["avoid_condition"] = worst[0]
            adj["avoid_reason"] = f"{worst[0]} 조건 승률 {worst[1]:.0%} ({worst[2]}거래) — 회피"

        return adj

    def get_stats(self) -> dict:
        """현재 통계"""
        return {
            **self.stats,
            "win_rate": self.win_rate,
            "risk_reward_ratio": self.risk_reward_ratio,
        }

    @property
    def win_rate(self) -> float:
        total = self.stats["total_trades"]
        return self.stats["wins"] / total if total > 0 else 0

    @property
    def risk_reward_ratio(self) -> float:
        avg_win = self.stats["avg_win_pct"]
        avg_loss = self.stats["avg_loss_pct"]
        if avg_loss and avg_loss != 0:
            return abs(avg_win / avg_loss)
        return 0

    def _normalize_signal(self, signal: str) -> str:
        """신호 이름 정규화 (수치 제거)"""
        import re
        return re.sub(r'\(.*?\)', '', signal).strip()

    def _get_best_condition(self) -> Optional[tuple]:
        """승률 최고 조건"""
        best = None
        for key, stats in self.condition_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total
            if best is None or wr > best[1]:
                best = (key, wr, total)
        return best

    def _get_worst_condition(self) -> Optional[tuple]:
        """승률 최악 조건"""
        worst = None
        for key, stats in self.condition_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total
            if worst is None or wr < worst[1]:
                worst = (key, wr, total)
        return worst

    def get_telegram_report(self) -> str:
        """텔레그램 리포트"""
        s = self.stats
        wr = self.win_rate
        rr = self.risk_reward_ratio

        text = f"""📊 <b>트레이딩 성과 리포트</b>

<b>총 거래:</b> {s['total_trades']}회 (✅{s['wins']} / ❌{s['losses']})
<b>승률:</b> {wr:.0%}
<b>누적 PnL:</b> {s['total_pnl_pct']:+.2f}%
<b>손익비:</b> {rr:.2f}
<b>최고:</b> {s['best_trade']:+.2f}%
<b>최악:</b> {s['worst_trade']:+.2f}%
<b>연패:</b> {s['consecutive_losses']} (최대 {s['max_consecutive_losses']})"""

        # 조건별 성과 Top 3
        sorted_conds = sorted(
            [(k, v) for k, v in self.condition_stats.items()
             if v["wins"] + v["losses"] >= 3],
            key=lambda x: x[1]["wins"] / (x[1]["wins"] + x[1]["losses"]),
            reverse=True
        )

        if sorted_conds:
            text += "\n\n<b>조건별 승률:</b>"
            for name, stats in sorted_conds[:5]:
                total = stats["wins"] + stats["losses"]
                wr = stats["wins"] / total
                emoji = "🟢" if wr >= 0.5 else "🔴"
                text += f"\n{emoji} {name}: {wr:.0%} ({total}거래)"

        # 피드백 조정사항
        adj = self.get_adjustments()
        if adj.get("position_size_multiplier", 1) < 1:
            text += f"\n\n⚠️ {adj.get('reason', '')}"
        if adj.get("avoid_condition"):
            text += f"\n🚫 {adj.get('avoid_reason', '')}"

        return text
