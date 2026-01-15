from pyrogram import Client, filters
from pyrogram.types import Message
from plugins.force_sub import check_force_subscription
from plugins.cancel import start_process, end_process, is_cancelled
from utils.quota_manager import check_user_quota, check_file_size, increment_user_quota
from utils.file_handler import download_file, extract_archive, get_all_files, cleanup_files, validate_file_type
from utils.helpers import format_size, format_duration, progress_bar
from database.database import bot_config_collection
import time
import re
import os
import asyncio


# Track last progress update time per user
last_progress_update = {}


async def progress_callback(current, total, message, start_time, user_id, action="Downloading"):
    """Progress callback with minimal overhead to prevent timeouts"""
    try:
        # Check for cancellation
        if is_cancelled(user_id):
            raise Exception("Process cancelled by user")
        
        # Skip if download complete
        if current == total:
            return
        
        current_time = time.time()
        
        # Only update every 10 seconds AND if at least 5% progress made
        user_key = f"{user_id}_{action}"
        last_update = last_progress_update.get(user_key, 0)
        
        if current_time - last_update < 10:
            return
        
        # Update the last update time
        last_progress_update[user_key] = current_time
        
        # Calculate stats
        elapsed = current_time - start_time
        percentage = (current / total) * 100
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0
        bar = progress_bar(current, total, width=15)
        
        progress_text = (
            f"**{action}**\n\n"
            f"{bar}\n"
            f"**Size:** {format_size(current)} / {format_size(total)}\n"
            f"**Speed:** {format_size(speed)}/s\n"
            f"**ETA:** {format_duration(eta)}\n\n"
            f"Use /cancel to stop"
        )
        
        # Update message without blocking
        try:
            await message.edit_text(progress_text)
        except:
            pass
            
    except Exception as e:
        if "cancelled" in str(e).lower():
            raise
        pass


@Client.on_message(filters.command("unzip") & filters.private)
async def unzip_command(client: Client, message: Message):
    """Handle /unzip command"""
    user_id = message.from_user.id
    
    # Check force subscription
    is_subscribed, buttons = await check_force_subscription(client, user_id)
    if not is_subscribed:
        await message.reply_text(
            "❌ **Access Denied!**\n\n"
            "You must join the following channels to use this bot:",
            reply_markup=buttons
        )
        return
    
    # Check if command is a reply
    if not message.reply_to_message:
        await message.reply_text(
            "❌ **Invalid Usage!**\n\n"
            "Please reply to a file or Telegram link with:\n"
            "• `/unzip` - for files without password\n"
            "• `/unzip \"password\"` - for password-protected files"
        )
        return
    
    replied_msg = message.reply_to_message
    
    # Extract password from command - improved parsing
    password = None
    command_text = message.text.split(maxsplit=1)
    if len(command_text) > 1:
        password_input = command_text[1].strip()
        
        # Try to extract from quotes first
        match = re.search(r'["\'](.*?)["\']', password_input)
        if match:
            password = match.group(1)
        else:
            # Use the text as-is if no quotes
            password = password_input
    
    # Check if replied message has a file
    if not replied_msg.document and not replied_msg.text:
        await message.reply_text("❌ Please reply to a file or Telegram file link!")
        return
    
    # Handle Telegram link
    if replied_msg.text and 't.me/' in replied_msg.text:
        await handle_telegram_link(client, message, replied_msg.text, password)
        return
    
    # Handle direct file
    if replied_msg.document:
        await handle_file_extraction(client, message, replied_msg, password)
        return
    
    await message.reply_text("❌ No valid file or link found in the replied message!")


async def handle_telegram_link(client: Client, message: Message, link_text: str, password: str):
    """Handle file extraction from Telegram link"""
    # Parse Telegram link (t.me/channel/message_id or t.me/c/channel_id/message_id)
    try:
        # Extract URL from text (in case there's extra text)
        import re
        url_match = re.search(r'https?://t\.me/\S+', link_text)
        if url_match:
            link = url_match.group(0)
        else:
            link = link_text.strip()
        
        # Remove query parameters and fragments
        link = link.split('?')[0].split('#')[0]
        
        parts = link.rstrip('/').split('/')
        
        # Determine channel and message ID
        if '/c/' in link:
            # Private channel link: https://t.me/c/1234567890/123
            try:
                channel_idx = parts.index('c')
                channel_id = int('-100' + parts[channel_idx + 1])
                msg_id = int(parts[channel_idx + 2])
            except (ValueError, IndexError):
                await message.reply_text(
                    "❌ Invalid private channel link format!\n\n"
                    "Expected format: `https://t.me/c/1234567890/123`"
                )
                return
        else:
            # Public channel link: https://t.me/channelname/123
            try:
                # Find the channel username (second to last part) and message ID (last part)
                if len(parts) >= 2:
                    channel_username = parts[-2]
                    msg_id = int(parts[-1])
                    channel_id = f"@{channel_username}" if not channel_username.startswith('@') else channel_username
                else:
                    raise ValueError("Invalid link format")
            except (ValueError, IndexError):
                await message.reply_text(
                    "❌ Invalid public channel link format!\n\n"
                    "Expected format: `https://t.me/channelname/123`"
                )
                return
        
        # Get the message
        try:
            file_msg = await client.get_messages(channel_id, msg_id)
        except Exception as e:
            error_str = str(e).lower()
            if 'peer_id_invalid' in error_str:
                await message.reply_text(
                    "❌ Cannot access this channel!\n\n"
                    "**Possible reasons:**\n"
                    "• Channel is private and bot is not a member\n"
                    "• Bot hasn't interacted with this channel\n"
                    "• Invalid channel ID\n\n"
                    "**For private channels:**\n"
                    "Make sure the bot is added to the channel as admin."
                )
            else:
                await message.reply_text(f"❌ Error accessing message: {str(e)}")
            return
        
        if not file_msg or not file_msg.document:
            await message.reply_text(
                "❌ No file found at the provided link!\n\n"
                "Make sure the link points to a message with a file attachment."
            )
            return
        
        # Process the file
        await handle_file_extraction(client, message, file_msg, password)
    
    except Exception as e:
        await message.reply_text(
            f"❌ Error processing Telegram link!\n\n"
            f"Error: {str(e)}\n\n"
            f"**Make sure:**\n"
            f"• Link format is correct\n"
            f"• Bot has access to the channel\n"
            f"• Message contains a file"
        )


async def handle_file_extraction(client: Client, message: Message, file_message: Message, password: str):
    """Handle file extraction process"""
    user_id = message.from_user.id
    
    # Get file info
    file = file_message.document
    file_name = file.file_name
    file_size = file.file_size
    
    # Validate file type - check extension
    ext = file_name.lower().rsplit('.', 1)[-1] if '.' in file_name else ''
    supported_exts = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'tgz', 'tbz2']
    
    if ext not in supported_exts:
        await message.reply_text(
            f"❌ **Unsupported File Type!**\n\n"
            f"File: `{file_name}`\n"
            f"Extension: `.{ext}`\n\n"
            f"Please send only compressed files:\n"
            f".zip, .rar, .7z, .tar, .gz, .bz2"
        )
        return
    
    # Check user quota
    can_proceed, quota_msg, tier = check_user_quota(user_id)
    if not can_proceed:
        await message.reply_text(quota_msg)
        return
    
    # Check file size
    can_proceed, size_msg = check_file_size(user_id, file_size)
    if not can_proceed:
        await message.reply_text(size_msg)
        return
    
    # Start process tracking
    start_process(user_id, 'extraction', filename=file_name)
    
    # Start processing
    status_msg = await message.reply_text(
        f"**📦 Processing Archive**\n\n"
        f"**File:** `{file_name}`\n"
        f"**Size:** {format_size(file_size)}\n\n"
        f"⏳ Starting download...\n\n"
        f"Use /cancel to stop"
    )
    
    file_path = None
    extract_dir = None
    
    try:
        # Check for cancellation
        if is_cancelled(user_id):
            await status_msg.edit_text("⏸️ Process cancelled by user.")
            return
        
        # Download file
        start_time = time.time()
        
        # Create progress wrapper
        async def progress_wrapper(current, total):
            await progress_callback(current, total, status_msg, start_time, user_id, "Downloading")
        
        file_path, _, _ = await download_file(
            client,
            file_message,
            progress_wrapper
        )
        
        if not file_path:
            await status_msg.edit_text("❌ Failed to download file!")
            return
        
        # Check for cancellation
        if is_cancelled(user_id):
            await status_msg.edit_text("⏸️ Process cancelled by user.")
            await cleanup_files([file_path])
            return
        
        # Extract archive
        try:
            await status_msg.edit_text("📂 Extracting archive...\n\nUse /cancel to stop")
            
            # Start a task to update status every 5 seconds during extraction
            extraction_running = True
            async def update_extraction_status():
                elapsed = 0
                while extraction_running:
                    await asyncio.sleep(5)
                    if extraction_running:  # Check again after sleep
                        elapsed += 5
                        try:
                            await status_msg.edit_text(
                                f"📂 Extracting archive... ({elapsed}s)\n\n"
                                f"Please wait, large files may take several minutes.\n\n"
                                f"Use /cancel to stop"
                            )
                        except:
                            pass
            
            # Start the status update task
            status_task = asyncio.create_task(update_extraction_status())
            
            try:
                success, extract_dir, error_msg = await extract_archive(file_path, password)
            finally:
                extraction_running = False
                await asyncio.sleep(0.1)  # Give status task time to see the flag
                status_task.cancel()
                try:
                    await status_task
                except asyncio.CancelledError:
                    pass
            
            if not success:
                await status_msg.edit_text(error_msg or "❌ Extraction failed!")
                await cleanup_files([file_path])
                return
        except Exception as e:
            await status_msg.edit_text(f"❌ Extraction error: {str(e)}")
            await cleanup_files([file_path])
            return
        
        # Check for cancellation
        if is_cancelled(user_id):
            await status_msg.edit_text("⏸️ Process cancelled by user.")
            await cleanup_files([file_path, extract_dir])
            return
        
        # Get extracted files
        try:
            await status_msg.edit_text("📋 Getting extracted files...\n\nUse /cancel to stop")
            extracted_files = await get_all_files(extract_dir, max_files=50)
        except Exception as e:
            await status_msg.edit_text(f"❌ Error reading extracted files: {str(e)}")
            await cleanup_files([file_path, extract_dir])
            return
        
        if not extracted_files:
            await status_msg.edit_text("❌ No files found in archive!")
            await cleanup_files([file_path, extract_dir])
            return
        
        # Send files
        total_files = len(extracted_files)
        await status_msg.edit_text(
            f"📤 **Uploading Files**\n\n"
            f"{'░' * 20} 0%\n"
            f"**Files:** 0 / {total_files} uploaded\n\n"
            f"Use /cancel to stop"
        )
        
        #Get user settings for file transformations
        from database.user_settings_helper import get_user_settings
        from utils.filename_transformer import transform_filename, substitute_caption_variables, apply_replacements, get_file_type
        from pyrogram.types import MessageEntity
        
        settings = get_user_settings(user_id)
        
        # Get log channel
        log_channel_id = await get_log_channel()
        
        # Resolve log channel to populate peer cache (fixes "Peer id invalid" error)
        if log_channel_id:
            try:
                await client.get_chat(log_channel_id)
            except Exception:
                log_channel_id = None  # Disable logging if channel is inaccessible
        
        sent_count = 0
        for idx, file in enumerate(extracted_files, 1):
            # Check for cancellation before each file
            if is_cancelled(user_id):
                await status_msg.edit_text(
                    f"⏸️ **Process Cancelled**\n\n"
                    f"Sent {sent_count}/{total_files} files before cancellation."
                )
                await cleanup_files([file_path, extract_dir])
                return
            
            try:
                # Get original filename
                original_name = os.path.basename(file)
                
                # Transform filename according to user settings
                new_name = transform_filename(original_name, settings)
                
                # Rename file to new name
                new_path = os.path.join(os.path.dirname(file), new_name)
                if file != new_path:
                    os.rename(file, new_path)
                    file = new_path
                
                # Prepare caption if user has set custom caption
                caption = None
                caption_entities = None
                
                if settings.get('custom_caption'):
                    # Get file size and extension
                    file_size_bytes = os.path.getsize(file)
                    file_ext = os.path.splitext(new_name)[1][1:] if '.' in new_name else ''
                    
                    # Prepare file info for variable substitution
                    file_info = {
                        'filename': new_name,
                        'size': format_size(file_size_bytes),
                        'extension': file_ext,
                        'caption': ''  # Original caption if any
                    }
                    
                    # Substitute variables in caption template
                    caption = substitute_caption_variables(settings['custom_caption'], file_info)
                    
                    # Apply caption word replacements
                    if settings.get('caption_replacements'):
                        caption = apply_replacements(caption, settings['caption_replacements'])
                    
                    # Restore formatting entities if they exist
                    if settings.get('caption_entities'):
                        caption_entities = [
                            MessageEntity(
                                type=e['type'],
                                offset=e['offset'],
                                length=e['length']
                            )
                            for e in settings['caption_entities']
                        ]
                
                # Send file according to upload type setting
                sent_msg = None
                
                # Get thumbnail and validate it exists
                thumb_path = settings.get('thumbnail')
                if thumb_path and not os.path.isfile(thumb_path):
                    thumb_path = None  # Reset if file doesn't exist
                
                if settings.get('upload_as_document', True):
                    # Send as document
                    sent_msg = await client.send_document(
                        chat_id=user_id,
                        document=file,
                        caption=caption,
                        caption_entities=caption_entities,
                        thumb=thumb_path
                    )
                else:
                    # Send as media (photo/video) based on file type
                    file_type = get_file_type(new_name)
                    
                    if file_type == 'photo':
                        sent_msg = await client.send_photo(
                            chat_id=user_id,
                            photo=file,
                            caption=caption,
                            caption_entities=caption_entities
                        )
                    elif file_type == 'video':
                        sent_msg = await client.send_video(
                            chat_id=user_id,
                            video=file,
                            caption=caption,
                            caption_entities=caption_entities,
                            thumb=thumb_path
                        )
                    else:
                        # Fall back to document for unknown types
                        sent_msg = await client.send_document(
                            chat_id=user_id,
                            document=file,
                            caption=caption,
                            caption_entities=caption_entities,
                            thumb=thumb_path
                        )
                
                # Only count as sent after successful delivery to user
                sent_count += 1
                
                # Update progress bar
                progress_percentage = (sent_count / total_files) * 100
                filled = int(20 * sent_count / total_files)
                bar = '█' * filled + '░' * (20 - filled)
                
                await status_msg.edit_text(
                    f"📤 **Uploading Files**\n\n"
                    f"{bar} {progress_percentage:.0f}%\n"
                    f"**Files:** {sent_count} / {total_files} uploaded\n\n"
                    f"Use /cancel to stop"
                )
                
                # Forward to log channel
                if log_channel_id and sent_msg:
                    try:
                        await sent_msg.copy(log_channel_id)
                    except Exception:
                        pass  # Silently skip if log channel forward fails
                
                # Delete file only after BOTH sends complete successfully
                try:
                    await asyncio.sleep(0.2)  # Small delay to ensure upload complete
                    if os.path.isfile(file):
                        os.remove(file)
                except Exception:
                    pass  # Silently skip if file deletion fails
                
            except Exception as e:
                await message.reply_text(f"⚠️ Could not send file {idx}: {str(e)}")

        
        # Increment quota
        increment_user_quota(user_id, file_name, file_size)
        
        # Success message
        await status_msg.edit_text(
            f"✅ **Extraction Complete!**\n\n"
            f"**Archive:** `{file_name}`\n"
            f"**Extracted:** {len(extracted_files)} file(s)\n\n"
            f"All files have been sent!"
        )
    
    except Exception as e:
        if "cancelled" in str(e).lower():
            await status_msg.edit_text("⏸️ Process cancelled by user.")
        else:
            await status_msg.edit_text(f"❌ An error occurred: {str(e)}")
    
    finally:
        # End process tracking
        end_process(user_id)
        
        # Cleanup
        if file_path:
            await cleanup_files([file_path])
        if extract_dir:
            await cleanup_files([extract_dir])


async def get_log_channel():
    """Get log channel ID from database"""
    try:
        config = bot_config_collection.find_one({"setting_name": "log_channel"})
        if config:
            return int(config['setting_value'])
        return None
    except:
        return None
