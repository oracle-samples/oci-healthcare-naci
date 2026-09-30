"""Boundary and integration checks with synthetic data only."""

import importlib.util
from unittest.mock import Mock

from fastapi.testclient import TestClient
from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine
import pytest

from guardrail.app import (
    MAX_BATCH_CHARS, MAX_BATCH_ITEMS, Redactor, build_redactor, create_app, merged_results,
)
from guardrail.recognizers import healthcare_recognizers


@pytest.mark.parametrize(
    ("text", "entity", "value"),
    [
        ("MRN: AB-123456", "MRN", "AB-123456"),
        ("medical record number = 902100", "MRN", "902100"),
        ("patient ID: PT_8891", "PATIENT_ID", "PT_8891"),
        ("Account number # AC-556677", "ACCOUNT_ID", "AC-556677"),
        ("DOB: 1978-02-11", "DATE_OF_BIRTH", "1978-02-11"),
        ("date of birth is 02/11/1978", "DATE_OF_BIRTH", "02/11/1978"),
        ("Birth date: February 11, 1978", "DATE_OF_BIRTH", "February 11, 1978"),
        ("DOB: 11 February 1978", "DATE_OF_BIRTH", "11 February 1978"),
    ],
)
def test_labeled_identifiers(text, entity, value):
    recognizer = next(r for r in healthcare_recognizers() if r.entity_type == entity)
    results = recognizer.analyze(text, [entity])
    assert len(results) == 1
    assert text[results[0].start:results[0].end] == value


@pytest.mark.parametrize("text", ["The patient is improving.", "MRN:\nReview the chart.", "AB-123456", "xMRN: 123456"])
def test_custom_recognizers_require_a_label_and_same_line_value(text):
    for recognizer in healthcare_recognizers():
        assert recognizer.analyze(text, recognizer.supported_entities) == []


def test_overlap_redacts_entire_union_with_stable_label():
    findings = [
        RecognizerResult("PERSON", 0, 5, 0.8),
        RecognizerResult("LOCATION", 3, 12, 0.8),
        RecognizerResult("MRN", 8, 10, 0.99),
    ]
    first = merged_results(findings, 12)
    second = merged_results(list(reversed(findings)), 12)
    assert [(r.start, r.end, r.entity_type) for r in first] == [(0, 12, "MRN")]
    assert first == second
    redactor = Redactor(Mock(analyze=Mock(return_value=findings)), AnonymizerEngine())
    result = redactor.redact(["ABCDEFGHIJKL"])
    assert result.texts == ["<MRN>"]
    assert result.entity_counts == {"MRN": 1}


def test_invalid_offsets_fail_closed():
    with pytest.raises(ValueError, match="Invalid analyzer result"):
        merged_results([RecognizerResult("PERSON", 0, 100, 0.8)], 10)


@pytest.fixture
def client():
    redactor = Redactor(Mock(analyze=Mock(return_value=[])), AnonymizerEngine())
    with TestClient(create_app(redactor)) as test_client:
        yield test_client


def test_health_and_size_boundaries(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/redact", json={"texts": ["a" * MAX_BATCH_CHARS]}).status_code == 200
    assert client.post("/redact", json={"texts": [""] * MAX_BATCH_ITEMS}).status_code == 200
    assert client.post("/redact", json={"texts": ["a" * MAX_BATCH_CHARS, "b"]}).status_code == 422
    assert client.post("/redact", json={"texts": [""] * (MAX_BATCH_ITEMS + 1)}).status_code == 422


def test_litellm_presidio_http_contract(client):
    analyzed = client.post("/analyze", json={"text": "synthetic", "language": "en", "entities": ["PERSON"]})
    assert analyzed.status_code == 200
    assert analyzed.json() == []

    anonymized = client.post("/anonymize", json={
        "text": "Jane synthetic",
        "analyzer_results": [{"entity_type": "PERSON", "start": 0, "end": 4, "score": 0.9}],
    })
    assert anonymized.status_code == 200
    assert anonymized.json()["text"] == "<PERSON> synthetic"
    assert anonymized.json()["items"][0]["entity_type"] == "PERSON"


@pytest.mark.parametrize("path,payload", [
    ("/analyze", {"text": "synthetic", "language": "fr"}),
    ("/analyze", {"text": "synthetic", "entities": ["UNSUPPORTED"]}),
    ("/anonymize", {"text": "synthetic", "analyzer_results": [{"entity_type": "PERSON", "start": 0, "end": 99, "score": 1.0}]}),
])
def test_litellm_presidio_contract_rejects_invalid_requests_without_echo(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code in (422, 503)
    assert "synthetic" not in response.text


@pytest.mark.parametrize("payload", [
    {"texts": []}, {"texts": "secret"}, {"texts": [123]}, {"texts": [None]},
    {"texts": [{"sensitive": "secret"}]}, {"texts": ["secret"], "extra": "secret"},
])
def test_invalid_requests_never_echo_input(client, payload):
    response = client.post("/redact", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid redaction request"}


def test_invalid_json_does_not_echo_input(client):
    response = client.post("/redact", content='{"texts": ["secret",}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "secret" not in response.text


def test_engine_failure_never_returns_original_text_or_exception(caplog):
    analyzer = Mock(analyze=Mock(side_effect=RuntimeError("synthetic secret")))
    with TestClient(create_app(Redactor(analyzer, AnonymizerEngine()))) as client:
        response = client.post("/redact", json={"texts": ["synthetic secret"]})
    assert response.status_code == 503
    assert response.json() == {"detail": "Guardrail unavailable"}
    assert "synthetic secret" not in caplog.text


@pytest.mark.skipif(importlib.util.find_spec("en_core_web_sm") is None, reason="Bundled spaCy model not installed")
def test_full_presidio_pipeline_and_http_contract(caplog):
    text = (
        "John Smith, MRN: AB-123456, patient ID: PT-8891, Account number: AC-556677. "
        "DOB: February 11, 1978. Email john.smith@example.com. Phone 212-555-0199."
    )
    with TestClient(create_app(build_redactor())) as client:
        first = client.post("/redact", json={"texts": [text, "", "A generic care question."]})
        second = client.post("/redact", json={"texts": [text, "", "A generic care question."]})
    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert set(payload) == {"texts", "entity_counts"}
    assert len(payload["texts"]) == 3
    assert payload["texts"][1] == ""
    for secret in ["John Smith", "AB-123456", "PT-8891", "AC-556677", "1978", "john.smith@example.com", "212-555-0199"]:
        assert secret not in first.text
        assert secret not in caplog.text
    for entity in ["PERSON", "MRN", "PATIENT_ID", "ACCOUNT_ID", "DATE_OF_BIRTH", "EMAIL_ADDRESS", "PHONE_NUMBER"]:
        assert f"<{entity}>" in payload["texts"][0]
        assert payload["entity_counts"][entity] >= 1
