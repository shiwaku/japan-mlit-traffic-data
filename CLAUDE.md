# JARTIC オープン交通データ

## プロジェクト概要

国土交通省が提供するJARTICオープン交通データを一括取得するプロジェクト。
全国2,060ヶ所の一般国道（道路種別=3）の交通量データをAPIで取得・保存する。

- ガイダンスサイト: https://www.jartic-open-traffic.org/
- API仕様書: https://www.jartic-open-traffic.org/action_method.pdf
- 利用の手引き: https://www.jartic-open-traffic.org/国土交通省交通量API仕様書（APIリクエストの作成方法）.pdf

## API仕様

### エンドポイント

```
GET https://api.jartic-open-traffic.org/geoserver
```

- 認証: 不要（HTTPS接続のみ）
- プロトコル: WFS 2.0.0
- 道路種別: `3`（一般国道）のみ取得可能 ※`1`（高速）は対象外

### 固定パラメータ

```
service=WFS
version=2.0.0
request=GetFeature
srsName=EPSG:4326
outputFormat=csv          # or application/json
exceptions=application/json
```

### 4種類のデータ（typeNames）

| 様式 | typeNames | 間隔 | 提供期間 | バッチ推奨サイズ |
|------|-----------|------|---------|----------------|
| 1 | `t_travospublic_measure_5m`     | 5分  | 過去1ヶ月 | 2時間 (~4MB) |
| 2 | `t_travospublic_measure_1h`     | 1時間 | 過去3ヶ月 | 1日 (~3.7MB) |
| 3 | `t_travospublic_measure_5m_img` | 5分  | 過去1ヶ月 | 2時間 (~4MB) |
| 4 | `t_travospublic_measure_1h_img` | 1時間 | 過去3ヶ月 | 1日 (~3.2MB) |

※ レスポンスペイロード上限: 約6MB
※ 近畿地方整備局のCCTVトラカンは様式3のみ（様式4なし）

### CQLフィルタ（cql_filter）

**重要: フィールド名はダブルクォートで囲まない**

```
道路種別=3 AND 時間コード>=202603270000 AND 時間コード<=202603272300 AND BBOX(ジオメトリ,122.0,24.0,154.0,46.0,'EPSG:4326')
```

- 時間コード形式: `YYYYMMDDhhmm`
  - 様式1,3（5分間）: mmは5の倍数
  - 様式2,4（1時間）: mmは00固定
- 日本全国BBOX: `122.0,24.0,154.0,46.0`（minX,minY,maxX,maxY）
- フィールド名にクォートを付けると400エラーになる（API仕様書のサンプルは誤記）

### レスポンス形式（CSV）

- 外側をダブルクォート`"`で囲んだ単一行文字列
- 行区切りは実際の改行文字ではなく、リテラル`\r\n`（4文字のエスケープシーケンス）
- パース方法:
  ```python
  inner = raw_bytes.decode("utf-8").strip('"')
  csv_text = inner.replace("\\r\\n", "\n")
  ```

### CSVの主要カラム

**様式1, 2（常設トラカン）**

```
FID, 地方整備局等番号, 開発建設部／都道府県コード, 常時観測点コード,
収集時間フラグ（5分間／1時間）, 観測年月日, 時間帯,
上り・小型交通量, 上り・大型交通量, 上り・車種判別不能交通量,
上り・停電, 上り・ループ異常, 上り・超音波異常, 上り・欠測,
下り・小型交通量, 下り・大型交通量, 下り・車種判別不能交通量,
下り・停電, 下り・ループ異常, 下り・超音波異常, 下り・欠測,
道路種別, 時間コード, 経度, 緯度
```

※ APIレスポンスの `ジオメトリ`（`MULTIPOINT ((lon lat))` 形式）は
　 保存時に `経度`・`緯度` の2列に分割する。座標系はWGS84（EPSG:4326）。

**様式3, 4（CCTVトラカン）**
上記に加えて: カメラプリセット位置, 気象影響による映像不良, 照度不足,
突発事象（交通事故等）, サーバの稼働, カメラの映像受信 など品質フラグが追加される

**注意: 様式1/2 と 様式3/4 でカラム名が異なる**

| 様式1/2 | 様式3/4 |
|--------|--------|
| `上り・小型交通量` | `上り・小型交通量（集計値）` |
| `上り・大型交通量` | `上り・大型交通量（集計値）` |
| `下り・小型交通量` | `下り・小型交通量（集計値）` |
| `下り・大型交通量` | `下り・大型交通量（集計値）` |

process_csv.py では両方のカラム名を `row.get("上り・小型交通量", row.get("上り・小型交通量（集計値）", ""))` の形で参照すること。

## ファイル構成

```
japan-mlit-traffic-data/
├── CLAUDE.md
├── .github/
│   └── workflows/
│       └── update-data.yml     # 毎日 11:00 JST に自動実行
├── scripts/
│   ├── download_jartic.py      # 一括ダウンロードスクリプト
│   └── process_csv.py          # CSV → GeoJSON + 時刻別JSON生成
├── data/                       # 生CSV（gitignore対象）
│   ├── shoshiki1/              # 様式1: 常設トラカン 5分間
│   │   └── YYYYMMDD_HHMM.csv
│   ├── shoshiki2/              # 様式2: 常設トラカン 1時間
│   │   └── YYYYMMDD.csv
│   ├── shoshiki3/              # 様式3: CCTVトラカン 5分間
│   │   └── YYYYMMDD_HHMM.csv
│   └── shoshiki4/              # 様式4: CCTVトラカン 1時間
│       └── YYYYMMDD.csv
├── docs/                       # GitHub Pages + S3配信ファイル
│   ├── index.html              # ビューワー
│   ├── pale.json               # 地図スタイル（国土地理院）
│   ├── assets/                 # Viteビルド成果物
│   ├── stations.pmtiles        # 観測点ベクトルタイル → S3配信（gitignore）
│   ├── stations.geojson        # 観測点マスタ（gitignore）
│   ├── data_5m/                # 5分間交通量 → S3配信（gitignore）
│   │   └── YYYYMMDD.json.gz
│   └── data_1h_all.json.gz     # 1時間交通量 全期間統合 → S3配信（gitignore）
└── viewer/                     # Viteプロジェクト（ソース）
    └── src/main.ts
```

## 時刻別JSON構造

```json
{
  "202603270000": {
    "1110010": [上り小型, 上り大型, 下り小型, 下り大型],
    "1110020": [219, 96, 186, 147]
  },
  "202603270100": { ... }
}
```

欠測は `null`。配列インデックス: 0=上り小型, 1=上り大型, 2=下り小型, 3=下り大型。

## ビューワー設計方針

タイトル: **国土交通省 交通量ビューワー（JARTIC提供）**（`<h1>` は「（JARTIC提供）」を `<br>` で改行）

出典表記: MapLibre の `customAttribution` に以下を設定（地図右下のアトリビューション欄に表示）

```
出典：<a href="https://www.jartic-open-traffic.org/">国土交通省 JARTIC オープン交通データ</a>（参考値）
```

### 5分間モード

- スライダー範囲: **1日分**（288コマ）
- 日付選択: プルダウン（年月日）
- データ: `data_5m/YYYYMMDD.json.gz` を日付変更時にフェッチ（~2.6MB）
- 前後日プリフェッチで体感遅延ほぼゼロ

### 1時間モード

- スライダー範囲: **3ヶ月分**（2,184コマ）
- データ: `data_1h_all.json.gz` をページロード時に一括フェッチ（29.6MB）
- ロード完了後、スライダーが全期間を即時スクラブ可能

### 共通アーキテクチャ

- `stations.pmtiles` でジオメトリ（位置）を描画
- 時刻別JSONから `観測点コード` をキーに交通量を取得
- MapLibre GL JS の `setPaintProperty` で色のみ更新（ジオメトリ再描画なし）

## ダウンロード実行

```bash
python scripts/download_jartic.py
python scripts/process_csv.py
```

- リクエスト数: 926回（様式1: 372, 様式2: 91, 様式3: 372, 様式4: 91）
- 所要時間: 約8分（8並列）
- ディスク使用量: 約3.1GB（shoshiki1: 1.3G, shoshiki2: 298M, shoshiki3: 1.3G, shoshiki4: 243M）
- 再実行時は既存ファイルをスキップ（レジューム対応）

## 注意事項

- 利用前に「交通量API利用規約」への同意が必要
- **本データは参考値。** 国土交通省による正式調査結果ではない
- データ精度は保証されない（気象・機器障害等で欠測あり）。欠測の場合は該当フィールドが空欄
- APIレスポンスのCSV・GeoJSON等をそのままWeb上に再公開することは制限されている
- 出典表記には「参考値」である旨を含めること
