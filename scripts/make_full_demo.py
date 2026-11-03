"""Render one full walkthrough video: cookiecutter from GitHub, answer the prompts, run the API.

Every output line is captured from a real run of these commands when the script
starts (needs uvx, uv, network and port 8766). The typing is animated at a slow, human pace.

    python scripts/make_full_demo.py [output.mp4]      # default: demo/full-walkthrough.mp4
"""

import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from moviepy import VideoClip
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import make_demos as m  # noqa: E402

URL = "https://github.com/nilu-mukherjee/CookiecutterFastapiTemplate.git"
CARD = 3.0
PORT = 8766
ANSWERS = ["Orders API", "", "", "", "", "3"]  # the prompts we answer; Enter keeps the rest


def sh(cmd, cwd, env, check=True):
    r = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"{cmd!r} failed:\n{r.stdout}{r.stderr}")
    return (r.stdout + r.stderr).splitlines()


def capture(work: Path):
    """Run every command of the walkthrough for real and return STEPS with the real output.

    The template is generated from this checkout, so the output matches the code being
    documented. The video shows the GitHub URL a user would type.
    """
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    # The real prompts. Piped stdin does not echo, so the typed answers are added back.
    stdin = "".join(a + "\n" for a in ANSWERS) + "\n" * 40
    raw = subprocess.run(["uvx", "cookiecutter", str(m.REPO), "-o", str(work)], input=stdin, env=env,
                         capture_output=True, text=True, check=True).stdout
    chunks = re.split(r"(?=  \[\d+/\d+\] )", raw)[1:]
    total = int(re.match(r"  \[\d+/(\d+)\]", chunks[0]).group(1))
    prompts = []
    for chunk, answer in zip(chunks, ANSWERS):
        lines = chunk.rstrip().splitlines()
        lines[-1] += f" {answer}" if answer else ""
        prompts += lines
    prompts.append(f"# Enter accepts the default for the other {total - len(ANSWERS)} prompts")

    proj = work / "orders_api"
    ls = sh("ls -C -w 78", proj, env)
    sync = sh("uv sync 2>&1 | tail -4", proj, env)
    sh("cp .env.example .env", proj, env)
    migrate = sh("uv run alembic upgrade head", proj, env)

    server = subprocess.Popen(["uv", "run", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=proj,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        log = []
        for line in server.stdout:
            log.append(line.rstrip())
            if "Uvicorn running" in line:
                break
        else:
            raise SystemExit("uvicorn did not start:\n" + "\n".join(log))
        health = sh(f"curl -s localhost:{PORT}/health/live", proj, env)
        items = sh(f"curl -si localhost:{PORT}/api/v1/items | head -1", proj, env)
    finally:
        server.terminate()
        server.wait()
    items = [line.rstrip() for line in items]  # curl -i ends status lines with CR

    # (command, output lines, seconds the command runs before output, seconds per output line, typing cap)
    return [
        ("cookiecutter " + URL, prompts, 3.0, .35, 4.0),
        ("cd orders_api && ls", ls, .3, .12, 3.0),
        ("uv sync 2>&1 | tail -4", sync, 3.5, .12, 3.0),
        ("cp .env.example .env", [], .2, 0, 3.0),
        ("uv run alembic upgrade head", migrate, 1.5, .12, 3.0),
        (f"uv run uvicorn app.main:app --port {PORT}", [*log, "# in a second terminal"], 1.5, .25, 3.0),
        (f"curl localhost:{PORT}/health/live", health, .3, 0, 3.0),
        (f"curl -i localhost:{PORT}/api/v1/items | head -1", items, .3, 0, 4.0),
    ]


def build(steps):
    rng = random.Random("full")
    events, t = [], CARD + .5
    for cmd, out, wait, per_line, cap in steps:
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
    with tempfile.TemporaryDirectory() as tmp:
        steps = capture(Path(tmp))
    events, duration = build(steps)
    intro = m.card("cookiecutter", ["Generate a project from the template on GitHub,",
                                  "answer the prompts, then run it"], fonts)
    clip = VideoClip(lambda t: intro if t < CARD else m.render(events, t, font, line_h, cols),
                     duration=duration)
    clip.write_videofile(str(path), fps=m.FPS, codec="libx264", logger=None)
    print(f"{path} ({duration:.1f}s)")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo/full-walkthrough.mp4"))
