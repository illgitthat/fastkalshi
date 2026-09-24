import asyncio
import logging
from typing import Any, Literal

import orjson
import websockets
from websockets.exceptions import WebSocketException

from fastkalshi import constants
from fastkalshi.auth import request_headers

logger = logging.getLogger(__name__)

SubscriptionAction = Literal[
    "add_markets",
    "delete_markets",
    "get_snapshot",
    "subscribe_indices",
    "unsubscribe_indices",
    "indexlist",
    "subscribe_underlyings",
    "unsubscribe_underlyings",
    "underlying_list",
]


def _is_closed_market_snapshot(message_type: str, payload: dict) -> bool:
    """Production sends ``market_id: ""`` and no price levels for closed markets.

    This departs from the AsyncAPI ``marketId`` format (uuid). Treating it as an
    empty book keeps one closed market from dropping every other subscription.
    """
    return (
        message_type == "orderbook_snapshot"
        and "yes_dollars_fp" not in payload
        and "no_dollars_fp" not in payload
    )


class KalshiWebSocketError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        command_id: int | None = None,
        subscription_id: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.command_id = command_id
        self.subscription_id = subscription_id


class Client:
    def __init__(self):
        self.message_id = 1
        self.ws = None
        self._orderbook_snapshots = {}
        self._sequence_by_subscription = {}

    async def connect(self, url: str | None = None):
        url = url or constants.WEBSOCKET_URL
        headers = request_headers("GET", url)
        connected = False
        close_code = None
        close_reason = None
        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                compression=None,
            ) as websocket:
                connected = True
                self.ws = websocket
                self._orderbook_snapshots.clear()
                self._sequence_by_subscription.clear()
                await self.on_open()
                await self.handler()
        except websockets.ConnectionClosed as error:
            close_code = error.code
            close_reason = error.reason
        finally:
            websocket = self.ws
            self.ws = None
            if connected:
                close_code = close_code or getattr(
                    websocket,
                    "close_code",
                    None,
                )
                close_reason = close_reason or getattr(
                    websocket,
                    "close_reason",
                    None,
                )
                await self.on_close(close_code, close_reason)

    async def run_forever(
        self,
        url: str | None = None,
        *,
        reconnect_delay: float = 1.0,
    ):
        while True:
            try:
                await self.connect(url)
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException) as error:
                await self.on_error(error)
            await asyncio.sleep(reconnect_delay)

    async def on_open(self):
        logger.debug("WebSocket connection opened.")

    async def on_message(self, message: dict):
        logger.debug("Received message: %s", message)

    async def on_error(self, error):
        logger.error("An error occurred: %s", error)

    async def on_close(self, close_status_code, close_msg):
        logger.warning(
            "WebSocket connection closed with code=%s, message=%s",
            close_status_code,
            close_msg,
        )

    async def on_sequence_gap(
        self,
        message: dict,
        expected_sequence: int,
    ):
        logger.warning(
            "WebSocket sequence gap for sid=%s: expected=%s, received=%s",
            message.get("sid"),
            expected_sequence,
            message.get("seq"),
        )

    async def send_command(
        self,
        command: str,
        params: dict[str, Any] | None = None,
    ):
        if self.ws is None:
            raise RuntimeError("WebSocket is not connected")
        command_id = self.message_id
        self.message_id += 1
        message = {"id": command_id, "cmd": command}
        if params is not None:
            message["params"] = params
        await self.ws.send(orjson.dumps(message).decode())
        return command_id

    async def subscribe(
        self,
        channels: list[str],
        tickers: list[str] | None = None,
        *,
        use_yes_price: bool = True,
        **options: Any,
    ):
        params: dict[str, Any] = {"channels": channels}
        params.update(options)
        if tickers:
            if "market_tickers" in options:
                raise ValueError("provide tickers or market_tickers, not both")
            params["market_tickers"] = tickers
        if "orderbook_delta" in channels:
            params["use_yes_price"] = use_yes_price
        return await self.send_command("subscribe", params)

    async def unsubscribe(self, sids: list[int]):
        if not sids or any(
            isinstance(sid, bool) or not isinstance(sid, int) or sid < 1 for sid in sids
        ):
            raise ValueError("sids must contain positive integers")
        return await self.send_command("unsubscribe", {"sids": sids})

    async def update_subscription(
        self,
        action: SubscriptionAction,
        *,
        sid: int | None = None,
        sids: list[int] | None = None,
        market_ticker: str | None = None,
        market_tickers: list[str] | None = None,
        market_id: str | None = None,
        market_ids: list[str] | None = None,
        send_initial_snapshot: bool | None = None,
        index_ids: list[str] | None = None,
        underlying_tickers: list[str] | None = None,
    ):
        if (sid is None) == (sids is None):
            raise ValueError("provide exactly one of sid or sids")
        if sid is not None and (
            isinstance(sid, bool) or not isinstance(sid, int) or sid < 1
        ):
            raise ValueError("sid must be a positive integer")
        if sids is not None and (
            len(sids) != 1
            or isinstance(sids[0], bool)
            or not isinstance(sids[0], int)
            or sids[0] < 1
        ):
            raise ValueError("sids must contain exactly one positive integer")

        params: dict[str, Any] = {"action": action}
        params["sid" if sid is not None else "sids"] = sid if sid is not None else sids
        params.update(
            {
                key: value
                for key, value in {
                    "market_ticker": market_ticker,
                    "market_tickers": market_tickers,
                    "market_id": market_id,
                    "market_ids": market_ids,
                    "send_initial_snapshot": send_initial_snapshot,
                    "index_ids": index_ids,
                    "underlying_tickers": underlying_tickers,
                }.items()
                if value is not None
            }
        )
        return await self.send_command("update_subscription", params)

    async def list_subscriptions(self):
        return await self.send_command("list_subscriptions")

    async def handler(self):
        websocket = self.ws
        if websocket is None:
            raise RuntimeError("WebSocket is not connected")
        async for raw_message in websocket:
            try:
                message = orjson.loads(raw_message)
            except orjson.JSONDecodeError:
                await self.on_error(
                    KalshiWebSocketError("Invalid JSON received from Kalshi")
                )
                await websocket.close(
                    code=1002,
                    reason="Invalid JSON",
                )
                return
            if not isinstance(message, dict):
                await self.on_error(
                    KalshiWebSocketError("Invalid message received from Kalshi")
                )
                await websocket.close(
                    code=1002,
                    reason="Invalid message",
                )
                return
            if not await self._handle_protocol_message(message):
                return

    async def _handle_protocol_message(self, message: dict):
        subscription_id = message.get("sid")
        sequence = message.get("seq")
        if subscription_id is not None and (
            isinstance(subscription_id, bool)
            or not isinstance(subscription_id, int)
            or subscription_id < 1
        ):
            return await self._reject_protocol_message(
                "WebSocket sid must be a positive integer"
            )
        if sequence is not None and (
            isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
        ):
            return await self._reject_protocol_message(
                "WebSocket seq must be a positive integer"
            )
        if subscription_id is not None and sequence is not None:
            previous = self._sequence_by_subscription.get(subscription_id)
            if previous is not None and sequence != previous + 1:
                await self.on_sequence_gap(message, previous + 1)
                if self.ws is not None:
                    await self.ws.close(
                        code=1011,
                        reason="WebSocket sequence gap",
                    )
                return False
            self._sequence_by_subscription[subscription_id] = sequence

        message_type = message.get("type")
        if not isinstance(message_type, str):
            return await self._reject_protocol_message(
                "WebSocket type must be a string"
            )
        if message_type in {"orderbook_snapshot", "orderbook_delta"}:
            payload = message.get("msg")
            if (
                subscription_id is None
                or sequence is None
                or not isinstance(payload, dict)
            ):
                return await self._reject_protocol_message(
                    f"{message_type} requires sid, seq, and an object payload"
                )
            market_ticker = payload.get("market_ticker")
            market_id = payload.get("market_id")
            if (
                not isinstance(market_ticker, str)
                or not market_ticker
                or not isinstance(market_id, str)
                or not (market_id or _is_closed_market_snapshot(message_type, payload))
            ):
                return await self._reject_protocol_message(
                    f"{message_type} requires valid market_ticker and market_id fields"
                )
            if message_type == "orderbook_delta":
                price = payload.get("price_dollars")
                delta = payload.get("delta_fp")
                side = payload.get("side")
                if (
                    not isinstance(price, str)
                    or not price
                    or not isinstance(delta, str)
                    or not delta
                    or side not in {"yes", "no"}
                ):
                    return await self._reject_protocol_message(
                        "orderbook_delta requires string price_dollars and "
                        "delta_fp fields plus side=yes|no"
                    )
            # AsyncAPI names market_ticker the unique identifier for this channel.
            market_key = market_ticker
            ready_markets = self._orderbook_snapshots.setdefault(
                subscription_id,
                set(),
            )
            if message_type == "orderbook_snapshot":
                ready_markets.add(market_key)
            elif market_key not in ready_markets:
                return True

        if message_type == "error":
            payload = message.get("msg") or {}
            if not isinstance(payload, dict):
                return await self._reject_protocol_message(
                    "WebSocket error payload must be an object"
                )
            code = payload.get("code")
            error_message = payload.get("msg")
            if isinstance(code, bool) or not isinstance(code, int):
                return await self._reject_protocol_message(
                    "WebSocket error code must be an integer"
                )
            if not isinstance(error_message, str):
                return await self._reject_protocol_message(
                    "WebSocket error message must be a string"
                )
            await self.on_error(
                KalshiWebSocketError(
                    error_message,
                    code=code,
                    command_id=message.get("id"),
                    subscription_id=subscription_id,
                )
            )
            if code in {10, 25} and self.ws is not None:
                reason = (
                    "WebSocket channel error"
                    if code == 10
                    else "WebSocket subscription buffer overflow"
                )
                await self.ws.close(
                    code=1011,
                    reason=reason,
                )
                return False
            return True

        await self.on_message(message)
        return True

    async def _reject_protocol_message(self, message: str) -> bool:
        await self.on_error(KalshiWebSocketError(message))
        if self.ws is not None:
            await self.ws.close(
                code=1002,
                reason="Invalid message",
            )
        return False
