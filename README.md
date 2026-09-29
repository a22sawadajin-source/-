# BTC_USDT 先物バックテスター (spec v0.3)

売買機能なし・バックテスト専用。注文API・APIキー・実口座接続は含まない。仕様は `spec-v0.3.md`。

## 使い方
```
python3 -m unittest discover -s tests -t .                       # テスト
python3 -m btbt.fetch_mexc --start 2022-01-01 --out data         # 公開データ取得(読み取り専用)
# data/raw/contract_detail.json を確認 → config/v0.3.json の contract を実値にして verified=true
# データ範囲を確認 → config/v0.3.json の periods(dev/mid/test) を人が固定
python3 -m btbt.cli run --period dev                             # 開発期間(参考値)
python3 -m btbt.cli run --period mid
python3 -m btbt.cli freeze                                       # 仕様・設定・コード・データのハッシュ固定
python3 -m btbt.cli run --period test                            # 未使用期間: 1回のみ(ロックファイル)
```
CSV形式: `data/bars_1h.csv` = time(epoch秒 or ISO),open,high,low,close,volume / `data/funding.csv` = time,rate。

## 状態
- MEXCの実データ・契約仕様・手数料は**未確認**(取得器は未検証の仮定に基づく。生JSONとSHA-256を保存)。
- `config/v0.3.json` の ASSUMPTION 値と `verified:false` の間は、未使用期間でも「合格」は出ず「判定保留」になる。
- `scripts/make_synthetic.py` はパイプライン確認用の乱数データ。その結果に意味はない。

## 仕様の曖昧点に対する実装上の保守的な仮定
- 損切りは足の高値/安値で判定し、参入足から有効。窓越えは不利な始値で約定。参入足での損切りは `same_bar_entry_stop` に件数記録。
- 数量は参入予定の始値+スリッページ後の価格で、往復手数料と退出スリッページ込みのリスクから算出。
- ファンディングは「参入時刻より後に迎えた精算時刻」で保有中なら請求。履歴が期間を覆わない取引には `funding_data_missing` を付ける(0で補わない)。
- 損切り足・退出足では新規参入判断をしない。欠損足をまたぐポジションは欠損直前の終値で強制決済(`data_gap`)。
- 最低取引数30・清算までの余裕50%・維持証拠金率0.5%は仕様に数値がないためのASSUMPTION。
