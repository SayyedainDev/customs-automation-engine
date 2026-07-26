from fastapi.testclient import TestClient

from app.api.routes import documents, shipments
from app.main import app


client = TestClient(app)


def setup_function() -> None:
    shipments._shipments.clear()
    shipments._next_id = 1


def test_register_and_list_a_document() -> None:
    payload = {
        "file_name": "invoice_001.pdf",
        "document_type": "commercial_invoice",
        "exporter_name": "Sialkot Surgical Exports",
    }

    created = client.post("/documents/", json=payload)
    listed = client.get("/documents/")

    assert created.status_code == 201
    assert created.json()["id"] == 1
    assert created.json()["status"] == "pending_extraction"
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_compliant_shipment_is_approved() -> None:
    created = client.post(
        "/shipments/",
        json={
            "exporter_name": "Sialkot Surgical Exports",
            "destination_country": "Germany",
            "port_of_loading": "Karachi Port Trust",
            "currency": "USD",
            "declared_invoice_value": 500.0,
            "items": [
                {
                    "description": "Surgical scissors",
                    "hs_code": "9018.9010",
                    "quantity": 10,
                    "unit_price": 50.0,
                    "weight_kg": 4.5,
                }
            ],
        },
    )

    report = client.post(f"/shipments/{created.json()['id']}/validate")
    stored = client.get(f"/shipments/{created.json()['id']}")

    assert created.status_code == 201
    assert report.status_code == 200
    assert report.json()["is_compliant"] is True
    assert report.json()["issues"] == []
    assert stored.json()["status"] == "approved"


def test_incorrect_invoice_value_flags_shipment() -> None:
    created = client.post(
        "/shipments/",
        json={
            "exporter_name": "Demo Exporter",
            "destination_country": "UAE",
            "port_of_loading": "Port Qasim",
            "declared_invoice_value": 600.0,
            "items": [
                {
                    "description": "Cotton shirts",
                    "hs_code": "6105.1000",
                    "quantity": 10,
                    "unit_price": 50.0,
                    "weight_kg": 8.0,
                }
            ],
        },
    )

    report = client.post(f"/shipments/{created.json()['id']}/validate")

    assert report.status_code == 200
    assert report.json()["is_compliant"] is False
    assert report.json()["issues"][0]["severity"] == "critical"
    assert client.get("/shipments/1").json()["status"] == "flagged"


def test_unknown_records_return_not_found() -> None:
    assert client.get("/documents/999").status_code == 404
    assert client.get("/shipments/999").status_code == 404
