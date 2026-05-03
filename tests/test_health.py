"""Health endpoint tests."""


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "investment-mas"
    assert data["status"] == "ok"
    assert "time" in data


def test_frontend_index(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Investment MAS" in response.text
