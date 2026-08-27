import discord
from discord.ext import commands
from utils.ai import generate_ai_reply


# Create Discord Client / Bot
def create_discord_client(config, logger):
    """
    Creates and configures the Discord bot using the command prefix
    and settings from config.json.
    """

    intents = discord.Intents.default()
    intents.message_content = True  # Required for reading messages
    intents.members = True          # Required for moderation actions

    prefix = config["discord"].get("prefix", "!")
    bot = commands.Bot(command_prefix=prefix, intents=intents, help_command=None)

    # On Ready
    @bot.event
    async def on_ready():
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info("Discord Support Agent is now running.")

    # On Message (AI replies + command processing)
    @bot.event
    async def on_message(message: discord.Message):
        # Ignore messages from bots
        if message.author.bot:
            return

        # Process commands first
        if message.content.startswith(prefix):
            await bot.process_commands(message)
            return

        # AI or fallback reply
        ai_config = config.get("ai", {})
        incoming_text = message.content.strip()

        reply = generate_ai_reply(incoming_text, ai_config, logger)

        try:
            await message.channel.send(reply)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

    # Commands
    @bot.command(name="help")
    async def help_command(ctx):
        """Shows available commands."""
        help_text = (
            "**Available Commands:**\n"
            f"`{prefix}help` - Show this menu\n"
            f"`{prefix}ping` - Test command\n"
            f"`{prefix}info` - Info about the bot\n"
            f"`{prefix}kick @user` - Kick a user (requires permissions)\n"
            f"`{prefix}ban @user` - Ban a user (requires permissions)\n"
        )
        await ctx.send(help_text)

    @bot.command(name="ping")
    async def ping_command(ctx):
        """Replies with pong."""
        await ctx.send("Pong! ")

    @bot.command(name="info")
    async def info_command(ctx):
        """Basic info about the bot."""
        await ctx.send("I'm an automated support bot with optional AI-powered responses.")

    # Moderation Commands (REAL)
    @bot.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick_command(ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Kick a user from the server."""
        if member is None:
            await ctx.send("Please mention a user to kick.")
            return

        try:
            await member.kick(reason=reason)
            await ctx.send(f"{member.display_name} has been kicked.")
            logger.info(f"User kicked: {member} | By: {ctx.author}")
        except Exception as e:
            logger.error(f"Kick error: {e}")
            await ctx.send("I couldn't kick that user.")

    @kick_command.error
    async def kick_error(ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have permission to use this command.")
        else:
            await ctx.send("An error occurred while trying to kick the user.")

    @bot.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_command(ctx, member: discord.Member = None, *, reason="No reason provided"):
        """Ban a user from the server."""
        if member is None:
            await ctx.send("Please mention a user to ban.")
            return

        try:
            await member.ban(reason=reason)
            await ctx.send(f"{member.display_name} has been banned.")
            logger.info(f"User banned: {member} | By: {ctx.author}")
        except Exception as e:
            logger.error(f"Ban error: {e}")
            await ctx.send("I couldn't ban that user.")

    @ban_command.error
    async def ban_error(ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have permission to use this command.")
        else:
            await ctx.send("An error occurred while trying to ban the user.")

    # Return bot instance
    return bot
