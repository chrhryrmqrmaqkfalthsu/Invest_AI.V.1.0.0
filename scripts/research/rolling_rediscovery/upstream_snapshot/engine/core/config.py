"""
설정 로더 모듈
- policy.yaml (운영 정책)
- .env (비밀 키)
- 전역 싱글톤으로 캐시
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


# 프로젝트 루트 경로 (engine/core/config.py 기준 2단계 위)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

POLICY_PATH = CONFIG_DIR / "policy.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


class Config:
    """전역 설정 싱글톤"""
    _instance: Optional["Config"] = None
    _policy: Optional[Dict[str, Any]] = None
    _env_loaded: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._env_loaded:
            self._load_env()
            self._load_policy()
            Config._env_loaded = True

    def _load_env(self) -> None:
        """.env 파일 로드"""
        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
        else:
            print(f"⚠️  .env not found at {ENV_PATH} (사용 가능하지만 비밀 키 없음)")

    def _load_policy(self) -> None:
        """policy.yaml 로드"""
        if not POLICY_PATH.exists():
            raise FileNotFoundError(
                f"policy.yaml not found at {POLICY_PATH}. "
                "Create it before running the engine."
            )
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            Config._policy = yaml.safe_load(f)

    # ---------- 정책 접근 ----------
    @property
    def policy(self) -> Dict[str, Any]:
        return self._policy or {}

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """
        'risk.daily_loss_limit_pct' 같은 점 표기로 중첩값 접근
        """
        node: Any = self._policy
        for part in dotted_key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    # ---------- 환경변수 접근 ----------
    @staticmethod
    def env(key: str, default: Optional[str] = None) -> Optional[str]:
        return os.environ.get(key, default)

    @staticmethod
    def env_required(key: str) -> str:
        val = os.environ.get(key)
        if val is None or val == "":
            raise RuntimeError(f"Required env var missing: {key}")
        return val

    # ---------- 경로 헬퍼 ----------
    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        return DATA_DIR

    def symbol_dir(self, ticker: str) -> Path:
        d = DATA_DIR / "symbols" / ticker
        d.mkdir(parents=True, exist_ok=True)
        return d

    def system_dir(self) -> Path:
        d = DATA_DIR / "_system"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def logs_dir(self) -> Path:
        d = DATA_DIR / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def backups_dir(self) -> Path:
        d = DATA_DIR / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d


# 모듈 임포트 시 즉시 사용 가능한 전역 인스턴스
config = Config()


if __name__ == "__main__":
    # 직접 실행 시 설정 확인 출력
    print("=" * 50)
    print("KingMaker Config Test")
    print("=" * 50)
    print(f"Project root: {config.project_root}")
    print(f"Data dir:     {config.data_dir}")
    print(f"Policy keys:  {list(config.policy.keys())}")
    print(f"Mode:         {config.get('mode', 'unknown')}")
    print(f"Daily loss:   {config.get('risk.daily_loss_limit_pct', 'N/A')}%")
    print("=" * 50)
