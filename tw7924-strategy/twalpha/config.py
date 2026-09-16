"""集中管理所有可調參數: 交易成本、部位規模、風控。"""

from dataclasses import dataclass, field, asdict

# 台股預設標的
DEFAULT_STOCK = "7924"


@dataclass
class CostModel:
    """台股實際交易成本。

    手續費: 成交金額 * 0.1425% * 券商折扣, 且有單筆最低收費 (多數券商 20 元,
    部分電子折扣券商零股/整股最低 1 元)。對小資金而言, 最低手續費才是真正的
    隱形殺手 -- 一筆 5,000 元的買賣, 20 元最低收費等於 0.4% 的成本。

    證交稅: 賣出時課 0.3% (當沖賣出 0.15%)。
    """

    fee_rate: float = 0.001425
    fee_discount: float = 0.6      # 6 折, 常見電子下單折扣
    min_fee: float = 20.0          # 單筆最低手續費, 折扣券商可設 1
    tax_rate: float = 0.003        # 賣出證交稅
    day_trade_tax_rate: float = 0.0015
    slippage_bps: float = 10.0     # 滑價 (萬分之 10 = 0.1%), 小型股請調高

    def buy_fee(self, amount: float) -> float:
        return max(self.min_fee, amount * self.fee_rate * self.fee_discount)

    def sell_fee(self, amount: float) -> float:
        return max(self.min_fee, amount * self.fee_rate * self.fee_discount)

    def sell_tax(self, amount: float, day_trade: bool = False) -> float:
        rate = self.day_trade_tax_rate if day_trade else self.tax_rate
        return amount * rate

    def slippage(self, price: float) -> float:
        return price * self.slippage_bps / 10000.0

    def round_trip_cost(self, amount: float) -> float:
        """一買一賣的總成本 (元)。"""
        return self.buy_fee(amount) + self.sell_fee(amount) + self.sell_tax(amount)

    def breakeven_pct(self, amount: float) -> float:
        """這筆交易要漲多少 % 才打平 (含滑價)。"""
        if amount <= 0:
            return float("inf")
        slip = 2 * amount * self.slippage_bps / 10000.0
        return (self.round_trip_cost(amount) + slip) / amount * 100.0


@dataclass
class RiskConfig:
    """部位規模與風控。小資金的核心不是「賺最快」, 而是「不被單筆打死」。"""

    initial_capital: float = 100_000.0
    sizing: str = "risk"        # risk | full | fraction
    risk_pct: float = 0.02      # 單筆最多虧掉總資金的 2%
    fraction: float = 0.5       # sizing=fraction 時投入的資金比例
    max_position_pct: float = 1.0   # 單一部位最多佔總資金比例
    lot_mode: str = "odd"       # odd = 零股(1股為單位), round = 整股(1000股)
    stop_atr_mult: float = 2.0  # 初始停損 = 進場價 - N * ATR
    trail_atr_mult: float = 3.0 # 移動停利 = 收盤 - N * ATR (0 表示不啟用)
    take_profit_r: float = 0.0  # 獲利了結 = N 倍風險 (0 表示不啟用)
    max_hold_days: int = 0      # 時間停損 (0 表示不啟用)

    @property
    def lot_size(self) -> int:
        return 1000 if self.lot_mode == "round" else 1


@dataclass
class BacktestConfig:
    stock: str = DEFAULT_STOCK
    costs: CostModel = field(default_factory=CostModel)
    risk: RiskConfig = field(default_factory=RiskConfig)
    start: str = ""   # YYYY-MM-DD, 空字串表示不限制
    end: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
