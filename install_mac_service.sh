#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=$(command -v python3)
ENV_FILE="$PROJECT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  printf 'Created private config: %s\nAdd TELEGRAM_BOT_TOKEN, ADMIN_USER_IDS, OPENROUTER_API_KEY, SUPABASE_URL, SUPABASE_SECRET_KEY, and WEATHERAPI_KEY there, then run this script again.\n' "$ENV_FILE"
  exit 2
fi
chmod 600 "$ENV_FILE"

if ! python3 - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path
values = {}
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"\'')
required = ['TELEGRAM_BOT_TOKEN', 'ADMIN_USER_IDS', 'SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'WEATHERAPI_KEY']
provider = values.get('AI_PROVIDER', 'openrouter').casefold()
key_name = {'openrouter': 'OPENROUTER_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY', 'openai': 'OPENAI_API_KEY'}.get(provider)
if not key_name:
    print('Unsupported AI_PROVIDER; use openrouter, deepseek, or openai.')
    raise SystemExit(1)
required.append(key_name)
missing = [key for key in required if not values.get(key)]
if missing:
    print('Local service not installed; fill required fields in .env: ' + ', '.join(missing))
    raise SystemExit(1)
if provider == 'openrouter' and not values.get('OPENROUTER_MODEL', 'deepseek/deepseek-v4-flash:free').endswith(':free'):
    print('This setup expects a :free OpenRouter model; paid models are disabled by this installer.')
    raise SystemExit(1)
PY
then
  exit 2
fi

LABEL='com.nhatranglife.newsbot'
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$HOME/Library/Application Support/NhaTrangLifeNewsBot"
mkdir -p "$HOME/Library/LaunchAgents" "$APP_DIR"
python3 - "$PLIST" "$PROJECT_DIR" "$PYTHON" "$APP_DIR" <<'PY'
import plistlib, sys
path, project, python, app = sys.argv[1:]
data = {
    'Label': 'com.nhatranglife.newsbot',
    'ProgramArguments': [python, project + '/local_daemon.py'],
    'WorkingDirectory': project,
    'RunAtLoad': True,
    'KeepAlive': True,
    'ThrottleInterval': 10,
    'StandardOutPath': app + '/service.log',
    'StandardErrorPath': app + '/service-error.log',
}
with open(path, 'wb') as f:
    plistlib.dump(data, f)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
printf 'Installed and started %s. Logs: %s/service.log\n' "$LABEL" "$APP_DIR"
