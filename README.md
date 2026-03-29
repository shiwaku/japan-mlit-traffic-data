# japan-mlit-traffic-data

国土交通省が提供する [JARTIC オープン交通データ](https://www.jartic-open-traffic.org/) を一括取得するスクリプト。

全国2,060ヶ所の一般国道（直轄国道）における方向別交通量データをWFS APIで取得し、CSVとして保存する。

## データ概要

| 様式 | 観測方法 | 集計単位 | 提供期間 | layer名 |
|------|---------|---------|---------|---------|
| 様式1 | 常設トラカン | 5分間 | 過去1ヶ月 | `t_travospublic_measure_5m` |
| 様式2 | 常設トラカン | 1時間 | 過去3ヶ月 | `t_travospublic_measure_1h` |
| 様式3 | CCTVトラカン | 5分間 | 過去1ヶ月 | `t_travospublic_measure_5m_img` |
| 様式4 | CCTVトラカン | 1時間 | 過去3ヶ月 | `t_travospublic_measure_1h_img` |

- 道路種別: 一般国道（コード `3`）のみ
- 座標系: WGS84（EPSG:4326）
- 利用前に [交通量API利用規約](https://www.jartic-open-traffic.org/) への同意が必要

## 処理フロー

```mermaid
flowchart TD
    A([開始]) --> B[実行時刻をJSTで取得]
    B --> C[各様式のバッチタスクを生成]

    C --> C1["様式1: 372バッチ<br>(2時間×12/日×31日)"]
    C --> C2["様式2: 91バッチ<br>(1日×91日)"]
    C --> C3["様式3: 372バッチ<br>(2時間×12/日×31日)"]
    C --> C4["様式4: 91バッチ<br>(1日×91日)"]

    C1 & C2 & C3 & C4 --> D["全926タスクをラウンドロビン順に並べる<br>→ 様式1,2,3,4,1,2,3,4..."]

    D --> E["ThreadPoolExecutor<br>(MAX_WORKERS=8)<br>で全タスクを並列実行"]

    E --> F{ファイル<br>既存?}
    F -- あり --> G[スキップ]
    F -- なし --> H["WFS API GET リクエスト<br>https://api.jartic-open-traffic.org/geoserver"]

    H --> I{レスポンス}
    I -- 200 OK --> J["レスポンスをパース<br>・外側クォート除去<br>・\\r\\n → 改行<br>・MULTIPOINT((lon lat)) → 経度,緯度"]
    I -- エラー --> K{リトライ<br>残あり?}
    K -- Yes --> H
    K -- No --> L[エラーログ記録]

    J --> M{データ<br>あり?}
    M -- なし --> N[空データとして記録]
    M -- あり --> O["CSVとして保存<br>data/shoshikiN/YYYYMMDD.csv<br>data/shoshikiN/YYYYMMDD_HHMM.csv"]

    G & L & N & O --> P{全タスク<br>完了?}
    P -- No --> E
    P -- Yes --> Q[完了サマリーをログ出力]
    Q --> R([終了])
```

## 出力ファイル

```
data/
├── shoshiki1/          # 様式1: 常設トラカン 5分間
│   ├── 20260101_0000.csv   # 00:00〜01:55 のデータ
│   ├── 20260101_0200.csv   # 02:00〜03:55 のデータ
│   └── ...                 # 2時間単位、12ファイル/日
├── shoshiki2/          # 様式2: 常設トラカン 1時間
│   ├── 20251227.csv        # 1日分のデータ
│   └── ...                 # 1ファイル/日
├── shoshiki3/          # 様式3: CCTVトラカン 5分間
│   └── ...                 # 様式1と同形式
└── shoshiki4/          # 様式4: CCTVトラカン 1時間
    └── ...                 # 様式2と同形式
```

### CSVカラム（様式1, 2）

```
FID, 地方整備局等番号, 開発建設部／都道府県コード, 常時観測点コード,
収集時間フラグ（5分間／1時間）, 観測年月日, 時間帯,
上り・小型交通量, 上り・大型交通量, 上り・車種判別不能交通量,
上り・停電, 上り・ループ異常, 上り・超音波異常, 上り・欠測,
下り・小型交通量, 下り・大型交通量, 下り・車種判別不能交通量,
下り・停電, 下り・ループ異常, 下り・超音波異常, 下り・欠測,
道路種別, 時間コード, 経度, 緯度
```

- `経度` / `緯度`: WGS84（EPSG:4326）。QGISやGeoJSONへの変換が容易
- 欠測の場合は該当フィールドが空欄
- 様式3, 4はカメラ品質フラグ列が追加される（カラム名も異なる: `上り・小型交通量（集計値）` など）

## 必要環境

- Python 3.10+（標準ライブラリのみ使用）

## 使い方

### 1. データダウンロード

```bash
python scripts/download_jartic.py
```

### 2. 前処理（観測点GeoJSON + 時刻別JSON生成）

```bash
python scripts/process_csv.py
```

出力先: `docs/`

| ファイル | 内容 | サイズ |
|---------|------|--------|
| `docs/stations.geojson` | 観測点マスタ (2,060点) | 656KB |
| `docs/data_5m/YYYYMMDD.json.gz` | 5分間交通量・日別 (様式1+3) | ~2.6MB/日 |
| `docs/data_1h_all.json.gz` | 1時間交通量・全期間統合 (91日・2,184ステップ) | 29.6MB |

### 3. ベクトルタイル生成（tippecanoe v2.17+ が必要）

```bash
# PMTiles（MapLibre GL JS + S3等での利用）
tippecanoe \
  -o docs/stations.pmtiles \
  --name=jartic-stations \
  --layer=stations \
  -z14 -Z5 \
  -r1 \
  --force \
  docs/stations.geojson
```

tippecanoe のインストール: https://github.com/felt/tippecanoe

### 設定（`scripts/download_jartic.py` 冒頭）

| 設定項目 | デフォルト | 説明 |
|---------|-----------|------|
| `MAX_WORKERS` | `8` | 並列ワーカー数（コア数に合わせて調整） |
| `MAX_RETRIES` | `3` | APIエラー時のリトライ回数 |
| `RETRY_DELAY_SEC` | `10` | リトライ間隔（秒） |
| `DATASETS[*].enabled` | `True` | 取得しない様式は `False` に設定 |

### 推計スペック（全様式取得時）

| 項目 | 値 |
|-----|---|
| 総リクエスト数 | 926回 |
| 所要時間 | 約8分（8並列） |
| ディスク使用量 | 約3.1GB |

### 再実行・レジューム

既存ファイルは自動でスキップされる。中断後の再実行でも途中から継続可能。

## S3配信設定

データファイルは `s3://pmtiles-data/mlit/traffic-data/` に格納し、GitHub Pagesからは除外しています。

### バケットポリシー（Refererによるアクセス制限）

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowReferer",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::pmtiles-data/mlit/traffic-data/*",
      "Condition": {
        "StringLike": {
          "aws:Referer": [
            "https://shiwaku.github.io/*",
            "http://localhost:*/*"
          ]
        }
      }
    }
  ]
}
```

### CORSポリシー

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": [
      "https://shiwaku.github.io",
      "http://localhost:5173"
    ],
    "ExposeHeaders": ["Content-Length", "Content-Range", "Accept-Ranges"],
    "MaxAgeSeconds": 3600
  }
]
```

> **注意:** Refererヘッダーは偽装可能なため、完全なアクセス制御にはなりません。悪意ある大量取得の抑止と、gitへのデータ非掲載が主な目的です。

### S3へのアップロード（手動）

```bash
aws s3 sync docs/data_5m/ s3://pmtiles-data/mlit/traffic-data/data_5m/ --size-only
aws s3 cp docs/data_1h_all.json.gz s3://pmtiles-data/mlit/traffic-data/data_1h_all.json.gz
aws s3 cp docs/stations.pmtiles s3://pmtiles-data/mlit/traffic-data/stations.pmtiles
```

## 自動更新（GitHub Actions）

`.github/workflows/update-data.yml` により毎日 **11:00 JST** に自動実行されます。

| ステップ | 内容 |
|---------|------|
| CSVキャッシュ復元 | 前日の `data/` を再利用し差分ダウンロードのみ実施 |
| `download_jartic.py` | 新しいCSVのみ取得（既存ファイルはスキップ） |
| `process_csv.py` | 全データをJSON.gzに変換 |
| `aws s3 sync` | `data_5m/` の差分のみアップロード |
| `aws s3 cp` | `data_1h_all.json.gz` を毎日上書き |

### 必要なGitHub Secrets

| Secret名 | 内容 |
|---------|------|
| `AWS_ACCESS_KEY_ID` | IAMアクセスキーID |
| `AWS_SECRET_ACCESS_KEY` | IAMシークレットアクセスキー |

## ビューワー設計方針（MapLibre GL JS）

タイトル: **国土交通省 交通量ビューワー（JARTIC提供）**

| モード | スライダー範囲 | 日付選択 | データ |
|--------|-------------|---------|--------|
| 5分間 | 1日分（288コマ） | プルダウン（年月日） | `data_5m/YYYYMMDD.json.gz` を日付変更時フェッチ |
| 1時間 | 3ヶ月分（2,184コマ） | なし（スライダーで全期間） | `data_1h_all.json.gz` をページロード時一括フェッチ |

- `stations.pmtiles` で観測点位置を描画
- 時刻別JSONの `観測点コード` をキーに交通量を紐付け
- `setPaintProperty` で色のみ更新（ジオメトリ再描画なし）

## QGISでの利用

### CSVを直接表示

1. レイヤ → レイヤを追加 → テキストの区切り文字ファイルレイヤを追加
2. X フィールド: `経度`、Y フィールド: `緯度` を指定
3. 座標系: EPSG:4326 を選択

### ベクタータイル（MBTiles）を表示

1. レイヤ → レイヤを追加 → **ベクタータイルレイヤを追加**
2. ソース: ファイル → `stations.mbtiles` を指定

## 利用条件・出典表記

本スクリプトで取得するデータは **交通量データ（国土交通省）** です。JARTICオープン交通データとして公開されており、利用前に [交通量API利用規約](https://www.jartic-open-traffic.org/) への同意が必要です。

### 出典表記（必須）

データを地図・レポート・Webアプリ等で使用する場合は、以下に準じた出典を明記してください。

```
出典：国土交通省 交通量データ（JARTIC提供）（参考値）
```

- **「参考値」の明示が必要です。** 本データは正式な交通量調査結果ではなく、機器の状態や気象等により欠測・異常値が含まれる可能性があります。
- 分析結果・可視化結果を公開する際も、出典と参考値である旨を併記してください。

### 二次配布・再公開の制限

- APIレスポンスのCSV・GeoJSON・PMTiles等を**そのままWeb上に再公開することは制限されています。**
- `output/` 以下のファイルは自己利用・内部共有の範囲にとどめ、不特定多数への再配布は行わないでください。

## 注意事項

- **本データは参考値です。** 国土交通省による正式な交通量調査結果ではありません。
- 欠測・異常値が含まれる可能性があります（気象・停電・機器障害等が原因）。GIS分析では外れ値処理や欠測補完の設計を前提としてください。
- 近畿地方整備局のCCTVトラカンは様式3のみ提供（様式4なし）
- APIレスポンスの上限は約6MB（バッチサイズはこれを考慮して設定済み）

## 参考資料

- [JARTIC オープン交通データ ガイダンスサイト](https://www.jartic-open-traffic.org/)
- [交通量API仕様書（APIリクエストの作成方法）](https://www.jartic-open-traffic.org/action_method.pdf)
- [交通量データ利用の手引き](https://www.jartic-open-traffic.org/%E5%9B%BD%E5%9C%9F%E4%BA%A4%E9%80%9A%E7%9C%81%E4%BA%A4%E9%80%9A%E9%87%8FAPI%E4%BB%95%E6%A7%98%E6%9B%B8%EF%BC%88API%E3%83%AA%E3%82%AF%E3%82%A8%E3%82%B9%E3%83%88%E3%81%AE%E4%BD%9C%E6%88%90%E6%96%B9%E6%B3%95%EF%BC%89.pdf)
