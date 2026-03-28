"""
JARTIC オープン交通データ 一括ダウンローダー
https://www.jartic-open-traffic.org/

提供データ:
  様式1: t_travospublic_measure_5m     (常設トラカン 5分間) 過去1ヶ月
  様式2: t_travospublic_measure_1h     (常設トラカン 1時間) 過去3ヶ月
  様式3: t_travospublic_measure_5m_img (CCTVトラカン 5分間) 過去1ヶ月
  様式4: t_travospublic_measure_1h_img (CCTVトラカン 1時間) 過去3ヶ月

バッチ戦略:
  5分間データ: 2時間バッチ (12バッチ/日, ~4MB/バッチ)
  1時間データ: 1日バッチ   (1バッチ/日, ~3.7MB/バッチ)
"""

import urllib.request
import urllib.error
import os
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

# ─── 設定 ──────────────────────────────────────────────
BASE_URL = "https://api.jartic-open-traffic.org/geoserver"

# 日本全国のバウンディングボックス (minX, minY, maxX, maxY)
JAPAN_BBOX = (122.0, 24.0, 154.0, 46.0)

OUTPUT_DIR = Path(__file__).parent.parent / "data"

# ダウンロードする様式の設定
DATASETS = [
    {
        "id": "shoshiki1",
        "label": "様式1 (常設トラカン 5分間)",
        "layer": "t_travospublic_measure_5m",
        "interval_min": 5,
        "period_days": 31,
        "batch_hours": 2,   # 2時間バッチ (~4MB)
        "enabled": True,
    },
    {
        "id": "shoshiki2",
        "label": "様式2 (常設トラカン 1時間)",
        "layer": "t_travospublic_measure_1h",
        "interval_min": 60,
        "period_days": 91,
        "batch_hours": 24,  # 1日バッチ (~3.7MB)
        "enabled": True,
    },
    {
        "id": "shoshiki3",
        "label": "様式3 (CCTVトラカン 5分間)",
        "layer": "t_travospublic_measure_5m_img",
        "interval_min": 5,
        "period_days": 31,
        "batch_hours": 2,   # 2時間バッチ (~4MB)
        "enabled": True,
    },
    {
        "id": "shoshiki4",
        "label": "様式4 (CCTVトラカン 1時間)",
        "layer": "t_travospublic_measure_1h_img",
        "interval_min": 60,
        "period_days": 91,
        "batch_hours": 24,  # 1日バッチ (~3.2MB)
        "enabled": True,
    },
]

MAX_WORKERS = 8           # 並列ワーカー数（コア数に合わせて調整）
MAX_RETRIES = 3           # リトライ回数
RETRY_DELAY_SEC = 10      # リトライ間隔（秒）
# ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            Path(__file__).parent.parent / "download_jartic.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def align_to_batch(dt: datetime, batch_hours: int) -> datetime:
    """datetimeをbatch_hours単位の境界に切り捨て（実行時刻非依存）"""
    hour = (dt.hour // batch_hours) * batch_hours
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def to_time_code(dt: datetime) -> int:
    """datetimeを時間コード(YYYYMMDDhhmm)に変換"""
    return int(dt.strftime("%Y%m%d%H%M"))


def build_url(layer: str, tc_start: int, tc_end: int, bbox: tuple) -> str:
    """APIリクエストURLを構築

    注意: CQLフィルタのフィールド名はクォートなしで指定すること。
    """
    minx, miny, maxx, maxy = bbox
    cql_raw = (
        f"道路種別=3 AND "
        f"時間コード>={tc_start} AND "
        f"時間コード<={tc_end} AND "
        f"BBOX(ジオメトリ,{minx},{miny},{maxx},{maxy},'EPSG:4326')"
    )
    cql_enc = quote(cql_raw, safe="")
    return (
        f"{BASE_URL}?service=WFS&version=2.0.0&request=GetFeature"
        f"&typeNames={layer}&srsName=EPSG:4326"
        f"&outputFormat=csv"
        f"&exceptions=application/json"
        f"&cql_filter={cql_enc}"
    )


# 最初の座標ペアのみ取得（複数点のMULTIPOINTにも対応）
# 例: MULTIPOINT ((lon1 lat1))
# 例: MULTIPOINT ((lon1 lat1), (lon2 lat2))
_MP_RE = re.compile(r"MULTIPOINT \(\(([0-9.\-]+) ([0-9.\-]+)")


def parse_csv_response(raw_bytes: bytes) -> str:
    """レスポンスバイト列を通常のCSV文字列に変換

    APIレスポンスは外側をダブルクォートで囲み、改行を \\r\\n（4文字）で
    エスケープした形式で返ってくる。これを通常のCSVに変換する。
    ジオメトリ列 "MULTIPOINT ((lon lat))" を経度・緯度の2列に分割する。

    csv.readerで正しくパースしてからジオメトリ変換することで、
    フィールド内クォートによる誤動作を防ぐ。
    """
    import csv as _csv
    import io

    text = raw_bytes.decode("utf-8")
    inner = text.strip('"')
    csv_text = inner.replace("\\r\\n", "\n")

    reader = _csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return ""

    # ヘッダ: ジオメトリ → 経度, 緯度
    header = rows[0]
    try:
        geom_idx = header.index("ジオメトリ")
        header = header[:geom_idx] + ["経度", "緯度"] + header[geom_idx + 1:]
    except ValueError:
        geom_idx = -1  # ジオメトリ列なし

    out_rows = [header]
    for row in rows[1:]:
        if not any(row):  # 空行スキップ
            continue
        if geom_idx >= 0 and geom_idx < len(row):
            m = _MP_RE.search(row[geom_idx])
            if m:
                lon, lat = m.group(1), m.group(2)
                row = row[:geom_idx] + [lon, lat] + row[geom_idx + 1:]
        out_rows.append(row)

    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerows(out_rows)
    return buf.getvalue()


def fetch_with_retry(url: str) -> bytes | None:
    """リトライ付きHTTPリクエスト。成功時はバイト列、失敗時はNone。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                raw = resp.read()
            # エラーレスポンス確認（JSONで返ってくる場合）
            if raw.startswith(b"{"):
                log.error(f"APIエラー: {raw[:200].decode('utf-8', errors='replace')}")
                return None
            return raw
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            log.warning(f"HTTP {e.code} (attempt {attempt}/{MAX_RETRIES}): {body}")
        except Exception as e:
            log.warning(f"接続エラー (attempt {attempt}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    log.error(f"取得失敗（{MAX_RETRIES}回リトライ）: {url[:150]}")
    return None


def count_csv_rows(csv_text: str) -> int:
    """CSV文字列のデータ行数（ヘッダ除く）を返す"""
    lines = [l for l in csv_text.split("\n") if l.strip()]
    return max(0, len(lines) - 1)


def _fetch_one(task: dict) -> dict:
    """1バッチ分を取得して保存する（スレッドプール用）"""
    fname: Path = task["fname"]
    url: str = task["url"]
    tc_start: int = task["tc_start"]
    tc_end: int = task["tc_end"]

    if fname.exists() and fname.stat().st_size > 0:
        return {"status": "skip"}

    raw = fetch_with_retry(url)
    if raw is None:
        return {"status": "error"}

    csv_text = parse_csv_response(raw)
    rows = count_csv_rows(csv_text)
    if rows == 0:
        log.debug(f"  空データ: {tc_start}-{tc_end}")
        return {"status": "empty"}

    fname.write_text(csv_text, encoding="utf-8")
    return {"status": "ok", "rows": rows}


def batch_fname(current: datetime, batch_hours: int) -> str:
    """バッチ開始時刻からファイル名を生成
    - 日次バッチ (24h): YYYYMMDD.csv
    - 時間バッチ (2h等): YYYYMMDD_HHMM.csv
    """
    if batch_hours >= 24:
        return f"{current:%Y%m%d}.csv"
    return f"{current:%Y%m%d_%H%M}.csv"


def build_tasks(ds: dict, now: datetime) -> list[dict]:
    """1データセット分の全バッチタスクリストを生成"""
    interval_min = ds["interval_min"]
    period_days = ds["period_days"]
    batch_hours = ds["batch_hours"]
    ds_id = ds["id"]

    # バッチ境界に揃えることで実行タイミング非依存のファイル名にする
    end_dt = align_to_batch(now, batch_hours)
    start_dt = align_to_batch(now - timedelta(days=period_days), batch_hours)

    out_dir = OUTPUT_DIR / ds_id
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    batch_delta = timedelta(hours=batch_hours)
    current = start_dt
    while current < end_dt:
        batch_end = current + batch_delta - timedelta(minutes=interval_min)
        tc_start = to_time_code(current)
        tc_end = to_time_code(batch_end)
        tasks.append({
            "ds_id": ds_id,
            "label": ds["label"],
            "fname": out_dir / batch_fname(current, batch_hours),
            "url": build_url(ds["layer"], tc_start, tc_end, JAPAN_BBOX),
            "tc_start": tc_start,
            "tc_end": tc_end,
        })
        current += batch_delta
    return tasks


def estimate_requests(now: datetime) -> int:
    """総リクエスト数を事前推計"""
    total = 0
    for ds in DATASETS:
        if not ds["enabled"]:
            continue
        batches_per_day = 24 // ds["batch_hours"]
        total += ds["period_days"] * batches_per_day
    return total


def main():
    now = datetime.utcnow() + timedelta(hours=9)  # JST
    log.info(f"JARTIC データダウンロード開始: {now:%Y-%m-%d %H:%M:%S JST}")
    log.info(f"出力先: {OUTPUT_DIR.resolve()}")
    log.info(f"並列ワーカー数: {MAX_WORKERS}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 全様式・全バッチのタスクを一括生成し、ラウンドロビン順に並べる
    # → 様式1[0], 様式2[0], 様式3[0], 様式4[0], 様式1[1], 様式2[1], ...
    # → 全様式が均等にワーカーへ割り当てられる
    per_ds_tasks = []
    for ds in DATASETS:
        if not ds["enabled"]:
            log.info(f"スキップ: {ds['label']}")
            continue
        tasks = build_tasks(ds, now)
        log.info(f"  {ds['label']}: {len(tasks)}バッチ")
        per_ds_tasks.append(tasks)

    from itertools import zip_longest
    all_tasks = [
        t for group in zip_longest(*per_ds_tasks)
        for t in group if t is not None
    ]

    log.info(f"総タスク数: {len(all_tasks)} (うちスキップ予定: {sum(1 for t in all_tasks if t['fname'].exists() and t['fname'].stat().st_size > 0)})")

    # 全様式を同時並列実行
    counters: dict[str, dict] = {
        ds["id"]: {"req": 0, "skip": 0, "error": 0, "empty": 0, "rows": 0}
        for ds in DATASETS if ds["enabled"]
    }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in all_tasks}
        for future in as_completed(futures):
            task = futures[future]
            ds_id = task["ds_id"]
            result = future.result()
            status = result["status"]
            c = counters[ds_id]
            if status == "skip":
                c["skip"] += 1
            elif status == "error":
                c["error"] += 1
                c["req"] += 1
            elif status == "empty":
                c["empty"] += 1
                c["req"] += 1
            else:
                c["req"] += 1
                c["rows"] += result.get("rows", 0)

    total_req = total_err = 0
    for ds in DATASETS:
        if not ds["enabled"]:
            continue
        c = counters[ds["id"]]
        log.info(
            f"  {ds['label']}: 取得={c['req']}, スキップ={c['skip']}, "
            f"エラー={c['error']}, 空={c['empty']}, 総行数={c['rows']:,}"
        )
        total_req += c["req"]
        total_err += c["error"]

    log.info(f"=== 全体完了: リクエスト総数={total_req:,}, エラー={total_err} ===")


if __name__ == "__main__":
    main()
