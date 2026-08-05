[app]
title = BinanceBot
package.name = binancebot
package.domain = org.bot
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0

# Trava a receita do Python no python-for-android em 3.11
requirements = python3==3.11.0,kivy==2.3.0,requests,urllib3,chardet,certifi,idna

orientation = portrait
fullscreen = 0
android.permissions = INTERNET

# Configurações de API e NDK sincronizadas com a versão r25b
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

# Força o p4a a usar a branch de release estável com suporte a Python 3.11
p4a.branch = release-2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
