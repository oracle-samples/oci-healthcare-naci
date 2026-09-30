"""Small, deliberately generic additions to Presidio's built-in recognizers.

These examples recognize *labeled* identifiers. Adapt them to actual customer
formats and evaluate recall on representative, appropriately protected data.
They are not a complete healthcare de-identification specification.
"""

from __future__ import annotations

import re

from presidio_analyzer import EntityRecognizer, RecognizerResult


class LabeledValueRecognizer(EntityRecognizer):
    """Redact the value after a label, keeping the label for useful context."""

    def __init__(self, entity_type: str, label: str, value: str) -> None:
        super().__init__(
            supported_entities=[entity_type],
            supported_language="en",
            name=f"Labeled{entity_type}Recognizer",
        )
        self.entity_type = entity_type
        # Spaces, not arbitrary whitespace: do not borrow a value from the next
        # line when a labeled field is empty. Limit field width and use word
        # boundaries to avoid redacting prefixes of larger identifiers.
        self.pattern = re.compile(
            rf"\b(?:{label})\b[ \t]*(?:(?:[:#=\-]|is\b)[ \t]*)?"
            rf"(?P<value>{value})(?![A-Za-z0-9_\-])",
            re.IGNORECASE,
        )

    def load(self) -> None:
        """No external model or resource is required."""

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None):
        if self.entity_type not in entities:
            return []
        return [
            RecognizerResult(
                entity_type=self.entity_type,
                start=match.start("value"),
                end=match.end("value"),
                score=0.99,
                recognition_metadata={
                    RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                    RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                },
            )
            for match in self.pattern.finditer(text)
        ]


def healthcare_recognizers() -> list[LabeledValueRecognizer]:
    """Return new recognizers, so registries do not share mutable instances."""
    identifier = r"[A-Za-z0-9][A-Za-z0-9_\-]{2,63}"
    month = (
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    )
    date = (
        r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
        rf"{month}[ \t]+\d{{1,2}}(?:st|nd|rd|th)?[,]?[ \t]+\d{{4}}|"
        rf"\d{{1,2}}[ \t]+{month}[ \t]+\d{{4}})"
    )
    return [
        LabeledValueRecognizer(
            "MRN", r"MRN|medical[ \t]+record(?:[ \t]+(?:number|no\.?|id))?", identifier
        ),
        LabeledValueRecognizer(
            "PATIENT_ID", r"patient[ \t]+(?:id|identifier|number|no\.?)", identifier
        ),
        LabeledValueRecognizer(
            "ACCOUNT_ID", r"(?:patient[ \t]+)?account[ \t]+(?:id|number|no\.?)", identifier
        ),
        LabeledValueRecognizer(
            "DATE_OF_BIRTH", r"DOB|date[ \t]+of[ \t]+birth|birth[ \t]+date", date
        ),
    ]
