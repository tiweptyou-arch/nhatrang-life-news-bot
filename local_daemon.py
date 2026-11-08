"""Run the Telegram bot continuously on macOS using a private local .env file."""
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time

PROJECT_DIR = Path(__file__).resolve().parent
APP_DIR = Path.home() / 'Library' / 'Application Support' / 'NhaTrangLifeNewsBot'
STOP = False


def load_env_file(path):
    if not path.is_file():
        return False
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key.replace('_', '').isalnum():
            os.environ.setdefault(key, value)
    return True


SECRET_ENV_NAMES = (
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'ADMIN_USER_ID', 'ADMIN_USER_IDS',
    'SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'SUPABASE_SERVICE_ROLE_KEY', 'WEATHERAPI_KEY',
    'OPENAI_API_KEY', 'AI_PROVIDER', 'OPENROUTER_API_KEY', 'OPENROUTER_MODEL', 'OPENAI_TEXT_MODEL',
    'OPENAI_DAILY_BUDGET_USD', 'OPENAI_MONTHLY_BUDGET_USD', 'MAX_SINGLE_IMAGE_RESERVE_USD',
    'MAX_AI_CALLS_PER_DAY', 'AUTO_PUBLISH', 'IMAGE_PROVIDER', 'OPENAI_IMAGE_MODEL',
    'OPENAI_IMAGE_QUALITY', 'MAX_DAILY_POSTS', 'MAX_DAILY_NEWS', 'MAX_DAILY_ADS',
    'MAX_IMAGE_GENERATIONS_PER_DAY', 'MAX_IMAGE_SPEND_USD_PER_DAY',
    'MAX_IMAGE_SPEND_USD_PER_MONTH'
)


def load_private_config(path):
    try:
        values = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        print('Local bot stopped: private Mac configuration is unreadable.')
        raise SystemExit(2)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        print('Local bot stopped: private Mac configuration permissions are too broad.')
        raise SystemExit(2)
    if not isinstance(values, dict):
        print('Local bot stopped: private Mac configuration is invalid.')
        raise SystemExit(2)
    for name in SECRET_ENV_NAMES:
        value = values.get(name)
        if isinstance(value, str) and value and not os.environ.get(name):
            os.environ[name] = value


def stop(_signum, _frame):
    global STOP
    STOP = True


def main():
    env_path = Path(os.environ.get('BOT_ENV_FILE', str(PROJECT_DIR / '.env'))).expanduser()
    load_env_file(env_path)
    load_private_config(APP_DIR / 'secrets.json')
    if not os.environ.get('TELEGRAM_BOT_TOKEN') or not os.environ.get('ADMIN_USER_IDS'):
        print('Local bot stopped: Telegram token and admin ID are missing from local configuration.')
        return 2

    os.umask(0o077)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    APP_DIR.chmod(0o700)
    state_path = Path(os.environ.get('BOT_STATE_FILE', 'bot_state.json')).expanduser()
    if not state_path.is_absolute():
        state_path = APP_DIR / state_path.name
    os.environ['BOT_STATE_FILE'] = str(state_path)
    os.environ.setdefault('AI_PROVIDER', 'openrouter')
    os.environ.setdefault('OPENROUTER_MODEL', 'deepseek/deepseek-v4-flash:free')
    os.environ.setdefault('AUTO_PUBLISH', 'true')
    os.environ.setdefault('NEWS_SOURCES_FILE', str(PROJECT_DIR / 'sources.json'))
    os.chdir(str(PROJECT_DIR))

    lock_path = APP_DIR / 'daemon.lock'
    lock_file = lock_path.open('w')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Another local bot process already holds the service lock.')
        return 3

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sys.path.insert(0, str(PROJECT_DIR))
    import bot

    interval = max(3, int(os.environ.get('BOT_POLL_INTERVAL_SECONDS', '5')))
    print('Local bot daemon started; polling every', interval, 'seconds; timezone=', bot.TZ)
    while not STOP:
        started = time.monotonic()
        try:
            bot.main()
        except Exception as exc:
            # Never print exception text: provider errors can contain URLs or request data.
            print('Bot cycle failed safely:', type(exc).__name__)
        remaining = interval - (time.monotonic() - started)
        if not STOP and remaining > 0:
            time.sleep(remaining)
    print('Local bot daemon stopped cleanly.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
