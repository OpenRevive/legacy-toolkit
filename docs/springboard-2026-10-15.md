# SpringBoard и системные окна — 15 октября 2026

OpenRevive **0.4.0** управляет домашним экраном, переключателем приложений, Центром уведомлений, системным диалогом поверх приложения и экраном блокировки на физическом **iPhone 4 GSM / iPhone3,1 / iOS 6.1.3 / 10B329**. Контроллер работает через Wi-Fi/SSH MacBook BlackLion / Lion 10.7.5 / Xcode 4.6.3, телефон подключён к нему по USB. Использованы существующие jailbreak, AFC2 и MobileSubstrate.

## Проверенные результаты

| Сценарий | Фактическое подтверждение |
|---|---|
| Домашний экран | 233 узла, 20 видимых иконок; настоящие имена и bundle ID из SBIcon, внутреннее окно статус-бара |
| Запуск по иконке | «Настройки» `[490,596,118,148]` пикселей → HID-тап → foreground `com.apple.Preferences` и новое дерево приложения |
| Старый Home | Повторный тап по снимку до запуска приложения отклонён из-за смены контекста |
| Системный диалог | Настоящий `SBUserNotificationAlert`, созданный CFUserNotification отдельным процессом, найден поверх `org.openrevive.TouchProbe` |
| Кнопка диалога | `Accept probe` `[328,544,254,86]` → настоящий HID-тап; отдельный процесс записал актуальный nonce, error 0, responseFlags 0 и accepted 1 |
| Приложение под диалогом | Попытка редактирования отклонена, независимый результат приложения не изменился |
| Центр уведомлений | 299 узлов и SBBulletinWindow; открытие и закрытие подтверждены новой системной структурой |
| Перелистывание Home | Настоящий HID-свайп изменил страницу 0 → 1; обратный свайп вернул 0 |
| Контекст жеста | Подменённый foreground PID и снимок предыдущей страницы отклонены без нового свайпа |
| Переключатель | 313 узлов; выбор недавнего OpenRevive через его иконку запустил именно `org.openrevive.TouchProbe` |
| Экран блокировки | 44 узла; обычный свайп слайдера снял блокировку устройства без установленного passcode |
| Нативные отказы | Неверный systemToken и SpringBoard PID отклонены; правильный снимок прошёл validate, повторный validate получил stale-snapshot |
| Регрессия приложений | Unicode-форма с новым nonce, 5 настоящих одновременных UITouch, pinch внутрь/наружу и завершение всех контактов прошли после расширения |
| Чужой системный процесс | AssistiveTouch открыл меню через VNC; его «Домой» вернула приложение на SpringBoard. Общий HID-тап по этой плавающей кнопке результата не дал |
| Установленные агенты | Codex и Claude Code 0.4.0 прочитали актуальный Home вне репозитория: по 20 иконок, одинаковые SpringBoard PID/session/build ID |

Количество узлов — наблюдение конкретных снимков; скрытые окна и внутренние объекты могут менять его между вызовами. `truncated:false` относится к указанным снимкам с depth 16 / limit 1500, не обещает универсальное дерево всего iOS.

## Что изменилось

Прежний маршрут требовал обычное разблокированное foreground-приложение. На Home `SBSCopyFrontmostApplicationDisplayIdentifier` возвращает пустое значение, а системный диалог может существовать поверх всё ещё указанного приложения. Теперь есть отдельный `scope:system` с собственной проверкой владельца.

Мост в SpringBoard читает внутренние UIWindow, включая SBAppWindow, SBAlertWindow, SBShowcaseWindow, SBBulletinWindow и UIStatusBarWindow. Для SBIconView берутся `displayName` и `applicationBundleID`, потому что iOS 6 рисует подписи иконок без UILabel. Геометрия UIWindow учитывается отдельно от свойства UIView.window.

Системный снимок содержит SpringBoard PID/session, nonce, foreground bundle/PID, lock/passcode, страницу иконок, идентификатор диалога, переключатель/шторку, видимые окна и токен контекста. Перед действием эти данные проверяются на телефоне. Снимок действует 30 секунд, заменяется следующим деревом и поглощается успешным действием. Жест сначала проходит нативный validate; во время кадров проверяются процессы, foreground, блокировка и идентичность диалога, а при прерывании контакты освобождаются. Изменение страницы/шторки в ходе самого системного жеста разрешено.

`system-control` вызывает ограниченный набор настоящих обработчиков iOS 6. `tap`, `swipe`, `gesture` и `pinch` с системным снимком идут через настоящий HID/backboardd. Это разные способы действия: семантическое открытие шторки не выдаётся за распознанный пальцевый жест. Запуск иконок, выбор недавнего приложения, кнопка диалога, перелистывание и слайдер проверены именно через HID.

Установщик собирает собственные исходники armv7 с `-Werror`, подписывает на телефоне и публикует новым inode. Готовность проверяет совпадение build ID и у HID, и у SpringBoard. Новых сетевых слушателей нет; запросы проходят через локальные CFMessagePort и существующий paired USB root service. Секреты и готовые двоичные файлы в плагин не включены.

## Команды

Подробности установки, формат снимка/жеста, время жизни и восстановление находятся в [скилле](../plugins/openrevive/skills/legacy-mac/SKILL.md) и [справочнике системного управления](../plugins/openrevive/skills/legacy-mac/references/system.md).

```sh
./bin/openrevive device --udid "$UDID" setup-uikit --respring
# После первого обновления разбудить/разблокировать устройство.
./bin/openrevive device --udid "$UDID" system-state
./bin/openrevive device --udid "$UDID" tree --system --depth 16 --limit 1500 --output /tmp/system.json
./bin/openrevive device --udid "$UDID" ui-action /tmp/system.json "$ICON_ID" tap
# Перед каждым следующим действием нужен новый tree.
./bin/openrevive device --udid "$UDID" system-control /tmp/system.json switcher
./bin/openrevive device --udid "$UDID" swipe 550 520 80 520 --duration .6 --system-snapshot /tmp/system.json
./bin/openrevive device --udid "$UDID" gesture /tmp/tracks.json --system-snapshot /tmp/system.json
```

Также есть `notifications`, `dismiss-notifications`, `dismiss-switcher`, `tap X Y --system-snapshot FILE` и `pinch ... --system-snapshot FILE`. У `tap`/`swipe` без этого параметра сохраняется прежний VNC-маршрут. Home может открыть Spotlight: `iconPage:-1` обозначает поиск, `0` — первую страницу. Нельзя слепо нажимать Home, предполагая неизменный экран.

`system-alert-probe` показывает одноразовый реальный диалог с nonce и возвращает путь независимого результата. Он завершается по ответу либо через 90 секунд. Проверять нужно nonce и flags в receipt, а не только код команды. В ходе этого теста настройки телефона не менялись.

## Доказательства

Выполнены **7/7 критериев**: шесть исполняемых проверок и ручная сверка экранов. [Команды, результаты и хеши артефактов](springboard-evidence/verification.json). Повторная установка проверила оба загруженных моста и вернула [alreadyInstalled / ready](springboard-evidence/setup-status.json).

- [Home](springboard-evidence/home-tree.json), [диалог](springboard-evidence/dialog-tree.json), [шторка](springboard-evidence/notifications-tree.json), [переключатель](springboard-evidence/switcher-tree.json), [lockscreen](springboard-evidence/lockscreen-tree.json).
- [Запуск иконки](springboard-evidence/home-validation.json), [ответ диалога](springboard-evidence/dialog-receipt.json), [проверки окон](springboard-evidence/overlays-validation.json), [системные жесты](springboard-evidence/control-validation.json), [нативные отказы](springboard-evidence/protocol-validation.json).
- [Две установленные копии](springboard-evidence/install-validation.json), [новый результат формы](springboard-evidence/app-actions-validation.json), [регрессия мультитача](springboard-evidence/app-multitouch-validation.json).
- [AssistiveTouch: маршрут и foreground](springboard-evidence/fallback-validation.json), [фактическое меню](springboard-evidence/assistive-menu.png), [Home после кнопки меню](springboard-evidence/assistive-home.png).
- [Наблюдение о кадрах](springboard-evidence/capture-notes.json): один ранний кадр не показал открытое меню. Сохранены отдельный удачный кадр меню и независимый переход Home; в скилле требуется дождаться анимации и переснять неоднозначное изображение.

![Домашний экран](springboard-evidence/home.png)

![Системный диалог поверх приложения](springboard-evidence/dialog.png)

![Переключатель приложений](springboard-evidence/switcher.png)

![Центр уведомлений](springboard-evidence/notifications.png)

![Экран блокировки](springboard-evidence/lockscreen.png)

Воспроизведение, последовательно на выделенном тестовом телефоне:

```sh
python3 tests/check_system_live.py home --udid "$UDID"
python3 tests/check_system_live.py overlays --udid "$UDID"
python3 tests/check_system_live.py control --udid "$UDID"
python3 tests/check_system_protocol.py --udid "$UDID"
python3 tests/check_system_install.py --udid "$UDID"
python3 tests/check_system_fallback.py --udid "$UDID"
```

## Границы

Подтверждён портретный iPhone3,1 / iOS 6.1.3. Обычный слайдер разблокирует только устройство без passcode; обхода пароля нет. Другие версии iOS, аппаратные модели, повороты и все отдельные разновидности системных диалогов этим набором испытаний не покрыты.

Это деревья конкретных процессов, а не универсальная межпроцессная AX-модель iOS. Внутреннее содержимое других системных демонов, web DOM, OpenGL/canvas и отдельно размещённых поверхностей требует собственного моста либо свежего скриншота и координатного ввода. Окна за оверлеем могут оставаться видимыми в структуре; перед действием выполняется hit test, но дерево SpringBoard не знает внутреннюю структуру чужого процесса. Вызовы, отправка сообщений, покупки, аппаратные сенсоры и восстановление прошивки не проверялись. Instruments и UIKit-дерево Simulator не исправлялись.

Для AssistiveTouch проверен именно VNC (`tap` без `--system-snapshot`): native HID сообщил отправку, но меню не открыл; тот же тап через VNC открыл меню, затем его Home вернула на SpringBoard. Эта развилка внесена в скилл. Положение кнопки для будущего запуска нужно выбирать по актуальному изображению.

Источники API/ABI: [iOS 6 SpringBoard headers](https://github.com/Bensge/iOS-6-SpringBoard-Headers), [Apple CFUserNotification](https://github.com/apple-oss-distributions/CF/blob/main/CFUserNotification.h); ссылки на конкретные классы и метод работы есть в справочнике. Это собственный мост, собираемый пользовательским SDK; чужих скачанных исполняемых библиотек управления в плагине нет.
