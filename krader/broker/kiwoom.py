"""Kiwoom Open API+ broker implementation using PyQt5."""

import asyncio
import logging
import queue
import sys
import threading
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from krader.broker.base import Balance, BaseBroker, Position, TickCallback
from krader.broker.errors import (
    ConnectionError,
    OrderRejectedError,
    RateLimitError,
)

if TYPE_CHECKING:
    from krader.execution.order import Order

logger = logging.getLogger(__name__)


# Kiwoom error codes (from official API documentation)
KIWOOM_ERROR_CODES = {
    0: "OP_ERR_NONE (정상처리)",
    -10: "OP_ERR_FAIL (실패)",
    -100: "OP_ERR_LOGIN (사용자정보교환실패)",
    -101: "OP_ERR_CONNECT (서버접속실패)",
    -102: "OP_ERR_VERSION (버전처리실패)",
    -103: "OP_ERR_FIREWALL (개인방화벽실패)",
    -104: "OP_ERR_MEMORY (메모리보호실패)",
    -105: "OP_ERR_INPUT (함수입력값오류)",
    -106: "OP_ERR_SOCKET_CLOSED (통신연결종료)",
    -200: "OP_ERR_SISE_OVERFLOW (시세조회과부하)",
    -201: "OP_ERR_RQ_STRUCT_FAIL (전문작성초기화실패)",
    -202: "OP_ERR_RQ_STRING_FAIL (전문작성입력값오류)",
    -203: "OP_ERR_NO_DATA (데이터없음)",
    -204: "OP_ERR_OVER_MAX_DATA (조회가능한종목수초과)",
    -205: "OP_ERR_DATA_RCV_FAIL (데이터수신실패)",
    -206: "OP_ERR_OVER_MAX_FID (조회가능한FID수초과)",
    -207: "OP_ERR_REAL_CANCEL (실시간해제오류)",
    -300: "OP_ERR_ORD_WRONG_INPUT (입력값오류)",
    -301: "OP_ERR_ORD_WRONG_ACCTNO (계좌비밀번호없음)",
    -302: "OP_ERR_OTHER_ACC_USE (타인계좌사용오류)",
    -303: "OP_ERR_MIS_2BILL_EXC (주문가격이20억원초과)",
    -304: "OP_ERR_MIS_5BILL_EXC (주문가격이50억원초과)",
    -305: "OP_ERR_MIS_1PER_EXC (주문수량이총발행주수1%초과)",
    -306: "OP_ERR_MIS_3PER_EXC (주문수량이총발행주수3%초과)",
    -307: "OP_ERR_SEND_FAIL (주문전송실패)",
    -308: "OP_ERR_ORD_OVERFLOW (주문전송과부하)",
    -309: "OP_ERR_MIS_300CNT_EXC (주문수량300계약초과)",
    -310: "OP_ERR_MIS_500CNT_EXC (주문수량500계약초과)",
    -340: "OP_ERR_ORD_WRONG_ACCTINFO (계좌정보없음)",
    -500: "OP_ERR_ORD_SYMCODE_EMPTY (종목코드없음)",
}


class KiwoomBroker(BaseBroker):
    """Kiwoom Open API+ broker adapter using PyQt5."""

    def __init__(
        self,
        account_number: str = "",
        tr_rate_limit_ms: int = 200,
    ) -> None:
        self._account_number = account_number
        self._tr_rate_limit_ms = tr_rate_limit_ms
        self._connected = False
        self._is_paper = False  # Will be detected after login
        self._last_tr_time: float = 0
        self._tick_callbacks: dict[str, list[TickCallback]] = {}

        # PyQt5 components
        self._app: Any = None
        self._ocx: Any = None
        self._qt_thread: Any = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

        # Async events for synchronization
        self._login_event: asyncio.Event | None = None
        self._tr_event: asyncio.Event | None = None
        self._tr_data: dict[str, Any] = {}

        # Qt timer for event processing
        self._timer: Any = None
        self._running = False

        # Thread-safe queue for dispatching OCX calls to the Qt thread
        self._qt_call_queue: queue.Queue = queue.Queue()

    @property
    def is_connected(self) -> bool:
        """Check if connected to Kiwoom."""
        return self._connected

    async def connect(self) -> None:
        """Connect to Kiwoom and login."""
        import threading

        self._event_loop = asyncio.get_running_loop()
        self._login_event = asyncio.Event()
        self._running = True

        # Start Qt in a separate thread
        ready_event = threading.Event()
        error_holder = {"error": None}

        self._qt_thread = threading.Thread(
            target=self._run_qt_thread,
            args=(ready_event, error_holder),
            daemon=True,
        )
        self._qt_thread.start()

        # Wait for Qt to initialize
        if not ready_event.wait(timeout=30.0):
            raise ConnectionError("Qt initialization timeout")

        if error_holder["error"]:
            raise ConnectionError(f"Qt initialization failed: {error_holder['error']}")

        # Wait for login to complete (user will see login popup)
        try:
            await asyncio.wait_for(self._login_event.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            raise ConnectionError("Login timeout - please complete login in the popup window")

        if not self._connected:
            raise ConnectionError("Login failed")

        # Detect paper trading mode (모의투자)
        server_gubun = await self._invoke_in_qt(
            lambda: self._ocx.dynamicCall("GetLoginInfo(QString)", "GetServerGubun")
        )
        self._is_paper = server_gubun == "1"

        # Get account number if not provided
        if not self._account_number:
            accounts = await self._invoke_in_qt(
                lambda: self._ocx.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            )
            if accounts:
                self._account_number = accounts.split(";")[0]

        mode_str = "paper (모의투자)" if self._is_paper else "live (실거래)"
        logger.info("Connected to Kiwoom [%s], account: %s", mode_str, self._account_number)

    def _run_qt_thread(self, ready_event, error_holder) -> None:
        """Run Qt event loop in a separate thread."""
        try:
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QAxContainer import QAxWidget
            from PyQt5.QtCore import QTimer

            # Create QApplication
            self._app = QApplication(sys.argv)

            # Create QAxWidget with Kiwoom OCX
            self._ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

            # Connect events
            self._ocx.OnEventConnect.connect(self._on_event_connect)
            self._ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
            self._ocx.OnReceiveRealData.connect(self._on_receive_real_data)
            self._ocx.OnReceiveChejanData.connect(self._on_receive_chejan_data)
            self._ocx.OnReceiveMsg.connect(self._on_receive_msg)

            logger.info("Kiwoom QAxWidget created")

            # Request login
            result = self._ocx.dynamicCall("CommConnect()")
            if result != 0:
                error_holder["error"] = f"CommConnect failed: {result}"
                ready_event.set()
                return

            logger.info("Login popup opened, waiting for user...")
            ready_event.set()

            # Process cross-thread OCX calls via timer
            self._qt_timer = QTimer()
            self._qt_timer.timeout.connect(self._process_qt_queue)
            self._qt_timer.start(10)

            # Run Qt event loop
            self._app.exec_()

        except Exception as e:
            error_holder["error"] = str(e)
            ready_event.set()
            logger.error("Qt thread error: %s", e)

    def _process_qt_queue(self) -> None:
        """Process pending OCX calls from other threads (runs in Qt thread)."""
        try:
            while True:
                callback = self._qt_call_queue.get_nowait()
                callback()
        except queue.Empty:
            pass

    async def _invoke_in_qt(self, func: Callable) -> Any:
        """Execute a function in the Qt thread and await the result."""
        if not self._event_loop:
            raise ConnectionError("Event loop not available")

        future = self._event_loop.create_future()

        def _execute():
            try:
                result = func()
                self._event_loop.call_soon_threadsafe(future.set_result, result)
            except Exception as e:
                self._event_loop.call_soon_threadsafe(future.set_exception, e)

        self._qt_call_queue.put(_execute)
        return await future

    async def _call_api(self, method: str, *args) -> Any:
        """Call Kiwoom API method via Qt thread."""
        if not self._ocx:
            raise ConnectionError("Not connected")

        if args:
            arg_types = ", ".join(["QString"] * len(args))
            signature = f"{method}({arg_types})"
            return await self._invoke_in_qt(lambda: self._ocx.dynamicCall(signature, *args))
        else:
            return await self._invoke_in_qt(lambda: self._ocx.dynamicCall(f"{method}()"))

    def _on_event_connect(self, err_code: int) -> None:
        """Handle login callback."""
        if err_code == 0:
            self._connected = True
            logger.info("Kiwoom login successful")
        else:
            self._connected = False
            logger.error("Kiwoom login failed: %s", KIWOOM_ERROR_CODES.get(err_code, err_code))

        # Signal the asyncio event
        if self._login_event and self._event_loop:
            self._event_loop.call_soon_threadsafe(self._login_event.set)

    def _on_receive_tr_data(
        self,
        screen_no: str,
        rq_name: str,
        tr_code: str,
        record_name: str,
        prev_next: str,
    ) -> None:
        """Handle TR data callback."""
        self._tr_data[rq_name] = {
            "screen_no": screen_no,
            "tr_code": tr_code,
            "record_name": record_name,
            "prev_next": prev_next,
        }
        if self._tr_event and self._event_loop:
            self._event_loop.call_soon_threadsafe(self._tr_event.set)

    def _on_receive_real_data(
        self, symbol: str, real_type: str, real_data: str
    ) -> None:
        """Handle real-time data callback."""
        if real_type == "주식체결":
            self._handle_tick_data(symbol)

    def _handle_tick_data(self, symbol: str) -> None:
        """Process tick data and notify callbacks."""
        from krader.market.types import Tick

        try:
            price = abs(int(self._ocx.dynamicCall("GetCommRealData(QString, int)", symbol, 10)))
            volume = abs(int(self._ocx.dynamicCall("GetCommRealData(QString, int)", symbol, 15)))
            tick = Tick(symbol=symbol, price=Decimal(price), volume=volume)

            callbacks = self._tick_callbacks.get(symbol, [])
            for callback in callbacks:
                if self._event_loop:
                    asyncio.run_coroutine_threadsafe(callback(tick), self._event_loop)

        except Exception as e:
            logger.error("Error processing tick for %s: %s", symbol, e)
            # Report error through callback if available
            if self._error_callback and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._report_error(
                        error_type="tick_processing",
                        message=f"{symbol}: {e}",
                        severity="warning",
                        context={"symbol": symbol},
                    ),
                    self._event_loop,
                )

    def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str) -> None:
        """Handle order/fill callback (Chejan)."""
        if gubun == "0":  # Order status
            self._handle_order_status()
        elif gubun == "1":  # Fill
            self._handle_fill()

    def _handle_order_status(self) -> None:
        """Process order status update."""
        order_no = self._ocx.dynamicCall("GetChejanData(int)", 9203)
        status = self._ocx.dynamicCall("GetChejanData(int)", 913)
        logger.info("Order status: %s - %s", order_no, status)

    def _handle_fill(self) -> None:
        """Process fill notification."""
        order_no = self._ocx.dynamicCall("GetChejanData(int)", 9203)
        filled_qty = int(self._ocx.dynamicCall("GetChejanData(int)", 911) or 0)
        filled_price = int(self._ocx.dynamicCall("GetChejanData(int)", 910) or 0)
        logger.info("Fill: order=%s, qty=%d, price=%d", order_no, filled_qty, filled_price)

    def _on_receive_msg(
        self, screen_no: str, rq_name: str, tr_code: str, msg: str
    ) -> None:
        """Handle message callback."""
        logger.info("Kiwoom message [%s]: %s", tr_code, msg)

    async def disconnect(self) -> None:
        """Disconnect from Kiwoom."""
        self._running = False
        self._connected = False

        if self._app:
            # Use thread-safe quit - post quit event to Qt event loop
            from PyQt5.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self._app, "quit", Qt.QueuedConnection)

        if self._qt_thread and self._qt_thread.is_alive():
            self._qt_thread.join(timeout=3.0)
            if self._qt_thread.is_alive():
                logger.warning("Qt thread did not terminate cleanly")

        self._ocx = None
        self._app = None
        logger.info("Disconnected from Kiwoom")

    async def _rate_limit(self) -> None:
        """Enforce TR rate limiting."""
        now = time.time()
        elapsed_ms = (now - self._last_tr_time) * 1000
        if elapsed_ms < self._tr_rate_limit_ms:
            await asyncio.sleep((self._tr_rate_limit_ms - elapsed_ms) / 1000)
        self._last_tr_time = time.time()

    async def _request_tr(
        self,
        tr_code: str,
        rq_name: str,
        inputs: dict[str, str],
        screen_no: str = "0101",
    ) -> dict:
        """Send a TR request and wait for response."""
        await self._rate_limit()
        self._tr_event = asyncio.Event()

        # SetInputValue + CommRqData must run together in Qt thread
        def _send():
            for key, value in inputs.items():
                self._ocx.dynamicCall("SetInputValue(QString, QString)", key, value)
            return self._ocx.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rq_name, tr_code, 0, screen_no
            )

        result = await self._invoke_in_qt(_send)

        if result != 0:
            raise RateLimitError(
                f"TR request failed: {KIWOOM_ERROR_CODES.get(result, result)}",
                code=str(result),
            )

        # Wait for TR response callback
        try:
            await asyncio.wait_for(self._tr_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            raise ConnectionError("TR request timeout")

        return self._tr_data.get(rq_name, {})

    async def request_tr(
        self,
        tr_code: str,
        rq_name: str,
        inputs: dict[str, str],
        screen_no: str = "0101",
    ) -> dict:
        """Public API: Send a TR request and wait for response."""
        return await self._request_tr(tr_code, rq_name, inputs, screen_no)

    async def get_comm_data(
        self, tr_code: str, rq_name: str, index: int, field: str
    ) -> str:
        """Get a single field from the most recent TR response."""
        if not self._ocx:
            return ""
        return await self._invoke_in_qt(
            lambda: self._ocx.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                tr_code, rq_name, index, field,
            )
        )

    async def get_repeat_cnt(self, tr_code: str, rq_name: str) -> int:
        """Get repeat count from the most recent TR response."""
        if not self._ocx:
            return 0
        return await self._invoke_in_qt(
            lambda: self._ocx.dynamicCall(
                "GetRepeatCnt(QString, QString)", tr_code, rq_name
            )
        )

    async def place_order(self, order: "Order") -> str:
        """Place an order with Kiwoom."""
        await self._rate_limit()

        order_type_map = {
            ("BUY", "LIMIT"): 1,
            ("SELL", "LIMIT"): 2,
            ("BUY", "MARKET"): 1,
            ("SELL", "MARKET"): 2,
        }

        order_type = order_type_map.get((order.side, order.order_type), 1)
        hoga_type = "00" if order.order_type == "LIMIT" else "03"
        price = int(order.price) if order.price else 0

        result = await self._invoke_in_qt(
            lambda: self._ocx.dynamicCall(
                "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                "주문",
                "0101",
                self._account_number,
                order_type,
                order.symbol,
                order.quantity,
                price,
                hoga_type,
                "",
            )
        )

        if result != 0:
            error_msg = KIWOOM_ERROR_CODES.get(result, f"Unknown error: {result}")
            raise OrderRejectedError(error_msg, code=str(result))

        broker_order_id = f"KW-{int(time.time() * 1000)}"
        logger.info("Order placed: %s -> %s", order.order_id, broker_order_id)
        return broker_order_id

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an order."""
        await self._rate_limit()

        result = await self._invoke_in_qt(
            lambda: self._ocx.dynamicCall(
                "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                "취소",
                "0102",
                self._account_number,
                3,
                "",
                0,
                0,
                "00",
                broker_order_id,
            )
        )
        return result == 0

    async def amend_order(
        self, broker_order_id: str, quantity: int | None = None, price: Decimal | None = None
    ) -> bool:
        """Amend an existing order."""
        await self._rate_limit()

        result = await self._invoke_in_qt(
            lambda: self._ocx.dynamicCall(
                "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                "정정",
                "0102",
                self._account_number,
                5 if quantity else 6,
                "",
                quantity or 0,
                int(price) if price else 0,
                "00",
                broker_order_id,
            )
        )
        return result == 0

    async def fetch_positions(self) -> list[Position]:
        """Fetch all positions from Kiwoom."""
        password = "0000" if self._is_paper else ""
        data = await self._request_tr(
            "opw00018",
            "계좌평가잔고내역",
            {
                "계좌번호": self._account_number,
                "비밀번호": password,
                "비밀번호입력매체구분": "00",
                "조회구분": "1",
            },
        )

        tr_code = data.get("tr_code", "opw00018")

        def _read_positions():
            positions = []
            row_count = self._ocx.dynamicCall(
                "GetRepeatCnt(QString, QString)", tr_code, "계좌평가결과"
            )
            for i in range(row_count):
                symbol = self._ocx.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    tr_code, "계좌평가결과", i, "종목번호",
                ).strip()
                qty = int(self._ocx.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    tr_code, "계좌평가결과", i, "보유수량",
                ) or 0)
                avg_price = int(self._ocx.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    tr_code, "계좌평가결과", i, "매입가",
                ) or 0)
                cur_price = int(self._ocx.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    tr_code, "계좌평가결과", i, "현재가",
                ) or 0)
                pnl = int(self._ocx.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    tr_code, "계좌평가결과", i, "평가손익",
                ) or 0)
                if symbol and qty > 0:
                    positions.append(
                        Position(
                            symbol=symbol.replace("A", ""),
                            quantity=qty,
                            avg_price=Decimal(avg_price),
                            current_price=Decimal(cur_price),
                            unrealized_pnl=Decimal(pnl),
                        )
                    )
            return positions

        return await self._invoke_in_qt(_read_positions)

    async def fetch_open_orders(self) -> list[dict]:
        """Fetch open orders from Kiwoom."""
        password = "0000" if self._is_paper else ""
        await self._request_tr(
            "opt10075",
            "미체결요청",
            {
                "계좌번호": self._account_number,
                "비밀번호": password,
                "비밀번호입력매체구분": "00",
                "체결구분": "1",
                "매매구분": "0",
            },
        )

        def _read_orders():
            orders = []
            row_count = self._ocx.dynamicCall(
                "GetRepeatCnt(QString, QString)", "opt10075", "미체결"
            )
            for i in range(row_count):
                order_data = {
                    "broker_order_id": self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        "opt10075", "미체결", i, "주문번호",
                    ).strip(),
                    "symbol": self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        "opt10075", "미체결", i, "종목코드",
                    ).strip(),
                    "side": "BUY" if self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        "opt10075", "미체결", i, "매매구분",
                    ).strip() == "2" else "SELL",
                    "quantity": int(self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        "opt10075", "미체결", i, "주문수량",
                    ) or 0),
                    "filled_quantity": int(self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        "opt10075", "미체결", i, "체결량",
                    ) or 0),
                    "price": int(self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        "opt10075", "미체결", i, "주문가격",
                    ) or 0),
                }
                if order_data["broker_order_id"]:
                    orders.append(order_data)
            return orders

        return await self._invoke_in_qt(_read_orders)

    async def fetch_balance(self) -> Balance:
        """Fetch account balance from Kiwoom."""
        password = "0000" if self._is_paper else ""
        await self._request_tr(
            "opw00018",
            "계좌평가",
            {
                "계좌번호": self._account_number,
                "비밀번호": password,
                "비밀번호입력매체구분": "00",
                "조회구분": "1",
            },
        )

        def _read_balance():
            total = int(self._ocx.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                "opw00018", "계좌평가결과", 0, "총평가금액",
            ) or 0)
            cash = int(self._ocx.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                "opw00018", "계좌평가결과", 0, "추정예탁자산",
            ) or 0)
            pnl = int(self._ocx.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                "opw00018", "계좌평가결과", 0, "총평가손익금액",
            ) or 0)
            return Balance(
                total_equity=Decimal(total),
                available_cash=Decimal(cash),
                unrealized_pnl=Decimal(pnl),
            )

        return await self._invoke_in_qt(_read_balance)

    async def subscribe_market_data(
        self, symbols: list[str], callback: TickCallback
    ) -> None:
        """Subscribe to real-time market data."""
        for symbol in symbols:
            if symbol not in self._tick_callbacks:
                self._tick_callbacks[symbol] = []
            self._tick_callbacks[symbol].append(callback)

        if not symbols:
            return

        fids = "10;11;12;15;20"  # price, volume, etc.
        symbol_list = ";".join(symbols)
        try:
            await asyncio.wait_for(
                self._invoke_in_qt(
                    lambda: self._ocx.dynamicCall(
                        "SetRealReg(QString, QString, QString, QString)",
                        "0200", symbol_list, fids, "1"
                    )
                ),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "SetRealReg timeout for %d symbols - will receive data when market opens",
                len(symbols),
            )

        logger.info("Subscribed to market data: %d symbols", len(symbols))

    async def unsubscribe_market_data(self, symbols: list[str]) -> None:
        """Unsubscribe from real-time market data."""
        for symbol in symbols:
            self._tick_callbacks.pop(symbol, None)

        for symbol in symbols:
            try:
                await asyncio.wait_for(
                    self._invoke_in_qt(
                        lambda s=symbol: self._ocx.dynamicCall(
                            "SetRealRemove(QString, QString)", "0200", s
                        )
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("SetRealRemove timeout for %s", symbol)

        logger.info("Unsubscribed from market data: %d symbols", len(symbols))
