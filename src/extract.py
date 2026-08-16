"""請求書テキストから項目を規則で抜く。生成AIには頼らない。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

TAX_TOLERANCE_YEN = 1.0
TAX_RATE = 0.10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 30


class InputFormatError(ValueError):
    """ファイル形式など、ユーザーが直せる入力エラー。"""


@dataclass
class InvoiceDraft:
    ファイル名: str
    請求番号: str = ""
    請求元: str = ""
    発行日: date | None = None
    支払期限: date | None = None
    税抜金額: float | None = None
    消費税: float | None = None
    税込金額: float | None = None
    原文抜粋: str = ""
    抜き出し: str = "規則"
    reasons: list[str] = field(default_factory=list)

    def filled_fields(self) -> int:
        count = 0
        if self.請求番号:
            count += 1
        if self.請求元:
            count += 1
        if self.発行日 is not None:
            count += 1
        if self.支払期限 is not None:
            count += 1
        if self.税抜金額 is not None:
            count += 1
        if self.消費税 is not None:
            count += 1
        if self.税込金額 is not None:
            count += 1
        return count


def decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InputFormatError("文字コードを読み取れませんでした。UTF-8 か Shift-JIS で保存してください。")


def read_source(filename: str, raw: bytes) -> str:
    if len(raw) > MAX_FILE_BYTES:
        raise InputFormatError(f"{filename} が大きすぎます。10MB以下にしてください。")
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return decode_bytes(raw)
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise InputFormatError(
                f"{filename} のページ数が多すぎます。{MAX_PDF_PAGES} ページ以下にしてください。"
            )
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(parts).strip()
        if not text:
            raise InputFormatError(
                f"{filename} から文字を取れませんでした。スキャン画像の OCR はこのデモの範囲外です。"
            )
        return text
    raise InputFormatError("対応形式は .txt / .md / .pdf です。")


def parse_yen(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = (
        value.replace(",", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("円", "")
        .replace("税込", "")
        .replace("税抜", "")
        .strip()
    )
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace(".", "-").replace("/", "-")
    text = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _first_group(pattern: str, text: str, flags: int = 0) -> str:
    match = re.search(pattern, text, flags)
    if not match:
        return ""
    return match.group(1).strip()


def _line_after_label(label: str, text: str) -> str:
    return _first_group(rf"{label}[：:\s]+(.+)", text)


_EXCERPT_LABEL = re.compile(
    r"(?<=\s)(?=(?:請求番号|請求書番号|発行日|支払期限|お支払期限|請求元|請求先|"
    r"登録番号|品目|小計|消費税|合計|ご請求金額))"
)
_EXCERPT_SENTENCE = re.compile(r"(?<=[。！？])")
EXCERPT_MAX_LINES = 8


def format_excerpt(text: str, max_lines: int = EXCERPT_MAX_LINES) -> str:
    chunks: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        labeled = [part.strip() for part in _EXCERPT_LABEL.split(line) if part.strip()]
        for part in labeled:
            if len(part) > 20 and _EXCERPT_SENTENCE.search(part):
                sentences = [piece.strip() for piece in _EXCERPT_SENTENCE.split(part) if piece.strip()]
                chunks.extend(sentences)
            else:
                chunks.append(part)
        if len(chunks) >= max_lines:
            break
    return "\n".join(chunks[:max_lines])


def extract_invoice(filename: str, text: str) -> InvoiceDraft:
    draft = InvoiceDraft(ファイル名=filename)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    draft.原文抜粋 = format_excerpt(text)

    draft.請求番号 = _line_after_label(r"(?:請求番号|請求書番号|Invoice\s*No\.?)", text)
    draft.請求元 = _line_after_label(r"(?:請求元|請求者|発行者|事業者名)", text)
    if not draft.請求元:
        for line in lines:
            if re.search(r"(株式会社|有限会社|合同会社|事務所|スタジオ)", line) and "御中" not in line:
                draft.請求元 = re.sub(r"^(請求元|請求者|発行者)[：:\s]*", "", line).strip()
                break

    issue_raw = _line_after_label(r"(?:発行日|請求日|日付)", text)
    due_raw = _line_after_label(r"(?:支払期限|お支払期限|期限)", text)
    draft.発行日 = parse_date(issue_raw)
    draft.支払期限 = parse_date(due_raw)

    net_raw = _first_group(r"(?:小計|本体)[^\n]{0,24}([¥￥][\d,]+)", text)
    if not net_raw:
        net_raw = _first_group(r"税抜[^\n]{0,16}([¥￥]?[\d,]+)", text)
    tax_raw = _first_group(r"消費税(?:（\d+%）)?[：:\s]*([¥￥][\d,]+)", text)
    if not tax_raw:
        tax_raw = _first_group(r"税額[：:\s]*([¥￥]?[\d,]+)", text)
    gross_raw = _first_group(r"(?:合計|ご請求金額)[^\n]{0,24}([¥￥][\d,]+)", text)
    draft.税抜金額 = parse_yen(net_raw)
    draft.消費税 = parse_yen(tax_raw)
    draft.税込金額 = parse_yen(gross_raw)
    if draft.税込金額 is None:
        loose = _first_group(r"税込\s*([¥￥]?[\d,]+)\s*円?", text)
        draft.税込金額 = parse_yen(loose)
    return draft
