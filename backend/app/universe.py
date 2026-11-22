"""Tradable commodity futures universe and contract specifications."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Contract:
    symbol: str            # root symbol, e.g. "CL"
    name: str
    sector: str            # Energy / Metals / Grains / Softs / Livestock
    exchange: str
    tick_size: float       # minimum price increment
    point_value: float     # USD per 1.0 price move per contract
    yahoo: str             # Yahoo Finance continuous symbol (live price adapter)
    cftc_code: str         # CFTC contract market code (COT reports)
    sim_price: float       # starting level for the simulator
    sim_vol: float         # annualised volatility for the simulator
    season_peak_month: int  # month where seasonal strength peaks (simulator + priors)
    eia_series: str = ""   # EIA weekly inventory series id, if any
    parent: str = ""       # micro contracts share data with their parent

    @property
    def tick_value(self) -> float:
        return round(self.tick_size * self.point_value, 4)

    @property
    def data_symbol(self) -> str:
        return self.parent or self.symbol

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tick_value"] = self.tick_value
        return d


CONTRACTS: list[Contract] = [
    # Energy
    Contract("CL", "WTI Crude Oil", "Energy", "NYMEX", 0.01, 1000, "CL=F", "067651", 68.0, 0.34, 6, "PET.WCESTUS1.W"),
    Contract("NG", "Henry Hub Natural Gas", "Energy", "NYMEX", 0.001, 10000, "NG=F", "023651", 3.40, 0.60, 1, "NG.NW2_EPG0_SWO_R48_BCF.W"),
    Contract("RB", "RBOB Gasoline", "Energy", "NYMEX", 0.0001, 42000, "RB=F", "111659", 2.05, 0.36, 4, "PET.WGTSTUS1.W"),
    Contract("HO", "NY Harbor ULSD", "Energy", "NYMEX", 0.0001, 42000, "HO=F", "022651", 2.30, 0.33, 11, "PET.WDISTUS1.W"),
    # Metals
    Contract("GC", "Gold", "Metals", "COMEX", 0.1, 100, "GC=F", "088691", 4100.0, 0.18, 1),
    Contract("SI", "Silver", "Metals", "COMEX", 0.005, 5000, "SI=F", "084691", 48.0, 0.30, 2),
    Contract("HG", "Copper", "Metals", "COMEX", 0.0005, 25000, "HG=F", "085692", 5.00, 0.24, 3),
    Contract("PL", "Platinum", "Metals", "NYMEX", 0.1, 50, "PL=F", "076651", 1500.0, 0.28, 2),
    # Grains (prices in cents/bu)
    Contract("ZC", "Corn", "Grains", "CBOT", 0.25, 50, "ZC=F", "002602", 440.0, 0.22, 6),
    Contract("ZS", "Soybeans", "Grains", "CBOT", 0.25, 50, "ZS=F", "005602", 1080.0, 0.18, 7),
    Contract("ZW", "Chicago SRW Wheat", "Grains", "CBOT", 0.25, 50, "ZW=F", "001602", 540.0, 0.28, 5),
    # Softs
    Contract("KC", "Coffee C", "Softs", "ICE US", 0.05, 375, "KC=F", "083731", 380.0, 0.36, 9),
    Contract("SB", "Sugar No. 11", "Softs", "ICE US", 0.01, 1120, "SB=F", "080732", 15.5, 0.30, 3),
    Contract("CC", "Cocoa", "Softs", "ICE US", 1.0, 10, "CC=F", "073732", 6000.0, 0.45, 10),
    Contract("CT", "Cotton No. 2", "Softs", "ICE US", 0.01, 500, "CT=F", "033661", 66.0, 0.24, 7),
    # Livestock
    Contract("LE", "Live Cattle", "Livestock", "CME", 0.025, 400, "LE=F", "057642", 230.0, 0.16, 3),
    Contract("HE", "Lean Hogs", "Livestock", "CME", 0.025, 400, "HE=F", "054642", 85.0, 0.28, 6),
    # Micros (popular with prop accounts) share data with the parent
    Contract("MCL", "Micro WTI Crude", "Energy", "NYMEX", 0.01, 100, "MCL=F", "067651", 68.0, 0.34, 6, parent="CL"),
    Contract("MGC", "Micro Gold", "Metals", "COMEX", 0.1, 10, "MGC=F", "088691", 4100.0, 0.18, 1, parent="GC"),
]

BY_SYMBOL: dict[str, Contract] = {c.symbol: c for c in CONTRACTS}
DATA_SYMBOLS: list[str] = [c.symbol for c in CONTRACTS if not c.parent]


def get(symbol: str) -> Contract:
    try:
        return BY_SYMBOL[symbol.upper()]
    except KeyError as exc:
        raise KeyError(f"Unknown symbol {symbol!r}") from exc
