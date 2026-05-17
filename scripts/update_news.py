#!/usr/bin/env python3
"""Aggregate updates from multiple AI news sites and produce 24h snapshot data."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parseaddr
import hashlib
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from scripts.ai_relevance import add_ai_relevance_fields, score_ai_relevance
except ModuleNotFoundError:  # pragma: no cover - direct `python scripts/update_news.py`
    from ai_relevance import add_ai_relevance_fields, score_ai_relevance

try:
    import feedparser
except ModuleNotFoundError:
    feedparser = None

UTC = timezone.utc
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SH_TZ = ZoneInfo("Asia/Shanghai")
WAYTOAGI_DEFAULT = (
    "https://waytoagi.feishu.cn/wiki/QPe5w5g7UisbEkkow8XcDmOpn8e?fromScene=spaceOverview"
)
WAYTOAGI_HISTORY_FALLBACK = "https://waytoagi.feishu.cn/wiki/FjiOwWp2giA7hRk6jjfcPioCnAc"

RSS_FEED_REPLACEMENTS: dict[str, str] = {
    "https://rsshub.app/infoq/recommend": "https://www.infoq.cn/feed",
    "https://rsshub.app/huggingface/blog-zh": "https://huggingface.co/blog/feed.xml",
    "https://rsshub.app/readhub/daily": "https://readhub.cn/rss",
    "https://rsshub.app/36kr/hot-list": "https://36kr.com/feed",
    "https://rsshub.app/sspai/index": "https://sspai.com/feed",
    "https://rsshub.app/sspai/matrix": "https://sspai.com/feed",
    "https://rsshub.app/meituan/tech": "https://tech.meituan.com/feed",
    "https://mjg59.dreamwidth.org/data/rss": "http://mjg59.dreamwidth.org/data/rss",
}

RSS_FEED_SKIP_PREFIXES: tuple[str, ...] = (
    "https://rsshub.app/telegram/channel/",
    "https://rsshub.app/jike/",
    "https://rsshub.app/bilibili/",
    "https://rsshub.app/zhihu/",
    "https://rsshub.app/xiaoyuzhou/podcast/",
    "https://rsshub.app/xyzrank",
    "https://rsshub.app/mittrchina/hot",
    "https://wechat2rss.bestblogs.dev/",
    "https://werss.bestblogs.dev/",
    "http://47.122.94.119:18080/",
)

RSS_FEED_SKIP_EXACT: set[str] = {
    "https://rachelbythebay.com/w/atom.xml",
    "https://flak.tedunangst.com/rss",
}

OFFICIAL_AI_FEEDS: tuple[dict[str, str], ...] = (
    {
        "title": "OpenAI News",
        "xml_url": "https://openai.com/news/rss.xml",
        "html_url": "https://openai.com/news",
    },
    {
        "title": "Google DeepMind",
        "xml_url": "https://deepmind.google/blog/rss.xml",
        "html_url": "https://deepmind.google/blog",
    },
    {
        "title": "Google AI Blog",
        "xml_url": "https://blog.google/innovation-and-ai/technology/ai/rss/",
        "html_url": "https://blog.google/innovation-and-ai/technology/ai/",
    },
    {
        "title": "Hugging Face Blog",
        "xml_url": "https://huggingface.co/blog/feed.xml",
        "html_url": "https://huggingface.co/blog",
    },
    {
        "title": "GitHub AI & ML",
        "xml_url": "https://github.blog/ai-and-ml/feed/",
        "html_url": "https://github.blog/ai-and-ml/",
    },
    {
        "title": "GitHub Changelog",
        "xml_url": "https://github.blog/changelog/feed/",
        "html_url": "https://github.blog/changelog/",
    },
    {
        "title": "OpenAI Skills",
        "xml_url": "https://github.com/openai/skills/commits/main.atom",
        "html_url": "https://github.com/openai/skills",
        "include_keywords": "hatch,pet,migrate-to-codex",
    },
)
OFFICIAL_AI_MAX_AGE_DAYS = 45
AIBREAKFAST_JINA_URL = "https://r.jina.ai/https://aibreakfast.beehiiv.com/"
AIHOT_FEED_URL = "https://aihot.virxact.com/feed.xml"
AIHOT_FALLBACK_FEED_URLS = (
    "https://aihot.virxact.com/rss.xml",
    "https://aihot.virxact.com/feed",
    "https://aihot.virxact.com/feed/daily.xml",
)
FOLLOW_BUILDERS_FEED_BASE = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main"
AGENTMAIL_API_BASE_DEFAULT = "https://api.agentmail.to"
AGENTMAIL_DIGEST_FILE = "email-digest.json"
AGENTMAIL_DEFAULT_LIMIT = 50
X_API_BASE_DEFAULT = "https://api.x.com"
X_API_POST_READ_COST_USD = 0.005
X_API_DEFAULT_QUERY = '(AI OR "artificial intelligence" OR "large language model" OR LLM) lang:en -is:retweet has:links'
X_API_DEFAULT_MAX_RESULTS = 20
X_API_MAX_QUERY_CHARS = 512

# --- GitHub Topics Radar (video/audio/music/agent AI) ---
GITHUB_TOPICS: tuple[str, ...] = (
    # Video Search & Retrieval
    "video-search", "semantic-video-search", "video-retrieval", "cross-modal-retrieval",
    # Video Understanding & Analysis
    "video-understanding", "video-analysis", "video-processing", "scene-detection",
    "shot-detection", "keyframe-detection", "video-captioning", "video-llm",
    "vision-language-model", "mllm",
    # Video Editing / Auto-editing / Editor UI
    "video-editing", "video-editor", "web-video-editor", "ai-video-editor",
    "automatic-video-editing", "automatic-editing", "video-automation",
    "text-based-video-editing", "transcript-editing", "video-clipping",
    "shorts-editor", "vertical-video", "auto-highlight",
    # Video Agent / Agentic
    "ai-agents", "llm-agents", "mcp", "model-context-protocol",
    # Audio Understanding
    "audio-understanding", "audio-analysis", "audio-feature-extraction",
    "speaker-diarization", "speech-to-text", "whisper", "whisperx",
    "audio-event-detection", "sound-event-detection",
    # Music Understanding & Analysis
    "music-information-retrieval", "music-understanding", "music-analysis",
    "beat-tracking", "onset-detection", "music-transcription",
    "drum-transcription", "source-separation", "vocal-separation",
    "chord-detection", "audio-fingerprinting", "music-similarity", "ismir",
)

GITHUB_TOPICS_MAX_AGE_DAYS = 7

# Core repos to track releases
GITHUB_CORE_REPOS: tuple[dict[str, str], ...] = (
    {"owner": "openai", "repo": "whisper", "name": "OpenAI Whisper"},
    {"owner": "FFmpeg", "repo": "FFmpeg", "name": "FFmpeg"},
    {"owner": "Zulko", "repo": "moviepy", "name": "MoviePy"},
    {"owner": "PixarAnimationStudios", "repo": "OpenTimelineIO", "name": "OpenTimelineIO"},
    {"owner": "remotion-dev", "repo": "remotion", "name": "Remotion"},
    {"owner": "m-bain", "repo": "whisperX", "name": "WhisperX"},
    {"owner": "jianfch", "repo": "stable-ts", "name": "stable-ts"},
    {"owner": "pyannote", "repo": "pyannote-audio", "name": "pyannote-audio"},
    {"owner": "librosa", "repo": "librosa", "name": "librosa"},
    {"owner": "opencv", "repo": "opencv", "name": "OpenCV"},
    {"owner": "scikit-video", "repo": "scikit-video", "name": "scikit-video"},
    {"owner": "facebookresearch", "repo": "demucs", "name": "Demucs"},
    {"owner": "haoheliu", "repo": "audiooldier", "name": "AudioLDM"},
    {"owner": "MCG-NJU", "repo": "VideoBERT", "name": "VideoBERT"},
    {"owner": "facebookresearch", "repo": "ImageBind", "name": "ImageBind"},
    {"owner": "lucidrains", "repo": "x-transformers", "name": "x-transformers"},
    {"owner": "huggingface", "repo": "transformers", "name": "HuggingFace Transformers"},
    {"owner": "PKU-YuanGroup", "repo": "Video-LLaVA", "name": "Video-LLaVA"},
    {"owner": "OpenGVLab", "repo": "InternVideo", "name": "InternVideo"},
    {"owner": "showlab", "repo": "VideoChat", "name": "VideoChat"},
    {"owner": "auto-editor", "repo": "auto-editor", "name": "auto-editor"},
    {"owner": "yt-dlp", "repo": "yt-dlp", "name": "yt-dlp"},
    {"owner": "StreamBLEND", "repo": "StreamBLEND", "name": "StreamBLEND"},
    {"owner": "jianchang512", "repo": "pyvideotrans", "name": "pyvideotrans"},
    {"owner": "xinntao", "repo": "Real-ESRGAN", "name": "Real-ESRGAN"},
)

GITHUB_RELEASES_MAX_AGE_DAYS = 30

# arXiv categories for video/audio/music AI
ARXIV_CATEGORIES: tuple[dict[str, str], ...] = (
    {"id": "cs.CV", "name": "Computer Vision", "url": "https://rss.arxiv.org/rss/cs.CV"},
    {"id": "cs.MM", "name": "Multimedia", "url": "https://rss.arxiv.org/rss/cs.MM"},
    {"id": "cs.SD", "name": "Sound", "url": "https://rss.arxiv.org/rss/cs.SD"},
    {"id": "eess.AS", "name": "Audio & Speech", "url": "https://rss.arxiv.org/rss/eess.AS"},
)

ARXIV_MAX_AGE_DAYS = 7

HF_DAILY_PAPERS_MAX_AGE_DAYS = 7

HF_MLX_COMMUNITY_MAX_AGE_DAYS = 7
HF_MLX_COMMUNITY_LIMIT = 50

REDDIT_SUBREDDITS: tuple[str, ...] = (
    "LocalLLaMA",
    "MachineLearning",
)
REDDIT_KEYWORDS: tuple[str, ...] = (
    "video editing", "video understanding", "video search", "video analysis",
    "audio understanding", "audio analysis", "music generation", "music understanding",
    "speech recognition", "whisper", "text to video", "video generation",
    "video llm", "video agent", "auto editing", "video clipping",
    "speaker diarization", "source separation", "beat tracking",
    "video retrieval", "multimodal", "vision language model", "vlm",
    "edge device", "edge inference", "on-device", "mlx",
    "mcp server", "ai agent", "llm agent", "edit agent",
)
REDDIT_MAX_AGE_DAYS = 3

HF_SPACES_TRENDING_LIMIT = 50
HF_SPACES_MAX_AGE_DAYS = 7

FINDIT_ORG = "Findit-AI"
FINDIT_MAX_AGE_DAYS = 30

# HN keyword filter for video/audio/agent AI topics
HN_KEYWORDS: tuple[str, ...] = (
    "video editing", "video understanding", "video search", "video analysis",
    "audio understanding", "audio analysis", "music generation", "speech recognition",
    "whisper", "text to video", "video generation", "video llm", "video agent",
    "auto editing", "video clipping", "speaker diarization", "source separation",
    "beat tracking", "video retrieval", "multimodal", "vision language",
    "mcp server", "ai agent", "llm agent",
)

HN_ALGOLIA_API = "https://hn.algolia.com/api/v1/search"
HN_MAX_AGE_DAYS = 3


@dataclass
class RawItem:
    site_id: str
    site_name: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    meta: dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt = dtparser.parse(dt_str)
    except Exception:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def normalize_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url.strip())
        if not parsed.scheme:
            return raw_url.strip()
        query = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_"):
                continue
            if lk in {
                "ref",
                "spm",
                "fbclid",
                "gclid",
                "igshid",
                "mkt_tok",
                "mc_cid",
                "mc_eid",
                "_hsenc",
                "_hsmi",
            }:
                continue
            query.append((k, v))
        parsed = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
            query=urlencode(query, doseq=True),
        )
        normalized = urlunparse(parsed)
        return normalized.rstrip("/")
    except Exception:
        return raw_url.strip()


def host_of_url(raw_url: str) -> str:
    try:
        return urlparse(raw_url).netloc.lower()
    except Exception:
        return ""


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return ""


def maybe_fix_mojibake(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    # Common mojibake signature from UTF-8 bytes decoded as Latin-1.
    if re.search(r"[Ãâåèæïð]|[\x80-\x9f]|æ|ç|å|é", s) is None:
        return s
    for enc in ("latin1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("utf-8")
            if fixed and fixed != s:
                return fixed
        except Exception:
            continue
    return s


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_mostly_english(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if has_cjk(s):
        return False
    letters = re.findall(r"[A-Za-z]", s)
    return len(letters) >= max(6, len(s) // 4)


def parse_feed_entries_via_xml(feed_xml: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        root = ET.fromstring(feed_xml)
    except Exception:
        return out

    for tag in (".//item", ".//{*}item", ".//entry", ".//{*}entry"):
        for node in root.findall(tag):
            title = (
                node.findtext("title")
                or node.findtext("{*}title")
                or ""
            ).strip()
            link = ""
            link_node = node.find("link")
            if link_node is None:
                link_node = node.find("{*}link")
            if link_node is not None:
                link = (link_node.get("href") or link_node.text or "").strip()
            if not link:
                link = (node.findtext("{*}link") or node.findtext("link") or "").strip()
            published = (
                node.findtext("pubDate")
                or node.findtext("{*}pubDate")
                or node.findtext("published")
                or node.findtext("{*}published")
                or node.findtext("updated")
                or node.findtext("{*}updated")
            )
            if title and link:
                key = (title, link)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "link": link, "published": published})
    return out


def make_item_id(site_id: str, source: str, title: str, url: str) -> str:
    key = "||".join(
        [
            site_id.strip().lower(),
            source.strip().lower(),
            title.strip().lower(),
            normalize_url(url),
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def parse_unix_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        n = float(value)
    except Exception:
        return None
    if n > 10_000_000_000:
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except Exception:
        return None


def parse_relative_time_zh(text: str, now: datetime) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None

    m = re.search(r"(\d+)\s*分钟前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    m = re.search(r"(\d+)\s*小时前", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    m = re.search(r"(\d+)\s*天前", text)
    if m:
        return now - timedelta(days=int(m.group(1)))

    if "刚刚" in text:
        return now

    if "昨天" in text:
        return now - timedelta(days=1)

    m = re.fullmatch(r"(?:今天)?\s*(\d{1,2}):(\d{2})", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now + timedelta(minutes=5):
            candidate -= timedelta(days=1)
        return candidate

    m = re.fullmatch(r"昨天\s*(\d{1,2}):(\d{2})", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        return (now - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    m = re.fullmatch(r"(?:\d{4}年\s*)?(\d{1,2})月(\d{1,2})日", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = now.year
        try:
            candidate = datetime(year, month, day, tzinfo=UTC)
            if candidate > now + timedelta(days=2):
                candidate = datetime(year - 1, month, day, tzinfo=UTC)
            return candidate
        except Exception:
            return None

    return None


def parse_date_any(value: Any, now: datetime) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.astimezone(UTC)

    if isinstance(value, (int, float)):
        return parse_unix_timestamp(value)

    s = str(value).strip()
    if not s:
        return None

    if s.startswith("$D"):
        s = s[2:]

    if re.fullmatch(r"\d{12,}", s):
        return parse_unix_timestamp(int(s))

    if re.fullmatch(r"\d{9,11}", s):
        return parse_unix_timestamp(int(s))

    dt = parse_relative_time_zh(s, now)
    if dt:
        return dt

    # TechURLs format: 2026-02-19 11:54:21AM UTC
    m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}[AP]M)\s+UTC", s)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %I:%M:%S%p")
            return dt.replace(tzinfo=UTC)
        except Exception:
            pass

    try:
        dt = dtparser.parse(s, tzinfos={"UT": 0, "UTC": 0, "GMT": 0})
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def decode_escaped_json(raw: str) -> dict[str, Any] | None:
    s = raw.replace('\\"', '"').replace("\\/", "/")
    try:
        return json.loads(s)
    except Exception:
        return None


def extract_waytoagi_history_url(root_html: str) -> str:
    pattern = r'\{\\"id\\":\\"[^\"]+\\",\\"type\\":\\"mention_doc\\",\\"data\\":\{[^\}]+\}\}'
    for raw in re.findall(pattern, root_html):
        obj = decode_escaped_json(raw)
        if not obj:
            continue
        data = obj.get("data", {})
        title = str(data.get("title") or "")
        if "历史更新" in title or "更新日志" in title:
            raw_url = str(data.get("raw_url") or "").strip()
            if raw_url:
                return raw_url
    return WAYTOAGI_HISTORY_FALLBACK


def extract_feishu_client_vars(page_html: str) -> dict[str, Any]:
    marker = "window.DATA = Object.assign({}, window.DATA, { clientVars: Object("
    idx = page_html.find(marker)
    if idx == -1:
        raise ValueError("Cannot locate Feishu clientVars marker")

    start = idx + len(marker)
    depth = 1
    in_str = False
    escaped = False
    end = None

    for i, ch in enumerate(page_html[start:], start):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end is None:
        raise ValueError("Cannot parse Feishu clientVars payload")

    payload = page_html[start:end]
    return json.loads(payload)


def block_text(block_data: dict[str, Any]) -> str:
    text_obj = block_data.get("text", {}) if isinstance(block_data, dict) else {}
    initial = text_obj.get("initialAttributedTexts", {}).get("text", {}) if isinstance(text_obj, dict) else {}
    if not isinstance(initial, dict):
        return ""

    def key_int(k: Any) -> int:
        try:
            return int(k)
        except Exception:
            return 0

    return "".join(str(v) for k, v in sorted(initial.items(), key=lambda kv: key_int(kv[0]))).strip()


def clean_update_title(text: str) -> str:
    text = text.replace("《 》", "").replace("《》", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_ym_heading(text: str) -> tuple[int, int] | None:
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_md_heading(text: str) -> tuple[int, int] | None:
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def infer_shanghai_year_for_month_day(now_sh: datetime, month: int, day: int) -> int | None:
    year = now_sh.year
    try:
        candidate = date(year, month, day)
    except Exception:
        return None
    if candidate > (now_sh.date() + timedelta(days=2)):
        year -= 1
    return year


def extract_waytoagi_recent_updates_from_block_map(
    block_map: dict[str, Any],
    now_sh: datetime,
    page_url: str,
) -> list[dict[str, Any]]:
    if not isinstance(block_map, dict) or not block_map:
        return []

    ym_by_heading2: dict[str, tuple[int, int]] = {}
    near_log_parent_ids: set[str] = set()

    for bid, block in block_map.items():
        bd = block.get("data", {})
        btype = bd.get("type")
        if btype not in {"heading1", "heading2", "heading3"}:
            continue
        heading_text = block_text(bd)
        if "近7日更新日志" in heading_text or "近 7 日更新日志" in heading_text:
            parent_id = str(bd.get("parent_id") or "").strip()
            if parent_id:
                near_log_parent_ids.add(parent_id)

    heading3_dates: dict[str, date] = {}

    for bid, block in block_map.items():
        bd = block.get("data", {})
        if bd.get("type") != "heading2":
            continue
        ym = parse_ym_heading(block_text(bd))
        if ym:
            ym_by_heading2[bid] = ym

    for bid, block in block_map.items():
        bd = block.get("data", {})
        if bd.get("type") != "heading3":
            continue
        md = parse_md_heading(block_text(bd))
        if not md:
            continue
        month, day = md
        parent = bd.get("parent_id")
        if near_log_parent_ids and parent not in near_log_parent_ids:
            continue
        year = ym_by_heading2.get(parent, (now_sh.year, month))[0]
        inferred = infer_shanghai_year_for_month_day(now_sh, month, day)
        if inferred is not None:
            year = inferred
        try:
            heading3_dates[bid] = date(year, month, day)
        except Exception:
            continue

    parent_map: dict[str, str] = {}
    for bid, block in block_map.items():
        bd = block.get("data", {})
        parent = str(bd.get("parent_id") or "").strip()
        if parent:
            parent_map[bid] = parent

    def nearest_heading_date(block_id: str) -> date | None:
        cur = parent_map.get(block_id)
        hops = 0
        while cur and hops < 20:
            if cur in heading3_dates:
                return heading3_dates[cur]
            cur = parent_map.get(cur)
            hops += 1
        return None

    updates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for bid, block in block_map.items():
        bd = block.get("data", {})
        if bd.get("type") not in {"bullet", "text", "todo", "ordered"}:
            continue

        day = nearest_heading_date(bid)
        if not day:
            continue
        title = clean_update_title(block_text(bd))
        if not title:
            continue
        key = (day.isoformat(), title)
        if key in seen:
            continue
        seen.add(key)
        updates.append({"date": day.isoformat(), "title": title, "url": page_url})

    return updates


def fetch_waytoagi_recent_7d(session: requests.Session, now_utc: datetime, root_url: str) -> dict[str, Any]:
    now_sh = now_utc.astimezone(SH_TZ)
    root_html = session.get(root_url, timeout=30).text
    history_url = extract_waytoagi_history_url(root_html)

    root_client_vars = extract_feishu_client_vars(root_html)
    root_block_map = root_client_vars.get("data", {}).get("block_map", {})
    updates: list[dict[str, Any]] = extract_waytoagi_recent_updates_from_block_map(root_block_map, now_sh, root_url)

    if history_url and history_url != root_url:
        try:
            history_html = session.get(history_url, timeout=30).text
            history_client_vars = extract_feishu_client_vars(history_html)
            history_block_map = history_client_vars.get("data", {}).get("block_map", {})
            updates.extend(
                extract_waytoagi_recent_updates_from_block_map(history_block_map, now_sh, history_url)
            )
        except Exception:
            pass

    dedup_updates: dict[tuple[str, str], dict[str, Any]] = {}
    for item in updates:
        key = (str(item.get("date") or ""), str(item.get("title") or ""))
        if key[0] and key[1] and key not in dedup_updates:
            dedup_updates[key] = item

    start_date = now_sh.date() - timedelta(days=6)
    end_date = now_sh.date()
    recent = [
        u
        for u in dedup_updates.values()
        if start_date <= date.fromisoformat(str(u.get("date") or "1970-01-01")) <= end_date
    ]
    recent.sort(key=lambda x: (x["date"], x["title"]), reverse=True)
    latest_date = recent[0]["date"] if recent else None
    updates_today = [u for u in recent if u.get("date") == latest_date] if latest_date else []

    warning = "近7日未解析到更新条目" if not recent else None
    return {
        "generated_at": iso(now_utc),
        "timezone": "Asia/Shanghai",
        "root_url": root_url,
        "history_url": history_url,
        "window_days": 7,
        "latest_date": latest_date,
        "count_today": len(updates_today),
        "updates_today": updates_today,
        "count_7d": len(recent),
        "updates_7d": recent,
        "warning": warning,
        "has_error": False,
        "error": None,
    }


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return session


def extract_next_f_merged(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    if not chunks:
        return ""
    merged = "".join(chunks)
    try:
        return bytes(merged, "utf-8").decode("unicode_escape")
    except Exception:
        return merged


def extract_balanced_json(decoded: str, key: str) -> Any:
    idx = decoded.find(key)
    if idx == -1:
        raise ValueError(f"Key not found: {key}")

    start = idx + len(key)
    while start < len(decoded) and decoded[start] != ":":
        start += 1
    start += 1
    while start < len(decoded) and decoded[start] not in "[{":
        start += 1

    open_ch = decoded[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    end = None

    for i, ch in enumerate(decoded[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

    if end is None:
        raise ValueError(f"Cannot parse JSON block for key: {key}")

    snippet = decoded[start:end]
    snippet = snippet.replace("$undefined", "null")
    snippet = re.sub(r'"\$D([^\"]+)"', r'"\1"', snippet)
    return json.loads(snippet)


def extract_next_data_payload(html: str) -> dict[str, Any] | None:
    m = re.search(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
        html,
        re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def parse_anthropic_news_items(page_html: str, now: datetime) -> list[RawItem]:
    site_id = "official_ai"
    site_name = "Official AI Updates"
    soup = BeautifulSoup(page_html, "html.parser")
    out: list[RawItem] = []
    seen: set[str] = set()

    for a in soup.select('a[href^="/news/"]'):
        href = str(a.get("href") or "").strip()
        if not href or href == "/news/" or href == "/news":
            continue

        title_tag = a.select_one("h1, h2, h3, h4")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        title = maybe_fix_mojibake(title)
        if not title or title.lower() == "news":
            continue

        url = urljoin("https://www.anthropic.com", href)
        if url in seen:
            continue
        seen.add(url)

        time_tag = a.select_one("time")
        published = None
        if time_tag:
            published = parse_date_any(time_tag.get("datetime") or time_tag.get_text(" ", strip=True), now)
        if not published:
            continue
        if now and published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="Anthropic News",
                title=title,
                url=url,
                published_at=published,
                meta={"provider": "Anthropic"},
            )
        )

    return out


def parse_openai_codex_changelog_items(page_html: str, now: datetime) -> list[RawItem]:
    site_id = "official_ai"
    site_name = "Official AI Updates"
    soup = BeautifulSoup(page_html, "html.parser")
    out: list[RawItem] = []
    seen: set[str] = set()

    for node in soup.select("#codex-changelog-content li[id], li[id]"):
        item_id = str(node.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue

        time_tag = node.select_one("time")
        title_tag = node.select_one("h3")
        if not time_tag or not title_tag:
            continue

        title = maybe_fix_mojibake(title_tag.get_text(" ", strip=True))
        published = parse_date_any(time_tag.get("datetime") or time_tag.get_text(" ", strip=True), now)
        if not title or not published:
            continue
        if now and published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        seen.add(item_id)
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="OpenAI Codex Changelog",
                title=title,
                url=f"https://developers.openai.com/codex/changelog#{item_id}",
                published_at=published,
                meta={"provider": "OpenAI"},
            )
        )

    return out


def fetch_feed_as_official_items(
    session: requests.Session,
    feed: dict[str, str],
    now: datetime,
) -> list[RawItem]:
    site_id = "official_ai"
    site_name = "Official AI Updates"
    feed_url = feed["xml_url"]
    feed_title = feed["title"]

    resp = session.get(
        feed_url,
        timeout=20,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    resp.raise_for_status()

    entries: list[dict[str, Any]]
    if feedparser is not None:
        parsed = feedparser.parse(resp.content)
        entries = list(parsed.entries)
    else:
        entries = parse_feed_entries_via_xml(resp.content)

    out: list[RawItem] = []
    include_keywords = [
        keyword.strip().lower()
        for keyword in str(feed.get("include_keywords") or "").split(",")
        if keyword.strip()
    ]
    for entry in entries:
        title = str(entry.get("title", "")).strip()
        link = str(entry.get("link", "")).strip()
        if not title or not link:
            continue
        if include_keywords:
            haystack = f"{title} {link}".lower()
            if not any(keyword in haystack for keyword in include_keywords):
                continue
        published = (
            parse_date_any(entry.get("published"), now)
            or parse_date_any(entry.get("updated"), now)
            or parse_date_any(entry.get("pubDate"), now)
        )
        if not published:
            continue
        if published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=feed_title,
                title=maybe_fix_mojibake(title),
                url=link,
                published_at=published,
                meta={
                    "feed_url": feed_url,
                    "feed_home": feed.get("html_url") or "",
                },
            )
        )

    return out


def fetch_official_ai_updates(session: requests.Session, now: datetime) -> list[RawItem]:
    out: list[RawItem] = []

    for feed in OFFICIAL_AI_FEEDS:
        try:
            out.extend(fetch_feed_as_official_items(session, feed, now))
        except Exception:
            continue

    try:
        r = session.get("https://www.anthropic.com/news", timeout=20)
        r.raise_for_status()
        out.extend(parse_anthropic_news_items(r.text, now))
    except Exception:
        pass

    try:
        r = session.get("https://developers.openai.com/codex/changelog", timeout=20)
        r.raise_for_status()
        out.extend(parse_openai_codex_changelog_items(r.text, now))
    except Exception:
        pass

    if not out:
        raise ValueError("No official AI update sources returned items")

    return out


def parse_ai_breakfast_items(markdown_text: str, now: datetime) -> list[RawItem]:
    site_id = "aibreakfast"
    site_name = "AI Breakfast"
    out: list[RawItem] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+•\s+\d+\s+min read\s+###\s+\*\*(.*?)\*\*.*?"
        r"\]\((https?://aibreakfast\.beehiiv\.com/p/[^)]+)\)",
        re.S,
    )

    for date_text, title_text, url in pattern.findall(markdown_text or ""):
        url = url.strip()
        if not url or url in seen:
            continue
        published = parse_date_any(date_text, now)
        if not published:
            continue
        if now and published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        seen.add(url)
        title = re.sub(r"\s+", " ", title_text).strip()
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="AI Breakfast",
                title=maybe_fix_mojibake(title),
                url=url,
                published_at=published,
                meta={"feed_home": "https://aibreakfast.beehiiv.com/"},
            )
        )

    return out


def fetch_ai_breakfast(session: requests.Session, now: datetime) -> list[RawItem]:
    resp = session.get(
        AIBREAKFAST_JINA_URL,
        timeout=25,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/plain, */*",
        },
    )
    resp.raise_for_status()
    out = parse_ai_breakfast_items(resp.text, now)
    if not out:
        raise ValueError("No AI Breakfast items parsed")
    return out


def parse_follow_builders_items(feeds: dict[str, dict[str, Any]], now: datetime) -> list[RawItem]:
    site_id = "followbuilders"
    site_name = "Follow Builders"
    out: list[RawItem] = []

    for builder in feeds.get("x", {}).get("x", []) or []:
        name = str(builder.get("name") or builder.get("handle") or "").strip()
        handle = str(builder.get("handle") or "").strip()
        source = f"Follow Builders · X · {name or handle}".strip(" ·")
        for tweet in builder.get("tweets", []) or []:
            text = str(tweet.get("text") or "").strip()
            url = str(tweet.get("url") or "").strip()
            published = parse_date_any(tweet.get("createdAt"), now)
            if not text or not url or not published:
                continue
            title = re.sub(r"\s+", " ", text)
            if len(title) > 220:
                title = title[:217].rstrip() + "..."
            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=source,
                    title=maybe_fix_mojibake(title),
                    url=url,
                    published_at=published,
                    meta={"handle": handle, "feed": "feed-x.json"},
                )
            )

    for article in feeds.get("blogs", {}).get("blogs", []) or []:
        title = str(article.get("title") or "").strip()
        url = str(article.get("url") or "").strip()
        published = parse_date_any(article.get("publishedAt"), now) or parse_date_any(
            feeds.get("blogs", {}).get("generatedAt"), now
        )
        if not title or not url or not published:
            continue
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"Follow Builders · Blog · {article.get('name') or 'Blog'}",
                title=maybe_fix_mojibake(title),
                url=url,
                published_at=published,
                meta={"feed": "feed-blogs.json"},
            )
        )

    for episode in feeds.get("podcasts", {}).get("podcasts", []) or []:
        title = str(episode.get("title") or "").strip()
        url = str(episode.get("url") or "").strip()
        published = parse_date_any(episode.get("publishedAt"), now) or parse_date_any(
            feeds.get("podcasts", {}).get("generatedAt"), now
        )
        if not title or not url or not published:
            continue
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"Follow Builders · Podcast · {episode.get('name') or 'Podcast'}",
                title=maybe_fix_mojibake(title),
                url=url,
                published_at=published,
                meta={"feed": "feed-podcasts.json"},
            )
        )

    return out


def fetch_follow_builders(session: requests.Session, now: datetime) -> list[RawItem]:
    feeds: dict[str, dict[str, Any]] = {}
    for key, filename in (
        ("x", "feed-x.json"),
        ("blogs", "feed-blogs.json"),
        ("podcasts", "feed-podcasts.json"),
    ):
        resp = session.get(
            f"{FOLLOW_BUILDERS_FEED_BASE}/{filename}",
            timeout=20,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json, */*",
            },
        )
        resp.raise_for_status()
        feeds[key] = resp.json()

    out = parse_follow_builders_items(feeds, now)
    if not out:
        raise ValueError("No Follow Builders items parsed")
    return out


def is_hubtoday_placeholder_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if "详情见官方介绍" in t:
        return True
    return t in {"原文链接", "查看详情", "点击查看", "详情"}


def is_hubtoday_generic_anchor_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if is_hubtoday_placeholder_title(t):
        return True
    return bool(re.search(r"\(AI资讯\)\s*$", t))


def normalize_aihubtoday_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, list[dict[str, Any]]] = {}
    keep: list[dict[str, Any]] = []

    for item in items:
        if str(item.get("site_id") or "") != "aihubtoday":
            keep.append(item)
            continue
        url = normalize_url(str(item.get("url") or ""))
        if not url:
            continue
        by_url.setdefault(url, []).append(item)

    for group in by_url.values():
        if not group:
            continue
        preferred = [g for g in group if not is_hubtoday_generic_anchor_title(str(g.get("title") or ""))]
        source = preferred if preferred else group
        best = max(
            source,
            key=lambda x: (
                event_time(x) or datetime.min.replace(tzinfo=UTC),
                str(x.get("id") or ""),
            ),
        )
        keep.append(best)

    keep.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return keep


def fetch_ai_hubtoday(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "aihubtoday"
    site_name = "AI HubToday"

    r = session.get("https://ai.hubtoday.app/", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    issue_date = None
    text = soup.get_text(" ", strip=True)
    m = re.search(r"AI资讯日报\s*(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if not m:
        m = re.search(r"AI资讯日报\s*(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        issue_date = datetime(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            tzinfo=UTC,
        )

    out: list[RawItem] = []
    seen_urls: set[str] = set()

    def add_item(title: str, href: str, source: str = "Daily Digest", fallback_title: str | None = None) -> None:
        title = (title or "").strip()
        href = (href or "").strip()
        fallback_title = (fallback_title or "").strip()
        if is_hubtoday_generic_anchor_title(title) and fallback_title:
            title = fallback_title
        if len(title) < 5 or not href.startswith("http"):
            return
        if title in {"自媒体账号"} or "source.hubtoday.app" in href or is_hubtoday_generic_anchor_title(title):
            return
        key_url = normalize_url(href)
        if key_url in seen_urls:
            return
        seen_urls.add(key_url)
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=source,
                title=title,
                url=href,
                published_at=issue_date,
                meta={},
            )
        )

    for p in soup.select("article .content li p"):
        link = p.select_one("a[href^='http']")
        if not link:
            continue
        strong = p.find("strong")
        strong_title = strong.get_text(" ", strip=True) if strong else ""
        add_item(strong_title, link.get("href") or "", source="Daily Digest")

    for a in soup.select("article .content a[target='_blank']"):
        fallback_title = ""
        p = a.find_parent("p")
        if p:
            strong = p.find("strong")
            if strong:
                fallback_title = strong.get_text(" ", strip=True)
        add_item(a.get_text(" ", strip=True), a.get("href") or "", fallback_title=fallback_title)

    # include article-level links without target='_blank' (e.g. GitHub 链接)
    for a in soup.select("article a[href^='http']"):
        fallback_title = ""
        p = a.find_parent("p")
        if p:
            strong = p.find("strong")
            if strong:
                fallback_title = strong.get_text(" ", strip=True)
        add_item(a.get_text(" ", strip=True), a.get("href") or "", fallback_title=fallback_title)

    if not out:
        # fallback: parse all external links in page when article container changes
        for a in soup.select("a[href^='http']"):
            fallback_title = ""
            p = a.find_parent("p")
            if p:
                strong = p.find("strong")
                if strong:
                    fallback_title = strong.get_text(" ", strip=True)
            add_item(
                a.get_text(" ", strip=True),
                a.get("href") or "",
                source="Page Fallback",
                fallback_title=fallback_title,
            )

    return out


def fetch_aibase(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "aibase"
    site_name = "AIbase"

    r = session.get("https://www.aibase.com/zh/news", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    out: list[RawItem] = []
    for a in soup.select("a[href^='/news/']"):
        h3 = a.select_one("h3")
        if not h3:
            continue
        title = h3.get_text(" ", strip=True)
        href = a.get("href", "").strip()
        if not title or not href:
            continue

        time_text = ""
        time_tag = a.select_one("div.text-sm.text-gray-400 span")
        if time_tag:
            time_text = time_tag.get_text(" ", strip=True)

        published = parse_date_any(time_text, now)
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=site_name,
                title=title,
                url=urljoin("https://www.aibase.com", href),
                published_at=published,
                meta={"time_hint": time_text},
            )
        )

    return out


def parse_aihot_feed_items(feed_content: bytes, now: datetime, feed_url: str = AIHOT_FEED_URL) -> list[RawItem]:
    site_id = "aihot"
    site_name = "AI HOT"
    source_name = site_name
    if feedparser is not None:
        parsed = feedparser.parse(feed_content)
        entries = list(parsed.entries)
        source_name = first_non_empty(getattr(parsed, "feed", {}).get("title"), site_name)
    else:
        entries = parse_feed_entries_via_xml(feed_content)

    out: list[RawItem] = []
    seen_urls: set[str] = set()
    for entry in entries:
        title = maybe_fix_mojibake(str(entry.get("title") or "").strip())
        link = str(entry.get("link") or "").strip()
        if not title or not link:
            continue
        normalized_url = normalize_url(link)
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        published = (
            parse_date_any(entry.get("published"), now)
            or parse_date_any(entry.get("updated"), now)
            or parse_date_any(entry.get("pubDate"), now)
        )
        if not published:
            continue
        author_detail = entry.get("author_detail") or {}
        entry_source = first_non_empty(
            author_detail.get("name") if isinstance(author_detail, dict) else "",
            entry.get("author"),
            source_name,
        )
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=maybe_fix_mojibake(entry_source),
                title=title,
                url=link,
                published_at=published,
                meta={"feed_url": feed_url},
            )
        )

    return out


def fetch_aihot(session: requests.Session, now: datetime) -> list[RawItem]:
    last_error: Exception | None = None
    for feed_url in (AIHOT_FEED_URL, *AIHOT_FALLBACK_FEED_URLS):
        try:
            r = session.get(
                feed_url,
                timeout=30,
                headers={
                    "User-Agent": BROWSER_UA,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                },
            )
            r.raise_for_status()
            items = parse_aihot_feed_items(r.content, now, feed_url=feed_url)
            if items:
                return items
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return []


# ---------------------------------------------------------------------------
# GitHub Topics Radar fetchers
# ---------------------------------------------------------------------------

def fetch_github_topics(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch new repos tagged with video/audio/music/agent AI topics via GitHub Search API."""
    site_id = "github_topics"
    site_name = "GitHub Topics"
    out: list[RawItem] = []
    cutoff_date = (now - timedelta(days=GITHUB_TOPICS_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    seen_repos: set[str] = set()
    batch_size = 8  # topics per query (comma-separated = OR)

    topics = list(GITHUB_TOPICS)
    batches: list[str] = []
    for i in range(0, len(topics), batch_size):
        batch = topics[i : i + batch_size]
        batches.append(",".join(batch))

    api_headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/vnd.github.v3+json",
    }

    for batch_query in batches:
        q = f"topic:{batch_query} created:>{cutoff_date}"
        try:
            resp = session.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "created", "order": "desc", "per_page": 30},
                headers=api_headers,
                timeout=20,
            )
            # Handle rate limit gracefully
            if resp.status_code == 403:
                time.sleep(10)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue

        for repo in data.get("items", []):
            full_name = repo.get("full_name", "")
            if full_name in seen_repos:
                continue
            seen_repos.add(full_name)

            created_at = parse_iso(repo.get("created_at"))
            if not created_at or created_at < now - timedelta(days=GITHUB_TOPICS_MAX_AGE_DAYS):
                continue

            repo_topics = repo.get("topics", [])
            matched = [t for t in repo_topics if t in GITHUB_TOPICS]
            topic_label = matched[0] if matched else "unknown"
            desc = repo.get("description") or ""
            stars = repo.get("stargazers_count", 0)

            out.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"github:{topic_label}",
                title=f"{full_name} - {desc}" if desc else full_name,
                url=repo.get("html_url", ""),
                published_at=created_at,
                meta={"topics": matched, "stars": stars, "language": repo.get("language", "")},
            ))

        # Respect rate limit: ~1 req/sec for unauthenticated
        time.sleep(1.5)

    return out


def fetch_github_releases(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch recent releases from core video/audio/agent repos."""
    site_id = "github_releases"
    site_name = "GitHub Releases"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=GITHUB_RELEASES_MAX_AGE_DAYS)
    ua = {"User-Agent": BROWSER_UA, "Accept": "application/atom+xml, application/xml, text/xml, */*"}

    def fetch_one_repo(repo_info: dict[str, str]) -> list[RawItem]:
        owner, repo, name = repo_info["owner"], repo_info["repo"], repo_info["name"]
        url = f"https://github.com/{owner}/{repo}/releases.atom"
        try:
            resp = session.get(url, timeout=20, headers=ua)
            resp.raise_for_status()
        except Exception:
            return []
        entries = parse_feed_entries_via_xml(resp.content)
        items: list[RawItem] = []
        for entry in entries:
            title = maybe_fix_mojibake(entry.get("title", "").strip())
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            published = parse_date_any(entry.get("published"), now) or parse_date_any(entry.get("updated"), now)
            if not published:
                continue
            if published < cutoff:
                continue
            items.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=name,
                title=f"[{name}] {title}",
                url=link,
                published_at=published,
                meta={"owner": owner, "repo": repo},
            ))
        return items

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_one_repo, r): r["name"] for r in GITHUB_CORE_REPOS}
        for future in as_completed(futures):
            try:
                out.extend(future.result())
            except Exception:
                continue

    return out


def fetch_hf_daily_papers(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch daily papers from Hugging Face with AI summaries and upvotes."""
    site_id = "hf_papers"
    site_name = "HF Daily Papers"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=HF_DAILY_PAPERS_MAX_AGE_DAYS)

    resp = session.get("https://huggingface.co/api/daily_papers", timeout=30)
    resp.raise_for_status()
    papers = resp.json()

    for item in papers:
        paper = item.get("paper") or item
        paper_id = paper.get("id", "")
        title = (paper.get("title") or "").strip()
        if not title or not paper_id:
            continue

        published = parse_date_any(paper.get("publishedAt") or paper.get("submittedOnDailyAt"), now)
        if not published:
            continue
        if published < cutoff:
            continue

        arxiv_url = f"https://arxiv.org/abs/{paper_id}"
        upvotes = paper.get("upvotes", 0)
        authors = [a.get("name", "") for a in (paper.get("authors") or [])[:3]]
        gh_repo = paper.get("githubRepo") or ""

        meta: dict[str, Any] = {"paper_id": paper_id, "upvotes": upvotes}
        if authors:
            meta["authors"] = authors
        if gh_repo:
            meta["github_repo"] = gh_repo

        out.append(RawItem(
            site_id=site_id,
            site_name=site_name,
            source="Hugging Face",
            title=title,
            url=arxiv_url,
            published_at=published,
            meta=meta,
        ))

    return out


def fetch_hf_mlx_community(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch latest models from mlx-community on Hugging Face (edge-device inference)."""
    site_id = "hf_mlx"
    site_name = "mlx-community"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=HF_MLX_COMMUNITY_MAX_AGE_DAYS)

    url = (
        "https://huggingface.co/api/models"
        "?author=mlx-community&sort=lastModified&direction=-1"
        f"&limit={HF_MLX_COMMUNITY_LIMIT}"
    )
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    models = resp.json()

    for m in models:
        model_id = m.get("id") or m.get("modelId", "")
        if not model_id:
            continue

        last_modified = parse_date_any(m.get("lastModified") or m.get("lastModifiedAt"), now)
        if not last_modified:
            continue
        if last_modified < cutoff:
            continue

        tags = m.get("tags") or []
        pipeline_tag = m.get("pipeline_tag") or ""
        likes = m.get("likes", 0)
        downloads = m.get("downloads", 0)

        # Build a descriptive title
        short_name = model_id.split("/")[-1] if "/" in model_id else model_id
        title = short_name
        if pipeline_tag:
            title = f"{short_name} — {pipeline_tag}"

        hf_url = f"https://huggingface.co/{model_id}"

        meta: dict[str, Any] = {
            "model_id": model_id,
            "likes": likes,
            "downloads": downloads,
        }
        if tags:
            meta["tags"] = tags[:8]
        if pipeline_tag:
            meta["pipeline_tag"] = pipeline_tag

        out.append(RawItem(
            site_id=site_id,
            site_name=site_name,
            source="Hugging Face · MLX",
            title=title,
            url=hf_url,
            published_at=last_modified,
            meta=meta,
        ))

    return out


def fetch_reddit_ai(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch topic-filtered posts from AI/ML subreddits."""
    site_id = "reddit_ai"
    site_name = "Reddit · AI"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=REDDIT_MAX_AGE_DAYS)
    seen_urls: set[str] = set()

    headers = {"User-Agent": "ai-news-radar/1.0 (by /u/ai_news_radar)"}

    for subreddit in REDDIT_SUBREDDITS:
        # Fetch newest posts and filter client-side (more reliable than search API)
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=100"
        try:
            resp = session.get(url, timeout=30, headers=headers)
            if resp.status_code == 429:
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue

        children = (data.get("data") or {}).get("children") or []
        for child in children:
            post = child.get("data") or {}
            title = (post.get("title") or "").strip()
            selftext = (post.get("selftext") or "").strip()[:500]
            permalink = (post.get("permalink") or "").strip()
            if not title or not permalink:
                continue

            # Check if any keyword appears in title or selftext (case-insensitive)
            hay = f"{title} {selftext}".lower()
            if not any(kw in hay for kw in REDDIT_KEYWORDS):
                continue

            full_url = f"https://www.reddit.com{permalink}"
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            created = parse_unix_timestamp(post.get("created_utc"))
            if not created:
                continue
            if created < cutoff:
                continue

            score = post.get("score", 0)
            comments = post.get("num_comments", 0)

            out.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"r/{subreddit}",
                title=title,
                url=full_url,
                published_at=created,
                meta={"score": score, "comments": comments, "subreddit": subreddit},
            ))

    return out


def fetch_hf_spaces_trending(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch trending Hugging Face Spaces (demos and apps)."""
    site_id = "hf_spaces"
    site_name = "HF Spaces"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=HF_SPACES_MAX_AGE_DAYS)

    url = (
        "https://huggingface.co/api/spaces"
        f"?sort=lastModified&direction=-1&limit={HF_SPACES_TRENDING_LIMIT}"
    )
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    spaces = resp.json()

    for s in spaces:
        space_id = s.get("id") or ""
        if not space_id:
            continue

        last_modified = parse_date_any(s.get("lastModified") or s.get("lastModifiedAt"), now)
        if not last_modified:
            continue
        if last_modified < cutoff:
            continue

        tags = s.get("tags") or []
        sdk = s.get("sdk") or ""
        likes = s.get("likes", 0)

        short_name = space_id.split("/")[-1] if "/" in space_id else space_id
        title = short_name
        if sdk:
            title = f"{short_name} [{sdk}]"

        space_url = f"https://huggingface.co/spaces/{space_id}"

        meta: dict[str, Any] = {
            "space_id": space_id,
            "likes": likes,
        }
        if sdk:
            meta["sdk"] = sdk
        if tags:
            meta["tags"] = tags[:8]

        out.append(RawItem(
            site_id=site_id,
            site_name=site_name,
            source="Hugging Face · Spaces",
            title=title,
            url=space_url,
            published_at=last_modified,
            meta=meta,
        ))

    return out


def fetch_findit_releases(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch releases from repos tracked in the Findit-AI research kanban."""
    site_id = "findit"
    site_name = "Findit-AI"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=FINDIT_MAX_AGE_DAYS)

    gh_headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        gh_headers["Authorization"] = f"Bearer {token}"

    # Try to load kanban repo list (generated by sync_findit_kanban.py)
    kanban_path = Path("data/findit-kanban.json")
    repo_names: list[str] = []
    if kanban_path.exists():
        try:
            kanban = json.loads(kanban_path.read_text())
            repo_names = kanban.get("repos", [])
        except Exception:
            pass

    # Fallback: list Findit-AI org repos
    if not repo_names:
        try:
            repos_url = f"https://api.github.com/orgs/{FINDIT_ORG}/repos?per_page=100&sort=pushed"
            repos_resp = session.get(repos_url, timeout=30, headers=gh_headers)
            repos_resp.raise_for_status()
            repo_names = [r["full_name"] for r in repos_resp.json()]
        except Exception:
            return out

    def fetch_repo_releases(full_name: str) -> list[RawItem]:
        items: list[RawItem] = []
        repo_name = full_name.split("/")[-1] if "/" in full_name else full_name

        releases_url = f"https://api.github.com/repos/{full_name}/releases?per_page=5"
        try:
            rel_resp = session.get(releases_url, timeout=20, headers=gh_headers)
            if rel_resp.status_code != 200:
                return items
            releases = rel_resp.json()
        except Exception:
            return items

        for rel in releases:
            tag = rel.get("tag_name") or ""
            name = rel.get("name") or tag
            published = parse_date_any(rel.get("published_at"), now)
            if not published or published < cutoff:
                continue
            if rel.get("draft"):
                continue

            html_url = rel.get("html_url") or f"https://github.com/{full_name}/releases"
            title = f"{repo_name} {name}" if name else repo_name

            items.append(RawItem(
                site_id="findit",
                site_name="Findit-AI",
                source=repo_name,
                title=title,
                url=html_url,
                published_at=published,
                meta={"repo": full_name, "tag": tag},
            ))
        return items

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_repo_releases, name) for name in repo_names]
        for fut in futures:
            try:
                out.extend(fut.result())
            except Exception:
                continue

    return out


def fetch_arxiv_feeds(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch recent papers from arXiv category feeds (cs.CV, cs.MM, cs.SD, eess.AS)."""
    site_id = "arxiv"
    site_name = "arXiv"
    out: list[RawItem] = []
    cutoff = now - timedelta(days=ARXIV_MAX_AGE_DAYS)
    ua = {"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}

    for cat in ARXIV_CATEGORIES:
        try:
            resp = session.get(cat["url"], timeout=30, headers=ua)
            resp.raise_for_status()
        except Exception:
            continue
        entries = parse_feed_entries_via_xml(resp.content)
        for entry in entries:
            title = maybe_fix_mojibake(entry.get("title", "").strip())
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            published = parse_date_any(entry.get("published"), now) or parse_date_any(entry.get("updated"), now)
            if not published:
                continue
            if published < cutoff:
                continue
            # arXiv titles often start with "arXiv:" prefix, clean it
            title = re.sub(r"^arXiv:\d+\.\d+\s*", "", title).strip()
            out.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=cat["name"],
                title=title,
                url=link,
                published_at=published,
                meta={"category": cat["id"]},
            ))

    return out


def fetch_hn_ai_filtered(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch AI video/audio/agent posts from Hacker News via Algolia API."""
    site_id = "hn_ai"
    site_name = "Hacker News · AI"
    out: list[RawItem] = []
    cutoff_ts = int((now - timedelta(days=HN_MAX_AGE_DAYS)).timestamp())
    seen_ids: set[int] = set()

    for keyword in HN_KEYWORDS:
        try:
            resp = session.get(
                HN_ALGOLIA_API,
                params={
                    "query": keyword,
                    "tags": "story",
                    "numericFilters": f"created_at_i>{cutoff_ts}",
                    "hitsPerPage": 15,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue

        for hit in data.get("hits", []):
            hn_id = hit.get("objectID")
            if not hn_id:
                continue
            try:
                hn_id_int = int(hn_id)
            except (ValueError, TypeError):
                continue
            if hn_id_int in seen_ids:
                continue
            seen_ids.add(hn_id_int)

            title = (hit.get("title") or "").strip()
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hn_id}"
            if not title:
                continue
            created = parse_unix_timestamp(hit.get("created_at_i")) or now

            out.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"HN · {keyword}",
                title=title,
                url=url,
                published_at=created,
                meta={"hn_id": hn_id_int, "points": hit.get("points", 0)},
            ))

    return out


GITHUB_TRENDING_FEEDS: tuple[tuple[str, str], ...] = (
    ("daily", "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"),
    ("weekly", "https://mshibanami.github.io/GitHubTrendingRSS/weekly/all.xml"),
)
GITHUB_TRENDING_MAX_AGE_DAYS = 8


def fetch_github_trending(session: requests.Session, now: datetime) -> list[RawItem]:
    """Fetch trending GitHub repositories via the public mshibanami RSS feeds.

    Uses the community-maintained daily/weekly all-language feeds so we don't have
    to scrape github.com/trending HTML ourselves.
    """
    site_id = "github_trending"
    site_name = "GitHub Trending"
    cutoff = now - timedelta(days=GITHUB_TRENDING_MAX_AGE_DAYS)
    out: list[RawItem] = []
    seen_urls: set[str] = set()
    ua = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    for window_name, feed_url in GITHUB_TRENDING_FEEDS:
        try:
            resp = session.get(feed_url, timeout=30, headers=ua)
            resp.raise_for_status()
        except Exception:
            continue
        entries = parse_feed_entries_via_xml(resp.content)
        for entry in entries:
            title = maybe_fix_mojibake(str(entry.get("title", "")).strip())
            link = str(entry.get("link", "")).strip()
            if not title or not link or link in seen_urls:
                continue
            seen_urls.add(link)
            published = parse_date_any(entry.get("published"), now) or now
            if published < cutoff:
                continue
            out.append(RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"GitHub Trending · {window_name}",
                title=title,
                url=link,
                published_at=published,
                meta={"feed_window": window_name},
            ))

    return out


def collect_all(session: requests.Session, now: datetime) -> tuple[list[RawItem], list[dict[str, Any]]]:
    tasks = [
        ("official_ai", "Official AI Updates", fetch_official_ai_updates),
        ("aibreakfast", "AI Breakfast", fetch_ai_breakfast),
        ("followbuilders", "Follow Builders", fetch_follow_builders),
        ("aihubtoday", "AI HubToday", fetch_ai_hubtoday),
        ("aibase", "AIbase", fetch_aibase),
        ("aihot", "AI HOT", fetch_aihot),
        ("hf_papers", "HF Daily Papers", fetch_hf_daily_papers),
        ("hf_mlx", "mlx-community", fetch_hf_mlx_community),
        ("reddit_ai", "Reddit · AI", fetch_reddit_ai),
        ("hf_spaces", "HF Spaces Trending", fetch_hf_spaces_trending),
        ("findit", "Findit-AI", fetch_findit_releases),
        ("github_topics", "GitHub Topics", fetch_github_topics),
        ("github_releases", "GitHub Releases", fetch_github_releases),
        ("github_trending", "GitHub Trending", fetch_github_trending),
        ("arxiv", "arXiv", fetch_arxiv_feeds),
        ("hn_ai", "Hacker News · AI", fetch_hn_ai_filtered),
    ]

    raw_items: list[RawItem] = []
    statuses: list[dict[str, Any]] = []

    for site_id, site_name, fn in tasks:
        start = time.perf_counter()
        error = None
        count = 0
        try:
            items = fn(session, now)
            count = len(items)
            raw_items.extend(items)
        except Exception as exc:
            error = str(exc)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        statuses.append(
            {
                "site_id": site_id,
                "site_name": site_name,
                "ok": error is None,
                "item_count": count,
                "duration_ms": elapsed_ms,
                "error": error,
            }
        )

    return raw_items, statuses


def parse_opml_subscriptions(opml_path: Path) -> list[dict[str, str]]:
    root = ET.parse(opml_path).getroot()
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for outline in root.findall(".//outline"):
        xml_url = str(outline.attrib.get("xmlUrl") or "").strip()
        if not xml_url:
            continue
        if xml_url in seen:
            continue
        seen.add(xml_url)
        title = first_non_empty(
            outline.attrib.get("title"),
            outline.attrib.get("text"),
            host_of_url(xml_url),
            xml_url,
        )
        html_url = str(outline.attrib.get("htmlUrl") or "").strip()
        out.append(
            {
                "title": title,
                "xml_url": xml_url,
                "html_url": html_url,
            }
        )
    return out


def resolve_official_rss_url(feed_url: str) -> tuple[str | None, str | None]:
    src = (feed_url or "").strip()
    if not src:
        return None, "empty_url"
    if src in RSS_FEED_SKIP_EXACT:
        return None, "no_official_rss_or_unreachable"
    for prefix in RSS_FEED_SKIP_PREFIXES:
        if src.startswith(prefix):
            return None, "no_official_rss_for_source_type"
    replaced = RSS_FEED_REPLACEMENTS.get(src)
    if replaced:
        return replaced, "official_replacement"
    return src, None


def resolve_opml_bridge_source(feed_url: str, html_url: str = "") -> dict[str, str] | None:
    src = (feed_url or "").strip()
    parsed = urlparse(src)
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]

    if parsed.netloc == "rsshub.app" and len(parts) >= 3 and parts[:2] == ["telegram", "channel"]:
        slug = parts[2]
        return {
            "bridge_type": "telegram",
            "bridge_slug": slug,
            "url": f"https://t.me/s/{slug}",
        }

    if parsed.netloc == "rsshub.app" and len(parts) >= 3 and parts[0] == "jike":
        kind = parts[1]
        ident = parts[2]
        if kind == "topic":
            return {
                "bridge_type": "jike",
                "bridge_kind": "topic",
                "bridge_slug": ident,
                "url": f"https://m.okjike.com/topics/{ident}",
            }
        if kind == "user":
            return {
                "bridge_type": "jike",
                "bridge_kind": "user",
                "bridge_slug": ident,
                "url": f"https://m.okjike.com/users/{ident}",
            }

    html = (html_url or "").strip()
    if html.startswith("https://t.me/s/"):
        slug = html.rstrip("/").split("/")[-1]
        return {"bridge_type": "telegram", "bridge_slug": slug, "url": html}
    if html.startswith("https://m.okjike.com/topics/"):
        ident = html.rstrip("/").split("/")[-1]
        return {"bridge_type": "jike", "bridge_kind": "topic", "bridge_slug": ident, "url": html}
    if html.startswith("https://m.okjike.com/users/"):
        ident = html.rstrip("/").split("/")[-1]
        return {"bridge_type": "jike", "bridge_kind": "user", "bridge_slug": ident, "url": html}

    return None


def compact_title(text: str, limit: int = 96) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def parse_telegram_public_items(
    html: str,
    *,
    now: datetime,
    source_name: str,
    slug: str,
) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[RawItem] = []
    for msg in soup.select(".tgme_widget_message"):
        data_post = str(msg.get("data-post") or "").strip()
        if not data_post:
            continue
        text_node = msg.select_one(".tgme_widget_message_text")
        text = text_node.get_text(" ", strip=True) if text_node else ""
        if not text:
            preview_title = msg.select_one(".tgme_widget_message_link_preview_title")
            text = preview_title.get_text(" ", strip=True) if preview_title else ""
        if not text:
            continue
        time_node = msg.select_one("time[datetime]")
        published = parse_date_any(time_node.get("datetime") if time_node else None, now)
        if not published:
            continue
        url = f"https://t.me/{data_post}"
        out.append(
            RawItem(
                site_id="opmlrss",
                site_name="OPML RSS",
                source=source_name,
                title=compact_title(text),
                url=url,
                published_at=published,
                meta={"bridge_type": "telegram", "bridge_slug": slug, "feed_home": f"https://t.me/s/{slug}"},
            )
        )
    return out


def parse_jike_public_items(
    html: str,
    *,
    now: datetime,
    source_name: str,
    source_url: str,
) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return []
    try:
        payload = json.loads(script.string)
    except Exception:
        return []
    page_props = payload.get("props", {}).get("pageProps", {})
    posts = page_props.get("posts") or []
    out: list[RawItem] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or "").strip()
        text = str(post.get("content") or "").strip()
        if not post_id or not text:
            continue
        published = parse_date_any(post.get("createdAt") or post.get("actionTime"), now)
        if not published:
            continue
        out.append(
            RawItem(
                site_id="opmlrss",
                site_name="OPML RSS",
                source=source_name,
                title=compact_title(text),
                url=f"https://m.okjike.com/originalPosts/{post_id}",
                published_at=published,
                meta={"bridge_type": "jike", "feed_home": source_url},
            )
        )
    return out


def fetch_opml_rss(
    now: datetime,
    opml_path: Path,
    max_feeds: int = 0,
) -> tuple[list[RawItem], dict[str, Any], list[dict[str, Any]]]:
    feeds = parse_opml_subscriptions(opml_path)
    if max_feeds > 0:
        feeds = feeds[:max_feeds]

    out: list[RawItem] = []
    feed_statuses: list[dict[str, Any]] = []
    resolved_feeds: list[dict[str, str]] = []

    for feed in feeds:
        original_url = feed["xml_url"]
        bridge = resolve_opml_bridge_source(original_url, feed.get("html_url") or "")
        if bridge:
            record = dict(feed)
            record["xml_url_original"] = original_url
            record["xml_url"] = bridge["url"]
            record["replaced"] = True
            record.update(bridge)
            resolved_feeds.append(record)
            continue

        resolved_url, skip_reason = resolve_official_rss_url(original_url)
        if not resolved_url:
            feed_id = hashlib.sha1(original_url.encode("utf-8")).hexdigest()[:10]
            feed_statuses.append(
                {
                    "site_id": f"opmlrss:{feed_id}",
                    "site_name": "OPML RSS",
                    "feed_title": feed["title"],
                    "feed_url": original_url,
                    "effective_feed_url": None,
                    "ok": True,
                    "item_count": 0,
                    "duration_ms": 0,
                    "error": None,
                    "skipped": True,
                    "skip_reason": skip_reason or "skipped",
                    "replaced": False,
                }
            )
            continue
        record = dict(feed)
        record["xml_url_original"] = original_url
        record["xml_url"] = resolved_url
        record["replaced"] = bool(resolved_url != original_url)
        resolved_feeds.append(record)

    def fetch_single_feed(feed: dict[str, str]) -> tuple[list[RawItem], dict[str, Any]]:
        feed_url = feed["xml_url"]
        original_feed_url = str(feed.get("xml_url_original") or feed_url)
        feed_title = feed["title"]
        feed_id = hashlib.sha1(feed_url.encode("utf-8")).hexdigest()[:10]
        start = time.perf_counter()
        error = None
        local_items: list[RawItem] = []

        try:
            resp = requests.get(
                feed_url,
                timeout=12,
                headers={
                    "User-Agent": BROWSER_UA,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            resp.raise_for_status()

            bridge_type = str(feed.get("bridge_type") or "")
            if bridge_type == "telegram":
                local_items = parse_telegram_public_items(
                    resp.text,
                    now=now,
                    source_name=feed_title,
                    slug=str(feed.get("bridge_slug") or ""),
                )
            elif bridge_type == "jike":
                local_items = parse_jike_public_items(
                    resp.text,
                    now=now,
                    source_name=feed_title,
                    source_url=feed_url,
                )
            elif feedparser is not None:
                parsed = feedparser.parse(resp.content)
                source_name = first_non_empty(
                    feed_title,
                    getattr(parsed, "feed", {}).get("title"),
                    host_of_url(feed_url),
                )
                entries = parsed.entries
                for entry in entries:
                    title = str(entry.get("title", "")).strip()
                    link = str(entry.get("link", "")).strip()
                    if not title or not link:
                        continue
                    published = (
                        parse_date_any(entry.get("published"), now)
                        or parse_date_any(entry.get("updated"), now)
                        or parse_date_any(entry.get("pubDate"), now)
                    )
                    if not published:
                        continue
                    local_items.append(
                        RawItem(
                            site_id="opmlrss",
                            site_name="OPML RSS",
                            source=source_name,
                            title=title,
                            url=link,
                            published_at=published,
                            meta={
                                "feed_url": feed_url,
                                "feed_home": feed.get("html_url") or "",
                            },
                        )
                    )
            else:
                source_name = first_non_empty(feed_title, host_of_url(feed_url))
                entries = parse_feed_entries_via_xml(resp.content)
                for entry in entries:
                    published = parse_date_any(entry.get("published"), now)
                    if not published:
                        continue
                    local_items.append(
                        RawItem(
                            site_id="opmlrss",
                            site_name="OPML RSS",
                            source=source_name,
                            title=entry.get("title", ""),
                            url=entry.get("link", ""),
                            published_at=published,
                            meta={
                                "feed_url": feed_url,
                                "feed_home": feed.get("html_url") or "",
                            },
                        )
                    )
        except Exception as exc:
            error = str(exc)

        duration_ms = int((time.perf_counter() - start) * 1000)
        status = {
            "site_id": f"opmlrss:{feed_id}",
            "site_name": "OPML RSS",
            "feed_title": feed_title,
            "feed_url": original_feed_url,
            "effective_feed_url": feed_url,
            "ok": error is None,
            "item_count": len(local_items),
            "duration_ms": duration_ms,
            "error": error,
            "skipped": False,
            "skip_reason": None,
            "replaced": bool(original_feed_url != feed_url),
            "bridge_type": feed.get("bridge_type"),
        }
        return local_items, status

    if resolved_feeds:
        worker_count = min(20, max(4, len(resolved_feeds)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(fetch_single_feed, feed) for feed in resolved_feeds]
            for future in as_completed(futures):
                items, status = future.result()
                out.extend(items)
                feed_statuses.append(status)

    feed_statuses.sort(key=lambda x: str(x.get("feed_title") or x.get("feed_url") or ""))
    total_duration_ms = sum(int(s.get("duration_ms") or 0) for s in feed_statuses)
    ok_feeds = sum(1 for s in feed_statuses if s["ok"])
    failed_feeds = sum(1 for s in feed_statuses if not s["ok"])
    skipped_feeds = sum(1 for s in feed_statuses if s.get("skipped"))
    replaced_feeds = sum(1 for s in feed_statuses if s.get("replaced"))

    summary_status = {
        "site_id": "opmlrss",
        "site_name": "OPML RSS",
        "ok": ok_feeds > 0,
        "partial_failures": failed_feeds,
        "item_count": len(out),
        "duration_ms": total_duration_ms,
        "error": None if failed_feeds == 0 else f"{failed_feeds} feeds failed",
        "feed_count": len(feeds),
        "effective_feed_count": len(resolved_feeds),
        "ok_feed_count": ok_feeds,
        "failed_feed_count": failed_feeds,
        "skipped_feed_count": skipped_feeds,
        "replaced_feed_count": replaced_feeds,
    }
    return out, summary_status, feed_statuses


def load_archive(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    items = payload.get("items", [])
    out: dict[str, dict[str, Any]] = {}
    if isinstance(items, list):
        for it in items:
            item_id = it.get("id")
            if item_id:
                out[item_id] = it
    elif isinstance(items, dict):
        for item_id, it in items.items():
            if isinstance(it, dict):
                it["id"] = item_id
                out[item_id] = it
    return out


def event_time(record: dict[str, Any]) -> datetime | None:
    # RSS sources must rely on the source's publish time only.
    # first_seen_at is fetch time and would falsely mark historical items as "24h".
    if str(record.get("site_id") or "") == "opmlrss":
        return parse_iso(record.get("published_at"))
    return parse_iso(record.get("published_at")) or parse_iso(record.get("first_seen_at"))


AI_KEYWORDS = [
    "aigc",
    "llm",
    "gpt",
    "claude",
    "gemini",
    "deepseek",
    "openai",
    "anthropic",
    "copilot",
    "codex",
    "mcp",
    "hugging face",
    "huggingface",
    "transformer",
    "prompt",
    "diffusion",
    "agent",
    "多模态",
    "大模型",
    "模型",
    "人工智能",
    "机器学习",
    "深度学习",
    "智能体",
    "算力",
    "推理",
    "微调",
]

TECH_KEYWORDS = [
    "robot",
    "robotics",
    "embodied",
    "autonomous",
    "vision",
    "chip",
    "semiconductor",
    "cuda",
    "npu",
    "gpu",
    "cloud",
    "developer",
    "开源",
    "技术",
    "编程",
    "软件",
    "芯片",
    "机器人",
    "具身",
]

NOISE_KEYWORDS = [
    "娱乐",
    "明星",
    "八卦",
    "足球",
    "篮球",
    "彩票",
    "情感",
    "旅游",
    "美食",
]

COMMERCE_NOISE_KEYWORDS = [
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "券后",
    "热销总榜",
    "促销",
    "优惠",
    "补贴",
    "下单",
    "首发价",
]

EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion|agent)(?![a-z0-9])"
)

TOPHUB_ALLOW_KEYWORDS = [
    "readhub · ai",
    "hacker news",
    "github",
    "product hunt",
    "v2ex",
    "少数派",
    "infoq",
    "36氪",
    "机器之心",
    "量子位",
    "科技",
    "人工智能",
    "机器人",
    "具身",
    "开源",
]

TOPHUB_BLOCK_KEYWORDS = [
    "热销总榜",
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "抖音",
    "快手",
    "微博",
    "小红书",
]


MEANINGFUL_EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion)(?![a-z0-9])"
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_LIKE_RE = re.compile(r"\b(sk-(?!hynix\b)[A-Za-z0-9_-]{12,}|(?:api[_-]?key|secret|token)=([^\s&]{6,}))\b", re.I)
BROAD_AI_TERMS = {"agent", "模型", "推理"}


def contains_any_keyword(haystack: str, keywords: list[str]) -> bool:
    h = haystack.lower()
    return any(k in h for k in keywords)


def contains_meaningful_ai_signal(haystack: str) -> bool:
    h = haystack.lower()
    if MEANINGFUL_EN_SIGNAL_RE.search(h):
        return True
    return any(k in h for k in AI_KEYWORDS if k not in BROAD_AI_TERMS)


def redact_public_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    text = EMAIL_RE.sub("[redacted-email]", text)
    return SECRET_LIKE_RE.sub("[redacted-secret]", text)


def sanitize_public_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_public_text(value)
    if isinstance(value, list):
        return [sanitize_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_public_value(val) for key, val in value.items()}
    return value


def sanitize_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return sanitize_public_value(payload)


def compact_public_snippet(text: str, max_chars: int = 240) -> str:
    """Return a short redacted snippet suitable for public/static JSON."""
    snippet = re.sub(r"\s+", " ", str(text or "")).strip()
    snippet = redact_public_text(snippet)
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max_chars - 1].rstrip() + "…"


def sender_domain_from_address(raw_sender: str) -> str | None:
    """Extract only the sender domain; never expose the raw email address."""
    _, email_addr = parseaddr(str(raw_sender or ""))
    if "@" not in email_addr:
        return None
    domain = email_addr.rsplit("@", 1)[-1].strip().lower().strip(">")
    return domain or None


def parse_domain_filter(raw: str) -> list[str]:
    """Parse a comma-separated sender-domain allowlist for private newsletter demos."""
    domains: list[str] = []
    for part in re.split(r"[,\s]+", str(raw or "")):
        domain = part.strip().lower().lstrip("@")
        if domain and re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
            domains.append(domain)
    return sorted(set(domains))


def domain_matches_filter(sender_domain: str | None, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    domain = str(sender_domain or "").lower().strip()
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains)


def filter_agentmail_messages_by_domain(
    messages: list[dict[str, Any]],
    allowed_domains: list[str],
) -> list[dict[str, Any]]:
    if not allowed_domains:
        return messages
    return [
        msg
        for msg in messages
        if domain_matches_filter(sender_domain_from_address(str(msg.get("from") or "")), allowed_domains)
    ]


def safe_agentmail_item(message: dict[str, Any]) -> dict[str, Any]:
    """Convert an AgentMail MessageItem into a metadata-only public digest item."""
    message_id = str(message.get("message_id") or "")
    stable_id = hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:12] if message_id else "unknown"
    domain = sender_domain_from_address(str(message.get("from") or ""))
    attachments = message.get("attachments") or []
    return {
        "id": f"agentmail:{stable_id}",
        "source_type": "email_newsletter",
        "source": f"AgentMail · {domain}" if domain else "AgentMail",
        "sender_domain": domain,
        "subject": compact_public_snippet(str(message.get("subject") or ""), max_chars=180),
        "preview": compact_public_snippet(str(message.get("preview") or ""), max_chars=240),
        "received_at": message.get("timestamp") or message.get("created_at"),
        "has_attachments": bool(attachments),
        "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
    }


def build_agentmail_digest_payload(
    messages: list[dict[str, Any]],
    generated_at: str,
    window_hours: int,
    allowed_sender_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Build a privacy-preserving digest from AgentMail list-message results."""
    filtered_messages = filter_agentmail_messages_by_domain(messages, allowed_sender_domains or [])
    items = [safe_agentmail_item(msg) for msg in filtered_messages]
    return sanitize_public_payload(
        {
            "generated_at": generated_at,
            "source": "agentmail",
            "enabled": True,
            "window_hours": window_hours,
            "privacy": "metadata_only_no_body",
            "allowed_sender_domains": allowed_sender_domains or [],
            "total_messages": len(items),
            "items": items,
        }
    )


def fetch_agentmail_digest(
    session: requests.Session,
    api_key: str,
    inbox_id: str,
    generated_at: str,
    after: str,
    limit: int = AGENTMAIL_DEFAULT_LIMIT,
    base_url: str = AGENTMAIL_API_BASE_DEFAULT,
    window_hours: int = 24,
    allowed_sender_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch AgentMail MessageItem metadata; deliberately does not request bodies or raw .eml."""
    base = (base_url or AGENTMAIL_API_BASE_DEFAULT).rstrip("/")
    url = f"{base}/v0/inboxes/{inbox_id}/messages"
    response = session.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        params={
            "limit": max(1, min(int(limit or AGENTMAIL_DEFAULT_LIMIT), 100)),
            "after": after,
            "ascending": "false",
            "include_spam": "false",
            "include_trash": "false",
            "include_blocked": "false",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    messages = payload.get("messages") if isinstance(payload, dict) else []
    if not isinstance(messages, list):
        messages = []
    return build_agentmail_digest_payload(
        messages,
        generated_at=generated_at,
        window_hours=window_hours,
        allowed_sender_domains=allowed_sender_domains,
    )


def env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name) or default).strip() or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name) or default).strip() or default)
    except ValueError:
        return default


def maybe_fetch_agentmail_digest(
    session: requests.Session,
    generated_at: str,
    after: str,
    window_hours: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch AgentMail only when explicitly enabled and fully configured."""
    status: dict[str, Any] = {
        "enabled": env_flag("EMAIL_DIGEST_ENABLED"),
        "ok": None,
        "item_count": 0,
        "privacy": "metadata_only_no_body",
        "published_by_default": False,
    }
    if not status["enabled"]:
        return None, status

    agentmail_api_key = str(os.environ.get("AGENTMAIL_API_KEY") or "").strip()
    agentmail_inbox_id = str(os.environ.get("AGENTMAIL_INBOX_ID") or "").strip()
    agentmail_base_url = str(os.environ.get("AGENTMAIL_API_BASE_URL") or AGENTMAIL_API_BASE_DEFAULT).strip()
    agentmail_limit = env_int("AGENTMAIL_LIMIT", AGENTMAIL_DEFAULT_LIMIT)
    allowed_sender_domains = parse_domain_filter(str(os.environ.get("AGENTMAIL_ALLOWED_SENDER_DOMAINS") or ""))
    status["allowed_sender_domains"] = allowed_sender_domains
    if not (agentmail_api_key and agentmail_inbox_id):
        status["ok"] = False
        status["error"] = "missing_agentmail_credentials"
        return None, status

    try:
        payload = fetch_agentmail_digest(
            session,
            api_key=agentmail_api_key,
            inbox_id=agentmail_inbox_id,
            generated_at=generated_at,
            after=after,
            limit=agentmail_limit,
            base_url=agentmail_base_url,
            window_hours=window_hours,
            allowed_sender_domains=allowed_sender_domains,
        )
        status["ok"] = True
        status["item_count"] = int(payload.get("total_messages") or 0)
        return payload, status
    except Exception as exc:
        status["ok"] = False
        status["error"] = type(exc).__name__
        return None, status


def x_api_should_run_now(now: datetime) -> bool:
    """Gate paid X API reads so a 30-minute cron does not spend every run."""
    if env_flag("X_API_FORCE_RUN"):
        return True
    run_hour = max(0, min(env_int("X_API_RUN_UTC_HOUR", 0), 23))
    minute_max = max(0, min(env_int("X_API_RUN_UTC_MINUTE_MAX", 10), 59))
    return now.astimezone(UTC).hour == run_hour and now.astimezone(UTC).minute <= minute_max


def x_api_status_base(now: datetime) -> dict[str, Any]:
    daily_post_limit = max(0, env_int("X_API_DAILY_POST_LIMIT", X_API_DEFAULT_MAX_RESULTS))
    max_results = max(10, min(env_int("X_API_MAX_RESULTS", X_API_DEFAULT_MAX_RESULTS), 100))
    effective_cap = min(max_results, daily_post_limit) if daily_post_limit else 0
    return {
        "enabled": env_flag("X_API_ENABLED"),
        "ok": None,
        "item_count": 0,
        "privacy": "public_posts_metadata_only",
        "published_by_default": False,
        "official_free_read_quota": False,
        "unit_cost_usd_per_post_read": X_API_POST_READ_COST_USD,
        "daily_post_limit": daily_post_limit,
        "max_results_per_run": max_results,
        "effective_result_cap": effective_cap,
        "estimated_max_cost_usd_per_run": round(effective_cap * X_API_POST_READ_COST_USD, 4),
        "run_utc_hour": max(0, min(env_int("X_API_RUN_UTC_HOUR", 0), 23)),
        "generated_date_utc": now.astimezone(UTC).date().isoformat(),
    }


def fetch_x_api_recent_search(
    session: requests.Session,
    bearer_token: str,
    query: str,
    now: datetime,
    max_results: int,
    base_url: str = X_API_BASE_DEFAULT,
) -> list[RawItem]:
    """Fetch public recent-search Posts from X API v2; no writes and no DMs."""
    query = re.sub(r"\s+", " ", (query or X_API_DEFAULT_QUERY).strip())
    if len(query) > X_API_MAX_QUERY_CHARS:
        raise ValueError("x_query_too_long")
    capped_max_results = max(10, min(int(max_results or X_API_DEFAULT_MAX_RESULTS), 100))
    url = f"{(base_url or X_API_BASE_DEFAULT).rstrip('/')}/2/tweets/search/recent"
    response = session.get(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        params={
            "query": query,
            "max_results": capped_max_results,
            "tweet.fields": "created_at,author_id,public_metrics,lang",
            "expansions": "author_id",
            "user.fields": "username,name,verified",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    users = {
        str(user.get("id")): user
        for user in (payload.get("includes", {}) or {}).get("users", [])
        if isinstance(user, dict) and user.get("id")
    }
    out: list[RawItem] = []
    for post in payload.get("data") or []:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or "").strip()
        text = compact_public_snippet(str(post.get("text") or ""), max_chars=220)
        if not (post_id and text):
            continue
        user = users.get(str(post.get("author_id") or ""), {})
        username = str(user.get("username") or "i/web").strip() or "i/web"
        published = parse_iso(str(post.get("created_at") or "")) or now
        out.append(
            RawItem(
                site_id="xapi",
                site_name="X API",
                source=f"@{username}",
                title=text,
                url=f"https://x.com/{username}/status/{post_id}",
                published_at=published,
                meta={
                    "post_id": post_id,
                    "lang": post.get("lang"),
                    "public_metrics": post.get("public_metrics") or {},
                },
            )
        )
    return out


def maybe_fetch_x_api_updates(
    session: requests.Session,
    now: datetime,
) -> tuple[list[RawItem], dict[str, Any]]:
    """Fetch X only when explicitly enabled, credentialed, scheduled, and capped."""
    status = x_api_status_base(now)
    if not status["enabled"]:
        return [], status

    if status["effective_result_cap"] < 10:
        status["ok"] = False
        status["error"] = "x_daily_post_limit_below_api_minimum"
        return [], status

    if not x_api_should_run_now(now):
        status["skipped"] = True
        status["skip_reason"] = "outside_x_api_daily_window"
        return [], status

    bearer_token = str(os.environ.get("X_BEARER_TOKEN") or os.environ.get("X_API_BEARER_TOKEN") or "").strip()
    if not bearer_token:
        status["ok"] = False
        status["error"] = "missing_x_bearer_token"
        return [], status

    query = str(os.environ.get("X_API_QUERY") or X_API_DEFAULT_QUERY).strip()
    base_url = str(os.environ.get("X_API_BASE_URL") or X_API_BASE_DEFAULT).strip()
    try:
        items = fetch_x_api_recent_search(
            session,
            bearer_token=bearer_token,
            query=query,
            now=now,
            max_results=int(status["effective_result_cap"]),
            base_url=base_url,
        )
        status["ok"] = True
        status["item_count"] = len(items)
        status["estimated_cost_usd"] = round(len(items) * X_API_POST_READ_COST_USD, 4)
        return items, status
    except Exception as exc:
        status["ok"] = False
        status["error"] = type(exc).__name__
        return [], status


def has_mojibake_noise(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"(Ã|Â|â€|æ·|�)", text))


def normalize_source_for_display(site_id: str, source: str, url: str) -> str:
    src = (source or "").strip()
    if not src:
        host = host_of_url(url)
        if host.startswith("www."):
            host = host[4:]
        return host or "未分区"
    if site_id == "buzzing" and src.lower() == "buzzing":
        host = host_of_url(url)
        if host.startswith("www."):
            host = host[4:]
        return host or src
    return src


def is_ai_related_record(record: dict[str, Any]) -> bool:
    if has_mojibake_noise(str(record.get("source") or "")) or has_mojibake_noise(str(record.get("title") or "")):
        return False
    return bool(score_ai_relevance(record)["is_ai_related"])


def load_title_zh_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(k).strip() and str(v).strip()}
    except Exception:
        pass
    return {}


def translate_to_zh_cn(session: requests.Session, text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        r = session.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": s,
            },
            timeout=12,
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, list) or not payload:
            return None
        segs = payload[0]
        if not isinstance(segs, list):
            return None
        translated = "".join(str(seg[0]) for seg in segs if isinstance(seg, list) and seg and seg[0])
        translated = translated.strip()
        if translated and translated != s:
            return translated
    except Exception:
        return None
    return None


def add_bilingual_fields(
    items_ai: list[dict[str, Any]],
    items_all: list[dict[str, Any]],
    session: requests.Session,
    cache: dict[str, str],
    max_new_translations: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    zh_by_url: dict[str, str] = {}
    for it in items_all:
        title = str(it.get("title") or "").strip()
        url = normalize_url(str(it.get("url") or ""))
        if title and url and has_cjk(title):
            zh_by_url[url] = title

    translated_now = 0

    def enrich(item: dict[str, Any], allow_translate: bool) -> dict[str, Any]:
        nonlocal translated_now
        out = dict(item)
        title = str(out.get("title") or "").strip()
        url = normalize_url(str(out.get("url") or ""))

        out["title_original"] = title
        out["title_en"] = None
        out["title_zh"] = None
        out["title_bilingual"] = title

        if has_cjk(title):
            out["title_zh"] = title
            return out

        if not is_mostly_english(title):
            return out

        out["title_en"] = title

        zh_title = zh_by_url.get(url)
        if not zh_title:
            zh_title = cache.get(title)
        if not zh_title and allow_translate and translated_now < max_new_translations:
            tr = translate_to_zh_cn(session, title)
            if tr and has_cjk(tr):
                zh_title = tr
                cache[title] = tr
                translated_now += 1

        if zh_title:
            out["title_zh"] = zh_title
            out["title_bilingual"] = f"{zh_title} / {title}"
        return out

    ai_out = [enrich(it, allow_translate=True) for it in items_ai]
    all_out = [enrich(it, allow_translate=False) for it in items_all]
    return ai_out, all_out, cache


def dedupe_items_by_title_url(items: list[dict[str, Any]], random_pick: bool = True) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        site_id = str(item.get("site_id") or "").strip().lower()
        title = str(item.get("title_original") or item.get("title") or "").strip().lower()
        url = normalize_url(str(item.get("url") or ""))
        if site_id == "aihubtoday":
            key = f"url::{url}"
        else:
            key = f"{title}||{url}"
        groups.setdefault(key, []).append(item)

    out: list[dict[str, Any]] = []
    for values in groups.values():
        if random_pick:
            out.append(random.choice(values))
        else:
            chosen = max(
                values,
                key=lambda x: (
                    event_time(x) or datetime.min.replace(tzinfo=UTC),
                    str(x.get("id") or ""),
                ),
            )
            out.append(chosen)

    out.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


def build_latest_payloads(latest_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split initial AI payload from bulky all-mode lists for lazy browser loading."""
    slim_payload = dict(latest_payload)
    all_payload = {
        "generated_at": latest_payload.get("generated_at"),
        "window_hours": latest_payload.get("window_hours"),
        "topic_filter": latest_payload.get("topic_filter"),
        "ai_relevance_threshold": latest_payload.get("ai_relevance_threshold"),
        "total_items_raw": latest_payload.get("total_items_raw"),
        "total_items_all_mode": latest_payload.get("total_items_all_mode"),
        "items_all": latest_payload.get("items_all", []),
        "items_all_raw": latest_payload.get("items_all_raw", []),
    }
    slim_payload.pop("items_all", None)
    slim_payload.pop("items_all_raw", None)
    slim_payload["all_mode_data_url"] = "data/latest-24h-all.json"
    return slim_payload, all_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate AI news updates from multiple sources")
    parser.add_argument("--output-dir", default="data", help="Directory for output JSON files")
    parser.add_argument("--window-hours", type=int, default=24, help="24h window size")
    parser.add_argument("--archive-days", type=int, default=21, help="Keep archive for N days")
    parser.add_argument("--translate-max-new", type=int, default=80, help="Max new EN->ZH title translations per run")
    parser.add_argument("--rss-opml", default="", help="Optional OPML file path to include RSS sources")
    parser.add_argument("--rss-max-feeds", type=int, default=0, help="Optional max OPML RSS feeds to fetch (0 means all)")
    args = parser.parse_args()

    now = utc_now()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archive_path = output_dir / "archive.json"
    latest_path = output_dir / "latest-24h.json"
    latest_all_path = output_dir / "latest-24h-all.json"
    status_path = output_dir / "source-status.json"
    waytoagi_path = output_dir / "waytoagi-7d.json"
    title_cache_path = output_dir / "title-zh-cache.json"
    email_digest_path = output_dir / AGENTMAIL_DIGEST_FILE

    archive = load_archive(archive_path)

    session = create_session()
    raw_items, statuses = collect_all(session, now)
    rss_feed_statuses: list[dict[str, Any]] = []
    email_digest_payload, agentmail_status = maybe_fetch_agentmail_digest(
        session,
        generated_at=iso(now),
        after=iso(now - timedelta(hours=args.window_hours)),
        window_hours=args.window_hours,
    )
    x_api_items, x_api_status = maybe_fetch_x_api_updates(session, now)
    if x_api_status.get("enabled"):
        raw_items.extend(x_api_items)
        statuses.append(
            {
                "site_id": "xapi",
                "site_name": "X API",
                "ok": bool(x_api_status.get("ok")) if x_api_status.get("ok") is not None else True,
                "item_count": int(x_api_status.get("item_count") or 0),
                "duration_ms": 0,
                "error": x_api_status.get("error"),
                "skipped": bool(x_api_status.get("skipped")),
                "skip_reason": x_api_status.get("skip_reason"),
            }
        )

    if args.rss_opml:
        opml_path = Path(args.rss_opml).expanduser()
        if opml_path.exists():
            rss_items, rss_summary_status, rss_feed_statuses = fetch_opml_rss(
                now,
                opml_path,
                max_feeds=max(0, int(args.rss_max_feeds)),
            )
            raw_items.extend(rss_items)
            statuses.append(rss_summary_status)
        else:
            statuses.append(
                {
                    "site_id": "opmlrss",
                    "site_name": "OPML RSS",
                    "ok": False,
                    "item_count": 0,
                    "duration_ms": 0,
                    "error": f"OPML not found: {opml_path}",
                    "feed_count": 0,
                    "ok_feed_count": 0,
                    "failed_feed_count": 0,
                }
            )

    seen_this_run: set[str] = set()

    for raw in raw_items:
        title = raw.title.strip()
        url = normalize_url(raw.url)
        if not title or not url:
            continue
        if not url.startswith("http"):
            continue

        item_id = make_item_id(raw.site_id, raw.source, title, url)
        seen_this_run.add(item_id)

        existing = archive.get(item_id)
        if existing is None:
            archive[item_id] = {
                "id": item_id,
                "site_id": raw.site_id,
                "site_name": raw.site_name,
                "source": raw.source,
                "title": title,
                "url": url,
                "published_at": iso(raw.published_at),
                "first_seen_at": iso(now),
                "last_seen_at": iso(now),
            }
        else:
            existing["site_id"] = raw.site_id
            existing["site_name"] = raw.site_name
            existing["source"] = raw.source
            existing["title"] = title
            existing["url"] = url
            if raw.published_at:
                # OPML RSS may fix previously wrong publish times; allow overwrite.
                if raw.site_id == "opmlrss" or not existing.get("published_at"):
                    existing["published_at"] = iso(raw.published_at)
            existing["last_seen_at"] = iso(now)

    # Prune old archive
    keep_after = now - timedelta(days=args.archive_days)
    pruned: dict[str, dict[str, Any]] = {}
    for item_id, record in archive.items():
        ts = (
            parse_iso(record.get("last_seen_at"))
            or parse_iso(record.get("published_at"))
            or parse_iso(record.get("first_seen_at"))
            or now
        )
        if ts >= keep_after:
            pruned[item_id] = record
    archive = pruned

    # 24h view
    window_start = now - timedelta(hours=args.window_hours)
    latest_items_all: list[dict[str, Any]] = []
    for record in archive.values():
        ts = event_time(record)
        if not ts:
            continue
        if ts >= window_start:
            normalized = dict(record)
            normalized["title"] = maybe_fix_mojibake(str(normalized.get("title") or ""))
            normalized["source"] = maybe_fix_mojibake(normalize_source_for_display(
                str(normalized.get("site_id") or ""),
                str(normalized.get("source") or ""),
                str(normalized.get("url") or ""),
            ))
            if str(normalized.get("site_id") or "") == "aihubtoday" and is_hubtoday_placeholder_title(
                str(normalized.get("title") or "")
            ):
                continue
            normalized = add_ai_relevance_fields(normalized)
            latest_items_all.append(normalized)

    latest_items_all = normalize_aihubtoday_records(latest_items_all)

    latest_items_all.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    latest_items = [record for record in latest_items_all if record.get("ai_is_related", is_ai_related_record(record))]
    title_cache = load_title_zh_cache(title_cache_path)
    latest_items, latest_items_all, title_cache = add_bilingual_fields(
        latest_items,
        latest_items_all,
        session,
        title_cache,
        max_new_translations=max(0, args.translate_max_new),
    )
    latest_items_ai_dedup = dedupe_items_by_title_url(latest_items, random_pick=False)
    latest_items_all_dedup = dedupe_items_by_title_url(latest_items_all, random_pick=True)

    # site stats
    site_stat: dict[str, dict[str, Any]] = {}
    raw_count_by_site: dict[str, int] = {}
    for record in latest_items_all:
        sid = record["site_id"]
        raw_count_by_site[sid] = raw_count_by_site.get(sid, 0) + 1

    site_name_by_id: dict[str, str] = {}
    for record in latest_items_all:
        site_name_by_id[record["site_id"]] = record["site_name"]
    for s in statuses:
        sid = s["site_id"]
        if sid not in site_name_by_id:
            site_name_by_id[sid] = s.get("site_name") or sid

    for record in latest_items_ai_dedup:
        sid = record["site_id"]
        if sid not in site_stat:
            site_stat[sid] = {
                "site_id": sid,
                "site_name": record["site_name"],
                "count": 0,
                "raw_count": raw_count_by_site.get(sid, 0),
            }
        site_stat[sid]["count"] += 1

    for sid, site_name in site_name_by_id.items():
        if sid in site_stat:
            continue
        site_stat[sid] = {
            "site_id": sid,
            "site_name": site_name,
            "count": 0,
            "raw_count": raw_count_by_site.get(sid, 0),
        }

    latest_payload = {
        "generated_at": iso(now),
        "window_hours": args.window_hours,
        "total_items": len(latest_items_ai_dedup),
        "total_items_ai_raw": len(latest_items),
        "total_items_raw": len(latest_items_all),
        "total_items_all_mode": len(latest_items_all_dedup),
        "topic_filter": "ai_relevance_scoring_v0_4",
        "ai_relevance_threshold": 0.65,
        "archive_total": len(archive),
        "site_count": len(site_stat),
        "source_count": len({f"{i['site_id']}::{i['source']}" for i in latest_items_ai_dedup}),
        "site_stats": sorted(site_stat.values(), key=lambda x: x["count"], reverse=True),
        "items": latest_items_ai_dedup,
        "items_ai": latest_items_ai_dedup,
        "items_all_raw": latest_items_all,
        "items_all": latest_items_all_dedup,
    }

    archive_payload = {
        "generated_at": iso(now),
        "total_items": len(archive),
        "items": sorted(
            archive.values(),
            key=lambda x: parse_iso(x.get("last_seen_at")) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        ),
    }

    status_payload = {
        "generated_at": iso(now),
        "sites": statuses,
        "successful_sites": sum(1 for s in statuses if s["ok"]),
        "failed_sites": [s["site_id"] for s in statuses if not s["ok"]],
        "zero_item_sites": [s["site_id"] for s in statuses if s.get("ok") and int(s.get("item_count") or 0) == 0],
        "fetched_raw_items": len(raw_items),
        "items_before_topic_filter": len(latest_items_all),
        "items_in_24h": len(latest_items_ai_dedup),
        "rss_opml": {
            "enabled": bool(args.rss_opml),
            "path": "configured" if args.rss_opml else None,
            "feed_total": len(rss_feed_statuses),
            "effective_feed_total": sum(1 for s in rss_feed_statuses if not s.get("skipped")),
            "ok_feeds": sum(1 for s in rss_feed_statuses if s["ok"] and not s.get("skipped")),
            "failed_feeds": [s.get("effective_feed_url") or s["feed_url"] for s in rss_feed_statuses if not s["ok"]],
            "zero_item_feeds": [
                s.get("effective_feed_url") or s["feed_url"]
                for s in rss_feed_statuses
                if s["ok"] and not s.get("skipped") and int(s.get("item_count") or 0) == 0
            ],
            "skipped_feeds": [
                {"feed_url": s["feed_url"], "reason": s.get("skip_reason")}
                for s in rss_feed_statuses
                if s.get("skipped")
            ],
            "replaced_feeds": [
                {"from": s["feed_url"], "to": s.get("effective_feed_url")}
                for s in rss_feed_statuses
                if s.get("replaced") and s.get("effective_feed_url")
            ],
            "feeds": rss_feed_statuses,
        },
        "agentmail": agentmail_status,
        "x_api": x_api_status,
    }

    try:
        waytoagi_payload = fetch_waytoagi_recent_7d(session, now, WAYTOAGI_DEFAULT)
    except Exception as exc:
        waytoagi_payload = {
            "generated_at": iso(now),
            "timezone": "Asia/Shanghai",
            "root_url": WAYTOAGI_DEFAULT,
            "history_url": None,
            "window_days": 7,
            "count_7d": 0,
            "updates_7d": [],
            "warning": "WaytoAGI 近7日更新抓取失败",
            "has_error": True,
            "error": str(exc),
        }

    latest_payload, latest_all_payload = build_latest_payloads(latest_payload)

    latest_path.write_text(json.dumps(sanitize_public_payload(latest_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    latest_all_path.write_text(json.dumps(sanitize_public_payload(latest_all_payload), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    archive_path.write_text(
        json.dumps(sanitize_public_payload(archive_payload), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    status_path.write_text(json.dumps(sanitize_public_payload(status_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    if email_digest_payload is not None:
        email_digest_path.write_text(
            json.dumps(sanitize_public_payload(email_digest_payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    waytoagi_path.write_text(json.dumps(sanitize_public_payload(waytoagi_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    title_cache_path.write_text(json.dumps(sanitize_public_payload(title_cache), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote: {latest_path} ({len(latest_items)} items)")
    print(f"Wrote: {latest_all_path} ({len(latest_items_all_dedup)} all-mode items)")
    print(f"Wrote: {archive_path} ({len(archive)} items)")
    print(f"Wrote: {status_path}")
    if email_digest_payload is not None:
        print(f"Wrote: {email_digest_path} ({email_digest_payload.get('total_messages', 0)} email items)")
    print(f"Wrote: {waytoagi_path} ({waytoagi_payload.get('count_7d', 0)} items)")
    print(f"Wrote: {title_cache_path} ({len(title_cache)} entries)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
