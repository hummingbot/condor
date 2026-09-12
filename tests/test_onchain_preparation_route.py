"""The preparation POST must remain separate from execution and its deed log."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from condor.web.routes import executors


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["venues", "market", "position", "prepare"])
async def test_preparation_only_forwards_unsigned_query(monkeypatch, operation):
    post = AsyncMock(return_value={"result": {"instructions": []}})
    client = SimpleNamespace(executors=SimpleNamespace(_post=post))
    manager = SimpleNamespace(get_client=AsyncMock(return_value=client))
    deed = Mock()
    monkeypatch.setattr(executors, "get_config_manager", lambda: manager)
    monkeypatch.setattr(executors, "record_ui_deed", deed)
    request = executors.OnchainPreparationRequest(
        operation=operation, arguments={"market": "vault"}
    )
    result = await executors.onchain_preparation("local", request, user=object())
    post.assert_awaited_once_with(
        "/executors/onchain/prepare",
        json={"operation": operation, "arguments": {"market": "vault"}},
    )
    assert result == {"result": {"instructions": []}}
    deed.assert_not_called()


def test_preparation_cannot_request_commit():
    with pytest.raises(ValidationError):
        executors.OnchainPreparationRequest(operation="commit")
    with pytest.raises(ValidationError):
        executors.OnchainPreparationRequest(operation="prepare", commit=True)
