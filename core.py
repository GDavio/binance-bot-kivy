"""core.py - lógica compartilhada (SEM Kivy): estratégia, Binance, backtest e o Motor do robô.

É usado por duas partes do app:
  * main.py     -> a tela (só mostra e envia comandos)
  * service.py  -> serviço Android em primeiro plano que roda o robô em segundo plano
"""
import hashlib
import hmac
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "EUR", "BRL", "TRY")


# ----------------------------------------------------------------------------
# Parâmetros da estratégia (os mesmos do seu bot)
# ----------------------------------------------------------------------------
COOLDOWN = 120        # segundos entre operações do mesmo par
VOL_MIN = 0.10        # volatilidade mínima (%) para entrar
PROB_MIN = 0.55       # filtro da IA (só vale depois de IA_MIN_HIST trades)
IA_MIN_HIST = 50
STOP_PCT = 0.5        # stop loss fixo (antes de o trailing ativar)
TRAIL_ATIVA = 0.45    # lucro máximo (%) que ativa o trailing escada (antes 0,35)
TRAIL_TRAVA = 0.25    # 1º degrau do trailing: trava +0,25% (antes +0,10%)
TAXA_IDA_VOLTA = 0.2  # % de taxa por operação (0,1% por lado)
PARAMS = {"vol_min": VOL_MIN, "trail_ativa": TRAIL_ATIVA, "trail_trava": TRAIL_TRAVA,
          "stop_pct": STOP_PCT, "stop_max": 0.8, "escala": 1.0,
          # Perfil 1m (entradas antigas): MA 9/21 simples, RSI 9, ATR 9
          "ma_tipo": "sma", "ma_f": 9, "ma_s": 21, "rsi_p": 9, "atr_p": 9,
          "vol_min_1m": 0.04,              # ATR9 mínimo (%) do perfil 1m (≈ o filtro antigo de 0,10% em 10 fechamentos)
          "sec_rev": True, "sec_trend": True}   # liga/desliga REVERSAO FORTE e PERDEU TENDENCIA
ATR_MIN_1M = PARAMS["vol_min_1m"]
# Perfil 5m (entradas Dia): MA 9/21 exponenciais, RSI 14, ATR 14
PERFIL_DIA = {"ma_tipo": "ema", "ma_f": 9, "ma_s": 21, "rsi_p": 14, "atr_p": 14}
# Saída "perdeu tendência": cruzamento das médias para baixo + RSI <= 40 e caindo (antes < 43)
TREND_RSI = 40
TREND_ATR_SEP = 0.1   # separação mínima entre as médias, em ATR, para valer o cruzamento
# Perfil 5m: stop e escada proporcionais ao ATR de 5m do par (com piso nos valores padrão)
K_STOP = 2.0          # stop = 2 x ATR%  (entre stop_pct e stop_max)
K_ATIVA = 2.5         # trailing ativa em 2,5 x ATR%
ATIVA_MAX = 1.2       # teto da ativação do trailing (%)
TRAVA_FRAC = 0.55     # 1º degrau trava ~55% da ativação (mínimo trail_trava)
ESTRATEGIAS = ["pullback", "continuidade", "rompimento"]
ROTULO = {"pullback": "Pullback", "continuidade": "Continuidade", "rompimento": "Rompimento"}
INTERVALO_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


# ----------------------------------------------------------------------------
# Rede
# ----------------------------------------------------------------------------
BASE_PUBLICA = "https://api.binance.com"
BASE_TESTNET = "https://testnet.binance.vision"
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()


def http_json(path, params=None, timeout=10, base=BASE_PUBLICA, headers=None, metodo="GET"):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    h = {"User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    for tentativa in range(3):
        try:
            req = urllib.request.Request(url, headers=h, method=metodo)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            corpo = e.read().decode("utf-8", "ignore") if e.fp else ""
            raise Exception("HTTP %s: %s" % (e.code, corpo or e.reason))
        except (urllib.error.URLError, OSError):
            if tentativa == 2:
                raise
            time.sleep(1.5)


def buscar_klines(symbol, intervalo, limit=100, inicio=None):
    """Lista de (open, high, low, close, open_time_ms, volume)."""
    p = {"symbol": symbol, "interval": intervalo, "limit": limit}
    if inicio:
        p["startTime"] = int(inicio)
    res = http_json("/api/v3/klines", p)
    return [(float(k[1]), float(k[2]), float(k[3]), float(k[4]), int(k[0]), float(k[5])) for k in res]


def buscar_tickers(symbols):
    arr = json.dumps(symbols, separators=(",", ":"))
    return http_json("/api/v3/ticker/24hr", {"symbols": arr})


def validar_par(sym):
    return float(http_json("/api/v3/ticker/price", {"symbol": sym})["price"])


def ajustar_qtd(qtd, step):
    d = Decimal(str(qtd))
    if step and step > 0:
        d = (d / step).to_integral_value(rounding=ROUND_DOWN) * step
    return d


class Binance:
    """Cliente assinado. Funciona com qualquer par (lê stepSize/minQty no exchangeInfo)."""

    def __init__(self, key, secret, testnet=True):
        self.key = key.strip()
        self.secret = secret.strip()
        self.base = BASE_TESTNET if testnet else BASE_PUBLICA
        self._info = {}

    def _assinado(self, metodo, endpoint, params=None):
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = 10000
        q = urllib.parse.urlencode(p)
        p["signature"] = hmac.new(self.secret.encode("utf-8"), q.encode("utf-8"),
                                  hashlib.sha256).hexdigest()
        return http_json(endpoint, p, base=self.base, metodo=metodo,
                         headers={"X-MBX-APIKEY": self.key})

    def saldo_livre(self):
        res = self._assinado("GET", "/api/v3/account")
        return {b["asset"]: float(b["free"]) for b in res.get("balances", [])}

    def info(self, sym):
        if sym not in self._info:
            r = http_json("/api/v3/exchangeInfo", {"symbol": sym}, base=self.base)["symbols"][0]
            step, minq, minn = Decimal("0"), 0.0, 0.0
            for f in r["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step, minq = Decimal(f["stepSize"]), float(f["minQty"])
                elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
                    minn = float(f.get("minNotional", 0))
            self._info[sym] = {"base": r["baseAsset"], "quote": r["quoteAsset"],
                               "step": step, "minq": minq, "minn": minn}
        return self._info[sym]

    def _resumo(self, res, sym):
        fills = res.get("fills", [])
        tq = sum(float(f["qty"]) for f in fills)
        preco = sum(float(f["price"]) * float(f["qty"]) for f in fills) / tq if tq > 0 else 0.0
        if preco == 0:
            ex, cq = float(res.get("executedQty", 0)), float(res.get("cummulativeQuoteQty", 0))
            preco = cq / ex if ex > 0 else 0.0
        base = self.info(sym)["base"]
        taxa = sum(float(f.get("commission", 0)) for f in fills if f.get("commissionAsset") == base)
        return {"preco": preco, "qtd": float(res.get("executedQty", 0)) - taxa}

    def comprar(self, sym, valor_quote):
        v = ("%.8f" % valor_quote).rstrip("0").rstrip(".")
        res = self._assinado("POST", "/api/v3/order", {
            "symbol": sym, "side": "BUY", "type": "MARKET", "quoteOrderQty": v})
        return self._resumo(res, sym)

    def vender(self, sym, qtd):
        info = self.info(sym)
        q = ajustar_qtd(qtd, info["step"])
        if q <= 0 or float(q) < info["minq"]:
            raise Exception("Invalid quantity: %s abaixo do mínimo" % qtd)
        res = self._assinado("POST", "/api/v3/order", {
            "symbol": sym, "side": "SELL", "type": "MARKET",
            "quantity": format(q.normalize(), "f")})
        return self._resumo(res, sym)


# ----------------------------------------------------------------------------
# Indicadores (iguais ao seu código)
# ----------------------------------------------------------------------------
def calcular_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    delta = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in delta]
    losses = [-d if d < 0 else 0 for d in delta]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def media(lista, periodo):
    return sum(lista[-periodo:]) / periodo


def serie_ma(closes, p):
    return [None if i + 1 < p else sum(closes[i + 1 - p:i + 1]) / p for i in range(len(closes))]


def serie_rsi(closes, p=14):
    out = [None] * len(closes)
    if len(closes) <= p:
        return out
    ganhos = perdas = 0.0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        ganhos += max(d, 0)
        perdas += max(-d, 0)
    ag, al = ganhos / p, perdas / p
    out[p] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(p + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


# ----------------------------------------------------------------------------
# ESTRATÉGIA: entradas
# ----------------------------------------------------------------------------
def analisar(closes, rsi_p=14, atr_pct=None):
    """Indicadores + os três gatilhos de entrada. Use as últimas ~50 closes."""
    if len(closes) < 30:
        return None
    preco, ant = closes[-1], closes[-2]
    rsi, rsi_ant = calcular_rsi(closes, rsi_p), calcular_rsi(closes[:-1], rsi_p)
    ma9, ma21 = media(closes, 9), media(closes, 21)
    # volatilidade: ATR% do perfil (1m: ATR9); sem ATR cai na faixa dos últimos 10 fechamentos
    vol = atr_pct if atr_pct is not None else (max(closes[-10:]) - min(closes[-10:])) / preco * 100
    dist = (ma9 - ma21) / ma21 * 100
    tend = ma9 > ma21
    forte = dist > 0.02
    pullback = (tend and preco <= ma9 * 1.003 and preco > ma9
                and rsi < 62 and rsi > rsi_ant)
    continuidade = (tend and preco > ma9 and 50 < rsi < 60      # RSI 50-60 (antes 52-68)
                    and rsi > rsi_ant and preco > ant
                    and (dist > 0.03 or (dist > 0.001 and preco > ant)))
    rompimento = (forte and preco >= max(closes[-3:]) and preco > ant
                  and 55 < rsi < 75 and rsi > rsi_ant and preco > ma9
                  and (preco - closes[-3]) / closes[-3] * 100 > 0.01)
    return {"preco": preco, "ant": ant, "rsi": rsi, "rsi_ant": rsi_ant, "ma9": ma9, "ma21": ma21,
            "vol": vol, "dist": dist, "tend": tend, "pullback": pullback,
            "continuidade": continuidade, "rompimento": rompimento}


def escolher_entrada(a, ativas, vol_min=None):
    """Prioridade: pullback > continuidade > rompimento (só as estratégias ligadas)."""
    vm = ATR_MIN_1M if vol_min is None else vol_min
    if a is None or a["vol"] <= vm:
        return None
    for nome in ESTRATEGIAS:
        if nome in ativas and a[nome]:
            return nome
    return None


def tipo_prioritario(a):
    for nome in ESTRATEGIAS:
        if a[nome]:
            return nome
    return "nenhum"


def prever_probabilidade(hist, rsi, ma9, ma21, vol, preco, tipo):
    if len(hist) < 20:
        return 0.50
    peso_total = score = 0
    for d in hist:
        if "distancia_ma" not in d:
            continue
        peso = 1.0
        peso *= max(0, 1 - abs(d["rsi"] - rsi) / 15)
        peso *= max(0, 1 - abs(d["volatilidade"] - vol) / 0.5)
        peso *= max(0, 1 - abs(d["distancia_ma"] - ((ma9 - ma21) / ma21 * 100)) / 0.3)
        if d["posicao_preco_ma9"] != (preco > ma9):
            peso *= 0.5
        if d["posicao_preco_ma21"] != (preco > ma21):
            peso *= 0.5
        if d.get("tipo_entrada") != tipo:
            peso *= 0.3
        if peso < 0.1:
            continue
        peso_total += peso
        score += peso * d["resultado"]
    if peso_total < 1:
        return 0.5
    return max(0, min(1, 0.5 + (score / peso_total)))


# ----------------------------------------------------------------------------
# ESTRATÉGIA: saídas
#   1) ANTES do trailing ativar  -> STOP LOSS fixo (-0,5%) é a saída principal
#   2) DEPOIS do trailing ativar -> TRAILING ESCADA passa a ser a principal
#   3) Em seguida (2ª camada)    -> REVERSAO FORTE e PERDEU TENDENCIA
# ----------------------------------------------------------------------------
def trava_trailing(lucro_max, trava1=None, esc=1.0):
    """Degraus da escada; esc=1 é a escada padrão. Em esc>1 todos os degraus ficam proporcionalmente maiores."""
    t1 = TRAIL_TRAVA if trava1 is None else trava1
    if lucro_max < 0.70 * esc:
        return t1
    if lucro_max < 1.05 * esc:
        return max(t1, 0.35 * esc)
    if lucro_max < 1.40 * esc:
        return max(t1, 0.70 * esc)
    if lucro_max < 1.75 * esc:
        return max(t1, 1.05 * esc)
    return max(t1, lucro_max - 0.35 * esc)


def nivel_stop(entrada, topo, p=None):
    """(preço do stop, motivo, trailing_ativo). p = parâmetros (padrão: PARAMS)."""
    p = p or PARAMS
    stop = p.get("stop_pct", STOP_PCT)
    lucro_max = (topo / entrada - 1) * 100
    if lucro_max >= p["trail_ativa"]:
        t = trava_trailing(lucro_max, p["trail_trava"], p.get("escala", 1.0))
        return entrada * (1 + t / 100), "TRAILING STOP (%.2f%%)" % t, True
    return entrada * (1 - stop / 100), "STOP LOSS -%.2f%%" % stop, False


def params_atr(atr_pct, base=None):
    """Parâmetros de saída proporcionais à volatilidade (ATR% de 5m) do par, fixados na entrada."""
    b = dict(PARAMS, **(base or {}))
    smin = b["stop_pct"]
    smax = max(smin, b.get("stop_max", 0.8))
    stop = min(max(K_STOP * atr_pct, smin), smax)
    a0, t0 = b["trail_ativa"], b["trail_trava"]
    ativa = min(max(K_ATIVA * atr_pct, a0), max(a0, ATIVA_MAX))
    trava = min(max(t0, TRAVA_FRAC * ativa), ativa - 0.05)
    b.update(PERFIL_DIA)
    b.update(stop_pct=stop, trail_ativa=ativa, trail_trava=trava, escala=(ativa / a0 if a0 > 0 else 1.0))
    return b


def atr_pct_velas(velas, periodo):
    """ATR (Wilder) em % do último fechamento. velas = (o, h, l, c, ...)."""
    a = _atr([x[1] for x in velas], [x[2] for x in velas], [x[3] for x in velas], periodo)[-1]
    return a / velas[-1][3] * 100 if a else 0.0


def indicadores_saida(velas, q):
    """MA rápida/lenta, RSI e ATR com os períodos do perfil da posição. velas = (o, h, l, c, ...)."""
    c = [x[3] for x in velas]
    h = [x[1] for x in velas]
    l = [x[2] for x in velas]
    pf, ps = q.get("ma_f", 9), q.get("ma_s", 21)
    if q.get("ma_tipo", "sma") == "ema":
        mf, ms = _ema(c, pf)[-1], _ema(c, ps)[-1]
    else:
        mf, ms = (media(c, pf), media(c, ps)) if len(c) >= ps else (None, None)
    rp = q.get("rsi_p", 14)
    rsi, rsi_ant = calcular_rsi(c, rp), calcular_rsi(c[:-1], rp)
    atr = _atr(h, l, c, q.get("atr_p", 14))[-1]
    return mf, ms, rsi, rsi_ant, atr


def perdeu_tendencia(mf, ms, rsi, rsi_ant, atr, lucro, k=1.0):
    """Média rápida cruzou abaixo da lenta (com separação >= 0,1 ATR), RSI <= 40 e caindo, em prejuízo."""
    if None in (mf, ms, atr) or atr <= 0:
        return False
    return (mf < ms and (ms - mf) >= TREND_ATR_SEP * atr
            and rsi <= TREND_RSI and rsi < rsi_ant and lucro < -0.35 * k)


def saida_secundaria(entrada, preco, closes, a, p=None, velas=None):
    q = p or PARAMS
    k = q.get("stop_pct", STOP_PCT) / STOP_PCT     # proporcional ao stop
    lucro = (preco / entrada - 1) * 100
    if (q.get("sec_rev", True) and a["rsi"] < 38 and a["rsi"] < a["rsi_ant"] and preco < a["ma9"]
            and preco < a["ant"] and lucro < -0.25 * k):
        return "REVERSAO FORTE"
    if q.get("sec_trend", True) and velas and len(velas) >= 30:
        mf, ms, rsi, rsi_ant, atr = indicadores_saida(velas, q)
        if perdeu_tendencia(mf, ms, rsi, rsi_ant, atr, lucro, k):
            return "PERDEU TENDENCIA"
    return None


def decidir_saida(entrada, topo, preco, closes, p=None, velas=None):
    """velas (opcional, ~70 últimas, inclui a atual): necessárias para a saída 'perdeu tendência' (ATR)."""
    px, motivo, _ = nivel_stop(entrada, topo, p)
    if preco <= px:
        return motivo
    a = analisar(closes, (p or PARAMS).get("rsi_p", 14))
    if a is None:
        return None
    return saida_secundaria(entrada, preco, closes, a, p, velas)


# ----------------------------------------------------------------------------
# BACKTEST (mesmas regras do robô, candle a candle)
# ----------------------------------------------------------------------------
def baixar_candles(sym, intervalo, dias):
    fim = int(time.time() * 1000)
    cur = fim - dias * 86400000
    out = []
    while cur < fim:
        lote = buscar_klines(sym, intervalo, 1000, cur)
        if not lote:
            break
        out += lote
        cur = lote[-1][4] + 1
        if len(lote) < 1000:
            break
    return out


def backtest_par(candles, ativas, p=None, stats=None):
    """Perfil 1m (entradas antigas). Volatilidade = ATR9 (mínimo p["vol_min_1m"]).
    stats (opcional) recebe atr_medio e pct_filtro (% das velas livres que passam no filtro de ATR)."""
    p = dict(PARAMS, **(p or {}))
    atr = _atr([x[1] for x in candles], [x[2] for x in candles], [x[3] for x in candles], p["atr_p"])
    vmin = p.get("vol_min_1m", ATR_MIN_1M)
    trades, pos = [], None
    n_av, soma_atr, n_ok = 0, 0.0, 0
    for i in range(30, len(candles)):
        o, h, l, c, t = candles[i][:5]
        janela = [x[3] for x in candles[max(0, i - 49):i + 1]]
        if pos:
            # 1) stop atingido dentro do candle (usa o topo dos candles anteriores)
            px, motivo, _ = nivel_stop(pos["entrada"], pos["topo"], p)
            if l <= px:
                saida = min(px, o)
            else:
                # 2) atualiza topo e avalia no fechamento (stop/trailing atualizado + 2ª camada)
                pos["topo"] = max(pos["topo"], h)
                motivo = decidir_saida(pos["entrada"], pos["topo"], c, janela, p, candles[max(0, i - 69):i + 1])
                saida = c
                if not motivo:
                    continue
            trades.append({"lucro": (saida / pos["entrada"] - 1) * 100, "motivo": motivo,
                           "tipo": pos["tipo"]})
            pos = None
        elif atr[i] is not None:
            ap = atr[i] / c * 100
            n_av += 1
            soma_atr += ap
            n_ok += ap > vmin
            tipo = escolher_entrada(analisar(janela, p["rsi_p"], ap), ativas, vmin)
            if tipo:
                pos = {"entrada": c, "topo": c, "tipo": tipo}
    if stats is not None:
        stats["atr_medio"] = soma_atr / n_av if n_av else 0.0
        stats["pct_filtro"] = n_ok / n_av * 100 if n_av else 0.0
    return trades


def _grupo(trades, chave):
    g = {}
    for t in trades:
        g.setdefault(chave(t), []).append(t["lucro"])
    return sorted(g.items(), key=lambda x: -len(x[1]))


def resumo_trades(trades, valor, dias=None):
    n = len(trades)
    lucros = [t["lucro"] for t in trades]
    soma = sum(lucros)
    liq = soma - TAXA_IDA_VOLTA * n
    por_dia = " (%.1f/dia)" % (n / dias) if dias else ""
    linhas = [
        "%d op%s · acerto %.0f%%" % (n, por_dia, len([x for x in lucros if x > 0]) / n * 100),
        "Por op: %+.3f%% bruto · %+.3f%% c/ taxa" % (soma / n, liq / n),
        "Soma %+.2f%% (≈ %+.2f USDT)" % (soma, valor * soma / 100),
        "C/ taxa 0,1%%/lado: %+.2f%% (≈ %+.2f USDT)" % (liq, valor * liq / 100),
        "Melhor %+.2f%% · Pior %+.2f%%" % (max(lucros), min(lucros)),
        "Por estratégia:"]
    for k, v in _grupo(trades, lambda t: t["tipo"]):
        m = sum(v) / len(v)
        linhas.append("  %s: %d op · acerto %.0f%% · média %+.3f%% (c/ taxa %+.3f%%)" % (
            k, len(v), len([x for x in v if x > 0]) / len(v) * 100, m, m - TAXA_IDA_VOLTA))
    linhas.append("Por saída:")
    for k, v in _grupo(trades, lambda t: re.sub(r" -[\d.]+%$", "", t["motivo"].split(" (")[0])):
        linhas.append("  %s: %d · média %+.2f%% · soma %+.1f%%" % (k, len(v), sum(v) / len(v), sum(v)))
    return n, soma, "\n".join(linhas)


# ----------------------------------------------------------------------------
# MODELO "DIA" (day trade): sinais em 5m, tendência em 15m, VWAP do dia (UTC) e volume relativo
# Base: guias de day trade (pullback em EMA20/21 com 15m como tendência, VWAP, volume menor no recuo,
# vela de confirmação), regras de RSI de Cardwell/Brown (recuos em 40-50 numa alta) e rompimento de
# máxima de N velas com volume >= 1,5x a média (Donchian). Só compra (spot).
# ----------------------------------------------------------------------------
TF_DIA = "5m"
MS5 = 300000


def _ema(vals, p):
    out = [None] * len(vals)
    if len(vals) < p:
        return out
    k = 2.0 / (p + 1)
    e = sum(vals[:p]) / p
    out[p - 1] = e
    for i in range(p, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def _atr(h, l, c, p=14):
    n = len(c)
    out = [None] * n
    if n <= p:
        return out
    tr = [None] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = sum(tr[1:p + 1]) / p
    out[p] = a
    for i in range(p + 1, n):
        a = (a * (p - 1) + tr[i]) / p
        out[i] = a
    return out


def _vwap_dia(c5):
    """VWAP acumulado desde 00:00 UTC; precisa de >= 4 velas no dia."""
    out = [None] * len(c5)
    dia, pv, vv, ini = None, 0.0, 0.0, 0
    for i, cd in enumerate(c5):
        h, l, c, t, v = cd[1], cd[2], cd[3], cd[4], cd[5]
        d = t // 86400000
        if d != dia:
            dia, pv, vv, ini = d, 0.0, 0.0, i
        pv += (h + l + c) / 3.0 * v
        vv += v
        out[i] = pv / vv if vv > 0 and i - ini >= 3 else None
    return out


def _ctx15(c5):
    """True quando, no 15m (montado a partir do 5m, só blocos já fechados), EMA21 > EMA50 e fechamento > EMA50."""
    n = len(c5)
    ctx = [False] * n
    cl, e21, e50 = [], None, None
    k21, k50 = 2.0 / 22, 2.0 / 51
    for i in range(n):
        if c5[i][4] % 900000 == 600000:          # 3ª vela de 5m do bloco: o 15m fechou
            cl.append(c5[i][3])
            m = len(cl)
            if m == 21:
                e21 = sum(cl) / 21
            elif m > 21:
                e21 = cl[-1] * k21 + e21 * (1 - k21)
            if m == 50:
                e50 = sum(cl) / 50
            elif m > 50:
                e50 = cl[-1] * k50 + e50 * (1 - k50)
        if e21 is not None and e50 is not None:
            ctx[i] = e21 > e50 and cl[-1] > e50
    return ctx


def preparar_dia(c5):
    """Pré-calcula indicadores de uma lista de velas 5m FECHADAS (o, h, l, c, t, v)."""
    n = len(c5)
    o = [x[0] for x in c5]
    h = [x[1] for x in c5]
    l = [x[2] for x in c5]
    c = [x[3] for x in c5]
    t = [x[4] for x in c5]
    v = [x[5] for x in c5]
    return {"o": o, "h": h, "l": l, "c": c, "t": t, "v": v,
            "ema9": _ema(c, PERFIL_DIA["ma_f"]), "ema21": _ema(c, PERFIL_DIA["ma_s"]), "ema50": _ema(c, 50),
            "rsi": serie_rsi(c, PERFIL_DIA["rsi_p"]), "atr": _atr(h, l, c, PERFIL_DIA["atr_p"]),
            "vavg": [None] * 20 + [sum(v[i - 20:i]) / 20 for i in range(20, n)],
            "vwap": _vwap_dia(c5), "ctx": _ctx15(c5)}


def sinais_dia(d, i, p=None):
    """Avalia as três entradas na vela fechada de índice i.
    Retorna {"pullback","continuidade","rompimento": bool, "falhas": {nome: 1º motivo que falhou}, "info": {...}}."""
    p = dict(PARAMS, **(p or {}))
    res = {"pullback": False, "continuidade": False, "rompimento": False, "falhas": {}, "info": {}}
    nomes = ESTRATEGIAS

    def falha_geral(motivo):
        for nm in nomes:
            res["falhas"][nm] = motivo
        return res

    if i < 60:
        return falha_geral("histórico curto (< 60 velas)")
    o, h, l, c, v = d["o"][i], d["h"][i], d["l"][i], d["c"][i], d["v"][i]
    atr, e9, e21, e50 = d["atr"][i - 1], d["ema9"][i], d["ema21"][i], d["ema50"][i]   # ATR da vela anterior
    rsi, rsi_a, vw, va = d["rsi"][i], d["rsi"][i - 1], d["vwap"][i], d["vavg"][i]
    if None in (atr, e9, e21, e50, rsi, rsi_a, va) or va <= 0 or atr <= 0:
        return falha_geral("dados insuficientes")
    atr_pct = atr / c * 100
    rvol = v / va
    res["info"] = {"preco": c, "rsi": rsi, "atr_pct": atr_pct, "rvol": rvol, "vwap": vw,
                   "ema9": e9, "ema21": e21, "ema50": e50, "ctx15": d["ctx"][i]}
    if not d["ctx"][i]:
        return falha_geral("sem tendência de alta no 15m (EMA21 > EMA50 e preço > EMA50)")
    if vw is None:
        return falha_geral("VWAP do dia ainda sem dados (primeiras velas do dia UTC)")
    if c <= vw:
        return falha_geral("fechou abaixo do VWAP do dia")
    if atr_pct < p["vol_min"]:
        return falha_geral("ATR%% baixo (%.3f%% < %.2f%%)" % (atr_pct, p["vol_min"]))

    def checar(nome, regras):
        for motivo, ok in regras:
            if not ok:
                res["falhas"][nome] = motivo
                return False
        res[nome] = True
        return True

    lows, highs, closes, vols = d["l"], d["h"], d["c"], d["v"]

    # ---- PULLBACK: recuo até a EMA21 numa alta, volume menor, RSI ~40-50 e vela de confirmação
    K = 8
    jan = range(i - K, i)
    min_low = min(lows[j] for j in jan)
    idx_min = max(j for j in jan if lows[j] == min_low)
    pico = max(highs[j] for j in range(i - 24, i))
    idx_pico = max(j for j in range(i - 24, i) if highs[j] == pico)
    rsi_min = min(d["rsi"][j] for j in jan)
    vol_rec = (sum(vols[idx_pico + 1:i]) / max(1, i - idx_pico - 1)) if i - idx_pico > 1 else 0.0
    checar("pullback", [
        ("topo anterior não veio antes do fundo do recuo", idx_pico < idx_min and i - idx_pico >= 3),
        ("recuo fora de 0,7–3,5 ATR", 0.7 <= (pico - min_low) / atr <= 3.5),
        ("recuo não chegou na EMA21", any(lows[j] <= d["ema21"][j] * 1.001 for j in jan)),
        ("recuo perdeu a EMA50", min_low >= d["ema50"][idx_min] * 0.998),
        ("RSI não desceu até ~50 no recuo", rsi_min <= 50),
        ("RSI caiu abaixo de 30 no recuo", rsi_min >= 30),
        ("RSI não está virando para cima", rsi > rsi_a and rsi >= 45),
        ("volume do recuo acima da média", vol_rec <= va),
        ("sem vela de confirmação (alta, acima da máxima anterior e da EMA21)",
         c > o and c > highs[i - 1] and c > e21)])

    # ---- CONTINUIDADE: impulso + bandeira curta acima da EMA21 e saída pela máxima da bandeira
    Kf = 4
    fl = range(i - Kf, i)
    f_high = max(highs[j] for j in fl)
    f_low = min(lows[j] for j in fl)
    pre_low = min(lows[j] for j in range(i - 16, i - Kf))
    checar("continuidade", [
        ("médias fora de ordem (EMA9 > EMA21 > EMA50) ou preço abaixo da EMA9", e9 > e21 > e50 and c > e9),
        ("sem impulso anterior (>= 1,5 ATR)", closes[i - Kf - 1] - pre_low >= 1.5 * atr),
        ("bandeira larga (> 1,2 ATR)", f_high - f_low <= 1.2 * atr),
        ("bandeira perdeu a EMA21", f_low > e21),
        ("RSI fora de 50–80", 50 <= rsi <= 80),
        ("não fechou acima da máxima da bandeira com vela de alta", c > f_high and c > o),
        ("volume abaixo da média no rompimento da bandeira", rvol >= 1.0)])

    # ---- ROMPIMENTO: máxima de 20 velas após consolidação, volume >= 1,5x e fechamento forte
    N = 20
    nivel = max(highs[j] for j in range(i - N, i))
    faixa = max(highs[i - 12:i]) - min(lows[i - 12:i])
    checar("rompimento", [
        ("não fechou acima da máxima de 20 velas (+0,1 ATR)", c >= nivel + 0.1 * atr),
        ("sem consolidação antes (faixa de 12 velas > 3,5 ATR)", faixa <= 3.5 * atr),
        ("volume abaixo de 1,5x a média", rvol >= 1.5),
        ("fechamento fraco (rejeição)", c > o and c >= l + 0.6 * (h - l))])
    return res


def escolher_tipo_dia(res, ativas):
    """Prioridade: pullback > continuidade > rompimento (só as ligadas)."""
    for nome in ESTRATEGIAS:
        if nome in ativas and res.get(nome):
            return nome
    return None


def velas_fechadas(candles, agora_ms=None):
    agora_ms = agora_ms or int(time.time() * 1000)
    if candles and candles[-1][4] + MS5 > agora_ms:
        return candles[:-1]
    return candles


def backtest_dia(c5, ativas, p=None, t_ini=0, stats=None):
    """Backtest do perfil 5m sobre velas 5m fechadas. Entra no fechamento da vela do sinal.
    Stop/escada proporcionais ao ATR% do momento da entrada (p["prop"]=False usa os valores fixos).
    stats (dict opcional) recebe atr_medio, stop_medio e ativa_media."""
    p = dict(PARAMS, **(p or {}))
    prop = p.pop("prop", True)
    d = preparar_dia(c5)
    trades, pos = [], None
    atrs, stops, ativas_l = [], [], []
    for i in range(60, len(c5)):
        o, h, l, c, t = c5[i][:5]
        if t >= t_ini and d["atr"][i] is not None:
            atrs.append(d["atr"][i] / c * 100)
        if pos:
            pp = pos["p"]
            px, motivo, _ = nivel_stop(pos["entrada"], pos["topo"], pp)
            if l <= px:
                saida = min(px, o)
            else:
                pos["topo"] = max(pos["topo"], h)
                motivo = decidir_saida(pos["entrada"], pos["topo"], c, d["c"][max(0, i - 49):i + 1], pp,
                                       c5[max(0, i - 69):i + 1])
                saida = c
                if not motivo:
                    continue
            trades.append({"lucro": (saida / pos["entrada"] - 1) * 100, "motivo": motivo,
                           "tipo": pos["tipo"]})
            pos = None
        elif t >= t_ini:
            sin = sinais_dia(d, i, p)
            tipo = escolher_tipo_dia(sin, ativas)
            if tipo:
                pp = params_atr(sin["info"]["atr_pct"], p) if prop else dict(p, **PERFIL_DIA)
                stops.append(pp["stop_pct"])
                ativas_l.append(pp["trail_ativa"])
                pos = {"entrada": c, "topo": c, "tipo": tipo, "p": pp}
    if stats is not None:
        stats["atr_medio"] = sum(atrs) / len(atrs) if atrs else 0.0
        stats["stop_medio"] = sum(stops) / len(stops) if stops else None
        stats["ativa_media"] = sum(ativas_l) / len(ativas_l) if ativas_l else None
    return trades


def diagnostico_dia(sym, p=None):
    """Busca 5m recentes e devolve (dados, índice, sinais) da última vela fechada."""
    fech = velas_fechadas(buscar_klines(sym, TF_DIA, 300))
    if len(fech) < 120:
        raise Exception("poucos candles")
    d = preparar_dia(fech)
    i = len(fech) - 1
    return d, i, sinais_dia(d, i, p)


# ----------------------------------------------------------------------------
# Formatação / pares
# ----------------------------------------------------------------------------
def fmt_preco(p):
    if p >= 10000:
        return "$%s" % format(p, ",.0f")
    if p >= 10:
        return "$%s" % format(p, ",.2f")
    return "$%.4f" % p


def fmt_vol(v):
    return "$%.1fM" % (v / 1e6)


def par_fmt(s):
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)] + "/" + q
    return s


def normalizar_par(s):
    s = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    if not s:
        return ""
    if s in ("BTC", "ETH", "BNB") or not any(s.endswith(q) and len(s) > len(q) for q in QUOTES):
        s += "USDT"
    return s


def lista_pares(texto, maximo=8):
    out = []
    for p in re.split(r"[\s,;]+", texto or ""):
        p = normalizar_par(p)
        if p and p not in out:
            out.append(p)
    return out[:maximo]




# ----------------------------------------------------------------------------
# Arquivos compartilhados entre a tela e o serviço
#   config_ui.json  -> escrito SÓ pela tela (modo, chaves, regras, robô ligado/desligado)
#   estado_app.json -> escrito SÓ pelo Motor (saldo, posições, execuções, IA...)
#   cmds/*.json     -> pedidos da tela para o Motor (ordem manual, encerrar posição)
# Cada arquivo tem um único "dono", então não há conflito de escrita.
# ----------------------------------------------------------------------------
ARQ_CONFIG = "config_ui.json"
ARQ_ESTADO = "estado_app.json"
CONFIG_KEYS = ("modo", "robo_ativo", "regras", "cfg", "modelo")
ESTADO_KEYS = ("saldo", "posicoes", "pos_sim", "pos_real", "pos_manual_real", "execucoes",
               "ia", "ia_sim", "ultimo", "resultados", "msg_robo", "hb",
               "origem", "inicio", "ciclo_ts")


def config_padrao():
    return {
        "modo": "SIM", "robo_ativo": False,
        "modelo": "dia",          # "dia" = entradas novas (5m/15m); "antigo" = entradas de 1m
        "regras": [{"par": p, "valor": 12, "estrategias": list(ESTRATEGIAS)}
                   for p in ("BTCUSDT", "SOLUSDT", "BNBUSDT")],
        "cfg": {"key": "", "secret": "", "testnet": True},
    }


def estado_servico_padrao():
    return {"saldo": 10000.0, "posicoes": {}, "pos_sim": {}, "pos_real": {}, "pos_manual_real": {},
            "execucoes": [], "ia": [], "ia_sim": [], "ultimo": {}, "resultados": {},
            "msg_robo": "", "hb": 0, "origem": "", "inicio": 0, "ciclo_ts": 0}


def ler_json(path, padrao):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return padrao


def gravar_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)          # troca atômica: ninguém lê arquivo pela metade


def pasta_dados():
    """Mesma pasta para a tela e para o serviço (files dir do app)."""
    try:
        from jnius import autoclass
        srv = autoclass("org.kivy.android.PythonService").mService
        if srv is not None:
            return srv.getFilesDir().getAbsolutePath()
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        if act is not None:
            return act.getFilesDir().getAbsolutePath()
    except Exception:
        pass
    return os.environ.get("ANDROID_PRIVATE") or os.path.dirname(os.path.abspath(__file__))


def caminho_relatorio(pasta):
    d = "/storage/emulated/0/Download"
    if os.path.isdir(d) and os.access(d, os.W_OK):
        return os.path.join(d, "relatorio_bot.txt")
    return os.path.join(pasta, "relatorio_bot.txt")


# ----------------------------------------------------------------------------
# Motor do robô + executor de ordens
# ----------------------------------------------------------------------------
class Motor:
    """Roda no serviço Android (ou numa thread local, se o serviço não existir, ex.: Pydroid)."""

    def __init__(self, pasta, origem="local"):
        self.origem = origem          # "servico" (processo do serviço Android) ou "local"
        self.pasta = pasta
        self.arq_estado = os.path.join(pasta, ARQ_ESTADO)
        self.arq_config = os.path.join(pasta, ARQ_CONFIG)
        self.dir_cmds = os.path.join(pasta, "cmds")
        os.makedirs(self.dir_cmds, exist_ok=True)
        self.arq_relatorio = caminho_relatorio(pasta)
        self.lock = threading.RLock()
        self.parar = threading.Event()
        self.est = self._carregar_estado()
        self.config = config_padrao()
        self._cfg_mtime = None
        self._cli = None
        self._ultimo_ciclo = 0.0
        self._ultimo_hb = 0.0
        self._ativo_em = time.time()
        self._fd_trava = None

    # ---------- estado / config ----------
    def _carregar_estado(self):
        d = estado_servico_padrao()
        antigo = ler_json(self.arq_estado, {})
        for k in ESTADO_KEYS:
            if k in antigo:
                d[k] = antigo[k]
        d["resultados"] = {}
        return d

    def ler_config(self):
        try:
            m = os.path.getmtime(self.arq_config)
        except OSError:
            return
        if m != self._cfg_mtime:
            cfg = ler_json(self.arq_config, None)
            if cfg:
                novo = dict(config_padrao(), **cfg)
                novo["cfg"] = dict(config_padrao()["cfg"], **novo.get("cfg", {}))
                for r in novo["regras"]:
                    r.setdefault("estrategias", list(ESTRATEGIAS))
                self.config = novo
                self._cfg_mtime = m
                self._ativo_em = time.time()

    def salvar(self):
        try:
            with self.lock:
                self.est["hb"] = time.time()
                gravar_json(self.arq_estado, self.est)
            self._ultimo_hb = time.time()
        except Exception as e:
            print("Erro ao salvar estado:", e)

    def cliente(self):
        c = self.config["cfg"]
        if not c["key"] or not c["secret"]:
            return None
        chave = (c["key"], c["secret"], c["testnet"])
        if not self._cli or self._cli[0] != chave:
            self._cli = (chave, Binance(c["key"], c["secret"], c["testnet"]))
        return self._cli[1]

    def hist(self, modo):
        return self.est["ia"] if modo == "REAL" else self.est["ia_sim"]

    def manual_d(self, modo):
        return self.est["posicoes"] if modo == "SIM" else self.est["pos_manual_real"]

    def registrar(self, lado, sym, motivo, direita, positivo):
        with self.lock:
            self.est["execucoes"].insert(0, {
                "t": "%s %s" % (lado, sym), "s": motivo, "d": direita, "ok": bool(positivo),
                "ts": time.time()})
            del self.est["execucoes"][50:]

    def resultado(self, cid, msg):
        with self.lock:
            r = self.est["resultados"]
            r[cid] = msg
            while len(r) > 30:
                r.pop(next(iter(r)))
        self.salvar()

    # ---------- comandos vindos da tela ----------
    def processar_cmds(self):
        try:
            nomes = sorted(n for n in os.listdir(self.dir_cmds) if n.endswith(".json"))
        except OSError:
            return
        for n in nomes:
            caminho = os.path.join(self.dir_cmds, n)
            cmd = ler_json(caminho, None)
            try:
                os.remove(caminho)
            except OSError:
                pass
            if not cmd:
                continue
            self._ativo_em = time.time()
            threading.Thread(target=self._executar_cmd, args=(cmd,), daemon=True).start()

    def _executar_cmd(self, cmd):
        cid = cmd.get("id", "?")
        try:
            if cmd.get("tipo") == "ordem":
                msg = self.cmd_ordem(cmd)
            elif cmd.get("tipo") == "encerrar":
                msg = self.cmd_encerrar(cmd)
            else:
                msg = "Comando desconhecido."
        except Exception as e:
            msg = "Erro: %s" % str(e)[:160]
        self.resultado(cid, msg)

    def cmd_ordem(self, cmd):
        est, modo = self.est, self.config["modo"]
        lado, sym = cmd["lado"], cmd["sym"]
        valor, preco = float(cmd["valor"]), float(cmd["preco"])
        nome = "Compra" if lado == "BUY" else "Venda"
        if modo == "SIM":
            with self.lock:
                pos = est["posicoes"]
                if lado == "BUY":
                    if valor > est["saldo"]:
                        return "Saldo virtual insuficiente."
                    est["saldo"] -= valor
                    pos[sym] = pos.get(sym, 0) + valor / preco
                else:
                    qtd = min(valor / preco, pos.get(sym, 0))
                    if qtd <= 0:
                        return "Sem posição para vender neste par."
                    est["saldo"] += qtd * preco
                    pos[sym] -= qtd
                    if pos[sym] <= 1e-12:
                        pos.pop(sym, None)
                self.registrar(lado, sym, "manual", "$%.2f" % preco, True)
            self.salvar()
            return "%s simulada em %s." % (nome, par_fmt(sym))
        ex = self.cliente()
        if not ex:
            return "Configure as chaves da Binance em Ajustes."
        if lado == "BUY":
            r = ex.comprar(sym, valor)
        else:
            livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
            r = ex.vender(sym, min(valor / preco, livre))
        with self.lock:
            man = est["pos_manual_real"]
            if lado == "BUY":
                man[sym] = man.get(sym, 0.0) + r["qtd"]
            else:
                man[sym] = max(0.0, man.get(sym, 0.0) - r["qtd"])
                if man[sym] <= 1e-12:
                    man.pop(sym, None)
            self.registrar(lado, sym, "manual", "$%.2f" % (r["preco"] or preco), True)
        self.salvar()
        return "%s executada em %s." % (nome, par_fmt(sym))

    def cmd_encerrar(self, cmd):
        modo = self.config["modo"]
        msgs = []
        for s in cmd.get("simbolos", []):
            try:
                msgs.append(self.encerrar_sync(s, modo))
            except Exception as e:
                msgs.append("%s: erro (%s)" % (par_fmt(s), str(e)[:100]))
        self.salvar()
        return "\n".join(msgs) or "Nenhuma posição aberta."

    def encerrar_sync(self, sym, modo):
        est = self.est
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        man = self.manual_d(modo)
        with self.lock:
            pos = pos_d.get(sym)
            qtd_man = man.get(sym, 0.0)
            if not pos and qtd_man <= 1e-12:
                return "%s: sem posição aberta." % par_fmt(sym)
            if pos:
                pos["encerrando"] = True
        try:
            if modo == "SIM":
                ps = validar_par(sym)
            else:
                ex = self.cliente()
                if not ex:
                    raise Exception("configure as chaves em Ajustes")
                livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
                total = (pos["qtd"] if pos else 0.0) + qtd_man
                ps = ex.vender(sym, min(total, livre))["preco"]
        except Exception as e:
            with self.lock:
                if pos:
                    pos.pop("encerrando", None)
            if modo == "REAL" and ("-1013" in str(e) or "Invalid quantity" in str(e)):
                with self.lock:
                    pos_d.pop(sym, None)
                    man.pop(sym, None)
                    self.registrar("SYNC", sym, "posição já encerrada ou abaixo do mínimo", "—", True)
                return "%s: já estava encerrada na Binance (registro local limpo)." % par_fmt(sym)
            raise
        with self.lock:
            if pos:
                lucro = (ps / pos["entrada"] - 1) * 100
                if modo == "SIM":
                    est["saldo"] += pos["qtd"] * ps
                pos_d.pop(sym, None)
                self.registrar("SELL", sym, "ENCERRADA MANUAL", "%+.2f%%" % lucro, lucro >= 0)
            if qtd_man > 1e-12:
                if modo == "SIM":
                    est["saldo"] += qtd_man * ps
                man.pop(sym, None)
                self.registrar("SELL", sym, "manual (encerrar)", "$%.2f" % ps, True)
        return "%s encerrada @ %s" % (par_fmt(sym), fmt_preco(ps))

    # ---------- robô ----------
    def ciclo(self):
        est, modo = self.est, self.config["modo"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        with self.lock:
            regras = [dict(r) for r in self.config["regras"]]
            # posição aberta sem regra (regra apagada) continua sendo gerenciada, só saída
            for s in list(pos_d):
                if not any(r["par"] == s for r in regras):
                    regras.append({"par": s, "valor": 0, "estrategias": []})
        erro = ""
        for regra in regras:
            if not self.config["robo_ativo"] or self.parar.is_set():
                break
            try:
                self.processar(regra, modo)
            except Exception as e:
                erro = "[%s] %s" % (regra["par"], str(e)[:120])
        with self.lock:
            est["msg_robo"] = erro
            est["ciclo_ts"] = time.time()
        self.salvar()

    def processar(self, regra, modo):
        if self.config.get("modelo", "dia") == "dia":
            return self.processar_dia(regra, modo)
        return self.processar_antigo(regra, modo)

    def processar_dia(self, regra, modo):
        """Entradas novas (5m + 15m + VWAP + volume). Saídas iguais às antigas, calculadas também em 5m."""
        est = self.est
        sym = regra["par"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        candles = buscar_klines(sym, TF_DIA, 300)
        agora_ms = int(time.time() * 1000)
        fech = velas_fechadas(candles, agora_ms)
        if len(fech) < 120:
            return
        preco = candles[-1][3]
        closes = [x[3] for x in fech][-49:] + [preco]
        ex, livre = None, {}
        if modo == "REAL":
            ex = self.cliente()
            if not ex:
                raise Exception("configure as chaves em Ajustes")
            livre = ex.saldo_livre()
        pos = pos_d.get(sym)

        if pos:
            if pos.get("encerrando"):
                return
            if modo == "REAL":
                base = ex.info(sym)["base"]
                if livre.get(base, 0.0) < 0.0001:
                    with self.lock:
                        pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posição encerrada na Binance", "—", True)
                    self.salvar()
                    return
            with self.lock:
                pos["topo"] = max(pos["topo"], preco)
            motivo = decidir_saida(pos["entrada"], pos["topo"], preco, closes, pos.get("p"), candles[-70:])
            if motivo:
                self.fechar(sym, pos, preco, motivo, closes, modo, ex, livre)
            return

        d = preparar_dia(fech)
        i = len(fech) - 1
        sinais = sinais_dia(d, i)
        tipo = escolher_tipo_dia(sinais, regra.get("estrategias", ESTRATEGIAS))
        if not tipo:
            return
        if agora_ms - (fech[-1][4] + MS5) > 90000:      # sinal "velho": não persegue
            return
        if preco > fech[-1][3] * 1.002:                  # já subiu demais desde o fechamento do sinal
            return
        chave = "%s:%s" % (modo, sym)
        if time.time() - est["ultimo"].get(chave, 0) <= COOLDOWN:
            return
        valor = float(regra["valor"])
        if valor <= 0:
            return
        if modo == "SIM":
            with self.lock:
                if est["saldo"] < valor:
                    return
                est["saldo"] -= valor
                preco_exec, qtd = preco, valor / preco
        else:
            inf_s = ex.info(sym)
            quote = inf_s["quote"]
            if valor < inf_s["minn"]:
                raise Exception("ordem de %.2f abaixo do mínimo da Binance para %s (%.2f)" % (valor, sym, inf_s["minn"]))
            if livre.get(quote, 0.0) < valor:
                return
            r = ex.comprar(sym, valor)
            preco_exec, qtd = r["preco"] or preco, r["qtd"]
        inf = sinais["info"]
        with self.lock:
            pos_d[sym] = {
                "entrada": preco_exec, "topo": preco_exec, "t": time.time(), "qtd": qtd,
                "valor": valor, "tipo": tipo, "modelo": "dia", "rsi": inf["rsi"], "ma9": inf["ema9"],
                "ma21": inf["ema21"], "vol": inf["atr_pct"],
                "dist": (inf["ema9"] - inf["ema21"]) / inf["ema21"] * 100,
                "p9": preco > inf["ema9"], "p21": preco > inf["ema21"],
                "p": params_atr(inf["atr_pct"])}          # stop/escada fixos desta operação
            est["ultimo"][chave] = time.time()
            self.registrar("BUY", sym, tipo, "$%.2f" % preco_exec, True)
        self.salvar()

    def processar_antigo(self, regra, modo):
        est = self.est
        sym = regra["par"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        velas = buscar_klines(sym, "1m", 70)
        closes = [c[3] for c in velas][-50:]
        if len(closes) < 30:
            return
        preco = closes[-1]
        ex, livre = None, {}
        if modo == "REAL":
            ex = self.cliente()
            if not ex:
                raise Exception("configure as chaves em Ajustes")
            livre = ex.saldo_livre()
        pos = pos_d.get(sym)

        if pos:
            if pos.get("encerrando"):
                return
            if modo == "REAL":
                base = ex.info(sym)["base"]
                if livre.get(base, 0.0) < 0.0001:
                    with self.lock:
                        pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posição encerrada na Binance", "—", True)
                    self.salvar()
                    return
            with self.lock:
                pos["topo"] = max(pos["topo"], preco)
            motivo = decidir_saida(pos["entrada"], pos["topo"], preco, closes, pos.get("p"), velas)
            if motivo:
                self.fechar(sym, pos, preco, motivo, closes, modo, ex, livre)
            return

        # entrada
        a = analisar(closes, PARAMS["rsi_p"], atr_pct_velas(velas, PARAMS["atr_p"]))
        tipo = escolher_entrada(a, regra.get("estrategias", ESTRATEGIAS))
        if not tipo:
            return
        chave = "%s:%s" % (modo, sym)
        if time.time() - est["ultimo"].get(chave, 0) <= COOLDOWN:
            return
        hist = [h for h in self.hist(modo) if h.get("modelo", "antigo") == "antigo"]
        prob = prever_probabilidade(hist, a["rsi"], a["ma9"], a["ma21"], a["vol"], preco,
                                    tipo_prioritario(a))
        if not (len(hist) < IA_MIN_HIST or prob > PROB_MIN):
            return
        valor = float(regra["valor"])
        if valor <= 0:
            return
        if modo == "SIM":
            with self.lock:
                if est["saldo"] < valor:
                    return
                est["saldo"] -= valor
                preco_exec, qtd = preco, valor / preco
        else:
            inf_s = ex.info(sym)
            quote = inf_s["quote"]
            if valor < inf_s["minn"]:
                raise Exception("ordem de %.2f abaixo do mínimo da Binance para %s (%.2f)" % (valor, sym, inf_s["minn"]))
            if livre.get(quote, 0.0) < valor:
                return
            r = ex.comprar(sym, valor)
            preco_exec, qtd = r["preco"] or preco, r["qtd"]
        with self.lock:
            pos_d[sym] = {
                "entrada": preco_exec, "topo": preco_exec, "t": time.time(), "qtd": qtd,
                "valor": valor, "tipo": tipo, "rsi": a["rsi"], "ma9": a["ma9"], "ma21": a["ma21"],
                "vol": a["vol"], "dist": a["dist"], "p9": preco > a["ma9"], "p21": preco > a["ma21"],
                "p": dict(PARAMS)}
            est["ultimo"][chave] = time.time()
            self.registrar("BUY", sym, tipo, "$%.2f" % preco_exec, True)
        self.salvar()

    def fechar(self, sym, pos, preco, motivo, closes, modo, ex, livre):
        est = self.est
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        preco_saida = preco
        if modo == "REAL":
            try:
                base = ex.info(sym)["base"]
                r = ex.vender(sym, min(pos["qtd"], livre.get(base, 0.0)))
                preco_saida = r["preco"] or preco
            except Exception as e:
                if "-1013" in str(e) or "Invalid quantity" in str(e):
                    with self.lock:
                        pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posição já encerrada na Binance", "—", True)
                    self.salvar()
                    return
                raise
        lucro = (preco_saida / pos["entrada"] - 1) * 100
        lucro_max = (pos["topo"] / pos["entrada"] - 1) * 100
        a = analisar(closes, (pos.get("p") or {}).get("rsi_p", 14)) or {"rsi": 0, "ma9": 0, "ma21": 0}
        with self.lock:
            if modo == "SIM":
                est["saldo"] += pos["qtd"] * preco_saida
            self.hist(modo).append({
                "tipo_entrada": pos["tipo"], "rsi": pos["rsi"], "volatilidade": pos["vol"],
                "distancia_ma": pos["dist"], "posicao_preco_ma9": pos["p9"],
                "posicao_preco_ma21": pos["p21"], "forca_tendencia": abs(pos["dist"]),
                "modelo": pos.get("modelo", "antigo"), "resultado": lucro})
            del self.hist(modo)[:-500]
            pos_d.pop(sym, None)
            self.registrar("SELL", sym, motivo, "%+.2f%%" % lucro, lucro >= 0)
        self.salvar()
        self.salvar_relatorio(sym, pos, modo, lucro, preco_saida, lucro_max, a)

    def salvar_relatorio(self, sym, pos, modo, lucro, preco_saida, lucro_max, a):
        try:
            with open(self.arq_relatorio, "a") as f:
                f.write("==============================\n")
                f.write("Par: %s  (%s)\nData: %s\n" % (sym, "SIMULAÇÃO" if modo == "SIM" else "REAL",
                                                       datetime.now()))
                f.write("Estratégia: %s\n--- ENTRADA ---\n" % pos["tipo"])
                f.write("Preço: %.4f\nRSI: %.2f\nMA9: %.4f\nMA21: %.4f\n" % (
                    pos["entrada"], pos["rsi"], pos["ma9"], pos["ma21"]))
                f.write("--- SAÍDA ---\nPreço: %.4f\nRSI: %.2f\nMA9: %.4f\nMA21: %.4f\n" % (
                    preco_saida, a["rsi"], a["ma9"], a["ma21"]))
                f.write("--- RESULTADO ---\nLucro: %.2f%%\nLucro Máximo: %.2f%%\n"
                        "Tempo operação: %ds\n\n\n" % (lucro, lucro_max, int(time.time() - pos["t"])))
        except Exception as e:
            print("Erro ao salvar relatório:", e)

    # ---------- laço principal ----------
    def tentar_travar(self):
        """Garante UM motor só (serviço ou thread local), para nunca operar em dobro."""
        try:
            import fcntl
        except ImportError:
            return True
        try:
            self._fd_trava = open(os.path.join(self.pasta, "motor.lock"), "w")
            fcntl.flock(self._fd_trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
        except Exception:
            return True

    def loop(self, ocioso_max=None):
        """ocioso_max: segundos sem robô ligado e sem comandos até o serviço se encerrar."""
        if not self.tentar_travar():
            print("Motor já está em execução; saindo.")
            return
        with self.lock:
            # prova de vida: quem está rodando e desde quando
            self.est["origem"] = self.origem
            self.est["inicio"] = time.time()
        self.salvar()
        try:
            while not self.parar.is_set():
                try:
                    self.ler_config()
                    self.processar_cmds()
                    agora = time.time()
                    if self.config["robo_ativo"]:
                        self._ativo_em = agora
                        if agora - self._ultimo_ciclo >= 10:
                            self._ultimo_ciclo = agora
                            self.ciclo()
                    if agora - self._ultimo_hb >= 3:
                        self.salvar()
                    if ocioso_max and agora - self._ativo_em > ocioso_max:
                        break
                except Exception as e:
                    print("motor:", e)
                self.parar.wait(1.0)
        finally:
            with self.lock:
                self.est["origem"] = ""      # motor parou (ex.: ociosidade)
            self.salvar()
            if self._fd_trava:
                try:
                    self._fd_trava.close()
                except Exception:
                    pass
