"""The LLM-facing description of the caseload.

This is the bounded-context boundary between the Caseload domain and the
Conversation/Agent domain. The Caseload context owns what its data is and
how it describes itself for an LLM; the Conversation context only consumes
``render_for_llm()`` to embed it inside the system prompt. The dict-shaped
profile the prompt builder used to reach into is gone -- if a case field is
added, only this module changes; ``agent/prompts.py`` is untouched.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from data.case import EXAMPLE_CLIENT_NAME, Case, CaseField


@dataclass(frozen=True)
class DateRange:
    """Inclusive min/max bounds of a date column, as ISO strings."""

    min: str
    max: str


@dataclass(frozen=True)
class DataProfile:
    """Schema + aggregate shape of the caseload that the LLM may see.

    Raw rows never appear here -- categorical values, date ranges, null
    counts, and one synthetic example row. ``reference_today`` is the date
    the LLM should treat as "now" so relative dates ("three months ago")
    resolve deterministically against a known anchor.

    The schema is the same ``CaseField`` list the storage layer uses; the
    renderer simply ignores ``.sql``. Keeping one schema type avoids the
    drift a parallel "LLM-facing" projection would invite.
    """

    reference_today: str
    row_count: int
    schema: list[CaseField]
    categorical_values: dict[str, list[str]]   # genuine map: field_name -> values
    date_ranges: dict[str, DateRange | None]   # genuine map: field_name -> range
    null_counts: dict[str, int]                # genuine map: field_name -> count
    example_row: Case                          # synthetic, no real PII

    def __post_init__(self) -> None:
        # The example row is rendered verbatim into the LLM system prompt.
        # Refuse to build a profile whose example carries real client data --
        # that would defeat the air-gap (ADR-001 §3). The synthetic marker is
        # the only client_name allowed to reach the prompt.
        if self.example_row.client_name != EXAMPLE_CLIENT_NAME:
            raise ValueError(
                "example_row must be synthetic: client_name must be "
                f"{EXAMPLE_CLIENT_NAME!r}, got "
                f"{self.example_row.client_name!r}. Raw client data must "
                "never be rendered into the system prompt (ADR-001 §3)."
            )

    def render_for_llm(self) -> str:
        """Produce the data section of the system prompt as a markdown fragment.

        The conversation context (``agent/prompts.py``) embeds this verbatim
        and surrounds it with policy / contract / rules sections. It does
        not reach into the dataclass fields -- the rendering belongs here.
        """
        schema_lines = "\n".join(
            f"  - {field.name} ({field.type}"
            + (", nullable" if field.nullable else "")
            + f"): {field.description}"
            for field in self.schema
        )
        enums = "\n".join(
            f"  - {field}: {', '.join(values)}"
            for field, values in self.categorical_values.items()
        )
        ranges = "\n".join(
            f"  - {field}: {bounds.min} to {bounds.max}"
            for field, bounds in self.date_ranges.items() if bounds
        )
        nulls = ", ".join(
            f"{field} ({count} null)"
            for field, count in self.null_counts.items()
        )

        return (
            f"Today's date is {self.reference_today}. Resolve all relative "
            f"dates (\"last quarter\", \"three months ago\") against this "
            f"date.\n\n"
            f"A variable `cases` (a list of {self.row_count} dicts) is "
            f"preloaded in the sandbox. Each case has these fields:\n"
            f"{schema_lines}\n\n"
            f"Categorical values present in the data:\n{enums}\n\n"
            f"Date ranges in the data:\n{ranges}\n\n"
            f"Nullable fields with null counts: {nulls}\n\n"
            f"One example row (synthetic -- shows shape only):\n"
            f"{json.dumps(asdict(self.example_row), indent=2)}"
        )
