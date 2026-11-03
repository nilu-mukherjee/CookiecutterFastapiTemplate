"""Render one full walkthrough video: cookiecutter from GitHub, answer the prompts, run the API.

Every output line below was captured from a real run of these commands (see the
README quick start). The typing is animated at a slow, human pace.

    python scripts/make_full_demo.py [output.mp4]      # default: demo/full-walkthrough.mp4
"""

import random
import sys
from pathlib import Path

from moviepy import VideoClip
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import make_demos as m  # noqa: E402

URL = "https://github.com/nilu-mukherjee/CookiecutterFastapiTemplate.git"
CARD = 3.0
LS = ["alembic          app          Dockerfile   proto           scripts",
      "alembic.ini      docker-compose.yml  Makefile     pyproject.toml  tests",
      "README.md"]

# The first prompts as cookiecutter prints them, with the answers typed. Enter keeps the default.
PROMPTS = [
    "  [1/26] project_name (My Project): Orders API",
    "  [2/26] project_slug (orders_api):",
    "  [3/26] author (Your Name):",
    "  [4/26] author_email (you@example.com):",
    "  [5/26] Select python_version",
    "    1 - 3.12", "    2 - 3.13",
    "    Choose from [1/2] (1):",
    "  [6/26] Select database",
    "    1 - postgresql", "    2 - mysql", "    3 - sqlite",
    "    Choose from [1/2/3] (1): 3",
    "# Enter accepts the default for the other 20 prompts",
]

# (command, output lines, seconds the command runs before output, seconds per output line, typing cap)
STEPS = [
    ("cookiecutter " + URL, PROMPTS, 3.0, .35, 4.0),
    ("cd orders_api && ls", LS, .3, .12, 3.0),
    ("uv sync 2>&1 | tail -4", [" + vine==5.1.0", " + watchfiles==1.3.0", " + wcwidth==0.9.4",
                                " + websockets==17.4"], 3.5, .12, 3.0),
    ("cp .env.example .env", [], .2, 0, 3.0),
    ("uv run alembic upgrade head", [], 1.5, 0, 3.0),
    ("uv run uvicorn app.main:app --port 8766",
     ["INFO:     Started server process [2002760]", "INFO:     Waiting for application startup.",
      "INFO:     Application startup complete.",
      "INFO:     Uvicorn running on http://127.0.0.1:8766 (Press CTRL+C to quit)",
      "# in a second terminal"], 1.5, .25, 3.0),
    ("curl localhost:8766/health/live", ['{"status":"ok"}'], .3, 0, 3.0),
    ("curl -i localhost:8766/api/v1/items | head -1", ["HTTP/1.1 401 Unauthorized"], .3, 0, 4.0),
]


def build():
    rng = random.Random("full")
    events, t = [], CARD + .5
    for cmd, out, wait, per_line, cap in STEPS:
        frames, t = m.type_keyframes(cmd, t + rng.uniform(.4, .8), rng, cap)
        events.append(("cmd", frames))
        t += .3 + wait
        for line in out:
            events.append(("out", [(t, line)]))
            t += per_line
    return events, t + 2.5


def main(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(m.FONT, m.SIZE)
    fonts = (ImageFont.truetype(m.FONT, 64), ImageFont.truetype(m.FONT, 28))
    line_h, cols = int(m.SIZE * 1.5), int((m.W - 80) / font.getlength("M"))
    events, duration = build()
    intro = m.card("cookiecutter", ["Generate a project from the template on GitHub,",
                                  "answer the prompts, then run it"], fonts)
    clip = VideoClip(lambda t: intro if t < CARD else m.render(events, t, font, line_h, cols),
                     duration=duration)
    clip.write_videofile(str(path), fps=m.FPS, codec="libx264", logger=None)
    print(f"{path} ({duration:.1f}s)")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo/full-walkthrough.mp4"))
