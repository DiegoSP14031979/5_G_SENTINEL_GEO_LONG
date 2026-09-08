import os
import json
import logging
from datetime import datetime
import pytz
import pandas as pd
import numpy as np
import ta
import MetaTrader5 as mt5

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class GeoLongEngineMultiBroker:
    def __init__(self):
        # Mapeo universal de activos a través de múltiples brokers (Pepperstone, IC Markets, OANDA)
        self.symbols = {
            "GOLD": ["XAUUSD", "XAUUSD.a", "GOLD", "GOLD.raw"],
            "SILVER": ["XAGUSD", "XAGUSD.a", "SILVER", "SILVER.raw"],
            "BRENT": ["BRENT", "UKOIL", "XBRUSD", "BRENT.spot"],
            "WTI": ["WTI", "USOIL", "XTIUSD", "WTI.spot"]
        }
        self.risk_per_trade = 0.015  # 1.5%
        self.atr_factor_sl = 1.8
        self.rr_ratio = 3.0
        self.timeframe_macro = mt5.TIMEFRAME_H4
        self.timeframe_trigger = mt5.TIMEFRAME_H1
        self.state_file = "data.json"

    def initialize_mt5_multi(self):
        """Conexión robusta que intenta autenticar con el broker primario o secundario configurado."""
        if not mt5.initialize():
            logging.error(f"Fallo al inicializar terminal MT5: {mt5.last_error()}")
            return False

        # Cargar credenciales desde variables de entorno
        login = int(os.getenv("MT5_LOGIN", 0))
        password = os.getenv("MT5_PASSWORD", "")
        server = os.getenv("MT5_SERVER", "Pepperstone-Demo")  # O 'ICMarkets-Demo', 'OANDA-Demo'

        if login and password and server:
            authorized = mt5.login(login, password=password, server=server)
            if authorized:
                acc_info = mt5.account_info()
                logging.info(f"[CONNECTED] Broker: {acc_info.company} | Servidor: {server} | Estatus: OK")
                return True
            else:
                logging.error(f"[AUTH ERROR] No se pudo conectar a {server}: {mt5.last_error()}")
                return False
        
        logging.warning("[WARN] Variables de entorno no detectadas. Usando conexión MT5 activa local.")
        return True

    def resolve_symbol(self, category):
        """Escanea la lista de alias para encontrar la nomenclatura exacta usada por el broker conectado."""
        candidates = self.symbols.get(category, [])
        for sym in candidates:
            info = mt5.symbol_info(sym)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(sym, True)
                return sym
        return None

    def calculate_position_size_safe(self, symbol, entry_price, sl_price, balance):
        """Cálculo dinámico de volumen adaptado a las reglas de margen del broker."""
        risk_amount = balance * self.risk_per_trade
        sl_distance = abs(entry_price - sl_price)

        if sl_distance == 0:
            return 0.0

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return 0.0

        # Obtención de variables financieras del contrato
        point = symbol_info.point
        contract_size = symbol_info.trade_contract_size
        
        # Valor real por pip/punto en la divisa de la cuenta
        value_per_point = contract_size * point
        risk_in_points = sl_distance / point
        loss_per_lot = risk_in_points * value_per_point

        if loss_per_lot == 0:
            return 0.0

        volume = risk_amount / loss_per_lot

        # Ajuste estricto a las restricciones del broker (Lotes Mínimos, Pasos y Apalancamiento)
        step_volume = symbol_info.volume_step
        min_volume = symbol_info.volume_min
        max_volume = symbol_info.volume_max

        volume = np.round(volume / step_volume) * step_volume
        volume = max(min_volume, min(max_volume, volume))

        return float(round(volume, 2))
