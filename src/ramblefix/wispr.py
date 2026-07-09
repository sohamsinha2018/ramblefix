from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ramblefix.corpus import DEFAULT_CORPUS_PATH, set_corpus_benchmark


DEFAULT_WISPR_DB = Path.home() / "Library" / "Application Support" / "Wispr Flow" / "flow.sqlite"

TEXT_FIELDS = {
    "formatted": "formattedText",
    "asr": "asrText",
    "edited": "editedText",
    "pasted": "pastedText",
    "default_asr": "defaultAsrText",
    "fallback_asr": "fallbackAsrText",
    "default_formatted": "defaultFormattedText",
    "fallback_formatted": "fallbackFormattedText",
}


@dataclass(frozen=True)
class WisprHistoryRow:
    transcript_id: str
    timestamp: str | None
    status: str | None
    duration: float | None
    num_words: int | None
    app: str | None
    detected_language: str | None
    e2e_latency: float | None
    text: str


def list_wispr_rows(
    *,
    db_path: str | Path = DEFAULT_WISPR_DB,
    limit: int = 10,
    field: str = "formatted",
) -> list[WisprHistoryRow]:
    column = _field_column(field)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            select
                transcriptEntityId,
                timestamp,
                status,
                duration,
                numWords,
                app,
                detectedLanguage,
                e2eLatency,
                coalesce({column}, '')
            from History
            where coalesce({column}, '') != ''
            order by timestamp desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [_row_from_sql(row) for row in rows]


def get_wispr_row(
    *,
    transcript_id: str | None = None,
    db_path: str | Path = DEFAULT_WISPR_DB,
    field: str = "formatted",
) -> WisprHistoryRow:
    column = _field_column(field)
    where = "transcriptEntityId = ?" if transcript_id else "coalesce({column}, '') != ''"
    where = where.format(column=column)
    params: tuple[object, ...] = (transcript_id,) if transcript_id else ()
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            select
                transcriptEntityId,
                timestamp,
                status,
                duration,
                numWords,
                app,
                detectedLanguage,
                e2eLatency,
                coalesce({column}, '')
            from History
            where {where}
            order by timestamp desc
            limit 1
            """,
            params,
        ).fetchone()
    if row is None:
        label = transcript_id or "latest non-empty row"
        raise KeyError(f"Wispr history row not found: {label}")
    return _row_from_sql(row)


def import_wispr_benchmark(
    *,
    item_id: str,
    transcript_id: str | None = None,
    benchmark_name: str = "wispr",
    field: str = "formatted",
    db_path: str | Path = DEFAULT_WISPR_DB,
    corpus_path: str | Path = DEFAULT_CORPUS_PATH,
) -> tuple[dict[str, object], WisprHistoryRow]:
    row = get_wispr_row(transcript_id=transcript_id, db_path=db_path, field=field)
    if not row.text.strip():
        raise ValueError(f"Wispr row has no text in field {field}: {row.transcript_id}")
    item = set_corpus_benchmark(item_id, benchmark_name, row.text, corpus_path)
    return item, row


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _field_column(field: str) -> str:
    try:
        return TEXT_FIELDS[field]
    except KeyError as exc:
        valid = ", ".join(sorted(TEXT_FIELDS))
        raise ValueError(f"Unknown Wispr text field {field!r}. Valid: {valid}") from exc


def _row_from_sql(row: sqlite3.Row) -> WisprHistoryRow:
    return WisprHistoryRow(
        transcript_id=str(row[0]),
        timestamp=row[1],
        status=row[2],
        duration=float(row[3]) if row[3] is not None else None,
        num_words=int(row[4]) if row[4] is not None else None,
        app=row[5],
        detected_language=row[6],
        e2e_latency=float(row[7]) if row[7] is not None else None,
        text=str(row[8] or ""),
    )
