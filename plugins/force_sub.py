from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate
from database.database import force_sub_channels_collection


async def check_force_subscription(client: Client, user_id: int):
    """
    Check if user is subscribed to all force sub channels (up to 4)
    Returns: (is_subscribed: bool, buttons: InlineKeyboardMarkup)
    """
    channels = list(force_sub_channels_collection.find().limit(4))
    
    if not channels:
        return True, None
    
    not_subscribed = []
    
    for channel in channels:
        try:
            # Use username or channel_id as identifier
            channel_identifier = channel.get('username') or channel.get('channel_id')
            
            # Try to get chat member status
            try:
                member = await client.get_chat_member(channel_identifier, user_id)
                # Check if user is actually a member
                if member.status in ['left', 'kicked', 'banned']:
                    not_subscribed.append(channel)
            except UserNotParticipant:
                # User is definitely not a member
                not_subscribed.append(channel)
            except Exception as e:
                # Any other error, assume not subscribed
                print(f"Error checking subscription for {channel_identifier}: {e}")
                not_subscribed.append(channel)
                
        except Exception as e:
            print(f"Error processing channel: {e}")
            not_subscribed.append(channel)
    
    if not_subscribed:
        # Create buttons with 2 columns layout
        buttons_list = []
        row = []
        
        for idx, channel in enumerate(not_subscribed):
            try:
                # Get channel info
                channel_identifier = channel.get('username') or channel.get('channel_id')
                channel_title = channel.get('channel_title', 'Channel')
                
                # Determine invite link
                if channel.get('username'):
                    # Public channel - use username
                    invite_link = f"https://t.me/{channel['username']}"
                else:
                    # Private channel - need to get invite link
                    try:
                        chat = await client.get_chat(channel_identifier)
                        invite_link = chat.invite_link
                        
                        if not invite_link:
                            # Try to export link if bot is admin
                            try:
                                invite_link = await client.export_chat_invite_link(channel_identifier)
                            except:
                                # Fallback
                                invite_link = f"https://t.me/c/{str(channel.get('channel_id', '')).replace('-100', '')}"
                        
                        # Update title from chat if available
                        if chat.title:
                            channel_title = chat.title[:20]
                    except Exception as e:
                        print(f"Error getting chat info: {e}")
                        # Use stored invite link or skip
                        invite_link = channel.get('invite_link', f"https://t.me/{channel.get('username', '')}")
                
                # Create button
                button = InlineKeyboardButton(f"📢 {channel_title}", url=invite_link)
                row.append(button)
                
                # Add row when we have 2 buttons or it's the last one
                if len(row) == 2 or idx == len(not_subscribed) - 1:
                    buttons_list.append(row)
                    row = []
                    
            except Exception as e:
                print(f"Error creating button for channel: {e}")
        
        # Add verification button at the end
        buttons_list.append([InlineKeyboardButton("✅ I Joined, Verify", callback_data="verify_subscription")])
        
        buttons = InlineKeyboardMarkup(buttons_list)
        return False, buttons
    
    return True, None


@Client.on_callback_query(filters.regex("verify_subscription"))
async def verify_subscription_callback(client: Client, callback_query: CallbackQuery):
    """Handle subscription verification callback"""
    user_id = callback_query.from_user.id
    
    # Show loading message
    await callback_query.answer("Checking your subscription...", show_alert=False)
    
    # Re-check subscription
    is_subscribed, buttons = await check_force_subscription(client, user_id)
    
    if is_subscribed:
        await callback_query.message.edit_text(
            "✅ **Verified!**\n\n"
            "Thank you for joining! You can now use the bot.\n\n"
            "Send /help to see available commands."
        )
    else:
        await callback_query.answer("❌ Please join all channels first!", show_alert=True)
        # Update buttons in case invite links changed
        await callback_query.message.edit_reply_markup(buttons)


