"""
KisBroker - 한국투자증권 OpenAPI 연동 (실전/모의)
Part 1: 토큰 + 잔고 + 현재가 (읽기 전용)
Part 2: 주문/취소/조회

주의
----
- 기존 place_buy/place_sell은 국내주식 order-cash 경로를 유지한다.
- 해외주식 주문은 실주문 방지를 위해 VTS/dry-run preview 메서드만 제공한다.
  실제 해외주식 주문 POST는 별도 검증 후 명시적으로 연결해야 한다.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from dotenv import dotenv_values

from .base import (
    Broker, Balance, Holding, Order,
    OrderSide, OrderType, OrderStatus, BrokerError,
)

HOSTS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "live": "https://openapi.koreainvestment.com:9443",
    "vts":  "https://openapivts.koreainvestment.com:29443",
}

TR_ID = {
    "inquire_balance":  {"real": "TTTC8434R", "vts": "VTTC8434R"},
    "inquire_price":    {"real": "FHKST01010100", "vts": "FHKST01010100"},
    "order_cash_buy":   {"real": "TTTC0802U", "vts": "VTTC0802U"},
    "order_cash_sell":  {"real": "TTTC0801U", "vts": "VTTC0801U"},
    "inquire_ccld":     {"real": "TTTC8001R", "vts": "VTTC8001R"},
    # 해외주식 일반 주문. 현재 코드는 실주문에 연결하지 않고 preview/dry-run 검증에만 사용한다.
    "overseas_order_buy":  {"real": "TTTT1002U", "vts": "VTTT1002U"},
    "overseas_order_sell": {"real": "TTTT1006U", "vts": "VTTT1006U"},
}

OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
TICKER_UNIVERSE_PATH = Path.home() / "kingmaker" / "data" / "_system" / "ticker_universe.json"

# KIS 해외주식 거래소 코드. ticker_universe.json의 exchange 필드를 이 코드로 변환한다.
# BATS/Cboe 계열 ETF는 KIS 일반 미국주식 주문에서 별도 거래소 코드가 불명확하므로 AMEX로 보수 매핑한다.
OVERSEAS_EXCHANGE_MAP = {
    "NASDAQ": "NASD",
    "NASD": "NASD",
    "NYSE": "NYSE",
    "NEW YORK STOCK EXCHANGE": "NYSE",
    "AMEX": "AMEX",
    "NYSE AMERICAN": "AMEX",
    "NYSE ARCA": "AMEX",
    "ARCA": "AMEX",
    "BATS": "AMEX",
    "CBOE": "AMEX",
}

TOKEN_CACHE_PATH = Path.home() / "kingmaker" / "data" / "_system" / "kis_token.json"
LOG_PATH         = Path.home() / "kingmaker" / "data" / "_system" / "logs" / "kis.log"


def _get_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("kis")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        lg.addHandler(fh)
    return lg


log = _get_logger()


class KisBroker(Broker):

    def __init__(self, env_path: Optional[str] = None, dry_run: bool = False):
        env = dotenv_values(env_path or str(Path.home() / "kingmaker" / ".env"))

        self.app_key    = env.get("KIS_APP_KEY", "").strip()
        self.app_secret = env.get("KIS_APP_SECRET", "").strip()
        self.cano       = env.get("KIS_ACCOUNT_NO", "").strip()
        self.prdt_code  = env.get("KIS_ACCOUNT_PRODUCT_CODE", "01").strip()
        self._mode      = env.get("KIS_MODE", "vts").strip().lower()

        if self._mode not in HOSTS:
            raise BrokerError(f"KIS_MODE 값이 잘못됨: {self._mode!r} (real/vts/live 중 하나)")
        if len(self.app_key) != 36:
            raise BrokerError(f"KIS_APP_KEY 길이 이상: {len(self.app_key)} (36 기대)")
        if not self.app_secret:
            raise BrokerError("KIS_APP_SECRET 누락")
        if not (self.cano.isdigit() and len(self.cano) == 8):
            raise BrokerError("KIS_ACCOUNT_NO 형식 이상")

        self.host = HOSTS[self._mode]
        self.dry_run = dry_run
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    @property
    def mode(self) -> str:
        return "live"

    @property
    def kis_mode(self) -> str:
        return self._mode

    def _tr(self, key: str) -> str:
        """tr_id 분기 (live는 real과 동일)"""
        m = "real" if self._mode in ("real", "live") else "vts"
        return TR_ID[key][m]

    def _tr_for_mode(self, key: str, mode: Optional[str] = None) -> str:
        """특정 mode 기준 tr_id 조회. 해외주식 preview에서 VTS 강제용으로 사용."""
        m0 = (mode or self._mode).strip().lower()
        m = "real" if m0 in ("real", "live") else "vts"
        return TR_ID[key][m]

    # ---------- 토큰 ----------
    def _load_token_cache(self) -> bool:
        try:
            if not TOKEN_CACHE_PATH.exists():
                return False
            data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("mode") != self._mode or data.get("app_key") != self.app_key:
                return False
            expiry = datetime.fromisoformat(data["expiry"])
            if expiry <= datetime.now() + timedelta(hours=1):
                return False
            self._token = data["token"]
            self._token_expiry = expiry
            log.info(f"토큰 캐시 재사용 (만료: {expiry.isoformat()})")
            return True
        except Exception as e:
            log.warning(f"토큰 캐시 로드 실패: {e}")
            return False

    def _save_token_cache(self) -> None:
        try:
            TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "mode": self._mode,
                "app_key": self.app_key,
                "token": self._token,
                "expiry": self._token_expiry.isoformat() if self._token_expiry else "",
            }
            TOKEN_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(TOKEN_CACHE_PATH, 0o600)
        except Exception as e:
            log.warning(f"토큰 캐시 저장 실패: {e}")

    def _issue_token(self) -> str:
        url = f"{self.host}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        log.info(f"토큰 발급 요청 → {self.host}")
        try:
            res = requests.post(url, json=body, timeout=10)
        except requests.RequestException as e:
            raise BrokerError(f"토큰 발급 네트워크 오류: {e}") from e

        if res.status_code != 200:
            raise BrokerError(f"토큰 발급 실패 ({res.status_code}): {res.text[:300]}")

        data = res.json()
        if "access_token" not in data:
            raise BrokerError(f"토큰 발급 응답 이상: {data}")

        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expiry = datetime.now() + timedelta(seconds=expires_in)
        log.info(f"토큰 발급 성공 (만료: {self._token_expiry.isoformat()})")
        self._save_token_cache()
        return self._token

    def _get_token(self) -> str:
        if self._token and self._token_expiry and self._token_expiry > datetime.now() + timedelta(hours=1):
            return self._token
        if self._load_token_cache():
            return self._token
        return self._issue_token()

    # ---------- 공통 요청 ----------
    def _headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _request(self, method, path, tr_id, params=None, body=None, retry=True):
        url = f"{self.host}{path}"
        headers = self._headers(tr_id)
        try:
            if method.upper() == "GET":
                res = requests.get(url, headers=headers, params=params or {}, timeout=10)
            else:
                res = requests.post(url, headers=headers, json=body or {}, timeout=10)
        except requests.RequestException as e:
            raise BrokerError(f"{path} 네트워크 오류: {e}") from e

        try:
            data = res.json()
        except Exception:
            raise BrokerError(f"{path} 응답 파싱 실패: HTTP {res.status_code} body={res.text[:300]}")

        msg_cd = data.get("msg_cd", "")
        if msg_cd in ("EGW00123", "EGW00121") and retry:
            log.warning(f"토큰 만료 감지({msg_cd}) → 재발급 후 재시도")
            self._token = None
            self._token_expiry = None
            try:
                TOKEN_CACHE_PATH.unlink(missing_ok=True)
            except Exception:
                pass
            return self._request(method, path, tr_id, params, body, retry=False)

        if res.status_code != 200 or data.get("rt_cd") not in ("0", None):
            log.error(f"{path} 실패: status={res.status_code} rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
            raise BrokerError(
                f"{path} 호출 실패: rt_cd={data.get('rt_cd')} "
                f"msg_cd={msg_cd} msg={data.get('msg1', '')[:200]}"
            )

        return data

    # ---------- 잔고 ----------
    def get_balance(self) -> Balance:
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.prdt_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            self._tr("inquire_balance"), params=params,
        )

        output1 = data.get("output1", [])
        output2 = data.get("output2", [])
        summary = (output2[0] if isinstance(output2, list) and output2
                   else (output2 if isinstance(output2, dict) else {}))

        cash_krw    = float(summary.get("dnca_tot_amt", 0) or 0)
        invested    = float(summary.get("pchs_amt_smtl_amt", 0) or 0)
        total_value = float(summary.get("tot_evlu_amt", 0) or 0)

        holdings: List[Holding] = []
        for row in output1:
            qty = int(float(row.get("hldg_qty", 0) or 0))
            if qty <= 0:
                continue
            holdings.append(Holding(
                ticker             = str(row.get("pdno", "")).strip(),
                shares             = qty,
                avg_cost           = float(row.get("pchs_avg_pric", 0) or 0),
                current_price      = float(row.get("prpr", 0) or 0),
                market_value       = float(row.get("evlu_amt", 0) or 0),
                unrealized_pnl     = float(row.get("evlu_pfls_amt", 0) or 0),
                unrealized_pnl_pct = float(row.get("evlu_pfls_rt", 0) or 0),
            ))

        if total_value <= 0:
            total_value = cash_krw + sum(h.market_value for h in holdings)

        return Balance(
            cash_krw=cash_krw, total_value_krw=total_value, invested_krw=invested,
            holdings=holdings, fetched_at=datetime.now().isoformat(),
        )

    def get_holdings(self) -> List[Holding]:
        return self.get_balance().holdings

    # ---------- 시세 ----------
    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
            data = self._request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                self._tr("inquire_price"), params=params,
            )
            output = data.get("output") or {}
            prpr = output.get("stck_prpr") or output.get("prpr")
            if prpr in (None, "", "0"):
                return None
            return float(prpr)
        except BrokerError as e:
            log.warning(f"현재가 조회 실패 {ticker}: {e}")
            return None

    # ---------- 시장 상태 ----------
    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        hm = now.hour * 100 + now.minute
        return 900 <= hm <= 1530

    # ==========================================================
    # Part 2: 주문/취소/조회
    # ==========================================================

    def _hashkey(self, body: Dict[str, Any]) -> str:
        """KIS 주문 POST 헤더에 필요한 hashkey 생성 (서버에 한 번 더 요청)"""
        url = f"{self.host}/uapi/hashkey"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        try:
            res = requests.post(url, headers=headers, json=body, timeout=5)
        except requests.RequestException as e:
            raise BrokerError(f"hashkey 네트워크 오류: {e}") from e
        if res.status_code != 200:
            raise BrokerError(f"hashkey 실패 ({res.status_code}): {res.text[:200]}")
        data = res.json()
        h = data.get("HASH") or data.get("hash")
        if not h:
            raise BrokerError(f"hashkey 응답 이상: {data}")
        return h

    def _build_order_body(
        self,
        ticker: str,
        shares: int,
        order_type: OrderType,
        price: float,
    ) -> Dict[str, Any]:
        """국내주식 주문 body 구성."""
        ord_dvsn = "01" if order_type == OrderType.MARKET else "00"  # 01=시장가, 00=지정가
        ord_unpr = "0" if order_type == OrderType.MARKET else str(int(round(price)))
        return {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.prdt_code,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(shares)),
            "ORD_UNPR": ord_unpr,
        }

    def _resolve_overseas_exchange_code(self, ticker: str, exchange_code: Optional[str] = None) -> str:
        """미국주식 거래소 코드를 KIS OVRS_EXCG_CD로 변환.

        explicit exchange_code가 있으면 그 값을 우선한다.
        없으면 data/_system/ticker_universe.json의 exchange를 읽어 NASD/NYSE/AMEX로 매핑한다.
        """
        if exchange_code:
            code = exchange_code.strip().upper()
            return OVERSEAS_EXCHANGE_MAP.get(code, code)

        ticker_u = str(ticker).strip().upper()
        try:
            data = json.loads(TICKER_UNIVERSE_PATH.read_text(encoding="utf-8"))
            for item in data:
                if str(item.get("symbol", "")).strip().upper() == ticker_u:
                    exch = str(item.get("exchange", "")).strip().upper()
                    if exch in OVERSEAS_EXCHANGE_MAP:
                        return OVERSEAS_EXCHANGE_MAP[exch]
                    raise BrokerError(f"{ticker_u} 거래소 {exch!r}를 OVRS_EXCG_CD로 매핑할 수 없음")
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(f"{ticker_u} 거래소 매핑 조회 실패: {e}") from e
        raise BrokerError(f"{ticker_u}를 ticker_universe.json에서 찾을 수 없음")

    def _build_overseas_order_body(
        self,
        ticker: str,
        shares: int,
        order_type: OrderType,
        price: float,
        exchange_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """해외주식 일반 주문 body 구성.

        소수점 주문은 지원하지 않는다. shares는 정수주로 강제한다.
        현재 이 body는 preview/dry-run 검증용이며 실제 POST에는 연결하지 않는다.
        """
        qty = int(shares)
        if qty <= 0:
            raise BrokerError(f"overseas shares must be positive integer (got {shares})")
        if float(shares) != float(qty):
            raise BrokerError(f"overseas fractional shares are not supported (got {shares})")
        if order_type == OrderType.LIMIT and price <= 0:
            raise BrokerError("해외주식 지정가 주문에는 price > 0 필요")

        ovrs_excg_cd = self._resolve_overseas_exchange_code(ticker, exchange_code)
        # KIS 해외주식 일반 주문 샘플은 ORD_DVSN=00(지정가)을 기본으로 사용한다.
        # 시장가 주문 지원 여부는 거래소/시간대별 차이가 있어 preview에서는 00 + 가격 0으로만 표현한다.
        ord_dvsn = "00"
        ovrs_ord_unpr = "0" if order_type == OrderType.MARKET else f"{float(price):.2f}"
        return {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.prdt_code,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "PDNO": str(ticker).strip().upper(),
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": ovrs_ord_unpr,
        }

    def build_overseas_order_preview(
        self,
        side: OrderSide,
        ticker: str,
        shares: int,
        order_type: OrderType = OrderType.LIMIT,
        price: float = 0.0,
        exchange_code: Optional[str] = None,
        force_vts: bool = True,
    ) -> Dict[str, Any]:
        """해외주식 주문 요청 preview.

        실제 주문을 보내지 않고 endpoint/tr_id/body만 반환한다.
        force_vts=True가 기본이라 실계좌 TR_ID를 실수로 쓰지 않는다.
        """
        side_enum = side if isinstance(side, OrderSide) else OrderSide(str(side).lower())
        tr_key = "overseas_order_buy" if side_enum == OrderSide.BUY else "overseas_order_sell"
        mode_for_tr = "vts" if force_vts else self._mode
        tr_id = self._tr_for_mode(tr_key, mode_for_tr)
        host = HOSTS["vts"] if force_vts else self.host
        body = self._build_overseas_order_body(ticker, shares, order_type, price, exchange_code)
        return {
            "dry_run_only": True,
            "force_vts": bool(force_vts),
            "host": host,
            "endpoint": OVERSEAS_ORDER_PATH,
            "url": f"{host}{OVERSEAS_ORDER_PATH}",
            "tr_id": tr_id,
            "side": side_enum.value,
            "order_type": order_type.value if isinstance(order_type, OrderType) else str(order_type),
            "body": body,
            "note": "Preview only. No network order request is sent by this method.",
        }

    def place_overseas_buy_dry_run(
        self,
        ticker: str,
        shares: int,
        order_type: OrderType = OrderType.LIMIT,
        price: float = 0.0,
        exchange_code: Optional[str] = None,
    ) -> Order:
        """해외주식 VTS 주문 preview를 Order 형태로 반환. 실제 주문 금지."""
        preview = self.build_overseas_order_preview(
            OrderSide.BUY, ticker, shares, order_type, price, exchange_code, force_vts=True
        )
        return Order(
            order_id="DRY-OVERSEAS-BUY",
            ticker=str(ticker).strip().upper(),
            side=OrderSide.BUY,
            order_type=order_type,
            shares=int(shares),
            price=price,
            status=OrderStatus.PENDING,
            submitted_at=datetime.now().isoformat(),
            message=json.dumps(preview, ensure_ascii=False),
        )

    def place_overseas_sell_dry_run(
        self,
        ticker: str,
        shares: int,
        order_type: OrderType = OrderType.LIMIT,
        price: float = 0.0,
        exchange_code: Optional[str] = None,
    ) -> Order:
        """해외주식 VTS 주문 preview를 Order 형태로 반환. 실제 주문 금지."""
        preview = self.build_overseas_order_preview(
            OrderSide.SELL, ticker, shares, order_type, price, exchange_code, force_vts=True
        )
        return Order(
            order_id="DRY-OVERSEAS-SELL",
            ticker=str(ticker).strip().upper(),
            side=OrderSide.SELL,
            order_type=order_type,
            shares=int(shares),
            price=price,
            status=OrderStatus.PENDING,
            submitted_at=datetime.now().isoformat(),
            message=json.dumps(preview, ensure_ascii=False),
        )

    def _send_order(
        self,
        side: OrderSide,
        ticker: str,
        shares: int,
        order_type: OrderType,
        price: float,
    ) -> Order:
        """국내주식 매수/매도 공통 발사 로직"""
        if shares <= 0:
            raise BrokerError(f"shares must be > 0 (got {shares})")
        if order_type == OrderType.LIMIT and price <= 0:
            raise BrokerError("지정가 주문에는 price > 0 필요")

        body = self._build_order_body(ticker, shares, order_type, price)
        tr_key = "order_cash_buy" if side == OrderSide.BUY else "order_cash_sell"
        tr_id = self._tr(tr_key)
        submitted_at = datetime.now().isoformat()

        # dry_run 모드: 실제 호출 안 하고 모의 응답
        if self.dry_run:
            log.info(f"[DRY] {side.value} {ticker} {shares}주 @{price} tr_id={tr_id} body={body}")
            return Order(
                order_id=f"DRY-{int(datetime.now().timestamp())}",
                ticker=ticker, side=side, order_type=order_type,
                shares=shares, price=price,
                status=OrderStatus.PENDING,
                submitted_at=submitted_at,
                message="dry_run",
            )

        # 실주문: hashkey 생성 후 헤더에 포함
        hashkey = self._hashkey(body)
        headers = self._headers(tr_id)
        headers["hashkey"] = hashkey

        url = f"{self.host}/uapi/domestic-stock/v1/trading/order-cash"
        try:
            res = requests.post(url, headers=headers, json=body, timeout=10)
        except requests.RequestException as e:
            raise BrokerError(f"주문 네트워크 오류: {e}") from e

        try:
            data = res.json()
        except Exception:
            raise BrokerError(f"주문 응답 파싱 실패: HTTP {res.status_code} body={res.text[:300]}")

        # 거부/오류 → REJECTED Order로 반환 (예외 대신 Order로 — SafetyLayer가 기록할 수 있게)
        if res.status_code != 200 or data.get("rt_cd") != "0":
            log.error(f"주문 거부: rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
            return Order(
                order_id="", ticker=ticker, side=side, order_type=order_type,
                shares=shares, price=price,
                status=OrderStatus.REJECTED,
                submitted_at=submitted_at,
                message=f"rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg={data.get('msg1', '')[:200]}",
            )

        out = data.get("output", {})
        odno = out.get("ODNO", "")
        ord_tmd = out.get("ORD_TMD", "")
        log.info(f"주문 접수: ODNO={odno} {side.value} {ticker} {shares}주")

        return Order(
            order_id=odno, ticker=ticker, side=side, order_type=order_type,
            shares=shares, price=price,
            status=OrderStatus.PENDING,  # 접수만 된 상태. 체결은 get_order로 조회
            submitted_at=submitted_at,
            message=f"KRX_FWDG_ORD_ORGNO={out.get('KRX_FWDG_ORD_ORGNO','')} ORD_TMD={ord_tmd}",
        )

    # ---------- 공개 API ----------
    def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id: str = "") -> Order:
        return self._send_order(OrderSide.BUY, ticker, shares, order_type, price)

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id: str = "") -> Order:
        return self._send_order(OrderSide.SELL, ticker, shares, order_type, price)

    def get_order(self, order_id: str) -> Optional[Order]:
        """그날 주문 리스트에서 ODNO로 검색 → 체결 상태 포함 반환"""
        if not order_id or order_id.startswith("DRY-"):
            return None
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.prdt_code,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",     # 00=전체, 01=매도, 02=매수
            "INQR_DVSN": "00",            # 00=역순, 01=정순
            "PDNO": "",
            "CCLD_DVSN": "00",            # 00=전체, 01=체결, 02=미체결
            "ORD_GNO_BRNO": "",
            "ODNO": order_id,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            data = self._request(
                "GET",
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                self._tr("inquire_ccld"), params=params,
            )
        except BrokerError as e:
            log.warning(f"주문 조회 실패 {order_id}: {e}")
            return None

        rows = data.get("output1", []) or []
        target = next((r for r in rows if r.get("odno") == order_id), None)
        if not target:
            return None

        ord_qty   = int(float(target.get("ord_qty", 0) or 0))
        tot_ccld  = int(float(target.get("tot_ccld_qty", 0) or 0))
        avg_price = float(target.get("avg_prvs", 0) or 0)
        side_cd   = target.get("sll_buy_dvsn_cd", "")  # 01=매도, 02=매수
        side      = OrderSide.SELL if side_cd == "01" else OrderSide.BUY

        if tot_ccld == 0:
            status = OrderStatus.PENDING
        elif tot_ccld < ord_qty:
            status = OrderStatus.PARTIAL
        else:
            status = OrderStatus.FILLED
        if target.get("cncl_yn", "N") == "Y":
            status = OrderStatus.CANCELLED

        return Order(
            order_id=order_id,
            ticker=str(target.get("pdno", "")).strip(),
            side=side,
            order_type=OrderType.MARKET if target.get("ord_dvsn", "") == "01" else OrderType.LIMIT,
            shares=ord_qty,
            price=float(target.get("ord_unpr", 0) or 0),
            status=status,
            filled_shares=tot_ccld,
            filled_avg_price=avg_price,
            submitted_at=str(target.get("ord_tmd", "")),
        )

    def cancel_order(self, order_id: str) -> bool:
        """미체결 주문 취소. 성공 시 True."""
        if not order_id or order_id.startswith("DRY-"):
            return self.dry_run  # dry_run은 그냥 True 반환

        # 1) 원주문 정보 필요 (KRX_FWDG_ORD_ORGNO, ORD_DVSN)
        original = self.get_order(order_id)
        if original is None:
            log.warning(f"취소 실패: 원주문 {order_id} 조회 불가")
            return False
        if original.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            log.info(f"취소 불가: {order_id} 이미 {original.status.value}")
            return False

        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.prdt_code,
            "KRX_FWDG_ORD_ORGNO": "",         # 실제론 원주문 조회 결과에서 가져와야 정확
            "ORGN_ODNO": order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",        # 01=정정, 02=취소
            "ORD_QTY": "0",                   # 0이면 잔량 전부 취소
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        try:
            hashkey = self._hashkey(body)
            headers = self._headers(self._tr("order_cash_buy"))  # 정정/취소 tr_id는 별도. 일단 buy tr 재사용 (개선 여지)
            headers["tr_id"] = "TTTC0803U" if self._mode in ("real", "live") else "VTTC0803U"
            headers["hashkey"] = hashkey
            url = f"{self.host}/uapi/domestic-stock/v1/trading/order-rvsecncl"
            res = requests.post(url, headers=headers, json=body, timeout=10)
            data = res.json()
            ok = (res.status_code == 200 and data.get("rt_cd") == "0")
            log.info(f"취소 결과 {order_id}: rt_cd={data.get('rt_cd')} msg={data.get('msg1')}")
            return ok
        except Exception as e:
            log.error(f"취소 예외 {order_id}: {e}")
            return False


if __name__ == "__main__":
    print("=" * 60)
    print("KisBroker 해외주식 주문 preview 검증 (네트워크 주문 없음)")
    print("=" * 60)

    broker = object.__new__(KisBroker)
    broker.app_key = ""  # preview에는 토큰/키 불필요
    broker.app_secret = ""
    broker.cano = "00000000"
    broker.prdt_code = "01"
    broker._mode = "vts"
    broker.host = HOSTS["vts"]
    broker.dry_run = True
    broker._token = None
    broker._token_expiry = None

    preview = broker.build_overseas_order_preview(
        OrderSide.BUY, "AAPL", 1, OrderType.LIMIT, 190.12, force_vts=True
    )
    print(json.dumps(preview, ensure_ascii=False, indent=2))
