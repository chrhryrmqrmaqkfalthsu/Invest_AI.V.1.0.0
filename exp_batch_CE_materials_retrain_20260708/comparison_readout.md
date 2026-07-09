# CE materials isolated retrain comparison

Stage2 판정: `SECTOR_MATTERS_MUCH`

Stage3 판정: `SECTOR_MATTERS_LITTLE`

CSV: `/home/g3000kkw/kingmaker/exp_batch_CE_materials_retrain_20260708/comparison.csv`

## Rows

```json
[
  {
    "MAE_distribution": {
      "count": 5942,
      "max": -0.012362816119377087,
      "mean": -4.742969172119325,
      "median": -3.3767796246731785,
      "min": -32.110713198416896,
      "p10": -10.401251622501958,
      "p90": -0.34940794038632333
    },
    "MDD": null,
    "MFE_distribution": {
      "count": 5657,
      "max": 27.36566320904187,
      "mean": 5.070751693313321,
      "median": 4.783513703140395,
      "min": 0.07157107658551091,
      "p10": 0.9176126698590364,
      "p90": 9.678037324584974
    },
    "expectancy": null,
    "label": "old",
    "max_holding_days": 22,
    "row_found": true,
    "rulebook_hash": "f5b4bc76f5dbe3c9559c9bac9d8d61c7f3bcc348fb3db74595b948e2e2298a81",
    "sector_name": "tech",
    "sector_strength_weight": 0.7395721693608839,
    "signal_threshold": 2.563611845834524,
    "stage": "stage2",
    "stage_dir": "/home/g3000kkw/kingmaker/exp_batch_stage123_2009_20260616_full/tickers/CE/stage2",
    "stop_loss_atr": 1.0652314199714366,
    "trade_count": "",
    "trailing_atr": 3.0,
    "use_market_entry_adjustment": false,
    "win_rate": ""
  },
  {
    "MAE_distribution": {
      "count": 4735,
      "max": -0.012362816119377087,
      "mean": -4.129877302091678,
      "median": -3.3711516356086566,
      "min": -17.694578767495862,
      "p10": -9.375831406731361,
      "p90": -0.7086695242160024
    },
    "MDD": null,
    "MFE_distribution": {
      "count": 4492,
      "max": 18.295581935536365,
      "mean": 4.379397498002509,
      "median": 4.209758667753268,
      "min": 0.12355416343484343,
      "p10": 0.8416026586608462,
      "p90": 7.433813834998411
    },
    "expectancy": null,
    "label": "new_materials",
    "max_holding_days": 30,
    "row_found": true,
    "rulebook_hash": "81cfbc6b444ab753209eee5889aaf4665ac1ecaa71d8ea6bdd0799830b0f6c09",
    "sector_name": "materials",
    "sector_strength_weight": -0.14607800639599866,
    "signal_threshold": 3.1384953547016305,
    "stage": "stage2",
    "stage_dir": "/home/g3000kkw/kingmaker/exp_batch_CE_materials_retrain_20260708/tickers/CE/stage2",
    "stop_loss_atr": 1.4826903947600663,
    "trade_count": "",
    "trailing_atr": 2.3885061843390467,
    "use_market_entry_adjustment": false,
    "win_rate": ""
  },
  {
    "MAE_distribution": {
      "count": 3749,
      "max": -0.012362816119377087,
      "mean": -5.253368518122301,
      "median": -4.976045069577566,
      "min": -20.966099884550452,
      "p10": -10.431050824167514,
      "p90": -0.8674579799340791
    },
    "MDD": -5.594310237552785,
    "MFE_distribution": {
      "count": 3463,
      "max": 23.401906758030396,
      "mean": 6.3517663566679,
      "median": 5.917598424332865,
      "min": 0.06020961081885662,
      "p10": 1.035281560020101,
      "p90": 11.658206269004303
    },
    "expectancy": 5.706243224828521,
    "label": "old",
    "max_holding_days": 22,
    "row_found": true,
    "rulebook_hash": "12fbd9799087bfe58a32393885cb0882cb29d72c2bac0f912b9782df4688eab1",
    "sector_name": "tech",
    "sector_strength_weight": -0.9225032985981738,
    "signal_threshold": 3.4032056095181815,
    "stage": "stage3",
    "stage_dir": "/home/g3000kkw/kingmaker/exp_batch_stage123_2009_20260616_full/tickers/CE/stage3",
    "stop_loss_atr": 1.822337886543205,
    "trade_count": 8,
    "trailing_atr": 1.4707344742120305,
    "use_market_entry_adjustment": false,
    "win_rate": 62.5
  },
  {
    "MAE_distribution": {
      "count": 3888,
      "max": -0.012362816119377087,
      "mean": -4.473160296451777,
      "median": -3.7969677138435487,
      "min": -18.918444741710626,
      "p10": -9.224805231122843,
      "p90": -0.5621920328852114
    },
    "MDD": -4.421969198482005,
    "MFE_distribution": {
      "count": 3716,
      "max": 37.180932718029936,
      "mean": 5.983060340561074,
      "median": 5.126259458523006,
      "min": 0.06020961081885662,
      "p10": 0.9848693754061648,
      "p90": 11.971970872667544
    },
    "expectancy": 7.606588528112571,
    "label": "new_materials",
    "max_holding_days": 26,
    "row_found": true,
    "rulebook_hash": "e524fb7e4f8ccf210f2170b4bc47f6d0dd6dfcd79c678690cbc1cf38ee2a338e",
    "sector_name": "materials",
    "sector_strength_weight": -0.9139515340986065,
    "signal_threshold": 2.9803387829516486,
    "stage": "stage3",
    "stage_dir": "/home/g3000kkw/kingmaker/exp_batch_CE_materials_retrain_20260708/tickers/CE/stage3",
    "stop_loss_atr": 1.4493545222984063,
    "trade_count": 9,
    "trailing_atr": 1.4278271787544827,
    "use_market_entry_adjustment": false,
    "win_rate": 66.66666666666666
  }
]
```
