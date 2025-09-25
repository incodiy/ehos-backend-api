from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_docs_available(client: AsyncClient) -> None:
    response = await client.get("/docs")
    assert response.status_code == 200


async def test_openapi_served(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"]