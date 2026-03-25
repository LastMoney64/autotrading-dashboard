"""
MessageBus — 에이전트 간 통신 채널

에이전트끼리 메시지를 주고받는 중앙 메시지 버스.
- 1:1 메시지 (Judge → Risk 등)
- 브로드캐스트 (전체 공지)
- 역할 기반 전송 (모든 분석 에이전트에게)
- 메시지 히스토리 보관
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Awaitable
from collections import defaultdict
import asyncio


class MessageType(str, Enum):
    """메시지 유형"""
    # 분석 관련
    ANALYSIS_REQUEST = "analysis_request"       # 분석 요청
    ANALYSIS_RESULT = "analysis_result"         # 분석 결과
    # 토론 관련
    DEBATE_START = "debate_start"               # 토론 시작
    DEBATE_OPINION = "debate_opinion"           # 토론 의견
    DEBATE_SUMMARY = "debate_summary"           # 토론 요약
    # 판단 관련
    JUDGMENT = "judgment"                       # Judge 판결
    RISK_REVIEW = "risk_review"                 # Risk 검토 결과
    VETO = "veto"                               # 거부권 발동
    # 실행 관련
    EXECUTE_ORDER = "execute_order"             # 주문 실행
    ORDER_RESULT = "order_result"               # 주문 결과
    TRADE_CLOSED = "trade_closed"               # 거래 종료
    # 시스템
    SYSTEM_ALERT = "system_alert"               # 시스템 경고
    AGENT_STATUS_CHANGE = "agent_status_change" # 에이전트 상태 변경
    EVOLUTION_UPDATE = "evolution_update"        # 진화 업데이트
    MEMORY_QUERY = "memory_query"               # 메모리 검색 요청
    MEMORY_RESPONSE = "memory_response"         # 메모리 검색 결과


@dataclass
class Message:
    """에이전트 간 메시지"""
    msg_type: MessageType
    sender_id: str                    # 보낸 에이전트 ID ("system" 가능)
    payload: dict                     # 메시지 내용
    receiver_id: Optional[str] = None # None이면 브로드캐스트
    timestamp: datetime = field(default_factory=datetime.utcnow)
    msg_id: str = field(default="")

    def __post_init__(self):
        if not self.msg_id:
            ts = self.timestamp.strftime("%Y%m%d%H%M%S%f")
            self.msg_id = f"{self.sender_id}_{ts}"

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type.value,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


# 핸들러 타입: async 함수로 Message를 받음
MessageHandler = Callable[[Message], Awaitable[None]]


class MessageBus:
    """에이전트 간 메시지 버스"""

    def __init__(self, max_history: int = 1000):
        self._max_history = max_history
        # agent_id → [handler, ...] (1:1 메시지용)
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        # msg_type → [handler, ...] (토픽 구독)
        self._topic_handlers: dict[MessageType, list[MessageHandler]] = defaultdict(list)
        # 메시지 히스토리
        self._history: list[Message] = []

    # ── 구독 ────────────────────────────────────────────

    def subscribe(self, agent_id: str, handler: MessageHandler) -> None:
        """특정 에이전트가 자기에게 오는 메시지를 구독"""
        self._handlers[agent_id].append(handler)

    def subscribe_topic(self, msg_type: MessageType, handler: MessageHandler) -> None:
        """특정 메시지 타입을 구독 (브로드캐스트/토픽 기반)"""
        self._topic_handlers[msg_type].append(handler)

    def unsubscribe(self, agent_id: str) -> None:
        """에이전트의 모든 구독 해제"""
        self._handlers.pop(agent_id, None)

    # ── 메시지 전송 ─────────────────────────────────────

    async def send(self, message: Message) -> None:
        """메시지 전송 (1:1 또는 브로드캐스트)"""
        # 히스토리에 저장
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        tasks = []

        # 1:1 메시지
        if message.receiver_id:
            for handler in self._handlers.get(message.receiver_id, []):
                tasks.append(handler(message))

        # 토픽 구독자에게 전달
        for handler in self._topic_handlers.get(message.msg_type, []):
            tasks.append(handler(message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast(
        self,
        msg_type: MessageType,
        sender_id: str,
        payload: dict,
    ) -> None:
        """전체 브로드캐스트"""
        message = Message(
            msg_type=msg_type,
            sender_id=sender_id,
            payload=payload,
            receiver_id=None,
        )
        await self.send(message)

    async def send_to(
        self,
        msg_type: MessageType,
        sender_id: str,
        receiver_id: str,
        payload: dict,
    ) -> None:
        """특정 에이전트에게 1:1 전송"""
        message = Message(
            msg_type=msg_type,
            sender_id=sender_id,
            receiver_id=receiver_id,
            payload=payload,
        )
        await self.send(message)

    # ── 히스토리 조회 ───────────────────────────────────

    def get_history(
        self,
        msg_type: Optional[MessageType] = None,
        sender_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[Message]:
        """메시지 히스토리 조회 (필터링)"""
        messages = self._history
        if msg_type:
            messages = [m for m in messages if m.msg_type == msg_type]
        if sender_id:
            messages = [m for m in messages if m.sender_id == sender_id]
        return messages[-limit:]

    def get_recent_debate(self, limit: int = 20) -> list[Message]:
        """최근 토론 메시지만 조회"""
        debate_types = {
            MessageType.DEBATE_START,
            MessageType.DEBATE_OPINION,
            MessageType.DEBATE_SUMMARY,
        }
        return [
            m for m in self._history[-limit * 3:]
            if m.msg_type in debate_types
        ][-limit:]

    def clear_history(self) -> None:
        """히스토리 초기화"""
        self._history.clear()

    @property
    def history_count(self) -> int:
        return len(self._history)
