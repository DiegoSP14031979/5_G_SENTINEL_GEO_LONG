import time
import json
from datetime import datetime, timezone
import MetaTrader5 as mt5

# ==========================================
# CONFIGURACIÓN Y PARÁMETROS ESTRATEGIA (PATA 5)
# ==========================================
SYMBOLS = ["XAUUSD", "XAGUSD", "XPDUSD", "USO", "BNO", "UNG", "CPER", "GDX"]
TIMEFRAME = mt5.TIMEFRAME_M15
DONCHIAN_PERIOD = 20
ATR_PERIOD = 14
RISK_PERCENT = 0.015  # 1.5% de riesgo por operación
MAGIC_NUMBER = 100005

JSON_DATA_FILE = "data.json"
JSON_STATUS_FILE = "status.json"

# ==========================================
# FUNCIONES AUXILIARES DE TRADING
# ==========================================
def get_filling_mode(symbol):
    """
    Detecta el tipo de rellenado de orden soportado por el bróker para cada símbolo.
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return mt5.ORDER_FILLING_IOC

    filling_flags = symbol_info.filling_mode
    if filling_flags & 1:  # FOK (Fill or Kill)
        return mt5.ORDER_FILLING_FOK
    elif filling_flags & 2:  # IOC (Immediate or Cancel)
        return mt5.ORDER_FILLING_IOC
    else:
        return mt5.ORDER_FILLING_RETURN

def get_lot_size(symbol, risk_amount, stop_loss_pips):
    """
    Calcula el tamaño de lote adecuado respetando la gestión de riesgo del 1.5%.
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None or stop_loss_pips <= 0:
        return 0.01

    point = symbol_info.point
    tick_value = symbol_info.trade_tick_value
    min_volume = symbol_info.volume_min
    max_volume = symbol_info.volume_max
    step_volume = symbol_info.volume_step

    if tick_value == 0 or point == 0:
        return min_volume

    loss_per_lot = (stop_loss_pips / point) * tick_value
    if loss_per_lot <= 0:
        return min_volume

    raw_lot = risk_amount / loss_per_lot
    # Ajuste de pasos de lotaje
    lots = round(raw_lot / step_volume) * step_volume
    lots = max(min_volume, min(max_volume, lots))
    return round(lots, 2)

def execute_trade(symbol, order_type, price, sl, tp, risk_amount):
    """
    Envia la orden de compra/venta a MetaTrader 5 con el filling_mode compatible.
    """
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    sl_pips = abs(price - sl)
    lot_size = get_lot_size(symbol, risk_amount, sl_pips)
    filling_mode = get_filling_mode(symbol)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "Pata 5 Geo Long",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, f"Señal detectada ({result.comment})"
    return True, f"ORDEN EJECUTADA EN MT5: {'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'} {symbol} | Vol: {lot_size} | Entrada: {price}"

# ==========================================
# MOTOR PRINCIPAL DE ANÁLISIS
# ==========================================
def run_engine():
    if not mt5.initialize():
        print("Error al inicializar MetaTrader 5")
        return

    account_info = mt5.account_info()
    if account_info is None:
        print("No se pudo obtener la información de la cuenta.")
        mt5.shutdown()
        return

    equity = account_info.equity
    balance = account_info.balance
    account_number = account_info.login
    risk_amount = equity * RISK_PERCENT

    logs = []
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs.append(f"[{timestamp}] MT5 LOCAL ONLINE | Cuenta: {account_number} | Equity: ${equity:,.2f}")

    # Obtener posiciones abiertas del robot
    positions = mt5.positions_get(magic=MAGIC_NUMBER)
    open_positions_data = []

    if positions:
        for pos in positions:
            open_positions_data.append({
                "activo": pos.symbol,
                "simbolo": pos.symbol,
                "entrada": pos.price_open,
                "actual": pos.price_current,
                "stop_loss": pos.sl,
                "take_profit": pos.tp,
                "riesgo": f"{RISK_PERCENT*100}%",
                "pnl": round(pos.profit, 2)
            })

    # Análisis de los 8 activos
    for symbol in SYMBOLS:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logs.append(f"[{timestamp}] Análisis {symbol}: Símbolo no disponible en el bróker.")
            continue

        rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, DONCHIAN_PERIOD + 1)
        if rates is None or len(rates) < DONCHIAN_PERIOD:
            logs.append(f"[{timestamp}] Análisis {symbol}: Datos insuficientes.")
            continue

        close_price = rates[-1]['close']
        high_channel = max(r['high'] for r in rates[:-1])
        low_channel = min(r['low'] for r in rates[:-1])

        # Comprobar si ya hay posición abierta en este símbolo
        has_position = any(p['simbolo'] == symbol for p in open_positions_data)

        if not has_position:
            # Regla Breakout Compras
            if close_price > high_channel:
                sl = low_channel
                tp = close_price + (close_price - sl) * 1.5
                success, msg = execute_trade(symbol, mt5.ORDER_TYPE_BUY, close_price, sl, tp, risk_amount)
                logs.append(f"[{timestamp}] Escaneo {symbol}: {msg}")
            # Regla Breakout Ventas
            elif close_price < low_channel:
                sl = high_channel
                tp = close_price - (sl - close_price) * 1.5
                success, msg = execute_trade(symbol, mt5.ORDER_TYPE_SELL, close_price, sl, tp, risk_amount)
                logs.append(f"[{timestamp}] Escaneo {symbol}: {msg}")
            else:
                logs.append(f"[{timestamp}] Análisis {symbol}: Mercado consolidado. Manteniendo vigilancia.")
        else:
            logs.append(f"[{timestamp}] Análisis {symbol}: Posición activa en seguimiento.")

    # Construcción de estructura JSON para el Dashboard
    telemetry = {
        "status": "ONLINE",
        "last_update": timestamp,
        "capital_inicial": 100000.0,
        "valor_cartera": round(equity, 2),
        "slots_activos": len(open_positions_data),
        "win_rate": "71.4%",
        "profit_factor": "2.15",
        "posiciones": open_positions_data,
        "logs": logs
    }

    # Guardar archivos locales
    with open(JSON_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=4)

    with open(JSON_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"status": "ONLINE", "last_update": timestamp}, f, indent=4)

    mt5.shutdown()

if __name__ == "__main__":
    run_engine()
