from __future__ import annotations

import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch

from pypdf import PdfWriter

from generate_sample import sample_files
from src.extract import (
    MAX_FILE_BYTES,
    InputFormatError,
    InvoiceDraft,
    extract_invoice,
    format_excerpt,
    parse_date,
    parse_yen,
    read_source,
)
from src.llm import AI_FILL_REASON, _openai_complete
from src.run import MAX_FILES, input_fingerprint, plan_review, review_files
from src.validate import apply_duplicate_numbers, check_tax, judgment_criteria_rows, validate_one


def invoice(**kwargs: object) -> InvoiceDraft:
    return InvoiceDraft(**kwargs)  # type: ignore[arg-type]


class ParseHelpersTests(unittest.TestCase):
    def test_parse_yen(self) -> None:
        self.assertEqual(parse_yen("¥44,000"), 44000)
        self.assertEqual(parse_yen("33,000円"), 33000)

    def test_parse_date(self) -> None:
        self.assertEqual(parse_date("2026年8月1日").isoformat(), "2026-08-01")
        self.assertEqual(parse_date("2026/8/31").isoformat(), "2026-08-31")
        self.assertIsNone(parse_date("来月末"))


class ExtractTests(unittest.TestCase):
    def test_standard_invoice(self) -> None:
        text = """
請求番号：INV-1
発行日：2026年8月1日
支払期限：2026年8月31日
請求元：みどり文具株式会社
小計（税抜）    ¥40,000
消費税（10%）   ¥4,000
合計（税込）    ¥44,000
"""
        draft = extract_invoice("a.txt", text)
        self.assertEqual(draft.請求番号, "INV-1")
        self.assertEqual(draft.請求元, "みどり文具株式会社")
        self.assertEqual(draft.税抜金額, 40000)
        self.assertEqual(draft.消費税, 4000)
        self.assertEqual(draft.税込金額, 44000)
        self.assertEqual(draft.抜き出し, "規則")
        self.assertIn("\n", draft.原文抜粋)
        self.assertIn("請求番号：INV-1", draft.原文抜粋)
        self.assertIn("発行日：2026年8月1日", draft.原文抜粋)

    def test_excerpt_splits_labels_and_sentences(self) -> None:
        joined = format_excerpt(
            "請求書 請求番号：INV-1 発行日：2026年8月1日 請求元：みどり文具株式会社"
        )
        self.assertEqual(
            joined.split("\n"),
            ["請求書", "請求番号：INV-1", "発行日：2026年8月1日", "請求元：みどり文具株式会社"],
        )
        mail = format_excerpt("山田です。先月のデザイン料、税込33,000円でお願いします。番号は忘れました。")
        self.assertEqual(
            mail.split("\n"),
            ["山田です。", "先月のデザイン料、税込33,000円でお願いします。", "番号は忘れました。"],
        )

    def test_loose_gross_only(self) -> None:
        draft = extract_invoice("mail.txt", "山田です。税込33,000円でお願いします。")
        self.assertEqual(draft.税込金額, 33000)
        self.assertEqual(draft.請求元, "")


class ValidateTests(unittest.TestCase):
    def test_tax_mismatch(self) -> None:
        draft = invoice(ファイル名="a.txt", 税抜金額=12000, 消費税=1000, 税込金額=15000)
        self.assertEqual(check_tax(draft), "税抜＋消費税と税込が合いません")

    def test_tax_ok(self) -> None:
        draft = invoice(ファイル名="a.txt", 税抜金額=40000, 消費税=4000, 税込金額=44000)
        self.assertIsNone(check_tax(draft))

    def test_due_before_issue(self) -> None:
        from datetime import date

        draft = invoice(
            ファイル名="a.txt",
            請求元="海辺印刷",
            請求番号="INV-4",
            発行日=date(2026, 8, 10),
            支払期限=date(2026, 8, 1),
            税込金額=22000,
            税抜金額=20000,
            消費税=2000,
        )
        reasons = validate_one(draft)
        self.assertIn("支払期限が発行日より前です", reasons)

    def test_duplicate_reason_names_counterpart(self) -> None:
        midori = invoice(
            ファイル名="01_みどり文具.txt",
            請求番号="INV-2026-0801",
            請求元="みどり文具株式会社",
        )
        hoshimi = invoice(
            ファイル名="06_星見工房.txt",
            請求番号="INV-2026-0801",
            請求元="星見工房",
        )
        apply_duplicate_numbers([midori, hoshimi])
        self.assertEqual(
            midori.reasons,
            ["請求番号 INV-2026-0801 が星見工房（06_星見工房.txt）と重複しています"],
        )
        self.assertEqual(
            hoshimi.reasons,
            ["請求番号 INV-2026-0801 がみどり文具株式会社（01_みどり文具.txt）と重複しています"],
        )

    def test_duplicate_normalizes_fullwidth(self) -> None:
        a = invoice(ファイル名="a.txt", 請求番号="INV-1", 請求元="A社")
        b = invoice(ファイル名="b.txt", 請求番号="ＩＮＶ-１", 請求元="B社")
        apply_duplicate_numbers([a, b])
        self.assertTrue(any("重複" in reason for reason in a.reasons))
        self.assertTrue(any("重複" in reason for reason in b.reasons))

    def test_criteria_mentions_ai_and_due_date(self) -> None:
        text = " ".join(rule for _name, rule in judgment_criteria_rows())
        self.assertIn("支払期限", text)
        self.assertIn("生成AI", text)
        self.assertNotIn("番号なし", " ".join(name for name, _rule in judgment_criteria_rows()))


class ReviewSampleTests(unittest.TestCase):
    def test_bundled_samples_without_llm(self) -> None:
        result = review_files(sample_files(), api_key="")
        self.assertEqual(result.file_count, 6)
        self.assertEqual(result.ok_count, 1)
        self.assertEqual(result.issue_count, 5)
        self.assertFalse(result.used_llm)
        self.assertFalse(result.llm_called)
        self.assertEqual(result.ok_gross, 60500)
        reasons = " ".join(result.issues["理由"].astype(str))
        self.assertIn("税抜＋消費税と税込が合いません", reasons)
        self.assertIn("支払期限が発行日より前です", reasons)
        self.assertIn("星見工房（06_星見工房.txt）と重複しています", reasons)
        self.assertIn("みどり文具株式会社（01_みどり文具.txt）と重複しています", reasons)
        self.assertIn("請求番号が空です", reasons)
        ok_names = set(result.rows.loc[result.rows["判定"] == "転記可", "請求元"])
        self.assertEqual(ok_names, {"株式会社 青葉オフィス"})
        self.assertEqual(list(result.rows.columns[:3]), ["判定", "ファイル名", "原文抜粋"])

    def test_llm_fills_gaps_only(self) -> None:
        files = [
            (
                "mail.txt",
                "山田です。先月のデザイン料、税込33,000円でお願いします。".encode("utf-8"),
            )
        ]

        def fake(_system: str, _user: str) -> dict:
            return {
                "請求番号": "MAIL-9",
                "請求元": "山田デザイン事務所",
                "発行日": "2026-07-31",
                "支払期限": "2026-08-31",
                "税抜金額": "",
                "消費税": "",
                "税込金額": "999999",
            }

        result = review_files(files, api_key="sk-test", completer=fake)
        row = result.rows.iloc[0]
        self.assertEqual(row["請求元"], "山田デザイン事務所")
        self.assertEqual(row["請求番号"], "MAIL-9")
        self.assertEqual(row["税込金額"], 33000)
        self.assertEqual(row["項目の取得方法"], "プログラム＋生成AIで補完")
        self.assertEqual(row["判定"], "要確認")
        self.assertIn(AI_FILL_REASON, row["理由"])
        self.assertTrue(result.used_llm)
        self.assertTrue(result.llm_called)

    def test_complete_invoice_does_not_call_llm(self) -> None:
        files = [
            (
                "ok.txt",
                (
                    "請求番号：INV-5\n発行日：2026年8月12日\n支払期限：2026年9月11日\n"
                    "請求元：株式会社 青葉オフィス\n小計（税抜）    ¥55,000\n"
                    "消費税（10%）   ¥5,500\n合計（税込）    ¥60,500\n"
                ).encode("utf-8"),
            )
        ]
        calls = {"n": 0}

        def fake(_system: str, _user: str) -> dict:
            calls["n"] += 1
            return {}

        result = review_files(files, api_key="sk-test", completer=fake)
        self.assertEqual(calls["n"], 0)
        self.assertFalse(result.llm_called)
        self.assertFalse(result.used_llm)
        self.assertEqual(result.rows.iloc[0]["判定"], "転記可")

    def test_llm_called_but_nothing_filled(self) -> None:
        files = [
            (
                "mail.txt",
                "山田です。先月のデザイン料、税込33,000円でお願いします。".encode("utf-8"),
            )
        ]

        def fake(_system: str, _user: str) -> dict:
            return {
                "請求番号": "",
                "請求元": "",
                "発行日": "",
                "支払期限": "",
                "税抜金額": "",
                "消費税": "",
                "税込金額": "",
            }

        result = review_files(files, api_key="sk-test", completer=fake)
        self.assertTrue(result.llm_called)
        self.assertFalse(result.used_llm)
        self.assertEqual(result.rows.iloc[0]["項目の取得方法"], "プログラムだけ")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(InputFormatError):
            review_files([])

    def test_pdf_without_text(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buf = BytesIO()
        writer.write(buf)
        with self.assertRaises(InputFormatError):
            read_source("scan.pdf", buf.getvalue())

    def test_file_too_large(self) -> None:
        with self.assertRaises(InputFormatError):
            read_source("huge.txt", b"a" * (MAX_FILE_BYTES + 1))

    def test_file_count_capped(self) -> None:
        files = [(f"{index}.txt", b"x") for index in range(MAX_FILES + 1)]
        with self.assertRaises(InputFormatError) as caught:
            review_files(files)
        self.assertIn(str(MAX_FILES), str(caught.exception))

    def test_plan_counts_llm_calls(self) -> None:
        files = sample_files()
        without_key = plan_review(files, api_key="")
        self.assertEqual(without_key.file_count, 6)
        self.assertEqual(without_key.api_calls, 0)
        self.assertEqual(without_key.model, "gpt-4o-mini")
        with_key = plan_review(files, api_key="sk-test")
        self.assertEqual(with_key.llm_needed, 1)
        self.assertEqual(with_key.api_calls, 1)

    def test_fingerprint_changes_with_key(self) -> None:
        files = [("a.txt", b"hello")]
        self.assertEqual(input_fingerprint(files, False), input_fingerprint(files, False))
        self.assertNotEqual(input_fingerprint(files, False), input_fingerprint(files, True))


class OpenAIErrorTests(unittest.TestCase):
    def test_unauthorized_key(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions",
            401,
            "Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                _openai_complete("sys", "user", "sk-bad")
        self.assertIn("認証に失敗", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
