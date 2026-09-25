"""
로깅 모듈 (loguru 기반)
- 콘솔: 컬러 출력
- 파일: data/logs/kingmaker.log (일일 회전, 30일 보관)
- 에러 별도: data/logs/error.log
"""
import sys
from pathlib import Path

from loguru import logger

from engine.core.config import config


# 기본 핸들러 제거 (중복 방지)
logger.remove()

# 로그 디렉토리 확보
LOG_DIR = config.logs_dir()
MAIN_LOG = LOG_DIR / "kingmaker.log"
ERROR_LOG = LOG_DIR / "error.log"
TRADE_LOG = LOG_DIR / "trades.log"

# 로그 레벨 (.env로 조정 가능)
LOG_LEVEL = config.env("LOG_LEVEL", "INFO")


# ---------- 콘솔 출력 ----------
logger.add(
    sys.stdout,
    level=LOG_LEVEL,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
    colorize=True,
    enqueue=True,
)

# ---------- 메인 파일 로그 (INFO 이상, 30일 보관) ----------
logger.add(
    MAIN_LOG,
    level="INFO",
    rotation="00:00",         # 매일 자정 회전
    retention="30 days",
    compression="zip",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    ),
    encoding="utf-8",
    enqueue=True,
)

# ---------- 에러 전용 로그 (ERROR 이상, 90일 보관) ----------
logger.add(
    ERROR_LOG,
    level="ERROR",
    rotation="00:00",
    retention="90 days",
    compression="zip",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
        "{name}:{function}:{line} | {message}\n{exception}"
    ),
    encoding="utf-8",
    backtrace=True,
    diagnose=True,
    enqueue=True,
)

# ---------- 거래 전용 로그 (영구 보관) ----------
logger.add(
    TRADE_LOG,
    level="INFO",
    rotation="100 MB",
    retention=None,           # 영구 보관
    compression="zip",
    filter=lambda record: record["extra"].get("trade") is True,
    format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
    encoding="utf-8",
    enqueue=True,
)


def get_logger(name: str = ""):
    """모듈별 로거 반환 (네임스페이스 바인딩)"""
    if name:
        return logger.bind(module=name)
    return logger


def trade_logger():
    """거래 로그 전용 (trades.log에만 기록)"""
    return logger.bind(trade=True)


if __name__ == "__main__":
    log = get_logger("test")
    log.debug("디버그 메시지")
    log.info("정보 메시지")
    log.warning("경고 메시지")
    log.error("에러 메시지")
    log.success("성공 메시지")

    trade_logger().info("BUY 379800 4주 @25615")

    print()
    print(f"✅ 로그 파일 위치:")
    print(f"  메인: {MAIN_LOG}")
    print(f"  에러: {ERROR_LOG}")
    print(f"  거래: {TRADE_LOG}")
