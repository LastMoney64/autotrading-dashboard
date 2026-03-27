"""
Database — SQLite 연결 및 테이블 관리

모든 거래 에피소드, 에이전트 성과, 패턴 메모리를 저장.
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class Database:
    """SQLite 데이터베이스 관리자"""

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = str(Path(__file__).parent.parent / "data" / "trading.db")
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_tables(self):
        c = self.conn
        c.executescript("""
            -- 거래 에피소드 (의사결정 사이클 전체)
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                -- 분석 결과 요약
                signal_consensus TEXT,    -- JSON {"BUY":5,"SELL":2,"HOLD":2}
                avg_confidence REAL,
                -- Judge 판결
                judge_signal TEXT,
                judge_confidence REAL,
                position_size_pct REAL,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                judge_reasoning TEXT,
                -- Risk 검토
                risk_approved INTEGER,
                risk_score REAL,
                veto_reason TEXT,
                -- 최종
                final_action TEXT,
                -- 거래 결과 (포지션 닫힌 후)
                exit_price REAL,
                pnl_pct REAL,
                pnl_usd REAL,
                closed_at TEXT,
                -- 전체 기록 (JSON)
                full_record TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- 에이전트별 성과 기록
            CREATE TABLE IF NOT EXISTS agent_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                signal TEXT NOT NULL,
                confidence REAL NOT NULL,
                was_correct INTEGER,      -- 1=맞음, 0=틀림, NULL=미정
                pnl_contribution REAL,
                reasoning TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (cycle_id) REFERENCES episodes(cycle_id)
            );

            -- 장기 패턴 메모리
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,   -- "time", "indicator", "whale", "onchain"
                description TEXT NOT NULL,
                conditions TEXT NOT NULL,     -- JSON 조건
                success_rate REAL,
                sample_count INTEGER DEFAULT 0,
                last_seen TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            -- 에이전트 가중치 히스토리
            CREATE TABLE IF NOT EXISTS weight_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                old_weight REAL,
                new_weight REAL,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- 인덱스
            CREATE INDEX IF NOT EXISTS idx_episodes_symbol ON episodes(symbol);
            CREATE INDEX IF NOT EXISTS idx_episodes_action ON episodes(final_action);
            CREATE INDEX IF NOT EXISTS idx_agent_perf_agent ON agent_performance(agent_id);
            CREATE INDEX IF NOT EXISTS idx_agent_perf_cycle ON agent_performance(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_patterns_type ON patterns(pattern_type);
        """)
        c.commit()

    # ── 에피소드 CRUD ────────────────────────────────────

    def save_episode(self, record_dict: dict) -> int:
        """DebateRecord.to_dict()를 저장"""
        consensus = record_dict.get("signal_consensus", {})
        judgment = record_dict.get("judgment") or {}
        risk = record_dict.get("risk_review") or {}

        cur = self.conn.execute("""
            INSERT OR REPLACE INTO episodes (
                cycle_id, symbol, started_at, finished_at,
                signal_consensus, avg_confidence,
                judge_signal, judge_confidence, position_size_pct,
                entry_price, stop_loss, take_profit, judge_reasoning,
                risk_approved, risk_score, veto_reason,
                final_action, full_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_dict["cycle_id"],
            record_dict["symbol"],
            record_dict["started_at"],
            record_dict.get("finished_at"),
            json.dumps(consensus),
            record_dict.get("avg_confidence", 0),
            judgment.get("signal"),
            judgment.get("confidence"),
            judgment.get("position_size_pct"),
            judgment.get("entry_price"),
            judgment.get("stop_loss"),
            judgment.get("take_profit"),
            judgment.get("reasoning"),
            1 if risk.get("approved") else 0,
            risk.get("risk_score"),
            risk.get("veto_reason"),
            record_dict.get("final_action"),
            json.dumps(record_dict, ensure_ascii=False),
        ))
        self.conn.commit()
        return cur.lastrowid

    def update_trade_result(
        self, cycle_id: str, exit_price: float, pnl_pct: float, pnl_usd: float
    ):
        """거래 결과 업데이트 (포지션 닫힌 후)"""
        self.conn.execute("""
            UPDATE episodes
            SET exit_price = ?, pnl_pct = ?, pnl_usd = ?, closed_at = ?
            WHERE cycle_id = ?
        """, (exit_price, pnl_pct, pnl_usd, datetime.utcnow().isoformat(), cycle_id))
        self.conn.commit()

    def get_recent_episodes(self, symbol: str = "", limit: int = 50) -> list[dict]:
        """최근 에피소드 조회"""
        if symbol:
            rows = self.conn.execute(
                "SELECT * FROM episodes WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM episodes ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_similar_episodes(
        self,
        judge_signal: str,
        avg_confidence_range: tuple[float, float],
        limit: int = 5,
    ) -> list[dict]:
        """유사 상황 검색 (Judge 신호 + 확신도 범위)"""
        rows = self.conn.execute("""
            SELECT * FROM episodes
            WHERE judge_signal = ?
              AND avg_confidence BETWEEN ? AND ?
              AND pnl_pct IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (judge_signal, avg_confidence_range[0], avg_confidence_range[1], limit)).fetchall()
        return [dict(r) for r in rows]

    # ── 에이전트 성과 CRUD ────────────────────────────────

    def save_agent_performance(
        self, agent_id: str, cycle_id: str, signal: str,
        confidence: float, reasoning: str = ""
    ):
        self.conn.execute("""
            INSERT INTO agent_performance (agent_id, cycle_id, signal, confidence, reasoning)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, cycle_id, signal, confidence, reasoning))
        self.conn.commit()

    def update_agent_correctness(self, cycle_id: str, correct_signal: str) -> tuple[list[str], list[str]]:
        """
        거래 결과 확정 후 에이전트 정답 여부 업데이트

        Returns: (agents_correct, agents_wrong)
        """
        self.conn.execute("""
            UPDATE agent_performance
            SET was_correct = CASE WHEN signal = ? THEN 1 ELSE 0 END
            WHERE cycle_id = ?
        """, (correct_signal, cycle_id))
        self.conn.commit()

        # 정답/오답 에이전트 리스트 반환
        rows = self.conn.execute("""
            SELECT agent_id, was_correct FROM agent_performance
            WHERE cycle_id = ?
        """, (cycle_id,)).fetchall()

        agents_correct = [r["agent_id"] for r in rows if r["was_correct"] == 1]
        agents_wrong = [r["agent_id"] for r in rows if r["was_correct"] == 0]
        return agents_correct, agents_wrong

    def get_agent_stats(self, agent_id: str, last_n: int = 100) -> dict:
        """에이전트 통계"""
        rows = self.conn.execute("""
            SELECT signal, confidence, was_correct, pnl_contribution
            FROM agent_performance
            WHERE agent_id = ? AND was_correct IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (agent_id, last_n)).fetchall()

        if not rows:
            return {"agent_id": agent_id, "total": 0, "win_rate": 0, "avg_confidence": 0}

        total = len(rows)
        wins = sum(1 for r in rows if r["was_correct"] == 1)
        avg_conf = sum(r["confidence"] for r in rows) / total

        return {
            "agent_id": agent_id,
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total if total > 0 else 0,
            "avg_confidence": round(avg_conf, 3),
        }

    # ── 패턴 CRUD ────────────────────────────────────────

    def save_pattern(
        self, pattern_type: str, description: str,
        conditions: dict, success_rate: float, sample_count: int
    ):
        self.conn.execute("""
            INSERT INTO patterns (pattern_type, description, conditions, success_rate, sample_count, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (pattern_type, description, json.dumps(conditions), success_rate, sample_count, datetime.utcnow().isoformat()))
        self.conn.commit()

    def get_patterns(self, pattern_type: str = "", min_samples: int = 5) -> list[dict]:
        if pattern_type:
            rows = self.conn.execute(
                "SELECT * FROM patterns WHERE pattern_type = ? AND sample_count >= ? ORDER BY success_rate DESC",
                (pattern_type, min_samples)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM patterns WHERE sample_count >= ? ORDER BY success_rate DESC",
                (min_samples,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 가중치 히스토리 ───────────────────────────────────

    def save_weight_change(self, agent_id: str, old_w: float, new_w: float, reason: str = ""):
        self.conn.execute("""
            INSERT INTO weight_history (agent_id, old_weight, new_weight, reason)
            VALUES (?, ?, ?, ?)
        """, (agent_id, old_w, new_w, reason))
        self.conn.commit()

    # ── 통계 ──────────────────────────────────────────────

    def get_overall_stats(self) -> dict:
        """전체 시스템 통계"""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
                AVG(pnl_pct) as avg_pnl,
                SUM(pnl_pct) as total_pnl,
                MAX(pnl_pct) as best_trade,
                MIN(pnl_pct) as worst_trade
            FROM episodes WHERE pnl_pct IS NOT NULL
        """).fetchone()
        return dict(row) if row else {}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
