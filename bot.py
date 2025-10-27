import discord
from discord import app_commands
from discord.ext import tasks
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("❌ No token found! Check your .env file and variable name (DISCORD_TOKEN).")

# Set intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ---------------------- BOT CLASS ----------------------
class CrewTaskBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Auto-create SQLite DB
        async with aiosqlite.connect("tasks.db") as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    task TEXT,
                    due_date TEXT,
                    completed INTEGER DEFAULT 0
                )
            """)
            await db.commit()
        self.check_due_tasks.start()

    @tasks.loop(minutes=1)
    async def check_due_tasks(self):
        """Automatically remind users of overdue tasks"""
        async with aiosqlite.connect("tasks.db") as db:
            cursor = await db.execute("SELECT id, user_id, task, due_date FROM tasks WHERE completed = 0")
            rows = await cursor.fetchall()

        now = datetime.now()
        for task_id, user_id, task, due_str in rows:
            try:
                due = datetime.strptime(due_str, "%Y-%m-%d %H:%M")
                if due < now:
                    user = await self.fetch_user(user_id)
                    await user.send(f"⏰ Reminder: Task **'{task}'** was due on {due_str}. Please complete it soon!")
            except Exception as e:
                print(f"⚠️ Error reminding user {user_id}: {e}")

    async def on_ready(self):
        await self.tree.sync()
        print(f"✅ Logged in as {self.user}")

# ---------------------- BOT INSTANCE ----------------------
bot = CrewTaskBot()

# ---------------------- COMMANDS ----------------------

# /assign — assign task to a user
@bot.tree.command(name="assign", description="Assign a task to a user")
@app_commands.describe(user="User to assign", task="Task description", due_date="Due date in YYYY-MM-DD HH:MM format")
async def assign(interaction: discord.Interaction, user: discord.Member, task: str, due_date: str):
    try:
        datetime.strptime(due_date, "%Y-%m-%d %H:%M")
    except ValueError:
        await interaction.response.send_message("❌ Invalid date format! Use YYYY-MM-DD HH:MM", ephemeral=True)
        return

    async with aiosqlite.connect("tasks.db") as db:
        await db.execute(
            "INSERT INTO tasks (user_id, username, task, due_date) VALUES (?, ?, ?, ?)",
            (user.id, str(user), task, due_date)
        )
        await db.commit()

    await interaction.response.send_message(f"✅ Task '{task}' assigned to {user.mention} (due {due_date})")
    try:
        await user.send(f"📋 You have a new task: **{task}** (due {due_date})")
    except:
        pass

# /complete — mark task completed
@bot.tree.command(name="complete", description="Mark one of your tasks as completed")
@app_commands.describe(task_id="Task ID to mark as completed")
async def complete(interaction: discord.Interaction, task_id: int):
    async with aiosqlite.connect("tasks.db") as db:
        await db.execute(
            "UPDATE tasks SET completed = 1 WHERE id = ? AND user_id = ?", (task_id, interaction.user.id)
        )
        await db.commit()
    await interaction.response.send_message(f"✅ Task ID {task_id} marked as completed!", ephemeral=True)

# /tasks — show user’s own tasks
@bot.tree.command(name="tasks", description="View your pending and completed tasks")
async def tasks_list(interaction: discord.Interaction):
    async with aiosqlite.connect("tasks.db") as db:
        cursor = await db.execute("SELECT id, task, due_date, completed FROM tasks WHERE user_id = ?", (interaction.user.id,))
        rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("🟢 You have no tasks assigned!", ephemeral=True)
        return

    msg = f"📋 **Your Tasks:**\n\n"
    for task_id, task, due_date, completed in rows:
        status = "✅ Done" if completed else "⌛ Pending"
        msg += f"**ID:** {task_id} | **Task:** {task} | **Due:** {due_date} | **Status:** {status}\n"

    await interaction.response.send_message(msg, ephemeral=True)

# /admin_tasks — only for admins
@bot.tree.command(name="admin_tasks", description="View all users' tasks (admin only)")
async def admin_tasks(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    async with aiosqlite.connect("tasks.db") as db:
        cursor = await db.execute("SELECT username, task, due_date, completed FROM tasks")
        rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("📭 No tasks found in the system.", ephemeral=True)
        return

    msg = f"📊 **All Tasks:**\n\n"
    for username, task, due_date, completed in rows:
        status = "✅ Completed" if completed else "⌛ Pending"
        msg += f"👤 {username} — {task} (Due {due_date}) → {status}\n"

    await interaction.response.send_message(msg, ephemeral=True)

# ---------------------- RUN ----------------------
bot.run(TOKEN)
