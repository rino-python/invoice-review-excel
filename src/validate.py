"""抜き出した項目を検算し、要確認にする。ここが「APIに投げただけ」との差。"""

from __future__ import annotations

import unicodedata

from src.extract import TAX_RATE, TAX_TOLERANCE_YEN, InvoiceDraft


def judgment_criteria_rows() -> list[tuple[str, str]]:
    rate_pct = int(round(TAX_RATE * 100))
    yen = int(TAX_TOLERANCE_YEN)
    return [
        ("転記可", "必須項目があり、税計算・日付・請求番号の重複に問題がなく、生成AIの補完もない"),
        ("要確認（必須欠落）", "請求元・発行日・支払期限・税込金額・請求番号のいずれかが空"),
        ("要確認（税計算）", f"税抜＋消費税と税込が {yen} 円以上ずれる。消費税が空なら税抜×{rate_pct}% で検算"),
        ("要確認（日付）", "支払期限が発行日より前"),
        ("要確認（重複番号）", "同じ請求番号が別ファイルにもある。理由に相手の請求元とファイル名を書く"),
        ("要確認（生成AI補完）", "生成AIが抜けた項目を埋めた。転記の前に原文と照合する"),
    ]


def check_tax(draft: InvoiceDraft) -> str | None:
    if draft.税込金額 is None:
        return None
    if draft.税抜金額 is not None and draft.消費税 is not None:
        expected = draft.税抜金額 + draft.消費税
        if abs(expected - draft.税込金額) >= TAX_TOLERANCE_YEN:
            return "税抜＋消費税と税込が合いません"
        return None
    if draft.税抜金額 is not None and draft.消費税 is None:
        expected = round(draft.税抜金額 * (1 + TAX_RATE))
        if abs(expected - draft.税込金額) >= TAX_TOLERANCE_YEN:
            return f"税込が税抜の{int(TAX_RATE * 100)}%込みと合いません"
    return None


def validate_one(draft: InvoiceDraft) -> list[str]:
    reasons: list[str] = []
    if not draft.請求元:
        reasons.append("請求元が空です")
    if draft.発行日 is None:
        reasons.append("発行日が読めません")
    if draft.税込金額 is None:
        reasons.append("税込金額が読めません")
    if not draft.請求番号:
        reasons.append("請求番号が空です")
    if not draft.支払期限:
        reasons.append("支払期限が空です")
    if (
        draft.発行日 is not None
        and draft.支払期限 is not None
        and draft.支払期限 < draft.発行日
    ):
        reasons.append("支払期限が発行日より前です")
    tax_reason = check_tax(draft)
    if tax_reason:
        reasons.append(tax_reason)
    return reasons


def _duplicate_label(draft: InvoiceDraft) -> str:
    if draft.請求元:
        return f"{draft.請求元}（{draft.ファイル名}）"
    return draft.ファイル名


def _number_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def apply_duplicate_numbers(drafts: list[InvoiceDraft]) -> None:
    by_number: dict[str, list[InvoiceDraft]] = {}
    for draft in drafts:
        if draft.請求番号:
            by_number.setdefault(_number_key(draft.請求番号), []).append(draft)
    for group in by_number.values():
        if len(group) < 2:
            continue
        for draft in group:
            others = "、".join(
                _duplicate_label(item) for item in group if item is not draft
            )
            note = f"請求番号 {draft.請求番号} が{others}と重複しています"
            if note not in draft.reasons:
                draft.reasons.append(note)


def validate_all(drafts: list[InvoiceDraft]) -> None:
    for draft in drafts:
        extra = validate_one(draft)
        for reason in extra:
            if reason not in draft.reasons:
                draft.reasons.append(reason)
    apply_duplicate_numbers(drafts)
