"""One-time GitHub Actions bootstrap: store Actions secrets privately and start launchd."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import plistlib

APP_DIR = Path.home() / 'Library' / 'Application Support' / 'NhaTrangLifeNewsBot'
APP_CODE = APP_DIR / 'app'
LABEL = 'com.nhatranglife.newsbot'
SECRET_NAMES = (
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'ADMIN_USER_ID', 'ADMIN_USER_IDS',
    'SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'SUPABASE_SERVICE_ROLE_KEY', 'WEATHERAPI_KEY',
    'OPENAI_API_KEY', 'AI_PROVIDER', 'OPENROUTER_API_KEY', 'OPENROUTER_MODEL', 'OPENAI_TEXT_MODEL',
    'OPENAI_DAILY_BUDGET_USD', 'OPENAI_MONTHLY_BUDGET_USD', 'MAX_SINGLE_IMAGE_RESERVE_USD',
    'MAX_AI_CALLS_PER_DAY', 'AUTO_PUBLISH', 'IMAGE_PROVIDER', 'OPENAI_IMAGE_MODEL',
    'OPENAI_IMAGE_QUALITY', 'MAX_DAILY_POSTS', 'MAX_DAILY_NEWS', 'MAX_DAILY_ADS',
    'MAX_IMAGE_GENERATIONS_PER_DAY', 'MAX_IMAGE_SPEND_USD_PER_DAY',
    'MAX_IMAGE_SPEND_USD_PER_MONTH'
)
REQUIRED = ('TELEGRAM_BOT_TOKEN', 'ADMIN_USER_IDS', 'SUPABASE_URL',
            'SUPABASE_SECRET_KEY', 'WEATHERAPI_KEY', 'OPENROUTER_API_KEY')


def run(args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False)


def set_runner_variable(value):
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    token = os.environ.get('GITHUB_TOKEN', '')
    api_root = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')
    if not repository or not token:
        raise RuntimeError('GitHub repository or workflow token is unavailable.')
    base = api_root + '/repos/' + repository + '/actions/variables'
    payload = json.dumps({'name': 'RUN_ON_MAC', 'value': value}).encode('utf-8')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'}
    request = urllib.request.Request(base + '/RUN_ON_MAC', data=payload, headers=headers, method='PATCH')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in (200, 204):
                raise RuntimeError('GitHub runner variable update was rejected.')
            return
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError('GitHub runner variable update failed: HTTP ' + str(exc.code)) from None
    request = urllib.request.Request(base, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in (201, 204):
                raise RuntimeError('GitHub runner variable creation was rejected.')
    except urllib.error.HTTPError as exc:
        raise RuntimeError('GitHub runner variable creation failed: HTTP ' + str(exc.code)) from None


def main():
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print('Mac service bootstrap stopped; missing Actions secrets: ' + ', '.join(missing))
        return 2
    model = os.environ.get('OPENROUTER_MODEL', 'deepseek/deepseek-v4-flash:free')
    if not model.endswith(':free'):
        print('Mac service bootstrap stopped; select an OpenRouter model ending in :free.')
        return 2
    os.umask(0o077)
    APP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    APP_DIR.chmod(0o700)
    APP_CODE.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_config = APP_DIR / 'secrets.json'
    private_config.write_text(json.dumps({name: os.environ[name] for name in SECRET_NAMES
                                         if os.environ.get(name)}, ensure_ascii=False), encoding='utf-8')
    private_config.chmod(0o600)
    source = Path(__file__).resolve().parent
    ignore = shutil.ignore_patterns('.git', '.env', '.env.*', '__pycache__', 'bot_state*', 'tests', '.github')
    for child in source.iterdir():
        target = APP_CODE / child.name
        if child.name in {'.git', '.env', 'tests', '.github', '__pycache__'} or child.name.startswith('bot_state'):
            continue
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True, ignore=ignore)
        elif child.is_file():
            shutil.copy2(child, target)
    local_env = APP_CODE / '.env'
    if local_env.exists():
        local_env.unlink()

    launch_agents = Path.home() / 'Library' / 'LaunchAgents'
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / (LABEL + '.plist')
    app_dir = str(APP_DIR)
    plist = {
        'Label': LABEL,
        'ProgramArguments': [sys.executable, str(APP_CODE / 'local_daemon.py')],
        'WorkingDirectory': str(APP_CODE),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'StandardOutPath': app_dir + '/service.log',
        'StandardErrorPath': app_dir + '/service-error.log',
    }
    with plist_path.open('wb') as stream:
        plistlib.dump(plist, stream)
    plist_path.chmod(0o600)
    gui = 'gui/' + str(os.getuid())
    try:
        set_runner_variable('true')
        run(['/bin/launchctl', 'bootout', gui, str(plist_path)])
        boot = run(['/bin/launchctl', 'bootstrap', gui, str(plist_path)])
        if boot.returncode:
            raise RuntimeError('launchd bootstrap failed')
        start = run(['/bin/launchctl', 'kickstart', '-k', gui + '/' + LABEL])
        check = run(['/bin/launchctl', 'print', gui + '/' + LABEL])
        if start.returncode or check.returncode:
            raise RuntimeError('launchd service did not start')
    except Exception as exc:
        run(['/bin/launchctl', 'bootout', gui, str(plist_path)])
        try:
            set_runner_variable('false')
        except Exception:
            print('Bootstrap failed and GitHub schedule could not be restored automatically.')
        print('Mac service bootstrap failed safely:', type(exc).__name__)
        return 6
    print('Mac bot service started. GitHub scheduled publishing is now routed to the Mac service.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
