"""Small, dependency-free adapter for TypeSafe Jev classification.

The adapter deliberately owns no Frappe concerns.  It turns purchase rows into
constrained Choice questions, sends bounded batches to TypeSafe's HTTP API,
and returns conservative suggestions for a caller to review.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Mapping


API_URL = "https://api.typesafe.ai/v1/systemone"
PROMPT_VERSION = "jev-bom-v1"

ASSEMBLIES = (
    "Chassis and fabrication",
    "Suspension",
    "Drivetrain",
    "Brakes and steering",
    "Electrical",
    "Cooling and fuel",
    "Interior and body",
    "Wheels and tires",
    "Consumables",
    "Unknown",
)
PURPOSES = ("Standard", "Custom", "Spare", "Rework", "Unknown")

# These limits keep a caller's historical descriptions from consuming the
# whole request budget.  The API permits substantially larger requests, but a
# smaller ceiling leaves room for question text and keeps retries cheap.
MAX_PAYLOAD_BYTES = 64_000
MAX_FIELD_CHARS = 1_000
MAX_STANDARD_CHARS = 2_000
MAX_BATCH_SIZE = 50
DEFAULT_BATCH_SIZE = 10
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_REQUESTS = 20
DEFAULT_TIMEOUT = 20.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.70
MAX_RETRY_ATTEMPTS = 3

_ROW_FIELDS = (
    "id",
    "item_code",
    "item_name",
    "description",
    "item_group",
    "project",
    "project_description",
)

_ASSEMBLY_CRITERIA = {
    "Chassis and fabrication": "Frame, chassis structure, brackets, mounts, and fabrication work.",
    "Suspension": "Springs, shocks, links, suspension arms, and suspension hardware.",
    "Drivetrain": "Engine, transmission, driveshaft, axles, and drivetrain hardware.",
    "Brakes and steering": "Braking, steering, hubs, and related control hardware.",
    "Electrical": "Wiring, batteries, controls, lighting, sensors, and electrical hardware.",
    "Cooling and fuel": "Cooling, radiator, hoses, fuel storage, and fuel delivery.",
    "Interior and body": "Body panels, seats, trim, weather sealing, and cabin/interior items.",
    "Wheels and tires": "Wheels, tires, tubes, and wheel hardware.",
    "Consumables": "Shop supplies, fluids, fasteners, abrasives, and items consumed during work.",
    "Unknown": "Insufficient evidence to classify safely.",
}

_PURPOSE_CRITERIA = {
    "Standard": "Normally required for the described standard vehicle configuration.",
    "Custom": "A project-specific or optional customization beyond the standard configuration.",
    "Spare": "A replacement or extra item held as a spare rather than installed in the vehicle.",
    "Rework": "An item purchased to correct, replace, or redo earlier work.",
    "Unknown": "Insufficient evidence to determine the purchase purpose.",
}


class _RequestFailure(Exception):
    """Internal failure with only safe, non-provider-derived details."""

    def __init__(self, kind: str, status: int | None = None, retryable: bool = False):
        self.kind = kind
        self.status = status
        self.retryable = retryable
        detail = f"status {status}" if status is not None else kind
        super().__init__(detail)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _bounded_text(value: Any, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        text = str(value)
    text = text.strip()
    return text[:limit], len(text) > limit


def _safe_text(value: Any, limit: int = MAX_FIELD_CHARS) -> str | None:
    return _bounded_text(value, limit)[0]


def _normalise_row(row: Mapping[str, Any], index: int) -> tuple[str, dict[str, Any], str | None, bool]:
    raw_id = row.get("id")
    row_id = _safe_text(raw_id, MAX_FIELD_CHARS) or f"row_{index}"
    warning = None if raw_id is not None else f"Row {index} has no id; assigned {row_id}."
    normalised: dict[str, Any] = {}
    truncated = False
    for field in _ROW_FIELDS:
        value, was_truncated = _bounded_text(row.get(field), MAX_FIELD_CHARS)
        normalised[field] = value
        truncated = truncated or was_truncated
    normalised["id"] = row_id
    return row_id, normalised, warning, truncated


def _normalise_standard_description(value: Any) -> str:
    return (_safe_text(value, MAX_STANDARD_CHARS) or "").strip()


def _normalise_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    model = _safe_text(config.get("model"), 128) or "jev-latest"
    batch_size = _bounded_int(config.get("batch_size"), DEFAULT_BATCH_SIZE, 1, MAX_BATCH_SIZE)
    concurrency = _bounded_int(config.get("concurrency"), DEFAULT_CONCURRENCY, 1, 32)
    max_requests = _bounded_int(config.get("max_requests"), DEFAULT_MAX_REQUESTS, 0, 100_000)
    timeout = _bounded_float(config.get("timeout"), DEFAULT_TIMEOUT, 0.1, 300.0)
    threshold = _bounded_float(
        config.get("confidence_threshold"), DEFAULT_CONFIDENCE_THRESHOLD, 0.0, 1.0
    )
    retry_backoff = _bounded_float(config.get("retry_backoff"), 0.15, 0.0, 2.0)
    # The production endpoint is intentionally fixed. Tests can mock
    # ``urllib.request.urlopen`` without making the destination configurable.
    endpoint = API_URL
    return {
        "api_key": config.get("api_key"),
        "model": model,
        "batch_size": batch_size,
        "concurrency": concurrency,
        "max_requests": max_requests,
        "timeout": timeout,
        "confidence_threshold": threshold,
        "retry_backoff": retry_backoff,
        "endpoint": endpoint,
    }


def _bounded_int(value: Any, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, number))


def _bounded_float(value: Any, default: float, lower: float, upper: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(lower, min(upper, number))


def _cache_key(
    row: Mapping[str, Any],
    standard_description: str,
    model: str,
    *,
    context_truncated: bool = False,
) -> str:
    context = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "standard_description": standard_description,
        "context_truncated": context_truncated,
        # The source row id is bookkeeping and must not prevent equivalent
        # purchase descriptions from sharing a cached classification.
        "row": {field: row.get(field) for field in _ROW_FIELDS if field != "id"},
        "assembly_options": ASSEMBLIES,
        "purpose_options": PURPOSES,
    }
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _unknown_decision() -> dict[str, Any]:
    return {"assembly": "Unknown", "purpose": "Unknown", "confidence": 0.0, "review_required": True}


def _validate_decision(decision: Any) -> dict[str, Any] | None:
    if not isinstance(decision, Mapping):
        return None
    assembly = decision.get("assembly")
    purpose = decision.get("purpose")
    confidence = decision.get("confidence")
    review_required = decision.get("review_required")
    if assembly not in ASSEMBLIES or purpose not in PURPOSES or not _finite_number(confidence):
        return None
    if not 0.0 <= float(confidence) <= 1.0 or not isinstance(review_required, bool):
        return None
    return {
        "assembly": assembly,
        "purpose": purpose,
        "confidence": float(confidence),
        "review_required": review_required,
    }


def _decision_from_answers(
    assembly_answer: Any,
    purpose_answer: Any,
    threshold: float,
    *,
    context_truncated: bool = False,
) -> tuple[dict[str, Any], str | None]:
    def choice(answer: Any, allowed: tuple[str, ...]) -> tuple[str | None, float | None]:
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            return None, None
        selected = answer.get("choice")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, Mapping) or set(probabilities) != set(allowed):
            return None, None
        probability_values = list(probabilities.values())
        if (
            not all(_finite_number(value) and 0.0 <= float(value) <= 1.0 for value in probability_values)
            or not math.isclose(sum(float(value) for value in probability_values), 1.0, rel_tol=0.0, abs_tol=1e-6)
        ):
            return None, None
        if selected not in allowed or not _finite_number(confidence):
            return None, None
        confidence_float = float(confidence)
        if not 0.0 <= confidence_float <= 1.0:
            return None, None
        return selected, confidence_float

    assembly, assembly_confidence = choice(assembly_answer, ASSEMBLIES)
    purpose, purpose_confidence = choice(purpose_answer, PURPOSES)
    if assembly is None or purpose is None:
        return _unknown_decision(), "Provider returned a missing or malformed classification answer."
    confidence = min(assembly_confidence, purpose_confidence)
    return {
        "assembly": assembly,
        "purpose": purpose,
        "confidence": confidence,
        "review_required": (
            assembly == "Unknown"
            or purpose == "Unknown"
            or confidence < threshold
            or context_truncated
        ),
    }, None


def _question_payload(batch: list[dict[str, Any]], standard_description: str, model: str) -> dict[str, Any]:
    state = {"standard_description": standard_description, "rows": batch}
    questions: dict[str, dict[str, Any]] = {}
    for index, _row in enumerate(batch):
        questions[f"row_{index}_assembly"] = {
            "type": "choice",
            "instructions": (
                f"Classify `rows[{index}]` into exactly one assembly for the vehicle in "
                "`standard_description`. Use the row's item and project context. "
                "Choose Unknown when evidence is insufficient; this is a suggestion for human review."
            ),
            "criteria": _ASSEMBLY_CRITERIA,
        }
        questions[f"row_{index}_purpose"] = {
            "type": "choice",
            "instructions": (
                f"Classify the purchase purpose of `rows[{index}]` in the context of "
                "`standard_description`. Choose exactly one purpose and choose Unknown "
                "when evidence is insufficient; this is a suggestion for human review."
            ),
            "criteria": _PURPOSE_CRITERIA,
        }
    return {"state": state, "model": model, "questions": questions}


def _payload_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _split_batches(rows: list[dict[str, Any]], batch_size: int, standard_description: str, model: str) -> list[list[dict[str, Any]]]:
    def split(batch: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        if len(batch) <= 1 or _payload_size(_question_payload(batch, standard_description, model)) <= MAX_PAYLOAD_BYTES:
            return [batch]
        midpoint = max(1, len(batch) // 2)
        return split(batch[:midpoint]) + split(batch[midpoint:])

    batches: list[list[dict[str, Any]]] = []
    for start in range(0, len(rows), batch_size):
        batches.extend(split(rows[start : start + batch_size]))
    return batches


def _read_response(response: Any) -> bytes:
    try:
        body = response.read()
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if isinstance(body, str):
        return body.encode("utf-8")
    if not isinstance(body, (bytes, bytearray)):
        raise _RequestFailure("invalid response body")
    return bytes(body)


def _send_request(payload: Mapping[str, Any], api_key: str, timeout: float, endpoint: str) -> Mapping[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise _RequestFailure("payload too large")
    request = urllib.request.Request(
        endpoint,
        data=encoded,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        raw = _read_response(response)
    except urllib.error.HTTPError as error:
        status = int(getattr(error, "code", 0) or 0)
        raise _RequestFailure("http error", status=status, retryable=status in {408, 425, 429, 500, 502, 503, 504, 529}) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise _RequestFailure("transport error", retryable=True) from None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _RequestFailure("invalid JSON response") from None
    if not isinstance(decoded, Mapping):
        raise _RequestFailure("invalid response shape")
    return decoded


class _RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.requests = 0
        self._lock = threading.Lock()

    def reserve(self) -> bool:
        with self._lock:
            if self.requests >= self.maximum:
                return False
            self.requests += 1
            return True


def _request_batch(
    payload: Mapping[str, Any],
    config: Mapping[str, Any],
    budget: _RequestBudget,
) -> tuple[Mapping[str, Any] | None, str | None]:
    last_failure: _RequestFailure | None = None
    for attempt in range(MAX_RETRY_ATTEMPTS):
        if not budget.reserve():
            return None, "Request budget exhausted before this batch could be sent."
        try:
            return _send_request(payload, config["api_key"], config["timeout"], config["endpoint"]), None
        except _RequestFailure as failure:
            last_failure = failure
            if not failure.retryable or attempt + 1 >= MAX_RETRY_ATTEMPTS:
                break
            delay = min(config["retry_backoff"] * (2**attempt), 2.0)
            if delay:
                time.sleep(delay)
    if last_failure is None:
        return None, "Request failed before a response was received."
    if last_failure.status is not None:
        return None, f"TypeSafe request failed with HTTP status {last_failure.status}."
    return None, "TypeSafe request failed due to a temporary transport or response error."


def classify_rows(
    rows: Iterable[Mapping[str, Any]],
    standard_description: str,
    config: Mapping[str, Any] | None,
    cache: Mapping[str, Any] | None = None,
    on_result: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Classify purchase rows with bounded, reviewable TypeSafe suggestions."""
    started = time.monotonic()
    settings = _normalise_config(config)
    standard, standard_truncated = _bounded_text(standard_description, MAX_STANDARD_CHARS)
    standard = standard or ""
    warnings: list[str] = []
    normalised_rows: list[dict[str, Any]] = []
    row_keys: list[str] = []
    row_truncated: list[bool] = []
    row_valid: list[bool] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            key = f"row_{index}"
            row_keys.append(key)
            normalised_rows.append({"id": key})
            row_truncated.append(False)
            row_valid.append(False)
            warnings.append(f"Row {index} is malformed and was marked Unknown.")
            continue
        key, normalised, warning, truncated = _normalise_row(row, index)
        row_keys.append(key)
        normalised_rows.append(normalised)
        row_truncated.append(truncated)
        row_valid.append(True)
        if warning:
            warnings.append(warning)
        if truncated:
            warnings.append(f"Row {key} context was truncated to the adapter size limit; review is required.")
    if standard_truncated:
        warnings.append("Standard description was truncated to the adapter size limit; review is required.")
    if len(set(row_keys)) != len(row_keys):
        raise ValueError("Duplicate row ids are not allowed in Jev classification input.")

    cache_out = dict(cache or {})
    decisions: dict[str, dict[str, Any]] = {}
    input_tokens_total = 0
    failed_rows = 0
    incomplete = False
    models_seen: set[str] = set()
    # One provider result can serve multiple rows with the same classification
    # context. Keep all row entries while scheduling one representative.
    pending_groups: dict[str, list[tuple[int, str, str]]] = {}
    for index, row in enumerate(normalised_rows):
        key = row_keys[index]
        if not row_valid[index]:
            continue
        context_truncated = row_truncated[index] or standard_truncated
        cache_key = _cache_key(
            row, standard, settings["model"], context_truncated=context_truncated
        )
        cached = _validate_decision(cache_out.get(cache_key))
        if cached is not None:
            decisions[key] = cached
            if on_result is not None:
                on_result(cache_key, dict(cached))
        else:
            if cache_key in cache_out:
                warnings.append(f"Ignored malformed cached decision for row {key}.")
                cache_out.pop(cache_key, None)
            pending_groups.setdefault(cache_key, []).append((index, key, cache_key))

    def assign(entries: list[tuple[int, str, str]], decision: dict[str, Any]) -> None:
        for _index, key, _cache_key_value in entries:
            decisions[key] = dict(decision)

    def fail(entries: list[tuple[int, str, str]], warning: str | None = None) -> None:
        nonlocal failed_rows, incomplete
        if warning:
            warnings.append(warning)
        assign(entries, _unknown_decision())
        failed_rows += len(entries)
        incomplete = True

    budget = _RequestBudget(settings["max_requests"])
    pending = [entries[0] for entries in pending_groups.values()]
    if pending and not settings["api_key"]:
        fail(
            [entry for entries in pending_groups.values() for entry in entries],
            "TypeSafe API key is missing; unresolved rows require review.",
        )
    elif pending and settings["max_requests"] == 0:
        fail(
            [entry for entries in pending_groups.values() for entry in entries],
            "TypeSafe request budget is zero; unresolved rows require review.",
        )
    elif pending:
        pending_rows = [normalised_rows[index] for index, _key, _cache_key in pending]
        batches = _split_batches(pending_rows, settings["batch_size"], standard, settings["model"])
        # Each batch retains the pending order.  A Future's result is consumed
        # below on this coordinating thread, where on_result is safe to call.
        batch_entries: list[list[tuple[int, str, str]]] = []
        cursor = 0
        for batch in batches:
            entries = pending[cursor : cursor + len(batch)]
            batch_entries.append(entries)
            cursor += len(batch)

        def run_batch(batch: list[dict[str, Any]]) -> tuple[Mapping[str, Any] | None, str | None]:
            payload = _question_payload(batch, standard, settings["model"])
            return _request_batch(payload, settings, budget)

        with ThreadPoolExecutor(max_workers=settings["concurrency"], thread_name_prefix="jev") as executor:
            future_map: dict[Future[tuple[Mapping[str, Any] | None, str | None]], list[tuple[int, str, str]]] = {
                executor.submit(run_batch, batch): entries for batch, entries in zip(batches, batch_entries)
            }
            for future in as_completed(future_map):
                entries = future_map[future]
                try:
                    response, error = future.result()
                except Exception:
                    response, error = None, "TypeSafe batch failed unexpectedly; unresolved rows require review."
                response_error = error
                actual_model = response.get("model") if isinstance(response, Mapping) else None
                if isinstance(actual_model, str) and actual_model.strip():
                    models_seen.add(actual_model)
                else:
                    warnings.append("TypeSafe response omitted its model identifier.")
                    response_error = "TypeSafe response failed validation; unresolved rows require review."
                answers = response.get("answers") if isinstance(response, Mapping) else None
                if isinstance(response, Mapping):
                    usage = response.get("usage")
                    if isinstance(usage, Mapping):
                        input_tokens = usage.get("input_tokens")
                        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0:
                            input_tokens_total += input_tokens
                        else:
                            warnings.append("TypeSafe response contained invalid usage data.")
                            response_error = "TypeSafe response failed validation; unresolved rows require review."
                        output_tokens = usage.get("output_tokens")
                        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 0:
                            warnings.append("TypeSafe response contained invalid usage data.")
                            response_error = "TypeSafe response failed validation; unresolved rows require review."
                    elif response is not None:
                        warnings.append("TypeSafe response omitted usage data.")
                        response_error = "TypeSafe response failed validation; unresolved rows require review."
                if not isinstance(answers, Mapping):
                    answers = {}
                    if response is not None:
                        warnings.append("TypeSafe response omitted answers; unresolved rows require review.")
                    response_error = "TypeSafe response failed validation; unresolved rows require review."
                if response_error:
                    fail(
                        [entry for representative in entries for entry in pending_groups[representative[2]]],
                        response_error if error is None else error,
                    )
                    continue
                for local_index, (global_index, _key, cache_key) in enumerate(entries):
                    grouped_entries = pending_groups[cache_key]
                    assembly_answer = answers.get(f"row_{local_index}_assembly")
                    purpose_answer = answers.get(f"row_{local_index}_purpose")
                    decision, malformed = _decision_from_answers(
                        assembly_answer,
                        purpose_answer,
                        settings["confidence_threshold"],
                        context_truncated=row_truncated[global_index] or standard_truncated,
                    )
                    if malformed:
                        fail(grouped_entries, f"Row {grouped_entries[0][1]}: {malformed}")
                        continue
                    assign(grouped_entries, decision)
                    cache_out[cache_key] = dict(decision)
                    if on_result is not None:
                        on_result(cache_key, dict(decision))

    # Every output row has a conservative decision, including malformed input.
    for index, key in enumerate(row_keys):
        if key not in decisions:
            assign([(index, key, "")], _unknown_decision())
            failed_rows += 1
            incomplete = True
    return {
        "decisions": decisions,
        "cache": cache_out,
        "metrics": {
            "requests": budget.requests,
            "input_tokens": input_tokens_total,
            "elapsed_seconds": max(0.0, time.monotonic() - started),
            "failed_rows": failed_rows,
            "models": sorted(models_seen),
        },
        "warnings": warnings,
        "incomplete": incomplete,
    }
