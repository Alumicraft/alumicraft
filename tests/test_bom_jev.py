import json
import threading
import urllib.error
from unittest.mock import patch

import pytest

from alumicraft.bom.jev import ASSEMBLIES, PURPOSES, classify_rows
from alumicraft.bom.jev import _payload_size, _question_payload, _split_batches


ROW = {
    "id": "line-1",
    "item_code": "SHOCK-01",
    "item_name": "Rear shock",
    "description": "Rear suspension shock absorber",
    "item_group": "Suspension",
    "project": "project-a",
    "project_description": "Standard off-road vehicle",
}


def _response_for(*, assembly="Suspension", purpose="Standard", confidence=0.92, input_tokens=17):
    return {
        "model": "jev-latest",
        "answers": {
            "row_0_assembly": {
                "type": "choice",
                "choice": assembly,
                "probabilities": {option: float(option == assembly) for option in ASSEMBLIES},
                "confidence": confidence,
            },
            "row_0_purpose": {
                "type": "choice",
                "choice": purpose,
                "probabilities": {option: float(option == purpose) for option in PURPOSES},
                "confidence": confidence,
            },
        },
        "usage": {"input_tokens": input_tokens, "output_tokens": 8},
    }


class _Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode("utf-8")

    def read(self):
        return self.value

    def close(self):
        pass


def test_sends_official_http_shape_and_maps_batched_answers():
    payloads = []

    def urlopen(request, timeout):
        payloads.append((json.loads(request.data), request, timeout))
        value = _response_for()
        value["answers"]["row_1_assembly"] = _response_for(assembly="Electrical")["answers"]["row_0_assembly"]
        value["answers"]["row_1_purpose"] = _response_for(purpose="Custom")["answers"]["row_0_purpose"]
        return _Response(value)

    row2 = dict(ROW, id="line-2", item_name="Battery", item_group="Electrical")
    with patch("alumicraft.bom.jev.urllib.request.urlopen", side_effect=urlopen):
        result = classify_rows(
            [ROW, row2],
            "Alumicraft standard off-road vehicle",
            {"api_key": "secret-key", "model": "jev-latest", "batch_size": 2, "concurrency": 1},
        )

    assert len(payloads) == 1
    body, request, timeout = payloads[0]
    assert body["model"] == "jev-latest"
    assert body["state"]["standard_description"] == "Alumicraft standard off-road vehicle"
    assert [row["id"] for row in body["state"]["rows"]] == ["line-1", "line-2"]
    assert body["questions"]["row_0_assembly"]["type"] == "choice"
    assert "Unknown" in body["questions"]["row_0_assembly"]["criteria"]
    assert body["questions"]["row_1_purpose"]["type"] == "choice"
    assert request.get_header("Authorization") == "Bearer secret-key"
    assert request.full_url == "https://api.typesafe.ai/v1/systemone"
    assert timeout == 20.0
    assert result["decisions"]["line-1"] == {
        "assembly": "Suspension", "purpose": "Standard", "confidence": 0.92, "review_required": False
    }
    assert result["decisions"]["line-2"]["assembly"] == "Electrical"
    assert result["metrics"]["requests"] == 1
    assert result["metrics"]["input_tokens"] == 17
    assert result["metrics"]["elapsed_seconds"] >= 0


def test_cache_is_reused_but_model_change_invalidates_it_and_callback_is_coordinating_thread():
    calls = []
    callbacks = []

    def urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return _Response(_response_for())

    config = {"api_key": "secret", "model": "jev-latest", "concurrency": 2}
    with patch("alumicraft.bom.jev.urllib.request.urlopen", side_effect=urlopen):
        first = classify_rows([ROW], "standard", config, on_result=lambda key, value: callbacks.append(threading.get_ident()))
        second = classify_rows([ROW], "standard", config, cache=first["cache"])
        third = classify_rows([ROW], "standard", dict(config, model="jev-next"), cache=first["cache"])

    assert len(calls) == 2
    assert second["metrics"]["requests"] == 0
    assert len(callbacks) == 1
    assert third["metrics"]["requests"] == 1


def test_request_cap_includes_retries_and_leaves_partial_batches_reviewable():
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        return _Response(_response_for())

    rows = [dict(ROW, id=f"line-{index}", item_name=f"Part {index}") for index in range(3)]
    with patch("alumicraft.bom.jev.urllib.request.urlopen", side_effect=urlopen):
        result = classify_rows(
            rows,
            "standard",
            {"api_key": "secret", "batch_size": 1, "concurrency": 1, "max_requests": 1},
        )

    assert len(calls) == 1
    assert result["metrics"]["requests"] == 1
    assert sum(decision["review_required"] for decision in result["decisions"].values()) == 2
    assert any("budget" in warning.lower() for warning in result["warnings"])


def test_transient_http_failure_retries_within_budget():
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, None)
        return _Response(_response_for())

    with patch("alumicraft.bom.jev.urllib.request.urlopen", side_effect=urlopen):
        result = classify_rows(
            [ROW], "standard", {"api_key": "secret", "max_requests": 2, "retry_backoff": 0}
        )

    assert len(calls) == 2
    assert result["metrics"]["requests"] == 2
    assert result["decisions"]["line-1"]["assembly"] == "Suspension"


def test_malformed_provider_answers_are_unknown_and_require_review():
    malformed = _response_for()
    malformed["answers"]["row_0_assembly"]["choice"] = "Not an allowed assembly"
    malformed["answers"]["row_0_purpose"].pop("confidence")
    with patch("alumicraft.bom.jev.urllib.request.urlopen", return_value=_Response(malformed)):
        result = classify_rows([ROW], "standard", {"api_key": "secret"})

    assert result["decisions"]["line-1"] == {
        "assembly": "Unknown", "purpose": "Unknown", "confidence": 0.0, "review_required": True
    }
    assert any("malformed" in warning.lower() for warning in result["warnings"])


def test_missing_probabilities_fails_row_without_caching_it():
    malformed = _response_for()
    malformed["answers"]["row_0_assembly"].pop("probabilities")
    with patch("alumicraft.bom.jev.urllib.request.urlopen", return_value=_Response(malformed)):
        result = classify_rows([ROW], "standard", {"api_key": "secret"})

    assert result["incomplete"] is True
    assert result["metrics"]["failed_rows"] == 1
    assert result["cache"] == {}
    assert result["decisions"]["line-1"]["review_required"] is True


def test_malformed_probability_distribution_fails_row_without_caching_it():
    malformed = _response_for()
    malformed["answers"]["row_0_purpose"]["probabilities"]["Standard"] = 0.5
    with patch("alumicraft.bom.jev.urllib.request.urlopen", return_value=_Response(malformed)):
        result = classify_rows([ROW], "standard", {"api_key": "secret"})

    assert result["incomplete"] is True
    assert result["metrics"]["failed_rows"] == 1
    assert result["cache"] == {}


def test_missing_key_never_calls_provider_and_marks_rows_unknown():
    with patch("alumicraft.bom.jev.urllib.request.urlopen") as urlopen:
        result = classify_rows([ROW], "standard", {})

    urlopen.assert_not_called()
    assert result["decisions"]["line-1"]["review_required"] is True
    assert any("api key" in warning.lower() for warning in result["warnings"])


def test_failed_fallback_is_not_cached_and_marks_result_incomplete():
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.URLError("private provider details")
        return _Response(_response_for())

    config = {"api_key": "secret", "max_requests": 1, "retry_backoff": 0}
    with patch("alumicraft.bom.jev.urllib.request.urlopen", side_effect=urlopen):
        failed = classify_rows([ROW], "standard", config)
        recovered = classify_rows([ROW], "standard", config, cache=failed["cache"])

    assert failed["incomplete"] is True
    assert failed["metrics"]["failed_rows"] == 1
    assert failed["cache"] == {}
    assert recovered["incomplete"] is False
    assert recovered["decisions"]["line-1"]["assembly"] == "Suspension"
    assert len(calls) == 2


def test_checkpoint_callback_errors_propagate():
    with patch("alumicraft.bom.jev.urllib.request.urlopen", return_value=_Response(_response_for())):
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            classify_rows([ROW], "standard", {"api_key": "secret"}, on_result=lambda *_: (_ for _ in ()).throw(RuntimeError("checkpoint failed")))


def test_returned_concrete_model_alias_is_recorded_without_invalidating_answers():
    response = _response_for()
    response["model"] = "jev-2026-09-18"
    with patch("alumicraft.bom.jev.urllib.request.urlopen", return_value=_Response(response)):
        result = classify_rows([ROW], "standard", {"api_key": "secret", "model": "jev-latest"})

    assert result["incomplete"] is False
    assert result["metrics"]["models"] == ["jev-2026-09-18"]
    assert result["decisions"]["line-1"]["review_required"] is False


def test_identical_contexts_are_deduplicated_and_duplicate_ids_error():
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        return _Response(_response_for())

    with patch("alumicraft.bom.jev.urllib.request.urlopen", side_effect=urlopen):
        result = classify_rows([ROW, dict(ROW, id="line-2")], "standard", {"api_key": "secret"})
    assert len(calls) == 1
    assert set(result["decisions"]) == {"line-1", "line-2"}

    with pytest.raises(ValueError, match="Duplicate row ids"):
        classify_rows([ROW, dict(ROW)], "standard", {"api_key": "secret"})


def test_recursive_payload_splitting_and_truncation_review():
    rows = [dict(ROW, id=f"line-{index}", item_name=f"Part {index}") for index in range(4)]
    one_row_size = _payload_size(_question_payload([rows[0]], "standard", "jev-latest"))
    with patch("alumicraft.bom.jev.MAX_PAYLOAD_BYTES", one_row_size + 1):
        batches = _split_batches(rows, 4, "standard", "jev-latest")
    assert len(batches) == 4
    assert all(_payload_size(_question_payload(batch, "standard", "jev-latest")) <= one_row_size + 1 for batch in batches)

    huge_row = dict(ROW, description="x" * 20_000)
    with patch("alumicraft.bom.jev.urllib.request.urlopen", return_value=_Response(_response_for())):
        result = classify_rows([huge_row], "standard", {"api_key": "secret"})
    assert result["incomplete"] is False
    assert result["decisions"]["line-1"]["review_required"] is True
    assert any("truncated" in warning.lower() for warning in result["warnings"])
