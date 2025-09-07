#!/usr/bin/env python3
import os
import docker
import requests
import tarfile
import traceback
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ------------------------------
# Configuration from environment
# ------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
AUTHORIZED_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
N8N_API_URL = os.environ.get("N8N_API_URL", "http://localhost:5678")
N8N_CONTAINER = os.environ.get("N8N_CONTAINER", "n8n")
N8N_DATA = os.environ.get("N8N_DATA", "/root/n8n_data")
N8N_BACKUPS = os.environ.get("N8N_BACKUPS", "/root/n8n_backups")
N8N_LOGS = os.environ.get("N8N_LOGS", "/var/log/n8n")

# Docker client
client = docker.from_env()

# ------------------------------
# Helpers
# ------------------------------
def is_authorized(user_id):
    return int(user_id) == int(AUTHORIZED_ID)

def api_headers():
    key = os.environ.get("N8N_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}

# ------------------------------
# n8n workflow API helpers
# ------------------------------
def list_workflows():
    try:
        r = requests.get(f"{N8N_API_URL}/workflows", headers=api_headers(), timeout=10)
        return r.json() if r.ok else []
    except:
        return []

def get_workflow(wf_id):
    try:
        r = requests.get(f"{N8N_API_URL}/workflows/{wf_id}", headers=api_headers(), timeout=10)
        return r.json() if r.ok else None
    except:
        return None

def run_workflow(wf_id):
    try:
        for endpoint in [f"{N8N_API_URL}/workflows/{wf_id}/execute", f"{N8N_API_URL}/workflows/{wf_id}/run"]:
            try:
                r = requests.post(endpoint, headers=api_headers(), timeout=30)
                if r.status_code in (200, 201, 202):
                    return {"ok": True, "response": r.json() if r.content else {"status": r.status_code}}
            except:
                continue
        return {"ok": False, "error": "Failed to trigger workflow"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def enable_workflow(wf_id):
    try:
        r = requests.post(f"{N8N_API_URL}/workflows/{wf_id}/activate", headers=api_headers(), timeout=10)
        return r.ok
    except:
        return False

def disable_workflow(wf_id):
    try:
        r = requests.post(f"{N8N_API_URL}/workflows/{wf_id}/deactivate", headers=api_headers(), timeout=10)
        return r.ok
    except:
        return False

def delete_workflow(wf_id):
    try:
        r = requests.delete(f"{N8N_API_URL}/workflows/{wf_id}", headers=api_headers(), timeout=10)
        return r.ok
    except:
        return False

def export_workflow(wf_id):
    wf = get_workflow(wf_id)
    if not wf:
        return None
    os.makedirs(N8N_BACKUPS, exist_ok=True)
    path = os.path.join(N8N_BACKUPS, f"workflow_{wf_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w") as f:
        f.write(requests.utils.json.dumps(wf))
    return path

def restore_workflow_from_file(path):
    try:
        with open(path, "r") as f:
            data = f.read()
        r = requests.post(f"{N8N_API_URL}/workflows/import", data=data, headers=api_headers(), timeout=30)
        return r.ok
    except:
        return False

# ------------------------------
# Docker container helpers
# ------------------------------
def get_container_status():
    try:
        c = client.containers.get(N8N_CONTAINER)
        return c.status
    except docker.errors.NotFound:
        return "not found"
    except:
        return "error"

def container_start():  # Start
    try:
        c = client.containers.get(N8N_CONTAINER)
        c.start()
        return True
    except:
        return False

def container_stop():  # Stop
    try:
        c = client.containers.get(N8N_CONTAINER)
        c.stop()
        return True
    except:
        return False

def container_restart():  # Restart
    try:
        c = client.containers.get(N8N_CONTAINER)
        c.restart()
        return True
    except:
        return False

# ------------------------------
# Backup/Restore helpers
# ------------------------------
def make_backup():
    os.makedirs(N8N_BACKUPS, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(N8N_BACKUPS, f"n8n_backup_{timestamp}.tar.gz")
    with tarfile.open(backup_file, "w:gz") as tar:
        if os.path.exists(N8N_DATA):
            tar.add(N8N_DATA, arcname="n8n_data")
        if os.path.exists(N8N_LOGS):
            tar.add(N8N_LOGS, arcname="n8n_logs")
    return backup_file

def tail_log(lines=50):
    log_file = os.path.join(N8N_LOGS, "n8n.log")
    if not os.path.exists(log_file):
        try:
            c = client.containers.get(N8N_CONTAINER)
            return c.logs(tail=lines).decode('utf-8', errors='ignore')
        except:
            return "No logs available."
    with open(log_file, "r") as f:
        data = f.readlines()
    return "".join(data[-lines:])

# ------------------------------
# Inline Keyboards
# ------------------------------
def main_keyboard():
    kb = [
        [InlineKeyboardButton("Backup n8n", callback_data="backup_n8n"),
         InlineKeyboardButton("Restore n8n", callback_data="restore_n8n")],
        [InlineKeyboardButton("Delete Logs", callback_data="delete_logs"),
         InlineKeyboardButton("Delete Backups", callback_data="delete_backups")],
        [InlineKeyboardButton("List Workflows", callback_data="list_workflows")],
        [InlineKeyboardButton("Status", callback_data="status")]
    ]
    return InlineKeyboardMarkup(kb)

def workflow_keyboard(workflows):
    kb = []
    for wf in workflows:
        wid = wf.get('id') or wf.get('workflowId') or wf.get('uuid') or str(wf.get('id'))
        name = wf.get('name') or wf.get('label') or f"workflow_{wid}"
        kb.append([
            InlineKeyboardButton(f"Run: {name}", callback_data=f"run_{wid}"),
            InlineKeyboardButton("Enable", callback_data=f"enable_{wid}"),
            InlineKeyboardButton("Disable", callback_data=f"disable_{wid}"),
            InlineKeyboardButton("Backup", callback_data=f"backup_{wid}"),
            InlineKeyboardButton("Delete", callback_data=f"delete_{wid}")
        ])
    return InlineKeyboardMarkup(kb)

# ------------------------------
# Handlers
# ------------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("Unauthorized")
        return
    await update.message.reply_text("n8n Manager Bot — Main Menu", reply_markup=main_keyboard())

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    text = (
        "Commands:\n"
        "/start - Show main menu\n"
        "/help - Show this help\n"
        "/status - Show server + n8n status\n"
        "/n8n_start - Start n8n container\n"
        "/n8n_stop - Stop n8n container\n"
        "/n8n_restart - Restart n8n container\n"
        "/n8n_logs - Tail n8n logs\n"
        "/backup - Create backup and send file\n"
        "/restore - Reply with a backup file to restore\n"
        "/list_workflows - List workflows (with buttons)\n"
        "Use inline buttons for quick actions."
    )
    await update.message.reply_text(text)

# ------------------------------
# Bot main
# ------------------------------
def main():
    if not BOT_TOKEN or AUTHORIZED_ID == 0:
        print("BOT_TOKEN or AUTHORIZED_ID not set in environment. Exiting.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    # Add all other handlers like n8n_start, n8n_stop, n8n_restart, logs, backup/restore...
    app.run_polling()

if __name__ == "__main__":
    main()
