"""Reconnection behaviour of the pooled Postgres backends.

Every other Postgres-touching test in this suite is offline, by design (see
``tests/test_api.py``'s module docstring) — but the bug this file guards against only
exists at the boundary those fakes skip: what happens when the *server* drops a
connection the pool still believes is healthy. Neon suspending its compute after a long
idle period is the case that motivated this (see ``PostgresRunPersistence``'s ``check=``
comment), and it cannot be reproduced against an in-process fake, only a real server.

So these tests are online and opt-in: they run only when ``PERIPLUS_DATABASE_URL`` (or
the bare ``DATABASE_URL``) points at a reachable Postgres, and skip otherwise — the same
shape ``pytest.mark.skipif``-gated integration tests take in most codebases, so CI
without database access still passes.

The failure is reproduced without waiting for a real idle timeout: a connection is
borrowed from the pool, and its live socket is severed directly (``shutdown(SHUT_RDWR)``
on the underlying fd) before being returned — indistinguishable, from the pool's side, to
a server that silently closed the connection while it sat idle. Every test below proves
one class recovers from exactly that.
"""

from __future__ import annotations

import os
import socket

import pytest
from psycopg_pool import AsyncConnectionPool

from periplus.storage.postgres import PostgresRunPersistence
from periplus.storage.postgres_artifacts import PostgresArtifactStore

DATABASE_URL = os.environ.get("PERIPLUS_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a reachable Postgres via PERIPLUS_DATABASE_URL or DATABASE_URL",
)


async def _sever(pool: AsyncConnectionPool) -> None:
    """Borrow the pool's connection and kill its socket out from under it, then return
    it — the pool's bookkeeping still sees a connection that was merely 'put back',
    never one that failed, exactly as it would after a server-side drop.
    """
    async with pool.connection() as conn:
        fd = conn.pgconn.socket
        sock = socket.socket(fileno=fd)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        finally:
            sock.detach()  # do not let the wrapper close psycopg's fd on GC


class TestReconnectOnBorrow:
    """Proves the fix directly: a pool without ``check=`` fails on the next query after
    its connection is severed; the same scenario against this package's pools (which do
    set ``check=``) succeeds instead.
    """

    async def test_unchecked_pool_fails_after_a_severed_connection(self) -> None:
        """Establishes the bug is real and reproducible, independent of any of this
        repo's code — a bare pool with no ``check=`` callback, psycopg_pool's default.
        """
        pool = AsyncConnectionPool(conninfo=DATABASE_URL, open=False, min_size=1, max_size=1)
        await pool.open()
        try:
            await _sever(pool)
            with pytest.raises(Exception):  # noqa: B017, PT011 - the exact psycopg
                # exception (OperationalError, "consuming input failed"/"server closed
                # the connection unexpectedly") is a libpq/SSL detail, not the contract
                # under test — only that an unchecked pool does surface a failure here.
                async with pool.connection() as conn:
                    await conn.execute("SELECT 1")
        finally:
            await pool.close()

    async def test_run_persistence_recovers_from_a_severed_connection(self) -> None:
        store = PostgresRunPersistence(DATABASE_URL)
        await store.open()
        try:
            await store.load_all()  # sanity: works before anything is severed
            await _sever(store._pool)
            # The call `/runs` makes. Before the fix this raised and the endpoint
            # returned 500; after it, the pool transparently swaps in a fresh
            # connection and the query succeeds.
            records = await store.load_all()
            assert isinstance(records, list)
        finally:
            await store.close()

    async def test_artifact_store_recovers_from_a_severed_connection(self) -> None:
        store = PostgresArtifactStore(DATABASE_URL)
        await store.open()
        try:
            await store.preload("nonexistent-run-id")  # sanity
            await _sever(store._pool)
            highest_attempt = await store.preload("nonexistent-run-id")
            assert highest_attempt == 0
        finally:
            await store.close()
