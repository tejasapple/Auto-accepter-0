# pip install motor python-telegram-bot python-dotenv
import os
import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import telegram
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatJoinRequest,
    BotCommand
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatJoinRequestHandler,
    ContextTypes,
    filters,
)

# ==========================================
# 🔐 ENVIRONMENT VARIABLES SETUP
# ==========================================
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==========================================
# 🛠️ LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# ⚙️ CONFIGURATION (.env Supported)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8963867350:AAE8ze1jqS30Vzc7PMoySpc5uq-5EIBz7V4")
MONGO_DB_URI = os.getenv("MONGO_DB_URI", "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7121137252"))

# ==========================================
# 🗄️ DATABASE SETUP (MongoDB)
# ==========================================
logger.info("Connecting to MongoDB...")
db_client = AsyncIOMotorClient(MONGO_DB_URI)
db = db_client["AutoAcceptBot"]
users_col = db["users"]
chats_col = db["chats"]

# Broadcast State Manager (Memory)
bcast_state: Dict[int, Dict[str, Any]] = {}

# ==========================================
# 🗃️ DATABASE HELPER FUNCTIONS
# ==========================================
async def save_user(user: telegram.User) -> None:
    """Saves or updates a user in the database without deleting old data."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        await users_col.update_one(
            {"user_id": user.id}, 
            {
                "$set": {
                    "name": user.first_name,
                    "username": user.username or "None",
                    "last_active": today
                }, 
                "$setOnInsert": {"date": today}
            }, 
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving user {user.id}: {e}")

async def save_chat(chat: telegram.Chat) -> None:
    """Saves or updates a chat in the database."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        await chats_col.update_one(
            {"chat_id": chat.id}, 
            {
                "$set": {
                    "title": chat.title,
                    "username": chat.username or "None",
                    "type": chat.type,
                    "last_active": today
                }, 
                "$setOnInsert": {"date": today}
            }, 
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving chat {chat.id}: {e}")

# ==========================================
# 🎨 COLOR BUTTONS HELPER (PTB api_kwargs)
# ==========================================
def normalize_style(value: str) -> str:
    """Normalizes color string for Telegram inline buttons."""
    value = (value or "").strip().lower()
    if value in {"success", "green", "paid"}:
        return "success"
    if value in {"danger", "red", "delete", "disable"}:
        return "danger"
    if value in {"default", "gray", "grey", "cancel"}:
        return "default"
    return "primary"

def get_color_btn(text: str, callback_data: Optional[str] = None, url: Optional[str] = None, style: str = "primary") -> InlineKeyboardButton:
    """Helper method to generate an InlineKeyboardButton with dynamic color support."""
    kwargs = {"api_kwargs": {"style": normalize_style(style)}}
    if url:
        return InlineKeyboardButton(text=text, url=url, **kwargs)
    return InlineKeyboardButton(text=text, callback_data=callback_data, **kwargs)

# ==========================================
# 🚀 START COMMAND & HELP
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    message = update.message
    if not message:
        return
        
    user = message.from_user
    bot = context.bot
    
    # Save user to DB immediately
    await save_user(user)
    
    admin_rights = "invite_users+manage_chat+restrict_members+promote_members+change_info+post_messages+edit_messages+delete_messages"
    
    keyboard = InlineKeyboardMarkup([
        [get_color_btn("➕ Add to your Group", url=f"https://t.me/{bot.username}?startgroup=true&admin={admin_rights}", style="success")],
        [get_color_btn("📢 Add to your Channel", url=f"https://t.me/{bot.username}?startchannel=true&admin={admin_rights}", style="primary")]
    ])
    
    # Updated Description 
    text = (
        f"<blockquote>👋 <b>WELCOME TO AUTO ACCEPT BOT</b></blockquote>\n\n"
        f"Hello <b>{user.first_name}</b>!\n\n"
        f"I am an advanced and lightning-fast Auto-Accept Bot. Add me to your Channel or Group as an Admin to automatically accept join requests instantly and I also delete join/left events to keep your group chat clean.\n\n"
        f"<i>⚠️ Note: Please make sure 'Remain Anonymous' permission is turned OFF.</i>"
    )
    
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /help command."""
    if not update.message:
        return
        
    user = update.message.from_user
    await save_user(user)
    
    # Updated Help Description
    text = (
        f"<blockquote>🛡️ <b>BOT HELP CENTER</b></blockquote>\n\n"
        f"<b>How to use me?</b>\n"
        f"1. Add me to your Group or Channel.\n"
        f"2. Promote me as an Admin with 'Invite Users' and 'Delete Messages' rights.\n"
        f"3. Turn on 'Approve New Members' in your group/channel settings.\n\n"
        f"Whenever someone requests to join, I will automatically accept them instantly, delete the join/left events, and send them a welcome DM!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ==========================================
# 🛡️ INSTANT AUTO ACCEPT & VERIFICATION DM 
# ==========================================
async def auto_accept_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles new chat join requests: Accepts instantly, then sends DM in background."""
    request = update.chat_join_request
    if not request:
        return
        
    chat = request.chat
    user = request.from_user
    
    # 1. ACCEPT THE JOIN REQUEST AUTOMATICALLY (INSTANTLY) - FIRST PRIORITY
    try:
        await context.bot.approve_chat_join_request(chat_id=chat.id, user_id=user.id)
        logger.info(f"Instantly approved {user.id} in {chat.id}")
    except Exception as e:
        logger.error(f"Error approving {user.id} in {chat.id}: {e}")

    # 2. BACKGROUND TASK: SAVE DB & SEND DM (Zero Delay to Acceptance)
    async def background_operations() -> None:
        # Save user and chat to database concurrently
        await asyncio.gather(
            save_user(user),
            save_chat(chat)
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Verify I am not a robot", callback_data="fake_verify")]
        ])
        
        text = (
            f"<blockquote>🛡️ <b>SECURITY VERIFICATION</b></blockquote>\n\n"
            f"Hello <b>{user.first_name}</b>,\n\n"
            f"To complete your joining process for <b>{chat.title}</b>, please verify that you are human by clicking the button below."
        )
        
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                await context.bot.send_message(
                    chat_id=user.id, 
                    text=text, 
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Verification DM sent to {user.id}")
                break
                
            except telegram.error.RetryAfter as e:
                logger.warning(f"Flood limit! Sleeping for {e.retry_after}s before DM to {user.id}")
                await asyncio.sleep(e.retry_after)
                    
            except (telegram.error.TimedOut, telegram.error.NetworkError) as e:
                if attempt < max_retries:
                    await asyncio.sleep(2)
                else:
                    logger.error(f"Network error DMing {user.id}: {e}")
                    
            except telegram.error.Forbidden:
                logger.info(f"User {user.id} blocked the bot. They are saved in DB, but DM failed.")
                break 
                
            except telegram.error.BadRequest as e:
                logger.error(f"Bad Request for {user.id}: {e}")
                break
                
            except Exception as e:
                if attempt < max_retries:
                    await asyncio.sleep(2)
                else:
                    logger.error(f"Failed to DM {user.id}: {e}")

    # Fire and forget the background operations task to ensure instant API response
    asyncio.create_task(background_operations())

# ==========================================
# 🧹 CLEAN JOIN/LEFT EVENTS
# ==========================================
async def clean_service_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deletes 'user joined' and 'user left' service messages instantly."""
    try:
        if update.message:
            await update.message.delete()
    except telegram.error.BadRequest as e:
        logger.warning(f"Could not delete service message: {e}")
    except Exception as e:
        logger.error(f"Error deleting service message: {e}")

# ==========================================
# ⚙️ ADVANCED ADMIN PANEL DASHBOARD
# ==========================================
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the Admin Dashboard."""
    if not update.effective_user or not update.message:
        return
        
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
        
    keyboard = InlineKeyboardMarkup([
        [get_color_btn("📊 View Bot Live Stats", callback_data="admin_stats", style="primary")],
        [get_color_btn("📢 Broadcast to Users (DM)", callback_data="bcast_users", style="success")],
        [get_color_btn("📢 Broadcast to Groups/Channels", callback_data="bcast_chats", style="danger")]
    ])
    
    text = (
        f"<blockquote>⚙️ <b>ADVANCED ADMIN PANEL</b></blockquote>\n\n"
        f"Welcome to the Enterprise Admin Dashboard. Manage your bot's statistics and broadcast systems directly from here."
    )
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

# ==========================================
# 📊 EXPORT DATA TO CSV (ADMIN ONLY)
# ==========================================
async def export_users_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exports all users from MongoDB to a CSV file."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
        
    if not update.message:
        return
        
    processing_msg = await update.message.reply_text("🔄 Fetching users data... Please wait.")
    users = await users_col.find({}).to_list(length=None)
    
    if not users:
        await processing_msg.edit_text("⚠️ No users found in database.")
        return
        
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Name", "Username", "Join Date", "Last Active"])
    
    for u in users:
        writer.writerow([
            u.get("user_id", "N/A"), 
            u.get("name", "N/A"), 
            u.get("username", "N/A"), 
            u.get("date", "N/A"),
            u.get("last_active", "N/A")
        ])
        
    output.seek(0)
    file_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
    file_bytes.name = f"Users_Export_{datetime.now().strftime('%Y%m%d')}.csv"
    
    await context.bot.send_document(
        chat_id=ADMIN_ID, 
        document=file_bytes, 
        caption=f"✅ <b>Users Export Completed</b>\nTotal Users: <code>{len(users)}</code>", 
        parse_mode=ParseMode.HTML
    )
    await processing_msg.delete()

# ==========================================
# 🎛️ CALLBACK QUERY ROUTER (ALL BUTTONS)
# ==========================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Routes all inline button clicks to their appropriate functions."""
    query = update.callback_query
    if not query:
        return
        
    data = query.data
    user = query.from_user
    uid = user.id
    
    try:
        await query.answer()
    except Exception:
        pass

    # 1. FAKE VERIFICATION BUTTON HANDLER
    if data == "fake_verify":
        try:
            await query.answer("✅ Verification Successful! You can now access the group.", show_alert=True)
            await query.message.edit_text(
                f"<blockquote>✅ <b>VERIFICATION SUCCESSFUL</b></blockquote>\n\n"
                f"Thank you <b>{user.first_name}</b>, you have been successfully verified and added to the group!",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    # 2. Admin Live Stats
    if data == "admin_stats" and uid == ADMIN_ID:
        today = datetime.now().strftime("%Y-%m-%d")
        total_users = await users_col.count_documents({})
        today_users = await users_col.count_documents({"date": today})
        total_chats = await chats_col.count_documents({})
        today_chats = await chats_col.count_documents({"date": today})
        
        text = (
            f"<blockquote>📊 <b>DATABASE LIVE STATISTICS</b></blockquote>\n\n"
            f"👤 <b>Total Verified Users:</b> <code>{total_users}</code>\n"
            f"🆕 <b>Today's New Users:</b> <code>{today_users}</code>\n\n"
            f"👥 <b>Total Groups/Channels:</b> <code>{total_chats}</code>\n"
            f"🆕 <b>Today's New Groups/Channels:</b> <code>{today_chats}</code>\n\n"
            f"<i>💡 Tip: Send /export_users to get full database in CSV.</i>"
        )
        keyboard = InlineKeyboardMarkup([[get_color_btn("⬅️ Back to Admin Panel", callback_data="back_to_admin", style="default")]])
        if query.message:
            await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    # 3. Back to Admin Panel
    if data == "back_to_admin" and uid == ADMIN_ID:
        keyboard = InlineKeyboardMarkup([
            [get_color_btn("📊 View Bot Live Stats", callback_data="admin_stats", style="primary")],
            [get_color_btn("📢 Broadcast to Users (DM)", callback_data="bcast_users", style="success")],
            [get_color_btn("📢 Broadcast to Groups/Channels", callback_data="bcast_chats", style="danger")]
        ])
        text = (
            f"<blockquote>⚙️ <b>ADVANCED ADMIN PANEL</b></blockquote>\n\n"
            f"Welcome to the Enterprise Admin Dashboard. Manage your bot's statistics and broadcast systems directly from here."
        )
        if query.message:
            await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    # 4. Initiate Broadcast Flow
    if data in ["bcast_users", "bcast_chats"] and uid == ADMIN_ID:
        btype = "users" if data == "bcast_users" else "chats"
        bcast_state[uid] = {
            "type": btype,
            "step": "media",
            "media_type": None,
            "media_id": None,
            "text": None,
            "target_button_count": 0,
            "current_button_index": 0,
            "buttons": [],
            "temp_name": "",
            "temp_url": ""
        }
        target_name = "Verified Users (DM)" if btype == "users" else "Groups & Channels"
        text = (
            f"<blockquote>📢 <b>BROADCAST WIZARD</b></blockquote>\n\n"
            f"<b>Target:</b> {target_name}\n\n"
            f"<b>Step 1:</b> Send <b>Media (Photo/Video/Audio/Doc)</b> for the broadcast.\n\n"
            f"<i>(Type /skip if you only want to send a text message)</i>"
        )
        if query.message:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML)
        return

    # 5. Broadcast Color Button Selection
    if data and data.startswith("setcol_") and uid == ADMIN_ID:
        if uid not in bcast_state or bcast_state[uid]["step"] != "btn_color":
            try: 
                await query.answer("Session expired or invalid step.", show_alert=True)
            except Exception: 
                pass
            return
            
        color_choice = data.split("_")[1]
        state = bcast_state[uid]
        
        state["buttons"].append({
            "name": state["temp_name"],
            "url": state["temp_url"],
            "style": color_choice
        })
        state["current_button_index"] += 1
        
        if query.message:
            try: 
                await query.message.delete()
            except Exception: 
                pass
        
        if state["current_button_index"] < state["target_button_count"]:
            state["step"] = "btn_name"
            next_num = state["current_button_index"] + 1
            await context.bot.send_message(
                uid, 
                f"✅ <b>Button {next_num-1} Configured!</b>\n\n📝 <b>Configuring Button {next_num}:</b>\nPlease send the <b>Name (Text)</b> for this button.", 
                parse_mode=ParseMode.HTML
            )
        else:
            state["step"] = "confirm"
            await context.bot.send_message(
                uid, 
                "✅ <b>All Buttons Configured Successfully!</b>\n\nAll set! Type <b>/confirm</b> to start the broadcast or <b>/cancel</b> to abort.", 
                parse_mode=ParseMode.HTML
            )
        return

# ==========================================
# 📢 BROADCAST WIZARD PROCESSORS
# ==========================================
async def cancel_bcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels the ongoing broadcast setup."""
    if not update.effective_user or not update.message:
        return
        
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        if uid in bcast_state:
            del bcast_state[uid]
            await update.message.reply_text("❌ <b>Broadcast Process Cancelled Successfully.</b>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("You don't have any active broadcast setup running.")

async def process_broadcast_steps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the sequential steps of broadcast setup (Media -> Text -> Buttons)."""
    if not update.effective_user or not update.message:
        return
        
    uid = update.effective_user.id
    if uid != ADMIN_ID or uid not in bcast_state:
        return
        
    state = bcast_state[uid]
    step = state["step"]
    message = update.message
    
    # Strict skip check to handle empty captions and actual /skip command
    raw_text = message.text or message.caption or ""
    is_skip_cmd = raw_text.strip().lower() == "/skip"
    
    if step == "media":
        if is_skip_cmd:
            state["step"] = "text"
            await message.reply_text("⏭ <b>Media Skipped.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.photo:
            state["media_type"] = "photo"
            state["media_id"] = message.photo[-1].file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Photo Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.video:
            state["media_type"] = "video"
            state["media_id"] = message.video.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Video Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.document:
            state["media_type"] = "document"
            state["media_id"] = message.document.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Document Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.audio:
            state["media_type"] = "audio"
            state["media_id"] = message.audio.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Audio Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.animation:
            state["media_type"] = "animation"
            state["media_id"] = message.animation.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>GIF/Animation Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.voice:
            state["media_type"] = "voice"
            state["media_id"] = message.voice.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Voice Note Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text("⚠️ Please send a Media File (Photo/Video/Doc/Audio/GIF) or type /skip.")
            
    elif step == "text":
        if is_skip_cmd:
            if not state["media_id"]:
                await message.reply_text("⚠️ You cannot skip both Media and Text! Please send some text.")
                return
            state["text"] = None  
            state["step"] = "btn_count"
            await message.reply_text("⏭ <b>Text Skipped.</b>\n\n<b>Step 3:</b> How many Inline Buttons do you want? (Send a number like 0, 1, 2, etc.)", parse_mode=ParseMode.HTML)
        else:
            state["text"] = message.text_html if message.text else (message.caption_html if message.caption else raw_text)
            state["step"] = "btn_count"
            await message.reply_text("✅ <b>Text Saved.</b>\n\n<b>Step 3:</b> How many Inline Buttons do you want? (Send a number like 0, 1, 2, etc.)", parse_mode=ParseMode.HTML)
            
    elif step == "btn_count":
        try: 
            count = int(raw_text)
        except ValueError:
            await message.reply_text("⚠️ Please send a valid number (e.g., 0, 1, 2).")
            return
            
        if count == 0:
            state["step"] = "confirm"
            await message.reply_text("✅ <b>No buttons selected.</b>\n\nAll set! Type <b>/confirm</b> to start the broadcast or <b>/cancel</b> to abort.", parse_mode=ParseMode.HTML)
        else:
            state["target_button_count"] = count
            state["current_button_index"] = 0
            state["step"] = "btn_name"
            await message.reply_text("📝 <b>Configuring Button 1:</b>\n\nPlease send the <b>Name (Text)</b> for this button.", parse_mode=ParseMode.HTML)
            
    elif step == "btn_name":
        state["temp_name"] = raw_text
        state["step"] = "btn_url"
        await message.reply_text("✅ <b>Name Saved.</b>\n\nNow send the <b>URL (Link)</b> for this button (must start with http:// or https://).", parse_mode=ParseMode.HTML)
        
    elif step == "btn_url":
        if not raw_text.startswith("http"):
            await message.reply_text("⚠️ Invalid Link! Please send a valid link starting with http:// or https://")
            return
            
        state["temp_url"] = raw_text
        state["step"] = "btn_color"
        
        # Color Selection Keyboard
        kb = InlineKeyboardMarkup([
            [get_color_btn("🟢 Success (Green)", callback_data="setcol_success", style="success"), 
             get_color_btn("🔴 Danger (Red)", callback_data="setcol_danger", style="danger")],
            [get_color_btn("🔵 Primary (Blue)", callback_data="setcol_primary", style="primary"), 
             get_color_btn("⚪ Default (Gray)", callback_data="setcol_default", style="default")]
        ])
        await message.reply_text("🎨 <b>Select Button Color:</b>\n\nChoose a color for this button from the menu below:", reply_markup=kb, parse_mode=ParseMode.HTML)

async def confirm_bcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm and launch the broadcast."""
    if not update.effective_user or not update.message:
        return
        
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        if uid not in bcast_state or bcast_state[uid]["step"] != "confirm":
            await update.message.reply_text("⚠️ No broadcast is waiting for confirmation. Use /admin to start a new one.")
            return
            
        state = bcast_state[uid]
        await update.message.reply_text("🚀 <b>Broadcast Starting... Please wait. I will notify you when it finishes.</b>", parse_mode=ParseMode.HTML)
        
        # Run broadcast in background
        asyncio.create_task(execute_broadcast(context, uid, state))
        del bcast_state[uid]

async def execute_broadcast(context: ContextTypes.DEFAULT_TYPE, admin_id: int, state: dict) -> None:
    """Executes the broadcast loop seamlessly with Async Cursors for High Scalability."""
    btype = state["type"]
    success, failed = 0, 0
    
    # Setup Inline Keyboard
    inline_buttons = []
    for btn in state["buttons"]:
        inline_buttons.append([get_color_btn(btn["name"], url=btn["url"], style=btn["style"])])
        
    kb = InlineKeyboardMarkup(inline_buttons) if inline_buttons else None
    
    # Resolve Text for Media Captions
    msg_text = state["text"] if state["text"] else ""
    
    # Choose Collection
    collection = users_col if btype == "users" else chats_col
    id_key = "user_id" if btype == "users" else "chat_id"
        
    # SCALABILITY FIX: Using async cursor prevents loading 2 Lakh users in RAM at once.
    cursor = collection.find({})
    
    async for target in cursor:
        tid = target.get(id_key)
        if not tid:
            continue
            
        try:
            # Handle all media types robustly
            if state["media_type"] == "photo":
                await context.bot.send_photo(chat_id=tid, photo=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "video":
                await context.bot.send_video(chat_id=tid, video=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "document":
                await context.bot.send_document(chat_id=tid, document=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "audio":
                await context.bot.send_audio(chat_id=tid, audio=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "animation":
                await context.bot.send_animation(chat_id=tid, animation=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "voice":
                await context.bot.send_voice(chat_id=tid, voice=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                # Text only broadcast (Media was skipped)
                await context.bot.send_message(chat_id=tid, text=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            
            success += 1
            await asyncio.sleep(0.05) # Safe rate limiting delay (20 msgs per sec avoids FloodWait Limits)
            
        except telegram.error.RetryAfter as e:
            logger.warning(f"Broadcast FloodWait for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            try:
                # Retry once after sleep
                if state["media_type"] == "photo":
                    await context.bot.send_photo(chat_id=tid, photo=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "video":
                    await context.bot.send_video(chat_id=tid, video=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "document":
                    await context.bot.send_document(chat_id=tid, document=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "audio":
                    await context.bot.send_audio(chat_id=tid, audio=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "animation":
                    await context.bot.send_animation(chat_id=tid, animation=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "voice":
                    await context.bot.send_voice(chat_id=tid, voice=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_message(chat_id=tid, text=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                success += 1
            except Exception:
                failed += 1
                
        except Exception as e:
            logger.info(f"Broadcast to {tid} failed. Reason: {e}")
            failed += 1
            # User Data will NOT be deleted here to ensure total persistence
                
    # Final Notification
    await context.bot.send_message(
        admin_id, 
        f"<blockquote>✅ <b>BROADCAST COMPLETED</b></blockquote>\n\n"
        f"🎯 <b>Successfully Sent:</b> <code>{success}</code>\n"
        f"🚫 <b>Failed (Blocked/Dead):</b> <code>{failed}</code>\n\n"
        f"<i>Note: Failed users are NOT deleted from the database. Their data is safe.</i>",
        parse_mode=ParseMode.HTML
    )

# ==========================================
# ⚙️ BOT INITIALIZATION & COMMAND SETUP
# ==========================================
async def post_init(application: Application) -> None:
    """Sets up the bot commands menu automatically."""
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Get help and instructions"),
        BotCommand("admin", "Open Advanced Admin Panel")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands successfully updated!")

# ==========================================
# 🏃 RUN THE BOT
# ==========================================
def main() -> None:
    logger.info("Bot is Starting... ✅")
    
    # Application Builder
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Basic Commands
    app.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
    
    # Admin Commands
    app.add_handler(CommandHandler("admin", admin_dashboard, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("cancel", cancel_bcast, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("confirm", confirm_bcast, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("export_users", export_users_csv, filters=filters.ChatType.PRIVATE))
    
    # Explicitly handled /skip command for Broadcast Wizard
    app.add_handler(CommandHandler("skip", process_broadcast_steps, filters=filters.ChatType.PRIVATE))
    
    # Core Handlers
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(ChatJoinRequestHandler(auto_accept_requests))
    
    # New Handler: Clean User Join/Left events automatically
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, clean_service_messages))
    
    # Broadcast Wizard Step Handler (Handles Text, Photo, Video, Doc, Audio, Animation, Voice)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (
            filters.PHOTO | filters.VIDEO | filters.TEXT | filters.Document.ALL | 
            filters.AUDIO | filters.ANIMATION | filters.VOICE
        ) & ~filters.COMMAND, 
        process_broadcast_steps
    ))
    
    # Start Polling
    app.run_polling(allowed_updates=["message", "callback_query", "chat_join_request"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
