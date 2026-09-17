"""Deterministic, presentation-only wealth detail fixtures."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client() -> TestClient:
    main.reset_state()
    with TestClient(main.app) as test_client:
        yield test_client
    main.reset_state()


def test_all_products_expose_complete_mock_detail_shape(client: TestClient) -> None:
    assert all("data_source" not in product and "nav_series" not in product for product in main.WEALTH_PRODUCTS)
    response = client.get("/api/v1/wealth/products", headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 200, response.text
    products = response.json()["data"]
    assert len(products) == 4

    required = {
        "data_source", "as_of_date", "latest_nav", "daily_change_pct", "performance_history",
        "nav_series", "analysis_metrics", "manager", "asset_allocation", "established_at",
        "scale_billion", "manager_company", "purchase_rule", "redemption_rule", "confirm_time",
        "fee_rate", "summary", "announcement",
    }
    for product in products:
        assert required <= product.keys()
        assert product["data_source"] == "MOCK_FIXTURE"
        assert product["as_of_date"] == "2026-09-15"
        assert math.isfinite(product["latest_nav"]) and product["latest_nav"] > 0
        assert math.isfinite(product["daily_change_pct"])
        assert product["performance_history"]
        assert {"period", "product_return_pct", "benchmark_return_pct", "rank"} <= product["performance_history"][0].keys()

        series = product["nav_series"]
        assert len(series) >= 13
        assert [row["date"] for row in series] == sorted(row["date"] for row in series)
        assert series[-1]["date"] == product["as_of_date"]
        for row in series:
            assert math.isfinite(row["nav"]) and row["nav"] > 0
            assert math.isfinite(row["benchmark_nav"]) and row["benchmark_nav"] > 0

        history = {row["period"]: row for row in product["performance_history"]}
        for date, period in (("2026-08-15", "近1月"), ("2026-06-15", "近3月"), ("2025-09-15", "近1年")):
            row = next(item for item in series if item["date"] == date)
            product_return = (series[-1]["nav"] / row["nav"] - 1) * 100
            benchmark_return = (series[-1]["benchmark_nav"] / row["benchmark_nav"] - 1) * 100
            assert product_return == pytest.approx(history[period]["product_return_pct"], abs=0.001)
            assert benchmark_return == pytest.approx(history[period]["benchmark_return_pct"], abs=0.001)

        assert product["manager"]["name"]
        assert 1 <= product["analysis_metrics"][0]["score"] <= 5
        assert all(1 <= metric["score"] <= 5 for metric in product["analysis_metrics"])
        assert sum(item["percentage"] for item in product["asset_allocation"]) == pytest.approx(100.0)


def test_read_projections_cover_recommend_compare_and_legacy_base_records(client: TestClient) -> None:
    recommended = client.get("/api/v1/wealth/recommend?risk_profile=C2", headers={"X-User-Id": "user_zhang"})
    assert recommended.status_code == 200
    assert recommended.json()["data"]
    assert all(item["data_source"] == "MOCK_FIXTURE" for item in recommended.json()["data"])

    compared = client.get("/api/v1/wealth/compare?product_ids=CJ01002,AY90204", headers={"X-User-Id": "user_zhang"})
    assert compared.status_code == 200
    assert all("nav_series" in item for item in compared.json()["data"])

    legacy = {"product_id": "CJ01002", "name": "旧快照产品", "risk_level": "R1"}
    projected = main._wealth_product_view(legacy)
    assert projected["name"] == "旧快照产品"
    assert projected["data_source"] == "MOCK_FIXTURE"
    assert projected["nav_series"][-1]["date"] == "2026-09-15"
    unknown = {"product_id": "CUSTOM-1", "name": "自定义产品"}
    assert main._wealth_product_view(unknown) == unknown


def test_zy_detail_matches_high_fidelity_mock_contract(client: TestClient) -> None:
    response = client.get("/api/v1/wealth/products", headers={"X-User-Id": "user_zhang"})
    product = next(item for item in response.json()["data"] if item["product_id"] == "ZY18031")
    assert product["term"] == "180天运作期"
    assert (product["latest_nav"], product["annual_return_pct"], product["recent_month_return_pct"]) == (1.2761, 19.46, 3.89)
    assert product["net_value_date"] == "2026-09-15"
    history = {item["period"]: item for item in product["performance_history"]}
    assert {period: (row["product_return_pct"], row["peer_average_pct"], row["rank"]) for period, row in history.items()} == {
        "近1月": (3.89, 1.18, "58/420"), "近3月": (8.67, 3.42, "40/415"),
        "近6月": (-2.12, -4.25, "51/390"), "近1年": (19.46, 10.28, "49/360"),
        "成立以来": (36.95, 19.50, "36/280"),
    }
    assert [item["percentage"] for item in product["asset_allocation"]] == [74.5, 18.2, 7.3]
    assert len(product["top_holdings"]) == 5
    series = product["nav_series"]
    assert series[0] == {"date": "2000-09-01", "nav": 1.0, "benchmark_nav": 1.0}
    for date, period in (("2026-03-16", "近6月"), ("2000-09-01", "成立以来")):
        anchor = next(item for item in series if item["date"] == date)
        assert (series[-1]["nav"] / anchor["nav"] - 1) * 100 == pytest.approx(history[period]["product_return_pct"], abs=0.001)
        assert (series[-1]["benchmark_nav"] / anchor["benchmark_nav"] - 1) * 100 == pytest.approx(history[period]["benchmark_return_pct"], abs=0.001)


def test_product_listing_is_read_only_and_purchase_stays_red(client: TestClient) -> None:
    before = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-User-Id": "user_zhang"}).json()
    listed = client.get("/api/v1/wealth/products?user_id=user_zhang", headers={"X-User-Id": "user_zhang"})
    assert listed.status_code == 200
    after = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-User-Id": "user_zhang"}).json()
    assert after == before

    assessed = client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "horizon_years": 3, "loss_tolerance": "MEDIUM", "experience": "BASIC"},
        headers={"X-User-Id": "user_zhang"},
    )
    assert assessed.status_code == 200, assessed.text
    precheck = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "CJ01002", "amount": 100},
        headers={"X-User-Id": "user_zhang"},
    )
    assert precheck.status_code == 200, precheck.text
    assert precheck.json()["risk_level"] == "RED"
