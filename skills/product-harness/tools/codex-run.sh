#!/usr/bin/env bash
# Запускает Codex из папки сессии с поиском и сетью: без них не работают снимки страниц и отправка на стенд.
# Песочница — workspace-write: без неё Codex в папке без git идёт только на чтение и не пишет файлы прогона.
# В папке git ещё даёт запись в git-каталог: песочница Codex держит .git только для чтения,
# а у ворктри git-каталог лежит в основном клоне. Вне git запускает без этого.
set -euo pipefail
command -v codex >/dev/null 2>&1 || { echo "codex-run: нет команды codex, поставь Codex CLI" >&2; exit 1; }
if GITDIR=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
  exec codex --search --sandbox workspace-write -c sandbox_workspace_write.network_access=true -c "sandbox_workspace_write.writable_roots=[\"$GITDIR\"]" "$@"
fi
exec codex --search --sandbox workspace-write -c sandbox_workspace_write.network_access=true "$@"
