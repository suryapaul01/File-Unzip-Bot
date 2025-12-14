from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.database import (users_collection, downloads_collection, force_sub_channels_collection, 
                                bot_config_collection, redeem_codes_collection)
from config import ADMINS, MAX_FORCE_SUB_CHANNELS
from datetime import datetime, timedelta
from utils.helpers import format_size, format_date
from plugins.cancel import get_active_processes
import random
import string
import csv
import io
import asyncio


def is_admin(user_id):
    """Check if user is admin"""
    return user_id in ADMINS


def generate_code():
    """Generate a 6-digit alphanumeric code"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


# Store user states for multi-step commands
admin_states = {}


@Client.on_message(filters.command("admin") & filters.private)
async def admin_panel(client: Client, message: Message):
    """Show admin panel with all commands"""
    if not is_admin(message.from_user.id):
        return
    
    text = "🔧 **Admin Panel**\n\n"
    text += "**User Management:**\n"
    text += "• /addpremium - Grant premium to user\n"
    text += "• /removepremium - Remove premium\n"
    text += "• /premiumusers - List all premium users\n"
    text += "• /exportusers - Export user data as CSV\n\n"
    
    text += "**Redeem Codes:**\n"
    text += "• /generate - Generate redeem codes\n"
    text += "• /listcodes - View all redeem codes\n\n"
    
    text += "**Force Subscription:**\n"
    text += "• /addforcesub - Add force sub channel\n"
    text += "• /removeforcesub - Remove channel\n"
    text += "• /listforcesub - List all channels\n\n"
    
    text += "**Bot Configuration:**\n"
    text += "• /setlogchannel - Set log channel\n"
    text += "• /setupi - Configure UPI payment\n"
    text += "• /stats - View bot statistics\n"
    text += "• /processes - View ongoing processes\n\n"
    
    text += "**Broadcasting:**\n"
    text += "• /broadcast - Broadcast message (reply to message)\n\n"
    
    await message.reply_text(text)


@Client.on_message(filters.command("generate") & filters.private)
async def generate_codes_start(client: Client, message: Message):
    """Start the code generation process"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    user_id = message.from_user.id
    
    # Ask for plan type
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Premium", callback_data="gen_premium")],
        [InlineKeyboardButton("⭐ Ultra Premium", callback_data="gen_ultra_premium")],
        [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel")]
    ])
    
    await message.reply_text(
        "**Generate Redeem Codes**\n\n"
        "Select the plan type:",
        reply_markup=keyboard
    )


@Client.on_callback_query(filters.regex("^gen_"))
async def generate_codes_callback(client: Client, callback_query: CallbackQuery):
    """Handle code generation callbacks"""
    user_id = callback_query.from_user.id
    
    if not is_admin(user_id):
        await callback_query.answer("❌ Admin only!", show_alert=True)
        return
    
    data = callback_query.data
    
    if data == "gen_cancel":
        await callback_query.message.edit_text("❌ Code generation cancelled.")
        admin_states.pop(user_id, None)
        return
    
    # Handle plan type selection
    if data in ["gen_premium", "gen_ultra_premium"]:
        plan_type = "premium" if data == "gen_premium" else "ultra_premium"
        admin_states[user_id] = {"plan_type": plan_type}
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 Day", callback_data="gen_dur_1")],
            [InlineKeyboardButton("7 Days", callback_data="gen_dur_7")],
            [InlineKeyboardButton("30 Days", callback_data="gen_dur_30")],
            [InlineKeyboardButton("❌ Cancel", callback_data="gen_cancel")]
        ])
        
        await callback_query.message.edit_text(
            f"**Plan:** {plan_type.replace('_', ' ').title()}\n\n"
            "Select validity duration:",
            reply_markup=keyboard
        )
        return
    
    # Handle duration selection
    if data.startswith("gen_dur_"):
        duration = int(data.split("_")[-1])
        admin_states[user_id]["duration"] = duration
        
        await callback_query.message.edit_text(
            f"**Plan:** {admin_states[user_id]['plan_type'].replace('_', ' ').title()}\n"
            f"**Duration:** {duration} day(s)\n\n"
            "Please type the number of codes to generate (1-50):"
        )
        
        # Set state to wait for count
        admin_states[user_id]["waiting_for_count"] = True
        return


@Client.on_message(filters.text & filters.private & ~filters.command(["start", "help", "unzip", "myplan", "premium", "redeem", "cancel", "admin", "generate", "listcodes", "broadcast", "exportusers", "processes", "addpremium", "removepremium", "addforcesub", "removeforcesub", "listforcesub", "setlogchannel", "stats", "premiumusers", "setupi"]))
async def handle_code_count(client: Client, message: Message):
    """Handle code count input"""
    user_id = message.from_user.id
    
    # Only handle if user is admin and waiting for count
    if not is_admin(user_id):
        return
    
    if user_id not in admin_states or not admin_states[user_id].get("waiting_for_count"):
        return
    
    try:
        count = int(message.text)
        
        if count < 1 or count > 50:
            await message.reply_text("❌ Please enter a number between 1 and 50.")
            return
        
        # Generate codes
        plan_type = admin_states[user_id]["plan_type"]
        duration = admin_states[user_id]["duration"]
        
        status_msg = await message.reply_text(f"⏳ Generating {count} code(s)...")
        
        generated_codes = []
        for _ in range(count):
            code = generate_code()
            # Ensure unique code
            while redeem_codes_collection.find_one({"code": code}):
                code = generate_code()
            
            redeem_codes_collection.insert_one({
                "code": code,
                "plan_type": plan_type,
                "duration_days": duration,
                "is_used": False,
                "used_by": None,
                "created_date": datetime.utcnow(),
                "used_date": None
            })
            
            generated_codes.append(code)
        
        # Format codes
        codes_text = "\n".join([f"`{code}`" for code in generated_codes])
        
        await status_msg.edit_text(
            f"✅ **{count} Code(s) Generated!**\n\n"
            f"**Plan:** {plan_type.replace('_', ' ').title()}\n"
            f"**Duration:** {duration} day(s)\n\n"
            f"**Codes:**\n{codes_text}\n\n"
            f"Users can redeem with: `/redeem CODE`"
        )
        
        # Clear state
        admin_states.pop(user_id, None)
    
    except ValueError:
        await message.reply_text("❌ Please enter a valid number.")


@Client.on_message(filters.command("listcodes") & filters.private)
async def list_codes_command(client: Client, message: Message):
    """List all redeem codes"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    codes = list(redeem_codes_collection.find().limit(50))
    
    if not codes:
        await message.reply_text("📋 No redeem codes generated yet.")
        return
    
    text = "📋 **Redeem Codes** (Last 50)\n\n"
    
    for code in codes:
        status = "✅ Used" if code.get('is_used') else "🟢 Available"
        text += f"**Code:** `{code['code']}`\n"
        text += f"Plan: {code['plan_type'].replace('_', ' ').title()} ({code['duration_days']}d)\n"
        text += f"Status: {status}\n"
        if code.get('is_used'):
            text += f"Used by: {code.get('used_by')}\n"
        text += "\n"
    
    await message.reply_text(text)


@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client: Client, message: Message):
    """Broadcast message to all users - reply to a message"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    if not message.reply_to_message:
        await message.reply_text(
            "❌ **Invalid Usage!**\n\n"
            "Reply to any message (text, photo, video, etc.) with `/broadcast` to send it to all users.\n\n"
            "The replied message will be broadcasted exactly as it is, including all formatting, buttons, and media."
        )
        return
    
    users = list(users_collection.find({"is_banned": {"$ne": True}}))
    total_users = len(users)
    
    status_msg = await message.reply_text(
        f"📤 **Broadcasting...**\n\n"
        f"Total users: {total_users}\n"
        f"Progress: 0/{total_users}\n"
        f"Success: 0\n"
        f"Failed: 0\n"
        f"Banned: 0"
    )
    
    success = 0
    failed = 0
    banned = 0
    
    for idx, user in enumerate(users, 1):
        try:
            await message.reply_to_message.copy(user['id'])
            success += 1
            await asyncio.sleep(0.05)  # Small delay to avoid flooding
        except Exception as e:
            error_msg = str(e).lower()
            if 'blocked' in error_msg or 'user is deactivated' in error_msg or 'forbidden' in error_msg:
                # Mark user as banned
                users_collection.update_one(
                    {"id": user['id']},
                    {"$set": {"is_banned": True}}
                )
                banned += 1
            else:
                failed += 1
        
        # Update status every 10 users
        if idx % 10 == 0 or idx == total_users:
            try:
                await status_msg.edit_text(
                    f"📤 **Broadcasting...**\n\n"
                    f"Total users: {total_users}\n"
                    f"Progress: {idx}/{total_users}\n"
                    f"Success: {success}\n"
                    f"Failed: {failed}\n"
                    f"Banned: {banned}"
                )
            except:
                pass
    
    await status_msg.edit_text(
        f"✅ **Broadcast Complete!**\n\n"
        f"**Total Users:** {total_users}\n"
        f"**Success:** {success}\n"
        f"**Failed:** {failed}\n"
        f"**Banned/Blocked:** {banned}\n\n"
        f"Banned users have been marked and won't receive future broadcasts."
    )


@Client.on_message(filters.command("exportusers") & filters.private)
async def export_users_command(client: Client, message: Message):
    """Export all user data as CSV"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    status_msg = await message.reply_text("⏳ Generating CSV file...")
    
    users = list(users_collection.find())
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['User ID', 'Username', 'First Name', 'Join Date', 'Tier', 'Premium Expiry', 'Daily Count', 'Is Banned'])
    
    # Write user data
    for user in users:
        writer.writerow([
            user.get('id', ''),
            user.get('username', ''),
            user.get('first_name', ''),
            user.get('join_date', '').strftime('%Y-%m-%d %H:%M:%S') if user.get('join_date') else '',
            user.get('tier', 'free'),
            user.get('premium_expiry', '').strftime('%Y-%m-%d %H:%M:%S') if user.get('premium_expiry') else '',
            user.get('daily_count', 0),
            user.get('is_banned', False)
        ])
    
    # Convert to bytes
    output.seek(0)
    csv_bytes = output.getvalue().encode('utf-8')
    
    # Send file
    await message.reply_document(
        document=io.BytesIO(csv_bytes),
        file_name=f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        caption=f"📊 **User Data Export**\n\nTotal Users: {len(users)}"
    )
    
    await status_msg.delete()


@Client.on_message(filters.command("processes") & filters.private)
async def processes_command(client: Client, message: Message):
    """Show ongoing processes"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    processes = get_active_processes()
    
    if not processes:
        await message.reply_text("📊 No ongoing processes.")
        return
    
    text = f"📊 **Ongoing Processes** ({len(processes)})\n\n"
    
    for process in processes:
        text += f"👤 **User ID:** `{process['user_id']}`\n"
        text += f"📁 **Type:** {process.get('type', 'Unknown')}\n"
        if process.get('filename'):
            text += f"📄 **File:** {process['filename']}\n"
        text += "\n"
    
    await message.reply_text(text)


# Keep existing premium management commands
@Client.on_message(filters.command("addpremium") & filters.private)
async def add_premium_command(client: Client, message: Message):
    """Add premium to user with smart upgrade/extend logic - /addpremium <user_id> <premium|ultra_premium> <1|7|30>"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 4:
            await message.reply_text(
                "❌ **Invalid Usage!**\n\n"
                "**Format:** `/addpremium <user_id> <plan_type> <duration>`\n\n"
                "**Example:** `/addpremium 123456789 premium 30`\n\n"
                "**Plan Types:** `premium`, `ultra_premium`\n"
                "**Duration:** `1`, `7`, `30` (days)"
            )
            return
        
        target_user_id = int(parts[1])
        plan_type = parts[2].lower()
        duration_days = int(parts[3])
        
        if plan_type not in ['premium', 'ultra_premium']:
            await message.reply_text("❌ Plan type must be 'premium' or 'ultra_premium'!")
            return
        
        if duration_days not in [1, 7, 30]:
            await message.reply_text("❌ Duration must be 1, 7, or 30 days!")
            return
        
        user = users_collection.find_one({"id": target_user_id})
        
        if not user:
            await message.reply_text(f"❌ User {target_user_id} not found in database!\nThey need to /start the bot first.")
            return
        
        # Get current user tier and expiry
        current_tier = user.get('tier', 'free')
        current_expiry = user.get('premium_expiry')
        
        # Define tier hierarchy
        tier_hierarchy = {'free': 0, 'premium': 1, 'ultra_premium': 2}
        new_tier_level = tier_hierarchy.get(plan_type, 0)
        current_tier_level = tier_hierarchy.get(current_tier, 0)
        
        # Determine action: upgrade, extend, or replace
        if new_tier_level > current_tier_level:
            # UPGRADE: New plan is better, replace current plan
            premium_expiry = datetime.utcnow() + timedelta(days=duration_days)
            action = "upgraded"
        elif new_tier_level == current_tier_level and current_tier != 'free':
            # EXTEND: Same tier, extend from current expiry
            if current_expiry and current_expiry > datetime.utcnow():
                # Extend from existing expiry
                premium_expiry = current_expiry + timedelta(days=duration_days)
                action = "extended"
            else:
                # Expired or no expiry, start fresh
                premium_expiry = datetime.utcnow() + timedelta(days=duration_days)
                action = "activated"
        else:
            # NEW or DOWNGRADE (treat as new)
            premium_expiry = datetime.utcnow() + timedelta(days=duration_days)
            action = "activated"
        
        # Update user tier
        users_collection.update_one(
            {"id": target_user_id},
            {"$set": {"tier": plan_type, "premium_expiry": premium_expiry}}
        )
        
        # Create status message
        if action == "upgraded":
            status_msg = "⬆️ **Plan Upgraded!**"
        elif action == "extended":
            status_msg = "➕ **Plan Extended!**"
        else:
            status_msg = "✅ **Plan Activated!**"
        
        await message.reply_text(
            f"{status_msg}\n\n"
            f"**User ID:** `{target_user_id}`\n"
            f"**Plan:** {plan_type.replace('_', ' ').title()}\n"
            f"**Duration:** +{duration_days} days\n"
            f"**Expires:** {format_date(premium_expiry)}"
        )
        
        # Notify user
        try:
            await client.send_message(
                target_user_id,
                f"{status_msg}\n\n"
                f"Your {plan_type.replace('_', ' ').title()} subscription has been {action}!\n"
                f"**Valid until:** {format_date(premium_expiry)}\n\n"
                f"Use /myplan to see your new limits."
            )
        except:
            pass
    
    except ValueError:
        await message.reply_text("❌ Invalid user ID or duration!")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("removepremium") & filters.private)
async def remove_premium_command(client: Client, message: Message):
    """Remove premium from user - /removepremium <user_id>"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply_text("**Usage:** `/removepremium <user_id>`")
            return
        
        target_user_id = int(parts[1])
        
        user = users_collection.find_one({"id": target_user_id})
        
        if not user:
            await message.reply_text(f"❌ User {target_user_id} not found!")
            return
        
        users_collection.update_one(
            {"id": target_user_id},
            {"$set": {"tier": "free", "premium_expiry": None}}
        )
        
        await message.reply_text(f"✅ Premium removed from user {target_user_id}")
        
        # Notify user
        try:
            await client.send_message(
                target_user_id,
                "ℹ️ Your premium subscription has been removed.\nYou are now on the Free tier."
            )
        except:
            pass
    
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


# Keep force sub and configuration commands
@Client.on_message(filters.command("addforcesub") & filters.private)
async def add_force_sub_command(client: Client, message: Message):
    """Add force subscription channel - /addforcesub <channel_id>"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply_text(
                "**Usage:** `/addforcesub <channel_id>`\n\n"
                "**Example:** `/addforcesub -1001234567890`\n"
                "or `/addforcesub @channelUsername`"
            )
            return
        
        channel_input = parts[1]
        
        # Try to get channel info
        try:
            chat = await client.get_chat(channel_input)
            channel_id = chat.id
            channel_title = chat.title
            channel_username = chat.username  # Get username for public channels
        except Exception as e:
            await message.reply_text(f"❌ Could not access channel: {str(e)}\n\nMake sure the bot is admin in the channel!")
            return
        
        # Check if already exists
        existing = force_sub_channels_collection.find_one({"channel_id": channel_id})
        if existing:
            await message.reply_text(f"⚠️ Channel {channel_title} is already in force sub list!")
            return
        
        # Check limit
        count = force_sub_channels_collection.count_documents({})
        if count >= MAX_FORCE_SUB_CHANNELS:
            await message.reply_text(f"❌ Maximum {MAX_FORCE_SUB_CHANNELS} channels allowed!")
            return
        
        # Prepare channel data
        channel_data = {
            "channel_id": channel_id,
            "channel_title": channel_title,
            "added_date": datetime.utcnow()
        }
        
        # Add username if it's a public channel
        if channel_username:
            channel_data["username"] = channel_username
        
        # Add channel
        force_sub_channels_collection.insert_one(channel_data)
        
        # Build success message
        success_msg = (
            f"✅ **Force Sub Channel Added!**\n\n"
            f"**Channel:** {channel_title}\n"
            f"**ID:** `{channel_id}`\n"
        )
        
        if channel_username:
            success_msg += f"**Username:** @{channel_username}\n"
        
        success_msg += "\nUsers must now join this channel to use the bot."
        
        await message.reply_text(success_msg)
    
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("removeforcesub") & filters.private)
async def remove_force_sub_command(client: Client, message: Message):
    """Remove force subscription channel - /removeforcesub <channel_id>"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply_text(
                "❌ **Invalid Usage!**\n\n"
                "**Format:** `/removeforcesub <channel_id>`\n\n"
                "**Example:** `/removeforcesub -1001234567890`\n\n"
                "Use /listforcesub to see all channels"
            )
            return
        
        try:
            channel_id = int(parts[1])
        except ValueError:
            await message.reply_text("❌ Invalid channel ID! Must be a number like -1001234567890")
            return
        
        channel = force_sub_channels_collection.find_one({"channel_id": channel_id})
        
        if not channel:
            await message.reply_text(
                f"❌ Channel ID `{channel_id}` not found in force sub list!\n\n"
                f"Use /listforcesub to see all channels"
            )
            return
        
        force_sub_channels_collection.delete_one({"channel_id": channel_id})
        
        await message.reply_text(
            f"✅ **Channel Removed!**\n\n"
            f"**Channel:** {channel.get('channel_title', 'Unknown')}\n"
            f"**ID:** `{channel_id}`\n\n"
            f"Channel removed from force subscription list."
        )
    
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}\n\nPlease check the channel ID and try again.")


@Client.on_message(filters.command("listforcesub") & filters.private)
async def list_force_sub_command(client: Client, message: Message):
    """List all force subscription channels"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    channels = list(force_sub_channels_collection.find())
    
    if not channels:
        await message.reply_text("📋 No force subscription channels configured.")
        return
    
    text = f"📋 **Force Subscription Channels** ({len(channels)}/{MAX_FORCE_SUB_CHANNELS})\n\n"
    
    for idx, channel in enumerate(channels, 1):
        text += f"{idx}. **{channel.get('channel_title', 'Unknown')}**\n"
        text += f"   ID: `{channel['channel_id']}`\n"
        text += f"   Added: {format_date(channel.get('added_date'))}\n\n"
    
    await message.reply_text(text)


@Client.on_message(filters.command("setlogchannel") & filters.private)
async def set_log_channel_command(client: Client, message: Message):
    """Set log channel - /setlogchannel <channel_id>"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply_text(
                "**Usage:** `/setlogchannel <channel_id>`\n\n"
                "**Example:** `/setlogchannel -1001234567890`"
            )
            return
        
        channel_id = int(parts[1])
        
        # Test if bot can send to channel
        try:
            await client.send_message(channel_id, "✅ Log channel configured successfully!")
        except Exception as e:
            await message.reply_text(f"❌ Could not send to channel: {str(e)}\n\nMake sure bot is admin!")
            return
        
        # Update or insert config
        bot_config_collection.update_one(
            {"setting_name": "log_channel"},
            {"$set": {"setting_value": str(channel_id)}},
            upsert=True
        )
        
        await message.reply_text(
            f"✅ **Log Channel Set!**\n\n"
            f"**Channel ID:** `{channel_id}`\n\n"
            f"All extracted files will be forwarded to this channel."
        )
    
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("premiumusers") & filters.private)
async def premium_users_command(client: Client, message: Message):
    """List all premium users"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    # Get all premium and ultra premium users
    premium_users = list(users_collection.find({
        "tier": {"$in": ["premium", "ultra_premium"]}
    }).sort("premium_expiry", -1))
    
    if not premium_users:
        await message.reply_text("📋 No premium users found.")
        return
    
    # Check for expired premiums and update
    active_premium = []
    for user in premium_users:
        if user.get('premium_expiry') and user['premium_expiry'] < datetime.utcnow():
            # Expire this user
            users_collection.update_one(
                {"id": user['id']},
                {"$set": {"tier": "free", "premium_expiry": None}}
            )
        else:
            active_premium.append(user)
    
    if not active_premium:
        await message.reply_text("📋 No active premium users found.")
        return
    
    text = f"👑 **Premium Users** ({len(active_premium)})\n\n"
    
    for idx, user in enumerate(active_premium, 1):
        plan_emoji = "💎" if user['tier'] == "premium" else "⭐"
        plan_name = user['tier'].replace('_', ' ').title()
        
        text += f"{idx}. {plan_emoji} **{plan_name}**\n"
        text += f"   **Name:** {user.get('first_name', 'Unknown')}\n"
        text += f"   **Username:** @{user.get('username', 'N/A')}\n"
        text += f"   **User ID:** `{user['id']}`\n"
        text += f"   **Expires:** {format_date(user.get('premium_expiry'))}\n\n"
    
    await message.reply_text(text)


@Client.on_message(filters.command("stats") & filters.private)
async def stats_command(client: Client, message: Message):
    """Show bot statistics"""
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ This command is for admins only!")
        return
    
    total_users = users_collection.count_documents({})
    active_users = users_collection.count_documents({"is_banned": {"$ne": True}})
    free_users = users_collection.count_documents({"tier": "free"})
    premium_users = users_collection.count_documents({"tier": "premium"})
    ultra_users = users_collection.count_documents({"tier": "ultra_premium"})
    total_downloads = downloads_collection.count_documents({})
    
    # Total data processed
    pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$size"}}}
    ]
    result = list(downloads_collection.aggregate(pipeline))
    total_bytes = result[0]['total'] if result else 0
    
    # Redeem codes stats
    total_codes = redeem_codes_collection.count_documents({})
    used_codes = redeem_codes_collection.count_documents({"is_used": True})
    
    text = "📊 **Bot Statistics**\n\n"
    text += f"👥 **Total Users:** {total_users}\n"
    text += f"   • Active: {active_users}\n"
    text += f"   • Free: {free_users}\n"
    text += f"   • Premium: {premium_users}\n"
    text += f"   • Ultra Premium: {ultra_users}\n\n"
    text += f"📦 **Total Extractions:** {total_downloads}\n"
    text += f"💾 **Data Processed:** {format_size(total_bytes)}\n\n"
    text += f"🎫 **Redeem Codes:**\n"
    text += f"   • Total: {total_codes}\n"
    text += f"   • Used: {used_codes}\n"
    text += f"   • Available: {total_codes - used_codes}\n"
    
    await message.reply_text(text)
