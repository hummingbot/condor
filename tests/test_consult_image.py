import asyncio
import base64
import hashlib

import pytest
from condor.agents.consult import (
    UnsupportedConsultImageError,
    _build_consult_prompt_input,
)
from condor.web.routes.agents import (
    MAX_CONSULT_IMAGE_BYTES,
    ConsultImage,
    ConsultRequest,
    _decode_consult_image,
)
from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai.messages import BinaryContent


PNG = b"\x89PNG\r\n\x1a\n" + b"exact-image-bytes"


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
                "data_base64": base64.b64encode(b"not-png").decode("ascii"),
                "sha256": hashlib.sha256(b"not-png").hexdigest(),
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
    data = b"\x89PNG\r\n\x1a\n" + b"x" * MAX_CONSULT_IMAGE_BYTES
    with pytest.raises(HTTPException) as exc:
        _decode_consult_image(_image(data))
    assert exc.value.status_code == 413


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
