import asyncio
import base64
import hashlib
import struct
import zlib
from io import BytesIO

import pytest
from PIL import Image
from condor.agents.consult import (
    UnsupportedConsultImageError,
    _build_consult_prompt_input,
    supports_consult_image,
)
from condor.web.routes.agents import (
    MAX_CONSULT_IMAGE_BASE64_CHARS,
    MAX_CONSULT_IMAGE_BYTES,
    ConsultImage,
    ConsultRequest,
    _decode_consult_image,
)
from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai.messages import BinaryContent


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(10, 20, 30)).save(output, format=image_format)
    return output.getvalue()


PNG = _image_bytes("PNG")
JPEG = _image_bytes("JPEG")


def _corrupt_png() -> bytes:
    data = bytearray(PNG)
    idat_data = data.index(b"IDAT") + 4
    data[idat_data] ^= 0xFF
    return bytes(data)


def _excessive_dimension_png() -> bytes:
    data = bytearray(PNG)
    ihdr_data_start = data.index(b"IHDR") + 4
    ihdr_data_end = ihdr_data_start + 13
    data[ihdr_data_start : ihdr_data_start + 8] = struct.pack(">II", 100_000, 100_000)
    crc = zlib.crc32(b"IHDR" + data[ihdr_data_start:ihdr_data_end])
    data[ihdr_data_end : ihdr_data_end + 4] = struct.pack(">I", crc)
    return bytes(data)


def _image(data: bytes = PNG, **overrides) -> ConsultImage:
    values = {
        "media_type": "image/png",
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    values.update(overrides)
    return ConsultImage(**values)


def test_text_only_request_remains_backward_compatible():
    request = ConsultRequest(task="analyze", context="facts", chat_id=0)
    assert request.image is None


def test_valid_image_decodes_to_exact_bytes_and_builds_multimodal_input():
    data = _decode_consult_image(_image())
    prompt = _build_consult_prompt_input("structured facts", data)
    assert prompt[0] == "structured facts"
    assert isinstance(prompt[1], BinaryContent)
    assert prompt[1].data == PNG
    assert prompt[1].media_type == "image/png"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"data_base64": "%%%"}, "encoding"),
        ({"data_base64": ""}, "empty"),
        (
            {
                "data_base64": base64.b64encode(JPEG).decode("ascii"),
                "sha256": hashlib.sha256(JPEG).hexdigest(),
            },
            "PNG",
        ),
        ({"sha256": "0" * 64}, "digest"),
    ],
)
def test_invalid_image_payloads_fail_closed(overrides, error):
    with pytest.raises(HTTPException, match=error):
        _decode_consult_image(_image(**overrides))


def test_oversized_decoded_image_is_rejected():
    data = b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_CONSULT_IMAGE_BYTES - 7)
    with pytest.raises(HTTPException) as exc:
        _decode_consult_image(_image(data))
    assert exc.value.status_code == 413


def test_oversized_encoded_image_is_rejected_before_decode(monkeypatch):
    decode_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal decode_called
        decode_called = True
        raise AssertionError("decoder must not run")

    monkeypatch.setattr("condor.web.routes.agents.base64.b64decode", fail_if_called)
    with pytest.raises(ValidationError):
        ConsultImage(
            media_type="image/png",
            data_base64="A" * (MAX_CONSULT_IMAGE_BASE64_CHARS + 1),
            sha256="0" * 64,
        )
    assert decode_called is False


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n",
        b"\x89PNG\r\n\x1a\nnot-a-png",
        PNG[:-12],
        _corrupt_png(),
    ],
)
def test_structurally_invalid_png_is_rejected(data):
    with pytest.raises(HTTPException, match="invalid"):
        _decode_consult_image(_image(data))


def test_excessive_dimension_png_is_rejected_as_invalid_input():
    with pytest.raises(HTTPException, match="invalid") as captured:
        _decode_consult_image(_image(_excessive_dimension_png()))
    assert captured.value.status_code == 422


def test_media_type_and_sha_format_are_strict():
    with pytest.raises(ValidationError):
        _image(media_type="image/jpeg")
    with pytest.raises(ValidationError):
        _image(sha256="A" * 64)


def test_prompt_builder_does_not_embed_or_return_base64():
    prompt = _build_consult_prompt_input("facts", PNG)
    assert base64.b64encode(PNG).decode("ascii") not in repr(prompt)


def test_text_only_prompt_stays_plain_text():
    assert _build_consult_prompt_input("facts", None) == "facts"


def test_image_capability_is_exact_and_fail_closed():
    assert supports_consult_image("openai:gpt-5-mini") is True
    assert supports_consult_image("openrouter:openai/gpt-5-mini") is True
    assert supports_consult_image("openrouter:openai/gpt-5") is False
    assert supports_consult_image("custom@vision:gpt-5-mini") is False
    assert supports_consult_image("claude-code") is False


def test_supported_model_receives_exact_multimodal_bytes(monkeypatch):
    from condor.agents import consult
    from condor.agents.agent import Agent, AgentStore

    agent = Agent(
        slug="vision",
        name="Vision",
        description="",
        instructions="instructions",
        agent_key="openrouter:openai/gpt-5-mini",
    )
    received = None

    class FakeClient:
        async def start(self):
            pass

        async def prompt(self, prompt):
            nonlocal received
            received = prompt
            return "answer"

        async def stop(self):
            pass

    async def healthy(*args, **kwargs):
        return None

    monkeypatch.setattr(AgentStore, "get", lambda self, slug: agent)
    monkeypatch.setattr(consult, "healthcheck_local_backend", healthy)
    monkeypatch.setattr(consult, "PydanticAIClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "handlers.agents._shared.build_agent_context", lambda *a, **k: "facts"
    )

    answer = asyncio.run(
        consult._run_agent_to_completion(
            slug="vision",
            user_id=1,
            chat_id=0,
            server_name=None,
            task="task",
            image_data=PNG,
        )
    )

    assert answer == "answer"
    assert received[0] == "facts"
    assert isinstance(received[1], BinaryContent)
    assert received[1].data == PNG


@pytest.mark.parametrize("image_data", [PNG, None])
def test_unknown_pydantic_model_rejects_only_image_input(monkeypatch, image_data):
    from condor.agents import consult
    from condor.agents.agent import Agent, AgentStore

    agent = Agent(
        slug="unknown",
        name="Unknown",
        description="",
        instructions="instructions",
        agent_key="openrouter:unknown-model",
    )
    client_created = False

    class FakeClient:
        async def start(self):
            pass

        async def prompt(self, prompt):
            return "text answer"

        async def stop(self):
            pass

    async def healthy(*args, **kwargs):
        return None

    def make_client(**kwargs):
        nonlocal client_created
        client_created = True
        return FakeClient()

    monkeypatch.setattr(AgentStore, "get", lambda self, slug: agent)
    monkeypatch.setattr(consult, "healthcheck_local_backend", healthy)
    monkeypatch.setattr(consult, "PydanticAIClient", make_client)
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "handlers.agents._shared.build_agent_context", lambda *a, **k: "facts"
    )

    call = consult._run_agent_to_completion(
        slug="unknown",
        user_id=1,
        chat_id=0,
        server_name=None,
        task="task",
        image_data=image_data,
    )
    if image_data is not None:
        with pytest.raises(UnsupportedConsultImageError):
            asyncio.run(call)
        assert client_created is False
    else:
        assert asyncio.run(call) == "text answer"
        assert client_created is True


def test_acp_image_failure_is_explicit_before_client_use(monkeypatch, tmp_path):
    from condor.agents import consult
    from condor.agents.agent import Agent, AgentStore

    agent = Agent(
        slug="text-only",
        name="Text only",
        description="",
        instructions="instructions",
        agent_key="claude-code",
    )
    monkeypatch.setattr(AgentStore, "get", lambda self, slug: agent)
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("handlers.agents._shared.get_project_dir", lambda: tmp_path)

    with pytest.raises(UnsupportedConsultImageError):
        asyncio.run(
            consult._run_agent_to_completion(
                slug="text-only",
                user_id=1,
                chat_id=0,
                server_name=None,
                task="task",
                image_data=PNG,
            )
        )
