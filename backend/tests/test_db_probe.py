"""Tests for the connection-layer probe.

WHY THIS EXISTS
---------------
MySQL reports a dead server, a refused socket, a proxy with nothing behind it
and a genuinely dropped query with the SAME message:

    (2013, 'Lost connection to MySQL server during query')

Reading that as a client problem cost two full ETL runs — it was chased through
connection pools, deadlock retries and isolation levels while the database
itself was simply not answering. The probe exists to name the layer, because
only some of them are ours to fix.
"""
from __future__ import annotations

import socket
import threading

import pytest

from scripts.db_probe import _ADVICE, probe


def _server(behaviour):
    """A throwaway TCP server on a free port. `behaviour(conn)` handles one client."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            behaviour(conn)
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def test_a_proxy_with_nothing_behind_it_is_reported_as_handshake():
    """THE case that burned two runs: the socket opens, then closes with no
    MySQL greeting. Railway's proxy answering while its database is stopped."""
    port = _server(lambda c: c.close())
    layer, detail = probe(f"mysql+pymysql://u:p@127.0.0.1:{port}/db")
    assert layer == "HANDSHAKE"
    assert "greeting" in detail


def test_nothing_listening_is_reported_as_tcp():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()                      # port now free → connection refused
    layer, _ = probe(f"mysql+pymysql://u:p@127.0.0.1:{port}/db")
    assert layer == "TCP"


def test_an_unresolvable_host_is_reported_as_dns():
    layer, _ = probe("mysql+pymysql://u:p@no-such-host.invalid:3306/db")
    assert layer == "DNS"


def test_a_url_without_a_host_is_rejected():
    layer, _ = probe("mysql+pymysql://")
    assert layer == "DNS"


@pytest.mark.parametrize("layer", ["DNS", "TCP", "HANDSHAKE", "AUTH"])
def test_every_failure_layer_carries_advice(layer):
    assert _ADVICE[layer]


def test_the_handshake_advice_says_it_is_not_a_client_problem():
    """The whole point: stop the next person tuning pools at a dead server."""
    advice = _ADVICE["HANDSHAKE"].lower()
    assert "not a client problem" in advice
    assert "pool" in advice
