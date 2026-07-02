# MInstAll

[![Build](https://github.com/assassins377/Python_Install/actions/workflows/build.yml/badge.svg)](https://github.com/assassins377/Python_Install/actions/workflows/build.yml)
[![License](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)](#)
[![Release](https://img.shields.io/github/v/release/assassins377/Python_Install)](https://github.com/assassins377/Python_Install/releases/latest)

[English](#english) | [Русский](#русский) | [简体中文](#简体中文)

---

## English

Universal silent installation wizard for programs and system tweaks.
Cross-platform: **Windows** and **Linux**.

Automates workstation setup:

- **Windows** — `.exe`, `.msi`, `.bat`/`.cmd`, `.reg`, PowerShell scripts
- **Linux** — `.deb` (via `apt-get`), `.sh`/`.bash`, `.AppImage`

### Screenshots

**Windows**

![MInstAll on Windows](docs/windows.png)

**Linux**

![MInstAll on Linux](docs/linux.png)

### Download

#### Windows

[**Latest release**](https://github.com/assassins377/Python_Install/releases/latest) → `MInstAll_x86.exe`

Works on Windows 7 and above (32-bit, compatible with all Windows systems).

#### Linux

No prebuilt binary — run from source (see [Running from source](#running-from-source)).

#### Integrity check

After downloading, compare the SHA-256 hash with the one in the Release description:

```powershell
# Windows
Get-FileHash MInstAll_x86.exe -Algorithm SHA256
```

```bash
# Linux
sha256sum MInstAll_x86.exe
```

### ⚠ SmartScreen warning (Windows only)

On first launch, Windows may show:

> **Windows protected your PC — Microsoft Defender SmartScreen prevented an unrecognized app from starting**

**This is normal.** The app currently has no code-signing certificate (~$200/year — not yet justified).

#### How to run

1. Click **"More info"** on the warning
2. Click the **"Run anyway"** button that appears

After several launches (or ~3000 downloads across all users), Microsoft will automatically add reputation and the warning will disappear.

#### Why you can trust it

- Open source — you can inspect the code yourself
- Built via GitHub Actions from this repository, not manually
- SHA-256 published in every Release description — you can verify integrity

### Features

- **Batch silent install** — pick a set of programs, click "Install", go make tea
- **Installed detection** — won't reinstall:
  - Windows — via registry
  - Linux — via `dpkg-query`
- **System components** — .NET Framework 4.8, DirectX, Visual C++ Redistributables (Windows)
- **Dependencies** — topological sort (e.g. VC++ installs before dependent apps)
- **Parallel installation** — independent programs install simultaneously (`--parallel`)
- **Retry with backoff** — retry on retryable errors (1618, 1603, 1641)
- **Rollback on error** — if install fails, runs the uninstall command
- **Watchdog** — detects hung installers by CPU and forcefully terminates them
- **URL download** — with SHA-256 verification and redirect control
- **Auto-update** — checks for new version via GitHub Releases (Windows)
- **Profiles** — ready-made program sets (`--install-profile`)
- **CLI mode** — install without GUI (`--install`, `--list`, …)
- **Localization** — Russian, English, Chinese (`i18n/`)
- **Window state persistence** — position and size remembered between launches

#### Privilege elevation

- **Windows** — UAC dialog (`runas`)
- **Linux** — `pkexec` with environment forwarding, fallback to `sudo -E`

### Running from source

```bash
# Linux
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
pip install -r requirements.txt
python main.py
```

```powershell
# Windows
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
pip install -r requirements.txt
python main.py
```

#### CLI (no GUI)

```bash
python main.py --version
python main.py --list
python main.py --list --filter-status missing
python main.py --install "Google Chrome,Telegram Desktop"
python main.py --install-profile developer
python main.py --install all --missing-only --parallel
python main.py --install Chrome --dry-run
```

### Tests

```bash
python -m pytest tests/ -v
```

### Build .exe locally (Windows)

```powershell
pip install pyinstaller
pyinstaller --clean --noconsole --onefile --name MInstAll_x86 --icon=icons/system.png main.py
```

### Project structure

```
├── main.py              # Entry point + CLI argument parsing
├── cli.py               # CLI mode (install/lists without GUI)
├── config.py            # Constants, paths, version
├── core.py              # Utilities: paths, command validation/build, download
├── installer.py         # InstallWorker: launch, retry, watchdog, rollback, hooks
├── registry.py          # Installed detection (registry / dpkg), statuses, versions
├── deps.py              # Dependency resolution, topological sort
├── gui.py               # wxPython window (assembled from mixins)
├── tree.py              # Program tree, context menu
├── menu.py              # App menu, settings toggles
├── dispatch.py          # GUI ↔ InstallWorker bridge, install finalization
├── log_panel.py         # Log panel in GUI
├── icons.py             # Icons (extraction from .exe on Windows)
├── i18n.py + i18n/      # Localization
├── profiles.py          # Profiles (ready-made program sets)
├── scanner.py           # Auto-discovery of installers in software/
├── stats.py             # Install time statistics
├── state.py             # Settings/session persistence
├── updater.py           # Auto-update via GitHub (Windows)
├── programs.json        # Program catalog
├── version.json         # Current version metadata
├── tests/               # Unit tests
├── tools/               # Utilities (scan_software.py, import_winget.py)
├── icons/               # Icons
├── software/            # Installers (local, not in repository)
└── .github/workflows/   # CI: tests + build + Release
```

### License

GNU GPL v3 — see [LICENSE](LICENSE).

---

## Русский

Универсальный мастер тихой установки программ и системных твиков.
Кросс-платформенный: **Windows** и **Linux**.

Автоматизирует развёртывание рабочего окружения:

- **Windows** — `.exe`, `.msi`, `.bat`/`.cmd`, `.reg`, PowerShell-скрипты
- **Linux** — `.deb` (через `apt-get`), `.sh`/`.bash`, `.AppImage`

### Скриншоты

**Windows**

![MInstAll на Windows](docs/windows.png)

**Linux**

![MInstAll на Linux](docs/linux.png)

### Скачать

#### Windows

[**Последний релиз**](https://github.com/assassins377/Python_Install/releases/latest) → `MInstAll_x86.exe`

Файл работает на Windows 7 и выше (32-bit, совместим со всеми Windows-системами).

#### Linux

Готового бинарника нет — запускается из исходников (см. [Запуск из исходников](#запуск-из-исходников)).

#### Проверка целостности

После скачивания сравни SHA-256 файла с указанным в описании Release:

```powershell
# Windows
Get-FileHash MInstAll_x86.exe -Algorithm SHA256
```

```bash
# Linux
sha256sum MInstAll_x86.exe
```

### ⚠ Предупреждение SmartScreen (только Windows)

При первом запуске Windows может показать:

> **Защитник Windows SmartScreen предотвратил запуск неопознанного приложения**

**Это нормально.** Приложение пока без code-signing сертификата (стоит ~$200/год — пока не оправдано).

#### Как запустить

1. На предупреждении нажми **"Подробнее"**
2. Появится кнопка **"Выполнить в любом случае"** — нажми её

После нескольких запусков (или ~3000 загрузок по всем пользователям) Microsoft автоматически добавит репутацию и предупреждение пропадёт.

#### Почему стоит доверять

- Открытый исходный код — можешь сам проверить что делает программа
- Сборка идёт через GitHub Actions из этого же репозитория, не вручную
- SHA-256 публикуется в описании каждого Release — можешь проверить целостность

### Возможности

- **Пакетная тихая установка** — выбираешь набор программ, нажимаешь "Установить", идёшь пить чай
- **Детекция установленного** — не ставит повторно:
  - Windows — через реестр
  - Linux — через `dpkg-query`
- **Системные компоненты** — .NET Framework 4.8, DirectX, Visual C++ Redistributables (Windows)
- **Зависимости** — топологическая сортировка (например, VC++ ставится до зависящих приложений)
- **Параллельная установка** — независимые программы ставятся одновременно (`--parallel`)
- **Retry с backoff** — повтор при retryable ошибках (1618, 1603, 1641)
- **Откат при ошибке** — если установка не удалась, запускается uninstall-команда
- **Watchdog** — детекция зависших инсталляторов по CPU и принудительное завершение
- **Скачивание по URL** — с проверкой SHA-256 и контролем редиректов
- **Автообновление** — проверяет новую версию через GitHub Releases (Windows)
- **Профили** — готовые наборы программ (`--install-profile`)
- **CLI-режим** — установка без GUI (`--install`, `--list`, …)
- **Локализация** — русский, английский, китайский (`i18n/`)
- **Сохранение размера окна** — позиция и размер запоминаются между запусками

#### Повышение прав

- **Windows** — UAC-диалог (`runas`)
- **Linux** — `pkexec` с пробросом окружения, fallback на `sudo -E`

### Запуск из исходников

```bash
# Linux
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
pip install -r requirements.txt
python main.py
```

```powershell
# Windows
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
pip install -r requirements.txt
python main.py
```

#### CLI (без GUI)

```bash
python main.py --version
python main.py --list
python main.py --list --filter-status missing
python main.py --install "Google Chrome,Telegram Desktop"
python main.py --install-profile developer
python main.py --install all --missing-only --parallel
python main.py --install Chrome --dry-run
```

### Тесты

```bash
python -m pytest tests/ -v
```

### Сборка .exe локально (Windows)

```powershell
pip install pyinstaller
pyinstaller --clean --noconsole --onefile --name MInstAll_x86 --icon=icons/system.png main.py
```

### Структура проекта

```
├── main.py              # Точка входа + разбор CLI-аргументов
├── cli.py               # CLI-режим (установка/списки без GUI)
├── config.py            # Константы, пути, версия
├── core.py              # Утилиты: пути, валидация/сборка команд, скачивание
├── installer.py         # InstallWorker: запуск, retry, watchdog, откат, хуки
├── registry.py          # Детекция установленного (реестр / dpkg), статусы, версии
├── deps.py              # Разрешение зависимостей, топологическая сортировка
├── gui.py               # wxPython-окно (сборка из миксинов)
├── tree.py              # Дерево программ, контекстное меню
├── menu.py              # Меню приложения, переключатели настроек
├── dispatch.py          # Связь GUI ↔ InstallWorker, финализация установки
├── log_panel.py         # Панель лога в GUI
├── icons.py             # Иконки (извлечение из .exe на Windows)
├── i18n.py + i18n/      # Локализация
├── profiles.py          # Профили (готовые наборы программ)
├── scanner.py           # Автообнаружение инсталляторов в software/
├── stats.py             # Статистика времени установки
├── state.py             # Сохранение настроек/сессии
├── updater.py           # Автообновление через GitHub (Windows)
├── programs.json        # Каталог программ
├── version.json         # Метаданные текущей версии
├── tests/               # Unit-тесты
├── tools/               # Утилиты (scan_software.py, import_winget.py)
├── icons/               # Иконки
├── software/            # Инсталляторы (локально, не в репозитории)
└── .github/workflows/   # CI: тесты + сборка + Release
```

### Лицензия

GNU GPL v3 — см. [LICENSE](LICENSE).

---

## 简体中文

通用的程序静默安装向导与系统调整工具。
跨平台：**Windows** 和 **Linux**。

自动化工作环境部署：

- **Windows** — `.exe`、`.msi`、`.bat`/`.cmd`、`.reg`、PowerShell 脚本
- **Linux** — `.deb`（通过 `apt-get`）、`.sh`/`.bash`、`.AppImage`

### 截图

**Windows**

![MInstAll on Windows](docs/windows.png)

**Linux**

![MInstAll on Linux](docs/linux.png)

### 下载

#### Windows

[**最新版本**](https://github.com/assassins377/Python_Install/releases/latest) → `MInstAll_x86.exe`

支持 Windows 7 及以上版本（32 位，兼容所有 Windows 系统）。

#### Linux

无预编译二进制文件 — 从源码运行（参见[从源码运行](#从源码运行)）。

#### 完整性校验

下载后，将文件的 SHA-256 与 Release 描述中的值进行比对：

```powershell
# Windows
Get-FileHash MInstAll_x86.exe -Algorithm SHA256
```

```bash
# Linux
sha256sum MInstAll_x86.exe
```

### ⚠ SmartScreen 警告（仅 Windows）

首次运行时，Windows 可能会显示：

> **Windows 已保护你的电脑 — Microsoft Defender SmartScreen 阻止了无法识别的应用启动**

**这是正常现象。** 该应用目前没有代码签名证书（约 $200/年 — 暂不划算）。

#### 如何运行

1. 在警告上点击 **"更多信息"**
2. 点击出现的 **"仍要运行"** 按钮

经过多次运行（或所有用户累计约 3000 次下载）后，Microsoft 将自动添加信誉，警告将消失。

#### 为什么可以信任

- 开源 — 你可以自行检查代码
- 通过 GitHub Actions 从此仓库构建，非手动构建
- 每个 Release 描述中均发布 SHA-256 — 你可以验证完整性

### 功能

- **批量静默安装** — 选择一组程序，点击"安装"，去泡杯茶
- **已安装检测** — 不会重复安装：
  - Windows — 通过注册表
  - Linux — 通过 `dpkg-query`
- **系统组件** — .NET Framework 4.8、DirectX、Visual C++ Redistributables（Windows）
- **依赖管理** — 拓扑排序（例如 VC++ 在依赖它的应用之前安装）
- **并行安装** — 独立程序同时安装（`--parallel`）
- **带退避的重试** — 对可重试错误进行重试（1618、1603、1641）
- **错误回滚** — 如果安装失败，运行卸载命令
- **看门狗** — 通过 CPU 检测挂起的安装程序并强制终止
- **URL 下载** — 带 SHA-256 验证和重定向控制
- **自动更新** — 通过 GitHub Releases 检查新版本（Windows）
- **配置文件** — 预置的程序集合（`--install-profile`）
- **CLI 模式** — 无需 GUI 即可安装（`--install`、`--list` 等）
- **本地化** — 俄语、英语、中文（`i18n/`）
- **窗口状态持久化** — 位置和大小在启动之间记忆

#### 权限提升

- **Windows** — UAC 对话框（`runas`）
- **Linux** — `pkexec` 带环境转发，回退到 `sudo -E`

### 从源码运行

```bash
# Linux
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
pip install -r requirements.txt
python main.py
```

```powershell
# Windows
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
pip install -r requirements.txt
python main.py
```

#### CLI（无 GUI）

```bash
python main.py --version
python main.py --list
python main.py --list --filter-status missing
python main.py --install "Google Chrome,Telegram Desktop"
python main.py --install-profile developer
python main.py --install all --missing-only --parallel
python main.py --install Chrome --dry-run
```

### 测试

```bash
python -m pytest tests/ -v
```

### 本地构建 .exe（Windows）

```powershell
pip install pyinstaller
pyinstaller --clean --noconsole --onefile --name MInstAll_x86 --icon=icons/system.png main.py
```

### 项目结构

```
├── main.py              # 入口点 + CLI 参数解析
├── cli.py               # CLI 模式（无需 GUI 的安装/列表）
├── config.py            # 常量、路径、版本
├── core.py              # 工具：路径、命令验证/构建、下载
├── installer.py         # InstallWorker：启动、重试、看门狗、回滚、钩子
├── registry.py          # 已安装检测（注册表 / dpkg）、状态、版本
├── deps.py              # 依赖解析、拓扑排序
├── gui.py               # wxPython 窗口（由 mixin 组装）
├── tree.py              # 程序树、右键菜单
├── menu.py              # 应用菜单、设置开关
├── dispatch.py          # GUI ↔ InstallWorker 桥接、安装完成处理
├── log_panel.py         # GUI 中的日志面板
├── icons.py             # 图标（Windows 上从 .exe 提取）
├── i18n.py + i18n/      # 本地化
├── profiles.py          # 配置文件（预置程序集合）
├── scanner.py           # 自动发现 software/ 中的安装程序
├── stats.py             # 安装时间统计
├── state.py             # 设置/会话持久化
├── updater.py           # 通过 GitHub 自动更新（Windows）
├── programs.json        # 程序目录
├── version.json         # 当前版本元数据
├── tests/               # 单元测试
├── tools/               # 工具（scan_software.py、import_winget.py）
├── icons/               # 图标
├── software/            # 安装程序（本地，不在仓库中）
└── .github/workflows/   # CI：测试 + 构建 + Release
```

### 许可证

GNU GPL v3 — 参见 [LICENSE](LICENSE)。
