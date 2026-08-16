from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from generate_sample import CLIENT, ZIP_NAME, sample_files, write_samples
from src.excel_io import build_result_xlsx
from src.extract import InputFormatError
from src.llm import api_key_from_env
from src.run import MAX_FILES, input_fingerprint, order_columns, plan_review, review_files
from src.validate import judgment_criteria_rows


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def format_yen(value: object) -> str:
    if _is_missing(value):
        return ""
    try:
        return f"¥{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


def cell_text(value: object) -> str:
    if _is_missing(value):
        return ""
    return str(value)


def present_table(df: pd.DataFrame) -> pd.DataFrame:
    view = order_columns(df.copy())
    for col in ("税抜金額", "消費税", "税込金額"):
        if col in view.columns:
            view[col] = view[col].map(format_yen)
    return view


def render_review_table(df: pd.DataFrame) -> None:
    view = present_table(df)
    headers = list(view.columns)

    def wrap_class(name: str) -> str:
        if name == "理由":
            return ' class="wrap wrap-reason"'
        if name == "原文抜粋":
            return ' class="wrap wrap-excerpt"'
        return ""

    head = "".join(
        f"<th{wrap_class(name)}>{html.escape(str(name))}</th>" for name in headers
    )
    rows: list[str] = []
    for _, row in view.iterrows():
        css = "issue" if str(row.get("判定", "")) == "要確認" else "ok"
        cells: list[str] = []
        for name in headers:
            cells.append(
                f"<td{wrap_class(name)}>{html.escape(cell_text(row[name]))}</td>"
            )
        rows.append(f'<tr class="{css}">{"".join(cells)}</tr>')
    st.html(
        f'<div class="review-table-wrap"><table class="review-table">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


st.set_page_config(
    page_title="請求書の転記チェック",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data
def _sample_zip_bytes() -> bytes:
    return write_samples().read_bytes()


sample_zip = _sample_zip_bytes()

st.html(
    """
<style>
[data-testid="stFileUploaderDropzoneInstructions"] > div > span,
[data-testid="stFileDropzoneInstructions"] > div > span {
  visibility: hidden;
}
[data-testid="stFileUploaderDropzoneInstructions"] > div > span::after,
[data-testid="stFileDropzoneInstructions"] > div > span::after {
  content: "ファイルをここにドラッグ＆ドロップ";
  visibility: visible;
  display: block;
}
[data-testid="stFileUploaderDropzoneInstructions"] > div > small,
[data-testid="stFileDropzoneInstructions"] > div > small {
  visibility: hidden;
}
[data-testid="stFileUploaderDropzoneInstructions"] > div > small::after,
[data-testid="stFileDropzoneInstructions"] > div > small::after {
  content: "TXT / MD / PDF ・ 複数可";
  visibility: visible;
  display: block;
}
[data-testid="stFileUploader"] [data-testid="stBaseButton-secondary"] {
  text-indent: -9999px;
  line-height: 0;
}
[data-testid="stFileUploader"] [data-testid="stBaseButton-secondary"]::after {
  content: "ファイルを選択";
  text-indent: 0;
  line-height: initial;
  display: inline-block;
}
.stAppDeployButton, [data-testid="stAppDeployButton"] {
  display: none !important;
}
[data-testid="stSidebar"] {
  border-right: 3px solid #8B3A2A;
}
.review-hero {
  background: linear-gradient(120deg, #8B3A2A 0%, #A34A32 55%, #C45C38 100%);
  color: #FBF4EE;
  padding: 1.35rem 1.5rem 1.2rem;
  border-radius: 18px;
  margin-bottom: 0.4rem;
}
.review-hero .kicker {
  display: inline-block;
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  background: rgba(255,255,255,0.16);
  border: 1px solid rgba(255,255,255,0.28);
  padding: 0.18rem 0.55rem;
  border-radius: 999px;
  margin-bottom: 0.55rem;
}
.review-hero h1 {
  font-size: 1.7rem;
  margin: 0 0 0.35rem 0;
  font-weight: 700;
}
.review-hero p {
  margin: 0;
  opacity: 0.92;
  font-size: 0.95rem;
}
.scope-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 0.6rem 0 0.9rem;
}
.scope-card {
  background: #FBF6F0;
  border: 1px solid #E6D5C6;
  border-radius: 14px;
  padding: 0.9rem 1rem;
}
.scope-card h3 {
  margin: 0 0 0.4rem 0;
  font-size: 0.92rem;
}
.scope-card ul {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.88rem;
  line-height: 1.55;
}
.scope-card.out {
  background: #F4EEE6;
  border-color: #DDD0C0;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0.2rem 0 0.8rem;
}
.pill {
  font-size: 0.78rem;
  padding: 0.22rem 0.7rem;
  border-radius: 999px;
  border: 1px solid transparent;
}
.pill.issue { background: #E8B4A8; }
.pill.ok { background: #D4E8C4; }
.idle-card {
  background: #FBF6F0;
  border: 1px dashed #8B3A2A;
  border-radius: 16px;
  padding: 1.4rem 1.3rem;
  text-align: center;
}
.idle-card .label {
  color: #8B3A2A;
  font-weight: 700;
  letter-spacing: 0.06em;
  font-size: 0.78rem;
  margin-bottom: 0.4rem;
}
.idle-card p {
  margin: 0;
  color: #4A372C;
}
.confirm-card {
  background: #FBF6F0;
  border: 1px solid #8B3A2A;
  border-radius: 16px;
  padding: 1.2rem 1.3rem 1.1rem;
  margin-bottom: 0.8rem;
}
.confirm-card .label {
  color: #8B3A2A;
  font-weight: 700;
  letter-spacing: 0.06em;
  font-size: 0.78rem;
  margin-bottom: 0.45rem;
}
.confirm-card ul {
  margin: 0.2rem 0 0;
  padding-left: 1.15rem;
  color: #4A372C;
  font-size: 0.92rem;
  line-height: 1.6;
}
@media (max-width: 900px) {
  .scope-grid { grid-template-columns: 1fr; }
}
.review-table-wrap {
  overflow-x: auto;
  border: 1px solid #E6D5C6;
  border-radius: 12px;
  background: #FBF6F0;
}
.review-table {
  border-collapse: collapse;
  width: max-content;
  min-width: 100%;
  font-size: 0.86rem;
}
.review-table th {
  background: #8B3A2A;
  color: #FBF4EE;
  padding: 0.55rem 0.7rem;
  text-align: left;
  white-space: nowrap;
}
.review-table td {
  padding: 0.55rem 0.7rem;
  vertical-align: top;
  border-bottom: 1px solid #E6D5C6;
  color: #2A1F18;
  white-space: nowrap;
}
.review-table td.wrap {
  white-space: pre-line;
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.55;
}
.review-table th.wrap-excerpt,
.review-table td.wrap-excerpt {
  min-width: 18rem;
  max-width: 28rem;
}
.review-table th.wrap-reason,
.review-table td.wrap-reason {
  min-width: 9rem;
  max-width: 14rem;
}
.review-table tr.issue td { background: #E8B4A8; }
.review-table tr.ok td { background: #D4E8C4; }
</style>
"""
)

with st.sidebar:
    st.markdown("### 検収パネル")
    st.caption("届いた請求書を上げると、抜いた項目と要確認が出ます。")
    st.download_button(
        label="サンプル一式",
        data=sample_zip,
        file_name=ZIP_NAME,
        mime="application/zip",
        use_container_width=True,
    )
    uploaded = st.file_uploader(
        "請求書（複数可）",
        type=["txt", "md", "pdf"],
        accept_multiple_files=True,
        help=f"文字のある PDF か、テキストの請求書。スキャン画像は範囲外です。一度に {MAX_FILES} 件まで。",
    )
    use_sample = st.checkbox("同梱サンプルで試す", value=False)
    st.caption("生成AIは必須ではありません。鍵は保存しません。鍵があるときだけ、抜けた項目の原文を OpenAI へ送ります。")
    typed_key = st.text_input(
        "OpenAI APIキー（任意）",
        type="password",
        placeholder="空なら規則と検算だけ",
        help="抜けた項目の補完にだけ使います。埋め込まれた値は上書きしません。補完した行は要確認のままです。",
    )
    run = st.button("読み取って検算する", type="primary", use_container_width=True)

st.html(
    f"""
<div class="review-hero">
  <div class="kicker">DEMO · {CLIENT}</div>
  <h1>請求書の転記チェック</h1>
  <p>届いた請求書から日付・金額・取引先を抜き、税計算と必須項目を検算します。怪しい行だけ要確認にして Excel に残します。</p>
</div>
<div class="scope-grid">
  <div class="scope-card">
    <h3>このデモで見られること</h3>
    <ul>
      <li>請求番号・請求元・日付・税額の抜き出し</li>
      <li>税抜＋消費税と税込の検算</li>
      <li>番号の重複、期限の前後、欠けた項目</li>
    </ul>
  </div>
  <div class="scope-card out">
    <h3>このデモの範囲外</h3>
    <ul>
      <li>スキャン画像の OCR</li>
      <li>会計ソフトへの自動登録</li>
      <li>生成AIのチャット画面</li>
    </ul>
  </div>
</div>
"""
)

def collect_files() -> list[tuple[str, bytes]]:
    if use_sample:
        return sample_files()
    if uploaded:
        return [(item.name, item.getvalue()) for item in uploaded]
    return []


def current_api_key() -> str:
    return typed_key.strip() or api_key_from_env()


def show_process_error(exc: BaseException) -> None:
    if isinstance(exc, InputFormatError):
        st.error(str(exc))
    elif isinstance(exc, RuntimeError):
        st.error(str(exc))
    else:
        st.error(f"処理に失敗しました。（{exc}）")
    st.stop()


def execute_review(files: list[tuple[str, bytes]], api_key: str, fingerprint: str) -> None:
    try:
        result = review_files(files, api_key=api_key)
    except (InputFormatError, RuntimeError, Exception) as exc:  # noqa: BLE001
        show_process_error(exc)
        return
    st.session_state.review_result = result
    st.session_state.review_xlsx = build_result_xlsx(result)
    st.session_state.completed_fingerprint = fingerprint
    st.session_state.pending_review = None
    st.session_state.pending_plan = None
    st.session_state.reused_result = False


if run:
    files = collect_files()
    if not files:
        st.session_state.pending_review = None
        st.session_state.pending_plan = None
        st.session_state.review_result = None
        st.session_state.review_xlsx = None
        st.warning("請求書を上げるか、「同梱サンプルで試す」にチェックしてから実行してください。")
    else:
        api_key = current_api_key()
        fingerprint = input_fingerprint(files, bool(api_key))
        if (
            fingerprint == st.session_state.get("completed_fingerprint")
            and st.session_state.get("review_result") is not None
        ):
            st.session_state.pending_review = None
            st.session_state.pending_plan = None
            st.session_state.reused_result = True
        else:
            try:
                plan = plan_review(files, api_key)
            except InputFormatError as exc:
                st.error(str(exc))
                st.stop()
            st.session_state.pending_review = {"files": files, "fingerprint": fingerprint}
            st.session_state.pending_plan = plan
            st.session_state.reused_result = False

pending = st.session_state.get("pending_review")
plan = st.session_state.get("pending_plan")
if pending and plan is not None:
    st.html(
        f"""
<div class="confirm-card">
  <div class="label">実行前の確認</div>
  <ul>
    <li>ファイル {plan.file_count} 件（上限 {MAX_FILES} 件）</li>
    <li>モデル {plan.model}（鍵があるときだけ使う）</li>
    <li>生成AIの呼び出し {plan.api_calls} 回（欠落 {plan.llm_needed} 件）</li>
  </ul>
</div>
"""
    )
    confirm_l, confirm_r = st.columns(2)
    with confirm_l:
        confirmed = st.button("実行する", type="primary", use_container_width=True)
    with confirm_r:
        cancelled = st.button("やめる", use_container_width=True)
    if cancelled:
        st.session_state.pending_review = None
        st.session_state.pending_plan = None
        st.rerun()
    if confirmed:
        api_key = current_api_key()
        fingerprint = input_fingerprint(pending["files"], bool(api_key))
        if fingerprint != pending["fingerprint"]:
            try:
                plan = plan_review(pending["files"], api_key)
            except InputFormatError as exc:
                st.error(str(exc))
                st.stop()
            st.session_state.pending_review = {
                "files": pending["files"],
                "fingerprint": fingerprint,
            }
            st.session_state.pending_plan = plan
            st.rerun()
        execute_review(pending["files"], api_key, fingerprint)
        st.rerun()
    st.stop()

if st.session_state.get("reused_result"):
    st.info("同じファイルのため、前回の結果を表示しています。生成AIは再呼び出ししていません。")

result = st.session_state.get("review_result")
if result is None:
    st.html(
        """
<div class="idle-card">
  <div class="label">待機中</div>
  <p>左で請求書を上げるか、サンプルにチェックしてから「読み取って検算する」を押すと、ここに結果が出ます。</p>
</div>
"""
    )
    st.stop()

xlsx_bytes = build_result_xlsx(result)
st.session_state.review_xlsx = xlsx_bytes

with st.container(border=True):
    head_l, head_r = st.columns([3.2, 1.2])
    with head_l:
        st.markdown("#### 今回の検収結果")
        st.caption(f"受取側：{CLIENT}")
    with head_r:
        st.download_button(
            label="結果Excelを保存",
            data=xlsx_bytes,
            file_name="請求書_転記チェック.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
    st.caption(result.source_note)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ファイル", f"{result.file_count} 件")
    m2.metric("転記可", f"{result.ok_count} 件")
    m3.metric("要確認", f"{result.issue_count} 件")
    m4.metric("転記可の税込", format_yen(result.ok_gross) or "¥0")

    if result.issue_count:
        st.warning(
            f"要確認が {result.issue_count} 件あります。"
            "転記一覧には残していますが、支払や会計への転記の前に理由を見てください。"
        )

    st.html(
        f"""
<div class="legend">
  <span class="pill issue">要確認 {result.issue_count}</span>
  <span class="pill ok">転記可 {result.ok_count}</span>
</div>
"""
    )

    tab_issues, tab_all, tab_rules = st.tabs(["要確認", "転記一覧", "判定基準"])
    with tab_issues:
        if result.issues.empty:
            st.success("要確認の行はありません。")
        else:
            render_review_table(result.issues)
    with tab_all:
        render_review_table(result.rows)
    with tab_rules:
        for name, rule in judgment_criteria_rows():
            st.markdown(f"**{name}** — {rule}")
