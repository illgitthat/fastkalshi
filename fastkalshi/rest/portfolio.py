from fastkalshi.auth import request_headers
from fastkalshi.constants import api_url

from .pagination import paginate
from .rest import (
    WRITE_SESSION,
    KalshiResponseContractError,
    delete,
    drop_none,
    get,
    post,
    put,
)


class Portfolio:
    def _authenticated_get_request(self, url: str, **kwargs):
        return get(url, headers=request_headers("GET", url), **kwargs)

    def _authenticated_post_request(self, url: str, data: dict, **kwargs):
        return post(
            url,
            headers=request_headers("POST", url),
            body=data,
            **kwargs,
        )

    def _authenticated_del_request(
        self,
        url: str,
        data: dict | None = None,
        **kwargs,
    ):
        return delete(
            url,
            headers=request_headers("DELETE", url),
            body=data,
            **kwargs,
        )

    def _authenticated_put_request(
        self,
        url: str,
        data: dict | None = None,
        **kwargs,
    ):
        return put(
            url,
            headers=request_headers("PUT", url),
            body=data,
            **kwargs,
        )

    def GetBalance(
        self,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/balance")
        return self._authenticated_get_request(
            url,
            **drop_none(
                {
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def Warmup(self):
        """Keep the order-write TLS session warm with a cheap read (exchange status).

        Not ``portfolio/balance``: that endpoint has its own 1 call/s bucket with
        no burst, so periodic warm-ups there make real balance reads fail with 429.
        """
        url = api_url("exchange/status")
        return get(
            url,
            headers=request_headers("GET", url),
            session=WRITE_SESSION,
        )

    def GetFills(
        self,
        ticker: str | None = None,
        order_id: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/fills")
        return self._authenticated_get_request(
            url,
            **drop_none(
                {
                    "ticker": ticker,
                    "order_id": order_id,
                    "min_ts": min_ts,
                    "max_ts": max_ts,
                    "limit": limit,
                    "cursor": cursor,
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def GetOrders(
        self,
        ticker: str | None = None,
        event_ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/orders")
        return self._authenticated_get_request(
            url,
            **drop_none(
                {
                    "ticker": ticker,
                    "event_ticker": event_ticker,
                    "min_ts": min_ts,
                    "max_ts": max_ts,
                    "status": status,
                    "cursor": cursor,
                    "limit": limit,
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def GetOrder(self, order_id: str):
        url = api_url(f"portfolio/orders/{order_id}")
        return self._authenticated_get_request(url)

    def GetOrderQueuePositions(
        self,
        market_tickers: list[str] | None = None,
        event_ticker: str | None = None,
        subaccount: int | None = None,
    ):
        if not market_tickers and event_ticker is None:
            raise ValueError("market_tickers or event_ticker is required")
        url = api_url("portfolio/orders/queue_positions")
        return self._authenticated_get_request(
            url,
            **drop_none(
                {
                    "market_tickers": (
                        ",".join(market_tickers) if market_tickers else None
                    ),
                    "event_ticker": event_ticker,
                    "subaccount": subaccount,
                }
            ),
        )

    def GetOrderQueuePosition(self, order_id: str):
        url = api_url(f"portfolio/orders/{order_id}/queue_position")
        return self._authenticated_get_request(url)

    def GetOrderGroups(self, subaccount: int | None = None):
        url = api_url("portfolio/order_groups")
        return self._authenticated_get_request(
            url,
            **drop_none({"subaccount": subaccount}),
        )

    def CreateOrderGroup(
        self,
        contracts_limit_fp: str,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/order_groups/create")
        return self._authenticated_post_request(
            url,
            drop_none(
                {
                    "contracts_limit_fp": contracts_limit_fp,
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def GetOrderGroup(
        self,
        order_group_id: str,
        subaccount: int | None = None,
    ):
        url = api_url(f"portfolio/order_groups/{order_group_id}")
        return self._authenticated_get_request(
            url,
            **drop_none({"subaccount": subaccount}),
        )

    def DeleteOrderGroup(
        self,
        order_group_id: str,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url(f"portfolio/order_groups/{order_group_id}")
        return self._authenticated_del_request(
            url,
            **drop_none(
                {
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def ResetOrderGroup(
        self,
        order_group_id: str,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url(f"portfolio/order_groups/{order_group_id}/reset")
        return self._authenticated_put_request(
            url,
            {},
            **drop_none(
                {
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def TriggerOrderGroup(
        self,
        order_group_id: str,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url(f"portfolio/order_groups/{order_group_id}/trigger")
        return self._authenticated_put_request(
            url,
            {},
            **drop_none(
                {
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def UpdateOrderGroupLimit(
        self,
        order_group_id: str,
        contracts_limit_fp: str,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url(f"portfolio/order_groups/{order_group_id}/limit")
        return self._authenticated_put_request(
            url,
            {"contracts_limit_fp": contracts_limit_fp},
            **drop_none(
                {
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def GetPositions(
        self,
        cursor: str | None = None,
        limit: int = 100,
        *,
        count_filter: str | None = None,
        ticker: str | None = None,
        event_ticker: str | None = None,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/positions")
        return self._authenticated_get_request(
            url,
            **drop_none(
                {
                    "cursor": cursor,
                    "limit": limit,
                    "count_filter": count_filter,
                    "ticker": ticker,
                    "event_ticker": event_ticker,
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def GetPortfolioSettlements(
        self,
        limit: int = 100,
        min_ts: int | None = None,
        max_ts: int | None = None,
        cursor: str | None = None,
        ticker: str | None = None,
        event_ticker: str | None = None,
        subaccount: int | None = None,
    ):
        url = api_url("portfolio/settlements")
        return self._authenticated_get_request(
            url,
            **drop_none(
                {
                    "limit": limit,
                    "min_ts": min_ts,
                    "max_ts": max_ts,
                    "cursor": cursor,
                    "ticker": ticker,
                    "event_ticker": event_ticker,
                    "subaccount": subaccount,
                }
            ),
        )

    GetSettlements = GetPortfolioSettlements

    def GetPortfolioRestingOrderTotalValue(self):
        url = api_url("exchange/status")
        return self._authenticated_get_request(url)

    def IntraExchangeInstanceTransfer(
        self,
        *,
        source: str,
        destination: str,
        amount_centicents: int,
        source_exchange_shard: int,
        destination_exchange_shard: int,
        source_subaccount: int = 0,
        destination_subaccount: int = 0,
    ):
        if source not in {"event_contract", "margined"}:
            raise ValueError("source must be event_contract or margined")
        if destination not in {"event_contract", "margined"}:
            raise ValueError("destination must be event_contract or margined")
        if (
            isinstance(amount_centicents, bool)
            or not isinstance(amount_centicents, int)
            or amount_centicents <= 0
        ):
            raise ValueError("amount_centicents must be a positive integer")
        for name, shard in (
            ("source_exchange_shard", source_exchange_shard),
            ("destination_exchange_shard", destination_exchange_shard),
        ):
            if (
                isinstance(shard, bool)
                or not isinstance(shard, int)
                or not 0 <= shard <= 100
            ):
                raise ValueError(f"{name} must be an integer from 0 to 100")
        for name, subaccount in (
            ("source_subaccount", source_subaccount),
            ("destination_subaccount", destination_subaccount),
        ):
            if (
                isinstance(subaccount, bool)
                or not isinstance(subaccount, int)
                or not 0 <= subaccount <= 63
            ):
                raise ValueError(f"{name} must be an integer from 0 to 63")
        if (source != "event_contract" or destination != "event_contract") and (
            source_subaccount != 0 or destination_subaccount != 0
        ):
            raise ValueError(
                "subaccounts are supported only for event_contract to "
                "event_contract transfers"
            )
        url = api_url("portfolio/intra_exchange_instance_transfer")
        response = self._authenticated_post_request(
            url,
            {
                "source": source,
                "destination": destination,
                "amount": amount_centicents,
                "source_exchange_shard": source_exchange_shard,
                "destination_exchange_shard": destination_exchange_shard,
                "source_subaccount": source_subaccount,
                "destination_subaccount": destination_subaccount,
            },
        )
        transfer_id = (
            response.get("transfer_id") if isinstance(response, dict) else None
        )
        if not isinstance(transfer_id, str) or not transfer_id.strip():
            raise KalshiResponseContractError(
                "POST",
                url,
                response if isinstance(response, dict) else {},
                "Kalshi transfer response did not contain transfer_id",
            )
        return response

    def GetIntraExchangeInstanceTransfers(
        self,
        limit: int = 100,
        cursor: str | None = None,
    ):
        url = api_url("portfolio/intra_exchange_instance_transfers")
        return self._authenticated_get_request(
            url,
            **drop_none({"limit": limit, "cursor": cursor}),
        )

    def GetIntraExchangeInstanceTransfer(self, transfer_id: str):
        if not isinstance(transfer_id, str) or not transfer_id.strip():
            raise ValueError("transfer_id is required")
        url = api_url(f"portfolio/intra_exchange_instance_transfers/{transfer_id}")
        return self._authenticated_get_request(url)

    def IterIntraExchangeInstanceTransfers(
        self,
        *,
        max_pages: int | None = None,
        **params,
    ):
        return paginate(
            self.GetIntraExchangeInstanceTransfers,
            "transfers",
            max_pages=max_pages,
            **params,
        )

    def GetTargetBalanceAllocation(self):
        url = api_url("portfolio/target_balance_allocation")
        return self._authenticated_get_request(url)

    def CreateOrder(
        self,
        ticker: str,
        side: str,
        count: str,
        price: str,
        time_in_force: str,
        self_trade_prevention_type: str,
        client_order_id: str | None = None,
        expiration_time: int | None = None,
        post_only: bool | None = None,
        cancel_order_on_pause: bool | None = None,
        reduce_only: bool | None = None,
        subaccount: int | None = None,
        order_group_id: str | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/events/orders")
        return self._authenticated_post_request(
            url,
            drop_none(
                {
                    "ticker": ticker,
                    "client_order_id": client_order_id,
                    "side": side,
                    "count": count,
                    "price": price,
                    "expiration_time": expiration_time,
                    "time_in_force": time_in_force,
                    "post_only": post_only,
                    "self_trade_prevention_type": self_trade_prevention_type,
                    "cancel_order_on_pause": cancel_order_on_pause,
                    "reduce_only": reduce_only,
                    "subaccount": subaccount,
                    "order_group_id": order_group_id,
                    "exchange_index": exchange_index,
                }
            ),
        )

    CreateOrderV2 = CreateOrder

    def BatchCreateOrders(self, orders: list[dict]):
        url = api_url("portfolio/events/orders/batched")
        return self._authenticated_post_request(url, {"orders": orders})

    BatchCreateOrdersV2 = BatchCreateOrders

    def AmendOrder(
        self,
        order_id: str,
        ticker: str,
        side: str,
        price: str,
        count: str,
        client_order_id: str | None = None,
        updated_client_order_id: str | None = None,
        subaccount: int | None = None,
        exchange_index: int | None = None,
    ):
        url = api_url(f"portfolio/events/orders/{order_id}/amend")
        return self._authenticated_post_request(
            url,
            drop_none(
                {
                    "ticker": ticker,
                    "side": side,
                    "price": price,
                    "count": count,
                    "client_order_id": client_order_id,
                    "updated_client_order_id": updated_client_order_id,
                    "exchange_index": exchange_index,
                }
            ),
            **drop_none({"subaccount": subaccount}),
        )

    AmendOrderV2 = AmendOrder

    def DecreaseOrder(
        self,
        order_id: str,
        reduce_by: str | None = None,
        reduce_to: str | None = None,
        subaccount: int | None = None,
        exchange_index: int | None = None,
        market_ticker: str | None = None,
    ):
        if (reduce_by is None) == (reduce_to is None):
            raise ValueError("provide exactly one of reduce_by or reduce_to")
        url = api_url(f"portfolio/events/orders/{order_id}/decrease")
        return self._authenticated_post_request(
            url,
            drop_none(
                {
                    "reduce_by": reduce_by,
                    "reduce_to": reduce_to,
                    "exchange_index": exchange_index,
                    "market_ticker": market_ticker,
                }
            ),
            **drop_none({"subaccount": subaccount}),
        )

    DecreaseOrderV2 = DecreaseOrder

    def CancelOrder(
        self,
        order_id: str,
        subaccount: int | None = None,
        exchange_index: int | None = None,
        market_ticker: str | None = None,
    ):
        url = api_url(f"portfolio/events/orders/{order_id}")
        return self._authenticated_del_request(
            url,
            **drop_none(
                {
                    "subaccount": subaccount,
                    "exchange_index": exchange_index,
                    "market_ticker": market_ticker,
                }
            ),
        )

    CancelOrderV2 = CancelOrder

    def BatchCancelOrders(self, orders: list[dict]):
        url = api_url("portfolio/events/orders/batched")
        return self._authenticated_del_request(url, {"orders": orders})

    BatchCancelOrdersV2 = BatchCancelOrders

    def CancelAllOrders(self, subaccount: int | None = None):
        url = api_url("portfolio/events/orders")
        return self._authenticated_del_request(
            url,
            **drop_none({"subaccount": subaccount}),
        )

    def CreateSubaccount(self, exchange_index: int | None = None):
        url = api_url("portfolio/subaccounts")
        return self._authenticated_post_request(
            url,
            drop_none({"exchange_index": exchange_index}),
        )

    def TransferBetweenSubaccounts(
        self,
        client_transfer_id: str,
        from_subaccount: int,
        to_subaccount: int,
        amount_cents: int,
        exchange_index: int | None = None,
    ):
        url = api_url("portfolio/subaccounts/transfer")
        return self._authenticated_post_request(
            url,
            drop_none(
                {
                    "client_transfer_id": client_transfer_id,
                    "from_subaccount": from_subaccount,
                    "to_subaccount": to_subaccount,
                    "amount_cents": amount_cents,
                    "exchange_index": exchange_index,
                }
            ),
        )

    def GetSubaccountBalances(self):
        url = api_url("portfolio/subaccounts/balances")
        return self._authenticated_get_request(url)

    def GetSubaccountTransfers(
        self,
        limit: int = 100,
        cursor: str | None = None,
    ):
        url = api_url("portfolio/subaccounts/transfers")
        return self._authenticated_get_request(
            url,
            **drop_none({"limit": limit, "cursor": cursor}),
        )

    def IterOrders(self, *, max_pages: int | None = None, **params):
        return paginate(
            self.GetOrders,
            "orders",
            max_pages=max_pages,
            **params,
        )

    def IterFills(self, *, max_pages: int | None = None, **params):
        return paginate(
            self.GetFills,
            "fills",
            max_pages=max_pages,
            **params,
        )

    def IterSubaccountTransfers(
        self,
        *,
        max_pages: int | None = None,
        **params,
    ):
        return paginate(
            self.GetSubaccountTransfers,
            "transfers",
            max_pages=max_pages,
            **params,
        )


portfolio = Portfolio()
