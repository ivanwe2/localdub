"""The guides: `localdub guide` for agents, the Agent Skill, and docs/cli.md stay in step with the commands."""
import json
import re
from pathlib import Path

from localdub.run import main

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ["check", "languages", "run", "shorts", "voice", "channel", "models", "guide"]


def subcommands(capsys) -> list[str]:
    """The commands `localdub --help` lists, as argparse prints them: {run,shorts,...}."""
    try:
        main(["--help"])
    except SystemExit:
        pass
    return re.search(r"\{([a-z,]+)\}", capsys.readouterr().out).group(1).split(",")


def test_the_guide_for_agents_covers_every_command(capsys):
    assert sorted(subcommands(capsys)) == sorted(COMMANDS), "a new command: add it to the guides and to COMMANDS"

    assert main(["guide"]) == 0
    guide = capsys.readouterr().out

    for command in COMMANDS:
        assert f"localdub {command}" in guide, command
    for must in ["status: approved", "waiting_for", "--key-stdin", "--shorts-only", "exit code", "agreed"]:
        assert must in guide, must


def test_the_guide_comes_as_an_event_with_json(capsys):
    assert main(["guide", "--json"]) == 0

    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert first["event"] == "guide" and first["text"].startswith("# localdub")


def test_the_skill_has_its_frontmatter_and_points_to_the_guide():
    text = (ROOT / "skills" / "localdub" / "SKILL.md").read_text(encoding="utf-8")

    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert front, "SKILL.md starts with --- frontmatter ---"
    fields = dict(line.split(": ", 1) for line in front.group(1).splitlines())
    assert fields["name"] == "localdub" and len(fields["description"]) > 80
    assert "localdub guide" in text


def test_the_command_line_guide_covers_every_command():
    text = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")

    for command in COMMANDS:
        assert f"localdub {command}" in text, command
