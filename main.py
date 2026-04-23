import asyncio
import os
from dataclasses import dataclass

import discord
import dotenv
from discord.ext import commands

dotenv.load_dotenv()

EMBED_COLOR = discord.Color.blurple()
UI_CHANNEL_NAME = "จับเวลาลูป"
UI_CHANNEL_TOPIC = "Timer control panel for this server"
FINISHED_ALERT_REPEAT_COUNT = 5
FINISHED_ALERT_DELETE_AFTER = 20
FINISHED_ALERT_INTERVAL = 1

intents = discord.Intents.default()
intents.message_content = True

client = commands.Bot(command_prefix="#!", intents=intents)


@dataclass
class TimerEntry:
    name: str
    owner_id: int
    owner_mention: str
    channel: discord.abc.Messageable
    duration_seconds: int
    deadline: float
    task: asyncio.Task


user_timers: dict[int, dict[str, TimerEntry]] = {}


def parse_duration(duration_text: str) -> int:
    cleaned_text = duration_text.strip().lower()

    if cleaned_text.isdigit():
        return int(cleaned_text)

    if len(cleaned_text) < 2:
        raise ValueError("missing unit")

    value_text = cleaned_text[:-1]
    unit = cleaned_text[-1]

    if not value_text.isdigit():
        raise ValueError("invalid number")

    value = int(value_text)

    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600

    raise ValueError("invalid unit")


def parse_named_timer_input(timer_input: str) -> tuple[str, int]:
    cleaned_input = timer_input.strip()
    if not cleaned_input:
        raise ValueError("missing input")

    parts = cleaned_input.rsplit(" ", 1)
    if len(parts) != 2:
        raise ValueError("missing duration")

    timer_name = parts[0].strip()
    duration_text = parts[1].strip()

    if not timer_name:
        raise ValueError("missing name")

    return timer_name, parse_duration(duration_text)


def format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def build_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


def build_panel_embed() -> discord.Embed:
    embed = build_embed(
        "ตั้วเวลาลูป",
        "สร้าง timer หลายอันพร้อมกันได้ ตั้งชื่อได้ และดูรายการ timer ของตัวเองได้จากปุ่มด้านล่าง",
    )
    embed.add_field(name="Start", value="สร้างเวลาลูปใหม่พร้อมชื่อ", inline=True)
    embed.add_field(name="Status", value="ดูเวลาลูปทั้งหมดของคุณ", inline=True)
    embed.add_field(name="Stop", value="ลบตามชื่อ หรือพิมพ์ all", inline=True)
    embed.set_footer(text="คำสั่งแชต: #!timer <ชื่อ> <เวลา>, #!status, #!stop <ชื่อ|all>")
    return embed    


def get_user_timer_bucket(user_id: int) -> dict[str, TimerEntry]:
    return user_timers.setdefault(user_id, {})


def get_timer_entries(user_id: int) -> list[TimerEntry]:
    bucket = user_timers.get(user_id, {})
    return sorted(bucket.values(), key=lambda entry: entry.deadline)


def get_all_timer_entries() -> list[TimerEntry]:
    entries: list[TimerEntry] = []
    for bucket in user_timers.values():
        entries.extend(bucket.values())

    return sorted(entries, key=lambda entry: entry.deadline)


def get_remaining_seconds(entry: TimerEntry) -> int:
    return max(0, int(entry.deadline - asyncio.get_running_loop().time()))


def build_timer_list_embed(user: discord.abc.User) -> discord.Embed:
    entries = get_all_timer_entries()

    if not entries:
        return build_embed("Timer Status", "ยังไม่มีตัวจับเวลาที่กำลังทำงานอยู่")

    lines = []
    for index, entry in enumerate(entries, start=1):
        remaining = format_duration(get_remaining_seconds(entry))
        total = format_duration(entry.duration_seconds)
        lines.append(
            f"{index}. `{entry.name}` | โดย {entry.owner_mention} | เหลือ {remaining} | ตั้งไว้ {total}"
        )

    return build_embed("Timer Status", "\n".join(lines))


async def send_repeating_finished_alert(entry: TimerEntry) -> None:
    embed = build_embed(
        "Timer Finished",
        f"{entry.owner_mention} timer `{entry.name}` หมดเวลาแล้ว",
    )

    for repeat_index in range(FINISHED_ALERT_REPEAT_COUNT):
        await entry.channel.send(
            content=entry.owner_mention,
            embed=embed,
            delete_after=FINISHED_ALERT_DELETE_AFTER,
        )

        if repeat_index < FINISHED_ALERT_REPEAT_COUNT - 1:
            await asyncio.sleep(FINISHED_ALERT_INTERVAL)


async def run_timer(entry: TimerEntry) -> None:
    try:
        await asyncio.sleep(entry.duration_seconds)
        await send_repeating_finished_alert(entry)
    except asyncio.CancelledError:
        raise
    finally:
        bucket = user_timers.get(entry.owner_id, {})
        current_entry = bucket.get(entry.name)
        if current_entry is entry:
            bucket.pop(entry.name, None)
        if not bucket:
            user_timers.pop(entry.owner_id, None)


async def create_timer(
    user: discord.abc.User,
    channel: discord.abc.Messageable,
    timer_name: str,
    total_seconds: int,
) -> None:
    bucket = get_user_timer_bucket(user.id)
    normalized_name = timer_name.strip()

    if normalized_name in bucket:
        raise ValueError("duplicate name")

    loop = asyncio.get_running_loop()
    placeholder_task = loop.create_future()
    entry = TimerEntry(
        name=normalized_name,
        owner_id=user.id,
        owner_mention=user.mention,
        channel=channel,
        duration_seconds=total_seconds,
        deadline=loop.time() + total_seconds,
        task=placeholder_task,  # replaced immediately below
    )
    task = asyncio.create_task(run_timer(entry))
    entry.task = task
    bucket[normalized_name] = entry


async def cancel_timer_by_name(user_id: int, timer_name: str) -> bool:
    bucket = user_timers.get(user_id, {})
    entry = bucket.get(timer_name)
    if entry is None:
        return False

    entry.task.cancel()
    try:
        await entry.task
    except asyncio.CancelledError:
        pass
    return True


async def cancel_all_timers(user_id: int) -> int:
    entries = list(get_timer_entries(user_id))
    cancelled_count = 0

    for entry in entries:
        entry.task.cancel()

    for entry in entries:
        try:
            await entry.task
        except asyncio.CancelledError:
            cancelled_count += 1

    return cancelled_count


class TimerDurationModal(discord.ui.Modal, title="Create Timer"):
    timer_name = discord.ui.TextInput(
        label="Timer Name",
        placeholder="เช่น อ่านหนังสือ / พัก / ทำงาน",
        required=True,
        max_length=40,
    )
    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="เช่น 30, 10s, 5m, 1h",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        timer_name = str(self.timer_name).strip()

        if not timer_name:
            await interaction.response.send_message(
                embed=build_embed("Invalid Timer", "กรุณาใส่ชื่อ timer"),
                ephemeral=True,
            )
            return

        try:
            total_seconds = parse_duration(str(self.duration))
        except ValueError:
            await interaction.response.send_message(
                embed=build_embed("Invalid Timer", "ใช้รูปแบบเวลาเช่น `30`, `10s`, `5m` หรือ `1h`"),
                ephemeral=True,
            )
            return

        if total_seconds <= 0:
            await interaction.response.send_message(
                embed=build_embed("Invalid Timer", "เวลาต้องมากกว่า 0"),
                ephemeral=True,
            )
            return

        try:
            await create_timer(interaction.user, interaction.channel, timer_name, total_seconds)
        except ValueError:
            await interaction.response.send_message(
                embed=build_embed("Duplicate Timer", f"มี timer ชื่อ `{timer_name}` อยู่แล้ว"),
                ephemeral=True,
            )
            return

        embed = build_embed(
            "Timer Started",
            f"{interaction.user.mention} สร้าง timer `{timer_name}` เป็นเวลา {format_duration(total_seconds)}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class StopTimerModal(discord.ui.Modal, title="Stop Timer"):
    timer_name = discord.ui.TextInput(
        label="Timer Name or all",
        placeholder="ใส่ชื่อ timer หรือพิมพ์ all",
        required=True,
        max_length=40,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        timer_name = str(self.timer_name).strip()
        if not timer_name:
            await interaction.response.send_message(
                embed=build_embed("Timer Stopped", "กรุณาใส่ชื่อ timer หรือ `all`"),
                ephemeral=True,
            )
            return

        if timer_name.lower() == "all":
            cancelled_count = await cancel_all_timers(interaction.user.id)
            if cancelled_count == 0:
                embed = build_embed(
                    "Timer Stopped",
                    f"{interaction.user.mention} ไม่มีตัวจับเวลาที่กำลังทำงานอยู่",
                )
            else:
                embed = build_embed(
                    "Timer Stopped",
                    f"{interaction.user.mention} ยกเลิก timer ทั้งหมด {cancelled_count} รายการแล้ว",
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        stopped = await cancel_timer_by_name(interaction.user.id, timer_name)
        if not stopped:
            embed = build_embed(
                "Timer Stopped",
                f"{interaction.user.mention} ไม่พบ timer ชื่อ `{timer_name}`",
            )
        else:
            embed = build_embed(
                "Timer Stopped",
                f"{interaction.user.mention} ยกเลิก timer `{timer_name}` แล้ว",
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class TimerControlView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Start Timer",
        style=discord.ButtonStyle.primary,
        custom_id="timer:start",
    )
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TimerDurationModal())

    @discord.ui.button(
        label="Status",
        style=discord.ButtonStyle.secondary,
        custom_id="timer:status",
    )
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=build_timer_list_embed(interaction.user),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Stop Timer",
        style=discord.ButtonStyle.danger,
        custom_id="timer:stop",
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StopTimerModal())


@client.event
async def on_ready():
    client.add_view(TimerControlView())
    print(f"Successfully logged in as {client.user}")


@client.event
async def on_message(message):
    if message.author.bot:
        return

    await client.process_commands(message)


@client.command()
async def hello(ctx):
    await ctx.send(f"Hello! {ctx.author}")


@client.command(name="timer")
async def timer_command(ctx, *, timer_input: str):
    try:
        timer_name, total_seconds = parse_named_timer_input(timer_input)
    except ValueError:
        embed = build_embed(
            "Invalid Timer",
            "ใช้คำสั่งแบบ `#!timer <ชื่อ> <เวลา>` เช่น `#!timer อ่านหนังสือ 30m`",
        )
        await ctx.send(embed=embed)
        return

    if total_seconds <= 0:
        await ctx.send(embed=build_embed("Invalid Timer", "เวลาต้องมากกว่า 0"))
        return

    try:
        await create_timer(ctx.author, ctx.channel, timer_name, total_seconds)
    except ValueError:
        await ctx.send(
            embed=build_embed("Duplicate Timer", f"มี timer ชื่อ `{timer_name}` อยู่แล้ว"),
        )
        return

    embed = build_embed(
        "Timer Started",
        f"{ctx.author.mention} สร้าง timer `{timer_name}` เป็นเวลา {format_duration(total_seconds)}",
    )
    await ctx.send(embed=embed)


@client.command(name="status")
async def status_command(ctx):
    await ctx.send(embed=build_timer_list_embed(ctx.author))


@client.command(name="timers")
async def timers_command(ctx):
    await ctx.send(embed=build_timer_list_embed(ctx.author))


@client.command(name="stop")
async def stop_command(ctx, *, timer_name: str | None = None):
    if timer_name is None or not timer_name.strip():
        await ctx.send(
            embed=build_embed(
                "Timer Stopped",
                "ใช้ `#!stop <ชื่อ>` หรือ `#!stop all`",
            )
        )
        return

    cleaned_name = timer_name.strip()
    if cleaned_name.lower() == "all":
        cancelled_count = await cancel_all_timers(ctx.author.id)
        if cancelled_count == 0:
            embed = build_embed("Timer Stopped", f"{ctx.author.mention} ไม่มีตัวจับเวลาที่กำลังทำงานอยู่")
        else:
            embed = build_embed(
                "Timer Stopped",
                f"{ctx.author.mention} ยกเลิก timer ทั้งหมด {cancelled_count} รายการแล้ว",
            )
        await ctx.send(embed=embed)
        return

    stopped = await cancel_timer_by_name(ctx.author.id, cleaned_name)
    if not stopped:
        embed = build_embed("Timer Stopped", f"{ctx.author.mention} ไม่พบ timer ชื่อ `{cleaned_name}`")
    else:
        embed = build_embed("Timer Stopped", f"{ctx.author.mention} ยกเลิก timer `{cleaned_name}` แล้ว")

    await ctx.send(embed=embed)


@commands.has_permissions(manage_channels=True)
@client.command(name="setup")
async def setup_command(ctx):
    if ctx.guild is None:
        await ctx.send(embed=build_embed("Setup Failed", "คำสั่งนี้ใช้ได้เฉพาะในเซิร์ฟเวอร์"))
        return

    channel = discord.utils.get(ctx.guild.text_channels, name=UI_CHANNEL_NAME)
    if channel is None:
        channel = await ctx.guild.create_text_channel(
            UI_CHANNEL_NAME,
            topic=UI_CHANNEL_TOPIC,
            reason=f"Requested by {ctx.author}",
        )

    await channel.send(embed=build_panel_embed(), view=TimerControlView())
    embed = build_embed("Setup Complete", f"สร้างห้อง {channel.mention} และส่ง UI ให้แล้ว")
    await ctx.send(embed=embed)


@setup_command.error
async def setup_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=build_embed("Setup Failed", "คุณต้องมีสิทธิ์จัดการห้องเพื่อใช้ `#!setup`"))
        return
    raise error


def run_bot() -> None:
    token = (os.getenv("TOKEN") or "").strip()

    if not token:
        raise RuntimeError("Missing TOKEN in .env")

    try:
        client.run(token)
    except discord.LoginFailure as error:
        raise RuntimeError(
            "Discord login failed. Check your bot token in .env and reset it in the Discord Developer Portal if needed."
        ) from error


if __name__ == "__main__":
    run_bot()
