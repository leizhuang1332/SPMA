"""Git log 需求关联提取——从 commit message 中匹配 REQ-XXXXX。"""

import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)
REQ_PATTERN = re.compile(r'REQ-\d{3,5}', re.IGNORECASE)


async def extract_req_links(repo_path: str) -> dict[str, list[str]]:
    req_links: dict[str, list[str]] = {}
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "log", "--name-only", "--oneline", "-n", "500",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
    current_req = None
    for line in lines:
        match = REQ_PATTERN.search(line)
        if match:
            current_req = match.group(0).upper()
            if current_req not in req_links:
                req_links[current_req] = []
        elif current_req and line.strip() and not re.match(r'^[a-f0-9]{7,}', line.strip()):
            file_path = line.strip()
            if file_path not in req_links[current_req]:
                req_links[current_req].append(file_path)
    logger.info(f"提取需求关联: {repo_path} -> {len(req_links)} REQs")
    return req_links


async def get_files_for_req(repo_path: str, req_id: str) -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_path, "log", "--grep", req_id, "--name-only", "--oneline",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
    files = [l.strip() for l in lines if l.strip() and not re.match(r'^[a-f0-9]{7,}', l.strip())]
    return list(set(files))
