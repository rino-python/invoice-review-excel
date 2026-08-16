"""抜けた項目だけ生成AIで補完する。鍵は引数で受け取り、保存しない。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from src.extract import InvoiceDraft, parse_date, parse_yen

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"
AI_FILL_REASON = "生成AIが抜けた項目を補完しています。原文と照合してください"

SYSTEM_PROMPT = """あなたは日本の請求書から項目を抜く係です。
推測で金額や日付を作ってはいけません。原文に無い項目は空文字にしてください。
出力は JSON だけ。キーは 請求番号, 請求元, 発行日, 支払期限, 税抜金額, 消費税, 税込金額。
日付は YYYY-MM-DD。金額は数字のみ。"""


Completer = Callable[[str, str], dict]


def api_key_from_env() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def _openai_complete(system: str, user: str, api_key: str) -> dict:
    payload = {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    request = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("生成AIの認証に失敗しました。APIキーを確認してください。") from exc
        if exc.code == 429:
            raise RuntimeError(
                "生成AIの利用上限に達しています。しばらく待つか、残高を確認してください。"
            ) from exc
        raise RuntimeError(f"生成AIの呼び出しに失敗しました。（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"生成AIに接続できませんでした。（{exc.reason}）") from exc
    except TimeoutError as exc:
        raise RuntimeError("生成AIの応答が時間切れです。もう一度試してください。") from exc
    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("生成AIの応答を読めませんでした。") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("生成AIの応答が JSON オブジェクトではありません。")
    return parsed


def needs_llm(draft: InvoiceDraft) -> bool:
    return (
        not draft.請求番号
        or not draft.請求元
        or draft.発行日 is None
        or draft.支払期限 is None
        or draft.税込金額 is None
    )


def _fill_empty(draft: InvoiceDraft, data: dict) -> bool:
    filled = False
    if not draft.請求番号 and str(data.get("請求番号") or "").strip():
        draft.請求番号 = str(data["請求番号"]).strip()
        filled = True
    if not draft.請求元 and str(data.get("請求元") or "").strip():
        draft.請求元 = str(data["請求元"]).strip()
        filled = True
    if draft.発行日 is None:
        parsed = parse_date(str(data.get("発行日") or ""))
        if parsed is not None:
            draft.発行日 = parsed
            filled = True
    if draft.支払期限 is None:
        parsed = parse_date(str(data.get("支払期限") or ""))
        if parsed is not None:
            draft.支払期限 = parsed
            filled = True
    if draft.税抜金額 is None:
        yen = parse_yen(str(data.get("税抜金額") or ""))
        if yen is not None:
            draft.税抜金額 = yen
            filled = True
    if draft.消費税 is None:
        yen = parse_yen(str(data.get("消費税") or ""))
        if yen is not None:
            draft.消費税 = yen
            filled = True
    if draft.税込金額 is None:
        yen = parse_yen(str(data.get("税込金額") or ""))
        if yen is not None:
            draft.税込金額 = yen
            filled = True
    return filled


def complete_gaps(
    draft: InvoiceDraft,
    raw_text: str,
    api_key: str,
    completer: Completer | None = None,
) -> bool:
    """規則で抜けた項目だけ埋める。埋まっている値は上書きしない。

    モデルを呼んだら True。補完した行は要確認のまま残す。
    """
    if not api_key or not needs_llm(draft):
        return False
    user = (
        f"ファイル名: {draft.ファイル名}\n"
        f"規則で取れた項目: 請求番号={draft.請求番号 or '空'} / 請求元={draft.請求元 or '空'} / "
        f"発行日={draft.発行日 or '空'} / 支払期限={draft.支払期限 or '空'} / "
        f"税込={draft.税込金額 if draft.税込金額 is not None else '空'}\n"
        f"原文:\n{raw_text[:4000]}"
    )
    fn = completer or (lambda system, prompt: _openai_complete(system, prompt, api_key))
    data = fn(SYSTEM_PROMPT, user)
    if _fill_empty(draft, data):
        draft.抜き出し = "規則+生成AI"
        if AI_FILL_REASON not in draft.reasons:
            draft.reasons.append(AI_FILL_REASON)
    return True
