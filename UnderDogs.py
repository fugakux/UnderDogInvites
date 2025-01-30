import discord
from discord.ext import commands
import json
import os
import random
import asyncio  

# -----------------------------
# Configuration / Constants
# -----------------------------
# Bot prefix
BOT_PREFIX = "!"

# Replace this with the role ID that is allowed to run !panel (admin privileges).
ADMIN_ROLE_ID = 1327828141669879928  # Example admin role ID for demonstration

# Role IDs to be assigned depending on how many invites have been used
FIRST_INVITE_ROLE_ID = 1334406662315966474  # given on 1st use
SECOND_INVITE_ROLE_ID = 1334406729823424684 # given on 2nd use

# This is the role that can *generate* invites and see the "Invites(x)" button
INVITE_GENERATOR_ROLE_ID = 1328552857267474495

# Filenames to store data
INVITES_FILE = "UnderDogInvites.json"
STATS_FILE   = "UnderDogStats.json"

# Embed info
EMBED_TITLE = "Underdog Dashboard"
EMBED_IMAGE_URL = (
    "https://cdn-longterm.mee6.xyz/plugins/embeds/images/"
    "1278609487367901305/3ba5f9be30184f6a68203ea2dd0cb934e552aba0"
    "7630727b5afb69bf1e29d9c8.png"
)

# Number of codes to generate for new Invite Generators
NUM_GENERATED_CODES = 3

# The format for generated codes: "UD-XXXXXX" 
CODE_PREFIX = "UD-"

intents = discord.Intents.default()
intents.message_content = True  # Required if you plan to read message content
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

# -----------------------------
# Concurrency Globals
# -----------------------------
# Lock ensures only one piece of code reads/writes the JSON at a time.
file_lock = asyncio.Lock()

# This queue_position will track how many interactions are queued up.
# Each new interaction increments queue_position by 1, then we sleep
# that many seconds to create a staggered queue effect.
queue_position = 0

# -----------------------------
# Helper functions
# -----------------------------
def load_json(filename: str) -> dict:
    """Load JSON data from a file, returns an empty dict if file does not exist."""
    if not os.path.exists(filename):
        return {}
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filename: str, data: dict):
    """Save JSON data to a file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def generate_invite_code() -> str:
    """Generate a random 6-digit code with the prefix (e.g. UD-123456)."""
    random_digits = ''.join(str(random.randint(0, 9)) for _ in range(6))
    return f"{CODE_PREFIX}{random_digits}"

# -----------------------------
# Data loading (initial)
# -----------------------------
# You will still reload inside each operation to stay up to date.
invites_data = load_json(INVITES_FILE)  # { "user_id": { "invites": [ { "code": str, "used_by": str/None }, ... ] } }
stats_data   = load_json(STATS_FILE)    # { "user_id": number_of_invites_used }

# -----------------------------
# Views and UI
# -----------------------------
class EnterInviteModal(discord.ui.Modal, title="Enter Invite Code"):
    """
    A simple Modal to let the user type an invite code.
    """
    code_input = discord.ui.TextInput(
        label="Invite Code",
        placeholder="UD-123456",
        required=True
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        """
        When the user submits the invite code, we:
          1. Defer the interaction (so we can respond later).
          2. Acquire a 'queue slot' and sleep accordingly.
          3. Acquire the file lock again to read/write the JSON safely.
          4. Perform all checks, assign roles if needed.
          5. Finally, send a follow-up message once done.
        """
        user = interaction.user
        entered_code = self.code_input.value.strip()

        # 1) Defer the response (ephemeral) so we can respond after waiting.
        #    If we don't defer, Discord might complain after a few seconds of no response.
        await interaction.response.defer(ephemeral=True)

        global file_lock, queue_position
        try:
            # 2) "Queue" logic: increment a queue_position and sleep for that many seconds.
            async with file_lock:
                queue_position += 1
                my_place_in_line = queue_position  # snapshot

            # Sleep outside the lock so the next user can also increment queue_position
            # immediately without waiting for our entire operation.
            await asyncio.sleep(my_place_in_line)

            # 3) Now reacquire the lock to do the actual file read/write safely.
            async with file_lock:
                # Reload the data from disk in case another user changed it while we waited
                current_invites = load_json(INVITES_FILE)
                current_stats   = load_json(STATS_FILE)

                # Check if the code exists and is unused
                owner_id = None
                found_code = False
                code_used = False

                for uid, invite_info in current_invites.items():
                    for code_entry in invite_info["invites"]:
                        if code_entry["code"] == entered_code:
                            found_code = True
                            if code_entry["used_by"] is not None:
                                code_used = True
                            else:
                                owner_id = uid
                            break
                    if found_code:
                        break

                if not found_code:
                    # Decrement the queue before we return
                    queue_position -= 1
                    # Send final response (the ephemeral deferral allows us to do this)
                    return await interaction.followup.send(
                        content="This invite code does not exist!",
                        ephemeral=True
                    )

                if code_used:
                    queue_position -= 1
                    return await interaction.followup.send(
                        content="This invite code is already used!",
                        ephemeral=True
                    )

                # Make sure you can't use your own code
                if str(user.id) == owner_id:
                    queue_position -= 1
                    return await interaction.followup.send(
                        content="You cannot use your **own** invite code.",
                        ephemeral=True
                    )

                # Mark the code as used
                for code_entry in current_invites[owner_id]["invites"]:
                    if code_entry["code"] == entered_code:
                        code_entry["used_by"] = str(user.id)
                        break

                # Increment user's used-invite count
                used_count = current_stats.get(str(user.id), 0)
                used_count += 1
                current_stats[str(user.id)] = used_count

                # Assign roles if needed
                guild = interaction.guild
                member = guild.get_member(user.id)

                if used_count == 1:
                    role = guild.get_role(FIRST_INVITE_ROLE_ID)
                    if role:
                        await member.add_roles(role, reason="User used first invite code.")
                elif used_count == 2:
                    role = guild.get_role(SECOND_INVITE_ROLE_ID)
                    if role:
                        await member.add_roles(role, reason="User used second invite code.")
                
                # Save the changes
                save_json(INVITES_FILE, current_invites)
                save_json(STATS_FILE, current_stats)

                # Decrement queue_position now that we've finished successfully
                queue_position -= 1

            # 5) Follow-up final message
            await interaction.followup.send(
                content=f"Invite code **{entered_code}** successfully used! (You have used {used_count} total).",
                ephemeral=True
            )

        except Exception as e:
            # If something goes wrong, make sure we decrement queue_position
            async with file_lock:
                if queue_position > 0:
                    queue_position -= 1
            # Then inform the user
            await interaction.followup.send(
                content=f"An error occurred: {e}",
                ephemeral=True
            )

class DashboardView(discord.ui.View):
    """
    The main view that appears under the Underdog Dashboard embed.
    It shows:
      - "Open Dashboard" button
    """
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)  # No timeout so the buttons don't disable
        self.bot = bot

    @discord.ui.button(label="Open Dashboard", style=discord.ButtonStyle.primary)
    async def open_dashboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild
        member = guild.get_member(user.id)

        # Possibly generate new invites if user has the generator role
        if any(r.id == INVITE_GENERATOR_ROLE_ID for r in member.roles):
            global invites_data
            invites_data = load_json(INVITES_FILE)

            if str(user.id) not in invites_data:
                invites_data[str(user.id)] = {
                    "invites": []
                }
                # Generate the codes
                for _ in range(NUM_GENERATED_CODES):
                    code = generate_invite_code()
                    invites_data[str(user.id)]["invites"].append({
                        "code": code,
                        "used_by": None
                    })
                save_json(INVITES_FILE, invites_data)

        dash_view = DashboardControlView(self.bot, member)
        await interaction.response.send_message(
            content="Here is your Underdog Dashboard:",
            view=dash_view,
            ephemeral=True  # Only the user clicking sees it
        )

class DashboardControlView(discord.ui.View):
    """
    A second-level view that has:
      - "Enter Invite" button
      - Possibly "Invites(x)" if user has generator role
    """
    def __init__(self, bot: commands.Bot, member: discord.Member):
        super().__init__(timeout=None)
        self.bot = bot
        self.member = member

        # Check if user has the special role
        if any(r.id == INVITE_GENERATOR_ROLE_ID for r in self.member.roles):
            invites_data_local = load_json(INVITES_FILE)
            user_invites = invites_data_local.get(str(self.member.id), {}).get("invites", [])
            unused_count = sum(1 for inv in user_invites if inv["used_by"] is None)

            # Add a button to show invites
            self.add_item(InvitesButton(label=f"Invites({unused_count})", style=discord.ButtonStyle.secondary))

    @discord.ui.button(label="Enter Invite", style=discord.ButtonStyle.success)
    async def enter_invite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Show a modal to collect the code
        await interaction.response.send_modal(EnterInviteModal(self.bot))

class InvitesButton(discord.ui.Button):
    """
    A button that, when clicked, shows the user's invites and their usage.
    """
    def __init__(self, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction):
        invites_data_local = load_json(INVITES_FILE)
        user_invites = invites_data_local.get(str(interaction.user.id), {}).get("invites", [])

        lines = []
        if not user_invites:
            lines.append("You have no invites stored.")
        else:
            for inv in user_invites:
                code = inv["code"]
                used_by = inv["used_by"]
                if used_by is None:
                    lines.append(f"`{code}` used by none")
                else:
                    lines.append(f"`{code}` used by <@{used_by}>")

        msg = "\n".join(lines) if lines else "No invites to display."
        await interaction.response.send_message(
            content=msg,
            ephemeral=True
        )

# -----------------------------
# Command(s)
# -----------------------------
@bot.command(name="panel")
@commands.has_permissions(administrator=True)
async def panel_cmd(ctx: commands.Context):
    """
    Command: !panel
    Creates the Underdog Dashboard embed that everyone can see, 
    with a button "Open Dashboard".
    """
    embed = discord.Embed(title=EMBED_TITLE, color=discord.Color.blue())
    embed.set_image(url=EMBED_IMAGE_URL)

    view = DashboardView(bot)
    await ctx.send(embed=embed, view=view)

# -----------------------------
# Example JSON File Format
# -----------------------------
# UnderDogInvites.json example:
# {
#   "123456789012345678": {
#     "invites": [
#       {
#         "code": "UD-123456",
#         "used_by": null
#       },
#       {
#         "code": "UD-234567",
#         "used_by": "987654321098765432"
#       }
#     ]
#   }
# }
#
# UnderDogStats.json example:
# {
#   "123456789012345678": 2,
#   "987654321098765432": 1
# }

# -----------------------------
# Bot Start
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as: {bot.user} (ID: {bot.user.id})")

# REPLACE WITH YOUR BOT TOKEN HERE
bot.run("TOKEN")
