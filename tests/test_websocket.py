import asyncio
import importlib
import json

import pytest

from fastkalshi.websocket import Client, KalshiWebSocketError

MARKET_A_ID = "00000000-0000-0000-0000-000000000001"
MARKET_B_ID = "00000000-0000-0000-0000-000000000002"


class FakeWebSocket:
    def __init__(self, incoming=None, close_code=None, close_reason=None):
        self.incoming = incoming or []
        self.messages = []
        self.closed = None
        self.close_code = close_code
        self.close_reason = close_reason

    async def send(self, message):
        await asyncio.sleep(0)
        self.messages.append(json.loads(message))

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)
        self.close_code = code
        self.close_reason = reason

    def __aiter__(self):
        return self._messages()

    async def _messages(self):
        for message in self.incoming:
            yield message


class RecordingClient(Client):
    def __init__(self):
        super().__init__()
        self.messages = []
        self.errors = []
        self.gaps = []
        self.closes = []

    async def on_message(self, message):
        self.messages.append(message)

    async def on_error(self, error):
        self.errors.append(error)

    async def on_sequence_gap(self, message, expected_sequence):
        self.gaps.append((message, expected_sequence))

    async def on_close(self, close_status_code, close_msg):
        self.closes.append((close_status_code, close_msg))


class FakeConnection:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *args):
        return False


def test_subscribe_uses_command_ids_and_explicit_yes_price():
    async def run():
        client = Client()
        client.ws = FakeWebSocket()

        command_id = await client.subscribe(
            ["orderbook_delta"],
            ["KXTEST"],
            send_initial_snapshot=True,
        )

        assert command_id == 1
        assert client.ws.messages == [
            {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "send_initial_snapshot": True,
                    "use_yes_price": True,
                    "market_tickers": ["KXTEST"],
                },
            }
        ]

    asyncio.run(run())


def test_concurrent_commands_reserve_unique_ids_before_sending():
    async def run():
        client = Client()
        client.ws = FakeWebSocket()

        command_ids = await asyncio.gather(
            client.list_subscriptions(),
            client.list_subscriptions(),
        )

        assert set(command_ids) == {1, 2}
        assert {message["id"] for message in client.ws.messages} == {1, 2}

    asyncio.run(run())


def test_subscription_control_methods_build_current_commands():
    async def run():
        client = Client()
        client.ws = FakeWebSocket()

        assert await client.unsubscribe([3, 5]) == 1
        assert (
            await client.update_subscription(
                "add_markets",
                sids=[7],
                market_tickers=["KXONE", "KXTWO"],
                send_initial_snapshot=True,
            )
            == 2
        )
        assert await client.update_subscription("indexlist", sid=8) == 3
        assert await client.list_subscriptions() == 4

        assert client.ws.messages == [
            {
                "id": 1,
                "cmd": "unsubscribe",
                "params": {"sids": [3, 5]},
            },
            {
                "id": 2,
                "cmd": "update_subscription",
                "params": {
                    "sids": [7],
                    "action": "add_markets",
                    "market_tickers": ["KXONE", "KXTWO"],
                    "send_initial_snapshot": True,
                },
            },
            {
                "id": 3,
                "cmd": "update_subscription",
                "params": {
                    "sid": 8,
                    "action": "indexlist",
                },
            },
            {
                "id": 4,
                "cmd": "list_subscriptions",
            },
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.unsubscribe([]),
        lambda client: client.update_subscription("add_markets"),
        lambda client: client.update_subscription(
            "add_markets",
            sid=1,
            sids=[1],
        ),
        lambda client: client.update_subscription(
            "add_markets",
            sids=[1, 2],
        ),
    ],
)
def test_subscription_control_methods_reject_invalid_ids(call):
    async def run():
        client = Client()
        client.ws = FakeWebSocket()

        with pytest.raises(ValueError):
            await call(client)
        assert client.ws.messages == []

    asyncio.run(run())


def test_sequence_gap_closes_socket_without_delivering_invalid_message():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket()

        await client._handle_protocol_message(
            {
                "type": "orderbook_snapshot",
                "sid": 3,
                "seq": 1,
                "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
            }
        )
        await client._handle_protocol_message(
            {
                "type": "orderbook_snapshot",
                "sid": 3,
                "seq": 3,
                "msg": {"market_ticker": "B", "market_id": MARKET_B_ID},
            }
        )

        assert client.gaps[0][1] == 2
        assert [message["seq"] for message in client.messages] == [1]
        assert client.ws.closed == (1011, "WebSocket sequence gap")

    asyncio.run(run())


def test_sequenced_error_advances_sequence_before_next_delta():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket()

        await client._handle_protocol_message(
            {
                "type": "orderbook_snapshot",
                "sid": 3,
                "seq": 1,
                "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
            }
        )
        await client._handle_protocol_message(
            {
                "type": "error",
                "sid": 3,
                "seq": 2,
                "msg": {"code": 27, "msg": "too many requests"},
            }
        )
        await client._handle_protocol_message(
            {
                "type": "orderbook_delta",
                "sid": 3,
                "seq": 3,
                "msg": {
                    "market_ticker": "A",
                    "market_id": MARKET_A_ID,
                    "price_dollars": "0.5000",
                    "delta_fp": "1.00",
                    "side": "yes",
                },
            }
        )

        assert client.gaps == []
        assert [message["seq"] for message in client.messages] == [1, 3]
        assert client.errors[0].code == 27
        assert client.ws.closed is None

    asyncio.run(run())


def test_reconnect_waits_for_each_market_snapshot(monkeypatch):
    async def run():
        first = FakeWebSocket(
            incoming=[
                json.dumps(
                    {
                        "type": "orderbook_snapshot",
                        "sid": 1,
                        "seq": 1,
                        "msg": {
                            "market_ticker": "A",
                            "market_id": MARKET_A_ID,
                            "price_dollars": "0.5000",
                            "delta_fp": "1.00",
                            "side": "yes",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "orderbook_delta",
                        "sid": 1,
                        "seq": 2,
                        "msg": {
                            "market_ticker": "A",
                            "market_id": MARKET_A_ID,
                            "price_dollars": "0.5000",
                            "delta_fp": "1.00",
                            "side": "yes",
                        },
                    }
                ),
            ]
        )
        second = FakeWebSocket(
            incoming=[
                json.dumps(
                    {
                        "type": "orderbook_delta",
                        "sid": 2,
                        "seq": 1,
                        "msg": {
                            "market_ticker": "A",
                            "market_id": MARKET_A_ID,
                            "price_dollars": "0.5000",
                            "delta_fp": "1.00",
                            "side": "yes",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "orderbook_snapshot",
                        "sid": 2,
                        "seq": 2,
                        "msg": {
                            "market_ticker": "A",
                            "market_id": MARKET_A_ID,
                            "price_dollars": "0.5000",
                            "delta_fp": "1.00",
                            "side": "yes",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "orderbook_delta",
                        "sid": 2,
                        "seq": 3,
                        "msg": {
                            "market_ticker": "A",
                            "market_id": MARKET_A_ID,
                            "price_dollars": "0.5000",
                            "delta_fp": "1.00",
                            "side": "yes",
                        },
                    }
                ),
            ]
        )
        connections = iter([first, second])
        module = importlib.import_module("fastkalshi.websocket.client")
        monkeypatch.setattr(module, "request_headers", lambda method, url: {})
        monkeypatch.setattr(
            module.websockets,
            "connect",
            lambda *args, **kwargs: FakeConnection(next(connections)),
        )

        class ReconnectClient(RecordingClient):
            async def on_open(self):
                await self.subscribe(["orderbook_delta"], ["A"])

        client = ReconnectClient()
        await client.connect()
        await client.connect()

        assert [message["seq"] for message in client.messages] == [1, 2, 2, 3]
        assert first.messages[0]["cmd"] == "subscribe"
        assert second.messages[0]["cmd"] == "subscribe"

    asyncio.run(run())


def test_buffer_overflow_error_closes_socket():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket()

        await client._handle_protocol_message(
            {
                "type": "error",
                "sid": 3,
                "seq": 1,
                "msg": {"code": 25, "msg": "buffer overflow"},
            }
        )

        assert isinstance(client.errors[0], KalshiWebSocketError)
        assert client.ws.closed == (
            1011,
            "WebSocket subscription buffer overflow",
        )

    asyncio.run(run())


def test_channel_error_closes_socket():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket()

        should_continue = await client._handle_protocol_message(
            {
                "type": "error",
                "sid": 3,
                "seq": 1,
                "msg": {"code": 10, "msg": "channel error"},
            }
        )

        assert should_continue is False
        assert client.ws.closed == (1011, "WebSocket channel error")

    asyncio.run(run())


def test_buffer_overflow_stops_buffered_deltas():
    async def run():
        incoming = [
            {
                "type": "orderbook_snapshot",
                "sid": 3,
                "seq": 1,
                "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
            },
            {
                "type": "error",
                "sid": 3,
                "seq": 2,
                "msg": {"code": 25, "msg": "buffer overflow"},
            },
            {
                "type": "orderbook_delta",
                "sid": 3,
                "seq": 3,
                "msg": {
                    "market_ticker": "A",
                    "market_id": MARKET_A_ID,
                    "price_dollars": "0.5000",
                    "delta_fp": "1.00",
                    "side": "yes",
                },
            },
        ]
        client = RecordingClient()
        client.ws = FakeWebSocket(
            incoming=[json.dumps(message) for message in incoming]
        )

        await client.handler()

        assert [message["seq"] for message in client.messages] == [1]
        assert client.ws.closed == (
            1011,
            "WebSocket subscription buffer overflow",
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "message",
    [
        {"type": "error", "msg": "failure"},
        {"type": "error", "msg": {"code": [], "msg": "failure"}},
        {"type": "error", "msg": {"code": {}, "msg": "failure"}},
        {"type": "error", "msg": {"code": True, "msg": "failure"}},
        {"type": "error", "msg": {"code": 10, "msg": []}},
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
        },
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 0,
            "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
        },
        {
            "type": "orderbook_snapshot",
            "sid": 0,
            "seq": 1,
            "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
        },
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 1,
            "msg": {"market_ticker": [], "market_id": MARKET_A_ID},
        },
        {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 1,
            "msg": {"market_ticker": "A", "market_id": {}},
        },
        {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 1,
            "msg": {
                "market_ticker": "A",
                "market_id": MARKET_A_ID,
                "price_dollars": [],
                "delta_fp": "1.00",
                "side": "yes",
            },
        },
        {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 1,
            "msg": {
                "market_ticker": "A",
                "market_id": MARKET_A_ID,
                "price_dollars": "0.5000",
                "side": "yes",
            },
        },
        {
            "type": "orderbook_delta",
            "sid": 1,
            "seq": 1,
            "msg": {
                "market_ticker": "A",
                "market_id": MARKET_A_ID,
                "price_dollars": "0.5000",
                "delta_fp": "1.00",
                "side": "bid",
            },
        },
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 1,
            "msg": {
                "market_ticker": "A",
                "market_id": "",
                "yes_dollars_fp": [["0.5000", "1.00"]],
            },
        },
        {
            "type": "orderbook_snapshot",
            "sid": [],
            "seq": 1,
            "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
        },
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": "1",
            "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
        },
    ],
)
def test_invalid_nested_fields_report_protocol_error(message):
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket()

        should_continue = await client._handle_protocol_message(message)

        assert should_continue is False
        assert isinstance(client.errors[0], KalshiWebSocketError)
        assert client.ws.closed == (1002, "Invalid message")

    asyncio.run(run())


def test_malformed_json_reports_protocol_error_and_closes():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket(incoming=["{"])

        await client.handler()

        assert isinstance(client.errors[0], KalshiWebSocketError)
        assert client.ws.closed == (1002, "Invalid JSON")

    asyncio.run(run())


def test_invalid_message_envelope_reports_protocol_error_and_closes():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket(incoming=["[]"])

        await client.handler()

        assert isinstance(client.errors[0], KalshiWebSocketError)
        assert client.ws.closed == (1002, "Invalid message")

    asyncio.run(run())


def test_connect_reports_clean_close_once(monkeypatch):
    async def run():
        websocket = FakeWebSocket(close_code=1000, close_reason="clean")
        module = importlib.import_module("fastkalshi.websocket.client")
        monkeypatch.setattr(module, "request_headers", lambda method, url: {})
        monkeypatch.setattr(
            module.websockets,
            "connect",
            lambda *args, **kwargs: FakeConnection(websocket),
        )
        client = RecordingClient()

        await client.connect()

        assert client.closes == [(1000, "clean")]

    asyncio.run(run())


def test_connect_reports_protocol_close_once(monkeypatch):
    async def run():
        websocket = FakeWebSocket(
            incoming=[
                json.dumps(
                    {
                        "type": "error",
                        "sid": 3,
                        "seq": 1,
                        "msg": {"code": 25, "msg": "buffer overflow"},
                    }
                )
            ]
        )
        module = importlib.import_module("fastkalshi.websocket.client")
        monkeypatch.setattr(module, "request_headers", lambda method, url: {})
        monkeypatch.setattr(
            module.websockets,
            "connect",
            lambda *args, **kwargs: FakeConnection(websocket),
        )
        client = RecordingClient()

        await client.connect()

        assert client.closes == [(1011, "WebSocket subscription buffer overflow")]

    asyncio.run(run())


def test_closed_market_snapshot_is_delivered_without_closing_socket():
    async def run():
        client = RecordingClient()
        client.ws = FakeWebSocket()
        # Captured from production for a determined market (2026-09-23).
        closed = {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 1,
            "msg": {
                "market_ticker": "KXMLBGAME-26SEP231310WSHDET-DET",
                "market_id": "",
            },
        }
        live = {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 2,
            "msg": {"market_ticker": "A", "market_id": MARKET_A_ID},
        }

        assert await client._handle_protocol_message(closed) is True
        assert await client._handle_protocol_message(live) is True

        assert [message["seq"] for message in client.messages] == [1, 2]
        assert client.ws.closed is None

    asyncio.run(run())
