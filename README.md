# twquote — 台股盤中報價 CLI

在終端機盯台股報價的小工具：吃股票代號、每 5 秒刷新一次表格。
純標準函式庫，不用 `pip install` 任何東西，Python 3.9+ 就能跑。

```
$ python3 -m twquote 2330 2317 ^TAIEX

代號   名稱            成交     漲跌    幅度%      買進      賣出        開        高        低     量(張)      時間
--------------------------------------------------------------------------------------------------------------------
2330   台積電      1,085.00   ▲15.00    +1.40  1,085.00  1,090.00  1,075.00  1,090.00  1,072.00     38,215  13:30:00
2317   鴻海          200.00   ▼10.00    -4.76    200.00    200.50    209.00    209.50    199.50    112,004  13:30:00
t00    加權指數   27,310.55  ▲130.43    +0.48         -         - 27,200.00 27,350.90 27,188.40          -  13:30:00
更新於 13:30:05．每 5 秒．Ctrl-C 離開
```

（上面是示意數字，不是真實行情。）漲紅跌綠（台股慣例），漲跌停會多一個 `✦`。

## 用法

```bash
python3 -m twquote 2330                 # 盯一檔，每 5 秒更新
python3 -m twquote 2330 6488.two ^TAIEX # 多檔 + 大盤
python3 -m twquote --once 2330          # 只抓一次就結束
python3 -m twquote --once --json 2330   # JSON Lines，好接 jq
python3 -m twquote -n 15 2330           # 放慢到 15 秒
```

裝成指令（可選）：`pip install -e .` 之後就能直接打 `twquote 2330`。

代號寫法：

| 寫法 | 意思 |
|---|---|
| `2330` | 自動判斷上市／上櫃（第一次兩邊都問，之後只問命中的那邊） |
| `2330.tw` / `tse:2330` | 指定上市 |
| `6488.two` / `otc:6488` | 指定上櫃 |
| `tse_2330.tw` | 直接給 MIS 原始頻道 |
| `^TAIEX` / `^OTC` | 加權指數／櫃買指數 |

`--once` 找不到代號時回傳 exit code 1，連線或來源錯誤也是 1，代號格式錯誤是 2。
連續模式下抓取失敗不會中斷，畫面會留著上一筆快照並在頁尾標示。

## 資料來源與限制

預設來源是證交所 MIS（基本市況報導）的 `getStockInfo.jsp`，也就是
mis.twse.com.tw 網頁自己在用的那支：

- **大約 5 秒一次快照**，不是逐筆 tick。`-n` 設得比 5 秒小會被自動拉回 5 秒，
  打太快只會被鎖 IP。
- 沒有官方文件、沒有 SLA，端點隨時可能改。
- 試撮時段（09:00 前、13:25–13:30）成交價是 `-`，表格會顯示 `-`，這是正常的。
- **授權**：即時行情自用沒問題，要對外轉發或商用得先向證交所／櫃買申請
  資訊使用授權。

需要真正的逐筆 tick 或完整五檔推播，就得走券商 API（見下）。

## 換掉資料來源

`twquote/sources/base.py` 定義了介面，只要有 `min_interval` 和
`fetch(symbols) -> list[Quote]` 就能接進 CLI：

```python
class ShioajiSource:
    min_interval = 0.0  # WebSocket 推播，不用輪詢

    def __init__(self, api_key, secret_key):
        import shioaji as sj
        self.api = sj.Shioaji()
        self.api.login(api_key=api_key, secret_key=secret_key)
        self.latest = {}  # code -> Quote，由 callback 更新

    def fetch(self, symbols):
        return [self.latest[s] for s in symbols if s in self.latest]
```

然後 `main(argv, source=ShioajiSource(...))`。`Quote` 是共用結構，
表格與 JSON 輸出都不用改。

## 開發

```bash
python3 -m unittest discover -s tests -t .
```

測試全部離線（用假的 opener 餵 MIS 樣本 JSON），不會打到交易所。

## 專案結構

```
twquote/
  models.py          # Quote：跨來源共用的報價結構
  render.py          # 終端機表格（含全形字寬處理、漲跌上色）
  cli.py             # argparse 與輪詢迴圈
  sources/
    base.py          # QuoteSource 介面
    mis.py           # 證交所 MIS 實作：代號正規化、解析、市場別快取
tests/               # 離線單元測試
```
