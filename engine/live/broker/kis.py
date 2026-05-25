"""
KisBroker - 한국투자증권 OpenAPI 연동 (실전/모의)
Part 1: 토큰 + 잔고 + 현재가 (읽기 전용)
Part 2: 주문/취소/조회 (별도 작성)
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
            raise BrokerError(f"KIS_ACCOUNT_NO 형식 이상")

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
        """주문 body 공통 구성"""
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

    def _send_order(
        self,
        side: OrderSide,
        ticker: str,
        shares: int,
        order_type: OrderType,
        price: float,
    ) -> Order:
        """매수/매도 공통 발사 로직"""
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
    def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0) -> Order:
        return self._send_order(OrderSide.BUY, ticker, shares, order_type, price)

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0) -> Order:
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
    print("KisBroker Part 1 검증")
    print("=" * 60)

    broker = KisBroker()
    print(f"[설정] kis_mode={broker.kis_mode}, host={broker.host}, "
          f"CANO={broker.cano}, PRDT={broker.prdt_code}")

    print("\n[1] 토큰 발급...")
    t1 = broker._get_token()
    print(f"  토큰 길이: {len(t1)}자 (앞 8자: {t1[:8]}...)")
    print(f"  만료: {broker._token_expiry.isoformat()}")
    print(f"  캐시 저장: {TOKEN_CACHE_PATH.exists()}")

    print("\n[2] 토큰 캐시 재사용 (새 인스턴스)...")
    broker2 = KisBroker()
    t2 = broker2._get_token()
    print(f"  같은 토큰?: {t1 == t2}")

    print("\n[3] 잔고 조회...")
    bal = broker.get_balance()
    print(f"  가용현금:   {bal.cash_krw:>15,.0f} 원")
    print(f"  매수원금:   {bal.invested_krw:>15,.0f} 원")
    print(f"  총평가금:   {bal.total_value_krw:>15,.0f} 원")
    print(f"  보유종목수: {len(bal.holdings)} 건")
    for h in bal.holdings:
        print(f"     - {h.ticker}: {h.shares}주 @ {h.avg_cost:,.0f} "
              f"(현재가 {h.current_price:,.0f}, 손익 {h.unrealized_pnl:+,.0f} / {h.unrealized_pnl_pct:+.2f}%)")

    print("\n[4] 현재가 조회 (379800)...")
    p = broker.get_current_price("379800")
    print(f"  379800 현재가: {p:,.0f} 원" if p else "  ❌ 조회 실패")

    print("\n[5] 잘못된 종목코드 (999999)...")
    p2 = broker.get_current_price("999999")
    print(f"  결과: {p2}")

    print("\n" + "=" * 60)
    print("Part 1 검증 완료")
    print("=" * 60)
