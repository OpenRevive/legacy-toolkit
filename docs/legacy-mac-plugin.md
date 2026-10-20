# OpenRevive: плагин для старого MacBook

Плагин и самостоятельный набор инструментов для работы агента со старым MacBook по Wi-Fi и SSH. Один пакет для **Codex** и **Claude Code**. Контроллер работает на современном компьютере; на старом Mac используются штатные Python 2.7, Cocoa, Accessibility и Xcode.

Проверяемая конфигурация: **BlackLion / MacBook4,1 / OS X 10.7.5 / Xcode 4.6.3 / iOS SDK 6.1 / 1280×800**. Установщик рассчитан на 10.7.3–10.8.x с SDK macOS 10.7; другие машины требуют отдельной проверки. Современный macOS использует другую модель разрешений и этим установщиком не обслуживается.

В комплекте:

- MCP-сервер с 17 инструментами: деревья и поиск по узлам, PNG прямо в ответе инструмента, ввод, мультитач и сборка/установка. Снимки дерева живут внутри сервера; [проверка 0.6.0](mcp-2026-10-15.md), [справка MCP](../plugins/openrevive/skills/legacy-mac/references/mcp.md).
- `doctor`: доступность SSH, графическая сессия, Accessibility, Xcode/SDK, место, подпись и USB; для проблем выдаются конкретные действия.
- Скриншоты с пробуждением дисплея, окна и приложения, ограниченное AX-дерево, проверка владельца/типа/заголовка перед AX-действием.
- Клики, двойной клик, перетаскивание, прокрутка, движение мыши, Unicode-ввод и сочетания клавиш.
- Установка удалённого помощника без сторонних пакетов; повторная установка, проверка артефактов, атомарное переключение и откат версий.
- Инкрементальная отправка исходников, получение файлов, команды с сохранением аргументов, кодов возврата и таймаутов.
- Временная Aqua LaunchAgent для инструментов, которым недостаточно SSH-сессии; сборка старым Xcode; исходники и сборка совместимого `ios-sim 1.7`; Cocoa/UIKit приложения для проверки.
- Пассивное обнаружение iPhone и временный USB-over-SSH мост для современных локальных libimobiledevice.
- Совместимый pairing для Lion, сборка/установка IPA и developer image; проверенный запуск на iPhone 4; экран, касания, свайпы, клавиатура, Home/Lock через Veency; команды и файлы через существующий jailbreak/AFC2.
- Единое дерево процессов и окон iPhone, включая AssistiveTouch, с поиском цели и автоматическим выбором semantic/HID/VNC: [отчёт 0.5.0](unified-ui-2026-10-15.md), [команды](../plugins/openrevive/skills/legacy-mac/references/unified-ui.md).
- SpringBoard и системные окна: Home/иконки, переключатель, уведомления, диалоги и lockscreen, с проверкой контекста и настоящим HID-вводом; [отчёт 0.4.0](springboard-2026-10-15.md).
- Дерево настоящих UIKit views и accessibility-элементов, защищённые действия с формами, навигация и прокрутка; настоящий HID-мультитач и pinch через backboardd на iPhone 4 / iOS 6.

## Быстрый старт

На современном компьютере нужны Python 3.9+, OpenSSH и `rsync` для загрузки исходников. MCP дополнительно требует локальный Node.js 20+; JavaScript-зависимости уже включены в сервер. Для работы только с Mac сторонних Python-пакетов нет. Для физического iPhone дополнительно нужны локальные libimobiledevice/libplist, ideviceinstaller и `cryptography` для VNC; см. [настройку телефона](../plugins/openrevive/skills/legacy-mac/references/iphone.md). На Lion нужен установленный Xcode; отдельные Command Line Tools в `/usr/bin` не обязательны.

```sh
./bin/openrevive init --address blacklion.local --user oz \
  --identity ~/.ssh/id_rsa_blackmac --legacy-rsa
./bin/openrevive bootstrap --check
./bin/openrevive bootstrap
./bin/openrevive doctor
./bin/openrevive ui probe
./bin/openrevive ui screenshot /tmp/blacklion.png
```

`init` по умолчанию сохраняет профиль в `~/.config/openrevive/hosts.json`. Для профиля проекта укажите `--config .openrevive/hosts.json` перед командой. Файлы этого каталога исключены из Git. Настройка не содержит пароль; ключ остаётся в SSH. Параметр `--legacy-rsa` разрешает старый алгоритм только для выбранного соединения.

Если SSH ещё не настроен, начните с [подготовки Mac](../plugins/openrevive/skills/legacy-mac/references/setup.md). Входящие подключения на роутере открывать не нужно.

## Подключение к агентам

Из корня проекта:

```sh
python3 tools/install.py --codex --claude
```

Скрипт регистрирует локальный marketplace и устанавливает плагин штатными CLI. Codex устанавливает плагин для пользователя; Claude Code по умолчанию — для этого проекта. Для Claude во всех проектах добавьте `--claude-scope user`. Уже открытой сессии может понадобиться перезагрузка плагинов или новая сессия.

В Codex выберите скилл `openrevive:legacy-mac` в списке `$` / Skills. В Claude Code — `/openrevive:legacy-mac`. Можно написать: «Используй legacy-mac: проверь BlackLion, открой приложение, введи текст и покажи результат».

Для разработки без установки Claude поддерживает:

```sh
claude --plugin-dir /absolute/path/to/OpenRevive/plugins/openrevive
```

Для установки только скилла скопируйте **весь** каталог `plugins/openrevive/skills/legacy-mac` в `~/.agents/skills/legacy-mac` для Codex или `~/.claude/skills/legacy-mac` для Claude. Codex также поддерживает символьную ссылку на этот каталог. Не устанавливайте одновременно дубликат отдельного скилла и того же плагина. [Формат Codex](https://developers.openai.com/plugins/build/plugins) и [формат Claude Code](https://code.claude.com/docs/en/plugins) проверены по официальной документации.

Сам плагин целиком находится в `plugins/openrevive`; он не зависит от соседнего `proxmox`, файлов этого репозитория или личных путей автора. Все исполняемые ресурсы скилла находятся внутри его каталога.

## Установка непосредственно на старом Mac

Перенесите весь каталог `plugins/openrevive/skills/legacy-mac/scripts/remote` на MacBook и выполните в Terminal:

```sh
bash /path/to/remote/bootstrap.command --check
bash /path/to/remote/bootstrap.command
```

Помощник устанавливается в `~/Library/Application Support/OpenRevive`. Исходники совместимого `ios-sim` уже вложены; интернет и современный TLS на Lion не требуются. Для системных настроек и настройки первого SSH-входа см. [setup](../plugins/openrevive/skills/legacy-mac/references/setup.md).

## Использование и проверка

[Скилл](../plugins/openrevive/skills/legacy-mac/SKILL.md) задаёт рабочий процесс агента. [Сценарии](../plugins/openrevive/skills/legacy-mac/references/workflows.md) содержат команды для GUI, исходников, Xcode, Simulator и USB. [Разбор проблем](../plugins/openrevive/skills/legacy-mac/references/troubleshooting.md) описывает проверенные особенности Lion.

```sh
python3 tests/check_package.py
python3 tests/check_live.py setup
python3 tests/check_live.py gui
python3 tests/check_live.py workflow
python3 tests/check_install.py
```

Live-проверки требуют настроенный профиль `blacklion` и изменяют только приложения/рабочие файлы OpenRevive. Результаты пишутся в `.artifacts/`. Проверка GUI действительно занимает экран удалённого MacBook.

Физический iPhone 4 с iOS 6.1.3 прошёл установку, запуск, ввод и проверку результата в приложении; [первый отчёт](iphone4-2026-10-15.md). В версии 0.3.0 добавлены дерево UIKit, семантическое редактирование и настоящий мультитач; [отчёт с журналами и изображениями](uikit-multitouch-2026-10-15.md); [команды и ограничения](../plugins/openrevive/skills/legacy-mac/references/uikit.md). Команды доступны через `device --udid АКТУАЛЬНЫЙ_UDID …`. Используются существующие jailbreak и AppSync телефона. Instruments UIAutomation по-прежнему выдаёт ошибки авторизации и способен завершиться кодом 0 при провале; новые мосты от него не зависят. Для Simulator подтверждены окно на Mac, скриншоты, ввод и независимый результат приложения; извлечение его UIKit-дерева требует отдельной реализации.

Лицензия OpenRevive — MIT. Лицензия и фиксация версии стороннего `ios-sim` находятся в [THIRD_PARTY](../plugins/openrevive/THIRD_PARTY.md).
