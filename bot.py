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
                completed INTEGER DEFAULT 0
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
        await db.execute("INSERT INTO tasks (user_id, assigned_by, task, due_date) VALUES (?, ?, ?, ?)",
                         (user.id, interaction.user.id, task, due_date))
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
    await interaction.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, interaction.user.id))
        row = await cursor.fetchone()
        if not row:
            await interaction.followup.send("❌ No such task found for you.")
            return
        await db.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
        await db.commit()
    await interaction.followup.send(f"✅ Task ID {task_id} marked as completed!")


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


# ✅ Startup event
@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    if not hourly_reminder.is_running():
        hourly_reminder.start()
    print(f"🤖 Logged in as {bot.user} | Reminders active.")


# Run bot
bot.run(TOKEN)
