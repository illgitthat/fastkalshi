# fastkalshi

A small, dictionary-based Python client for Kalshi REST and WebSocket APIs.
It follows Kalshi OpenAPI 3.30.0 and the AsyncAPI specification dated
September 9, 2026.

## Install

```bash
uv add "fastkalshi @ git+https://github.com/illgitthat/fastkalshi.git"
```

Python 3.14 or newer is required. Demo is the default environment.
Install the optional plotting utilities with
`uv add "fastkalshi[analytics] @ git+https://github.com/illgitthat/fastkalshi.git"`.

After the first PyPI release, installation can use `uv add fastkalshi`.

For local development:

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run pytest -m schema
uv build
```

The schema suite is opt-in because it downloads the current Kalshi OpenAPI
and AsyncAPI files. It detects endpoint and WebSocket contract drift without
making normal tests depend on the network.

```python
import fastkalshi

fastkalshi.constants.use_prod()
fastkalshi.auth.set_key("API_KEY_ID", "path/to/private-key.pem")
```

Demo and production credentials are not interchangeable.

## Discover markets and events

```python
from fastkalshi.rest import market

live_markets = market.GetLiveMarkets(limit=100)
upcoming_events = market.GetUpcomingEvents(with_milestones=True)
metadata = market.GetEventMetadata("KXEVENT")
```

Event status is derived from its child markets. Use `GetLiveMarkets()` or
`GetUpcomingMarkets()` when exact market status matters. Paginated responses
include the next Kalshi cursor.

Bounded iterators handle cursors and reject cursor cycles:

```python
for item in market.IterMarkets(status="open", max_pages=3):
    print(item["ticker"])
```

Resolve structured target IDs from event milestones without building requests:

```python
from fastkalshi.rest import structured_target

targets = structured_target.GetStructuredTargets(
    ids=["target-id-1", "target-id-2"],
    page_size=2,
)
```

Structured-target requests use the same 10-second default as other REST
requests. Latency-sensitive callers can pass a shorter scalar timeout or a
`(connect, read)` timeout tuple.

## Trade with V2 orders

V2 orders use fixed-point strings. `bid` buys YES and `ask` sells YES.

```python
from fastkalshi.rest import portfolio

order = portfolio.CreateOrder(
    ticker="KXEXAMPLE-26-T1",
    side="bid",
    count="10.00",
    price="0.5600",
    time_in_force="good_till_canceled",
    self_trade_prevention_type="taker_at_cross",
    client_order_id="example-order-1",
    subaccount=1,
    exchange_index=-1,
)
```

The client never retries failed requests. A caller must reconcile an
uncertain write by `client_order_id` before it sends another order.
HTTP failures raise `fastkalshi.rest.KalshiAPIError` with the API status and
structured fields plus the raw payload. Transport failures raise
`KalshiTransportError`; its `outcome_unknown` flag is true for mutations that
may have reached Kalshi.
Invalid successful responses raise `KalshiResponseError` with the same
mutation uncertainty flag.
Mutation responses with status `408` or `5xx` are also marked uncertain.

Always provide a unique `client_order_id`. Validate prices against the
market's `price_ranges`, and inspect every result in a batch response.
Call `portfolio.Warmup()` during startup to prepare the dedicated mutation
connection before a time-sensitive order.

## Subaccounts and milestones

```python
from fastkalshi.rest import milestone, portfolio

balances = portfolio.GetSubaccountBalances()
milestones = milestone.GetMilestones(limit=100)
live_data = milestone.GetLiveData(milestones["milestones"][0]["id"])
```

Subaccounts require a supported Direct account and API tier.
Primary funds can be moved between exchange shards with
`IntraExchangeInstanceTransfer()` and verified through its status methods.
The transfer amount is in centicents (`10_000` = `$1.00`), both shard indexes
are required, and a returned transfer ID must be polled before using the funds.
Nonzero subaccounts are valid only for event-contract-to-event-contract
transfers.

## Rate-limit information

```python
from fastkalshi.rest import account

limits = account.GetLimits()
costs = account.GetEndpointCosts()
```

These methods expose Kalshi's current account buckets and endpoint token
costs. The SDK does not add sleeps or automatic rate limiting.

HTTP 429 responses raise `KalshiRateLimitError`, which preserves the raw
response and exposes parsed request ID and rate-limit headers when Kalshi
provides them. Kalshi currently does not guarantee rate-limit response headers.

Register an optional observer to capture response status, latency, headers,
and errors without changing request behavior:

```python
from fastkalshi.rest import KalshiRequestEvent, set_request_observer


def observe(event: KalshiRequestEvent) -> None:
    print(event.method, event.status_code, event.elapsed_seconds)


set_request_observer(observe)
```

Observer failures are isolated from API requests. Applications remain
responsible for retry and pacing policy, especially for uncertain mutations.

Order groups, queue positions, event live data, and cancel-all controls are
available from `portfolio` and `market`.
`exchange.GetUserDataTimestamp()` reports portfolio-data freshness for
reconciliation; it does not prove that an order is absent.

## WebSocket

```python
import asyncio
import fastkalshi


class Feed(fastkalshi.websocket.Client):
    async def on_open(self):
        await self.subscribe(
            ["orderbook_delta"],
            ["KXEXAMPLE-26-T1"],
            use_yes_price=True,
        )

    async def on_message(self, message):
        print(message)


fastkalshi.constants.use_prod()
asyncio.run(Feed().run_forever())
```

`run_forever()` reconnects and calls `on_open()` after each connection, so
the same subscription code restores the feed. A sequence gap drops the
out-of-sequence message and closes the socket so every subscribed market can
restart from a fresh snapshot.
Use `unsubscribe()`, `update_subscription()`, and `list_subscriptions()` for
typed subscription control. `send_command()` remains available for commands
that are not yet represented by a dedicated method.

## Compatibility

This release replaces legacy order writes with `/portfolio/events/orders`.
Old `action`, `type`, `yes_price`, and `no_price` arguments are not accepted.
`GetExchangeAnnouncements()` is retained only to report that Kalshi removed
the endpoint from the current specification.

The recommended `external-api` hosts are used by default. The older hosts
remain available through `use_legacy_demo()` and `use_legacy_prod()`.

## References

- [OpenAPI](https://docs.kalshi.com/openapi.yaml)
- [AsyncAPI](https://docs.kalshi.com/asyncapi.yaml)
- [API changelog](https://docs.kalshi.com/changelog)
- [API environments](https://docs.kalshi.com/getting_started/api_environments)
- [Fixed-point migration](https://docs.kalshi.com/getting_started/fixed_point_migration)
