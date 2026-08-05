[app]
title = BinanceBot
package.name = binancebot
package.domain = org.bot
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0

# Trava explicitamente o Python em 3.11 para evitar a versão experimental 3.14
requirements = python3==3.11.0,kivy==2.3.0,requests,urllib3,chardet,certifi,idna

orientation = portrait
fullscreen = 0
android.permissions = INTERNET

android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
