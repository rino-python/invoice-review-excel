"""請求書ファイルを読み、抜き出し・任意のAI補完・検算まで通す。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.extract import InputFormatError, InvoiceDraft, extract_invoice, read_source
from src.llm import MODEL, Completer, complete_gaps, needs_llm
from src.validate import validate_all

CLIENT_NAME = "合同会社 灯台デザイン"
MAX_FILES = 20
DISPLAY_COLUMNS = [
    "判定",
    "ファイル名",
    "原文抜粋",
    "請求番号",
    "請求元",
    "発行日",
    "支払期限",
    "税抜金額",
    "消費税",
    "税込金額",
    "項目の取得方法",
    "理由",
]
EXTRACT_METHOD_LABELS = {
    "規則": "プログラムだけ",
    "規則+生成AI": "プログラム＋生成AIで補完",
}


@dataclass
class ReviewResult:
    rows: pd.DataFrame
    issues: pd.DataFrame
    ok_count: int
    issue_count: int
    file_count: int
    ok_gross: float
    llm_called: bool
    used_llm: bool
    source_note: str


def order_columns(df: pd.DataFrame) -> pd.DataFrame:
    view = df.copy()
    if "抜き出し" in view.columns and "項目の取得方法" not in view.columns:
        view["項目の取得方法"] = view["抜き出し"].map(_method_label)
        view = view.drop(columns=["抜き出し"])
    elif "項目の取得方法" in view.columns:
        view["項目の取得方法"] = view["項目の取得方法"].map(_method_label)
    ordered = [name for name in DISPLAY_COLUMNS if name in view.columns]
    extras = [name for name in view.columns if name not in ordered]
    return view.loc[:, ordered + extras]


def _method_label(value: object) -> str:
    text = "" if value is None else str(value)
    return EXTRACT_METHOD_LABELS.get(text, text)


def _date_cell(value: date | None) -> str:
    return value.isoformat() if value else ""


def drafts_to_frames(drafts: list[InvoiceDraft]) -> tuple[pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, object]] = []
    for draft in drafts:
        judgment = "要確認" if draft.reasons else "転記可"
        records.append(
            {
                "判定": judgment,
                "ファイル名": draft.ファイル名,
                "原文抜粋": draft.原文抜粋,
                "請求番号": draft.請求番号,
                "請求元": draft.請求元,
                "発行日": _date_cell(draft.発行日),
                "支払期限": _date_cell(draft.支払期限),
                "税抜金額": draft.税抜金額,
                "消費税": draft.消費税,
                "税込金額": draft.税込金額,
                "項目の取得方法": _method_label(draft.抜き出し),
                "理由": " / ".join(draft.reasons),
            }
        )
    columns = DISPLAY_COLUMNS
    rows = pd.DataFrame(records, columns=columns)
    issues = rows[rows["判定"] == "要確認"].copy().reset_index(drop=True)
    return rows, issues


@dataclass(frozen=True)
class ReviewPlan:
    file_count: int
    llm_needed: int
    api_calls: int
    model: str
    has_api_key: bool


def _assert_batch_size(files: list[tuple[str, bytes]]) -> None:
    if not files:
        raise InputFormatError("請求書ファイルを1つ以上上げてください。")
    if len(files) > MAX_FILES:
        raise InputFormatError(f"一度に処理できるのは {MAX_FILES} 件までです。")


def input_fingerprint(files: list[tuple[str, bytes]], has_api_key: bool) -> str:
    digest = hashlib.sha256()
    digest.update(b"col-excerpt-after-name")
    digest.update(b"1" if has_api_key else b"0")
    for name, raw in files:
        digest.update(name.encode("utf-8", errors="replace"))
        digest.update(hashlib.sha256(raw).digest())
    return digest.hexdigest()


def load_drafts(files: list[tuple[str, bytes]]) -> tuple[list[InvoiceDraft], list[str]]:
    _assert_batch_size(files)
    drafts: list[InvoiceDraft] = []
    texts: list[str] = []
    for filename, raw in files:
        text = read_source(filename, raw)
        texts.append(text)
        drafts.append(extract_invoice(filename, text))
    return drafts, texts


def plan_review(files: list[tuple[str, bytes]], api_key: str = "") -> ReviewPlan:
    drafts, _texts = load_drafts(files)
    llm_needed = sum(1 for draft in drafts if needs_llm(draft))
    has_api_key = bool(api_key)
    return ReviewPlan(
        file_count=len(files),
        llm_needed=llm_needed,
        api_calls=llm_needed if has_api_key else 0,
        model=MODEL,
        has_api_key=has_api_key,
    )


def _source_note(llm_called: bool, used_llm: bool) -> str:
    if used_llm:
        return (
            "抜けた項目を生成AIで補完したうえで、税計算と必須項目を検算しています。"
            "補完した行は要確認です。"
        )
    if llm_called:
        return "生成AIを呼びましたが、埋められた項目はありません。検算は規則の結果です。"
    return "項目はプログラムの規則で取得。生成AIは鍵があるときだけ、抜けた項目の補完に使います。"


def review_files(
    files: list[tuple[str, bytes]],
    api_key: str = "",
    completer: Completer | None = None,
) -> ReviewResult:
    drafts, texts = load_drafts(files)

    llm_called = False
    used_llm = False
    if api_key:
        for draft, text in zip(drafts, texts, strict=True):
            if complete_gaps(draft, text, api_key=api_key, completer=completer):
                llm_called = True
            if draft.抜き出し == "規則+生成AI":
                used_llm = True

    validate_all(drafts)
    rows, issues = drafts_to_frames(drafts)
    ok = rows[rows["判定"] == "転記可"]
    ok_gross = float(ok["税込金額"].fillna(0).sum()) if not ok.empty else 0.0

    return ReviewResult(
        rows=rows,
        issues=issues,
        ok_count=int((rows["判定"] == "転記可").sum()),
        issue_count=int((rows["判定"] == "要確認").sum()),
        file_count=len(files),
        ok_gross=ok_gross,
        llm_called=llm_called,
        used_llm=used_llm,
        source_note=_source_note(llm_called, used_llm),
    )
