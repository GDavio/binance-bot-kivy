[app]

title = BinanceBot

package.name = binancebot

package.domain = org.bot

source.dir = .

source.include_exts = py,png,jpg,kv,atlas

version = 1.1

requirements = hostpython3==3.11.0,python3==3.11.0,cython==0.29.36,kivy==2.3.0,requests,urllib3,chardet,certifi,idna

orientation = portrait

fullscreen = 0

android.permissions = INTERNET,FOREGROUND_SERVICE,WAKE_LOCK,POST_NOTIFICATIONS,REQUEST_IGNORE_BATTERY_OPTIMIZATIONS

services = botservice:service.py:foreground:sticky

# Configurações do SDK/NDK
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

android.release_artifact = apk

# CORREÇÃO: Habilita AndroidX e adiciona a dependência do FileProvider
android.enable_androidx = True
android.gradle_dependencies = androidx.core:core:1.12.0

[buildozer]

log_level = 2

warn_on_root = 1
