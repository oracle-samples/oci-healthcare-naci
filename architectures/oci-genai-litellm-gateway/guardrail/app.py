"""English text redaction service. Accessible only on the internal Docker network.

No request text, detected values, or reversible replacement mapping is logged
or persisted. Automated redaction is not certified de-identification.
"""

from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
import logging
from threading import Lock
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
import spacy

from guardrail.recognizers import healthcare_recognizers

MAX_BATCH_CHARS = 50_000
MAX_BATCH_ITEMS = 100
ENTITIES = (
    "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "DATE_TIME", "LOCATION",
    "US_DRIVER_LICENSE", "US_PASSPORT", "US_BANK_NUMBER", "CREDIT_CARD",
    "IP_ADDRESS", "MEDICAL_LICENSE", "URL", "MRN", "PATIENT_ID", "ACCOUNT_ID",
    "DATE_OF_BIRTH",
)

# Some libraries emit analyzer diagnostics at DEBUG. Disable those loggers in
# this service even if a caller changes the root logging level.
for logger_name in ("presidio-analyzer", "presidio-anonymizer"):
    logging.getLogger(logger_name).disabled = True


class RedactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texts: Annotated[list[StrictStr], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]

    @field_validator("texts")
    @classmethod
    def validate_total_size(cls, texts: list[str]) -> list[str]:
        if sum(map(len, texts)) > MAX_BATCH_CHARS:
            raise ValueError("Text batch exceeds the character limit")
        return texts


class RedactResponse(BaseModel):
    texts: list[str]
    entity_counts: dict[str, int]


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=MAX_BATCH_CHARS)
    language: Literal["en"] = "en"
    entities: list[str] | None = Field(default=None, max_length=len(ENTITIES))

    @field_validator("entities")
    @classmethod
    def validate_entities(cls, entities: list[str] | None) -> list[str] | None:
        if entities is not None and (not entities or any(entity not in ENTITIES for entity in entities)):
            raise ValueError("Unsupported entity list")
        return entities


class AnalyzeResult(BaseModel):
    entity_type: str
    start: int
    end: int
    score: float


class AnonymizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=MAX_BATCH_CHARS)
    analyzer_results: list[AnalyzeResult] = Field(max_length=MAX_BATCH_ITEMS)


class AnonymizeItem(BaseModel):
    start: int
    end: int
    entity_type: str
    text: str
    operator: str


class AnonymizeResponse(BaseModel):
    text: str
    items: list[AnonymizeItem]


def merged_results(results: list[RecognizerResult], text_length: int) -> list[RecognizerResult]:
    """Resolve overlapping spans conservatively and deterministically.

    Redact the entire union of each overlapping group. Use its highest-score
    entity (then longest span and name for ties) for the replacement label.
    Invalid analyzer offsets cause the request to fail, never pass through.
    """
    for result in results:
        if not 0 <= result.start < result.end <= text_length:
            raise ValueError("Invalid analyzer result")
        if result.entity_type not in ENTITIES:
            raise ValueError("Unexpected analyzer entity")

    groups: list[list[RecognizerResult]] = []
    group_end = -1
    for result in sorted(results, key=lambda r: (r.start, r.end, r.entity_type)):
        if not groups or result.start >= group_end:
            groups.append([result])
            group_end = result.end
        else:
            groups[-1].append(result)
            group_end = max(group_end, result.end)
    merged = []
    for group in groups:
        label = min(group, key=lambda r: (-r.score, -(r.end - r.start), r.entity_type))
        merged.append(RecognizerResult(
            entity_type=label.entity_type,
            start=min(r.start for r in group),
            end=max(r.end for r in group),
            score=label.score,
        ))
    return merged


class Redactor:
    def __init__(self, analyzer: AnalyzerEngine, anonymizer: AnonymizerEngine) -> None:
        self.analyzer = analyzer
        self.anonymizer = anonymizer
        # Bound CPU/memory concurrency on the inexpensive single-VM deployment.
        self.lock = Lock()
        self.operators = {
            entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
            for entity in ENTITIES
        }

    def _analyze(self, text: str, entities: list[str] | None = None) -> list[RecognizerResult]:
        findings = self.analyzer.analyze(
            text=text,
            language="en",
            entities=entities or list(ENTITIES),
            score_threshold=0.4,
            return_decision_process=False,
        )
        return merged_results(findings, len(text))

    def analyze(self, text: str, entities: list[str] | None = None) -> list[AnalyzeResult]:
        with self.lock:
            spans = self._analyze(text, entities)
        return [
            AnalyzeResult(
                entity_type=span.entity_type,
                start=span.start,
                end=span.end,
                score=span.score,
            )
            for span in spans
        ]

    def _anonymize(self, text: str, spans: list[RecognizerResult]) -> AnonymizeResponse:
        result = self.anonymizer.anonymize(
            text=text, analyzer_results=spans, operators=self.operators
        )
        return AnonymizeResponse(
            text=result.text,
            items=[
                AnonymizeItem(
                    start=item.start,
                    end=item.end,
                    entity_type=item.entity_type,
                    text=item.text,
                    operator=item.operator,
                )
                for item in result.items
            ],
        )

    def anonymize(self, text: str, results: list[AnalyzeResult]) -> AnonymizeResponse:
        findings = [
            RecognizerResult(
                entity_type=result.entity_type,
                start=result.start,
                end=result.end,
                score=result.score,
            )
            for result in results
        ]
        with self.lock:
            spans = merged_results(findings, len(text))
            return self._anonymize(text, spans)

    def redact(self, texts: list[str]) -> RedactResponse:
        redacted = []
        counts: Counter[str] = Counter()
        with self.lock:
            for text in texts:
                if not text:
                    redacted.append("")
                    continue
                result = self._anonymize(text, self._analyze(text))
                redacted.append(result.text)
                counts.update(item.entity_type for item in result.items)
        return RedactResponse(texts=redacted, entity_counts=dict(sorted(counts.items())))


def build_redactor() -> Redactor:
    # Load explicitly before initializing Presidio: fail startup if the bundled
    # model is absent, instead of Presidio downloading a model at runtime.
    model = spacy.load("en_core_web_sm")
    model.max_length = MAX_BATCH_CHARS + 1
    nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
    nlp_engine.nlp = {"en": model}
    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
    for recognizer in healthcare_recognizers():
        analyzer.registry.add_recognizer(recognizer)
    return Redactor(analyzer, AnonymizerEngine())


def create_app(redactor: Redactor | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.redactor = redactor if redactor is not None else build_redactor()
        yield

    app = FastAPI(
        title="Internal Presidio guardrail", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # FastAPI's default validation response may include the original input.
        return JSONResponse(status_code=422, content={"detail": "Invalid redaction request"})

    @app.get("/health")
    def health(request: Request):
        if not getattr(request.app.state, "redactor", None):
            raise HTTPException(status_code=503, detail="Guardrail unavailable")
        return {"status": "ok"}

    @app.post("/redact", response_model=RedactResponse)
    def redact_texts(payload: RedactRequest, request: Request):
        try:
            return request.app.state.redactor.redact(payload.texts)
        except Exception:
            # No exception details: a recognizer could include matched text.
            raise HTTPException(status_code=503, detail="Guardrail unavailable") from None

    # These two endpoints implement the Presidio HTTP contract used by
    # LiteLLM Gateway's built-in Presidio guardrail.
    @app.post("/analyze", response_model=list[AnalyzeResult])
    def analyze_text(payload: AnalyzeRequest, request: Request):
        try:
            return request.app.state.redactor.analyze(payload.text, payload.entities)
        except Exception:
            raise HTTPException(status_code=503, detail="Guardrail unavailable") from None

    @app.post("/anonymize", response_model=AnonymizeResponse)
    def anonymize_text(payload: AnonymizeRequest, request: Request):
        try:
            return request.app.state.redactor.anonymize(payload.text, payload.analyzer_results)
        except Exception:
            raise HTTPException(status_code=503, detail="Guardrail unavailable") from None

    return app


app = create_app()
