from __future__ import annotations

from types import SimpleNamespace as NS

import anthropic
import httpx
import httpx2
import openai
import pytest

from adaptive_harness.contracts import ModelUnavailable, load_config, resolve_model
from adaptive_harness.runner.models import AnthropicClient, OpenAICompatClient, build_client, extract_json

ENV = {"MODEL_PROVIDER": "anthropic", "MODEL_NAME": "default-model", "MODEL_NAME_RACE_ENGINEER": "big-model",
       "MODEL_NAME_STATISTICIAN": "", "MODEL_BASE_URL": ""}
SAMPLING = {"temperature", "top_p", "top_k"}


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeAnthropicSDK:
    def __init__(self, stop_reason="end_turn", text='{"action": "finish", "output": {"summary": "s"}}', error=None):
        self.requests, self.retrieved = [], []
        self.stop_reason, self.text, self.error = stop_reason, text, error
        self.models = NS(retrieve=self._retrieve)
        self.messages = NS(stream=self._stream)

    def _retrieve(self, model):
        self.retrieved.append(model)
        return NS(id=model, max_tokens=64000)

    def _stream(self, **kwargs):
        if self.error:
            raise self.error
        self.requests.append(kwargs)
        usage = NS(input_tokens=10, output_tokens=5, cache_creation_input_tokens=100, cache_read_input_tokens=1000)
        content = [NS(type="thinking", thinking=""), NS(type="text", text=self.text)]
        return FakeStream(NS(stop_reason=self.stop_reason, content=content, usage=usage, model=kwargs["model"]))


def resolved(env=ENV):
    return resolve_model(load_config().model, env)


def test_each_role_goes_to_its_model_and_unset_roles_use_the_default():
    sdk = FakeAnthropicSDK()
    client = AnthropicClient(resolved(), api_key=None, sdk_client=sdk)
    for role in ("race_engineer", "statistician", "data_engineer"):
        r = client.complete(role, "sys", [{"role": "user", "content": "go"}])
        assert r.model == {"race_engineer": "big-model"}.get(role, "default-model")
    assert [q["model"] for q in sdk.requests] == ["big-model", "default-model", "default-model"]


def test_anthropic_request_shape():
    sdk = FakeAnthropicSDK()
    client = AnthropicClient(resolved(), api_key=None, sdk_client=sdk)
    r = client.complete("statistician", "stable system", [{"role": "user", "content": "go"}])
    client.complete("data_engineer", "stable system", [{"role": "user", "content": "again"}])
    q = sdk.requests[0]
    assert q["max_tokens"] == 64000 and sdk.retrieved == ["default-model"]  # read once per model
    assert not SAMPLING & set(q)
    assert q["system"][0]["text"] == "stable system" and q["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert q["messages"] == [{"role": "user", "content": "go"}]
    assert r.parsed == {"action": "finish", "output": {"summary": "s"}}
    assert r.usage.input_tokens == 110 and r.usage.cache_read_tokens == 1000 and r.usage.output_tokens == 5
    assert r.stop_reason == "end_turn"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_anthropic_refusal_or_truncation_is_not_parsed(stop):
    client = AnthropicClient(resolved(), api_key=None, sdk_client=FakeAnthropicSDK(stop_reason=stop))
    r = client.complete("race_engineer", "sys", [{"role": "user", "content": "go"}])
    assert r.stop_reason == stop and r.parsed is None
    if stop == "refusal":
        assert r.text == ""


def test_anthropic_api_failure_raises_model_unavailable():
    req = httpx2.Request("POST", "https://example.invalid")
    err = anthropic.APIConnectionError(request=req)
    client = AnthropicClient(resolved(), api_key=None, sdk_client=FakeAnthropicSDK(error=err))
    with pytest.raises(ModelUnavailable):
        client.complete("race_engineer", "sys", [{"role": "user", "content": "go"}])


class FakeOpenAISDK:
    def __init__(self, refusal=None, finish_reason="stop"):
        self.requests = []
        self.refusal, self.finish_reason = refusal, finish_reason
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        msg = NS(content='```json\n{"action": "finish", "output": {"summary": "s"}}\n```', refusal=self.refusal)
        usage = NS(prompt_tokens=50, completion_tokens=7, prompt_tokens_details=NS(cached_tokens=40))
        return NS(choices=[NS(message=msg, finish_reason=self.finish_reason)], usage=usage, model=kwargs["model"])


def test_openai_compatible_request_shape():
    env = dict(ENV, MODEL_PROVIDER="openai_compatible", MODEL_BASE_URL="https://example.invalid/v1")
    sdk = FakeOpenAISDK()
    client = OpenAICompatClient(resolved(env), api_key=None, sdk_client=sdk)
    r = client.complete("race_engineer", "stable system", [{"role": "user", "content": "go"}])
    q = sdk.requests[0]
    assert "max_tokens" not in q and not SAMPLING & set(q)
    assert q["messages"][0] == {"role": "system", "content": "stable system"}
    assert q["model"] == "big-model" and r.model == "big-model"
    assert r.parsed == {"action": "finish", "output": {"summary": "s"}}
    assert r.usage.input_tokens == 10 and r.usage.cache_read_tokens == 40


def test_openai_refusal_and_truncation():
    client = OpenAICompatClient(resolved(), api_key=None, sdk_client=FakeOpenAISDK(refusal="no"))
    r = client.complete("race_engineer", "s", [{"role": "user", "content": "go"}])
    assert r.stop_reason == "refusal" and r.parsed is None and r.text == ""
    client = OpenAICompatClient(resolved(), api_key=None, sdk_client=FakeOpenAISDK(finish_reason="length"))
    assert client.complete("race_engineer", "s", [{"role": "user", "content": "go"}]).parsed is None


def test_openai_failure_raises_model_unavailable():
    class Boom(FakeOpenAISDK):
        def _create(self, **kwargs):
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

    client = OpenAICompatClient(resolved(), api_key=None, sdk_client=Boom())
    with pytest.raises(ModelUnavailable):
        client.complete("race_engineer", "s", [{"role": "user", "content": "go"}])


def test_build_client_selects_provider():
    assert isinstance(build_client(resolved(), {"MODEL_API_KEY": "test-key"}), AnthropicClient)
    env = dict(ENV, MODEL_PROVIDER="openai_compatible", MODEL_BASE_URL="https://example.invalid/v1")
    assert isinstance(build_client(resolved(env), {"MODEL_API_KEY": "test-key"}), OpenAICompatClient)
    with pytest.raises(ValueError):
        build_client(resolved(dict(ENV, MODEL_PROVIDER="nope")), {})


def test_extract_json():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here:\n```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    assert extract_json("no json") is None
