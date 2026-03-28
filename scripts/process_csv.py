"""
JARTIC 交通量データ前処理スクリプト
CSV → 観測点マスタGeoJSON + 時刻別交通量JSON

出力:
  output/stations.geojson          観測点ジオメトリ（静的、~2,600点）
  output/data_5m/YYYYMMDD.json     5分間交通量（様式1+3合算、1日1ファイル）
  output/data_1h/YYYYMMDD.json     1時間交通量（様式2+4合算、1日1ファイル）

時刻別JSONの構造:
  {
    "202603270000": {
      "1110010": [上り小型, 上り大型, 下り小型, 下り大型],
      ...
    },
    ...
  }
"""

import json
import csv
import gzip
import logging
from collections import defaultdict
from pathlib import Path

# ─── 設定 ──────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "docs"

# 様式グループ定義
GROUPS = {
    "5m": {
        "label": "5分間交通量",
        "datasets": ["shoshiki1", "shoshiki3"],
        "out_dir": "data_5m",
    },
    "1h": {
        "label": "1時間交通量",
        "datasets": ["shoshiki2", "shoshiki4"],
        "out_dir": "data_1h",
    },
}

GZIP_OUTPUT = True  # True: .json.gz で出力
# ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def iter_csv_rows(csv_path: Path):
    """CSVファイルを1行ずつ返すジェネレータ"""
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        yield from reader


def safe_int(val: str) -> int | None:
    """空文字・欠測は None として返す"""
    v = val.strip()
    return int(v) if v else None


def extract_stations(all_datasets: list[str]) -> dict:
    """
    全様式CSVから観測点マスタを抽出する。
    同一観測点コードが複数ファイルに出てくるため重複排除。
    戻り値: {観測点コード: {lon, lat, 地方整備局等番号, 都道府県コード}}
    """
    stations = {}
    for ds_id in all_datasets:
        ds_dir = DATA_DIR / ds_id
        if not ds_dir.exists():
            continue
        files = sorted(ds_dir.glob("*.csv"))
        log.info(f"  観測点抽出: {ds_id} ({len(files)}ファイル)")
        for fpath in files:
            for row in iter_csv_rows(fpath):
                code = row.get("常時観測点コード", "").strip()
                lon = row.get("経度", "").strip()
                lat = row.get("緯度", "").strip()
                if not (code and lon and lat):
                    continue
                if code not in stations:
                    stations[code] = {
                        "lon": float(lon),
                        "lat": float(lat),
                        "地方整備局等番号": row.get("地方整備局等番号", "").strip(),
                        "都道府県コード": row.get("開発建設部／都道府県コード", "").strip(),
                    }
    return stations


def build_stations_geojson(stations: dict) -> dict:
    """観測点dictをGeoJSON FeatureCollectionに変換"""
    features = []
    for code, info in stations.items():
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [info["lon"], info["lat"]],
            },
            "properties": {
                "観測点コード": int(code),
                "地方整備局等番号": info["地方整備局等番号"],
                "都道府県コード": info["都道府県コード"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def process_traffic_group(group_key: str, group: dict, out_base: Path):
    """
    1グループ（5m または 1h）のCSVを処理し、日別JSONを出力する。

    出力JSON構造:
      { "YYYYMMDDHHM": { "観測点コード": [上小, 上大, 下小, 下大], ... }, ... }
    """
    out_dir = out_base / group["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # 日付ごとに時刻別データを蓄積: {date: {timecode: {code: [values]}}}
    daily: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))

    for ds_id in group["datasets"]:
        ds_dir = DATA_DIR / ds_id
        if not ds_dir.exists():
            log.warning(f"  ディレクトリ未存在: {ds_dir}")
            continue
        files = sorted(ds_dir.glob("*.csv"))
        log.info(f"  処理: {ds_id} ({len(files)}ファイル)")

        for fpath in files:
            for row in iter_csv_rows(fpath):
                code = row.get("常時観測点コード", "").strip()
                timecode = row.get("時間コード", "").strip()
                if not (code and timecode):
                    continue

                # 観測年月日から日付キーを取得
                date = str(row.get("観測年月日", "")).strip()
                if not date:
                    # 時間コードの先頭8桁
                    date = timecode[:8]

                up_s   = safe_int(row.get("上り・小型交通量") or row.get("上り・小型交通量（集計値）", ""))
                up_l   = safe_int(row.get("上り・大型交通量") or row.get("上り・大型交通量（集計値）", ""))
                down_s = safe_int(row.get("下り・小型交通量") or row.get("下り・小型交通量（集計値）", ""))
                down_l = safe_int(row.get("下り・大型交通量") or row.get("下り・大型交通量（集計値）", ""))

                # 欠測は None のままJSONに格納（フロントエンドで判定）
                daily[date][timecode][code] = [up_s, up_l, down_s, down_l]

    # 日別JSONとして書き出し
    ext = ".json.gz" if GZIP_OUTPUT else ".json"
    total_files = 0
    for date, time_data in sorted(daily.items()):
        out_path = out_dir / f"{date}{ext}"
        payload = json.dumps(time_data, ensure_ascii=False, separators=(",", ":"))
        if GZIP_OUTPUT:
            with gzip.open(out_path, "wt", encoding="utf-8") as f:
                f.write(payload)
        else:
            out_path.write_text(payload, encoding="utf-8")
        total_files += 1

    log.info(
        f"  {group['label']}: {total_files}日分を {out_dir} に出力"
    )
    return daily


def main():
    log.info("=== JARTIC 前処理開始 ===")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. 観測点マスタ抽出 ──────────────────────────
    log.info("【1】観測点マスタ抽出")
    all_ds = [ds for g in GROUPS.values() for ds in g["datasets"]]
    stations = extract_stations(all_ds)
    log.info(f"  観測点数: {len(stations):,}")

    geojson = build_stations_geojson(stations)
    out_stations = OUTPUT_DIR / "stations.geojson"
    out_stations.write_text(
        json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"  → {out_stations} ({len(geojson['features']):,}点)")

    # ── 2. 時刻別交通量JSON生成 ───────────────────────
    log.info("【2】時刻別交通量JSON生成")
    for key, group in GROUPS.items():
        log.info(f"  [{key}] {group['label']}")
        daily = process_traffic_group(key, group, OUTPUT_DIR)

        # 5分データのみ: ビューワー用 index.json を生成
        if key == "5m":
            dates_list = sorted(d for d in daily.keys() if d.isdigit() and len(d) == 8)
            out_index = OUTPUT_DIR / "data_5m" / "index.json"
            out_index.write_text(
                json.dumps({"dates": dates_list}, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info(f"  → {out_index} ({len(dates_list)}日分)")

        # 1時間データのみ: 全期間統合ファイルを追加生成
        if key == "1h":
            log.info("  [1h] 全期間統合ファイル生成 (data_1h_all.json.gz)")
            all_timecodes: dict[str, dict] = {}
            for time_data in (daily[d] for d in sorted(daily)):
                all_timecodes.update(time_data)
            out_all = OUTPUT_DIR / "data_1h_all.json.gz"
            payload = json.dumps(all_timecodes, ensure_ascii=False, separators=(",", ":"))
            with gzip.open(out_all, "wt", encoding="utf-8") as f:
                f.write(payload)
            size_mb = out_all.stat().st_size / 1024 / 1024
            log.info(f"  → {out_all} ({size_mb:.1f}MB, {len(all_timecodes):,}タイムステップ)")

    # ── 3. tippecanoe コマンド表示 ───────────────────
    log.info("【3】ベクトルタイル生成コマンド（要 tippecanoe v2.17+）")
    base_opts = (
        f"--name=jartic-stations "
        f"--layer=stations "
        f"-z14 -Z5 "
        f"-r1 "
        f"--force "
        f"{out_stations}"
    )
    cmd_mbtiles = f"tippecanoe -o {OUTPUT_DIR}/stations.mbtiles {base_opts}"
    cmd_pmtiles = f"tippecanoe -o {OUTPUT_DIR}/stations.pmtiles {base_opts}"

    log.info(f"  MBTiles: $ {cmd_mbtiles}")
    log.info(f"  PMTiles: $ {cmd_pmtiles}")
    print(
        f"\ntippecanoe コマンド:\n"
        f"  # MBTiles（QGIS・MapServer等）\n"
        f"  {cmd_mbtiles}\n\n"
        f"  # PMTiles（MapLibre GL JS + CloudFront/R2等）\n"
        f"  {cmd_pmtiles}\n"
    )

    log.info("=== 完了 ===")


if __name__ == "__main__":
    main()
