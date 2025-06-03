"""HuntingTourneyBot: Discord bot for managing Sonic Adventure 2: Battle hunting tourney drafts."""

from asyncio import run_coroutine_threadsafe, sleep as asyncio_sleep
from configparser import ConfigParser
from random import choice, randint, shuffle
from sys import exit as sys_exit
from threading import Thread
from time import sleep as time_sleep
from xml.dom.minidom import parseString
from xml.etree.ElementTree import Element, SubElement, tostring
from typing import Any, Dict, List, Optional, Tuple
import os

import discord

PREFIX = "!"
# TOKEN is loaded from a separate file for security.
# Do NOT check token.txt into version control.
TOKEN_FILE = "token.txt"


def load_token(token_file: str = TOKEN_FILE) -> str:
    """Load the Discord bot token from a file."""
    try:
        with open(token_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(
            f"Error: {token_file} not found. "
            "Please create this file and put your Discord bot token inside."
        )
        sys_exit(1)


# File paths for OBS text files
DRAFT_STATUS_FILE = "draft_status.txt"
OUTPUT_DIR = ".output"

# Maximum seed value in tourney mod config (32-bit int)
MAX_SEED = 2**31 - 1

intents = discord.Intents.default()
intents.message_content = True

# Updated STAGES dictionary with full names and abbreviations
STAGES: Dict[str, List[str]] = {
    "Wild Canyon": ["WC", "wild", "canyon"],
    "Pumpkin Hill": ["PH", "pumpkin", "hill"],
    "Death Chamber": ["DC", "death", "chamber"],
    "Aquatic Mine": ["AM", "aquatic", "mine"],
    "Meteor Herd": ["MH", "meteor", "herd"],
    "Dry Lagoon": ["DL", "dry", "lagoon"],
    "Egg Quarters": ["EQ", "egg", "quarters"],
    "Security Hall": ["SH", "security", "hall"],
    "Mad Space": ["MS", "mad", "space"],
}

# List of full stage names for validation
STAGE_NAMES: List[str] = list(STAGES.keys())


class DraftManager:
    """Manages the draft process and file generation for the tournament."""

    def __init__(self) -> None:
        self.runner1: Optional[str] = None
        self.runner2: Optional[str] = None
        self.first_banner: Optional[str] = None
        self.second_banner: Optional[str] = None
        self.banned_stages: List[str] = []
        self.current_turn: Optional[str] = None
        self.draft_active: bool = False
        self.countdown_channel: Optional[discord.TextChannel] = None
        self.waiting_for_ban1: bool = False
        self.waiting_for_ban2: bool = False
        self.console_input_thread: Optional[Thread] = None
        self.ordered_stages: List[Tuple[str, int, str]] = []
        self.split_list: List[str] = []
        self.output_lines: List[str] = ["", "", ""]

    def reset(self) -> None:
        self.runner1 = None
        self.runner2 = None
        self.first_banner = None
        self.second_banner = None
        self.banned_stages = []
        self.current_turn = None
        self.draft_active = False
        self.countdown_channel = None
        self.waiting_for_ban1 = False
        self.waiting_for_ban2 = False
        self.ordered_stages = []
        self.split_list = []

        self.clear_draft_status()

    def write_draft_status(self) -> bool:
        """Write content to a file for OBS to display"""
        try:
            with open(DRAFT_STATUS_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(self.output_lines))
            return True
        except OSError as e:
            print(f"Error writing to file {DRAFT_STATUS_FILE}: {str(e)}")
            return False

    def clear_draft_status(self) -> None:
        """Clear the output lines and write to file"""
        self.output_lines = ["", "", ""]
        self.write_draft_status()

    def generate_ordered_stages(
        self,
    ) -> Tuple[List[Tuple[str, int, str]], Dict[str, str]]:
        """Generate a consistent ordered list of stages for both config and splits files"""
        # Create stage abbreviations mapping
        stage_abbrs: Dict[str, str] = {}
        for stage in STAGE_NAMES:
            abbr = STAGES[stage][0].lower()
            stage_abbrs[stage] = abbr

        # Reverse mapping from abbreviation to stage name
        abbr_to_stage: Dict[str, str] = {
            abbr: stage for stage, abbr in stage_abbrs.items()
        }

        # Create the order dictionary for all stages
        order_dict: Dict[str, str] = {}

        # First, set all banned stages to 0
        for stage in self.banned_stages:
            if stage in stage_abbrs:
                order_dict[stage_abbrs[stage]] = "0"

        # Then assign sequential values to remaining stages
        remaining_stages = [
            stage for stage in STAGE_NAMES if stage not in self.banned_stages
        ]
        shuffle(remaining_stages)
        if remaining_stages and remaining_stages[0] == "Security Hall":
            # If Security Hall is first, swap it with another random stage
            swap_idx = randint(1, len(remaining_stages) - 1)
            remaining_stages[0], remaining_stages[swap_idx] = (
                remaining_stages[swap_idx],
                remaining_stages[0],
            )

        for i, stage in enumerate(remaining_stages):
            if stage in stage_abbrs:
                order_dict[stage_abbrs[stage]] = str(i + 1)

        # Make sure order_dict has all stages
        for stage in STAGE_NAMES:
            abbr = stage_abbrs[stage]
            if abbr not in order_dict:
                order_dict[abbr] = "0"  # Default to 0 if missing

        # Create a list of stages ordered by their config order value
        ordered_stages = []
        for abbr, order_value in order_dict.items():
            stage_name = abbr_to_stage[abbr]
            ordered_stages.append((stage_name, int(order_value), abbr))

        self.ordered_stages = ordered_stages

        # Return the complete ordered data
        return ordered_stages, stage_abbrs

    def _create_livesplit_segments(
        self, segments: Element, stage_order: List[str], total_splits: int
    ) -> None:
        """Add segments to the LiveSplit XML for each stage and split."""
        self.split_list = []
        # Create segments for each stage and split
        split_num = 1
        for stage in stage_order:
            for i in range(1, 6):  # 5 splits per stage
                split_name = f"{stage} {i}"
                self.split_list.append(split_name)
                split_name += f" ({split_num}/{total_splits})"

                segment = SubElement(segments, "Segment")
                name = SubElement(segment, "Name")
                name.text = split_name
                split_num += 1
                SubElement(segment, "Icon")
                split_times = SubElement(segment, "SplitTimes")
                SubElement(split_times, "SplitTime", name="Personal Best")
                SubElement(segment, "BestSegmentTime")
                SubElement(segment, "SegmentHistory")

    def _add_livesplit_settings(self, custom_settings: Element) -> None:
        """Add custom settings to the LiveSplit XML."""
        settings = [
            ("storyStart", "False"),
            ("NG+", "False"),
            ("huntingTimer", "True"),
            ("timeIGT", "False"),
            ("combinedHunting", "False"),
            ("no280", "False"),
            ("fileReset", "True"),
            ("stageExit", "False"),
            ("resetIL", "False"),
            ("stageEntry", "False"),
            ("chaoRace", "False"),
            ("backRing", "False"),
            ("cannonsCore", "False"),
            ("bossRush", "False"),
        ]
        for setting_id, value in settings:
            setting = SubElement(custom_settings, "Setting", id=setting_id, type="bool")
            setting.text = value

    def generate_split_file(self) -> str:
        """Generate the LiveSplit split file (.lss) based on remaining stages"""
        # Ensure output directory exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        # Generate a timestamp and filename for the split file
        filename = f"SA2B - Hunting Tourney - {self.runner1} vs {self.runner2}.lss"
        filepath = os.path.join(OUTPUT_DIR, filename)

        # Use the ordered stages determined by the draft
        ordered_stages = self.ordered_stages

        # Create the root XML element for the LiveSplit file
        root = Element("Run", version="1.7.0")
        SubElement(root, "GameIcon")

        # Add game and category information
        game_name = SubElement(root, "GameName")
        game_name.text = "Sonic Adventure 2: Battle - Category Extensions"
        category_name = SubElement(root, "CategoryName")
        category_name.text = "Hunting Tourney!"
        SubElement(root, "LayoutPath")

        # Add metadata section
        metadata = SubElement(root, "Metadata")
        SubElement(metadata, "Run", id="")
        SubElement(metadata, "Platform", usesEmulator="False")
        SubElement(metadata, "Region")
        SubElement(metadata, "Variables")

        # Add run offset and attempt info
        offset = SubElement(root, "Offset")
        offset.text = "00:00:00"
        attempt_count = SubElement(root, "AttemptCount")
        attempt_count.text = "0"
        SubElement(root, "AttemptHistory")

        # Add segments for each stage and split
        segments = SubElement(root, "Segments")
        # Filter out banned stages (order=0) and sort by order
        active_stages = [
            (stage, order) for stage, order, _ in ordered_stages if order > 0
        ]
        active_stages.sort(key=lambda x: x[1])
        # Get the ordered list of stage names
        stage_order = [stage for stage, _ in active_stages]
        total_splits = len(stage_order) * 5
        # Add segment XML nodes for each split
        self._create_livesplit_segments(segments, stage_order, total_splits)

        # Add AutoSplitterSettings section
        auto_splitter = SubElement(root, "AutoSplitterSettings")
        version = SubElement(auto_splitter, "Version")
        version.text = "1.5"
        script_path = SubElement(auto_splitter, "ScriptPath")
        script_path.text = (
            "C:\\Users\\PC\\Downloads\\LiveSplit_1.8.16\\Components\\LiveSplit.SA2.asl"
        )
        start = SubElement(auto_splitter, "Start")
        start.text = "True"
        reset = SubElement(auto_splitter, "Reset")
        reset.text = "True"
        split = SubElement(auto_splitter, "Split")
        split.text = "True"
        custom_settings = SubElement(auto_splitter, "CustomSettings")
        # Add custom settings for the autosplitter
        self._add_livesplit_settings(custom_settings)

        # Convert the XML tree to a pretty-printed string
        rough_string = tostring(root, "utf-8")
        reparsed = parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ")

        # Write the XML to the split file in the output directory
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(pretty_xml)
        print(f"Generated LiveSplit file: {filepath}")
        return filepath

    def _write_config_section(
        self, config: ConfigParser, section: str, data: Dict[str, str]
    ) -> None:
        config[section] = data

    def generate_config_file(self) -> str:
        """Generate the config file based on remaining stages"""
        # Ensure output directory exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        filename = "config.ini"
        filepath = os.path.join(OUTPUT_DIR, filename)
        ordered_stages, stage_abbrs = self.generate_ordered_stages()
        config = ConfigParser()
        config.optionxform = str  # type: ignore
        self._write_config_section(
            config, "set", {"seed": str(randint(1, MAX_SEED)), "ups": "True"}
        )
        order_dict = {
            abbr: str(order_value) for stage_name, order_value, abbr in ordered_stages
        }
        for stage in STAGE_NAMES:
            abbr = stage_abbrs[stage]
            if abbr not in order_dict:
                order_dict[abbr] = "0"
        self._write_config_section(config, "order", order_dict)
        number_dict = {stage_abbrs[stage]: "5" for stage in STAGE_NAMES}
        self._write_config_section(config, "number", number_dict)
        with open(filepath, "w", encoding="utf-8") as configfile:
            for section in config.sections():
                configfile.write(f"[{section}]\n")
                for key, value in config[section].items():
                    configfile.write(f"{key}={value}\n")
                configfile.write("\n")
        print(f"Generated config file: {filepath}")
        return filepath

    def resolve_stage_name(self, stage_input: str) -> Optional[str]:
        """Convert abbreviated or partial stage name to full stage name"""
        stage_input = stage_input.lower()

        # First check for exact matches (case insensitive)
        for stage_name, abbreviations in STAGES.items():
            if stage_input == stage_name.lower():
                return stage_name

        # Then check for abbreviations and partial matches
        for stage_name, abbreviations in STAGES.items():
            if stage_input in [abbr.lower() for abbr in abbreviations]:
                return stage_name

        # No match found
        return None

    def start_console_input(self, discord_client: "MyClient") -> None:
        """Start a thread to handle console input for stage bans"""
        if self.console_input_thread and self.console_input_thread.is_alive():
            return  # Thread already running

        self.console_input_thread = Thread(
            target=self.console_input_loop, args=(discord_client,), daemon=True
        )
        self.console_input_thread.start()

    def console_input_loop(self, discord_client: "MyClient") -> None:
        """Loop to handle console input for stage bans"""
        while self.draft_active:
            if self.waiting_for_ban1 or self.waiting_for_ban2:
                print(
                    f"\nWaiting for {'first' if self.waiting_for_ban1 else 'second'} stage ban..."
                )
                print(
                    f"Enter stage name or abbreviation for {self.current_turn} to ban:"
                )

                # Print available stages
                print("\nAvailable stages:")
                for name, abbrs in STAGES.items():
                    if (
                        name not in self.banned_stages
                    ):  # Only show stages that haven't been banned
                        print(f"- {name} ({abbrs[0]})")

                try:
                    stage_input = input("> ").strip()
                    full_stage_name = self.resolve_stage_name(stage_input)

                    if not full_stage_name:
                        print("Invalid stage name. Please try again.")
                        continue

                    if full_stage_name in self.banned_stages:
                        print(
                            f"{full_stage_name} has already been banned. "
                            "Please choose another stage."
                        )
                        continue

                    # Process the ban asynchronously
                    if self.waiting_for_ban1:
                        future = run_coroutine_threadsafe(
                            discord_client.process_ban1(full_stage_name),
                            discord_client.loop,
                        )
                        # Wait for the future to complete with a timeout
                        try:
                            future.result(timeout=60 * 5)
                        except Exception as e:
                            print(f"Error processing ban1: {str(e)}")
                    else:
                        future = run_coroutine_threadsafe(
                            discord_client.process_ban2(full_stage_name),
                            discord_client.loop,
                        )
                        # Wait for the future to complete with a timeout
                        try:
                            future.result(timeout=60 * 5)
                        except Exception as e:
                            print(f"Error processing ban2: {str(e)}")

                except Exception as e:
                    print(f"Error processing input: {str(e)}")

            # Sleep a bit before checking again
            time_sleep(0.5)


class MyClient(discord.Client):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.draft_manager = DraftManager()

    async def on_ready(self) -> None:
        print(f"We have logged in as {self.user}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        # Check if this is a command (starts with prefix)
        if message.content.startswith(PREFIX):
            tokens = message.content.split()
            command = tokens[0][len(PREFIX) :].lower()

            # Command #1: !start <Runner Name 1> <Runner Name 2>
            if command == "start":
                await self._handle_start_command(message, tokens)
            # Command #2: !end
            elif command == "end":
                self.draft_manager.reset()
                await message.channel.send("The draft has been reset.")

    async def _handle_start_command(
        self, message: discord.Message, tokens: List[str]
    ) -> None:
        """Handle the !start command logic."""
        # Role Check
        for role in getattr(message.author, "roles", []):
            if role.name.lower() == "hunting drafter":
                break
        else:
            await message.channel.send(
                "You need the Hunting Drafter role to use this command."
            )
            return

        # Argument Validation
        if len(tokens) != 3:
            await message.channel.send("Usage: !start <Runner Name 1> <Runner Name 2>")
            return

        # Ensure runner names are less than 10 characters
        if len(tokens[1]) >= 10 or len(tokens[2]) >= 10:
            await message.channel.send("Runner names must be less than 10 characters long.")
            return

        # Draft Initialization
        self.draft_manager.reset()
        self.draft_manager.runner1 = tokens[1]
        self.draft_manager.runner2 = tokens[2]
        self.draft_manager.draft_active = True
        # Only assign countdown_channel if the channel is a TextChannel
        if isinstance(message.channel, discord.TextChannel):
            self.draft_manager.countdown_channel = message.channel
        else:
            self.draft_manager.countdown_channel = None
        self.draft_manager.output_lines[0] = "The draft is starting!"
        self.draft_manager.write_draft_status()

        # Coin Flip to Decide First Banner
        coin_flip = choice([0, 1])
        if coin_flip == 0:
            self.draft_manager.first_banner = self.draft_manager.runner1
            self.draft_manager.second_banner = self.draft_manager.runner2
        else:
            self.draft_manager.first_banner = self.draft_manager.runner2
            self.draft_manager.second_banner = self.draft_manager.runner1
        self.draft_manager.current_turn = self.draft_manager.first_banner

        # Countdown and Announcement
        await message.channel.send("Who will ban first!?")
        await asyncio_sleep(1)
        await message.channel.send("3...")
        await asyncio_sleep(1)
        await message.channel.send("2...")
        await asyncio_sleep(1)
        await message.channel.send("1...")
        await asyncio_sleep(1)
        await message.channel.send(
            f"{self.draft_manager.first_banner} bans first! "
            "Please type which stage you would like to ban."
        )

        # Update Draft Status and Start Console Input
        self.draft_manager.output_lines[0] = (
            f"{self.draft_manager.first_banner}'s turn to ban"
        )
        self.draft_manager.write_draft_status()
        print(f"\n{self.draft_manager.first_banner} bans first!")
        self.draft_manager.waiting_for_ban1 = True
        self.draft_manager.start_console_input(self)

    async def _process_ban(
        self,
        full_stage_name: str,
        banner: Optional[str],
        next_banner: Optional[str],
        line_index: int,
        waiting_attr: str,
        next_waiting_attr: Optional[str],
        is_final_ban: bool = False,
    ) -> None:
        """Generalized method to process a stage ban for a runner."""
        if not self.draft_manager.draft_active or not getattr(
            self.draft_manager, waiting_attr
        ):
            return
        if banner is None:
            print("Error: banner is None in _process_ban.")
            return
        # Record the banned stage
        self.draft_manager.banned_stages.append(full_stage_name)
        # Update the ban file
        self.draft_manager.output_lines[line_index] = full_stage_name
        self.draft_manager.write_draft_status()
        # Update draft status temporarily
        self.draft_manager.output_lines[0] = f"{banner} banned {full_stage_name}"
        self.draft_manager.write_draft_status()
        # Send message to discord
        if self.draft_manager.countdown_channel:
            if not is_final_ban and next_banner is not None:
                await self.draft_manager.countdown_channel.send(
                    f"{full_stage_name} has been banned. It's {next_banner}'s turn to ban. Please type which stage you would like to ban."
                )
            else:
                await self.draft_manager.countdown_channel.send(
                    f"{full_stage_name} has been banned."
                )

        # Wait a moment
        await asyncio_sleep(3)

        if not is_final_ban and next_banner is not None:
            # Switch turns
            self.draft_manager.current_turn = next_banner
            self.draft_manager.output_lines[0] = f"{next_banner}'s turn to ban"
            self.draft_manager.write_draft_status()
            setattr(self.draft_manager, waiting_attr, False)
            if next_waiting_attr:
                setattr(self.draft_manager, next_waiting_attr, True)
            print(f"\n{banner} banned {full_stage_name}")
            print(f"Now waiting for {next_banner}'s ban...")
        else:
            print(f"\n{banner} banned {full_stage_name}")

            # Conclude the draft
            self.draft_manager.output_lines[0] = "Draft concluded"
            self.draft_manager.write_draft_status()
            config_file = self.draft_manager.generate_config_file()
            split_file = self.draft_manager.generate_split_file()
            if self.draft_manager.countdown_channel:
                try:
                    await self.draft_manager.countdown_channel.send(
                        (
                            "The draft has been concluded. Thank you. Here are the split file and the config file.\n"
                            "As a reminder, the config.ini file goes inside this folder: "
                            r'"C:\Program Files (x86)\Steam\steamapps\common\Sonic Adventure 2\mods\HuntingTourney"'
                        ),
                        files=[discord.File(split_file), discord.File(config_file)],
                    )
                    print(
                        f"Successfully sent files to Discord: {split_file} and {config_file}"
                    )
                except Exception as e:
                    print(f"Error sending files to Discord: {str(e)}")
                    # Try to send files individually if combined send fails
                    try:
                        await self.draft_manager.countdown_channel.send(
                            "Split file:", file=discord.File(split_file)
                        )
                        await self.draft_manager.countdown_channel.send(
                            "Config file:", file=discord.File(config_file)
                        )
                        print("Successfully sent files individually")
                    except Exception as e2:
                        print(f"Error sending individual files: {str(e2)}")

            # Send splits list to pacekeeping channel
            if self.draft_manager.countdown_channel:
                # TODO make this channel configurable
                pacekeeping_channel = discord.utils.get(
                    self.draft_manager.countdown_channel.guild.text_channels,
                    name="⏰-pacekeeping",
                )
                if pacekeeping_channel:
                    try:
                        await pacekeeping_channel.send(
                            "Here is the list of splits for the draft:",
                        )
                        await pacekeeping_channel.send(
                            "\n".join(self.draft_manager.split_list)
                        )
                        print("Successfully sent splits list to pacekeeping channel")
                    except Exception as e:
                        print(f"Error sending splits list to pacekeeping channel: {str(e)}")

            # Reset the draft status
            self.draft_manager.waiting_for_ban2 = False
            self.draft_manager.draft_active = False
            print("The draft has been concluded.")

    async def process_ban1(self, full_stage_name: str) -> None:
        await self._process_ban(
            full_stage_name=full_stage_name,
            banner=self.draft_manager.first_banner,
            next_banner=self.draft_manager.second_banner,
            line_index=(
                1
                if self.draft_manager.first_banner == self.draft_manager.runner1
                else 2
            ),
            waiting_attr="waiting_for_ban1",
            next_waiting_attr="waiting_for_ban2",
            is_final_ban=False,
        )

    async def process_ban2(self, full_stage_name: str) -> None:
        await self._process_ban(
            full_stage_name=full_stage_name,
            banner=self.draft_manager.second_banner,
            next_banner=None,
            line_index=(
                1
                if self.draft_manager.second_banner == self.draft_manager.runner1
                else 2
            ),
            waiting_attr="waiting_for_ban2",
            next_waiting_attr=None,
            is_final_ban=True,
        )


if __name__ == "__main__":
    print("Starting Hunting Tournament Bot...")
    print("Use !start <Runner1> <Runner2> in Discord to begin a draft.")
    print("Stage bans will be entered through this console window.")
    client = MyClient(intents=intents)
    token = load_token()
    client.run(token)
