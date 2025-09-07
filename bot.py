#!/usr/bin/env python3
import os
import docker
import requests
import tarfile
import shutil
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

# ------------------------------
# Docker client
# ------------------------------
client = docker.from_env()

def is_authorized(user_id):
    return int(user_id) == int(AUTHORIZED_ID)

def api_headers():
    key = os.environ.get("N8N_API_KEY")
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}

# ------------------------------
# n8n workflow helpers
# ------------------------------
def list_workflows():
    try:
        resp = requests.get(f"{N8N_API_URL}/workflows", headers=api_headers(), timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        return []

def get_workflow(wf_id):
    try:
        resp = requests.get(f"{N8N_API_URL}/workflows/{wf_id}", headers=api_headers(), timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        return None

def run_workflow(wf_id):
    try:
        for endpoint in [f"{N8N_API_URL}/workflows/{wf_id}/execute", f"{N8N_API_URL}/workflows/{wf_id}/run", f"{N8N_API_URL}/workflows/{wf_id}/executions"]:
            try:
                r = requests.post(endpoint, headers=api_headers(), timeout=30)
                if r.status_code in (200,201,202):
                    return {"ok": True, "response": r.json() if r.content else {"status": r.status_code}}
            except Exception:
                continue
        return {"ok": False, "error": "Failed to trigger workflow."}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def enable_workflow(wf_id):
    try:
        r = requests.post(f"{N8N_API_URL}/workflows/{wf_id}/activate", headers=api_headers(), timeout=10)
        return r.ok
    except Exception:
        return False

def disable_workflow(wf_id):
    try:
        r = requests.post(f"{N8N_API_URL}/workflows/{wf_id}/deactivate", headers=api_headers(), timeout=10)
        return r.ok
    except Exception:
        return False

def delete_workflow(wf_id):
    try:
        r = requests.delete(f"{N8N_API_URL}/workflows/{wf_id}", headers=api_headers(), timeout=10)
        return r.ok
    except Exception:
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
            content = f.read()
        r = requests.post(f"{N8N_API_URL}/workflows/import", data=content, headers=api_headers(), timeout=30)
        return r.ok
    except Exception:
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
    except Exception:
        return "error"

def container_restart():
    try:
        c = client.containers.get(N8N_CONTAINER)
        c.restart()
        return True
    except Exception:
        return False

def container_start():
    try:
        c = client.containers.get(N8N_CONTAINER)
        c.start()
        return True
    except Exception:
        return False

def container_stop():
    try:
        c = client.containers.get(N8N_CONTAINER)
        c.stop()
        return True
    except Exception:
        return False

# ------------------------------
# Filesystem helpers
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
        except Exception:
            return "No logs available."
    try:
        with open(log_file, "r") as f:
            data = f.readlines()
        return "".join(data[-lines:])
    except Exception:
        return "Failed to read log file."

# ------------------------------
# Inline keyboards
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
    if not is_authorized(update.effective_user.id):
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

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    cpu = os.popen("top -bn1 | grep 'Cpu' || true").read().strip()
    ram = os.popen("free -h || true").read().strip()
    disk = os.popen("df -h / || true").read().strip()
    c_status = get_container_status()
    msg = f"CPU: {cpu}\nRAM:\n{ram}\nDisk:\n{disk}\nn8n container: {c_status}"
    await update.message.reply_text(msg)

async def n8n_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = container_start()
    await update.message.reply_text("n8n started ✅" if ok else "Failed to start n8n ❌")

async def n8n_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = container_stop()
    await update.message.reply_text("n8n stopped ✅" if ok else "Failed to stop n8n ❌")

async def n8n_restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = container_restart()
    await update.message.reply_text("n8n restarted ✅" if ok else "Failed to restart n8n ❌")

async def n8n_logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logs = tail_log(100)
    if len(logs) > 4000:
        for i in range(0, len(logs), 3500):
            await update.message.reply_text(logs[i:i+3500])
    else:
        await update.message.reply_text(f"Logs:\n{logs}")

async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Creating backup...")
    try:
        path = make_backup()
        await update.message.reply_document(document=InputFile(path))
        await msg.edit_text("Backup completed ✅")
    except Exception as e:
        await msg.edit_text(f"Backup failed: {e}")

async def restore_cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please upload the backup (.tar.gz) or workflow (.json) file now.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text("No document found."); return
    file = await doc.get_file()
    fname = doc.file_name
    tmp = os.path.join("/tmp", fname)
    await file.download_to_drive(tmp)
    if fname.endswith(".tar.gz") or fname.endswith(".tgz"):
        try:
            with tarfile.open(tmp, "r:gz") as tar:
                tar.extractall(path="/")
            await update.message.reply_text("Full n8n backup restored ✅")
            container_restart()
        except Exception as e:
            await update.message.reply_text(f"Restore failed: {e}")
    elif fname.endswith(".json"):
        ok = restore_workflow_from_file(tmp)
        await update.message.reply_text("Workflow restore successful ✅" if ok else "Workflow restore failed ❌")
    else:
        await update.message.reply_text("Unsupported file type. Use .tar.gz or .json")

# ------------------------------
# Inline buttons callback
# ------------------------------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        if data == "backup_n8n":
            path = make_backup()
            await query.message.reply_document(document=InputFile(path))
            await query.edit_message_text("Backup completed ✅")
        elif data == "restore_n8n":
            await query.edit_message_text("Please upload backup (.tar.gz) or workflow (.json) file.")
        elif data == "delete_logs":
            if os.path.exists(N8N_LOGS):
                for f in os.listdir(N8N_LOGS):
                    p = os.path.join(N8N_LOGS, f)
                    if os.path.isfile(p): os.remove(p)
            await query.edit_message_text("Logs deleted ✅")
        elif data == "delete_backups":
            if os.path.exists(N8N_BACKUPS):
                for f in os.listdir(N8N_BACKUPS):
                    p = os.path.join(N8N_BACKUPS, f)
                    if os.path.isfile(p): os.remove(p)
            await query.edit_message_text("Backups deleted ✅")
        elif data == "list_workflows":
            wfs = list_workflows()
            if not wfs:
                await query.edit_message_text("No workflows found or API inaccessible.")
            else:
                await query.edit_message_text("Select a workflow:", reply_markup=workflow_keyboard(wfs))
        elif data.startswith("run_"):
            wf_id = data.split("_",1)[1]
            res = run_workflow(wf_id)
            await query.edit_message_text(f"Run result: {res}")
        elif data.startswith("enable_"):
            wf_id = data.split("_",1)[1]
            ok = enable_workflow(wf_id)
            await query.edit_message_text("Workflow enabled ✅" if ok else "Enable failed ❌")
        elif data.startswith("disable_"):
            wf_id = data.split("_",1)[1]
            ok = disable_workflow(wf_id)
            await query.edit_message_text("Workflow disabled ✅" if ok else "Disable failed ❌")
        elif data.startswith("backup_"):
            wf_id = data.split("_",1)[1]
            path = export_workflow(wf_id)
            if path:
                await query.message.reply_document(document=InputFile(path))
                await query.edit_message_text("Workflow backup sent ✅")
            else:
                await query.edit_message_text("Export failed ❌")
        elif data.startswith("delete_"):
            wf_id = data.split("_",1)[1]
            ok = delete_workflow(wf_id)
            await query.edit_message_text("Workflow deleted ✅" if ok else "Delete failed ❌")
        elif data == "status":
            cpu = os.popen("top -bn1 | grep 'Cpu' || true").read().strip()
            ram = os.popen("free -h || true").read().strip()
            disk = os.popen("df -h / || true").read().strip()
            c_status = get_container_status()
            msg = f"CPU: {cpu}\nRAM:\n{ram}\nDisk:\n{disk}\nn8n container: {c_status}"
            await query.edit_message_text(msg)
        else:
            await query.edit_message_text("Unknown action.")
    except Exception as e:
        await query.edit_message_text(f"Action failed: {e}\n{traceback.format_exc()}")

# ------------------------------
# Main
# ------------------------------
def main():
    if not BOT_TOKEN or AUTHORIZED_ID == 0:
        print("BOT_TOKEN or AUTHORIZED_ID not set in environment. Exiting.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("n8n_start", n8n_start_cmd))
    app.add_handler(CommandHandler("n8n_stop", n8n_stop_cmd))
    app.add_handler(CommandHandler("n8n_restart", n8n_restart_cmd))
    app.add_handler(CommandHandler("n8n_logs", n8n_logs_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd_prompt))
    app.add_handler(CommandHandler("list_workflows", lambda u,c: u.message.reply_text('Use /start and the List Workflows button.')))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # File handler
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    print("Bot is starting...")
    app.run_polling()

if __name__ == '__main__':
    main()
