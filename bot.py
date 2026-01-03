import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp
import asyncio
from datetime import datetime, timedelta
import os
from typing import List, Dict
import time
import secrets
import hashlib
from bson import ObjectId

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
MONGODB_URI = os.getenv('MONGODB_URI')
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []

# MongoDB setup
client = AsyncIOMotorClient(MONGODB_URI)
db = client['uptime_bot']
websites_collection = db['websites']
users_collection = db['users']
stats_collection = db['stats']
api_keys_collection = db['api_keys']
monitoring_tasks = {}

# Monitoring intervals
INTERVALS = {
    '10sec': 10,
    '30sec': 30,
    '1min': 60,
    '2min': 120,
    '3min': 180,
    '5min': 300,
    '10min': 600,
    '15min': 900,
    '30min': 1800,
    '1hour': 3600
}

class UptimeMonitor:
    def __init__(self):
        self.monitoring = {}
        
    async def check_website(self, url: str) -> Dict:
        """Check if a website is up and return status info"""
        start_time = time.time()
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as response:
                    response_time = int((time.time() - start_time) * 1000)
                    return {
                        'status': 'up' if response.status < 500 else 'down',
                        'status_code': response.status,
                        'response_time': response_time,
                        'checked_at': datetime.utcnow(),
                        'error': None
                    }
        except asyncio.TimeoutError:
            return {
                'status': 'down',
                'status_code': 0,
                'response_time': int((time.time() - start_time) * 1000),
                'checked_at': datetime.utcnow(),
                'error': 'Timeout'
            }
        except Exception as e:
            return {
                'status': 'down',
                'status_code': 0,
                'response_time': int((time.time() - start_time) * 1000),
                'checked_at': datetime.utcnow(),
                'error': str(e)
            }

monitor = UptimeMonitor()

async def generate_api_key(user_id: int) -> str:
    """Generate a unique API key for a user"""
    api_key = secrets.token_urlsafe(32)
    
    await api_keys_collection.insert_one({
        'user_id': user_id,
        'api_key': api_key,
        'created_at': datetime.utcnow(),
        'last_used': None,
        'requests_count': 0,
        'active': True
    })
    
    return api_key

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    
    await users_collection.update_one(
        {'user_id': user.id},
        {
            '$set': {
                'user_id': user.id,
                'username': user.username,
                'first_name': user.first_name,
                'joined_at': datetime.utcnow()
            }
        },
        upsert=True
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Website", callback_data='add_website')],
        [InlineKeyboardButton("📊 My Websites", callback_data='list_websites')],
        [InlineKeyboardButton("🔑 API Key", callback_data='api_key_menu'),
         InlineKeyboardButton("📈 Statistics", callback_data='show_stats')],
        [InlineKeyboardButton("📖 API Docs", callback_data='api_docs'),
         InlineKeyboardButton("ℹ️ Help", callback_data='help')]
    ]
    
    if user.id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("📢 Broadcast", callback_data='broadcast')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""
🤖 **Welcome to Advanced Uptime Monitor Bot!**

Hi {user.first_name}! Monitor your websites with custom intervals!

**Features:**
✅ Custom monitoring intervals (10s - 1h)
📊 Detailed statistics & reports
⚡ Instant downtime alerts
🔑 API key generation
📡 REST API access
🔔 Smart notifications

**Get Started:**
Add your first website to start monitoring!
"""
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def add_website_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the add website process"""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text(
        "🌐 **Add New Website**\n\n"
        "Please send me the website URL to monitor.\n"
        "Example: `https://example.com`\n\n"
        "Use /cancel to cancel.",
        parse_mode='Markdown'
    )
    context.user_data['awaiting_url'] = True

async def select_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show interval selection"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("⚡ 10 seconds", callback_data='interval_10sec'),
         InlineKeyboardButton("⚡ 30 seconds", callback_data='interval_30sec')],
        [InlineKeyboardButton("🕐 1 minute", callback_data='interval_1min'),
         InlineKeyboardButton("🕑 2 minutes", callback_data='interval_2min')],
        [InlineKeyboardButton("🕒 3 minutes", callback_data='interval_3min'),
         InlineKeyboardButton("🕔 5 minutes", callback_data='interval_5min')],
        [InlineKeyboardButton("🕙 10 minutes", callback_data='interval_10min'),
         InlineKeyboardButton("🕞 15 minutes", callback_data='interval_15min')],
        [InlineKeyboardButton("🕥 30 minutes", callback_data='interval_30min'),
         InlineKeyboardButton("🕐 1 hour", callback_data='interval_1hour')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "⏱️ **Select Check Interval**\n\n"
        f"🌐 URL: `{context.user_data.get('pending_url')}`\n\n"
        "Choose how often to check this website:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_interval_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, interval_key: str):
    """Handle interval selection and save website"""
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get('pending_url')
    interval_seconds = INTERVALS[interval_key]
    
    # Initial check
    result = await monitor.check_website(url)
    
    # Save to database
    website_data = {
        'user_id': query.from_user.id,
        'url': url,
        'interval': interval_key,
        'interval_seconds': interval_seconds,
        'added_at': datetime.utcnow(),
        'status': result['status'],
        'last_checked': result['checked_at'],
        'last_status_code': result['status_code'],
        'last_response_time': result['response_time'],
        'uptime_percentage': 100.0,
        'total_checks': 1,
        'successful_checks': 1 if result['status'] == 'up' else 0,
        'notifications_enabled': True
    }
    
    inserted = await websites_collection.insert_one(website_data)
    website_id = str(inserted.inserted_id)
    
    # Start monitoring task
    task = asyncio.create_task(monitor_website(website_id, query.from_user.id))
    monitoring_tasks[website_id] = task
    
    status_emoji = "🟢" if result['status'] == 'up' else "🔴"
    
    await query.message.reply_text(
        f"✅ **Website Added Successfully!**\n\n"
        f"🌐 URL: `{url}`\n"
        f"{status_emoji} Status: {result['status'].upper()}\n"
        f"⚡ Response Time: {result['response_time']}ms\n"
        f"⏱️ Check Interval: **{interval_key.replace('sec', ' sec').replace('min', ' min').replace('hour', ' hour')}**\n"
        f"🔔 Notifications: Enabled\n\n"
        f"Monitoring started! 🚀",
        parse_mode='Markdown'
    )
    
    context.user_data.clear()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    if context.user_data.get('awaiting_url'):
        url = update.message.text.strip()
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        await update.message.reply_text("🔍 Checking website...")
        
        result = await monitor.check_website(url)
        
        context.user_data['pending_url'] = url
        context.user_data['awaiting_url'] = False
        
        # Show interval selection
        keyboard = [
            [InlineKeyboardButton("⚡ 10 seconds", callback_data='interval_10sec'),
             InlineKeyboardButton("⚡ 30 seconds", callback_data='interval_30sec')],
            [InlineKeyboardButton("🕐 1 minute", callback_data='interval_1min'),
             InlineKeyboardButton("🕑 2 minutes", callback_data='interval_2min')],
            [InlineKeyboardButton("🕒 3 minutes", callback_data='interval_3min'),
             InlineKeyboardButton("🕔 5 minutes", callback_data='interval_5min')],
            [InlineKeyboardButton("🕙 10 minutes", callback_data='interval_10min'),
             InlineKeyboardButton("🕞 15 minutes", callback_data='interval_15min')],
            [InlineKeyboardButton("🕥 30 minutes", callback_data='interval_30min'),
             InlineKeyboardButton("🕐 1 hour", callback_data='interval_1hour')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status_emoji = "🟢" if result['status'] == 'up' else "🔴"
        
        await update.message.reply_text(
            f"⏱️ **Select Check Interval**\n\n"
            f"🌐 URL: `{url}`\n"
            f"{status_emoji} Status: {result['status'].upper()}\n"
            f"⚡ Response: {result['response_time']}ms\n\n"
            f"Choose monitoring interval:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif context.user_data.get('awaiting_broadcast'):
        if update.effective_user.id in ADMIN_IDS:
            message = update.message.text
            users = await users_collection.find({}).to_list(None)
            
            sent = 0
            failed = 0
            
            status_msg = await update.message.reply_text("📢 Broadcasting...")
            
            for user in users:
                try:
                    await context.bot.send_message(
                        chat_id=user['user_id'],
                        text=message,
                        parse_mode='Markdown'
                    )
                    sent += 1
                    await asyncio.sleep(0.05)
                except Exception as e:
                    failed += 1
            
            await status_msg.edit_text(
                f"📢 **Broadcast Complete!**\n\n"
                f"✅ Sent: {sent}\n"
                f"❌ Failed: {failed}"
            )
            
            context.user_data['awaiting_broadcast'] = False

async def monitor_website(website_id: str, user_id: int):
    """Monitor a specific website"""
    try:
        while True:
            site = await websites_collection.find_one({'_id': ObjectId(website_id)})
            
            if not site:
                break
            
            result = await monitor.check_website(site['url'])
            
            total_checks = site.get('total_checks', 0) + 1
            successful_checks = site.get('successful_checks', 0)
            
            if result['status'] == 'up':
                successful_checks += 1
            
            uptime_percentage = (successful_checks / total_checks) * 100
            status_changed = site['status'] != result['status']
            
            await websites_collection.update_one(
                {'_id': ObjectId(website_id)},
                {
                    '$set': {
                        'status': result['status'],
                        'last_checked': result['checked_at'],
                        'last_status_code': result['status_code'],
                        'last_response_time': result['response_time'],
                        'uptime_percentage': uptime_percentage,
                        'total_checks': total_checks,
                        'successful_checks': successful_checks
                    }
                }
            )
            
            # Send notification if status changed
            if status_changed and site.get('notifications_enabled', True):
                from telegram import Bot
                bot = Bot(token=BOT_TOKEN)
                
                if result['status'] == 'down':
                    message = f"""
🚨 **Website Down Alert!**

🌐 URL: `{site['url']}`
❌ Status: DOWN
🕒 Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
📊 Error: {result['error'] or 'HTTP ' + str(result['status_code'])}
⏱️ Interval: {site['interval']}

Your website is unreachable!
"""
                else:
                    message = f"""
✅ **Website Back Online!**

🌐 URL: `{site['url']}`
✅ Status: UP
🕒 Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
⚡ Response: {result['response_time']}ms
📈 Uptime: {uptime_percentage:.2f}%

Your website is back!
"""
                
                try:
                    await bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
                except:
                    pass
            
            await asyncio.sleep(site['interval_seconds'])
            
    except Exception as e:
        logger.error(f"Error monitoring {website_id}: {e}")

async def list_websites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all websites"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    websites = await websites_collection.find({'user_id': user_id}).to_list(None)
    
    if not websites:
        await query.message.reply_text(
            "📭 **No Websites Added**\n\n"
            "Add your first website to start monitoring!",
            parse_mode='Markdown'
        )
        return
    
    text = "📊 **Your Monitored Websites**\n\n"
    
    for idx, site in enumerate(websites, 1):
        status_emoji = "🟢" if site['status'] == 'up' else "🔴"
        uptime = site.get('uptime_percentage', 100)
        
        text += f"{idx}. {status_emoji} `{site['url']}`\n"
        text += f"   📈 Uptime: {uptime:.2f}%\n"
        text += f"   ⚡ Response: {site.get('last_response_time', 0)}ms\n"
        text += f"   ⏱️ Interval: {site['interval']}\n"
        text += f"   🕒 Last: {site['last_checked'].strftime('%H:%M')}\n\n"
    
    keyboard = [[InlineKeyboardButton("➕ Add More", callback_data='add_website')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def api_key_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show API key menu"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    api_key = await api_keys_collection.find_one({'user_id': user_id, 'active': True})
    
    if api_key:
        keyboard = [
            [InlineKeyboardButton("🔄 Regenerate Key", callback_data='regenerate_api_key')],
            [InlineKeyboardButton("📖 View API Docs", callback_data='api_docs')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_menu')]
        ]
        
        text = f"""
🔑 **Your API Key**

`{api_key['api_key']}`

📊 **Statistics:**
• Created: {api_key['created_at'].strftime('%Y-%m-%d')}
• Requests: {api_key.get('requests_count', 0)}
• Status: {'✅ Active' if api_key['active'] else '❌ Inactive'}

⚠️ **Keep your API key secure!**
"""
    else:
        keyboard = [
            [InlineKeyboardButton("➕ Generate API Key", callback_data='generate_api_key')],
            [InlineKeyboardButton("📖 View API Docs", callback_data='api_docs')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_menu')]
        ]
        
        text = """
🔑 **API Key Management**

You don't have an API key yet.

Generate one to access our REST API and integrate uptime monitoring into your applications!
"""
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def generate_new_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate new API key"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Deactivate old keys
    await api_keys_collection.update_many(
        {'user_id': user_id},
        {'$set': {'active': False}}
    )
    
    # Generate new key
    api_key = await generate_api_key(user_id)
    
    await query.message.reply_text(
        f"✅ **API Key Generated!**\n\n"
        f"`{api_key}`\n\n"
        f"⚠️ Save this key securely!\n"
        f"📖 Check /api_docs for usage instructions.",
        parse_mode='Markdown'
    )

async def show_api_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show API documentation"""
    query = update.callback_query
    await query.answer()
    
    docs = """
📖 **API Documentation**

**Base URL:** `https://your-api.com/api/v1`

**Authentication:**
Include your API key in the header:
```
Authorization: Bearer YOUR_API_KEY
```

**Endpoints:**

1️⃣ **Add Website**
```
POST /websites
Body: {
  "url": "https://example.com",
  "interval": "1min"
}
```

2️⃣ **List Websites**
```
GET /websites
```

3️⃣ **Get Website Status**
```
GET /websites/{website_id}
```

4️⃣ **Delete Website**
```
DELETE /websites/{website_id}
```

5️⃣ **Get Statistics**
```
GET /stats
```

**Response Format:**
```json
{
  "success": true,
  "data": {...},
  "message": "Success"
}
```

**Intervals Available:**
10sec, 30sec, 1min, 2min, 3min, 5min, 10min, 15min, 30min, 1hour
"""
    
    await query.message.reply_text(docs, parse_mode='Markdown')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    
    if query.data == 'add_website':
        await add_website_start(update, context)
    elif query.data == 'list_websites':
        await list_websites(update, context)
    elif query.data == 'show_stats':
        await show_stats(update, context)
    elif query.data == 'api_key_menu':
        await api_key_menu(update, context)
    elif query.data == 'generate_api_key' or query.data == 'regenerate_api_key':
        await generate_new_api_key(update, context)
    elif query.data == 'api_docs':
        await show_api_docs(update, context)
    elif query.data == 'help':
        await help_command(update, context)
    elif query.data == 'broadcast':
        await broadcast_start(update, context)
    elif query.data.startswith('interval_'):
        interval_key = query.data.replace('interval_', '')
        await handle_interval_selection(update, context, interval_key)
    elif query.data == 'back_to_menu':
        await start_menu(update, context)

async def start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show start menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Website", callback_data='add_website')],
        [InlineKeyboardButton("📊 My Websites", callback_data='list_websites')],
        [InlineKeyboardButton("🔑 API Key", callback_data='api_key_menu'),
         InlineKeyboardButton("📈 Statistics", callback_data='show_stats')],
        [InlineKeyboardButton("📖 API Docs", callback_data='api_docs'),
         InlineKeyboardButton("ℹ️ Help", callback_data='help')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        "🤖 **Main Menu**\n\nChoose an option:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    websites = await websites_collection.find({'user_id': user_id}).to_list(None)
    
    if not websites:
        await query.message.reply_text("No statistics available.")
        return
    
    total_sites = len(websites)
    up_sites = sum(1 for site in websites if site['status'] == 'up')
    down_sites = total_sites - up_sites
    
    avg_uptime = sum(site.get('uptime_percentage', 100) for site in websites) / total_sites
    avg_response = sum(site.get('last_response_time', 0) for site in websites) / total_sites
    
    text = f"""
📈 **Your Statistics**

🌐 Total Websites: {total_sites}
🟢 Online: {up_sites}
🔴 Offline: {down_sites}

📊 Average Uptime: {avg_uptime:.2f}%
⚡ Avg Response: {avg_response:.0f}ms

🕒 Updated: {datetime.utcnow().strftime('%H:%M UTC')}
"""
    
    await query.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    query = update.callback_query
    await query.answer()
    
    help_text = """
ℹ️ **Help & Commands**

**Commands:**
/start - Start bot
/cancel - Cancel operation

**Features:**
✅ Custom check intervals
🔑 API key generation
📡 REST API access
📊 Real-time statistics
🔔 Instant alerts

**How it works:**
1. Add website URL
2. Select check interval
3. Get instant alerts
4. Access via API

Need help? Contact support!
"""
    
    await query.message.reply_text(help_text, parse_mode='Markdown')

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        await query.message.reply_text("❌ Unauthorized")
        return
    
    await query.message.reply_text(
        "📢 **Broadcast**\n\nSend message to broadcast:",
        parse_mode='Markdown'
    )
    context.user_data['awaiting_broadcast'] = True

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel operation"""
    context.user_data.clear()
    await update.message.reply_text("✅ Cancelled")

async def post_init(application: Application):
    """Initialize after bot starts"""
    # Restart monitoring for existing websites
    websites = await websites_collection.find({}).to_list(None)
    for site in websites:
        website_id = str(site['_id'])
        task = asyncio.create_task(monitor_website(website_id, site['user_id']))
        monitoring_tasks[website_id] = task
    
    logger.info(f"Resumed monitoring for {len(websites)} websites")

def main():
    """Start bot"""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
