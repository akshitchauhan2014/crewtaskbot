import os
import asyncio
import discord
import aiosqlite
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.dm_messages = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

DB_PATH = "tasks.db"


# ✅ Create or connect database
async def init_db():
    db_exists = os.path.exists(DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                assigned_by INTEGER,
                task TEXT,
                due_date TEXT,
                completed INTEGER DEFAULT 0,
                overdue_notified INTEGER DEFAULT 0,
                last_overdue_notified INTEGER DEFAULT 0,
                guild_id INTEGER
            )
        """)
        await db.commit()
        # Ensure existing databases get the new column if it was created before adding it
        cursor = await db.execute("PRAGMA table_info(tasks)")
        cols = await cursor.fetchall()
        col_names = [c[1] for c in cols]
        # Add missing migration columns safely if DB created before these fields were added
        if "overdue_notified" not in col_names:
            await db.execute("ALTER TABLE tasks ADD COLUMN overdue_notified INTEGER DEFAULT 0")
            await db.commit()
        if "last_overdue_notified" not in col_names:
            await db.execute("ALTER TABLE tasks ADD COLUMN last_overdue_notified INTEGER DEFAULT 0")
            await db.commit()
        if "guild_id" not in col_names:
            await db.execute("ALTER TABLE tasks ADD COLUMN guild_id INTEGER")
            await db.commit()
        # Create a simple settings table for guild-wide configuration
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                thank_channel_id INTEGER
            )
        """)
        await db.commit()
    if db_exists:
        print("✅ Connected to existing database.")
    else:
        print("🆕 New database created.")


# ✅ Assign command (Admin or anyone)
@tree.command(name="assign", description="Assign a task to a user")
async def assign_task(interaction: discord.Interaction, user: discord.Member, task: str, due_date: str = None):
    await interaction.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        guild_id = interaction.guild.id if interaction.guild else None
        await db.execute("INSERT INTO tasks (user_id, assigned_by, task, due_date, guild_id) VALUES (?, ?, ?, ?, ?)",
                         (user.id, interaction.user.id, task, due_date, guild_id))
        await db.commit()

    # Send confirmation
    await interaction.followup.send(f"✅ Task assigned to {user.mention}: **{task}** (Due: {due_date or 'No date'})")

    # DM the user
    try:
        await user.send(f"📋 You’ve been assigned a new task: **{task}** (Due: {due_date or 'No due date'}) by {interaction.user.mention}")
    except discord.Forbidden:
        await interaction.followup.send(f"⚠️ Could not DM {user.mention} (DMs disabled).", ephemeral=True)


# ✅ Complete task
@tree.command(name="complete", description="Mark your task as completed")
async def complete_task(interaction: discord.Interaction, task_id: int):
    print(f"🔍 Task completion requested by {interaction.user} for task ID {task_id}")
    await interaction.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Fetch the task row so we can include the actual task text in the thank-you message
        print(f"📝 Fetching task details for ID {task_id}")
        cursor = await db.execute("SELECT id, task, guild_id FROM tasks WHERE id = ? AND user_id = ?", (task_id, interaction.user.id))
        row = await cursor.fetchone()
        if not row:
            print(f"❌ No task found with ID {task_id} for user {interaction.user.id}")
            await interaction.followup.send("❌ No such task found for you.")
            return

        _, task_text, guild_id = row

        # Mark completed
        await db.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
        await db.commit()
    await interaction.followup.send(f"✅ Task marked as completed: **{task_text}**")

    # Send a thank-you message in the configured server channel (if set)
    if guild_id:
        print(f"🔍 Checking thank-you channel for guild {guild_id}")
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT thank_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
            gs = await cursor.fetchone()
            print(f"📋 Thank-you channel query result: {gs}")

        if gs and gs[0]:
            channel_id = gs[0]
            print(f"🎯 Attempting to send to channel ID: {channel_id}")
            try:
                # Try to get the channel from cache first
                channel = bot.get_channel(channel_id)
                if channel is None:
                    print(f"🔄 Channel not in cache, attempting API fetch for channel {channel_id}")
                    # fallback to API fetch
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception as e:
                        print(f"❌ Failed to fetch channel {channel_id}: {str(e)}")
                        channel = None

                if channel:
                    print(f"✅ Found channel: #{channel.name} ({channel.id})")
                    try:
                        # Send as a regular message (not ephemeral) so everyone can see it
                        embed = discord.Embed(
                            title="✨ Task Completed!",
                            description=f"{interaction.user.mention} has completed their task:\n**{task_text}**",
                            color=0x2ecc71  # Green color
                        )
                        embed.set_footer(text="Great work! 🎉")
                        await channel.send(embed=embed)
                        print(f"✅ Successfully sent thank-you message to #{channel.name}")
                    except discord.Forbidden as e:
                        print(f"❌ Missing permissions to send message in #{channel.name}: {str(e)}")
                        await interaction.followup.send("⚠️ Bot doesn't have permission to send messages in the thank-you channel. Please contact an admin.", ephemeral=True)
                    except Exception as e:
                        print(f"❌ Failed to send thank-you message in #{channel.name} ({channel_id}): {str(e)}")
                else:
                    print(f"❌ Thank-you channel (ID {channel_id}) could not be found or accessed.")
            except Exception as e:
                print(f"❌ Error resolving thank-you channel for guild {guild_id}: {e}")

# ✅ View your own tasks
@tree.command(name="tasks", description="View your own tasks")
async def view_tasks(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, task, due_date, completed FROM tasks WHERE user_id = ?", (interaction.user.id,))
        rows = await cursor.fetchall()

    if not rows:
        await interaction.followup.send("📭 You have no tasks assigned.")
        return

    pending = [f"🕓 ID {r[0]} — {r[1]} (Due: {r[2] or 'N/A'})" for r in rows if r[3] == 0]
    completed = [f"✅ ID {r[0]} — {r[1]}" for r in rows if r[3] == 1]

    msg = "**Your Tasks:**\n\n"
    if pending:
        msg += "**Pending:**\n" + "\n".join(pending) + "\n\n"
    if completed:
        msg += "**Completed:**\n" + "\n".join(completed)
    await interaction.followup.send(msg)


# ✅ Admin command to see all users' task statuses
@tree.command(name="admin_tasks", description="(Admin only) View all users’ task statuses")
async def admin_tasks(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id, task, due_date, completed FROM tasks")
        rows = await cursor.fetchall()

    if not rows:
        await interaction.followup.send("📋 No tasks in the system.")
        return

    msg = "**📊 All Users’ Task Status:**\n\n"
    for r in rows:
        status = "✅ Done" if r[3] else "🕓 Pending"
        msg += f"<@{r[0]}> — {r[1]} (Due: {r[2] or 'N/A'}) — {status}\n"

    await interaction.followup.send(msg)


@tree.command(name="test_thank_channel", description="(Admin) Send a test message to the configured thank-you channel for this server")
async def test_thank_channel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a server (guild).", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT thank_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()

    if not row or not row[0]:
        await interaction.followup.send("⚠️ No thank-you channel configured for this server. Set one using /set_thank_channel.", ephemeral=True)
        return

    channel_id = row[0]
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except Exception as e:
        channel = None

    if not channel:
        await interaction.followup.send(f"❌ Configured channel (ID {channel_id}) could not be found. It might have been deleted or the bot lacks access.", ephemeral=True)
        return

    try:
        await channel.send(f"🔧 Test message: thank-you messages are configured to post here.")
        await interaction.followup.send(f"✅ Test message sent to {channel.mention}.", ephemeral=True)
    except Exception as e:
        print(f"❌ Failed to send test message to channel {channel_id} in guild {guild_id}: {e}")
        await interaction.followup.send("❌ Failed to send test message. Check bot permissions in the configured channel.", ephemeral=True)


# ✅ Admin command to set thank-you channel for completed tasks
@tree.command(name="set_thank_channel", description="(Admin) Set channel for server thank-you messages when users complete tasks")
async def set_thank_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("❌ This command must be used in a server (guild).", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    channel_id = channel.id
    # Ensure the channel belongs to this guild to avoid cross-guild mistakes
    if channel.guild and channel.guild.id != guild_id:
        await interaction.followup.send("❌ The channel you provided is not in this server. Please pick a channel from this server.", ephemeral=True)
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO guild_settings (guild_id, thank_channel_id) VALUES (?, ?)", (guild_id, channel_id))
            await db.commit()
    except Exception as e:
        print(f"❌ Failed to save thank-you channel for guild {guild_id}: {e}")
        await interaction.followup.send("❌ Failed to save the channel. Please try again or check bot permissions.", ephemeral=True)
        return

    # Publicly confirm in the configured channel so admins can immediately see it's set
    try:
        try:
            target = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        except Exception:
            target = None

        if target:
            try:
                await target.send("✅ This channel has been configured to receive thank-you messages when users complete tasks.")
            except Exception:
                # If we can't send into the channel, still acknowledge to admin
                pass
    except Exception:
        pass

    await interaction.followup.send(f"✅ Thank-you channel set to {channel.mention} for this server.", ephemeral=True)


# ✅ Hourly reminders for pending tasks
@tasks.loop(hours=1)
async def hourly_reminder():
    print("⏰ Sending hourly reminders...")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT user_id FROM tasks WHERE completed = 0")
        users = await cursor.fetchall()

        for (user_id,) in users:
            cursor = await db.execute("SELECT id, task, due_date FROM tasks WHERE user_id = ? AND completed = 0", (user_id,))
            tasks_list = await cursor.fetchall()

            if tasks_list:
                user = bot.get_user(user_id)
                if user:
                    task_text = "\n".join([f"• ID {t[0]} — {t[1]} (Due: {t[2] or 'N/A'})" for t in tasks_list])
                    try:
                        await user.send(f"⏰ **Reminder:** You have {len(tasks_list)} pending task(s):\n{task_text}")
                    except discord.Forbidden:
                        print(f"⚠️ Cannot DM user {user_id}.")
    print("✅ Reminder cycle complete.")

# ---------- OVERDUE REMINDER ----------
@tasks.loop(seconds=20)
async def overdue_reminder():
    """Checks overdue tasks every 20 seconds and notifies repeatedly until completion.

    Sends a DM when a task is overdue, then re-sends only if at least 20 seconds
    have passed since the last overdue notification for that task. This prevents
    spamming faster than the configured interval while ensuring regular reminders.
    """
    print("🚨 Checking for overdue tasks...")
    now = datetime.now()
    now_ts = int(now.timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, task, due_date, last_overdue_notified FROM tasks WHERE completed = 0 AND due_date IS NOT NULL"
        )
        rows = await cursor.fetchall()

        for task_id, user_id, task, due_date_str, last_notified in rows:
            try:
                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            except Exception:
                # If parsing fails, skip this task
                continue

            if due_date < now:
                # interpret last_notified safely
                try:
                    last_ts = int(last_notified) if last_notified else 0
                except Exception:
                    last_ts = 0

                # send if never sent or cooldown (20s) has passed
                if now_ts - last_ts >= 20:
                    try:
                        user = await bot.fetch_user(user_id)
                        await user.send(
                            f"🚨 **Overdue Task Alert:**\n"
                            f"Task ID {task_id}: **{task}** was due on **{due_date_str}**.\n"
                            f"Please complete it immediately!"
                        )
                        # update last notification timestamp
                        try:
                            await db.execute("UPDATE tasks SET last_overdue_notified = ? WHERE id = ?", (now_ts, task_id))
                            await db.commit()
                        except Exception:
                            pass
                        print(f"⚠️ Sent overdue reminder to user {user_id} for task {task_id}.")
                    except discord.Forbidden:
                        print(f"⚠️ Cannot DM user {user_id} (DMs disabled).")
                    except Exception as e:
                        print(f"❌ Error sending overdue reminder to user {user_id}: {e}")

# ✅ Startup event
@bot.event
async def on_ready():
    await init_db()
    try:
        print("🔄 Syncing application commands...")
        # Sync commands
        commands = await tree.sync()
        print(f"✅ Successfully synced {len(commands)} commands:")
        for cmd in commands:
            print(f"  • /{cmd.name}")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

    if not hourly_reminder.is_running():
        hourly_reminder.start()
    # Ensure overdue reminders are started as well
    if not overdue_reminder.is_running():
        overdue_reminder.start()
    print(f"🤖 Logged in as {bot.user} | Reminders active.")


# Run bot
bot.run(TOKEN)
