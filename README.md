# Welcome to GitHub Desktop!

This is your README. READMEs are where you can communicate what your project is and how to use it.

Write your name on line 6, save it, and then head back to GitHub Desktop.

## 即時比特幣價格 (btc_price.py)

透過 Binance WebSocket 串流即時成交價，價格一變動就會顯示（漲綠 ▲ / 跌紅 ▼），斷線會自動重連。

```bash
pip install websockets
python btc_price.py            # BTC/USDT
python btc_price.py ethusdt    # 其他 Binance 交易對
```

按 Ctrl+C 停止。
