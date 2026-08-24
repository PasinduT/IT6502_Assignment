from fastapi.testclient import TestClient

from app.dependencies import get_rag_service
from app.errors import AppError
from app.main import app
from app.schemas import ChatResponse


class FakeRag:
    async def answer(self, _):
        return ChatResponse(answer="Grounded answer [SOURCE_1]")


class FakeQuotaRag:
    async def answer(self, _):
        raise AppError(
            "GEMINI_USAGE_LIMIT",
            "The assistant has temporarily reached its Gemini usage limit. "
            "Please try again later.",
            429,
        )


client = TestClient(app)


def test_health_is_minimal():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_rejects_empty_messages():
    response = client.post("/api/chat", json={"messages": []})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_chat_rejects_non_text_content():
    response = client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": {"image": "x"}}]}
    )
    assert response.status_code == 422


def test_chat_uses_dependency():
    app.dependency_overrides[get_rag_service] = lambda: FakeRag()
    response = client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "What is VAT?"}]}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["answer"] == "Grounded answer [SOURCE_1]"


def test_chat_returns_clear_gemini_usage_limit_error():
    app.dependency_overrides[get_rag_service] = lambda: FakeQuotaRag()
    response = client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "What is VAT?"}]}
    )
    app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "GEMINI_USAGE_LIMIT",
            "message": (
                "The assistant has temporarily reached its Gemini usage limit. "
                "Please try again later."
            ),
        }
    }
