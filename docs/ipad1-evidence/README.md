# iPad 1: доказательства прогона

[Сводка](acceptance.json) и [отчёт](../ipad1-2026-10-16.md) различают успешные проверки, измеренные отказы и непроверенные возможности. [Manifest](manifest.json) фиксирует SHA-256 файлов.

| Проверка | Снимок | Независимые данные |
| --- | --- | --- |
| Unicode через MCP | [экран](mcp-form.png) | [app receipt и MCP](mcp.json) |
| Multitouch | [экран](multitouch-result.png) | [UITouch / recognizer](gestures.json) |
| DNS/CA в приложении | [экран](dns-ca-pass.png) | [native trust и серверные run receipts](dns.json) |
| Системный CA | [установленный профиль](ca-profile-installed.png) | [установка](ca-profile-install.json), [удаление](ca-profile-cleanup.json) |
| Safari HTTPS | [загруженная страница](safari-vscale-ca-loaded.png) | [nginx receipt с iPad User-Agent](safari-ca-server-log.json) |
| VK | [friends](vk-friends.png), [profile](vk-own-profile.png), [переписка](vk-incoming-live.png) | [SQLite](vk-message-sqlite.json), [wire](vk-message-wire.json), [оригинальность executable](vk-binary-verification.json) |

Снимок Safari извлечён побайтно из первоначального `view_image` результата этого чата: рабочее имя PNG позднее было перезаписано снимком ещё загружавшейся страницы. [Provenance](safari-image-provenance.json) содержит время первоначального просмотра и SHA-256. Пиксели не изменялись. Публичный IP клиента в access-log receipts заменён на `[client]`.

Pairing, пароли, private keys, bearer credentials и IPA сюда не копировались. Backend проверки выполнялись в отдельных временных БД; их успешность не означает приёмку всех UI-функций VK на iPad.
