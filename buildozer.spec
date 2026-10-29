[app]

title = BinanceBot

package.name = binancebot

package.domain = org.bot

source.dir = .

source.include_exts = py,png,jpg,kv,atlas

version = 1.1

# Removidos ccxt e cryptography para evitar incompatibilidades de binárias C/Rust no Android
requirements = hostpython3==3.11.0,python3==3.11.0,cython==0.29.36,kivy==2.3.0,requests,urllib3,chardet,certifi,idna

orientation = portrait

fullscreen = 0

# FOREGROUND_SERVICE / WAKE_LOCK: serviço em segundo plano (como um player de música)
# POST_NOTIFICATIONS: notificação fixa do serviço (Android 13+)
# REQUEST_IGNORE_BATTERY_OPTIMIZATIONS: pedir ao Android para não limitar o app por bateria
android.permissions = INTERNET,FOREGROUND_SERVICE,WAKE_LOCK,POST_NOTIFICATIONS,REQUEST_IGNORE_BATTERY_OPTIMIZATIONS

# Serviço do robô: roda em outro processo, em primeiro plano, e é reiniciado pelo Android se for morto.
# Se um dia subir android.api para 34 ou mais, acrescente ":foregroundServiceType=dataSync"
# no fim da linha abaixo e a permissão FOREGROUND_SERVICE_DATA_SYNC.
services = botservice:service.py:foreground:sticky

# Configurações do SDK/NDK
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

# FORÇA O BUILD A GERAR APK (E NÃO O .AAB DA PLAY STORE)
android.release_artifact = apk

[buildozer]

log_level = 2

warn_on_root = 1
