import json
import os
import random
import threading
import time
from datetime import datetime
from kivy.utils import platform

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

import importlib
import core
importlib.reload(core)
from core import *

BG = (0.071, 0.075, 0.090, 1)
NAV = (0.067, 0.071, 0.086, 1)
CARD = (0.105, 0.115, 0.150, 1)
CARD2 = (0.140, 0.150, 0.190, 1)
YELLOW = (0.941, 0.725, 0.050, 1)
GREEN = (0.070, 0.800, 0.520, 1)
RED = (0.970, 0.270, 0.350, 1)
TEXT = (1, 1, 1, 1)
MUTED = (0.55, 0.58, 0.65, 1)
DARK = (0.10, 0.10, 0.10, 1)
BLUE = (0.25, 0.40, 1.00, 1)
CLEAR = (0, 0, 0, 0)

PARES = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"]
TF_LISTA = ["1m", "5m", "15m", "1h", "4h", "1D"]
TF_API = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1D": "1d"}

DICAS = {
    "stop_pct": "É o limite máximo de perda que você aceita. Se o preço cair isso, o robô vende para não virar prejuízo grande.",
    "stop_max": "Quando a volatilidade (ATR) está muito alta, o robô ajusta o Stop Loss para não ser parado pelo ruído. Esse é o teto máximo que ele pode chegar.",
    "trail_ativa": "Se a operação subir esse valor (ex: 0.80%), o robô liga a escada de proteção de lucro.",
    "trail_trava": "Depois que a escada liga, se o preço começar a cair de volta, o robô vende travando pelo menos esse valor de lucro.",
    "corpo_estouro": "Para o rompimento ser válido, a vela de explosão precisa ter um corpo (tamanho) X vezes maior que a média das velas anteriores.",
    "vol_estouro": "A vela de rompimento precisa ter um volume X vezes maior que a média de volume dos últimos candles.",
    "n_compressao": "Quantos candles para trás o robô olha para ver se o preço está parado (formando a caixinha/compressão).",
    "atr_compressao": "A faixa de preço da compressão não pode ser maior que X vezes o ATR. Garante que está realmente parado.",
    "atr_esticado": "O robô não compra se o preço já subiu muito de uma vez (esticou). Se o preço estiver mais de X vezes o ATR acima da EMA21, o robô espera cair um pouco.",
    "sec_rev": "Saída por Reversão: O mercado estava subindo, mas de repente despencou com força. O robô sai para não tomar prejuízo.",
    "sec_trend": "Saída por Perda de Tendência: A tendência de alta perdeu força (EMA9 cruzou para baixo da EMA21). O robô encerra a operação antes do Stop Loss bater."
}

def txt(texto, tam=14, cor=TEXT, bold=False, halign="left", altura=None, **kw):
    lb = Label(text=texto, font_size=sp(tam), color=cor, bold=bold,
               halign=halign, valign="middle", **kw)
    lb.bind(size=lambda w, s: setattr(w, "text_size", s))
    if altura:
        lb.size_hint_y = None
        lb.height = dp(altura)
    return lb

def txt_auto(texto="", tam=15, cor=TEXT):
    lb = Label(text=texto, font_size=sp(tam), color=cor, halign="left", valign="top",
               size_hint_y=None)
    lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    lb.bind(texture_size=lambda w, s: setattr(w, "height", s[1]))
    return lb

class Card(BoxLayout):
    def __init__(self, bg=CARD, radius=16, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            self._c = Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size

class ClickCard(Card):
    def __init__(self, cb=None, **kw):
        super().__init__(**kw)
        self.cb = cb

    def on_touch_up(self, touch):
        if (self.cb and self.collide_point(*touch.pos)
                and abs(touch.y - touch.oy) < dp(12) and abs(touch.x - touch.ox) < dp(12)):
            self.cb(self)
        return super().on_touch_up(touch)

class RoundBtn(Button):
    def __init__(self, bg=CARD2, fg=TEXT, radius=22, **kw):
        kw.setdefault("background_normal", "")
        kw.setdefault("background_down", "")
        kw.setdefault("bold", True)
        super().__init__(background_color=CLEAR, color=fg, **kw)
        with self.canvas.before:
            self._c = Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size

    def estilo(self, bg, fg=None):
        self._c.rgba = bg
        if fg is not None:
            self.color = fg

def auto_altura(w):
    w.size_hint_y = None
    w.bind(minimum_height=w.setter("height"))
    return w

def rolavel(box):
    auto_altura(box)
    sv = ScrollView(do_scroll_x=False, bar_width=0)
    sv.add_widget(box)
    return sv

def chip_row(opcoes, selecionado, ao_escolher, altura=44, tam=13):
    row = BoxLayout(size_hint_y=None, height=dp(altura), spacing=dp(8))
    botoes = {}

    def pintar(sel):
        for k, b in botoes.items():
            b.estilo(YELLOW if k == sel else CARD2, DARK if k == sel else TEXT)

    def clicou(inst):
        pintar(inst.text)
        ao_escolher(inst.text)

    for o in opcoes:
        b = RoundBtn(text=o, font_size=sp(tam))
        b.bind(on_release=clicou)
        botoes[o] = b
        row.add_widget(b)
    pintar(selecionado)
    return row

def toggle_row(opcoes, ativas, ao_mudar, altura=46, tam=13, minimo=1):
    ativas = set(ativas)
    row = BoxLayout(size_hint_y=None, height=dp(altura), spacing=dp(8))
    botoes = {}

    def pintar():
        for k, b in botoes.items():
            lig = k in ativas
            b.estilo(YELLOW if lig else CARD2, DARK if lig else TEXT)

    def clicou(inst):
        k = inst._chave
        if k in ativas:
            if len(ativas) <= minimo:
                return
            ativas.discard(k)
        else:
            ativas.add(k)
        pintar()
        ao_mudar([x for x, _ in opcoes if x in ativas])

    for k, rot in opcoes:
        b = RoundBtn(text=rot, font_size=sp(tam))
        b._chave = k
        b.bind(on_release=clicou)
        botoes[k] = b
        row.add_widget(b)
    pintar()
    return row

def tag(texto, vermelha=False):
    w = dp(26 + 7.4 * len(texto))
    c = Card(bg=RED if vermelha else CARD2, radius=15, size_hint=(None, None), size=(w, dp(30)))
    c.add_widget(txt(texto, 12, TEXT, True, "center"))
    return c

def campo_numero(valor, tam=22):
    return TextInput(text=str(valor), input_filter="float", multiline=False,
                     font_size=sp(tam), size_hint_y=None, height=dp(56),
                     padding=[dp(16), dp(14), dp(16), dp(10)],
                     background_normal="", background_active="", background_color=CARD2,
                     foreground_color=TEXT, cursor_color=YELLOW)

def campo_texto(valor="", hint="", tam=18, senha=False):
    return TextInput(text=valor, hint_text=hint, multiline=False, write_tab=False, password=senha,
                     font_size=sp(tam), size_hint_y=None, height=dp(52),
                     padding=[dp(14), dp(13), dp(14), dp(8)],
                     background_normal="", background_active="", background_color=CARD2,
                     foreground_color=TEXT, hint_text_color=MUTED, cursor_color=YELLOW)

class CandleChart(Widget):
    N = 70

    def __init__(self, **kw):
        super().__init__(**kw)
        self.candles = []
        self.bind(pos=self.redraw, size=self.redraw)

    def set_data(self, candles):
        self.candles = candles
        self.redraw()

    def _t(self, s, x, y, cor=MUTED, tam=11, dir_="l"):
        lb = CoreLabel(text=s, font_size=sp(tam))
        lb.refresh()
        tx = lb.texture
        Color(*cor)
        px = x if dir_ == "l" else x - tx.width
        Rectangle(texture=tx, pos=(px, y - tx.height / 2), size=tx.size)

    def redraw(self, *a):
        self.canvas.clear()
        if not self.candles or self.width < 20:
            return
        full = self.candles
        closes = [c[3] for c in full]
        m9, m21, rs = serie_ma(closes, 9), serie_ma(closes, 21), serie_rsi(closes)
        vis = full[-self.N:]
        off = len(full) - len(vis)
        x0, y0 = self.pos
        w, h = self.size
        xa, xb = x0 + dp(58), x0 + w - dp(6)
        h_rsi = h * 0.2
        r0 = y0 + dp(8)
        p0 = r0 + h_rsi + dp(22)
        p1 = y0 + h - dp(8)
        lo = min(c[2] for c in vis)
        hi = max(c[1] for c in vis)
        if hi == lo:
            hi, lo = hi + 1, lo - 1
        pad = (hi - lo) * 0.05
        lo, hi = lo - pad, hi + pad

        def py(v):
            return p0 + (v - lo) / (hi - lo) * (p1 - p0)

        def ry(v):
            return r0 + v / 100.0 * h_rsi

        cw = (xb - xa) / len(vis)

        def cx(i):
            return xa + (i + 0.5) * cw

        with self.canvas:
            for k in range(5):
                v = lo + (hi - lo) * k / 4.0
                Color(1, 1, 1, 0.06)
                Line(points=[xa, py(v), xb, py(v)], width=1)
                self._t("%.1f" % v if hi > 100 else "%.5g" % v, xa - dp(6), py(v), MUTED, 11, "r")
            for i, c in enumerate(vis):
                o, hh, ll, cl = c[0], c[1], c[2], c[3]
                Color(*(GREEN if cl >= o else RED))
                Line(points=[cx(i), py(ll), cx(i), py(hh)], width=1)
                top, bot = py(max(o, cl)), py(min(o, cl))
                Rectangle(pos=(cx(i) - cw * 0.35, bot), size=(cw * 0.7, max(top - bot, 1)))
            for serie, cor in ((m9, YELLOW), (m21, BLUE)):
                pts = []
                for i in range(len(vis)):
                    v = serie[off + i]
                    if v:
                        pts += [cx(i), py(v)]
                if len(pts) >= 4:
                    Color(*cor)
                    Line(points=pts, width=1.3)
            self._t("MA9", xa + dp(4), p1 - dp(6), YELLOW, 12)
            self._t("MA21", xa + dp(44), p1 - dp(6), BLUE, 12)
            for nivel, cor in ((70, (0.97, 0.27, 0.35, 0.8)), (30, (0.07, 0.8, 0.52, 0.8))):
                Color(*cor)
                Line(points=[xa, ry(nivel), xb, ry(nivel)], width=1, dash_length=dp(3), dash_offset=dp(3))
                self._t(str(nivel), xa - dp(6), ry(nivel), MUTED, 11, "r")
            pts = []
            for i in range(len(vis)):
                v = rs[off + i]
                if v is not None:
                    pts += [cx(i), ry(v)]
            if len(pts) >= 4:
                Color(*YELLOW)
                Line(points=pts, width=1.3)
            self._t("RSI14", xa + dp(4), ry(100) + dp(10), YELLOW, 12)

class BotApp(App):
    title = "Bot Binance"

    def build(self):
        Window.clearcolor = BG
        self.pasta = pasta_dados()
        self.arq_config = os.path.join(self.pasta, ARQ_CONFIG)
        self.arq_estado = os.path.join(self.pasta, ARQ_ESTADO)
        self.dir_cmds = os.path.join(self.pasta, "cmds")
        os.makedirs(self.dir_cmds, exist_ok=True)
        self.lock = threading.RLock()
        self.estado = self.carregar()
        self.aba, self.sub = "markets", "robo"
        self.par, self.tf = "BTCUSDT", "15m"
        self.valor_ordem = "100"
        self.preco_atual = None
        self.saldo_real = None
        self.lbl_saldo = None
        self.sw_robo = None
        self.lbl_robo_sub = None
        self.msg_robo = ""
        self._silencio = False
        self._ev = None
        self._cli = None
        self._pend = set()
        self._modo_motor = ""
        self._motor_local = None
        self._thr_local = None
        self._bat_pedido = False
        self.bt = {"pares": "BTCUSDT", "estr": list(ESTRATEGIAS), "dias": "7d",
                   "valor": "12", "rev": True, "tend": True,
                   "stop": "%.2f" % STOP_PCT, "stopmax": "%.2f" % PARAMS["stop_max"],
                   "ativa": "%.2f" % TRAIL_ATIVA, "trava": "%.2f" % TRAIL_TRAVA,
                   "modelo": "5m", "funil": False}
        self.filtro_exec = None
        self.exec_box = None
        self.btn_f = {}
        self.painel_params = None # Controle do painel de ajustes

        root = BoxLayout(orientation="vertical")
        root.add_widget(self.montar_barra())
        self.conteudo = BoxLayout(orientation="vertical")
        root.add_widget(self.conteudo)
        root.add_widget(self.montar_nav())
        self.abrir_aba("markets")
        self.pedir_permissoes()
        if self.estado["robo_ativo"]:
            self.garantir_motor()
        Clock.schedule_interval(self.recarregar, 1)
        return root

    def on_stop(self):
        self.salvar()

    def on_pause(self):
        self.salvar()
        return True

    def carregar(self):
        d = dict(estado_servico_padrao(), **config_padrao())
        antigo = ler_json(self.arq_estado, {})
        cfg = ler_json(self.arq_config, None)
        migrando = cfg is None
        if migrando:
            cfg = {k: antigo[k] for k in CONFIG_KEYS if k in antigo}
        for k in CONFIG_KEYS:
            if k in cfg:
                d[k] = cfg[k]
        for k in ESTADO_KEYS:
            if k in antigo:
                d[k] = antigo[k]
        if migrando and d["modo"] == "REAL":
            d["robo_ativo"] = False
        d["cfg"] = dict(config_padrao()["cfg"], **d.get("cfg", {}))
        d["custom_params"] = dict(d.get("custom_params", {}))
        for r in d["regras"]:
            r.setdefault("estrategias", list(ESTRATEGIAS))
        if d.get("modelo") not in PERFIS:
            d["modelo"] = "5m"
        try:
            gravar_json(self.arq_config, {k: d[k] for k in CONFIG_KEYS})
        except Exception as e:
            print("Erro ao gravar config:", e)
        return d

    def salvar(self):
        try:
            with self.lock:
                ok = gravar_json(self.arq_config, {k: self.estado[k] for k in CONFIG_KEYS})
                if not ok:
                    self.aviso("Falha ao salvar config (armazenamento)")
        except Exception as e:
            print("Erro ao salvar config:", e)

    def cliente(self):
        c = self.estado["cfg"]
        if not c["key"] or not c["secret"]:
            return None
        chave = (c["key"], c["secret"], c["testnet"])
        if not self._cli or self._cli[0] != chave:
            self._cli = (chave, Binance(c["key"], c["secret"], c["testnet"]))
        return self._cli[1]

    def hist(self, modo=None):
        modo = modo or self.estado["modo"]
        return self.estado["ia"] if modo == "REAL" else self.estado["ia_sim"]
        
# FIM DA PARTE 1 DO MAIN


    def montar_barra(self):
        self.barra = Card(bg=CARD, radius=0, size_hint_y=None, height=dp(72),
                          padding=[dp(16), dp(8)], spacing=dp(8))
        dot_box = AnchorLayout(anchor_y="center", size_hint_x=None, width=dp(20))
        self.dot = Card(bg=GREEN, radius=6, size_hint=(None, None), size=(dp(12), dp(12)))
        dot_box.add_widget(self.dot)
        info = BoxLayout(orientation="vertical", padding=[0, dp(10)])
        self.lbl_modo = txt("SIMULACAO", 17, TEXT, True)
        self.lbl_modo_sub = txt("", 14, MUTED)
        info.add_widget(self.lbl_modo)
        info.add_widget(self.lbl_modo_sub)
        btn_box = AnchorLayout(anchor_x="right", anchor_y="center", size_hint_x=None, width=dp(150))
        self.btn_modo = RoundBtn(bg=RED, text="Ir p/ Real", font_size=sp(15),
                                 size_hint=(None, None), size=(dp(140), dp(46)))
        self.btn_modo.bind(on_release=self.clicou_modo)
        btn_box.add_widget(self.btn_modo)
        for w in (dot_box, info, btn_box):
            self.barra.add_widget(w)
        self.atualizar_barra()
        return self.barra

    def atualizar_barra(self):
        est = self.estado
        if est["modo"] == "SIM":
            self.lbl_modo.text = "SIMULACAO"
            self.lbl_modo_sub.text = "$%.2f USDT ficticio" % est["saldo"]
            self.dot._c.rgba = GREEN
            self.btn_modo.text = "Ir p/ Real"
            self.btn_modo.estilo(RED, TEXT)
        else:
            rede = "Testnet" if est["cfg"]["testnet"] else "Conta real"
            self.lbl_modo.text = "REAL"
            self.lbl_modo_sub.text = ("$%.2f USDT . %s" % (self.saldo_real, rede)
                                      if self.saldo_real is not None else rede)
            self.dot._c.rgba = RED
            self.btn_modo.text = "Ir p/ Simulacao"
            self.btn_modo.estilo(CARD2, TEXT)
        self.atualizar_saldo_label()

    def atualizar_saldo_real(self):
        ex = self.cliente()
        if self.estado["modo"] != "REAL" or not ex:
            return

        def job():
            try:
                v = ex.saldo_livre().get("USDT", 0.0)
            except Exception as e:
                print("saldo:", e)
                return

            def ui(dt):
                self.saldo_real = v
                self.atualizar_barra()

            Clock.schedule_once(ui)

        threading.Thread(target=job, daemon=True).start()

    def clicou_modo(self, *a):
        if self.estado["modo"] == "SIM":
            self.confirmar_real()
        else:
            self.trocar_modo("SIM")

    def trocar_modo(self, modo):
        self.desligar_robo()
        self.estado["modo"] = modo
        self.salvar()
        self.atualizar_barra()
        if modo == "REAL":
            if not self.cliente():
                self.aviso("Configure as chaves da Binance em Ajustes (aba Markets).")
            else:
                self.atualizar_saldo_real()

    def confirmar_real(self):
        box = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        box.add_widget(txt("!", 38, RED, True, "center", altura=48))
        box.add_widget(txt("Ativar Modo Real?", 22, TEXT, True, "center", altura=36))
        box.add_widget(txt("Ordens em modo real usarao as chaves da Binance configuradas em "
                           "Ajustes. Por padrao, o app opera na Testnet. Confirme para continuar.",
                           14, MUTED, False, "center"))
        linha = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(12))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.92, None),
                    height=dp(360), background="", background_color=CARD, auto_dismiss=False)
        b1 = RoundBtn(bg=CARD2, text="Cancelar", font_size=sp(15), radius=14)
        b2 = RoundBtn(bg=RED, text="Ativar Real", font_size=sp(15), radius=14)
        b1.bind(on_release=lambda *_: pop.dismiss())

        def ativar(*_):
            pop.dismiss()
            self.trocar_modo("REAL")

        b2.bind(on_release=ativar)
        linha.add_widget(b1)
        linha.add_widget(b2)
        box.add_widget(linha)
        pop.open()

    def aviso(self, msg):
        pop = Popup(title="", separator_height=0, size_hint=(0.85, None), height=dp(140),
                    content=txt(msg, 15, TEXT, False, "center"), background="", background_color=CARD2)
        pop.open()
        Clock.schedule_once(lambda dt: pop.dismiss(), 2.8)

    def aviso_thread(self, msg):
        Clock.schedule_once(lambda dt: self.aviso(msg))

    def ajustes(self):
        c = self.estado["cfg"]
        box = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))
        box.add_widget(txt("Ajustes . Binance", 20, TEXT, True, altura=32))
        box.add_widget(txt("API Key", 13, MUTED, altura=18))
        k = campo_texto(c["key"], "API Key", 15, senha=True)
        box.add_widget(k)
        box.add_widget(txt("Secret Key", 13, MUTED, altura=18))
        s = campo_texto(c["secret"], "Secret Key", 15, senha=True)
        box.add_widget(s)
        linha = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        linha.add_widget(txt("Usar Testnet (recomendado)", 15, TEXT))
        sw = Switch(active=c["testnet"], size_hint=(None, None), size=(dp(90), dp(46)))
        linha.add_widget(sw)
        box.add_widget(linha)
        botoes = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(12))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.94, None),
                    height=dp(470), background="", background_color=CARD)
        b1 = RoundBtn(bg=CARD2, text="Cancelar", radius=14)
        b2 = RoundBtn(bg=YELLOW, fg=DARK, text="Salvar", radius=14)
        b1.bind(on_release=lambda *_: pop.dismiss())

        def salvar(*_):
            self.estado["cfg"] = {"key": k.text.strip(), "secret": s.text.strip(),
                                  "testnet": bool(sw.active)}
            self.saldo_real = None
            self.salvar()
            pop.dismiss()
            self.atualizar_barra()
            self.atualizar_saldo_real()

        b2.bind(on_release=salvar)
        botoes.add_widget(b1)
        botoes.add_widget(b2)
        box.add_widget(botoes)
        pop.open()

    def montar_nav(self):
        nav = Card(bg=NAV, radius=0, size_hint_y=None, height=dp(64), padding=dp(4))
        self.nav_btns = {}
        for chave, rotulo in (("markets", "MARKETS"), ("trade", "TRADE"), ("bot", "BOT . IA")):
            b = RoundBtn(bg=CLEAR, fg=MUTED, text=rotulo, font_size=sp(13), radius=0)
            b.bind(on_release=lambda inst, k=chave: self.abrir_aba(k))
            self.nav_btns[chave] = b
            nav.add_widget(b)
        return nav

    def abrir_aba(self, nome):
        if self._ev:
            self._ev.cancel()
            self._ev = None
        self.aba = nome
        self.lbl_saldo = None
        self.sw_robo = None
        self.lbl_robo_sub = None
        for k, b in self.nav_btns.items():
            b.estilo(CLEAR, YELLOW if k == nome else MUTED)
        self.conteudo.clear_widgets()
        if nome == "markets":
            self.tela_markets()
        elif nome == "trade":
            self.tela_trade()
            self.atualizar_saldo_real()
        else:
            self.tela_bot()

    def tela_markets(self):
        box = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(10))
        topo = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        topo.add_widget(txt("Mercado Spot", 30, TEXT, True))
        aj = RoundBtn(bg=CARD2, text="Ajustes", font_size=sp(14), size_hint=(None, None),
                      size=(dp(96), dp(42)))
        aj.bind(on_release=lambda *_: self.ajustes())
        topo.add_widget(aj)
        box.add_widget(topo)
        box.add_widget(txt("Precos em tempo real . Binance", 14, MUTED, altura=22))

        busca = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.inp_busca = campo_texto("", "Digite qualquer par (ex.: PEPEUSDT)", 16)
        self.inp_busca.bind(on_text_validate=lambda *_: self.abrir_busca())
        ok = RoundBtn(bg=YELLOW, fg=DARK, text="Abrir", font_size=sp(15), size_hint_x=None,
                      width=dp(86), radius=14)
        ok.bind(on_release=lambda *_: self.abrir_busca())
        busca.add_widget(self.inp_busca)
        busca.add_widget(ok)
        box.add_widget(busca)

        lista = BoxLayout(orientation="vertical", spacing=dp(12), padding=[0, dp(6)])
        self.linhas = {}
        for p in PARES:
            card = ClickCard(cb=lambda c, s=p: self.ir_trade(s), bg=CARD, radius=16,
                             size_hint_y=None, height=dp(84), padding=dp(16))
            esq = BoxLayout(orientation="vertical")
            esq.add_widget(txt(par_fmt(p), 18, TEXT, True))
            lv = txt("Vol 24h: -", 13, MUTED)
            esq.add_widget(lv)
            dir_ = BoxLayout(orientation="vertical")
            lp = txt("-", 18, TEXT, True, "right")
            dir_.add_widget(lp)
            ancora = AnchorLayout(anchor_x="right", anchor_y="center")
            pill = Card(bg=CARD2, radius=10, size_hint=(None, None), size=(dp(90), dp(28)))
            lc = txt("", 13, TEXT, True, "center")
            pill.add_widget(lc)
            ancora.add_widget(pill)
            dir_.add_widget(ancora)
            card.add_widget(esq)
            card.add_widget(dir_)
            lista.add_widget(card)
            self.linhas[p] = (lp, lv, pill, lc)
        box.add_widget(rolavel(lista))
        self.conteudo.add_widget(box)
        self.atualizar_markets()
        self._ev = Clock.schedule_interval(lambda dt: self.atualizar_markets(), 3)

    def abrir_busca(self):
        sym = normalizar_par(self.inp_busca.text)
        if sym:
            self.ir_trade(sym)

    def atualizar_markets(self):
        def job():
            try:
                dados = buscar_tickers(PARES)
            except Exception as e:
                print("markets:", e)
                return
            Clock.schedule_once(lambda dt: self.pintar_markets(dados))

        threading.Thread(target=job, daemon=True).start()

    def pintar_markets(self, dados):
        if self.aba != "markets":
            return
        for d in dados:
            ref = self.linhas.get(d["symbol"])
            if not ref:
                continue
            lp, lv, pill, lc = ref
            var = float(d["priceChangePercent"])
            lp.text = fmt_preco(float(d["lastPrice"]))
            lv.text = "Vol 24h: " + fmt_vol(float(d["quoteVolume"]))
            lc.text = "%s%.2f%%" % ("+" if var >= 0 else "", var)
            pill._c.rgba = GREEN if var >= 0 else RED

    def ir_trade(self, simbolo):
        self.par = simbolo
        self.preco_atual = None
        self.abrir_aba("trade")

    def tela_trade(self):
        box = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(12))
        linha = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.inp_par = campo_texto(par_fmt(self.par), "Digite qualquer par (ex.: SOLUSDT)", 20)
        self.inp_par.bind(on_text_validate=lambda *_: self.trocou_par(self.inp_par.text))
        ir = RoundBtn(bg=YELLOW, fg=DARK, text="Ir", font_size=sp(16), size_hint_x=None,
                      width=dp(72), radius=14)
        ir.bind(on_release=lambda *_: self.trocou_par(self.inp_par.text))
        linha.add_widget(self.inp_par)
        linha.add_widget(ir)
        box.add_widget(linha)

        info = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.lbl_par = txt(par_fmt(self.par), 22, TEXT, True)
        info.add_widget(self.lbl_par)
        dir_ = BoxLayout(orientation="vertical")
        self.lbl_preco = txt("-", 24, TEXT, True, "right")
        self.lbl_var = txt("", 14, GREEN, True, "right")
        dir_.add_widget(self.lbl_preco)
        dir_.add_widget(self.lbl_var)
        info.add_widget(dir_)
        box.add_widget(info)

        box.add_widget(chip_row(TF_LISTA, self.tf, self.trocou_tf, altura=46, tam=14))

        cg = Card(bg=CARD, radius=20, size_hint_y=None, height=dp(360), padding=dp(6))
        self.chart = CandleChart()
        cg.add_widget(self.chart)
        box.add_widget(cg)

        ordem = Card(bg=CARD, radius=20, orientation="vertical", padding=dp(20), spacing=dp(12))
        ordem.add_widget(txt("Nova Ordem . Market", 22, TEXT, True, altura=32))
        ordem.add_widget(txt("Valor (USDT)", 15, MUTED, altura=22))
        self.inp_valor = campo_numero(self.valor_ordem)
        self.inp_valor.bind(text=lambda w, v: setattr(self, "valor_ordem", v))
        ordem.add_widget(self.inp_valor)
        rapidos = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        for v in (25, 50, 100, 250, 500):
            b = RoundBtn(bg=CARD2, text="$%d" % v, font_size=sp(14))
            b.bind(on_release=lambda inst, x=v: setattr(self.inp_valor, "text", str(x)))
            rapidos.add_widget(b)
        ordem.add_widget(rapidos)
        lin = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(12))
        bc = RoundBtn(bg=GREEN, fg=DARK, text="COMPRAR", font_size=sp(17), radius=16)
        bv = RoundBtn(bg=CARD2, text="VENDER", font_size=sp(17), radius=16)
        bc.bind(on_release=lambda *_: self.enviar_ordem("BUY"))
        bv.bind(on_release=lambda *_: self.enviar_ordem("SELL"))
        lin.add_widget(bc)
        lin.add_widget(bv)
        ordem.add_widget(lin)
        self.lbl_posinfo = txt("", 13, MUTED, False, "center", altura=22)
        ordem.add_widget(self.lbl_posinfo)
        be = RoundBtn(bg=RED, text="ENCERRAR POSICAO", font_size=sp(16), size_hint_y=None,
                      height=dp(52), radius=16)
        be.bind(on_release=lambda *_: self.encerrar([self.par]))
        ordem.add_widget(be)
        self.lbl_saldo = txt("", 14, MUTED, False, "center", altura=24)
        ordem.add_widget(self.lbl_saldo)
        auto_altura(ordem)
        box.add_widget(ordem)

        self.conteudo.add_widget(rolavel(box))
        self.atualizar_saldo_label()
        self.carregar_trade()
        self._ev = Clock.schedule_interval(lambda dt: self.atualizar_trade(), 1)

    def atualizar_saldo_label(self):
        if self.lbl_saldo is None:
            return
        if self.estado["modo"] == "SIM":
            self.lbl_saldo.text = "Saldo virtual: $%.2f USDT" % self.estado["saldo"]
        else:
            self.lbl_saldo.text = "Modo real . ordens a mercado na Binance"

    def trocou_par(self, texto):
        sym = normalizar_par(texto)
        if not sym:
            return
        self.par = sym
        self.preco_atual = None
        self.inp_par.text = par_fmt(sym)
        self.lbl_par.text = par_fmt(sym)
        self.lbl_preco.text = "-"
        self.lbl_var.text = ""
        self.carregar_trade()

    def trocou_tf(self, tf):
        self.tf = tf
        self.carregar_trade()

    def carregar_trade(self):
        self.candles_trade = []
        self._gen = getattr(self, "_gen", 0) + 1
        self._busy = False
        self._tk_ts = 0
        self._var = None
        self.atualizar_trade()

    def atualizar_trade(self):
        if getattr(self, "_busy", False):
            return
        par, tf, gen = self.par, TF_API[self.tf], self._gen
        completo = not self.candles_trade
        pedir_tk = completo or time.time() - self._tk_ts > 10
        self._busy = True

        def job():
            try:
                lote = buscar_klines(par, tf, 100 if completo else 3)
                tk = http_json("/api/v3/ticker/24hr", {"symbol": par}) if pedir_tk else None
            except Exception as e:
                print("trade:", e)

                def erro(dt):
                    self._busy = False
                    if gen == self._gen and self.aba == "trade" and not self.candles_trade:
                        self.lbl_var.text = "par invalido ou sem conexao"
                        self.lbl_var.color = RED

                Clock.schedule_once(erro)
                return

            def ui(dt):
                self._busy = False
                if self.aba != "trade" or gen != self._gen or not lote:
                    return
                if completo:
                    self.candles_trade = list(lote)
                else:
                    for c in lote:
                        if c[4] == self.candles_trade[-1][4]:
                            self.candles_trade[-1] = c
                        elif c[4] > self.candles_trade[-1][4]:
                            self.candles_trade.append(c)
                    del self.candles_trade[:-150]
                if tk:
                    self._tk_ts = time.time()
                    self._var = float(tk["priceChangePercent"])
                self.chart.set_data(self.candles_trade)
                self.preco_atual = self.candles_trade[-1][3]
                self.lbl_preco.text = fmt_preco(self.preco_atual)
                if self._var is not None:
                    self.lbl_var.text = "%s%.2f%%" % ("+" if self._var >= 0 else "", self._var)
                    self.lbl_var.color = GREEN if self._var >= 0 else RED
                self.atualizar_posinfo()

            Clock.schedule_once(ui)

        threading.Thread(target=job, daemon=True).start()

    def manual_d(self, modo=None):
        modo = modo or self.estado["modo"]
        return self.estado["posicoes"] if modo == "SIM" else self.estado["pos_manual_real"]

    def atualizar_posinfo(self):
        lb = getattr(self, "lbl_posinfo", None)
        if self.aba != "trade" or lb is None:
            return
        est = self.estado
        modo, sym = est["modo"], self.par
        pos = (est["pos_sim"] if modo == "SIM" else est["pos_real"]).get(sym)
        man = self.manual_d(modo).get(sym, 0.0)
        partes, cor = [], MUTED
        if pos:
            if self.preco_atual:
                lucro = (self.preco_atual / pos["entrada"] - 1) * 100
                cor = GREEN if lucro >= 0 else RED
                partes.append("Robo: entrada %s . %+.2f%%" % (fmt_preco(pos["entrada"]), lucro))
            else:
                partes.append("Robo: entrada %s" % fmt_preco(pos["entrada"]))
        if man > 1e-12:
            partes.append("Manual: %.6g" % man)
        lb.text = "  |  ".join(partes) if partes else "Sem posicao aberta neste par"
        lb.color = cor

    def enviar_ordem(self, lado):
        try:
            valor = float(self.valor_ordem or 0)
        except ValueError:
            valor = 0
        if valor <= 0 or not self.preco_atual:
            return self.aviso("Informe um valor valido e aguarde o preco carregar.")
        if self.estado["modo"] == "REAL" and not self.cliente():
            return self.aviso("Configure as chaves da Binance em Ajustes.")
        self.enviar_cmd({"tipo": "ordem", "lado": lado, "sym": self.par,
                         "valor": valor, "preco": self.preco_atual})

    def enviar_cmd(self, cmd):
        cid = "%d_%04d" % (int(time.time() * 1000), random.randint(0, 9999))
        cmd["id"] = cid
        self.garantir_motor()
        try:
            gravar_json(os.path.join(self.dir_cmds, cid + ".json"), cmd)
        except Exception as e:
            return self.aviso("Falha ao enviar o comando: %s" % e)
        self._pend.add(cid)

    def tela_bot(self):
        box = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(12))
        sw = Card(bg=CARD, radius=18, size_hint_y=None, height=dp(58), padding=dp(5), spacing=dp(4))
        self.sub_btns = {}
        for chave, rotulo in (("robo", "Robo"), ("ia", "Assistente IA"), ("backtest", "Backtest"), ("manual", "Manual")):
            b = RoundBtn(bg=CLEAR, fg=TEXT, text=rotulo, font_size=sp(14), radius=14)
            b.bind(on_release=lambda inst, k=chave: self.mostrar_sub(k))
            self.sub_btns[chave] = b
            sw.add_widget(b)
        box.add_widget(sw)
        self.sub_area = BoxLayout(orientation="vertical")
        box.add_widget(self.sub_area)
        self.conteudo.add_widget(box)
        self.mostrar_sub(self.sub)

    def mostrar_sub(self, chave):
        if self._ev:
            self._ev.cancel()
            self._ev = None
        self.sub = chave
        self.sw_robo = None
        self.lbl_robo_sub = None
        for k, b in self.sub_btns.items():
            b.estilo(YELLOW if k == chave else CLEAR, DARK if k == chave else TEXT)
        self.sub_area.clear_widgets()
        if chave == "robo":
            self.sub_area.add_widget(self.sub_robo())
        elif chave == "ia":
            self.sub_area.add_widget(self.sub_ia())
        elif chave == "backtest":
            self.sub_area.add_widget(self.sub_backtest())
        else:
            self.sub_area.add_widget(self.sub_manual())

    def _ajuda(self, chave):
        msg = DICAS.get(chave, "Sem informacoes.")
        box = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        box.add_widget(txt("Ajuda", 18, YELLOW, True, altura=28))
        box.add_widget(txt(msg, 15, TEXT, False, "left"))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.85, None),
                    height=dp(250), background="", background_color=CARD)
        btn = RoundBtn(bg=CARD2, text="Entendi", radius=14, size_hint_y=None, height=dp(46))
        btn.bind(on_release=lambda *_: pop.dismiss())
        box.add_widget(btn)
        pop.open()

    def _label_com_ajuda(self, texto, chave_dica):
        lin = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(4))
        lin.add_widget(txt(texto, 14, TEXT))
        if chave_dica:
            b = RoundBtn(bg=CLEAR, fg=BLUE, text="(?)", font_size=sp(14), bold=True,
                         size_hint=(None, None), size=(dp(30), dp(30)), radius=15)
            b.bind(on_release=lambda *_: self._ajuda(chave_dica))
            lin.add_widget(b)
        lin.add_widget(Widget())
        return lin

    def sub_manual(self):
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])
        
        box.add_widget(txt("Manual do Robô", 22, TEXT, True, altura=36))
        box.add_widget(txt("Entenda o que cada função faz.", 14, MUTED, altura=22))
        
        c1 = Card(bg=CARD, radius=16, orientation="vertical", size_hint_y=None, padding=dp(16), spacing=dp(8))
        c1.add_widget(txt("Estratégias de Entrada", 16, YELLOW, True))
        c1.add_widget(txt_auto("Pullback (Correção): O preço estava subindo, recuou um pouco e está voltando a subir. O robô compra na 'promoção'.\n\nContinuidade (Tendência): O preço está em uma bandeira (lateralização) dentro de uma forte tendência de alta. O robô compra quando ele rompe a bandeira.\n\nRompimento (Explosão): O preço ficou comprimido (andando de lado) e de repente explode para cima com força. O robô compra no estouro.", 14, TEXT))
        auto_altura(c1)
        box.add_widget(c1)
        
        c2 = Card(bg=CARD, radius=16, orientation="vertical", size_hint_y=None, padding=dp(16), spacing=dp(8))
        c2.add_widget(txt("Saídas Secundárias", 16, YELLOW, True))
        c2.add_widget(txt_auto("Stop Loss: Limite máximo de perda. Se o preço cair isso, o robô vende para não virar prejuízo grande.\n\nTrailing Stop: Proteção de lucro. Se o preço subir, o robô 'sobe a escada' e trava o lucro, garantindo que você não devolva o dinheiro ganho.\n\nSaída por Reversão: O mercado despencou com força. O robô sai antes do Stop Loss bater.\n\nSaída por Perda de Tendência: A tendência de alta perdeu força. O robô encerra a operação antes do Stop Loss bater.", 14, TEXT))
        auto_altura(c2)
        box.add_widget(c2)
        
        c3 = Card(bg=CARD, radius=16, orientation="vertical", size_hint_y=None, padding=dp(16), spacing=dp(8))
        c3.add_widget(txt("Configurando os Parâmetros", 16, YELLOW, True))
        c3.add_widget(txt_auto("Stop Loss (%): O limite máximo de perda que você aceita.\nTrailing Stop (%): Se a operação subir X%, o robô liga a proteção. Se cair, trava Y% de lucro.\nForça da Vela (x): A vela de rompimento precisa ter um corpo X vezes maior que a média.\nVolume do Rompimento (x): A vela de rompimento precisa ter um volume X vezes maior que a média.\nVelas de Compressão: Quantos candles o robô olha para trás para achar a 'caixinha'.\nATR (Volatilidade): Mede o quão agitado o mercado está.", 14, TEXT))
        auto_altura(c3)
        box.add_widget(c3)
        
        c4 = Card(bg=RED, radius=16, orientation="vertical", size_hint_y=None, padding=dp(16), spacing=dp(8))
        c4.add_widget(txt("Avisos Importantes", 16, TEXT, True))
        c4.add_widget(txt_auto("Risco: O mercado de criptomoedas é altamente volátil. Nenhum robô é 100% garantido.\n\nTaxas da Binance: Cada ordem custa 0.1% (ida e volta = 0.2%). Em timeframes curtos (1m), as taxas podem consumir o lucro.\n\nModo Simulação vs Real: No modo SIMULAÇÃO, você usa um saldo virtual. No modo REAL, o robô usa a sua conta.", 14, TEXT))
        auto_altura(c4)
        box.add_widget(c4)

        return rolavel(box)

    def sub_robo(self):
        est = self.estado
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])

        # --- RESUMO DO DIA (NOVO) ---
        exs_hoje = [e for e in est["execucoes"] if "SELL" in e["t"] and datetime.fromtimestamp(e.get("ts", 0)).date() == datetime.now().date()]
        n_hoje = len(exs_hoje)
        acertos_hoje = len([e for e in exs_hoje if e["ok"]])
        acerto_pct = (acertos_hoje / n_hoje * 100) if n_hoje > 0 else 0
        
        c_resumo = Card(bg=CARD2, radius=18, size_hint_y=None, height=dp(70), padding=dp(16))
        t_res = BoxLayout(orientation="vertical")
        t_res.add_widget(txt("Resumo de Hoje", 16, YELLOW, True))
        t_res.add_widget(txt("Operações: %d  |  Acerto: %.0f%%" % (n_hoje, acerto_pct), 15, TEXT))
        c_resumo.add_widget(t_res)
        box.add_widget(c_resumo)
        # ----------------------------

        c1 = Card(bg=CARD, radius=18, size_hint_y=None, height=dp(86), padding=dp(18))
        t = BoxLayout(orientation="vertical")
        t.add_widget(txt("Robo 24/7", 22, TEXT, True))
        self.lbl_robo_sub = txt("", 14, MUTED)
        t.add_widget(self.lbl_robo_sub)
        c1.add_widget(t)
        self.sw_robo = Switch(active=est["robo_ativo"], size_hint=(None, None), size=(dp(90), dp(46)))
        self.sw_robo.pos_hint = {"center_y": 0.5}
        self.sw_robo.bind(active=self.toggle_robo)
        c1.add_widget(self.sw_robo)
        box.add_widget(c1)
        self.atualizar_sub_robo()
        
        box.add_widget(txt("Perfil do robo", 15, MUTED, altura=22))
        rot_mod = {"5m": "Perfil 5m", "1m": "Perfil 1m"}
        box.add_widget(chip_row(list(rot_mod.values()), rot_mod.get(est.get("modelo", "5m"), "Perfil 5m"),
                                lambda r: self.trocar_modelo("5m" if "5m" in r else "1m"),
                                altura=44, tam=14))
        
        info_p = txt("5m: gatilho 5m, semaforo 15m, EMA 9/21, RSI 14, ATR 14. "
                     "1m: gatilho 1m, semaforo 5m, EMA 9/21, RSI 9, ATR 9.", 13, MUTED)
        info_p.size_hint_y = None
        info_p.height = dp(40)
        box.add_widget(info_p)

        # --- BOTOES DE CONTROLE (NOVO) ---
        controles = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        self.btn_pausar = RoundBtn(bg=YELLOW if est.get("pausado") else CARD2, 
                                   fg=DARK if est.get("pausado") else TEXT, 
                                   text="Retomar Entradas" if est.get("pausado") else "Pausar Entradas", 
                                   font_size=sp(14), radius=14)
        self.btn_pausar.bind(on_release=self.toggle_pausa)
        controles.add_widget(self.btn_pausar)
        
        btn_exportar = RoundBtn(bg=BLUE, fg=TEXT, text="Exportar Relatorio", font_size=sp(14), radius=14)
        btn_exportar.bind(on_release=lambda *_: self.exportar_relatorio())
        controles.add_widget(btn_exportar)
        box.add_widget(controles)
        # ---------------------------------

        box.add_widget(self._label_com_ajuda("Saídas Secundárias", "sec_rev"))
        ativos_sec = []
        custom = est.get("custom_params", {})
        if custom.get("sec_rev", PARAMS.get("sec_rev", True)): ativos_sec.append("rev")
        if custom.get("sec_trend", PARAMS.get("sec_trend", True)): ativos_sec.append("tend")

        def mudou_sec(lst):
            with self.lock:
                cp = self.estado.get("custom_params", {})
                cp["sec_rev"] = "rev" in lst
                cp["sec_trend"] = "tend" in lst
                self.estado["custom_params"] = cp
            self.salvar()

        box.add_widget(toggle_row([("rev", "Reversão Forte"), ("tend", "Perdeu Tendência")], ativos_sec,
                                  mudou_sec, altura=46, tam=13, minimo=0))

        btn_ajustes = RoundBtn(bg=CARD2, text="Ajustes Fino do Robo (Parametros)", font_size=sp(15), size_hint_y=None, height=dp(50), radius=14)
        box.add_widget(btn_ajustes)
        
        self.painel_params = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        auto_altura(self.painel_params)
        
        def get_p(k):
            return str(custom.get(k, PARAMS.get(k, 0)))
            
        campos = [
            ("stop_pct", "Stop Loss Mínimo (%)", get_p("stop_pct"), "stop_pct"),
            ("stop_max", "Stop Loss Máximo (%)", get_p("stop_max"), "stop_max"),
            ("trail_ativa", "Ativar Trailing (%)", get_p("trail_ativa"), "trail_ativa"),
            ("trail_trava", "Travar Lucro Mínimo (%)", get_p("trail_trava"), "trail_trava"),
            ("corpo_estouro", "Força da Vela (x)", get_p("corpo_estouro"), "corpo_estouro"),
            ("vol_estouro", "Volume do Rompimento (x)", get_p("vol_estouro"), "vol_estouro"),
            ("n_compressao", "Velas de Compressão", get_p("n_compressao"), "n_compressao"),
            ("atr_compressao", "Máx. Volatilidade Compressão", get_p("atr_compressao"), "atr_compressao"),
            ("atr_esticado", "Máx. Esticamento (ATR)", get_p("atr_esticado"), "atr_esticado"),
        ]
        
        inputs = {}
        for k, lbl, val, dica in campos:
            self.painel_params.add_widget(self._label_com_ajuda(lbl, dica))
            inp = campo_numero(val, 18)
            self.painel_params.add_widget(inp)
            inputs[k] = inp
            
        btn_salvar_p = RoundBtn(bg=GREEN, fg=DARK, text="Salvar Parametros", font_size=sp(16), size_hint_y=None, height=dp(50), radius=14)
        self.painel_params.add_widget(btn_salvar_p)
        
        def toggle_painel(*_):
            # ALTERADO: O painel agora abre logo abaixo do botao, e nao no final da lista
            if self.painel_params.parent:
                box.remove_widget(self.painel_params)
            else:
                box.add_widget(self.painel_params, index=box.children.index(btn_ajustes))
                
        def salvar_p(*_):
            novo_custom = dict(self.estado.get("custom_params", {}))
            for k, inp in inputs.items():
                try:
                    novo_custom[k] = float(inp.text)
                except ValueError:
                    pass
            with self.lock:
                self.estado["custom_params"] = novo_custom
            self.salvar()
            self.aviso("Parametros salvos! O robo usara os novos valores na proxima operacao.")
            
        btn_ajustes.bind(on_release=toggle_painel)
        btn_salvar_p.bind(on_release=salvar_p)

        topo = BoxLayout(size_hint_y=None, height=dp(48))
        topo.add_widget(txt("Regras (%d)" % len(est["regras"]), 22, TEXT, True))
        bn = RoundBtn(bg=YELLOW, fg=DARK, text="+  Nova", font_size=sp(16),
                      size_hint=(None, None), size=(dp(120), dp(46)))
        bn.bind(on_release=lambda *_: self.nova_regra())
        topo.add_widget(bn)
        box.add_widget(topo)

        for i, r in enumerate(est["regras"]):
            card = Card(bg=CARD, radius=18, orientation="vertical", size_hint_y=None,
                        height=dp(168), padding=dp(16), spacing=dp(8))
            linha = BoxLayout(size_hint_y=None, height=dp(52))
            tt = BoxLayout(orientation="vertical")
            tt.add_widget(txt("Trailing Escada", 20, TEXT, True))
            tt.add_widget(txt("%s . $%s USDT/ordem" % (r["par"], r["valor"]), 15, MUTED))
            linha.add_widget(tt)
            rm = RoundBtn(bg=CLEAR, fg=RED, text="X", font_size=sp(20),
                          size_hint=(None, None), size=(dp(44), dp(44)))
            rm.bind(on_release=lambda inst, idx=i: self.remover_regra(idx))
            linha.add_widget(rm)
            card.add_widget(linha)
            l1 = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
            for e in r["estrategias"]:
                l1.add_widget(tag(ROTULO[e]))
            l1.add_widget(Widget())
            card.add_widget(l1)
            l2 = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
            perfil = PERFIS.get(est.get("modelo", "5m"), PERFIL_5M)
            l2.add_widget(tag("Stop %.1f-%.1f%% (ATR)" % (PARAMS["stop_pct"], PARAMS["stop_max"]), True))
            l2.add_widget(tag("Escada ATR"))
            l2.add_widget(tag("Perfil " + perfil["nome"]))
            l2.add_widget(Widget())
            card.add_widget(l2)
            box.add_widget(card)

        modo = est["modo"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        man = self.manual_d(modo)
        abertas = list(pos_d) + [s for s, q in man.items() if q > 1e-12 and s not in pos_d]
        self.lbl_pnl = {}
        if abertas:
            cab = BoxLayout(size_hint_y=None, height=dp(44))
            cab.add_widget(txt("Posicoes abertas (%d)" % len(abertas), 22, TEXT, True))
            if len(abertas) > 1:
                bt = RoundBtn(bg=RED, text="Encerrar todas", font_size=sp(13), size_hint=(None, None),
                              size=(dp(140), dp(40)), radius=14)
                bt.bind(on_release=lambda *_: self.encerrar(abertas))
                cab.add_widget(bt)
            box.add_widget(cab)
            for s in abertas:
                p = pos_d.get(s)
                c = Card(bg=CARD, radius=16, size_hint_y=None, height=dp(78),
                         padding=[dp(18), dp(10)], spacing=dp(8))
                tt = BoxLayout(orientation="vertical")
                tt.add_widget(txt(par_fmt(s), 18, TEXT, True))
                tt.add_widget(txt(("entrada %s . %s" % (fmt_preco(p["entrada"]), p["tipo"])) if p
                                  else "manual . qtd %.6g" % man[s], 13, MUTED))
                c.add_widget(tt)
                lp = txt("-", 17, MUTED, True, "right", size_hint_x=None, width=dp(80))
                c.add_widget(lp)
                if p:
                    self.lbl_pnl[s] = (lp, p["entrada"])
                b = RoundBtn(bg=RED, text="Encerrar", font_size=sp(13), size_hint=(None, None),
                             size=(dp(92), dp(40)), radius=14)
                b.pos_hint = {"center_y": 0.5}
                b.bind(on_release=lambda inst, x=s: self.encerrar([x]))
                c.add_widget(b)
                box.add_widget(c)

        box.add_widget(txt("Ultimas execucoes", 22, TEXT, True, altura=40))
        exs = est["execucoes"]
        nb = len([e for e in exs if e["t"].startswith("BUY")])
        ns = len([e for e in exs if e["t"].startswith("SELL")])
        filtro = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        self.btn_f = {}
        for k, rot, qtd in (("BUY", "Buy", nb), ("SELL", "Sell", ns)):
            b = RoundBtn(text="%s (%d)" % (rot, qtd), font_size=sp(15))
            b.bind(on_release=lambda inst, kk=k: self.filtrar_exec(kk))
            self.btn_f[k] = b
            filtro.add_widget(b)
        box.add_widget(filtro)
        self.exec_box = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        auto_altura(self.exec_box)
        box.add_widget(self.exec_box)
        self.montar_execucoes()
        self._ult_exec = est["execucoes"][:1]
        if self.lbl_pnl:
            self.atualizar_pnl_posicoes()
            self._ev = Clock.schedule_interval(lambda dt: self.atualizar_pnl_posicoes(), 4)
        return rolavel(box)

    def toggle_pausa(self, *a):
        est = self.estado
        est["pausado"] = not est.get("pausado", False)
        self.salvar()
        if self.btn_pausar:
            self.btn_pausar.text = "Retomar Entradas" if est["pausado"] else "Pausar Entradas"
            self.btn_pausar.estilo(YELLOW if est["pausado"] else CARD2, DARK if est["pausado"] else TEXT)

    def exportar_relatorio(self, *a):
        arq = core.caminho_relatorio(self.pasta)
        if not os.path.exists(arq) or os.path.getsize(arq) == 0:
            return self.aviso("Relatorio vazio ou nao encontrado.")
        try:
            if platform == "android":
                from jnius import autoclass, cast
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                File = autoclass("java.io.File")
                FileProvider = autoclass("androidx.core.content.FileProvider")
                
                context = PythonActivity.mActivity
                f = File(arq)
                
                # Usa o FileProvider para compartilhar o arquivo de forma segura
                uri = FileProvider.getUriForFile(context, context.getPackageName() + ".provider", f)
                
                intent = Intent(Intent.ACTION_SEND)
                intent.setType("text/csv")
                intent.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                
                context.startActivity(Intent.createChooser(intent, "Salvar ou Compartilhar Relatorio"))
            else:
                self.aviso("Exportacao disponivel apenas no Android compilado.")
        except Exception as e:
            self.aviso("Erro ao exportar: %s" % str(e))

    def filtrar_exec(self, lado):
        self.filtro_exec = None if self.filtro_exec == lado else lado
        self.montar_execucoes()

    def montar_execucoes(self):
        if self.exec_box is None:
            return
        for k, b in self.btn_f.items():
            on = self.filtro_exec == k
            b.estilo(YELLOW if on else CARD2, DARK if on else TEXT)
        self.exec_box.clear_widgets()
        exs = [e for e in self.estado["execucoes"]
               if not self.filtro_exec or e["t"].startswith(self.filtro_exec)]
        if not exs:
            self.exec_box.add_widget(txt("Nenhuma execucao%s." % (
                " deste tipo" if self.filtro_exec else " ainda"), 15, MUTED, altura=30))
        for e in exs[:30]:
            c = Card(bg=CARD, radius=16, size_hint_y=None, height=dp(70), padding=[dp(18), dp(10)])
            tt = BoxLayout(orientation="vertical")
            tt.add_widget(txt(e["t"], 18, TEXT, True))
            tt.add_widget(txt(e["s"], 14, MUTED))
            c.add_widget(tt)
            dir_ = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(112))
            dir_.add_widget(txt(e["d"], 19, GREEN if e["ok"] else RED, True, "right"))
            if e.get("ts"):
                dir_.add_widget(txt(time.strftime("%d/%m %H:%M", time.localtime(e["ts"])),
                                    12, MUTED, False, "right"))
            c.add_widget(dir_)
            self.exec_box.add_widget(c)

    def atualizar_pnl_posicoes(self):
        if self.aba != "bot" or self.sub != "robo" or not getattr(self, "lbl_pnl", None):
            return
        syms = list(self.lbl_pnl)

        def job():
            try:
                res = http_json("/api/v3/ticker/price",
                                {"symbols": json.dumps(syms, separators=(",", ":"))})
            except Exception as e:
                print("pnl:", e)
                return
            precos = {r["symbol"]: float(r["price"]) for r in res}

            def ui(dt):
                if self.aba != "bot" or self.sub != "robo":
                    return
                for s, (lb, ent) in self.lbl_pnl.items():
                    if s in precos:
                        v = (precos[s] / ent - 1) * 100
                        lb.text = "%+.2f%%" % v
                        lb.color = GREEN if v >= 0 else RED

            Clock.schedule_once(ui)

        threading.Thread(target=job, daemon=True).start()

    def encerrar(self, simbolos):
        simbolos = list(simbolos)
        if not simbolos:
            return self.aviso("Nenhuma posicao aberta.")
        if self.estado["modo"] == "REAL" and not self.cliente():
            return self.aviso("Configure as chaves da Binance em Ajustes.")
        self.enviar_cmd({"tipo": "encerrar", "simbolos": simbolos})

    def atualizar_sub_robo(self):
        if self.lbl_robo_sub is None:
            return
        est = self.estado
        agora = time.time()
        if self.msg_robo:
            t = self.msg_robo
        elif est["robo_ativo"]:
            idade = agora - est.get("hb", 0)
            if idade > 25 or not est.get("origem"):
                t = "Sem sinal do motor ha %ds (iniciando ou parado)" % int(idade) if est.get("hb") else \
                    "Iniciando o motor..."
            else:
                onde = "Servico" if est["origem"] == "servico" else "Motor local (sem servico)"
                desde = datetime.fromtimestamp(est["inicio"]).strftime("%d/%m %H:%M")
                ciclo = ("ciclo ha %ds" % int(agora - est["ciclo_ts"])) if est.get("ciclo_ts") else "aguardando ciclo"
                t = "%s ativo desde %s . %s . %s" % (
                    onde, desde, ciclo, "simulacao" if est["modo"] == "SIM" else "REAL")
        else:
            t = "Desligado"
        self.lbl_robo_sub.text = t

    def nova_regra(self):
        escolha = list(ESTRATEGIAS)
        custom = self.estado.get("custom_params", {})
        sec_rev = custom.get("sec_rev", PARAMS.get("sec_rev", True))
        sec_trend = custom.get("sec_trend", PARAMS.get("sec_trend", True))
        ativos_sec = []
        if sec_rev: ativos_sec.append("rev")
        if sec_trend: ativos_sec.append("tend")

        box = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(8))
        box.add_widget(txt("Nova regra . Trailing Escada", 19, TEXT, True, altura=32))
        box.add_widget(txt("Par (qualquer um da Binance)", 13, MUTED, altura=18))
        inp = campo_texto("", "ex.: BTCUSDT, PEPEUSDT, ETHBTC", 18)
        box.add_widget(inp)
        box.add_widget(txt("Valor por ordem", 13, MUTED, altura=18))
        val = campo_numero("12", 20)
        box.add_widget(val)
        
        box.add_widget(txt("Estrategias de entrada", 13, MUTED, altura=18))
        box.add_widget(toggle_row([(k, ROTULO[k]) for k in ESTRATEGIAS], escolha,
                                  lambda lst: escolha.__setitem__(slice(None), lst), altura=44, tam=12))

        box.add_widget(txt("Saídas Secundárias", 13, MUTED, altura=18))
        def mudar_sec(lst):
            nonlocal ativos_sec
            ativos_sec = lst
        box.add_widget(toggle_row([("rev", "Reversao forte"), ("tend", "Perdeu tendencia")], ativos_sec,
                                  mudar_sec, altura=44, tam=12, minimo=0))

        msg = txt("", 13, RED, altura=20)
        box.add_widget(msg)
        linha = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(12))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.94, None),
                    height=dp(650), background="", background_color=CARD)
        b1 = RoundBtn(bg=CARD2, text="Cancelar", radius=14)
        b2 = RoundBtn(bg=YELLOW, fg=DARK, text="Salvar", radius=14)
        b1.bind(on_release=lambda *_: pop.dismiss())

        def salvar(*_):
            sym = normalizar_par(inp.text)
            try:
                v = float(val.text)
            except ValueError:
                v = 0
            if not sym or v <= 0:
                msg.text = "Informe um par e um valor validos."
                return
            if any(r["par"] == sym for r in self.estado["regras"]):
                msg.text = "Ja existe uma regra para %s." % sym
                return
            msg.text = "Validando %s..." % sym
            msg.color = MUTED

            def job():
                try:
                    validar_par(sym)
                except Exception:
                    def erro(dt):
                        msg.color = RED
                        msg.text = "Par invalido na Binance: %s" % sym

                    Clock.schedule_once(erro)
                    return

                def ok(dt):
                    with self.lock:
                        self.estado["regras"].append({
                            "par": sym, "valor": int(v) if v == int(v) else v,
                            "estrategias": list(escolha),
                            "sec_rev": "rev" in ativos_sec,
                            "sec_trend": "tend" in ativos_sec})
                    self.salvar()
                    pop.dismiss()
                    self.mostrar_sub("robo")

                Clock.schedule_once(ok)

            threading.Thread(target=job, daemon=True).start()

        b2.bind(on_release=salvar)
        linha.add_widget(b1)
        linha.add_widget(b2)
        box.add_widget(linha)
        pop.open()

    def remover_regra(self, idx):
        with self.lock:
            if 0 <= idx < len(self.estado["regras"]):
                del self.estado["regras"][idx]
        self.salvar()
        self.mostrar_sub("robo")

    def toggle_robo(self, sw, valor):
        if self._silencio:
            return
        self.estado["robo_ativo"] = valor
        self.msg_robo = ""
        self.salvar()
        self.atualizar_sub_robo()
        if valor:
            self.garantir_motor()
            self.pedir_ignorar_bateria()

    def trocar_modelo(self, modelo):
        self.estado["modelo"] = modelo
        self.salvar()
        self.aviso("Perfil do robo: %s (vale para as proximas entradas)." % modelo)

    def desligar_robo(self):
        if not self.estado["robo_ativo"]:
            return
        self.estado["robo_ativo"] = False
        if self.sw_robo is not None:
            self._silencio = True
            self.sw_robo.active = False
            self._silencio = False
        self.atualizar_sub_robo()

    def garantir_motor(self):
        if self._iniciar_servico():
            self._modo_motor = "servico"
            return
        self._modo_motor = "local"
        if self._thr_local is None or not self._thr_local.is_alive():
            self._motor_local = Motor(self.pasta, origem="local")
            self._thr_local = threading.Thread(target=self._motor_local.loop, daemon=True)
            self._thr_local.start()

    def _iniciar_servico(self):
        if platform != "android":
            return False
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            srv = autoclass(act.getPackageName() + ".ServiceBotservice")
            try:
                srv.start(act, "icon", "Bot Trading Binance", "Robo em execucao", "")
            except Exception:
                srv.start(act, "")
            return True
        except Exception as e:
            print("Servico indisponivel, usando motor local:", e)
            return False

    def pedir_permissoes(self):
        if platform != "android":
            return
        try:
            from android.permissions import request_permissions
            request_permissions(["android.permission.POST_NOTIFICATIONS"])
        except Exception as e:
            print("Permissoes:", e)

    def pedir_ignorar_bateria(self):
        if platform != "android" or self._bat_pedido:
            return
        self._bat_pedido = True
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            Context = autoclass("android.content.Context")
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            pkg = act.getPackageName()
            pm = act.getSystemService(Context.POWER_SERVICE)
            if not pm.isIgnoringBatteryOptimizations(pkg):
                it = Intent("android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS")
                it.setData(Uri.parse("package:" + pkg))
                act.startActivity(it)
        except Exception as e:
            print("Bateria:", e)

    def recarregar(self, dt=None):
        novo = ler_json(self.arq_estado, None)
        if not novo:
            return
        est = self.estado

        def foto():
            return (est["execucoes"][:1], sorted(est["pos_sim"]), sorted(est["pos_real"]),
                    dict(est["posicoes"]), dict(est["pos_manual_real"]))

        antes = foto()
        with self.lock:
            for k in ESTADO_KEYS:
                if k in novo:
                    est[k] = novo[k]
        self.msg_robo = est.get("msg_robo", "")
        for cid in list(self._pend):
            if cid in est["resultados"]:
                self._pend.discard(cid)
                self.aviso(str(est["resultados"][cid])[:300])
        self.atualizar_barra()
        self.atualizar_sub_robo()
        if self.aba == "trade":
            self.atualizar_posinfo()
        if foto() != antes:
            if self.aba == "bot" and self.sub == "robo":
                self.mostrar_sub("robo")
            self.atualizar_saldo_real()

    def sub_ia(self):
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])
        c = Card(bg=CARD, radius=18, orientation="vertical", size_hint_y=None,
                 padding=dp(16), spacing=dp(12))
        c.add_widget(txt("Analisar par", 17, TEXT, True, altura=26))
        self.ia_inp = campo_texto("BTCUSDT", "Digite qualquer par (ex.: SOLUSDT)", 18)
        c.add_widget(self.ia_inp)
        bg = RoundBtn(bg=YELLOW, fg=DARK, text="Gerar sinal com IA", font_size=sp(17),
                      size_hint_y=None, height=dp(54), radius=16)
        bg.bind(on_release=lambda *_: self.gerar_sinal())
        c.add_widget(bg)
        auto_altura(c)
        box.add_widget(c)
        self.ia_out = BoxLayout(orientation="vertical", size_hint_y=None)
        auto_altura(self.ia_out)
        box.add_widget(self.ia_out)
        return rolavel(box)

    def mostrar_card(self, destino, msg):
        destino.clear_widgets()
        card = Card(bg=CARD, radius=18, orientation="vertical", padding=dp(18))
        card.add_widget(txt_auto(msg, 15, TEXT))
        auto_altura(card)
        destino.add_widget(card)

    def gerar_sinal(self):
        sym = normalizar_par(self.ia_inp.text)
        if not sym:
            return
        self.ia_inp.text = sym
        self.mostrar_card(self.ia_out, "Analisando %s..." % sym)
        perfil = PERFIS.get(self.estado.get("modelo", "5m"), PERFIL_5M)

        def job():
            try:
                d, i, sin = diagnostico_perfil(sym, perfil)
            except Exception:
                msg = "Nao consegui analisar %s (par invalido, sem conexao ou poucos candles)." % sym
                Clock.schedule_once(lambda dt: self.mostrar_card(self.ia_out, msg))
                return
            inf = sin["info"]
            tipo = escolher_tipo_dia(sin, ESTRATEGIAS)
            linhas = []
            for nm in ESTRATEGIAS:
                linhas.append("%s: %s" % (ROTULO[nm], "SIM" if sin[nm] else "nao - " + sin["falhas"].get(nm, "?")))
            if inf.get("atr_pct"):
                pp = params_atr(inf["atr_pct"])
                linhas.append("Saidas se entrar agora: stop -%.2f%% . trailing ativa %.2f%% / trava %.2f%%"
                              % (pp["stop_pct"], pp["trail_ativa"], pp["trail_trava"]))
            msg = ("%s . perfil %s (ultima vela fechada)\nPreco: %s\nRSI%d: %.1f . ATR: %.3f%% . volume rel.: %.2fx\n"
                   "EMA9: %.4f . EMA21: %.4f . EMA50: %.4f\nVWAP do dia: %s . semaforo %s: %s\n\n%s\n\n"
                   "Sinal: %s\n\nRegras do perfil %s. Nao e recomendacao."
                   % (sym, perfil["nome"], fmt_preco(inf.get("preco", 0)),
                      perfil["rsi_p"], inf.get("rsi", 0), inf.get("atr_pct", 0),
                      inf.get("rvol", 0), inf.get("ema9", 0), inf.get("ema21", 0), inf.get("ema50", 0),
                      fmt_preco(inf["vwap"]) if inf.get("vwap") else "-",
                      perfil["tf_semaforo"],
                      "alta" if inf.get("ctx") else "sem alta", "\n".join(linhas),
                      ("COMPRA (%s)" % tipo) if tipo else "AGUARDAR", perfil["nome"]))
            Clock.schedule_once(lambda dt: self.mostrar_card(self.ia_out, msg))

        threading.Thread(target=job, daemon=True).start()

    def sub_backtest(self):
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])
        c = Card(bg=CARD, radius=18, orientation="vertical", size_hint_y=None,
                 padding=dp(20), spacing=dp(12))
        c.add_widget(txt("Backtest Rapido", 24, TEXT, True, altura=34))
        d = txt("Simula o perfil do robo nos ultimos N dias com candles reais. O perfil define o intervalo, "
                "os indicadores e o ATR (a volatilidade e medida pelo ATR, sem valor manual).", 15, MUTED)
        d.size_hint_y = None
        d.height = dp(74)
        c.add_widget(d)
        c.add_widget(txt("Par(es) . qualquer par, separados por espaco", 15, MUTED, altura=22))
        self.bt_pares = campo_texto(self.bt["pares"], "ex.: BTCUSDT PEPEUSDT SOLUSDT", 17)
        self.bt_pares.bind(text=lambda w, v: self.bt.__setitem__("pares", v))
        c.add_widget(self.bt_pares)
        c.add_widget(txt("Perfil do robo", 15, MUTED, altura=22))
        rot_bt = {"5m": "Perfil 5m", "1m": "Perfil 1m"}
        c.add_widget(chip_row(list(rot_bt.values()), rot_bt.get(self.bt["modelo"], "Perfil 5m"),
                              lambda r: self.bt.__setitem__("modelo", "5m" if "5m" in r else "1m"),
                              altura=46, tam=14))
        dp_ = txt("5m: gatilho 5m, semaforo 15m, EMA 9/21, RSI 14, ATR 14 + stop/escada por ATR. "
                  "1m: gatilho 1m, semaforo 5m, EMA 9/21, RSI 9, ATR 9 + stop/escada por ATR (max. 30 dias).", 13, MUTED)
        dp_.size_hint_y = None
        dp_.height = dp(70)
        c.add_widget(dp_)
        c.add_widget(txt("Regras de entrada", 15, MUTED, altura=22))
        c.add_widget(toggle_row([(k, ROTULO[k]) for k in ESTRATEGIAS], self.bt["estr"],
                                lambda lst: self.bt.__setitem__("estr", lst), altura=46, tam=13))
        c.add_widget(txt("Periodo", 15, MUTED, altura=22))
        c.add_widget(chip_row(["3d", "7d", "14d", "30d", "60d", "90d"], self.bt["dias"],
                              lambda k: self.bt.__setitem__("dias", k), altura=46, tam=13))
        c.add_widget(txt("Valor por ordem (USDT)", 15, MUTED, altura=22))
        bv = campo_numero(self.bt["valor"], 20)
        bv.bind(text=lambda w, v: self.bt.__setitem__("valor", v))
        c.add_widget(bv)

        av = BoxLayout(orientation="vertical", spacing=dp(10))
        av.add_widget(txt("Saidas secundarias (desligue para testar)", 15, MUTED, altura=22))
        ativos_sec = [k for k, on in (("rev", self.bt["rev"]), ("tend", self.bt["tend"])) if on]

        def mudou_sec(lst):
            self.bt["rev"] = "rev" in lst
            self.bt["tend"] = "tend" in lst

        av.add_widget(toggle_row([("rev", "Reversao forte"), ("tend", "Perdeu tendencia")], ativos_sec,
                                 mudou_sec, altura=46, tam=13, minimo=0))
        for pares_campos in ((("ativa", "Trailing ativa (%)"), ("trava", "Trailing trava (%)")),
                             (("stop", "Stop min. (%)"), ("stopmax", "Stop max. (%)"))):
            lin = BoxLayout(size_hint_y=None, height=dp(80), spacing=dp(8))
            for chave, rot in pares_campos:
                col = BoxLayout(orientation="vertical", spacing=dp(2))
                col.add_widget(txt(rot, 12, MUTED, altura=18))
                f = campo_numero(self.bt[chave], 18)
                f.bind(text=lambda w, v, k=chave: self.bt.__setitem__(k, v))
                col.add_widget(f)
                lin.add_widget(col)
            av.add_widget(lin)

        funil_linha = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(10))
        funil_linha.add_widget(txt("Mostrar funil de filtros", 14, TEXT))
        self.sw_funil = Switch(active=bool(self.bt.get("funil", False)),
                               size_hint=(None, None), size=(dp(80), dp(46)))
        self.sw_funil.bind(active=lambda w, v: self.bt.__setitem__("funil", bool(v)))
        funil_linha.add_widget(self.sw_funil)
        av.add_widget(funil_linha)

        auto_altura(av)
        estado_av = {"aberto": False}
        btn_av = RoundBtn(bg=CARD2, text="Avancado (mostrar)", font_size=sp(15),
                          size_hint_y=None, height=dp(46), radius=14)

        def alternar(*_):
            if estado_av["aberto"]:
                c.remove_widget(av)
                btn_av.text = "Avancado (mostrar)"
            else:
                c.add_widget(av, index=1)
                btn_av.text = "Avancado (ocultar)"
            estado_av["aberto"] = not estado_av["aberto"]

        btn_av.bind(on_release=alternar)
        c.add_widget(btn_av)
        run = RoundBtn(bg=YELLOW, fg=DARK, text="Rodar backtest", font_size=sp(18),
                       size_hint_y=None, height=dp(58), radius=18)
        run.bind(on_release=lambda *_: self.executar_backtest())
        c.add_widget(run)
        auto_altura(c)
        box.add_widget(c)
        self.bt_out = BoxLayout(orientation="vertical", size_hint_y=None)
        auto_altura(self.bt_out)
        box.add_widget(self.bt_out)
        return rolavel(box)

    def executar_backtest(self):
        pares = lista_pares(self.bt["pares"])
        if not pares:
            return self.aviso("Digite ao menos um par.")

        def num(k, padrao):
            try:
                return float(self.bt.get(k) or padrao)
            except ValueError:
                return padrao

        valor = num("valor", 12.0)
        p = {"trail_ativa": num("ativa", TRAIL_ATIVA), "trail_trava": num("trava", TRAIL_TRAVA),
             "stop_pct": num("stop", STOP_PCT), "stop_max": num("stopmax", PARAMS["stop_max"]),
             "sec_rev": bool(self.bt.get("rev", True)), "sec_trend": bool(self.bt.get("tend", True))}
        if p["stop_max"] < p["stop_pct"]:
            return self.aviso("O stop maximo deve ser maior ou igual ao stop minimo.")
        if p["trail_trava"] >= p["trail_ativa"]:
            return self.aviso("O trailing deve travar abaixo de onde ativa (ex.: ativa 0.45, trava 0.25).")
        dias = int(self.bt["dias"][:-1])
        modelo = self.bt.get("modelo", "5m")
        perfil = PERFIS.get(modelo, PERFIL_5M)
        if modelo == "1m" and dias > 30:
            return self.aviso("No Perfil 1m o maximo e 30 dias (sao muitos candles de 1 minuto).")
        ativas = list(self.bt["estr"])
        usar_funil = bool(self.bt.get("funil", False))
        self.mostrar_card(self.bt_out, "Preparando backtest...")

        ultimo_upd = {"t": 0.0}

        def prog(msg, forcar=False):
            agora = time.time()
            if not forcar and agora - ultimo_upd["t"] < 2.0:
                return
            ultimo_upd["t"] = agora
            Clock.schedule_once(lambda dt: self.mostrar_card(self.bt_out, msg))

        def job():
            partes, tot_n, tot_soma = [], 0, 0.0
            
            funil_global = None
            if usar_funil:
                try:
                    funil_global = {"total": 0, "geral": {}, "passou_geral": 0, "estrategia": {"pullback": {}, "continuidade": {}, "rompimento": {}}, "entradas": {"pullback": 0, "continuidade": 0, "rompimento": 0}}
                except Exception as e:
                    Clock.schedule_once(lambda dt: self.mostrar_card(self.bt_out, "ERRO: core.py desatualizado.\n%s" % str(e)))
                    return

            try:
                for i, sym in enumerate(pares):
                    prog("Baixando %s (%d/%d)..." % (sym, i + 1, len(pares)), forcar=True)
                    t0 = time.time()
                    tf_gat = perfil["tf_gatilho"]

                    def cb_download(baixados, estimado, _sym=sym, _i=i):
                        pct = (baixados / estimado * 100) if estimado else 0
                        prog("Baixando %s (%d/%d): %d/%d candles (%.0f%%)" % (
                            _sym, _i + 1, len(pares), baixados, estimado, pct))

                    try:
                        t_ini = int(time.time() * 1000) - dias * 86400000
                        candles = velas_fechadas(
                            baixar_candles(sym, tf_gat, dias + 2, progresso=cb_download),
                            INTERVALO_S[tf_gat])
                    except Exception as e:
                        partes.append("%s: erro no download (%s)" % (sym, str(e)[:80]))
                        continue
                    dt_dl = time.time() - t0
                    if len(candles) < 200:
                        partes.append("%s: poucos candles (%d, par novo ou invalido)." % (sym, len(candles)))
                        continue

                    prog("Processando %s (%d/%d): %d candles baixados em %.1fs..." % (
                        sym, i + 1, len(pares), len(candles), dt_dl), forcar=True)
                    t1 = time.time()

                    def cb_proc(feitos, total, _sym=sym, _i=i):
                        pct = (feitos / total * 100) if total else 0
                        prog("Processando %s (%d/%d): %d/%d velas (%.0f%%)" % (
                            _sym, _i + 1, len(pares), feitos, total, pct))

                    st = {}
                    f_par = {"total": 0, "geral": {}, "passou_geral": 0, "estrategia": {"pullback": {}, "continuidade": {}, "rompimento": {}}, "entradas": {"pullback": 0, "continuidade": 0, "rompimento": 0}} if usar_funil else None

                    try:
                        trades = backtest_perfil(candles, ativas, perfil, p, t_ini, st,
                                                 funil=f_par, progresso_cb=cb_proc)
                    except Exception as e:
                        import traceback
                        partes.append("%s: erro no processamento.\n%s" % (sym, traceback.format_exc()[-200:]))
                        continue
                    
                    dt_proc = time.time() - t1

                    resumo_par = []
                    if not trades:
                        resumo_par.append("%s: nenhuma operacao. (download %.1fs . processamento %.1fs)" % (
                            sym, dt_dl, dt_proc))
                    else:
                        n, soma, texto = resumo_trades(trades, valor, dias)
                        if st.get("stop_medio"):
                            texto += "\nATR%d %s medio %.2f%% . stop medio %.2f%% . trailing ativa media %.2f%%" % (
                                perfil["atr_p"], perfil["tf_gatilho"], st["atr_medio"],
                                st["stop_medio"], st["ativa_media"])
                        texto += "\nTempo: download %.1fs . processamento %.1fs" % (dt_dl, dt_proc)
                        tot_n += n
                        tot_soma += soma
                        resumo_par.append("%s . %s" % (sym, texto))

                    if f_par is not None:
                        try:
                            # Calcula o funil manual aqui mesmo (sem depender do core.py)
                            linhas_f = ["FUNIL (%s, %sd) - %d candles avaliados" % (perfil["nome"], dias, f_par["total"])]
                            for motivo, qtd in sorted(f_par["geral"].items(), key=lambda x: -x[1]):
                                linhas_f.append("  [geral] %s: %d (%.1f%%)" % (motivo, qtd, (qtd/f_par["total"]*100) if f_par["total"] else 0))
                            pg = f_par["passou_geral"]
                            linhas_f.append("  -> passaram do geral: %d (%.1f%%)" % (pg, (pg/f_par["total"]*100) if f_par["total"] else 0))
                            base = pg if pg > 0 else 1
                            for est in ESTRATEGIAS:
                                motivos = f_par["estrategia"].get(est, {})
                                entrada_n = f_par["entradas"].get(est, 0)
                                if not motivos and not entrada_n:
                                    continue
                                linhas_f.append("  %s: %d entradas" % (ROTULO[est], entrada_n))
                                for motivo, qtd in sorted(motivos.items(), key=lambda x: -x[1]):
                                    linhas_f.append("    - %s: %d (%.1f%%)" % (motivo, qtd, (qtd/base*100)))
                            resumo_par.append("\n".join(linhas_f))
                            
                            # Acumula no global
                            funil_global["total"] += f_par["total"]
                            funil_global["passou_geral"] += f_par["passou_geral"]
                            for k, v in f_par["geral"].items():
                                funil_global["geral"][k] = funil_global["geral"].get(k, 0) + v
                            for est in ESTRATEGIAS:
                                for k, v in f_par["estrategia"].get(est, {}).items():
                                    funil_global["estrategia"][est][k] = funil_global["estrategia"][est].get(k, 0) + v
                                funil_global["entradas"][est] += f_par["entradas"].get(est, 0)
                                
                        except Exception as e:
                            resumo_par.append("Erro no funil: %s" % str(e))
                    partes.append("\n".join(resumo_par))

                sec = "Reversao forte %s . Perdeu tendencia %s" % (
                    "ON" if p["sec_rev"] else "OFF", "ON" if p["sec_trend"] else "OFF")
                cab = ("Perfil %s (gatilho %s, semaforo %s; EMA 9/21, RSI %d, ATR %d; stop/escada por ATR) . %s . %d dias\n"
                       "ATR%d min. %.2f%% . stop %.2f-%.2f%% . trailing ativa >= %.2f%% / trava >= %.2f%%\n%s . ordem %.0f USDT\n\n" % (
                           perfil["nome"], perfil["tf_gatilho"], perfil["tf_semaforo"],
                           perfil["rsi_p"], perfil["atr_p"],
                           " ".join(ROTULO[e] for e in ativas), dias, perfil["atr_p"], perfil["vol_min"],
                           p["stop_pct"], p["stop_max"], p["trail_ativa"], p["trail_trava"], sec, valor))

                if len(pares) > 1 and tot_n:
                    liq = tot_soma - TAXA_IDA_VOLTA * tot_n
                    partes.append("TOTAL: %d op . soma %+.2f%% (aprox %+.2f USDT) . c/ taxa %+.2f%% (aprox %+.2f USDT)" % (
                        tot_n, tot_soma, valor * tot_soma / 100, liq, valor * liq / 100))

                if usar_funil and len(pares) > 1 and funil_global and funil_global["total"] > 0:
                    try:
                        linhas_g = ["=== FUNIL CONSOLIDADO ==="]
                        linhas_g.append("FUNIL (%s, %sd) - %d candles avaliados" % (perfil["nome"], dias, funil_global["total"]))
                        for motivo, qtd in sorted(funil_global["geral"].items(), key=lambda x: -x[1]):
                            linhas_g.append("  [geral] %s: %d (%.1f%%)" % (motivo, qtd, (qtd/funil_global["total"]*100)))
                        pg = funil_global["passou_geral"]
                        linhas_g.append("  -> passaram do geral: %d (%.1f%%)" % (pg, (pg/funil_global["total"]*100)))
                        partes.append("\n".join(linhas_g))
                    except Exception:
                        pass

                rodape = "\n\nExecucao no fechamento do candle; stop/trailing checados na minima. " \
                         "Sem filtro de IA e sem slippage."
                msg = cab + "\n\n".join(partes) + rodape
                Clock.schedule_once(lambda dt: self.mostrar_card(self.bt_out, msg))

            except Exception as e:
                import traceback
                erro_msg = "ERRO NO BACKTEST:\n\n%s\n\n%s" % (str(e), traceback.format_exc()[-400:])
                Clock.schedule_once(lambda dt: self.mostrar_card(self.bt_out, erro_msg))

        threading.Thread(target=job, daemon=True).start()


if __name__ == "__main__":
    BotApp().run()
