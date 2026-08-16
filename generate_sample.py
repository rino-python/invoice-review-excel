from __future__ import annotations

import zipfile
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
ZIP_NAME = "請求書サンプル.zip"
CLIENT = "合同会社 灯台デザイン"

SAMPLES: list[tuple[str, str]] = [
    (
        "01_みどり文具.txt",
        """請求書

請求番号：INV-2026-0801
発行日：2026年8月1日
支払期限：2026年8月31日

請求元：みどり文具株式会社
登録番号：T1234567890123

請求先：合同会社 灯台デザイン 御中

品目
事務用品一式

小計（税抜）    ¥40,000
消費税（10%）   ¥4,000
合計（税込）    ¥44,000
""",
    ),
    (
        "02_北風運送.txt",
        """請求書

請求番号：INV-2026-0802
発行日：2026年8月3日
支払期限：2026年9月2日

請求元：有限会社 北風運送

請求先：合同会社 灯台デザイン 御中

品目
搬入費

小計（税抜）    ¥12,000
消費税（10%）   ¥1,000
合計（税込）    ¥15,000
""",
    ),
    (
        "03_山田デザイン.txt",
        """山田です。先月のデザイン料、税込33,000円でお願いします。
番号は忘れました。期限は来月末くらいで。
""",
    ),
    (
        "04_海辺印刷.txt",
        """請求書

請求番号：INV-2026-0804
発行日：2026年8月10日
支払期限：2026年8月1日

請求元：合同会社 海辺印刷

請求先：合同会社 灯台デザイン 御中

小計（税抜）    ¥20,000
消費税（10%）   ¥2,000
合計（税込）    ¥22,000
""",
    ),
    (
        "05_青葉オフィス.txt",
        """請求書

請求番号：INV-2026-0805
発行日：2026年8月12日
支払期限：2026年9月11日

請求元：株式会社 青葉オフィス

請求先：合同会社 灯台デザイン 御中

品目
レンタルスペース 8月分

小計（税抜）    ¥55,000
消費税（10%）   ¥5,500
合計（税込）    ¥60,500
""",
    ),
    (
        "06_星見工房.txt",
        """請求書

請求番号：INV-2026-0801
発行日：2026年8月14日
支払期限：2026年9月13日

請求元：星見工房

請求先：合同会社 灯台デザイン 御中

小計（税抜）    ¥8,000
消費税（10%）   ¥800
合計（税込）    ¥8,800
""",
    ),
]


def write_samples() -> Path:
    SAMPLES_DIR.mkdir(exist_ok=True)
    for name, body in SAMPLES:
        (SAMPLES_DIR / name).write_text(body.strip() + "\n", encoding="utf-8")
    zip_path = SAMPLES_DIR / ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, _body in SAMPLES:
            zf.write(SAMPLES_DIR / name, arcname=name)
    return zip_path


def sample_files() -> list[tuple[str, bytes]]:
    write_samples()
    return [(name, (SAMPLES_DIR / name).read_bytes()) for name, _body in SAMPLES]


def main() -> Path:
    path = write_samples()
    print(f"wrote {path}")
    return path


if __name__ == "__main__":
    main()
