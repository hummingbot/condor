"""A failed transcription says so and nothing more (WR-09).

The exception text is a local model path or a download URL; it belongs in the
server log, not in the 500 the browser renders.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import transcribe as routes


def test_the_failure_reason_stays_out_of_the_response(monkeypatch):
    import utils.transcribe

    async def boom(data, language=None, model_size="base"):
        raise RuntimeError("cannot load /home/owner/.cache/whisper/small.pt")

    monkeypatch.setattr(utils.transcribe, "transcribe_voice", boom)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=1, username="u", first_name="U", role="user"
    )

    res = TestClient(app).post(
        "/transcribe",
        files={"file": ("a.webm", b"abc", "audio/webm")},
        data={"language": "en", "model": "small"},
    )

    assert res.status_code == 500
    assert res.json() == {"detail": "Transcription failed"}
