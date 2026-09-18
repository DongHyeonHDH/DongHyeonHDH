#!/usr/bin/env python3
"""
Notion "Algorithm Note" -> GitHub profile README stats sync.

Reads every row from the Notion "문제" (Problems) data source, computes
solving stats (총 문제 수, 티어별/유형별/언어별 분포, 최근 푼 문제 목록),
and writes the result as a Markdown block into README.md between:

    <!-- ALGO-STATS:START -->
    ...
    <!-- ALGO-STATS:END -->

Only Python standard library is used (no pip install needed in CI).

Required env vars:
    NOTION_TOKEN            Notion internal integration secret

Optional env vars:
    NOTION_DATA_SOURCE_ID   defaults to the "문제" data source used when this
                             script was generated
    README_PATH             defaults to "README.md"
    RECENT_COUNT             how many recent (unique) problems to list, default 8
    NOTION_USER_FILTER       if set, only count rows whose "유저" select equals
                             this value OR is empty/unset (useful once this
                             Notion page is shared with other people)
    CARD_TITLE               title shown on the generated stat card, defaults
                             to the GitHub repo owner (from GITHUB_REPOSITORY)
    ASSET_SVG_PATH           where to write the generated stat-card SVG,
                             defaults to "assets/algo-card.svg"
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATA_SOURCE_ID = os.environ.get("NOTION_DATA_SOURCE_ID") or (
    "87b85b41-79ee-8226-86a0-87858026d0e4"
)
README_PATH = os.environ.get("README_PATH", "README.md")
RECENT_COUNT = int(os.environ.get("RECENT_COUNT", "8"))
USER_FILTER = os.environ.get("NOTION_USER_FILTER", "").strip()
ASSET_SVG_PATH = os.environ.get("ASSET_SVG_PATH", "assets/algo-card.svg")
CARD_TITLE = os.environ.get("CARD_TITLE", "").strip() or (
    os.environ.get("GITHUB_REPOSITORY", "").split("/")[0] or "Algorithm Stats"
)

NOTION_VERSION = "2025-09-03"
API_URL = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"

START_MARKER = "<!-- ALGO-STATS:START -->"
END_MARKER = "<!-- ALGO-STATS:END -->"

PROP_NAME = "이름"
PROP_PLATFORM = "플랫폼"
PROP_TIER = "티어"
PROP_LANG = "언어"
PROP_TYPE = "유형"
PROP_USER = "유저"
PROP_PERF = "성능"
PROP_DATE = "시간"


def fail(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def fetch_all_rows() -> list:
    if not NOTION_TOKEN:
        fail("NOTION_TOKEN is not set. Add it as a GitHub Actions secret.")

    rows = []
    payload = {"page_size": 100}
    while True:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=body,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            fail(f"Notion API error {e.code}: {detail}")

        rows.extend(data.get("results", []))
        if data.get("has_more"):
            payload["start_cursor"] = data["next_cursor"]
        else:
            break
    return rows


def prop_title(props: dict, name: str) -> str:
    arr = props.get(name, {}).get("title", []) or []
    return "".join(t.get("plain_text", "") for t in arr).strip()


def prop_select(props: dict, name: str):
    sel = props.get(name, {}).get("select")
    return sel["name"] if sel else None


def prop_multi(props: dict, name: str) -> list:
    return [o["name"] for o in props.get(name, {}).get("multi_select", []) or []]


def prop_date(props: dict, name: str):
    d = props.get(name, {}).get("date")
    return d["start"] if d else None


def tier_sort_key(tier: str):
    m = re.match(r"([A-Za-z가-힣]*)(\d+)", tier or "")
    if m:
        return (m.group(1), int(m.group(2)))
    return (tier or "", 0)


# --- stat-card SVG (solved.ac-style tier ribbon card, rendered fresh each run) ---

def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _tier_level_num(tier: str) -> int:
    m = re.search(r"(\d+)$", tier or "")
    return int(m.group(1)) if m else 5


def _gradient_colors(tier: str):
    """Single-hue blue diagonal gradient (steps taken from a validated
    sequential blue ramp - #5598e7...#0d366b), depth-mapped by tier (1-10
    scale, matching this database's D1-D10 style tiers). Deliberately one
    hue only (no green/purple mix) for a calmer, more corporate look; higher
    tiers shift toward a deeper navy rather than changing hue."""
    t = max(1, min(10, _tier_level_num(tier))) / 10
    low_start, low_end = (85, 152, 231), (37, 106, 191)      # #5598e7 -> #256abf
    high_start, high_end = (28, 92, 171), (13, 54, 107)      # #1c5cab -> #0d366b

    def mix(c1, c2):
        return tuple(_lerp(c1[i], c2[i], t) for i in range(3))

    tl = mix(low_start, low_end)
    br = mix(high_start, high_end)
    start = f"#{tl[0]:02x}{tl[1]:02x}{tl[2]:02x}"
    end = f"#{br[0]:02x}{br[1]:02x}{br[2]:02x}"
    return start, end


def _next_milestone(n: int, step: int = 50) -> int:
    return step if n == 0 else ((n // step) + 1) * step


def render_card_svg(
    title: str,
    tier_label,
    total_solved: int,
    top_tag,
    top_lang,
    width: int = 380,
    height: int = 158,
) -> str:
    ribbon_w = 108
    start_c, end_c = _gradient_colors(tier_label)
    milestone = _next_milestone(total_solved)
    pct = max(0.0, min(1.0, total_solved / milestone)) if milestone else 0.0
    bar_w = width - ribbon_w - 44
    bar_x = ribbon_w + 22
    bar_y = height - 24
    fill_w = round(bar_w * pct, 1)

    tier_display = tier_label if tier_label else "-"
    tier_font_size = 30 if len(tier_display) <= 3 else (22 if len(tier_display) <= 5 else 16)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Algorithm stats card">
  <defs>
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{start_c}"/>
      <stop offset="100%" stop-color="{end_c}"/>
    </linearGradient>
    <clipPath id="cardClip">
      <rect x="0" y="0" width="{width}" height="{height}" rx="16" ry="16"/>
    </clipPath>
  </defs>
  <g clip-path="url(#cardClip)">
    <rect x="0" y="0" width="{width}" height="{height}" fill="url(#cardGrad)"/>
    <rect x="0" y="0" width="{width}" height="{height}" fill="black" opacity="0.04"/>
    <path d="M0,0 H{ribbon_w} V{height-22} L{ribbon_w/2},{height} L0,{height-22} Z"
          fill="white" opacity="0.14"/>
    <path d="M0,0 H{ribbon_w} V{height-22} L{ribbon_w/2},{height} L0,{height-22} Z"
          fill="none" stroke="white" stroke-opacity="0.35" stroke-width="1"/>
    <text x="{ribbon_w/2}" y="38" text-anchor="middle"
          font-family="Georgia, 'Times New Roman', serif" font-style="italic"
          font-size="14" fill="white" fill-opacity="0.92">Tier</text>
    <text x="{ribbon_w/2}" y="82" text-anchor="middle"
          font-family="'Segoe UI', Helvetica, Arial, sans-serif" font-weight="700"
          font-size="{tier_font_size}" fill="white">{_esc(tier_display)}</text>
    <text x="{ribbon_w + 22}" y="32" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-weight="700" font-size="17" fill="white">{_esc(title)}</text>
    <line x1="{ribbon_w + 22}" y1="42" x2="{width - 20}" y2="42" stroke="white" stroke-opacity="0.3"/>
    <text x="{ribbon_w + 22}" y="66" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-size="12.5" fill="white" fill-opacity="0.85">solved</text>
    <text x="{width - 20}" y="66" text-anchor="end" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-weight="700" font-size="13" fill="white">{_esc(total_solved)}</text>
    <text x="{ribbon_w + 22}" y="88" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-size="12.5" fill="white" fill-opacity="0.85">top type</text>
    <text x="{width - 20}" y="88" text-anchor="end" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-weight="700" font-size="13" fill="white">{_esc(top_tag or '-')}</text>
    <text x="{ribbon_w + 22}" y="110" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-size="12.5" fill="white" fill-opacity="0.85">language</text>
    <text x="{width - 20}" y="110" text-anchor="end" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-weight="700" font-size="13" fill="white">{_esc(top_lang or '-')}</text>
    <text x="{width - 20}" y="{bar_y - 8}" text-anchor="end" font-family="'Segoe UI', Helvetica, Arial, sans-serif"
          font-size="11" fill="white" fill-opacity="0.9">{total_solved} / {milestone} ({round(pct*100)}%)</text>
    <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="6" rx="3" fill="white" fill-opacity="0.25"/>
    <rect x="{bar_x}" y="{bar_y}" width="{fill_w}" height="6" rx="3" fill="white"/>
  </g>
</svg>'''


def main() -> None:
    raw_rows = fetch_all_rows()

    problems = []
    for page in raw_rows:
        props = page.get("properties", {})
        name = prop_title(props, PROP_NAME)
        if not name:
            continue

        user = prop_select(props, PROP_USER)
        if USER_FILTER and user and user != USER_FILTER:
            continue

        problems.append(
            {
                "name": name,
                "platform": prop_select(props, PROP_PLATFORM),
                "tier": prop_select(props, PROP_TIER),
                "lang": prop_select(props, PROP_LANG),
                "tags": prop_multi(props, PROP_TYPE),
                "date": prop_date(props, PROP_DATE),
                "url": page.get("url", ""),
            }
        )

    if not problems:
        fail("No rows found in the Notion data source (check sharing / filters).")

    # Count unique problems by name (a problem can appear multiple times if
    # logged more than once, e.g. re-attempts).
    unique_by_name = {}
    for p in problems:
        existing = unique_by_name.get(p["name"])
        if existing is None or (p["date"] or "") > (existing["date"] or ""):
            unique_by_name[p["name"]] = p
    unique_problems = list(unique_by_name.values())
    total_unique = len(unique_problems)

    platform_counts = Counter(p["platform"] for p in unique_problems if p["platform"])
    tier_counts = Counter(p["tier"] for p in unique_problems if p["tier"])
    lang_counts = Counter(p["lang"] for p in unique_problems if p["lang"])
    tag_counts = Counter()
    for p in unique_problems:
        for t in p["tags"]:
            tag_counts[t] += 1

    tiers_present = [t for t in tier_counts if t]
    highest_tier = (
        max(tiers_present, key=tier_sort_key) if tiers_present else None
    )

    recent = sorted(unique_problems, key=lambda p: p["date"] or "", reverse=True)[
        :RECENT_COUNT
    ]

    top_tag_name = tag_counts.most_common(1)[0][0] if tag_counts else None
    top_lang_name = lang_counts.most_common(1)[0][0] if lang_counts else None

    card_svg = render_card_svg(
        title=CARD_TITLE,
        tier_label=highest_tier,
        total_solved=total_unique,
        top_tag=top_tag_name,
        top_lang=top_lang_name,
    )
    asset_dir = os.path.dirname(ASSET_SVG_PATH)
    if asset_dir:
        os.makedirs(asset_dir, exist_ok=True)
    with open(ASSET_SVG_PATH, "w", encoding="utf-8") as f:
        f.write(card_svg)

    lines = [START_MARKER, "", "### 🧩 Algorithm Problem Solving", ""]
    lines.append(f'<img src="./{ASSET_SVG_PATH}" alt="Algorithm stats card" width="380" />')
    lines.append("")

    if tier_counts:
        lines.append("| 티어 | 문제 수 |")
        lines.append("|---|---|")
        for tier, cnt in sorted(tier_counts.items(), key=lambda kv: tier_sort_key(kv[0])):
            lines.append(f"| {tier} | {cnt} |")
        lines.append("")

    if lang_counts:
        lang_line = ", ".join(f"`{lang}` {cnt}" for lang, cnt in lang_counts.most_common())
        lines.append(f"**사용 언어**: {lang_line}")
        lines.append("")

    if tag_counts:
        top_tags = ", ".join(f"`{t}` ({c})" for t, c in tag_counts.most_common(6))
        lines.append(f"**자주 푼 유형 TOP {min(6, len(tag_counts))}**: {top_tags}")
        lines.append("")

    if recent:
        lines.append("<details>")
        lines.append(f"<summary>최근 푼 문제 ({len(recent)})</summary>")
        lines.append("")
        lines.append("| 날짜 | 문제 | 플랫폼 | 티어 | 유형 |")
        lines.append("|---|---|---|---|---|")
        for p in recent:
            date_str = p["date"][:10] if p["date"] else "-"
            tags_str = ", ".join(p["tags"][:3]) if p["tags"] else "-"
            name_display = f"[{p['name']}]({p['url']})" if p["url"] else p["name"]
            lines.append(
                f"| {date_str} | {name_display} | {p['platform'] or '-'} | {p['tier'] or '-'} | {tags_str} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"_Last synced: {now} · from [Algorithm Note](https://notion.so) via GitHub Actions_")
    lines.append("")
    lines.append(END_MARKER)

    new_block = "\n".join(lines)

    if not os.path.exists(README_PATH):
        fail(f"{README_PATH} not found.")

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
    if pattern.search(content):
        new_content = pattern.sub(lambda _: new_block, content)
    else:
        sep = "" if content.endswith("\n\n") else ("\n\n" if content.endswith("\n") else "\n\n")
        new_content = content + sep + new_block + "\n"

    if new_content != content:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"README updated. total_unique={total_unique}")
    else:
        print("No changes.")


if __name__ == "__main__":
    main()
