"""GraphQL client for Andersen EV API using gql library."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
    TransportQueryError,
    TransportServerError,
)
from graphql import DocumentNode

from . import const

_LOGGER = logging.getLogger(__name__)

# The integration's top-level logger whose effective level we check.
_INTEGRATION_LOGGER = logging.getLogger("custom_components.andersen_ev")

# Quiet the gql transport logger (INFO-level HTTP lifecycle messages) unless
# the integration itself is set to DEBUG, in which case let INFO through too.
_gql_transport_logger = logging.getLogger("gql.transport.aiohttp")
_gql_transport_logger.setLevel(logging.DEBUG)  # let the filter decide


class _GqlTransportFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    """Allow gql transport INFO logs only when the integration is at DEBUG."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter log records based on integration log level."""
        if record.levelno >= logging.WARNING:
            return True
        # INFO (and DEBUG) from gql only when the integration is in debug mode
        return _INTEGRATION_LOGGER.isEnabledFor(logging.DEBUG)


_gql_transport_logger.addFilter(_GqlTransportFilter())


class GraphQLClient:
    """Async GraphQL client for Andersen EV API using gql[aiohttp].

    Maintains a persistent session and handles token refresh automatically,
    both reactively (on HTTP 401) and proactively (via a scheduled timer
    that fires 5 minutes before the token expires).
    """

    def __init__(
        self,
        token: str,
        token_refresh: Callable[[], Any],
        url: str = const.GRAPHQL_URL,
        token_expiry_time: float | None = None,
    ) -> None:
        """Initialize the GraphQL client."""
        self._token = token
        self.url = url
        self._token_refresh = token_refresh
        self._client: Client | None = None
        self._session = None
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._initial_expiry_time = token_expiry_time

    @property
    def token(self) -> str:
        """Return the current bearer token."""
        return self._token

    # -- connection management ---------------------------------------------

    async def _ensure_connected(self) -> None:
        """Lazily create and connect the gql client session."""
        if self._session is not None:
            return

        transport = AIOHTTPTransport(
            url=self.url,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        self._client = Client(
            transport=transport,
            fetch_schema_from_transport=False,
        )
        self._session = await self._client.connect_async()

        # Schedule proactive refresh on first connect if we know expiry
        if self._initial_expiry_time is not None:
            self._schedule_token_refresh(self._initial_expiry_time)
            self._initial_expiry_time = None

    async def _reconnect_with_token(self, token: str) -> None:
        """Close the current session and reconnect with a new token."""
        self._token = token

        if self._client is not None:
            try:
                await self._client.close_async()
            except (TransportServerError, TransportQueryError, OSError) as err:
                _LOGGER.debug("Error closing client during reconnect: %s", err)

        self._client = None
        self._session = None
        await self._ensure_connected()

    async def close(self) -> None:
        """Close the client session and cancel any pending refresh timer."""
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

        if self._client is not None:
            try:
                await self._client.close_async()
            except (TransportServerError, TransportQueryError, OSError) as err:
                _LOGGER.debug("Error closing client: %s", err)

        self._client = None
        self._session = None

    # -- execution ---------------------------------------------------------

    @staticmethod
    def _parse_document(query: str) -> DocumentNode:
        """Parse a GraphQL query string into a DocumentNode."""
        return gql(query)

    async def execute_query(
        self,
        operation_name: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query.

        Handles 401 auth failures by refreshing the token and retrying once.

        Returns:
            The ``data`` portion of the response, or *None* on error.
        """
        document = self._parse_document(query)

        try:
            await self._ensure_connected()
            return await self._session.execute(
                document,
                variable_values=variables,
                operation_name=operation_name,
            )
        except TransportServerError as err:
            if err.code != 401:
                _LOGGER.warning("Failed %s, HTTP status code: %s", operation_name, err.code)
                return None

            # 401: refresh token and retry once
            _LOGGER.debug(
                "Token expired during %s, refreshing and retrying",
                operation_name,
            )
            try:
                await self._refresh_and_reconnect()
                return await self._session.execute(
                    document,
                    variable_values=variables,
                    operation_name=operation_name,
                )
            except (
                TransportServerError,
                TransportQueryError,
                OSError,
            ) as retry_err:
                _LOGGER.error(
                    "Retry after token refresh failed for %s: %s",
                    operation_name,
                    retry_err,
                )
                return None
        except TransportQueryError as err:
            _LOGGER.warning("GraphQL errors in %s: %s", operation_name, err.errors)
            return None
        except OSError as err:
            _LOGGER.error("Error executing GraphQL query %s: %s", operation_name, err)
            return None

    async def execute_mutation(
        self,
        operation_name: str,
        mutation: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL mutation (delegates to execute_query)."""
        return await self.execute_query(operation_name, mutation, variables)

    # -- token refresh -----------------------------------------------------

    async def _refresh_and_reconnect(self) -> None:
        """Call the token-refresh callback and reconnect with new credentials."""
        token, expiry_time = await self._token_refresh()
        await self._reconnect_with_token(token)
        if expiry_time:
            self._schedule_token_refresh(expiry_time)

    def _schedule_token_refresh(self, expiry_time: float) -> None:
        """Schedule an automatic token refresh 5 minutes before expiry."""
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

        delay = expiry_time - time.time() - 300  # 5 minutes before expiry

        if delay <= 0:
            _LOGGER.debug("Token near expiry, scheduling immediate refresh")
            self._refresh_handle = None  # no timer, task runs immediately
            asyncio.ensure_future(self._proactive_refresh())  # noqa: RUF006
            return

        _LOGGER.debug("Scheduled proactive token refresh in %d seconds", int(delay))
        loop = asyncio.get_running_loop()
        self._refresh_handle = loop.call_later(
            delay,
            lambda: asyncio.ensure_future(self._proactive_refresh()),
        )

    async def _proactive_refresh(self) -> None:
        """Proactive refresh triggered by the scheduled timer."""
        self._refresh_handle = None
        try:
            _LOGGER.debug("Proactive token refresh triggered")
            await self._refresh_and_reconnect()
            _LOGGER.debug("Proactive token refresh completed successfully")
        except (TransportServerError, TransportQueryError, OSError) as err:
            _LOGGER.warning("Proactive token refresh failed: %s", err)
