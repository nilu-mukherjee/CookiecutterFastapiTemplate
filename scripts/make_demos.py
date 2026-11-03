"""Render one short terminal demo video (at most 10 s) for every template option.

For each option the script really generates a project with that option on and
a baseline with every flag off, then shows the real `diff -rq` between them in
a human-paced terminal animation (variable typing speed, a typo now and then,
streaming output). Needs `uvx` (cookiecutter) and moviepy.

    python scripts/make_demos.py [output_dir]      # default: demo/
"""

import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from moviepy import VideoClip
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
MAX_SECONDS = 10.0
W, H, FPS, SIZE, BAR = 1280, 720, 24, 24, 48
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
COLORS = dict(bg="#000000", bar="#1c1c1c", fg="#ffffff", dim="#9a9a9a")
CARD = 1.6  # seconds the intro card (what the option does) stays on screen
PROMPT = "~ $ "

FLAGS = [
    "use_docker", "use_github_actions", "use_auth", "use_auth_jwt_bearer", "use_auth_jwt_cookie",
    "use_auth_session", "use_auth_api_key", "use_auth_basic", "use_social_login", "use_rbac",
    "use_multitenancy", "use_redis", "use_sse", "use_long_polling", "use_websocket",
    "use_graphql", "use_grpc", "use_celery", "use_celery_beat", "use_flower",
]
AUTH = ["use_auth=y", "use_auth_jwt_bearer=y"]
# option name -> the flags to switch on (an option's required dependencies included)
OPTIONS = {
    "database-postgresql": ["database=postgresql"],
    "database-mysql": ["database=mysql"],
    "use_docker": ["use_docker=y"],
    "use_github_actions": ["use_github_actions=y"],
    "use_auth": AUTH,
    "use_auth_jwt_bearer": AUTH,
    "use_auth_jwt_cookie": ["use_auth=y", "use_auth_jwt_cookie=y"],
    "use_auth_session": ["use_auth=y", "use_auth_session=y"],
    "use_auth_api_key": [*AUTH, "use_auth_api_key=y"],
    "use_auth_basic": ["use_auth=y", "use_auth_basic=y"],
    "use_social_login": [*AUTH, "use_social_login=y"],
    "use_rbac": [*AUTH, "use_rbac=y"],
    "use_multitenancy": [*AUTH, "use_multitenancy=y"],
    "use_redis": ["use_redis=y"],
    "use_sse": ["use_sse=y"],
    "use_long_polling": ["use_long_polling=y"],
    "use_websocket": ["use_websocket=y"],
    "use_graphql": ["use_graphql=y"],
    "use_grpc": ["use_grpc=y"],
    "use_celery": ["use_celery=y"],
    "use_celery_beat": ["use_celery=y", "use_celery_beat=y"],
    "use_flower": ["use_celery=y", "use_flower=y"],
}

# What each option does: (line 1, line 2). Grounded in the README and the real diffs.
DESC = {
    "database-postgresql": ("Use PostgreSQL instead of the default SQLite", "Changes the config, .env.example and pyproject.toml"),
    "database-mysql": ("Use MySQL instead of the default SQLite", "Changes the config, .env.example and pyproject.toml"),
    "use_docker": ("Adds a Dockerfile, docker-compose.yml and .dockerignore", "and updates the Makefile"),
    "use_github_actions": ("Adds a GitHub Actions CI workflow", ""),
    "use_auth": ("Adds users, login and refresh tokens", "Needs at least one auth method"),
    "use_auth_jwt_bearer": ("Sign in with a JWT in the Authorization header", ""),
    "use_auth_jwt_cookie": ("Sign in with a JWT in an HttpOnly cookie", "With CSRF protection"),
    "use_auth_session": ("Sign in with server-side sessions", "Adds a sessions table and migration"),
    "use_auth_api_key": ("Authenticate with API keys", "Keys are created by a signed-in user"),
    "use_auth_basic": ("Authenticate with HTTP Basic", ""),
    "use_social_login": ("Adds OAuth sign-in with social accounts", "Adds a social_accounts table and an oauth endpoint"),
    "use_rbac": ("Adds roles and permissions", "Adds a roles endpoint and migration"),
    "use_multitenancy": ("Shared-schema multi-tenancy", "Repositories filter by tenant on every query"),
    "use_redis": ("Adds Redis cache and rate limiting", ""),
    "use_sse": ("Realtime item events over Server-Sent Events", "GET /api/v1/items/events"),
    "use_long_polling": ("Realtime item events by long polling", "GET /api/v1/items/poll"),
    "use_websocket": ("Realtime item events over WebSocket", "WS /api/v1/ws/items"),
    "use_graphql": ("Adds a GraphQL API at /graphql", "Same items service as REST"),
    "use_grpc": ("Adds a gRPC ItemService", "Run as its own process, default port 50051"),
    "use_celery": ("Adds Celery background workers", ""),
    "use_celery_beat": ("Adds Celery Beat scheduled tasks", "Needs use_celery"),
    "use_flower": ("Adds Flower, the Celery monitoring UI", "Needs use_celery"),
}


# ---------- capture: real generation + real diff ----------

def generate(out: Path, dest: Path, on: list[str]) -> None:
    args = {f: "n" for f in FLAGS} | {"database": "sqlite"}
    args |= dict(a.split("=") for a in on)
    kv = [f"{k}={v}" for k, v in args.items()]
    subprocess.run(["uvx", "cookiecutter", str(REPO), "--no-input", "-o", str(out),
                    "project_name=proj", *kv], check=True, capture_output=True)
    (out / "proj").rename(dest)


def capture(work: Path, name: str, on: list[str]) -> list[str]:
    generate(work / f"gen-{name}", work / "proj", on)
    r = subprocess.run(["diff", "-rq", "base", "proj"], cwd=work, capture_output=True, text=True)
    lines = r.stdout.splitlines()
    (work / "proj").rename(work / f"done-{name}")  # keep the work dir clean for the next option
    return lines


def display(line: str) -> str:
    """`Only in proj/app: x.py` -> `+ proj/app/x.py`; `Files a and b differ` -> `~ b`."""
    if line.startswith("Only in "):
        d, f = line[len("Only in "):].split(": ", 1)
        return f"{'+' if d.startswith('proj') else '-'} {d}/{f}"
    if line.startswith("Files "):
        return "~ " + line.split(" and ")[1].removesuffix(" differ")
    return line


# ---------- timeline: human-paced typing ----------

def type_keyframes(text: str, t0: float, rng: random.Random, cap: float = 3.0):
    """Return ([(time, shown_text)], end_time) with jittery speed and one typo."""
    frames, shown, t = [(t0, "")], "", t0
    step = min(.09, cap / len(text))  # slow (10 keys/s) unless a long command needs the cap
    typo_at = rng.randrange(len(text) // 3, len(text) - 3) if 12 < len(text) <= 40 and rng.random() < .7 else -1
    for i, ch in enumerate(text):
        if i == typo_at:  # hit a wrong key, notice, backspace
            shown += rng.choice("qwertyuiopasdfghjklzxcvbnm".replace(ch, ""))
            t += rng.uniform(.06, .12)
            frames.append((t, shown))
            t += rng.uniform(.25, .45)
            shown = shown[:-1]
            frames.append((t, shown))
        t += step * rng.uniform(.6, 1.4) + (.08 if ch in " =-" and rng.random() < .3 else 0)
        shown += ch
        frames.append((t, shown))
    return frames, t


def build(name: str, cmds: list[tuple[str, list[str]]]):
    """cmds: [(command, output_lines)] -> (events, duration). Event = (time, kind, text)."""
    rng = random.Random(name)
    events, t = [], CARD + .3
    for cmd, out in cmds:
        frames, t = type_keyframes(cmd, t + rng.uniform(.15, .3), rng)
        events.append(("cmd", frames))
        t += .25  # Enter
        for line in out:
            t += rng.uniform(.05, .12)
            events.append(("out", [(t, line)]))
    return events, t + .8


# ---------- rendering ----------

def card(title, lines, fonts):
    img = Image.new("RGB", (W, H), COLORS["bg"])
    d = ImageDraw.Draw(img)
    big, small = fonts
    d.text((W / 2, 250), title, font=big, fill=COLORS["fg"], anchor="mm")
    for i, line in enumerate(lines):
        d.text((W / 2, 340 + i * 44), line, font=small, fill=COLORS["fg"], anchor="mm")
    return np.asarray(img)


def render(events, t, font, line_h, cols):
    """Draw the terminal on a tall canvas and crop the bottom, so long sessions scroll."""
    canvas = Image.new("RGB", (W, H * 4), COLORS["bg"])
    d = ImageDraw.Draw(canvas)
    cw, y, x_cursor, y_cursor = font.getlength("M"), 24, 40, 24
    for kind, frames in events:
        if t < frames[0][0]:
            break
        text = [s for ft, s in frames if ft <= t][-1]
        if kind == "cmd":
            d.text((40, y), PROMPT, font=font, fill=COLORS["fg"])
            rows = text.split("\n")
            for row in rows:
                d.text((40 + cw * len(PROMPT), y), row, font=font, fill=COLORS["fg"])
                y += line_h
            y -= line_h
            x_cursor, y_cursor = 40 + cw * (len(PROMPT) + len(rows[-1])), y
        else:
            text = text if len(text) <= cols else text[: cols - 1] + "…"
            colour = COLORS["dim"] if text.startswith("#") else COLORS["fg"]
            d.text((40, y), text, font=font, fill=colour)
            x_cursor, y_cursor = 40, y + line_h
        y += line_h
    if int(t * 2) % 2 == 0 or t < 1:  # blinking block cursor
        d.rectangle([x_cursor, y_cursor + 2, x_cursor + cw, y_cursor + line_h - 4], fill=COLORS["fg"])
    view = H - BAR
    top = max(0, y_cursor + line_h + 24 - view)
    img = Image.new("RGB", (W, H), COLORS["bg"])
    img.paste(canvas.crop((0, top, W, top + view)), (0, BAR))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, BAR], fill=COLORS["bar"])
    for i, c in enumerate(("#f38ba8", "#f9e2af", "#a6e3a1")):
        d.ellipse([20 + i * 28, 16, 36 + i * 28, 32], fill=c)
    d.text((W / 2, BAR / 2), "nilu@dev: ~", font=font, fill=COLORS["dim"], anchor="mm")
    return np.asarray(img)


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT, SIZE)
    fonts = (ImageFont.truetype(FONT, 64), ImageFont.truetype(FONT, 28))
    line_h, cols = int(SIZE * 1.5), int((W - 80) / font.getlength("M"))
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        generate(work / "gen-base", work / "base", [])
        for n, (name, on) in enumerate(OPTIONS.items(), 1):
            lines = [display(x) for x in capture(work, name, on)]
            shown = lines[:5] + ([f"# … and {len(lines) - 5} more"] if len(lines) > 5 else [])
            cmd = "cookiecutter . --no-input $OFF" + (" \\\n" if len(on) > 1 else " ") + " ".join(on)
            events, duration = build(name, [(cmd, []), ("diff -rq base proj", shown)])
            if duration > MAX_SECONDS:
                raise SystemExit(f"{name}: {duration:.1f}s exceeds {MAX_SECONDS}s")
            title = " ".join(on[-1:]) if name.startswith("use_") else on[0]
            text = [*(x for x in DESC[name] if x), "$OFF = every other flag set to n"]
            intro = card(title, text, fonts)
            clip = VideoClip(lambda t: intro if t < CARD else render(events, t, font, line_h, cols),
                             duration=duration)
            path = out_dir / f"{n:02d}-{name}.mp4"
            clip.write_videofile(str(path), fps=FPS, codec="libx264", logger=None)
            print(f"{path} ({duration:.1f}s, {len(lines)} changes)", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo"))
