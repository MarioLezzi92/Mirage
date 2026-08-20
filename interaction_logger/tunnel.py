import os
import queue
import re
import shutil
import subprocess
import time
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PublicTunnel:
    def __init__(self, process: subprocess.Popen[str], url: str) -> None:
        self.process = process
        self.url = url

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)


def open_tunnel(root: Path, port: int) -> PublicTunnel:
    process = subprocess.Popen(
        [
            _executable(root),
            "tunnel",
            "--url",
            f"http://localhost:{port}",
            "--no-autoupdate",
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        if process.stdout:
            for line in process.stdout:
                lines.put(line)

    Thread(target=read_output, daemon=True).start()
    recent: list[str] = []
    deadline = time.monotonic() + 45
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = " | ".join(recent[-3:]) or "processo terminato"
                raise RuntimeError(f"Cloudflared non avviato: {detail}")
            try:
                line = lines.get(timeout=0.5).strip()
            except queue.Empty:
                continue
            if line:
                recent.append(line)
            match = pattern.search(line)
            if match:
                tunnel = PublicTunnel(process, match.group(0))
                _wait_until_reachable(f"{tunnel.url}/health")
                return tunnel
        raise RuntimeError("Cloudflared non ha fornito un URL entro 45 secondi")
    except Exception:
        PublicTunnel(process, "").close()
        raise


def _executable(root: Path) -> str:
    configured = os.getenv("CLOUDFLARED_PATH", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        root / "cloudflared.exe",
        root / "cloudflared",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate.resolve())
    command = shutil.which("cloudflared")
    if command:
        return command
    raise FileNotFoundError(
        "cloudflared non trovato: metti cloudflared.exe nella cartella del "
        "progetto oppure imposta CLOUDFLARED_PATH"
    )


def _wait_until_reachable(url: str) -> None:
    deadline = time.monotonic() + 25
    last_error = ""
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"User-Agent": "SocialEng/1.0"})
            with urlopen(request, timeout=4) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.75)
    raise RuntimeError(f"Tunnel creato ma non raggiungibile: {last_error}")
