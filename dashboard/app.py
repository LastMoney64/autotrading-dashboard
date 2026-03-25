"""
Dashboard — FastAPI 대시보드 서버

트레이딩 시스템의 모든 상태를 실시간 모니터링.
main.py와 같은 프로세스에서 실행 가능.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.agent_registry import AgentRegistry
from core.base_agent import AgentRole
from config.settings import Settings
from db.database import Database
from evolution.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    registry: AgentRegistry,
    db: Database,
    tracker: PerformanceTracker,
    settings: Settings,
) -> FastAPI:
    """FastAPI 앱 생성"""

    app = FastAPI(title="AutoTrading Dashboard", version="1.0.0")

    # 정적 파일
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # WebSocket 연결 관리
    ws_clients: list[WebSocket] = []

    # ── 메인 페이지 ──────────────────────────────────────

    @app.get("/")
    async def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    # ── API: 시스템 개요 ─────────────────────────────────

    @app.get("/api/overview")
    async def api_overview():
        overall = db.get_overall_stats()
        summary = registry.get_summary()

        total = overall.get("total_trades", 0) or 0
        wins = overall.get("wins", 0) or 0

        return {
            "total_pnl": round(overall.get("total_pnl", 0) or 0, 2),
            "avg_pnl": round(overall.get("avg_pnl", 0) or 0, 2),
            "total_trades": total,
            "wins": wins,
            "losses": overall.get("losses", 0) or 0,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "best_trade": round(overall.get("best_trade", 0) or 0, 2),
            "worst_trade": round(overall.get("worst_trade", 0) or 0, 2),
            "active_agents": summary["active"],
            "isolated_agents": summary["isolated"],
            "probation_agents": summary["probation"],
            "total_agents": summary["total_agents"],
            "trading_pair": settings.trading_pair,
        }

    # ── API: 에이전트 ────────────────────────────────────

    @app.get("/api/agents")
    async def api_agents():
        reports = tracker.analyze_all()
        return [r.to_dict() for r in sorted(reports, key=lambda r: r.win_rate, reverse=True)]

    @app.get("/api/agents/{agent_id}")
    async def api_agent_detail(agent_id: str):
        agent = registry.get(agent_id)
        if not agent:
            return {"error": "Agent not found"}

        report = tracker.analyze_agent(agent_id)
        stats = db.get_agent_stats(agent_id, last_n=100)

        # 최근 거래 기록
        recent = db.conn.execute("""
            SELECT cycle_id, signal, confidence, was_correct, created_at
            FROM agent_performance
            WHERE agent_id = ?
            ORDER BY id DESC LIMIT 20
        """, (agent_id,)).fetchall()

        return {
            "report": report.to_dict(),
            "config": {
                "model": agent.config.model,
                "parameters": agent.config.parameters,
            },
            "recent_trades": [dict(r) for r in recent],
        }

    @app.get("/api/weights")
    async def api_weights():
        normalized = registry.get_normalized_weights()
        result = []
        for aid, weight in sorted(normalized.items(), key=lambda x: x[1], reverse=True):
            agent = registry.get(aid)
            result.append({
                "agent_id": aid,
                "name": agent.name if agent else aid,
                "raw_weight": round(agent.weight, 2) if agent else 0,
                "normalized": round(weight, 3),
            })
        return result

    # ── API: 거래 내역 ───────────────────────────────────

    @app.get("/api/trades")
    async def api_trades(limit: int = 50):
        episodes = db.get_recent_episodes(limit=limit)
        trades = []
        for ep in episodes:
            consensus = {}
            try:
                consensus = json.loads(ep.get("signal_consensus", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            trades.append({
                "cycle_id": ep.get("cycle_id", ""),
                "symbol": ep.get("symbol", ""),
                "signal": ep.get("judge_signal", ""),
                "confidence": ep.get("judge_confidence", 0),
                "position_size": ep.get("position_size_pct", 0),
                "entry_price": ep.get("entry_price"),
                "stop_loss": ep.get("stop_loss"),
                "take_profit": ep.get("take_profit"),
                "exit_price": ep.get("exit_price"),
                "pnl_pct": ep.get("pnl_pct"),
                "pnl_usd": ep.get("pnl_usd"),
                "risk_approved": bool(ep.get("risk_approved")),
                "final_action": ep.get("final_action", ""),
                "consensus": consensus,
                "reasoning": (ep.get("judge_reasoning", "") or "")[:200],
                "created_at": ep.get("created_at", ""),
            })
        return trades

    @app.get("/api/trades/chart")
    async def api_trades_chart():
        """누적 PnL 차트 데이터"""
        rows = db.conn.execute("""
            SELECT created_at, pnl_pct
            FROM episodes
            WHERE pnl_pct IS NOT NULL
            ORDER BY id ASC
        """).fetchall()

        cumulative = 0
        data = []
        for r in rows:
            cumulative += r["pnl_pct"]
            data.append({
                "date": r["created_at"],
                "pnl": round(r["pnl_pct"], 2),
                "cumulative": round(cumulative, 2),
            })
        return data

    # ── API: 토론 기록 ───────────────────────────────────

    @app.get("/api/debates/latest")
    async def api_latest_debate():
        episodes = db.get_recent_episodes(limit=1)
        if not episodes:
            return {"debate": None}

        ep = episodes[0]
        full_record = {}
        try:
            full_record = json.loads(ep.get("full_record", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

        # 분석 결과 추출
        analyses = full_record.get("analyses", [])
        debate_rounds = full_record.get("debate_rounds", [])
        moderator_summary = full_record.get("moderator_summary", "")

        return {
            "cycle_id": ep.get("cycle_id", ""),
            "symbol": ep.get("symbol", ""),
            "analyses": analyses,
            "debate_rounds": debate_rounds,
            "moderator_summary": moderator_summary,
            "judgment": full_record.get("judgment"),
            "risk_review": full_record.get("risk_review"),
            "final_action": ep.get("final_action", ""),
            "created_at": ep.get("created_at", ""),
        }

    # ── API: 진화 히스토리 ───────────────────────────────

    @app.get("/api/evolution/history")
    async def api_evolution_history(limit: int = 50):
        rows = db.conn.execute("""
            SELECT agent_id, old_weight, new_weight, reason, created_at
            FROM weight_history
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/evolution/isolations")
    async def api_isolations():
        """격리/복귀 이력 (registry log에서)"""
        log = registry.registration_log
        events = [
            e for e in log
            if e.get("action") in ("isolate", "activate", "probation")
        ]
        return events[-20:]  # 최근 20건

    # ── WebSocket: 실시간 업데이트 ────────────────────────

    @app.websocket("/ws/live")
    async def websocket_live(ws: WebSocket):
        await ws.accept()
        ws_clients.append(ws)
        try:
            while True:
                # 클라이언트가 연결 유지하는 동안 대기
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_clients.remove(ws)

    # 외부에서 호출할 수 있도록 broadcast 함수 노출
    async def broadcast_update(event_type: str, data: dict):
        """모든 WebSocket 클라이언트에 업데이트 전송"""
        message = json.dumps({"type": event_type, "data": data, "ts": datetime.utcnow().isoformat()})
        disconnected = []
        for ws in ws_clients:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            ws_clients.remove(ws)

    app.broadcast_update = broadcast_update

    return app
