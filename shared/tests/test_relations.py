# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest

from livepatch_common.relations import (
    build_database_uri,
    cve_source_url,
    otlp_metrics_endpoint,
    pro_airgapped_server_address,
    strip_query_parameters,
)


def test_build_database_uri_uses_first_endpoint():
    uri = build_database_uri("user", "pass", "host:5432,host2:5432")
    assert uri == "postgresql://user:pass@host:5432/livepatch"


def test_build_database_uri_escapes_credentials():
    uri = build_database_uri("user@corp", "p@ss:w/rd", "host:5432")
    assert uri == "postgresql://user%40corp:p%40ss%3Aw%2Frd@host:5432/livepatch"


def test_build_database_uri_custom_db_name():
    uri = build_database_uri("u", "p", "host", db_name="livepatch-metrics-db")
    assert uri == "postgresql://u:p@host/livepatch-metrics-db"


def test_strip_query_parameters():
    assert strip_query_parameters("postgresql://u:p@host/db?sslmode=require") == "postgresql://u:p@host/db"
    assert strip_query_parameters("postgresql://u:p@host/db") == "postgresql://u:p@host/db"


def test_pro_airgapped_server_address_minimal():
    assert pro_airgapped_server_address({"hostname": "10.0.0.1"}) == "http://10.0.0.1"


def test_pro_airgapped_server_address_full():
    data = {"hostname": "airgap.internal", "scheme": "https", "port": "8443"}
    assert pro_airgapped_server_address(data) == "https://airgap.internal:8443"


def test_pro_airgapped_server_address_invalid():
    assert pro_airgapped_server_address({}) is None
    assert pro_airgapped_server_address({"hostname": ""}) is None


def test_cve_source_url():
    assert cve_source_url({"url": "http://cve:8080"}) == "http://cve:8080"
    assert cve_source_url({"url": ""}) is None
    assert cve_source_url({}) is None


def test_otlp_metrics_endpoint():
    assert otlp_metrics_endpoint({"endpoint": "http://otel:4318"}) == ("http://otel:4318", "http", True)
    data = {"endpoint": "http://otel:4318", "protocol": "grpc", "insecure": "false"}
    assert otlp_metrics_endpoint(data) == ("http://otel:4318", "grpc", False)
    assert otlp_metrics_endpoint({}) is None


def test_build_database_uri_attributes_not_ops_tied():
    # The shared helpers must not require any ops objects; only plain mappings.
    assert isinstance(build_database_uri("u", "p", "h"), str)
    assert build_database_uri("u", "p", "h") == strip_query_parameters(build_database_uri("u", "p", "h") + "?x=1")