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
    # Five fictional products, one per risk level R1-R5.
    assert len(products) == 5
    assert [item["risk_level"] for item in products] == ["R1", "R2", "R3", "R4", "R5"]

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

    compared = client.get("/api/v1/wealth/compare?product_ids=LX00118,HQ30060", headers={"X-User-Id": "user_zhang"})
    assert compared.status_code == 200
    assert all("nav_series" in item for item in compared.json()["data"])

    legacy = {"product_id": "LX00118", "name": "旧快照产品", "risk_level": "R1"}
    projected = main.wealth_product_view(legacy)
    assert projected["name"] == "旧快照产品"
    assert projected["data_source"] == "MOCK_FIXTURE"
    assert projected["nav_series"][-1]["date"] == "2026-09-15"
    unknown = {"product_id": "CUSTOM-1", "name": "自定义产品"}
    assert main.wealth_product_view(unknown) == unknown


def test_sg_detail_matches_its_fixture(client: TestClient) -> None:
    response = client.get("/api/v1/wealth/products", headers={"X-User-Id": "user_zhang"})
    product = next(item for item in response.json()["data"] if item["product_id"] == "SG20270")
    assert product["name"] == "双桂平衡·股债270天" and product["term"] == "每 270 天开放一次申赎"
    assert (product["latest_nav"], product["annual_return_pct"], product["recent_month_return_pct"]) == (1.1638, 7.85, 1.12)
    assert product["net_value_date"] == "2026-09-15"
    history = {item["period"]: item for item in product["performance_history"]}
    assert {period: (row["product_return_pct"], row["peer_average_pct"], row["rank"]) for period, row in history.items()} == {
        "近1周": (0.31, 0.22, "33/310"), "近1月": (1.12, 0.75, "41/310"), "近3月": (2.95, 2.05, "38/305"),
        "近6月": (-0.84, -1.65, "47/298"), "近1年": (7.85, 5.90, "36/285"), "成立以来": (16.38, 12.10, "28/240"),
    }
    assert [item["percentage"] for item in product["asset_allocation"]] == [77.0, 18.5, 4.5]
    assert len(product["top_holdings"]) == 5
    series = product["nav_series"]
    assert series[0] == {"date": "2021-11-01", "nav": 1.0, "benchmark_nav": 1.0}
    for date, period in (("2026-03-16", "近6月"), ("2021-11-01", "成立以来")):
        anchor = next(item for item in series if item["date"] == date)
        assert (series[-1]["nav"] / anchor["nav"] - 1) * 100 == pytest.approx(history[period]["product_return_pct"], abs=0.001)
        assert (series[-1]["benchmark_nav"] / anchor["benchmark_nav"] - 1) * 100 == pytest.approx(history[period]["benchmark_return_pct"], abs=0.001)


def test_every_name_in_the_catalogue_is_fictional(client: TestClient) -> None:
    response = client.get("/api/v1/wealth/products", headers={"X-User-Id": "user_zhang"})
    for product in response.json()["data"]:
        assert product["manager_company"] == "星河理财有限公司"
        assert "虚构" in product["manager"]["bio"] and "虚构" in product["announcement"]
        assert all(holding["code"].startswith("Z") for holding in product["top_holdings"])


def test_a_saved_ledger_does_not_bring_back_retired_products(tmp_path, monkeypatch) -> None:
    from bank_core import persistence

    store = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(store))
    main.reset_state()
    main.WEALTH_PRODUCTS.append({"product_id": "OLD00001", "name": "已下架产品", "risk_level": "R1"})
    persistence.persist_ledger()
    main.WEALTH_PRODUCTS[:] = [row for row in main.WEALTH_PRODUCTS if row["product_id"] != "OLD00001"]
    summary = persistence.load_ledger_store(str(store))
    assert summary["loaded_state"] is True
    assert [row["product_id"] for row in main.WEALTH_PRODUCTS] == ["LX00118", "HQ30060", "SG20270", "YH50365", "XH70730"]
    monkeypatch.delenv("BANK_LEDGER_STORE_PATH")
    main.reset_state()


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
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 100},
        headers={"X-User-Id": "user_zhang"},
    )
    assert precheck.status_code == 200, precheck.text
    assert precheck.json()["risk_level"] == "RED"


def test_the_r5_product_is_only_offered_to_an_aggressive_profile(client: TestClient) -> None:
    def offered(profile):
        response = client.get(f"/api/v1/wealth/recommend?risk_profile={profile}", headers={"X-User-Id": "user_zhang"})
        assert response.status_code == 200, response.text
        return [item["product_id"] for item in response.json()["data"]]

    assert "XH70730" in offered("C5")
    assert "XH70730" not in offered("C4") and "YH50365" in offered("C4")
    assert offered("C1") == ["LX00118"]
