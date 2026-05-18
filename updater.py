import os
import sys
import json
import time
import subprocess
import urllib.request
import logging

# Замените эти ссылки на ваши реальные!
# Это может быть RAW-ссылка на файл в GitHub, GitLab или локальном сервере.
VERSION_URL = "https://raw.githubusercontent.com/YourName/MInstAll/main/version.json"
DOWNLOAD_URL = "https://github.com/YourName/MInstAll/releases/latest/download/MInstAll.exe"

def check_for_updates(current_version):
    """
    Проверяет наличие новой версии на сервере.
    Возвращает (has_update, latest_version_string).
    """
    try:
        # Делаем короткий таймаут, чтобы программа не зависала, если нет интернета
        req = urllib.request.Request(VERSION_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            latest_version = data.get("version", 0)
            
            if latest_version > current_version:
                return True, latest_version
            return False, latest_version
    except Exception as e:
        logging.warning(f"Не удалось проверить обновления: {e}")
        return False, current_version

def download_and_update(main_window_callback=None):
    """
    Скачивает новый файл и запускает процесс подмены.
    main_window_callback - функция для передачи прогресса в GUI.
    """
    if getattr(sys, 'frozen', False):
        current_exe = sys.executable
    else:
        # Если запускаем скрипт из исходников (.py), обновлять нечего
        return False

    exe_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    new_exe_path = os.path.join(exe_dir, exe_name + ".new")
    bat_path = os.path.join(os.environ.get('TEMP', exe_dir), "minstall_updater.bat")

    try:
        if main_window_callback:
            main_window_callback("Скачивание обновления...")
            
        # Скачиваем новый файл
        req = urllib.request.Request(DOWNLOAD_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response, open(new_exe_path, 'wb') as out_file:
            out_file.write(response.read())

        if main_window_callback:
            main_window_callback("Установка обновления...")

        # Формируем BAT-файл для подмены
        # ping используется как хак для паузы в 2 секунды (timeout не всегда работает в старых Windows)
        bat_content = f"""@echo off
echo Обновление MInstAll... Пожалуйста, подождите.
ping 127.0.0.1 -n 3 > nul
del "{current_exe}"
ren "{new_exe_path}" "{exe_name}"
start "" "{current_exe}"
del "%~f0"
"""
        with open(bat_path, "w", encoding="cp866") as f:
            f.write(bat_content)

        # Запускаем BAT-файл в скрытом режиме и выходим
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            bat_path, 
            creationflags=CREATE_NO_WINDOW, 
            shell=True,
            cwd=exe_dir
        )
        
        logging.info("Передача управления updater.bat. Завершение работы.")
        sys.exit(0)

    except Exception as e:
        logging.error(f"Ошибка при загрузке обновления: {e}")
        if os.path.exists(new_exe_path):
            os.remove(new_exe_path)
        return False