#!/usr/bin/env bash
# No cloud resource creation, credential input or trading command is performed.
set -Eeuo pipefail
umask 077
mode="${1:---status}"
[[ $# -le 1 && ( "$mode" == --status || "$mode" == --connect ) ]] || exit 2
session=sinka-demo-control
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
storage="$HOME/sinka-demo-trial"
python="$HOME/sinka-demo-venv/bin/python"
log="$storage/control-tmux.log"
command -v tmux >/dev/null || { echo '缺少 tmux，尚未啟動執行器。' >&2; exit 2; }
if tmux has-session -t "=$session" 2>/dev/null; then
  echo '控制器工作階段已存在，保留原程序，沒有重複啟動。'
  echo '請到系統按「連線／更新狀態」，確認最新心跳與是否需要恢復核對。'
  exit 0
fi
if pgrep -af '[o]kx_demo_control_runner.py|[o]kx_demo_cloudshell.py|[o]kx_demo_trading_worker.py' >/dev/null; then
  echo '偵測到既有執行器或交易程序，沒有重複啟動。請先核對原程序。'
  exit 0
fi
if [[ "$mode" == --status ]]; then
  echo '目前沒有找到控制器工作階段或交易程序。'; exit 0
fi
[[ -x "$python" && -f "$storage/account.json" && -f "$storage/ledger.sqlite" ]] || {
  echo '找不到原環境或原帳本，停止操作；不建立新帳本。' >&2; exit 2;
}
[[ ! -e "$storage/migrated-to-vm.json" && ! -L "$storage/migrated-to-vm.json" && ! -L "$log" ]] || {
  echo '來源已搬遷或日誌路徑不安全，沒有啟動。' >&2; exit 2;
}
touch "$log"; chmod 0600 "$log"
printf -v launch 'exec %q -u %q --connect --seconds 14400 >>%q 2>&1' \
  "$python" "$root/backend/okx_demo_control_runner.py" "$log"
tmux new-session -d -s "$session" "$launch"
echo '已建立獨立終端工作階段，控制器最多連線 4 小時；此步未送出交易指令。'
echo '請到系統按「連線／更新狀態」；若顯示需要核對，先按「恢復既有交易」。'
echo '終端斷線不會主動停止工作階段，但 Cloud Shell 主機回收或程序錯誤仍可能中斷。'
echo '停止交易請使用系統停止按鈕；關閉這個畫面不等於停止交易。'
