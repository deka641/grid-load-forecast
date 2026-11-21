"""Resource limits and run time limits in the deployment files."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_LIMIT = "timeout -k 1m 3h gridload run"


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def services(text: str) -> dict[str, str]:
    """The block of each service under ``services:`` (two-space indented keys)."""
    body = text.split("\nservices:\n", 1)[1]
    body = re.split(r"\n(?=\S)", body, maxsplit=1)[0]
    parts = re.split(r"^  (\w+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_every_service_has_memory_and_cpu_limits() -> None:
    blocks = services(read("compose.yaml"))
    assert set(blocks) == {"pipeline", "api", "web"}
    for name, block in blocks.items():
        cpus = re.search(r"^    cpus: ([\d.]+)$", block, re.MULTILINE)
        assert cpus is not None, name
        assert float(cpus.group(1)) >= 1, name
        mem = re.search(r"^    mem_limit: (\S+)$", block, re.MULTILINE)
        swap = re.search(r"^    memswap_limit: (\S+)$", block, re.MULTILINE)
        assert mem is not None, name
        assert swap is not None, name
        assert mem.group(1) == swap.group(1), name


def test_the_check_stack_keeps_the_limits_of_api_and_web() -> None:
    blocks = services(read("compose.check.yaml"))
    for name in ("api", "web"):
        assert "cpus:" not in blocks[name]
        assert "mem_limit:" not in blocks[name]
    assert "cpus:" in blocks["pipeline"]


def test_web_connections_are_bounded() -> None:
    web = services(read("compose.yaml"))["web"]
    assert "nofile:\n        soft: 4096\n        hard: 4096" in web
    assert "mem_limit: 256m" in web
    caddyfile = read("docker/Caddyfile")
    assert re.search(r"read_header\s+10s", caddyfile)
    assert re.search(r"idle\s+60s", caddyfile)
    pools = [int(n) for n in re.findall(r"max_conns_per_host\s+(\d+)", caddyfile)]
    assert pools == [16, 16, 192]
    limit = re.search(r'"--limit-concurrency", "(\d+)"', read("compose.yaml"))
    assert limit is not None
    # Caddy never opens enough connections for uvicorn to refuse /health and /status.
    assert sum(pools) < int(limit.group(1))


def test_every_pipeline_run_has_a_time_limit() -> None:
    pipeline = services(read("compose.yaml"))["pipeline"]
    assert f'"{RUN_LIMIT}; exec supercronic /app/crontab"' in pipeline
    jobs = [line for line in read("docker/crontab").splitlines() if line and line[0] != "#"]
    assert jobs == [f"5 * * * * {RUN_LIMIT}"]
