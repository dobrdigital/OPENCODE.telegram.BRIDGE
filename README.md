# cc-connect ↔ OpenCode: Telegram-мост с локальным голосом

Поднимает OpenCode в Telegram через мост [cc-connect](https://github.com/chenhg5/cc-connect) так,
чтобы **одна сессия была общей** для Telegram, десктоп-клиента и CLI — плюс распознавание речи
локально (GigaAM на своей видеокарте) и голосовые ответы. Windows.

Полный разбор с обоснованиями и граблями — в **[SKILL.md](SKILL.md)**. Здесь быстрый старт.

---

## Что это даёт

```
Telegram ──┐
Desktop  ──┼─→ cc-connect ──→ opencode-cli serve --service ──→ opencode.db
CLI TUI  ──┘                      (ОДИН сервер, ОДНА сессия)

голос на вход : Telegram OGG → ffmpeg → MP3 → shim → GigaAM GGUF (Vulkan) → текст
голос на выход: edge-tts (без API-ключа), только по команде /tts always
```

Без ключей, без облака, без подписки.

---

## Требования

| | Проверка |
|---|---|
| Node.js + `opencode` CLI | `opencode --version` |
| cc-connect | `npm i -g cc-connect` |
| **ffmpeg** | `ffmpeg -version` — нужен **обоим** голосовым путям |
| Python 3.10+ | для shim |
| Handy + модель GigaAM | `handy.exe --list-models --json` → `"is_downloaded": true` |
| `edge-tts` | `pip install edge-tts` — только если нужен голос на выход |

---

## Установка

```powershell
git clone <this-repo> cc-connect-bridge
cd cc-connect-bridge

# 1. Конфиг: копия шаблона + свой токен бота
copy config.example.toml "$env:USERPROFILE\.cc-connect\config.toml"
notepad "$env:USERPROFILE\.cc-connect\config.toml"   # токен + work_dir

# 2. Лаунчеры
copy start-cc-connect.ps1 "$env:USERPROFILE\.cc-connect\"
copy gigaam-stt\* "$env:USERPROFILE\.cc-connect\gigaam-stt\" -Force

# 3. Починить вызов opencode (обязательно, см. «Почему это важно»)
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1

# 4. Автозагрузка
powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1

# 5. Запуск
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cc-connect\start-cc-connect.ps1"
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.cc-connect\gigaam-stt\start-gigaam-stt.ps1"

# 6. Проверка
powershell -ExecutionPolicy Bypass -File .\autostart-test.ps1
```

Ожидаемо: `failures: 0`.

---

## Почему `install-npm-shim.ps1` обязателен

cc-connect v1.5.0 вызывает OpenCode двумя способами, которые 2.0.28 отвергает:

```
opencode run --dir <workDir>      → Unrecognized flag: --dir
opencode session list --json      → нужен --format json, иначе тихий exit 1
```

Скрипт перенаправляет npm-шим на шим, который это чинит.

**Любой `npm i -g @opencode/cli` перезапишет шим.** После обновления:

```powershell
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1 -Verify
```

`-Verify` ничего не меняет и возвращает 1, если чинить надо.

---

## Проверка

```powershell
# всё ли живо
powershell -ExecutionPolicy Bypass -File .\autostart-test.ps1

# шим
node .\opencode-shim\test-opencode-shim.js          # 24 passing

# голос
curl http://127.0.0.1:8300/health
curl -X POST http://127.0.0.1:8300/v1/audio/transcriptions -F "file=@sample.wav" -F "model=whisper-1"

# шим не сломан (после любого npm update)
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1 -Verify
```

---

## Расхождение версий — главный источник боли

Этот проект существует в основном потому, что **cc-connect 1.5.0 писался под более старый
OpenCode**, а более новый OpenCode на неизвестный флаг не ругается, а молча печатает справку
и выходит с кодом 1.

Если что-то сломалось «непонятно как» — сначала прочитай раздел
**«Расхождение версий»** в [SKILL.md](SKILL.md).

---

## Состав

```
SKILL.md                    разбор: архитектура, все шаги, 15 граблей
install-autostart.ps1       регистрация задач планировщика (+/- -Uninstall)
config.example.toml         шаблон конфига, без секретов
autostart-test.ps1          автотест, секции A–F, включая живой round-trip распознавания
start-cc-connect.ps1        лаунчер моста
gigaam-stt/server.py        OpenAI-совместимый STT поверх Handy (только stdlib)
gigaam-stt/start-gigaam-stt.ps1
opencode-shim/              шим совместимости + тесты + install-npm-shim.ps1
stt-selftest.txt            фраза для автотеста
```

---

## Лицензия и благодарность

MIT. Проект собран вокруг [cc-connect](https://github.com/chenhg5/cc-connect) и
[OpenCode](https://github.com/sst/opencode); обе части — их авторов.
GigaAM — модель Сбера. Распознавание речи и синтез выполняются локально, данные никуда не уходят.