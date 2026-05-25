"""
텔레그램 봇 라이브 테스트 스크립트
- PaperBroker + SafetyLayer 연결
- Ctrl+C로 종료

사용:
    PYTHONPATH=. python scripts/run_bot.py
    → 휴대폰에서 /start 보내기
"""
import logging
import signal
import sys

from engine.live.broker.factory import make_broker
from engine.live.safety.layer import SafetyLayer
from engine.live.telegram.bot import TelegramBot
from engine.live.telegram.notifier import TelegramNotifier

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("run_bot")


def main():
    log.info("브로커 초기화 (paper)...")
    broker = make_broker(force_mode="paper")

    log.info("SafetyLayer 초기화...")
    safety = SafetyLayer(broker=broker)

    log.info("TelegramBot 초기화...")
    notifier = TelegramNotifier()
    bot = TelegramBot(broker=broker, safety=safety, notifier=notifier)

    notifier.send(
        "🚀 *Kingmaker 봇 가동* (paper 모드, 라이브 테스트)\n"
        "/help 로 명령어 확인"
    )

    # Ctrl+C 처리
    def shutdown(signum, frame):
        log.info("종료 신호 수신, 정리 중...")
        bot.stop()
        notifier.send("🛑 봇 종료됨")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("polling 시작 — 휴대폰에서 명령 보내세요 (Ctrl+C로 종료)")
    bot.start_polling(blocking=True)


if __name__ == "__main__":
    main()
